#!/usr/bin/env python3
"""Fuse OCR candidates, deterministic CV geometry, and agent-vision observations.

The fusion is alignment-only: it anchors every vision claim to a CV measurement
when one exists, computes a per-fact consistency tier across the OCR/CV/VLM
channels, and orders a human review queue by disagreement severity.  Fusion
never waives human review, never invents text, and never grants coordinate or
text authority — those stay with measurement and the user/source respectively.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from evidence_metrics import (
        bbox_containment,
        bbox_iou,
        bbox_list_to_xwyh,
        box_to_x0y0x1y1,
        box_to_xwyh,
        normalize_text,
        text_similarity,
    )
    from output_policy import resolve_output_path
    from prepare_agent_vision_task import (
        EXIT_OK,
        TaskPackageError,
        UpstreamInconclusive,
        load_json_object,
        load_schema,
        sha256_file,
        validate_json,
        verify_task_package_file,
    )
except ModuleNotFoundError:  # Support: python -m tools.cross_modal_fusion
    from .evidence_metrics import (
        bbox_containment,
        bbox_iou,
        bbox_list_to_xwyh,
        box_to_x0y0x1y1,
        box_to_xwyh,
        normalize_text,
        text_similarity,
    )
    from .output_policy import resolve_output_path
    from .prepare_agent_vision_task import (
        EXIT_OK,
        TaskPackageError,
        UpstreamInconclusive,
        load_json_object,
        load_schema,
        sha256_file,
        validate_json,
        verify_task_package_file,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
GEOMETRY_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "geometry-manifest.schema.json"
VISION_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "agent-vision.schema.json"
FUSION_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "fusion-manifest.schema.json"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "agent-vision-config.json"

EXIT_CONTRACT_REJECTED = 2
EXIT_INCONCLUSIVE = 3

TIER_TRIPLE = "TRIPLE"
TIER_PAIR = "PAIR"
TIER_SINGLE = "SINGLE"
TIER_CONFLICT = "CONFLICT"
TIER_UNSUPPORTED = "UNSUPPORTED_VLM_CLAIM"

BAND_FOCUS_CONFLICT = "FOCUS_CONFLICT"
BAND_FOCUS_UNSUPPORTED = "FOCUS_UNSUPPORTED"
BAND_FOCUS_SINGLE = "FOCUS_SINGLE"
BAND_ROUTINE = "ROUTINE"
BAND_LOW = "LOW"

PRIORITY_CONFLICT = 100
PRIORITY_UNSUPPORTED = 80
PRIORITY_SINGLE_VLM = 70
PRIORITY_FORMULA = 50
PRIORITY_CONFLICTED_PAIR = 40
PRIORITY_PAIR = 20
PRIORITY_TRIPLE = 10


class FusionError(TaskPackageError):
    """A fail-closed fusion validation error."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest().upper()


def _file_binding(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _validate_cross_bindings(
    ocr: Mapping[str, Any],
    geometry: Mapping[str, Any],
    task_package: Mapping[str, Any],
    vision: Mapping[str, Any] | None,
    run_id: str,
    source_hash: str,
) -> None:
    for label, manifest in (("OCR", ocr), ("geometry", geometry), ("task package", task_package)):
        if str(manifest["run_id"]) != run_id:
            raise FusionError(f"{label} run_id is not bound to {run_id}")
        if str(manifest["source"]["sha256"]).upper() != source_hash:
            raise FusionError(f"{label} source hash is not bound to the frozen source")
    if str(ocr["status"]) != "OCR_HYPOTHESES_REVIEW_REQUIRED":
        raise UpstreamInconclusive(f"ocr_manifest_status={ocr['status']}")
    if str(geometry["status"]) != "GEOMETRY_OBSERVATIONS_READY":
        raise UpstreamInconclusive(f"geometry_manifest_status={geometry['status']}")
    if vision is not None:
        binding = vision["task_package"]
        if str(binding["run_id"]) != run_id or str(binding["source_sha256"]).upper() != source_hash:
            raise FusionError("agent-vision document is not bound to the current run/source")


def _anchor_pool(
    panels: Mapping[str, Any] | None,
    geometry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    if panels is not None:
        for region in panels.get("region_candidates", []):
            anchors.append(
                {
                    "anchor_id": str(region["candidate_id"]),
                    "anchor_source": "CV_REGION_CANDIDATE",
                    "box": bbox_list_to_xwyh(region["bbox"]),
                }
            )
    for frame in geometry.get("frame_candidates", []):
        anchors.append(
            {
                "anchor_id": str(frame["frame_id"]),
                "anchor_source": "CV_FRAME_CANDIDATE",
                "box": box_to_xwyh(frame["bbox_source"]),
            }
        )
    return anchors


def _area_ratio(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    area_a = float(a["w"]) * float(a["h"])
    area_b = float(b["w"]) * float(b["h"])
    smaller, larger = min(area_a, area_b), max(area_a, area_b)
    return smaller / larger if larger > 0 else 0.0


def _match_anchor(
    panel_box: Mapping[str, float],
    anchors: Sequence[Mapping[str, Any]],
    alignment: Mapping[str, float],
) -> tuple[dict[str, Any] | None, float, str]:
    """Return (best anchor, score, verdict) with verdict in MATCHED/WEAK/UNSUPPORTED.

    Containment only counts when the two boxes are comparable in scale: without
    the area-ratio guard a canvas-wide frame anchor would absorb every nested
    panel claim with containment 1.0 and destroy the anchoring meaning.
    """
    min_area_ratio = float(alignment["region_containment_min_area_ratio"])
    best: dict[str, Any] | None = None
    best_score = -1.0
    for anchor in anchors:
        iou = bbox_iou(panel_box, anchor["box"])
        containment = bbox_containment(panel_box, anchor["box"])
        if _area_ratio(panel_box, anchor["box"]) < min_area_ratio:
            containment = 0.0
        score = max(iou, containment)
        if score > best_score or (
            score == best_score and best is not None and anchor["anchor_id"] < best["anchor_id"]
        ):
            best = anchor
            best_score = score
    if best is None:
        return None, 0.0, "UNSUPPORTED"
    iou = bbox_iou(panel_box, best["box"])
    containment = bbox_containment(panel_box, best["box"])
    if _area_ratio(panel_box, best["box"]) < min_area_ratio:
        containment = 0.0
    matched = (
        iou >= float(alignment["region_match_iou"])
        or containment >= float(alignment["region_match_containment"])
    )
    if matched:
        return best, best_score, "MATCHED"
    if best_score >= float(alignment["region_weak_score"]):
        return best, best_score, "WEAK"
    return best, best_score, "UNSUPPORTED"


def _region_facts(
    vision: Mapping[str, Any] | None,
    panels: Mapping[str, Any] | None,
    geometry: Mapping[str, Any],
    ocr: Mapping[str, Any],
    alignment: Mapping[str, float],
    fact_index: list[int],
) -> list[dict[str, Any]]:
    if vision is None:
        return []
    structure = next(
        (
            query["structure"]
            for query in vision["queries"]
            if query["task_type"] == "STRUCTURE_GLOBAL" and query["observation_status"] == "OBSERVED"
        ),
        None,
    )
    if structure is None:
        return []

    anchors = _anchor_pool(panels, geometry)
    envelopes = {
        candidate["candidate_id"]: candidate["bbox_envelope_source"]
        for candidate in ocr["text_candidates"]
    }

    facts: list[dict[str, Any]] = []
    for panel in structure["panels"]:
        fact_index[0] += 1
        fact_id = f"FUSE-{fact_index[0]:04d}"
        panel_box = box_to_xwyh(panel["bbox_source"])
        anchor, score, verdict = _match_anchor(panel_box, anchors, alignment)

        if verdict == "UNSUPPORTED":
            fact = {
                "fact_id": fact_id,
                "fact_kind": "REGION_STRUCTURE",
                "subject_id": panel["panel_id"],
                "channels": {"ocr": False, "cv": False, "vlm": True},
                "consistency_tier": TIER_UNSUPPORTED,
                "conflict_reasons": ["VLM_STRUCTURE_CLAIM_WITHOUT_CV_SUPPORT"],
                "detail": {
                    "vlm_panel_id": panel["panel_id"],
                    "vlm_kind": panel["kind"],
                    "vlm_bbox_source": dict(panel["bbox_source"]),
                    "anchored": False,
                    "anchor_source": None,
                    "anchor_id": None,
                    "anchored_bbox_source": None,
                    "match_score": round(score, 4) if score >= 0 else None,
                    "contained_candidate_ids": [],
                },
            }
            facts.append(fact)
            continue

        anchored_box = box_to_x0y0x1y1(anchor["box"])
        contained = sorted(
            candidate_id
            for candidate_id, envelope in envelopes.items()
            if bbox_containment(envelope, anchor["box"]) >= float(alignment["text_align_containment"])
        )
        reasons: list[str] = []
        if verdict == "WEAK":
            reasons.append("WEAK_SPATIAL_MATCH")
        if not anchors:
            reasons.append("NO_CV_ANCHOR_AVAILABLE")
        tier = TIER_TRIPLE if (contained and verdict == "MATCHED") else TIER_PAIR
        if verdict == "WEAK":
            tier = TIER_PAIR
        fact = {
            "fact_id": fact_id,
            "fact_kind": "REGION_STRUCTURE",
            "subject_id": panel["panel_id"],
            "channels": {"ocr": bool(contained), "cv": True, "vlm": True},
            "consistency_tier": tier,
            "conflict_reasons": reasons,
            "detail": {
                "vlm_panel_id": panel["panel_id"],
                "vlm_kind": panel["kind"],
                "vlm_bbox_source": dict(panel["bbox_source"]),
                "anchored": True,
                "anchor_source": anchor["anchor_source"],
                "anchor_id": anchor["anchor_id"],
                "anchored_bbox_source": anchored_box,
                "match_score": round(score, 4),
                "contained_candidate_ids": contained,
            },
        }
        facts.append(fact)
    return facts


def _vlm_opinion_by_candidate(
    vision: Mapping[str, Any] | None,
    task_package: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Map OCR candidate id -> arbitration opinion (SELECT/REJECT_ALL/NOT_OBSERVABLE)."""
    if vision is None:
        return {}
    payload_by_query = {q["query_id"]: q["payload"] for q in task_package["queries"]}
    response_by_query = {q["query_id"]: q for q in vision["queries"]}
    opinions: dict[str, dict[str, Any]] = {}
    for query in task_package["queries"]:
        if query["task_type"] != "CONFLICT_ARBITRATION":
            continue
        candidate_id = query["payload"]["candidate_id"]
        response = response_by_query.get(query["query_id"])
        if response is None or response["observation_status"] != "OBSERVED":
            continue
        conflict = response["conflict"]
        selections = payload_by_query[query["query_id"]]["selections"]
        decision = conflict["decision"]
        if decision == "REJECT_ALL":
            opinion_kind = "ARBITRATION_REJECT_ALL"
            selected_text = None
        else:
            selected_text = str(selections[int(conflict["selected_index"])]["text"])
            # PRIMARY-vs-ALTERNATIVE is resolved against the OCR primary text below.
            opinion_kind = None
        opinions[candidate_id] = {
            "query_id": query["query_id"],
            "decision": decision,
            "selected_text": selected_text,
            "opinion_kind": opinion_kind,
            "reason_code": conflict.get("reason_code"),
            "self_rating": conflict.get("confidence_self_rating"),
        }
    return opinions


def _finalize_opinions(
    opinions: dict[str, dict[str, Any]],
    ocr: Mapping[str, Any],
) -> None:
    primary_by_candidate = {
        candidate["candidate_id"]: str(candidate["text"])
        for candidate in ocr["text_candidates"]
    }
    for candidate_id, opinion in opinions.items():
        if opinion["decision"] != "SELECT":
            continue
        primary = primary_by_candidate.get(candidate_id)
        if primary is None:
            opinion["opinion_kind"] = "ARBITRATION_SELECT_PRIMARY"
            continue
        if normalize_text(opinion["selected_text"]) == normalize_text(primary):
            opinion["opinion_kind"] = "ARBITRATION_SELECT_PRIMARY"
        else:
            opinion["opinion_kind"] = "ARBITRATION_SELECT_ALTERNATIVE"


def _text_facts(
    ocr: Mapping[str, Any],
    geometry: Mapping[str, Any],
    opinions: Mapping[str, Mapping[str, Any]],
    miss_findings: Sequence[Mapping[str, Any]],
    fact_index: list[int],
) -> list[dict[str, Any]]:
    geometry_status = {
        record["candidate_id"]: str(record["status"])
        for record in geometry.get("text_geometry", [])
    }
    facts: list[dict[str, Any]] = []
    for candidate in ocr["text_candidates"]:
        fact_index[0] += 1
        fact_id = f"FUSE-{fact_index[0]:04d}"
        candidate_id = candidate["candidate_id"]
        cv_status = geometry_status.get(candidate_id)
        cv_channel = cv_status == "MEASURED"
        opinion = opinions.get(candidate_id)

        vlm_opinion: str | None = None
        vlm_query: str | None = None
        conflict_reasons: list[str] = []
        if opinion is not None:
            vlm_opinion = opinion["opinion_kind"]
            vlm_query = opinion["query_id"]
        else:
            confirmed_by = next(
                (
                    finding
                    for finding in miss_findings
                    if finding["candidate_id"] == candidate_id
                ),
                None,
            )
            if confirmed_by is not None:
                vlm_opinion = "CONFIRMS_TEXT"
                vlm_query = confirmed_by["query_id"]

        if vlm_opinion in ("ARBITRATION_SELECT_ALTERNATIVE", "ARBITRATION_REJECT_ALL"):
            tier = TIER_CONFLICT
            conflict_reasons.append(
                "VLM_DISAGREES_WITH_OCR_PRIMARY"
                if vlm_opinion == "ARBITRATION_SELECT_ALTERNATIVE"
                else "VLM_REJECTS_ALL_OCR_CANDIDATES"
            )
        else:
            agreeing = 1 + int(cv_channel) + int(vlm_opinion is not None)
            tier = {1: TIER_SINGLE, 2: TIER_PAIR, 3: TIER_TRIPLE}[agreeing]

        if candidate.get("confidence_band") == "OCR_CONFLICT":
            conflict_reasons.append("OCR_ALTERNATIVES_PRESENT")
        if any(
            str(flag) == "FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"
            for flag in candidate.get("review_flags", [])
        ):
            conflict_reasons.append("FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE")

        facts.append(
            {
                "fact_id": fact_id,
                "fact_kind": "TEXT_CANDIDATE",
                "subject_id": candidate_id,
                "channels": {
                    "ocr": True,
                    "cv": cv_channel,
                    "vlm": vlm_opinion is not None,
                },
                "consistency_tier": tier,
                "conflict_reasons": sorted(set(conflict_reasons)),
                "detail": {
                    "candidate_id": candidate_id,
                    "ocr_text": str(candidate["text"]),
                    "ocr_confidence_band": candidate["confidence_band"],
                    "ocr_alternative_count": len(candidate.get("alternatives", [])),
                    "cv_geometry_status": cv_status,
                    "vlm_source_query_id": vlm_query,
                    "vlm_opinion": vlm_opinion,
                },
            }
        )
    return facts


def _miss_scan_findings(
    vision: Mapping[str, Any] | None,
    task_package: Mapping[str, Any],
    ocr: Mapping[str, Any],
    alignment: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Resolve miss-scan text hypotheses: confirm existing candidates or new findings."""
    if vision is None:
        return []
    findings: list[dict[str, Any]] = []
    for query in task_package["queries"]:
        if query["task_type"] != "MISS_SCAN":
            continue
        response = next(
            (r for r in vision["queries"] if r["query_id"] == query["query_id"]),
            None,
        )
        if response is None or response["observation_status"] != "OBSERVED":
            continue
        miss = response["miss_scan"]
        if not miss["contains_text"] or not miss.get("text_hypothesis"):
            continue
        crop_box = query["payload"]["crop_bbox_source"]
        hypothesis = str(miss["text_hypothesis"])
        matched_candidate = None
        for candidate in ocr["text_candidates"]:
            envelope = candidate["bbox_envelope_source"]
            spatial = bbox_containment(envelope, crop_box)
            similarity = text_similarity(hypothesis, str(candidate["text"]))
            if (
                spatial >= float(alignment["text_align_containment"])
                and similarity >= float(alignment["text_align_similarity"])
            ):
                matched_candidate = candidate["candidate_id"]
                break
        findings.append(
            {
                "query_id": query["query_id"],
                "region_candidate_id": query["payload"]["region_candidate_id"],
                "hypothesis": hypothesis,
                "candidate_id": matched_candidate,
            }
        )
    return findings


def _standalone_miss_facts(
    findings: Sequence[Mapping[str, Any]],
    fact_index: list[int],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for finding in findings:
        if finding["candidate_id"] is not None:
            continue  # already folded into the candidate's CONFIRMS_TEXT opinion
        fact_index[0] += 1
        fact_id = f"FUSE-{fact_index[0]:04d}"
        facts.append(
            {
                "fact_id": fact_id,
                "fact_kind": "TEXT_CANDIDATE",
                "subject_id": finding["query_id"],
                "channels": {"ocr": False, "cv": True, "vlm": True},
                "consistency_tier": TIER_SINGLE,
                "conflict_reasons": ["VLM_REPORTS_TEXT_WITHOUT_OCR_CANDIDATE"],
                "detail": {
                    "candidate_id": None,
                    "ocr_text": None,
                    "ocr_confidence_band": None,
                    "ocr_alternative_count": 0,
                    "cv_geometry_status": None,
                    "vlm_source_query_id": finding["query_id"],
                    "vlm_opinion": "NEW_TEXT_HYPOTHESIS",
                },
            }
        )
    return facts


def _formula_facts(
    vision: Mapping[str, Any] | None,
    task_package: Mapping[str, Any],
    fact_index: list[int],
) -> list[dict[str, Any]]:
    if vision is None:
        return []
    response_by_query = {q["query_id"]: q for q in vision["queries"]}
    facts: list[dict[str, Any]] = []
    for query in task_package["queries"]:
        if query["task_type"] != "FORMULA_TRANSCRIPTION":
            continue
        response = response_by_query.get(query["query_id"])
        candidate_id = query["payload"]["candidate_id"]
        fact_index[0] += 1
        fact_id = f"FUSE-{fact_index[0]:04d}"

        if response is None or response["observation_status"] != "OBSERVED":
            facts.append(
                {
                    "fact_id": fact_id,
                    "fact_kind": "FORMULA_TRANSCRIPTION",
                    "subject_id": candidate_id,
                    "channels": {"ocr": True, "cv": False, "vlm": False},
                    "consistency_tier": TIER_SINGLE,
                    "conflict_reasons": ["VLM_NOT_OBSERVABLE"],
                    "detail": {
                        "candidate_id": candidate_id,
                        "source_query_id": query["query_id"],
                        "self_consistency": None,
                        "proposal_latex": None,
                        "proposal_latex_sha256": None,
                        "proposal_status": "PROPOSAL_ONLY_NOT_AUTHORITATIVE",
                    },
                }
            )
            continue

        formula = response["formula"]
        consistency = formula.get("self_consistency")
        samples = formula.get("samples", [])
        proposal = (
            str(samples[0]["latex"])
            if consistency == "SELF_CONSISTENT_K3" and samples
            else None
        )
        tier = TIER_PAIR if proposal is not None else TIER_CONFLICT
        reasons = (
            []
            if proposal is not None
            else ["VLM_SAMPLES_INCONSISTENT"]
            if samples
            else ["VLM_OBSERVED_WITHOUT_SAMPLES"]
        )
        facts.append(
            {
                "fact_id": fact_id,
                "fact_kind": "FORMULA_TRANSCRIPTION",
                "subject_id": candidate_id,
                "channels": {"ocr": True, "cv": False, "vlm": True},
                "consistency_tier": tier,
                "conflict_reasons": reasons,
                "detail": {
                    "candidate_id": candidate_id,
                    "source_query_id": query["query_id"],
                    "self_consistency": consistency,
                    "proposal_latex": proposal,
                    "proposal_latex_sha256": sha256_text(proposal) if proposal else None,
                    "proposal_status": "PROPOSAL_ONLY_NOT_AUTHORITATIVE",
                },
            }
        )
    return facts


def _queue_priority_band(fact: Mapping[str, Any]) -> tuple[int, str]:
    tier = fact["consistency_tier"]
    reasons = fact["conflict_reasons"]
    if tier == TIER_CONFLICT:
        return PRIORITY_CONFLICT, BAND_FOCUS_CONFLICT
    if tier == TIER_UNSUPPORTED:
        return PRIORITY_UNSUPPORTED, BAND_FOCUS_UNSUPPORTED
    if tier == TIER_SINGLE and fact["channels"]["vlm"] and not fact["channels"]["ocr"]:
        return PRIORITY_SINGLE_VLM, BAND_FOCUS_SINGLE
    if fact["fact_kind"] == "FORMULA_TRANSCRIPTION":
        return PRIORITY_FORMULA, BAND_ROUTINE
    if "OCR_ALTERNATIVES_PRESENT" in reasons or "FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE" in reasons:
        return PRIORITY_CONFLICTED_PAIR, BAND_ROUTINE
    if tier == TIER_TRIPLE:
        return PRIORITY_TRIPLE, BAND_LOW
    return PRIORITY_PAIR, BAND_ROUTINE


def _queue_item(rank: int, fact: Mapping[str, Any]) -> dict[str, Any]:
    priority, band = _queue_priority_band(fact)
    return {
        "rank": rank,
        "fact_id": fact["fact_id"],
        "subject_id": fact["subject_id"],
        "priority": priority,
        "band": band,
        "reasons": sorted(set(fact["conflict_reasons"])),
    }


def _review_queue(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        facts,
        key=lambda fact: (-_queue_priority_band(fact)[0], fact["fact_id"]),
    )
    return [_queue_item(rank, fact) for rank, fact in enumerate(ordered, start=1)]


def _queue_markdown(
    manifest: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    queue: Sequence[Mapping[str, Any]],
    ocr: Mapping[str, Any],
) -> str:
    fact_by_id = {fact["fact_id"]: fact for fact in facts}
    lines = [
        "# 融合审核队列（run "
        + str(manifest["run_id"])
        + "）",
        "",
        "策略：融合只决定**先看什么**，不改变**什么算确认**。TRIPLE 一致也不豁免人工审核。",
        "",
        "| rank | band | priority | 主体 | 层级 | 事实类型 | 摘要 | 原因 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in queue:
        fact = fact_by_id[item["fact_id"]]
        detail = fact["detail"]
        if fact["fact_kind"] == "TEXT_CANDIDATE" and detail.get("ocr_text") is not None:
            digest = str(detail["ocr_text"])[:40].replace("|", "\\|")
        elif fact["fact_kind"] == "FORMULA_TRANSCRIPTION":
            proposal = detail.get("proposal_latex")
            digest = (
                ("LaTeX 提议: `" + str(proposal)[:48] + "`") if proposal else "（无自一致提议）"
            )
        elif fact["fact_kind"] == "REGION_STRUCTURE":
            digest = f"{detail['vlm_kind']} @ {detail['anchor_id'] or '无CV锚定'}"
        else:
            digest = str(detail.get("ocr_text") or "")[:40].replace("|", "\\|")
        lines.append(
            "| {rank} | {band} | {priority} | {subject} | {tier} | {kind} | {digest} | {reasons} |".format(
                rank=item["rank"],
                band=item["band"],
                priority=item["priority"],
                subject=fact["subject_id"],
                tier=fact["consistency_tier"],
                kind=fact["fact_kind"],
                digest=digest,
                reasons=", ".join(item["reasons"]) or "-",
            )
        )
    lines.append("")
    lines.append("## 公式 LaTeX 提议（仅候选，需用户/原文确认）")
    lines.append("")
    for fact in facts:
        if fact["fact_kind"] != "FORMULA_TRANSCRIPTION":
            continue
        detail = fact["detail"]
        if detail.get("proposal_latex"):
            lines.append(
                f"- {detail['candidate_id']}（{detail['source_query_id']}）: "
                f"`{detail['proposal_latex']}`"
            )
    return "\n".join(lines) + "\n"


def _overlay_png(
    source_path: Path,
    region_facts: Sequence[Mapping[str, Any]],
) -> bytes:
    image = Image.open(source_path)
    image.load()
    rgb = image.convert("RGB")
    draw = ImageDraw.Draw(rgb)
    for fact in region_facts:
        detail = fact["detail"]
        box = detail["vlm_bbox_source"]
        corners = (box["x0"], box["y0"], box["x1"] - 1, box["y1"] - 1)
        if fact["consistency_tier"] == TIER_UNSUPPORTED:
            draw.rectangle(corners, outline=(220, 30, 30), width=3)
        elif detail["anchored"]:
            anchor_box = detail["anchored_bbox_source"]
            draw.rectangle(
                (anchor_box["x0"], anchor_box["y0"], anchor_box["x1"] - 1, anchor_box["y1"] - 1),
                outline=(20, 140, 40),
                width=3,
            )
        else:
            draw.rectangle(corners, outline=(230, 140, 20), width=3)
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG")
    return buffer.getvalue()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    destination = Path(resolve_output_path(path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_fusion(
    ocr_path: Path,
    geometry_path: Path,
    task_package_path: Path,
    vision_path: Path | None,
    segment_dir: Path | None,
    config_path: Path,
    output_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    ocr = load_json_object(ocr_path, "OCR perception manifest")
    geometry = load_json_object(geometry_path, "geometry manifest")
    task_package = verify_task_package_file(task_package_path)

    validate_json(ocr, load_schema(OCR_SCHEMA_PATH, "perception manifest schema"), "OCR manifest")
    validate_json(
        geometry,
        load_schema(GEOMETRY_SCHEMA_PATH, "geometry manifest schema"),
        "geometry manifest",
    )
    if str(geometry["inputs"]["ocr_manifest"]["sha256"]).upper() != sha256_file(ocr_path):
        raise FusionError("geometry manifest is not bound to the supplied OCR manifest")
    if (
        str(task_package["inputs"]["ocr_manifest"]["sha256"]).upper() != sha256_file(ocr_path)
        or str(task_package["inputs"]["geometry_manifest"]["sha256"]).upper()
        != sha256_file(geometry_path)
    ):
        raise FusionError("task package is not bound to the supplied OCR/geometry manifests")

    config = load_json_object(config_path, "agent-vision config")
    alignment = config["alignment"]

    vision = None
    degradations: list[str] = []
    if vision_path is not None:
        vision = load_json_object(vision_path, "agent-vision observations")
        validate_json(
            vision,
            load_schema(VISION_SCHEMA_PATH, "agent-vision schema"),
            "agent-vision observations",
        )
        if str(vision.get("document_type")) != "AGENT_VISION_OBSERVATIONS":
            raise FusionError(
                "vision input must be the validator-stamped AGENT_VISION_OBSERVATIONS document"
            )
        if str(vision["task_package"]["sha256"]).upper() != sha256_file(task_package_path):
            raise FusionError("vision document is bound to a different task package")
    else:
        degradations.append("AGENT_VISION_ABSENT")

    run_id = str(task_package["run_id"])
    source_hash = str(task_package["source"]["sha256"]).upper()
    _validate_cross_bindings(ocr, geometry, task_package, vision, run_id, source_hash)

    panels = None
    if segment_dir is not None:
        panels_candidate = Path(segment_dir) / "panels.json"
        if panels_candidate.is_file():
            panels = load_json_object(panels_candidate, "segmentation panels")
        else:
            degradations.append("SEGMENTATION_PANELS_MISSING")

    opinions = _vlm_opinion_by_candidate(vision, task_package)
    _finalize_opinions(opinions, ocr)
    miss_findings = _miss_scan_findings(vision, task_package, ocr, alignment)

    fact_index = [0]
    text_facts = _text_facts(ocr, geometry, opinions, miss_findings, fact_index)
    region_facts = _region_facts(vision, panels, geometry, ocr, alignment, fact_index)
    standalone_facts = _standalone_miss_facts(miss_findings, fact_index)
    formula_facts = _formula_facts(vision, task_package, fact_index)
    facts = text_facts + region_facts + standalone_facts + formula_facts

    queue = _review_queue(facts)
    tier_counts = Counter(fact["consistency_tier"] for fact in facts)
    formula_proposals = [
        fact
        for fact in formula_facts
        if fact["detail"]["proposal_latex"] is not None
    ]
    summary = {
        "fact_count": len(facts),
        "text_fact_count": len(text_facts) + len(standalone_facts),
        "region_fact_count": len(region_facts),
        "formula_fact_count": len(formula_facts),
        "tier_counts": {
            "TRIPLE": tier_counts[TIER_TRIPLE],
            "PAIR": tier_counts[TIER_PAIR],
            "SINGLE": tier_counts[TIER_SINGLE],
            "CONFLICT": tier_counts[TIER_CONFLICT],
            "UNSUPPORTED_VLM_CLAIM": tier_counts[TIER_UNSUPPORTED],
        },
        "focus_item_count": sum(
            1
            for item in queue
            if item["band"] in (BAND_FOCUS_CONFLICT, BAND_FOCUS_UNSUPPORTED, BAND_FOCUS_SINGLE)
        ),
        "vlm_query_count": len(vision["queries"]) if vision else 0,
        "vlm_observed_count": (
            sum(1 for q in vision["queries"] if q["observation_status"] == "OBSERVED")
            if vision
            else 0
        ),
        "formula_proposal_count": len(formula_proposals),
        "self_consistent_formula_proposal_count": len(formula_proposals),
        "degradations": sorted(set(degradations)),
    }

    output_dir = Path(resolve_output_path(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    fusion_manifest_path = output_dir / "fusion-manifest.json"

    script_path = Path(__file__).resolve()
    source_record = {
        "path": str(task_package["source"]["path"]),
        "sha256": source_hash,
        "size_bytes": int(task_package["source"]["size_bytes"]),
        "width_px": int(task_package["source"]["width_px"]),
        "height_px": int(task_package["source"]["height_px"]),
        "pixel_mode": str(task_package["source"]["pixel_mode"]),
        "format": "PNG",
    }
    receipt = task_package["inputs"]["host_runtime_receipt"]
    manifest = {
        "schema_version": "1.0.0",
        "document_type": "PERCEPTION_FUSION_MANIFEST",
        "run_id": run_id,
        "created_at_utc": str(ocr["created_at_utc"]),
        "status": "FUSION_REVIEW_QUEUE_READY",
        "mode": "observation_only",
        "degradations": summary["degradations"],
        "policy": {
            "promotion_allowed": False,
            "human_review_required": True,
            "vlm_is_ground_truth": False,
            "triple_agreement_does_not_waive_review": True,
            "coordinates_authority": "CV_AND_OCR_MEASUREMENT_ONLY",
            "text_authority": "USER_OR_SOURCE_TEXT_ONLY",
        },
        "source": source_record,
        "inputs": {
            "ocr_manifest": {
                **_file_binding(ocr_path),
                "schema_version": ocr["schema_version"],
                "run_id": ocr["run_id"],
                "source_sha256": str(ocr["source"]["sha256"]).upper(),
            },
            "geometry_manifest": {
                **_file_binding(geometry_path),
                "schema_version": geometry["schema_version"],
                "run_id": geometry["run_id"],
                "source_sha256": str(geometry["source"]["sha256"]).upper(),
            },
            "task_package": {
                **_file_binding(task_package_path),
                "run_id": task_package["run_id"],
                "source_sha256": source_hash,
            },
            "agent_vision_document": (
                {
                    **_file_binding(vision_path),
                    "document_type": vision["document_type"],
                    "run_id": vision["task_package"]["run_id"],
                    "source_sha256": str(vision["task_package"]["source_sha256"]).upper(),
                    "task_package_sha256": sha256_file(task_package_path),
                }
                if vision is not None
                else None
            ),
            "segmentation": _file_binding(Path(segment_dir) / "panels.json")
            if panels is not None
            else None,
            "config": _file_binding(config_path),
        },
        "implementation": {
            "algorithm_id": "cross_modal_perception_fusion",
            "version": "1.0.0",
            "script": {
                "path": str(script_path),
                "relative_path": "tools/cross_modal_fusion.py",
                "size_bytes": script_path.stat().st_size,
                "sha256": sha256_file(script_path),
            },
            "schema": {
                "path": str(FUSION_SCHEMA_PATH.resolve()),
                "relative_path": "schemas/fusion-manifest.schema.json",
                "size_bytes": FUSION_SCHEMA_PATH.stat().st_size,
                "sha256": sha256_file(FUSION_SCHEMA_PATH),
            },
            "evidence_metrics": {
                "path": str(TOOLS_DIRECTORY / "evidence_metrics.py"),
                "relative_path": "tools/evidence_metrics.py",
                "size_bytes": (TOOLS_DIRECTORY / "evidence_metrics.py").stat().st_size,
                "sha256": sha256_file(TOOLS_DIRECTORY / "evidence_metrics.py"),
            },
            "alignment_parameters": {
                "region_match_iou": float(alignment["region_match_iou"]),
                "region_match_containment": float(alignment["region_match_containment"]),
                "region_containment_min_area_ratio": float(
                    alignment["region_containment_min_area_ratio"]
                ),
                "region_weak_score": float(alignment["region_weak_score"]),
                "text_align_containment": float(alignment["text_align_containment"]),
                "text_align_similarity": float(alignment["text_align_similarity"]),
            },
        },
        "runtime": {
            "runtime_id": receipt["runtime"]["runtime_id"],
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": platform.python_version(),
            "isolated": bool(
                sys.flags.isolated
                and sys.flags.ignore_environment
                and sys.flags.no_user_site
                and sys.flags.safe_path
            ),
        },
        "facts": facts,
        "review_queue": queue,
        "summary": summary,
    }

    queue_markdown = _queue_markdown(manifest, facts, queue, ocr)
    manifest["artifacts"] = {
        "review_queue_markdown": {
            "relative_path": "fusion-review-queue.md",
            "size_bytes": len(queue_markdown.encode("utf-8")),
            "sha256": sha256_text(queue_markdown),
            "media_type": "text/markdown",
            "encoding": "utf8_no_bom_lf",
        },
        "overlay": None,  # filled after render
    }
    overlay_payload = _overlay_png(Path(task_package["source"]["path"]), region_facts)
    manifest["artifacts"]["overlay"] = {
        "relative_path": "fusion-overlay.png",
        "size_bytes": len(overlay_payload),
        "sha256": hashlib.sha256(overlay_payload).hexdigest().upper(),
        "media_type": "image/png",
        "width_px": int(task_package["source"]["width_px"]),
        "height_px": int(task_package["source"]["height_px"]),
        "encoding": "rgb8_png",
    }

    validate_json(
        manifest, load_schema(FUSION_SCHEMA_PATH, "fusion manifest schema"), "fusion manifest"
    )
    atomic_write_bytes(output_dir / "fusion-review-queue.md", queue_markdown.encode("utf-8"))
    atomic_write_bytes(output_dir / "fusion-overlay.png", overlay_payload)
    manifest_json = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(fusion_manifest_path, manifest_json)
    return fusion_manifest_path, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Align OCR, CV geometry, and agent-vision evidence into a review queue."
    )
    parser.add_argument("--ocr-manifest", required=True, type=Path)
    parser.add_argument("--geometry-manifest", required=True, type=Path)
    parser.add_argument("--task-package", required=True, type=Path)
    parser.add_argument(
        "--vision-document", type=Path, default=None, help="Validator-stamped observations"
    )
    parser.add_argument("--segment-dir", type=Path, default=None)
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="agent-vision-config.json"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        manifest_path, manifest = build_fusion(
            ocr_path=Path(args.ocr_manifest),
            geometry_path=Path(args.geometry_manifest),
            task_package_path=Path(args.task_package),
            vision_path=args.vision_document,
            segment_dir=args.segment_dir,
            config_path=Path(args.config),
            output_dir=Path(args.output_dir),
        )
    except UpstreamInconclusive as exc:
        print(f"FUSION_INCONCLUSIVE: {exc}", file=sys.stderr)
        return EXIT_INCONCLUSIVE
    except TaskPackageError as exc:
        print(f"FUSION_REJECTED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_REJECTED

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "run_id": manifest["run_id"],
                "fact_count": manifest["summary"]["fact_count"],
                "focus_item_count": manifest["summary"]["focus_item_count"],
                "tier_counts": manifest["summary"]["tier_counts"],
                "manifest": str(Path(manifest_path).resolve()),
                "review_queue": str(Path(manifest_path).parent / "fusion-review-queue.md"),
            },
            ensure_ascii=False,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
