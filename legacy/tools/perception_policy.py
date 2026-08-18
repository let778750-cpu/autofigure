"""Calibrated, fail-closed policy for ordinary OCR consensus decisions."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES = PROJECT_ROOT / "policy-profiles.json"
DEFAULT_CALIBRATION_SCHEMA = PROJECT_ROOT / "schemas" / "ocr-consensus-calibration.schema.json"
CRITICAL_FLAGS = ("FORMULA", "CONFLICT", "LOW", "UNREADABLE", "SINGLE_GLYPH")
CRITICAL_ROLE_TOKENS = ("formula", "title", "axis", "legend", "connector", "unit", "number", "digit")


class PerceptionPolicyError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_json(path: str | Path, label: str) -> dict[str, Any]:
    resolved = Path(path).resolve(strict=True)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PerceptionPolicyError(f"cannot read {label}: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise PerceptionPolicyError(f"{label} must be one JSON object")
    return value


def load_profile(name: str, path: str | Path = DEFAULT_PROFILES) -> dict[str, Any]:
    profiles = load_json(path, "policy profiles")
    try:
        return dict(profiles["profiles"][name])
    except (KeyError, TypeError) as exc:
        raise PerceptionPolicyError(f"unknown policy profile: {name}") from exc


def validate_calibration(
    path: str | Path, profile_name: str, profile: Mapping[str, Any]
) -> dict[str, Any]:
    receipt = load_json(path, "OCR calibration receipt")
    schema = load_json(DEFAULT_CALIBRATION_SCHEMA, "OCR calibration schema")
    errors = sorted(Draft202012Validator(schema).iter_errors(receipt), key=lambda e: list(e.path))
    if errors:
        raise PerceptionPolicyError(f"invalid OCR calibration receipt: {errors[0].message}")
    if receipt["status"] != "PASS" or receipt["policy_profile"] != profile_name:
        raise PerceptionPolicyError("OCR calibration receipt is not a passing receipt for this profile")
    if float(receipt["metrics"]["precision"]) < float(profile["ocr"]["minimum_fixture_precision"]):
        raise PerceptionPolicyError("OCR calibration precision is below the selected policy")
    fixture = Path(str(receipt["fixture"]["path"])).resolve(strict=True)
    if sha256_file(fixture) != receipt["fixture"]["sha256"]:
        raise PerceptionPolicyError("OCR calibration fixture hash is stale")
    return receipt


def region_for_candidate(candidate: Mapping[str, Any], fusion: Mapping[str, Any]) -> str:
    box = candidate["bbox_source"]
    center = (float(box["x"]) + float(box["w"]) / 2, float(box["y"]) + float(box["h"]) / 2)
    matches: list[tuple[float, str]] = []
    for fact in fusion.get("facts", []):
        if fact.get("fact_kind") != "REGION_STRUCTURE":
            continue
        detail = fact.get("detail", {})
        if candidate.get("candidate_id") in detail.get("contained_candidate_ids", []):
            region = detail.get("anchored_bbox_source") or detail.get("vlm_bbox_source")
            if isinstance(region, Mapping):
                area = float(region["x1"] - region["x0"]) * float(region["y1"] - region["y0"])
                matches.append((area, str(fact["subject_id"])))
                continue
        region = detail.get("anchored_bbox_source") or detail.get("vlm_bbox_source")
        if not isinstance(region, Mapping):
            continue
        x0, y0, x1, y1 = (float(region[key]) for key in ("x0", "y0", "x1", "y1"))
        if x0 <= center[0] <= x1 and y0 <= center[1] <= y1:
            matches.append(((x1 - x0) * (y1 - y0), str(fact["subject_id"])))
    return min(matches)[1] if matches else "UNASSIGNED"


def semantic_role_for_candidate(candidate_id: str, fusion: Mapping[str, Any]) -> str:
    kinds = [
        str(fact.get("detail", {}).get("vlm_kind", "ordinary_text"))
        for fact in fusion.get("facts", [])
        if fact.get("fact_kind") == "REGION_STRUCTURE"
        and candidate_id in fact.get("detail", {}).get("contained_candidate_ids", [])
    ]
    return min(kinds, key=len) if kinds else "ordinary_text"


def find_text_fact(candidate_id: str, fusion: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return next(
        (
            fact for fact in fusion.get("facts", [])
            if fact.get("fact_kind") == "TEXT_CANDIDATE" and fact.get("subject_id") == candidate_id
        ),
        None,
    )


def is_critical(candidate: Mapping[str, Any], semantic_role: str = "ordinary_text") -> bool:
    flags = " ".join(str(value).upper() for value in candidate.get("review_flags", []))
    role = semantic_role.casefold()
    text = str(candidate.get("text", ""))
    return (
        any(token in flags for token in CRITICAL_FLAGS)
        or any(token in role for token in CRITICAL_ROLE_TOKENS)
        or any(character.isdigit() for character in text)
    )


def consensus_eligible(
    candidate: Mapping[str, Any],
    fact: Mapping[str, Any] | None,
    calibration: Mapping[str, Any],
    *,
    semantic_role: str = "ordinary_text",
) -> tuple[bool, str]:
    if is_critical(candidate, semantic_role):
        return False, "critical_text"
    if candidate.get("alternatives") or candidate.get("review_flags"):
        return False, "ocr_conflict_or_review_flag"
    if float(candidate.get("ocr_confidence", 0)) < float(calibration["selected_min_confidence"]):
        return False, "below_calibrated_threshold"
    if fact is None or fact.get("conflict_reasons"):
        return False, "fusion_missing_or_conflicted"
    detail = fact.get("detail", {})
    if detail.get("ocr_alternative_count") != 0:
        return False, "fusion_reports_alternative"
    if detail.get("vlm_opinion") not in {"CONFIRMS_TEXT", "ARBITRATION_SELECT_PRIMARY"}:
        return False, "agent_did_not_select_primary"
    if fact.get("consistency_tier") not in {"PAIR", "TRIPLE"}:
        return False, "insufficient_channel_agreement"
    return True, "eligible"


def deterministic_sample_ids(
    source_sha256: str,
    candidate_ids: Sequence[str],
    *,
    fraction: float,
    minimum: int,
) -> set[str]:
    if not candidate_ids:
        return set()
    count = min(len(candidate_ids), max(minimum, math.ceil(len(candidate_ids) * fraction)))
    ranked = sorted(
        candidate_ids,
        key=lambda value: hashlib.sha256(f"{source_sha256.upper()}:{value}".encode()).hexdigest(),
    )
    return set(ranked[:count])


def sampled_error_regions(
    decisions_by_id: Mapping[str, Mapping[str, Any]],
    candidates_by_id: Mapping[str, Mapping[str, Any]],
    fusion: Mapping[str, Any],
    sampled_ids: Sequence[str] | set[str],
) -> set[str]:
    """Return regions whose deterministic audit sample disproved OCR consensus.

    A sampled candidate is an observed error only after authoritative review marks
    it CORRECTED, NOT_TEXT, or FORMULA_CONFIRMED.  Pending or inconclusive samples
    still block the review receipt, but do not pretend that an error was observed.
    """

    failed: set[str] = set()
    for candidate_id in sampled_ids:
        decision = decisions_by_id.get(str(candidate_id))
        candidate = candidates_by_id.get(str(candidate_id))
        if decision is None or candidate is None:
            continue
        if decision.get("status") not in {"CORRECTED", "NOT_TEXT", "FORMULA_CONFIRMED"}:
            continue
        region = region_for_candidate(candidate, fusion)
        if region != "UNASSIGNED":
            failed.add(region)
    return failed
