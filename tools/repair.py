"""Prepare or ingest a hash-bound PowerPoint-live regional repair handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import (
    ContractError,
    read_json,
    set_modes,
    transition,
    utc_now,
    write_json,
)


REQUIRED_LIVE_CAPABILITIES = (
    "managed-session", "visible-canvas", "native-connector", "freeform",
    "inspect", "audit", "save-reopen", "finalize-target", "object-bindings",
)


def build_live_request(run: common.Run) -> dict[str, Any]:
    from tools.live_bridge import build_powerpoint_live_bridge

    bridge = build_powerpoint_live_bridge(run)
    meta = run.load_meta()
    regions_contract = read_json(run.regions_path)
    regions_by_id = {
        item["id"]: item for item in regions_contract.get("regions", []) if item.get("id")
    }
    region_report_path = run.qa_dir / "regions-report.json"
    if region_report_path.is_file():
        report = read_json(region_report_path)
        failed_ids = [item["id"] for item in report.get("regions", []) if item.get("critical") and not item.get("pass")]
        report_by_id = {
            item["id"]: item for item in report.get("regions", []) if item.get("id")
        }
    else:
        failed_ids = [
            item["id"] for item in regions_contract.get("regions", []) if item.get("critical")
        ]
        report_by_id = {}
    scene = read_json(run.scene_path)
    bindings = read_json(run.bindings_path)
    from tools.providers import write_case_capabilities

    provider_report = write_case_capabilities(run)
    repair_plan = None
    repair_actions: list[dict[str, Any]] = []
    blocker_records: list[dict[str, Any]] = []
    non_live_categories: list[str] = []
    if run.repair_plan_path.is_file():
        from tools.repair_plan import validate_repair_plan

        repair_plan = read_json(run.repair_plan_path)
        validation = validate_repair_plan(
            repair_plan,
            expected_reference_sha256=meta["source_sha256"],
            expected_artifact_sha256=common.sha256_file(run.pptx_path),
        )
        if not validation["pass"]:
            raise common.fail(
                "repair-plan validation failed: " + ", ".join(validation["errors"])
            )
        repair_actions = repair_plan.get("actions", [])
        blocker_records = repair_plan.get("blockers", [])
        non_live_categories = sorted(
            {
                action["category"]
                for action in repair_actions
                if action.get("category") in {"contract", "source_model", "compiler"}
            }
        )
    request = {
        "schema_version": "1.0.0",
        "kind": "powerpoint_live_repair_request",
        "created_at": utc_now(),
        "path_base": "autofigure-case-root",
        "case_root": bridge["case_root"],
        "autofigure_case_root": ".",
        "project_id": bridge["project_id"],
        "task_mode": "RECONSTRUCT_1TO1",
        "target_id": bridge["target_id"],
        "reference_sha256": meta["source_sha256"],
        "candidate_pptx": bridge["template_path"],
        "template_path": bridge["template_path"],
        "visible": True,
        "failed_regions": failed_ids,
        "all_blockers": blocker_records,
        "repair_actions": repair_actions,
        "repair_plan": (
            None
            if repair_plan is None
            else {
                "path": "qa/repair-plan.json",
                "sha256": common.sha256_file(run.repair_plan_path),
                "plan_sha256": repair_plan.get("plan_sha256"),
            }
        ),
        "execution_permitted": not non_live_categories,
        "execution_blocked_by_categories": non_live_categories,
        "execution_policy": (
            "PowerPoint Live may inspect/save/reopen/finalize only after contract, "
            "source_model, and compiler actions are closed through scene/compiler."
        ),
        "failed_region_tasks": [
            {
                "region_id": region_id,
                "label": regions_by_id.get(region_id, {}).get("label", region_id),
                "bbox": regions_by_id.get(region_id, {}).get("bbox"),
                "allowed_element_ids": regions_by_id.get(region_id, {}).get("element_ids", []),
                "all_other_elements_protected": True,
                "manual_scope_required": not bool(
                    regions_by_id.get(region_id, {}).get("element_ids")
                ),
                "current_metrics": {
                    key: report_by_id.get(region_id, {}).get(key)
                    for key in ("ssim", "edge_iou", "mean_abs_rgb_delta")
                    if report_by_id.get(region_id, {}).get(key) is not None
                },
                "thresholds": report_by_id.get(region_id, {}).get("thresholds")
                or regions_by_id.get(region_id, {}).get("thresholds")
                or regions_contract.get("defaults", {}),
            }
            for region_id in failed_ids
        ],
        "required_capabilities": list(REQUIRED_LIVE_CAPABILITIES),
        "mutation_policy": {
            "arrow_authoring": provider_report["powerpoint_live"]["arrow_authoring_allowed"],
            "arrow_authoring_capability_sha256": provider_report["powerpoint_live"][
                "capability_fingerprint_sha256"
            ],
            "arrow_updates_must_return_to_offline_compiler": not provider_report[
                "powerpoint_live"
            ]["arrow_authoring_allowed"],
        },
        "scene_element_ids": [item.get("id") for item in scene.get("elements", []) if item.get("id")],
        "bound_element_ids": [
            item.get("element_id")
            for collection in ("bindings", "logical_group_bindings")
            for item in bindings.get(collection, [])
            if item.get("element_id")
        ],
        "scene_compatibility": {
            "source_schema_version": bridge["source_scene_schema_version"],
            "adapter_schema_version": bridge["adapter_scene_schema_version"],
            "adapter_scene_sha256": bridge["adapter_scene_sha256"],
            "bridge_manifest": "qa/powerpoint-live-bridge.json",
            "direct_scene_v3_submission_forbidden": True,
        },
        "completion_contract": {
            "saved_reopened": True,
            "bindings_complete": True,
            "root_candidate_hash_must_equal_reopened_hash": True,
            "arrow_readback_required": True,
            "render_must_be_finalizer_bound": True,
            "region_results_required": failed_ids,
            "automatic_release_authority": "NONE",
        },
    }
    write_json(run.live_request_path, request)
    return request


def ingest_live_evidence(run: common.Run, evidence_path: Path) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise common.fail("live evidence must be a JSON object")
    meta = run.load_meta()
    required = {"provider": "powerpoint-live", "reference_sha256": meta["source_sha256"], "target_id": "autofigure-pptx", "saved_reopened": True, "bindings_complete": True}
    mismatches = [key for key, value in required.items() if evidence.get(key) != value]
    if mismatches:
        raise common.fail(f"live evidence contract mismatch: {', '.join(mismatches)}")
    if not isinstance(evidence.get("regions"), dict):
        raise common.fail("live evidence requires a regions object")
    blockers = _live_evidence_blockers(run, evidence, [])
    if blockers:
        raise common.fail(f"live evidence artifact identity mismatch: {', '.join(blockers)}")
    write_json(run.live_evidence_path, evidence)
    transition(run, "candidate", "powerpoint-live-evidence-ingested")
    return evidence


def _case_bound_live_path(run: common.Run, path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(run.live_case_dir.resolve())
    except ValueError as exc:
        raise common.fail(f"{label} must stay inside qa/powerpoint-live-case: {resolved}") from exc
    if not resolved.is_file():
        raise common.fail(f"{label} is missing: {resolved}")
    return resolved


def _verify_live_source_candidate_is_current(run: common.Run) -> None:
    """Fail closed when a prepared Live case no longer matches root truth.

    A managed save/reopen may legitimately normalize the candidate package, so
    its output hash need not equal the pre-Live root hash.  The immutable Live
    input copy must, however, still equal both the bridge-declared source hash
    and the current root ``redraw.pptx`` before any output is published.  This
    prevents an old Live session from overwriting later native-math or other
    deterministic root upgrades.
    """

    if not run.live_bridge_path.is_file():
        raise common.fail("PowerPoint-live bridge manifest is missing")
    bridge = read_json(run.live_bridge_path)
    source_hash = bridge.get("source_candidate_sha256")
    if not _is_sha256(source_hash):
        raise common.fail("PowerPoint-live bridge source candidate hash is missing")
    live_input = run.live_case_dir / "input" / "candidate.pptx"
    if not live_input.is_file():
        raise common.fail("PowerPoint-live input candidate is missing")
    if common.sha256_file(live_input) != source_hash:
        raise common.fail("PowerPoint-live input candidate has drifted from its bridge")
    source_manifest_path = run.live_case_dir / "input" / "source_manifest.json"
    if not source_manifest_path.is_file():
        raise common.fail("PowerPoint-live source manifest is missing")
    expected_manifest_hash = bridge.get("contract_files", {}).get(
        "input/source_manifest.json"
    )
    if (
        not _is_sha256(expected_manifest_hash)
        or common.sha256_file(source_manifest_path) != expected_manifest_hash
    ):
        raise common.fail("PowerPoint-live source manifest has drifted from its bridge")
    source_manifest = read_json(source_manifest_path)
    candidate_sources = [
        item
        for item in source_manifest.get("sources", [])
        if item.get("sourceId") == "candidate-pptx"
        and item.get("pathOrUrl") == "input/candidate.pptx"
    ]
    if len(candidate_sources) != 1 or candidate_sources[0].get("sha256") != source_hash:
        raise common.fail(
            "PowerPoint-live source manifest candidate hash does not match its bridge"
        )
    current_root_hash = common.sha256_file(run.pptx_path)
    if current_root_hash != source_hash:
        raise common.fail(
            "stale PowerPoint-live input candidate: redraw.pptx changed after "
            "the Live case was prepared"
        )
    if bridge.get("source_bindings_sha256") != common.sha256_file(run.bindings_path):
        raise common.fail(
            "stale PowerPoint-live input candidate: bindings changed after "
            "the Live case was prepared"
        )
    math_summary_path = run.qa_dir / "math-summary.json"
    expected_math_summary_hash = bridge.get("source_math_summary_sha256")
    if expected_math_summary_hash is None:
        if math_summary_path.is_file():
            raise common.fail(
                "stale PowerPoint-live input candidate: math evidence appeared "
                "after the Live case was prepared"
            )
        return
    if not _is_sha256(expected_math_summary_hash) or not math_summary_path.is_file():
        raise common.fail("PowerPoint-live bridge math evidence binding is incomplete")
    if common.sha256_file(math_summary_path) != expected_math_summary_hash:
        raise common.fail(
            "stale PowerPoint-live input candidate: math evidence changed after "
            "the Live case was prepared"
        )
    math_summary = read_json(math_summary_path)
    if math_summary.get("pptx_sha256") != current_root_hash:
        raise common.fail("current math evidence is not bound to redraw.pptx")


def _pptx_roundtrip_signature(
    path: Path,
) -> tuple[
    set[tuple[str, int, str]],
    tuple[tuple[str, int, str, tuple[str, ...]], ...],
]:
    """Return stable identities plus logical native-math content per object."""

    identities: set[tuple[str, int, str]] = set()
    native_math: list[tuple[str, int, str, tuple[str, ...]]] = []
    with zipfile.ZipFile(path) as package:
        slide_entries = sorted(
            entry
            for entry in package.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", entry)
        )
        for entry in slide_entries:
            root = ET.fromstring(package.read(entry))
            for node in root.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local == "cNvPr" and node.get("id") and node.get("name"):
                    identities.add((entry, int(node.get("id")), node.get("name", "")))
                elif local == "AlternateContent" and any(
                    descendant.tag.rsplit("}", 1)[-1] == "oMath"
                    for descendant in node.iter()
                ):
                    carriers = list(node)
                    carrier_names = [
                        carrier.tag.rsplit("}", 1)[-1] for carrier in carriers
                    ]
                    if (
                        carrier_names.count("Choice") != 1
                        or carrier_names.count("Fallback") > 1
                        or any(
                            name not in {"Choice", "Fallback"}
                            for name in carrier_names
                        )
                    ):
                        raise common.fail(
                            "PowerPoint native-math AlternateContent carrier profile is invalid"
                        )
                    branch_formulas: list[ET.Element] = []
                    formula_branch_identities: list[tuple[int, str]] = []
                    for carrier in carriers:
                        formulas = [
                            descendant
                            for descendant in carrier.iter()
                            if descendant.tag.rsplit("}", 1)[-1] == "oMath"
                        ]
                        if len(formulas) > 1:
                            raise common.fail(
                                "PowerPoint native-math branch has duplicate oMath objects"
                            )
                        if formulas:
                            carrier_identities = {
                                (
                                    int(descendant.get("id")),
                                    descendant.get("name", ""),
                                )
                                for descendant in carrier.iter()
                                if descendant.tag.rsplit("}", 1)[-1] == "cNvPr"
                                and descendant.get("id")
                                and descendant.get("name")
                            }
                            if len(carrier_identities) != 1:
                                raise common.fail(
                                    "PowerPoint native-math branch has ambiguous identity"
                                )
                            formula_branch_identities.append(
                                next(iter(carrier_identities))
                            )
                        branch_formulas.extend(formulas)
                    choice = next(
                        carrier
                        for carrier in carriers
                        if carrier.tag.rsplit("}", 1)[-1] == "Choice"
                    )
                    if not any(
                        descendant.tag.rsplit("}", 1)[-1] == "oMath"
                        for descendant in choice.iter()
                    ):
                        raise common.fail(
                            "PowerPoint native-math Choice branch has no oMath object"
                        )
                    if len(set(formula_branch_identities)) != 1:
                        raise common.fail(
                            "PowerPoint native-math branches disagree on shape identity"
                        )
                    math_identities = {
                        (int(descendant.get("id")), descendant.get("name", ""))
                        for descendant in node.iter()
                        if descendant.tag.rsplit("}", 1)[-1] == "cNvPr"
                        and descendant.get("id")
                        and descendant.get("name")
                    }
                    if len(math_identities) != 1:
                        raise common.fail(
                            "PowerPoint native-math AlternateContent has ambiguous identity"
                        )
                    shape_id, shape_name = next(iter(math_identities))
                    formulas = {
                        "".join(
                            unicodedata.normalize("NFKC", descendant.text or "")
                            for descendant in formula.iter()
                            if descendant.tag.rsplit("}", 1)[-1] == "t"
                        ).strip()
                        for formula in branch_formulas
                    }
                    formulas.discard("")
                    if len(formulas) != 1:
                        raise common.fail(
                            "PowerPoint native-math branches disagree on formula content"
                        )
                    native_math.append(
                        (entry, shape_id, shape_name, tuple(sorted(formulas)))
                    )
    return identities, tuple(sorted(native_math))


def _verify_pptx_save_reopen_structure(
    run: common.Run, candidate: Path, reopened: Path
) -> None:
    live_input = run.live_case_dir / "input" / "candidate.pptx"
    source_signature = _pptx_roundtrip_signature(live_input)
    candidate_signature = _pptx_roundtrip_signature(candidate)
    reopened_signature = _pptx_roundtrip_signature(reopened)
    if candidate_signature != source_signature:
        raise common.fail(
            "PowerPoint save/reopen changed shape identities or native-math inventory"
        )
    if reopened_signature != candidate_signature:
        raise common.fail(
            "PowerPoint reopened artifact changed shape identities or native-math inventory"
        )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _inventory_sha256(inventory: Any) -> str:
    """Match powerpoint-live's stableValue + JSON.stringify digest."""

    payload = json.dumps(
        inventory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _verify_live_inventories(evidence: dict[str, Any]) -> None:
    """Validate the host-returned live/reopened inventory pair."""

    session_id = evidence.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise common.fail("live evidence requires a managed PowerPoint session_id")
    for field in ("live_inventory_sha256", "reopened_inventory_sha256"):
        if not _is_sha256(evidence.get(field)):
            raise common.fail(f"live evidence requires a valid {field}")
    for field in ("live_inventory", "reopened_inventory"):
        if not isinstance(evidence.get(field), dict):
            raise common.fail(f"live evidence requires a complete {field} object")
    for inventory_field, digest_field in (
        ("live_inventory", "live_inventory_sha256"),
        ("reopened_inventory", "reopened_inventory_sha256"),
    ):
        if _inventory_sha256(evidence[inventory_field]) != evidence[digest_field]:
            raise common.fail(
                f"live evidence {inventory_field} does not match {digest_field}"
            )
    if evidence["live_inventory_sha256"] != evidence["reopened_inventory_sha256"]:
        raise common.fail("PowerPoint live/reopened inventories differ")
    if evidence["live_inventory"] != evidence["reopened_inventory"]:
        raise common.fail("PowerPoint live/reopened inventory content differs")


def _powerpoint_operation_log(
    run: common.Run, evidence: dict[str, Any]
) -> tuple[Path, list[dict[str, Any]]]:
    session_id = evidence["session_id"]
    operation_log = _case_bound_live_path(
        run,
        run.live_case_dir
        / "build"
        / "sessions"
        / "powerpoint"
        / session_id
        / "operation-log.ndjson",
        "PowerPoint operation log",
    )
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        operation_log.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise common.fail(
                f"PowerPoint operation log line {line_number} is invalid JSON"
            ) from exc
        if isinstance(event, dict):
            events.append(event)
    return operation_log, events


def _matching_begin_before_operation(
    run: common.Run,
    events: list[dict[str, Any]],
    operation_index: int,
) -> tuple[int, dict[str, Any]]:
    if not run.live_bridge_path.is_file():
        raise common.fail("PowerPoint-live bridge manifest is missing")
    bridge = read_json(run.live_bridge_path)
    expected_adapter_hash = bridge.get("adapter_scene_sha256")
    expected_source_manifest_hash = bridge.get("contract_files", {}).get(
        "input/source_manifest.json"
    )
    matching_begins = [
        (index, event)
        for index, event in enumerate(events)
        if index < operation_index
        if event.get("event") == "begin_session"
        and event.get("scene_graph_sha256") == expected_adapter_hash
        and event.get("contract_hashes", {}).get("sourceManifestSha256")
        == expected_source_manifest_hash
    ]
    if not matching_begins:
        raise common.fail(
            "PowerPoint operation log has no current-scene begin_session "
            "before the matching save operation"
        )
    return matching_begins[-1]


def _minimal_operation_event(
    event: dict[str, Any], *, publication_mode: str
) -> dict[str, Any]:
    keys = (
        (
            "event",
            "tool",
            "revision",
            "request_sha256",
            "candidate_path",
            "candidate_sha256",
            "reopened_inventory_sha256",
            "preview_path",
            "preview_sha256",
        )
        if publication_mode == "finalizer"
        else (
            "event",
            "tool",
            "revision",
            "request_sha256",
            "candidate_path",
            "candidate_sha256",
            "reopened_inventory_sha256",
        )
    )
    return {key: event[key] for key in keys if key in event}


def _operation_receipt(
    run: common.Run,
    evidence: dict[str, Any],
    operation_log: Path,
    events: list[dict[str, Any]],
    operation_index: int,
    *,
    publication_mode: str,
) -> dict[str, Any]:
    begin_index, begin_event = _matching_begin_before_operation(
        run, events, operation_index
    )
    minimal_begin = {
        key: begin_event[key]
        for key in (
            "event",
            "revision",
            "target_id",
            "scene_schema_version",
            "scene_graph_sha256",
            "render_plan_sha256",
            "contract_hashes",
        )
        if key in begin_event
    }
    minimal_operation = _minimal_operation_event(
        events[operation_index], publication_mode=publication_mode
    )
    return {
        "schema_version": "1.0.0",
        "kind": "powerpoint_live_operation_receipt",
        "provider": "powerpoint-live",
        "publication_mode": publication_mode,
        "session_id": evidence["session_id"],
        "target_id": evidence["target_id"],
        "event_index_base": 0,
        "begin_event_index": begin_index,
        "operation_event_index": operation_index,
        "operation_log_path": operation_log.relative_to(
            run.live_case_dir.resolve()
        ).as_posix(),
        "operation_log_sha256": common.sha256_file(operation_log),
        "begin_event": minimal_begin,
        "begin_event_sha256": _canonical_json_sha256(minimal_begin),
        "matching_begin_event_sha256": _canonical_json_sha256(begin_event),
        "operation_event": minimal_operation,
        "operation_event_sha256": _canonical_json_sha256(minimal_operation),
        "matching_operation_event_sha256": _canonical_json_sha256(
            events[operation_index]
        ),
    }


def _verify_powerpoint_live_machine_evidence(
    run: common.Run,
    evidence: dict[str, Any],
    candidate: Path,
    live_render: Path,
) -> dict[str, Any]:
    """Bind a publication claim to the managed PowerPoint operation log."""

    _verify_live_inventories(evidence)
    render_hash = common.sha256_file(live_render)
    if evidence.get("render_sha256") != render_hash:
        raise common.fail("live evidence render_sha256 does not match the supplied live render")

    operation_log, events = _powerpoint_operation_log(run, evidence)
    candidate_relative = candidate.relative_to(run.live_case_dir.resolve()).as_posix()
    render_relative = live_render.relative_to(run.live_case_dir.resolve()).as_posix()
    matching_finalizers = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") == "finalize_target_prepared"
        and event.get("candidate_sha256") == evidence.get("candidate_sha256")
        and event.get("reopened_inventory_sha256")
        == evidence.get("reopened_inventory_sha256")
        and event.get("preview_sha256") == render_hash
        and str(event.get("candidate_path", "")).replace("\\", "/").casefold()
        == candidate_relative.casefold()
        and str(event.get("preview_path", "")).replace("\\", "/").casefold()
        == render_relative.casefold()
    ]
    if not matching_finalizers:
        raise common.fail(
            "live evidence is not backed by a matching PowerPoint "
            "finalize_target candidate/render operation"
        )
    operation_index, _ = matching_finalizers[-1]
    return _operation_receipt(
        run,
        evidence,
        operation_log,
        events,
        operation_index,
        publication_mode="finalizer",
    )


def _verify_powerpoint_live_save_candidate_evidence(
    run: common.Run,
    evidence: dict[str, Any],
    candidate: Path,
    reopened: Path,
    live_render: Path | None,
) -> dict[str, Any]:
    """Verify an intermediate save/reopen without promoting it to final evidence."""

    _verify_live_inventories(evidence)
    candidate_hash = common.sha256_file(candidate)
    reopened_hash = common.sha256_file(reopened)
    if candidate_hash != reopened_hash:
        raise common.fail("live candidate and reopened artifact hashes differ")
    _verify_pptx_save_reopen_structure(run, candidate, reopened)
    if evidence.get("candidate_sha256") != candidate_hash:
        raise common.fail(
            "live evidence candidate_sha256 does not match the supplied candidate"
        )
    if evidence.get("reopened_artifact_sha256") != reopened_hash:
        raise common.fail(
            "live evidence reopened_artifact_sha256 does not match the reopened artifact"
        )
    if live_render is not None:
        render_hash = common.sha256_file(live_render)
        if evidence.get("render_sha256") != render_hash:
            raise common.fail(
                "live evidence render_sha256 does not match the supplied live render"
            )

    operation_log, events = _powerpoint_operation_log(run, evidence)
    candidate_relative = candidate.relative_to(run.live_case_dir.resolve()).as_posix()
    matching_saves = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") == "save_candidate"
        and event.get("tool") == "powerpoint_save_candidate"
        and event.get("candidate_sha256") == candidate_hash
        and event.get("reopened_inventory_sha256")
        == evidence.get("reopened_inventory_sha256")
        and str(event.get("candidate_path", "")).replace("\\", "/").casefold()
        == candidate_relative.casefold()
    ]
    if not matching_saves:
        raise common.fail(
            "live evidence is not backed by a matching PowerPoint "
            "save_candidate operation"
        )
    operation_index, _ = matching_saves[-1]
    return _operation_receipt(
        run,
        evidence,
        operation_log,
        events,
        operation_index,
        publication_mode="save-reopen-only",
    )


def _publish_file_set_atomically(
    files: list[tuple[Path, Path]], transaction_root: Path
) -> None:
    """Publish a validated file set with rollback on any replacement failure."""

    backup_root = transaction_root / "backups"
    staged_root = transaction_root / "publish"
    backup_root.mkdir(parents=True, exist_ok=True)
    staged_root.mkdir(parents=True, exist_ok=True)
    states: list[tuple[Path, Path | None]] = []
    try:
        for index, (source, destination) in enumerate(files):
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if destination.exists():
                backup = backup_root / f"{index:03d}.bak"
                shutil.copy2(destination, backup)
            staged = staged_root / f"{index:03d}.tmp"
            shutil.copy2(source, staged)
            os.replace(staged, destination)
            states.append((destination, backup))
    except Exception:
        for destination, backup in reversed(states):
            if backup is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(backup, destination)
        raise


def publish_live_candidate(
    run: common.Run,
    evidence_path: Path,
    candidate_path: Path,
    *,
    reopened_path: Path | None = None,
    render_path: Path | None = None,
) -> dict[str, Any]:
    """Publish only a candidate/render pair backed by ``finalize_target``."""

    return _publish_live_candidate_impl(
        run,
        evidence_path,
        candidate_path,
        reopened_path=reopened_path,
        render_path=render_path,
        verification_mode="finalizer",
    )


def publish_live_save_reopen_candidate(
    run: common.Run,
    evidence_path: Path,
    candidate_path: Path,
    *,
    reopened_path: Path | None = None,
    render_path: Path | None = None,
) -> dict[str, Any]:
    """Publish a verified intermediate host save/reopen, never formal evidence.

    ``evidence_path`` must contain the provider/reference/target contract,
    ``saved_reopened=true``, ``bindings_complete=true``, candidate and reopened
    SHA-256 values, the equal live/reopened inventory objects and their stable
    digests, ``session_id``, ``host_reopen_method=powerpoint-live``, and
    ``arrow_mutations=false`` when the provider cannot author arrows.  A
    separately exported render is optional; when supplied its SHA-256 must be
    declared as ``render_sha256``.  The matching host operation must be
    ``powerpoint_save_candidate``.  Consequently the published evidence is
    always marked ``render_finalizer_bound=false`` and strict validation keeps
    the stable ``live-render-finalizer-unverified`` blocker.
    """

    return _publish_live_candidate_impl(
        run,
        evidence_path,
        candidate_path,
        reopened_path=reopened_path,
        render_path=render_path,
        verification_mode="save-reopen-only",
    )


def _publish_live_candidate_impl(
    run: common.Run,
    evidence_path: Path,
    candidate_path: Path,
    *,
    reopened_path: Path | None = None,
    render_path: Path | None = None,
    verification_mode: str,
) -> dict[str, Any]:
    """Validate a managed Live candidate in isolation, then publish atomically."""

    if verification_mode not in {"finalizer", "save-reopen-only"}:
        raise common.fail(f"unsupported PowerPoint Live verification mode: {verification_mode}")

    _verify_live_source_candidate_is_current(run)

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise common.fail("live evidence must be a JSON object")
    candidate = _case_bound_live_path(run, candidate_path, "live candidate")
    if reopened_path is None:
        raise common.fail("publishing a Live candidate requires an explicit reopened artifact")
    reopened = _case_bound_live_path(run, reopened_path, "reopened candidate")
    if render_path is None and verification_mode == "finalizer":
        raise common.fail("publishing a Live candidate requires a PowerPoint live render")
    live_render = (
        _case_bound_live_path(run, render_path, "live render")
        if render_path is not None
        else None
    )
    render_source = live_render if live_render is not None else run.render_png
    if not render_source.is_file():
        raise common.fail("save/reopen-only publication requires an existing case render")
    expected_size = common.image_size(run.source_png)
    if common.image_size(render_source) != expected_size:
        raise common.fail(
            f"live render size mismatch: expected {expected_size}, got {common.image_size(render_source)}"
        )
    candidate_hash = common.sha256_file(candidate)
    reopened_hash = common.sha256_file(reopened)
    if candidate_hash != reopened_hash:
        raise common.fail("live candidate and reopened artifact hashes differ")
    if evidence.get("candidate_sha256") != candidate_hash:
        raise common.fail("live evidence candidate_sha256 does not match the supplied candidate")
    if evidence.get("reopened_artifact_sha256") != reopened_hash:
        raise common.fail("live evidence reopened_artifact_sha256 does not match the reopened artifact")
    if evidence.get("host_reopen_method") != "powerpoint-live":
        raise common.fail("live evidence must identify host_reopen_method=powerpoint-live")
    required = {
        "provider": "powerpoint-live",
        "reference_sha256": run.load_meta()["source_sha256"],
        "target_id": "autofigure-pptx",
        "saved_reopened": True,
        "bindings_complete": True,
    }
    mismatches = [key for key, value in required.items() if evidence.get(key) != value]
    if mismatches:
        raise common.fail(f"live evidence contract mismatch: {', '.join(mismatches)}")
    if verification_mode == "finalizer" and not isinstance(
        evidence.get("regions"), dict
    ):
        raise common.fail("live evidence requires a regions object")
    if verification_mode == "finalizer":
        assert live_render is not None
        operation_receipt = _verify_powerpoint_live_machine_evidence(
            run, evidence, candidate, live_render
        )
    else:
        operation_receipt = _verify_powerpoint_live_save_candidate_evidence(
            run, evidence, candidate, reopened, live_render
        )

    from pptx import Presentation

    Presentation(reopened)  # package integrity preflight
    with tempfile.TemporaryDirectory(
        prefix=".autofigure-live-publish-", dir=run.root
    ) as temporary:
        transaction_root = Path(temporary)
        # QA reports serialize ``run.root.name`` as their case identity.  Keep
        # the isolated shadow's leaf name identical to the real case so the
        # published reports remain byte-for-byte reproducible when strict
        # immediately recomputes them at the case root.
        shadow_root = transaction_root / "shadow" / run.root.name
        shadow_root.mkdir(parents=True)
        shadow_qa = shadow_root / "qa"
        shadow_qa.mkdir()
        source_math_dir = run.qa_dir / "math"
        if source_math_dir.is_dir():
            shutil.copytree(source_math_dir, shadow_qa / "math")
        for source, destination in (
            (run.meta_path, shadow_root / "run.json"),
            (run.source_png, shadow_root / "reference.png"),
            (run.redraw_svg, shadow_root / "redraw.svg"),
            (run.scene_path, shadow_root / "scene.json"),
            (run.bindings_path, shadow_root / "bindings.json"),
            (run.regions_path, shadow_root / "regions.json"),
            (reopened, shadow_root / "redraw.pptx"),
            (render_source, shadow_root / "render.png"),
        ):
            shutil.copy2(source, destination)
        shadow = common.Run(shadow_root)
        source_scene_snapshot = shadow.qa_dir / "powerpoint-live-source-scene.json"
        shutil.copy2(shadow.scene_path, source_scene_snapshot)
        source_scene_snapshot_hash = common.sha256_file(source_scene_snapshot)
        if not run.live_bridge_path.is_file():
            raise common.fail("PowerPoint-live bridge manifest is missing")
        shutil.copy2(run.live_bridge_path, shadow.live_bridge_path)
        bridge_manifest = read_json(shadow.live_bridge_path)
        if bridge_manifest.get("source_scene_sha256") != source_scene_snapshot_hash:
            raise common.fail(
                "PowerPoint-live bridge is not bound to the pre-Live source scene"
            )
        inventory_report_path = shadow.qa_dir / "powerpoint-live-inventory.json"
        operation_receipt_path = (
            shadow.qa_dir / "powerpoint-live-operation-receipt.json"
        )
        write_json(operation_receipt_path, operation_receipt)
        write_json(
            inventory_report_path,
            {
                "schema_version": "1.0.0",
                "kind": "powerpoint_live_save_reopen_inventory",
                "session_id": evidence["session_id"],
                "candidate_sha256": candidate_hash,
                "live_inventory_sha256": evidence["live_inventory_sha256"],
                "reopened_inventory_sha256": evidence[
                    "reopened_inventory_sha256"
                ],
                "inventories_equal": True,
                "render_finalizer_bound": verification_mode == "finalizer",
                "live_inventory": evidence["live_inventory"],
                "reopened_inventory": evidence["reopened_inventory"],
            },
        )
        scene = read_json(shadow.scene_path)
        scene.setdefault("artifact", {}).update(
            {"backend": "pptx-powerpoint-live", "path": "redraw.pptx", "sha256": candidate_hash}
        )
        scene["updated_at"] = utc_now()
        write_json(shadow.scene_path, scene)

        from tools.pptx_arrows import refresh_bindings, write_arrow_reports
        from tools.layout import audit_layout
        from tools.primitives import audit_primitives
        from tools.providers import write_case_capabilities

        binding_summary = refresh_bindings(shadow, host_saved_reopened=True)
        if binding_summary.get("bindings_complete") is not True:
            raise common.fail(
                "live candidate bindings are incomplete after PowerPoint save/reopen"
            )
        from tools.revisions import stamp_active_revision

        active_revision = stamp_active_revision(shadow)
        native_math_present = any(
            item.get("object_kind") == "native-math"
            or item.get("native_math") is True
            for item in read_json(shadow.bindings_path).get("bindings", [])
        )
        math_summary_path: Path | None = None
        if native_math_present or (run.qa_dir / "math-summary.json").is_file():
            from tools.math import math_summary_blockers, upgrade as upgrade_math

            upgrade_math(shadow, dry_run=True)
            math_summary_path = shadow.qa_dir / "math-summary.json"
            math_blockers = math_summary_blockers(shadow)
            if math_blockers:
                raise common.fail(
                    "live candidate failed native-math readback: "
                    + ", ".join(math_blockers)
                )
        arrow_compile, arrow_readback = write_arrow_reports(shadow)
        primitive_report = audit_primitives(shadow)
        audit_layout(shadow)
        provider_report = write_case_capabilities(shadow)
        if not arrow_compile.get("pass"):
            raise common.fail("live candidate failed ArrowSpec compilation evidence")
        if not arrow_readback.get("pass"):
            raise common.fail("live candidate failed PowerPoint arrow readback")
        if not primitive_report.get("pass"):
            raise common.fail("live candidate failed semantic primitive readback")
        if (
            provider_report["powerpoint_live"]["arrow_authoring_allowed"] is not True
            and evidence.get("arrow_mutations") is not False
        ):
            raise common.fail("unverified PowerPoint Live provider must attest arrow_mutations=false")
        from tools.regions import evaluate_regions

        region_report = evaluate_regions(shadow)
        machine_regions = {
            item["id"]: "REGION_PASS" if item.get("pass") else "REGION_REVISE"
            for item in region_report.get("regions", [])
            if item.get("critical") and item.get("id")
        }
        declared_regions = evidence.get("regions")
        mismatched_regions = (
            [
                region_id
                for region_id, result in machine_regions.items()
                if declared_regions.get(region_id) != result
            ]
            if isinstance(declared_regions, dict)
            else []
        )
        if mismatched_regions:
            raise common.fail(
                "live evidence region results do not match the supplied live render: "
                + ", ".join(mismatched_regions)
            )

        published_evidence = {
            key: value
            for key, value in evidence.items()
            if key not in {"live_inventory", "reopened_inventory"}
        }
        published_evidence.update(
            {
                "candidate_sha256": candidate_hash,
                "reopened_artifact_sha256": reopened_hash,
                "binding_artifact_sha256": common.sha256_file(shadow.pptx_path),
                "bindings_sha256": common.sha256_file(shadow.bindings_path),
                "scene_sha256": common.sha256_file(shadow.scene_path),
                "source_scene_snapshot_sha256": source_scene_snapshot_hash,
                "bridge_manifest_sha256": common.sha256_file(
                    shadow.live_bridge_path
                ),
                "powerpoint_live_inventory_sha256": common.sha256_file(
                    inventory_report_path
                ),
                "operation_receipt_path": "qa/powerpoint-live-operation-receipt.json",
                "operation_receipt_sha256": common.sha256_file(
                    operation_receipt_path
                ),
                "arrow_readback_sha256": common.sha256_file(
                    shadow.powerpoint_arrow_readback_path
                ),
                "arrow_compile_report_sha256": common.sha256_file(
                    shadow.arrow_compile_report_path
                ),
                "arrow_composition_audit_sha256": common.sha256_file(
                    shadow.qa_dir / "arrow-composition-audit.json"
                ),
                "primitive_audit_sha256": common.sha256_file(
                    shadow.primitive_audit_path
                ),
                "layout_audit_sha256": common.sha256_file(
                    shadow.layout_audit_path
                ),
                "provider_capabilities_sha256": common.sha256_file(
                    shadow.provider_capabilities_path
                ),
                "regions_report_sha256": common.sha256_file(
                    shadow.qa_dir / "regions-report.json"
                ),
                "autofigure_render_sha256": common.sha256_file(shadow.render_png),
                "published_root_artifact_sha256": common.sha256_file(shadow.pptx_path),
                "regions": machine_regions,
                "render_finalizer_bound": verification_mode == "finalizer",
                "live_render_published": live_render is not None,
                "publication_mode": verification_mode,
                "revision_id": active_revision["revision_id"],
                "canonical_scene_sha256": active_revision["scene_sha256"],
                "compiler_fingerprint": active_revision["compiler_fingerprint"],
            }
        )
        if math_summary_path is not None:
            published_evidence["math_summary_sha256"] = common.sha256_file(
                math_summary_path
            )
        blockers = _live_evidence_blockers(shadow, published_evidence, [])
        allowed_blockers = (
            {"live-render-finalizer-unverified"}
            if verification_mode == "save-reopen-only"
            else set()
        )
        unexpected_blockers = [
            blocker for blocker in blockers if blocker not in allowed_blockers
        ]
        if unexpected_blockers:
            raise common.fail(
                "live evidence artifact identity mismatch: "
                + ", ".join(unexpected_blockers)
            )
        write_json(shadow.live_evidence_path, published_evidence)
        write_live_save_reopen_summary(shadow, published_evidence)
        transition(
            shadow,
            "candidate",
            (
                "powerpoint-live-candidate-published"
                if verification_mode == "finalizer"
                else "powerpoint-live-save-reopen-only-published"
            ),
        )

        publication_files = [
            (shadow.pptx_path, run.pptx_path),
            (shadow.scene_path, run.scene_path),
            (shadow.bindings_path, run.bindings_path),
            (shadow.arrow_compile_report_path, run.arrow_compile_report_path),
            (
                shadow.powerpoint_arrow_readback_path,
                run.powerpoint_arrow_readback_path,
            ),
            (
                shadow.qa_dir / "arrow-composition-audit.json",
                run.qa_dir / "arrow-composition-audit.json",
            ),
            (shadow.primitive_audit_path, run.primitive_audit_path),
            (shadow.layout_audit_path, run.layout_audit_path),
            (shadow.provider_capabilities_path, run.provider_capabilities_path),
            (shadow.qa_dir / "regions-report.json", run.qa_dir / "regions-report.json"),
            (
                source_scene_snapshot,
                run.qa_dir / "powerpoint-live-source-scene.json",
            ),
            (
                inventory_report_path,
                run.qa_dir / "powerpoint-live-inventory.json",
            ),
            (
                operation_receipt_path,
                run.qa_dir / "powerpoint-live-operation-receipt.json",
            ),
            (shadow.live_bridge_path, run.live_bridge_path),
            (shadow.live_evidence_path, run.live_evidence_path),
            (
                shadow.qa_dir / "live-save-reopen-summary.json",
                run.qa_dir / "live-save-reopen-summary.json",
            ),
            (shadow.meta_path, run.meta_path),
            (shadow.revision_receipt_path, run.revision_receipt_path),
        ]
        if math_summary_path is not None:
            publication_files.append(
                (math_summary_path, run.qa_dir / "math-summary.json")
            )
        if live_render is not None:
            publication_files.insert(1, (shadow.render_png, run.render_png))
        _publish_file_set_atomically(publication_files, transaction_root)
        return published_evidence


def _operation_receipt_blockers(
    run: common.Run, evidence: dict[str, Any]
) -> list[str]:
    receipt_path = run.qa_dir / "powerpoint-live-operation-receipt.json"
    if not receipt_path.is_file():
        return ["live-operation-receipt-missing"]
    blockers: list[str] = []
    if evidence.get("operation_receipt_path") != (
        "qa/powerpoint-live-operation-receipt.json"
    ):
        blockers.append("live-operation-receipt-path-mismatch")
    if evidence.get("operation_receipt_sha256") != common.sha256_file(
        receipt_path
    ):
        blockers.append("live-evidence-operation-receipt-mismatch")
    try:
        receipt = read_json(receipt_path)
    except ContractError:
        return blockers + ["live-operation-receipt-invalid"]
    required_top_level = {
        "schema_version",
        "kind",
        "provider",
        "publication_mode",
        "session_id",
        "target_id",
        "event_index_base",
        "begin_event_index",
        "operation_event_index",
        "operation_log_path",
        "operation_log_sha256",
        "begin_event",
        "begin_event_sha256",
        "matching_begin_event_sha256",
        "operation_event",
        "operation_event_sha256",
        "matching_operation_event_sha256",
    }
    if not required_top_level.issubset(receipt):
        blockers.append("live-operation-receipt-fields-missing")
        return list(dict.fromkeys(blockers))
    if (
        receipt.get("kind") != "powerpoint_live_operation_receipt"
        or receipt.get("provider") != "powerpoint-live"
        or receipt.get("provider") != evidence.get("provider")
        or receipt.get("publication_mode") != evidence.get("publication_mode")
        or receipt.get("session_id") != evidence.get("session_id")
        or receipt.get("target_id") != evidence.get("target_id")
    ):
        blockers.append("live-operation-receipt-binding-mismatch")
    begin_index = receipt.get("begin_event_index")
    operation_index = receipt.get("operation_event_index")
    if (
        receipt.get("event_index_base") != 0
        or not isinstance(begin_index, int)
        or isinstance(begin_index, bool)
        or not isinstance(operation_index, int)
        or isinstance(operation_index, bool)
        or begin_index < 0
        or operation_index <= begin_index
    ):
        blockers.append("live-operation-receipt-order-invalid")
    begin_event = receipt.get("begin_event")
    operation_event = receipt.get("operation_event")
    if not isinstance(begin_event, dict) or not isinstance(operation_event, dict):
        blockers.append("live-operation-receipt-fields-missing")
        return list(dict.fromkeys(blockers))
    if receipt.get("begin_event_sha256") != _canonical_json_sha256(begin_event):
        blockers.append("live-operation-receipt-event-digest-mismatch")
    if receipt.get("operation_event_sha256") != _canonical_json_sha256(
        operation_event
    ):
        blockers.append("live-operation-receipt-event-digest-mismatch")
    for field in (
        "matching_begin_event_sha256",
        "matching_operation_event_sha256",
    ):
        if not _is_sha256(receipt.get(field)):
            blockers.append("live-operation-receipt-event-digest-mismatch")
    begin_required = {"event", "revision", "target_id", "scene_graph_sha256"}
    mode = receipt.get("publication_mode")
    operation_required = {
        "event",
        "candidate_path",
        "candidate_sha256",
        "reopened_inventory_sha256",
    }
    if mode == "save-reopen-only":
        operation_required.update({"tool", "revision"})
        expected_operation = "save_candidate"
    elif mode == "finalizer":
        operation_required.update({"preview_path", "preview_sha256"})
        expected_operation = "finalize_target_prepared"
    else:
        expected_operation = None
        blockers.append("live-operation-receipt-binding-mismatch")
    if not begin_required.issubset(begin_event) or not operation_required.issubset(
        operation_event
    ):
        blockers.append("live-operation-receipt-fields-missing")
    expected_adapter_hash = None
    if run.live_bridge_path.is_file():
        expected_adapter_hash = read_json(run.live_bridge_path).get(
            "adapter_scene_sha256"
        )
    if (
        begin_event.get("event") != "begin_session"
        or begin_event.get("target_id") != evidence.get("target_id")
        or begin_event.get("scene_graph_sha256") != expected_adapter_hash
        or operation_event.get("event") != expected_operation
        or operation_event.get("candidate_sha256")
        != evidence.get("candidate_sha256")
        or operation_event.get("reopened_inventory_sha256")
        != evidence.get("reopened_inventory_sha256")
        or (
            mode == "save-reopen-only"
            and operation_event.get("tool") != "powerpoint_save_candidate"
        )
        or (
            mode == "finalizer"
            and operation_event.get("preview_sha256")
            != evidence.get("render_sha256")
        )
    ):
        blockers.append("live-operation-receipt-binding-mismatch")
    if not _is_sha256(receipt.get("operation_log_sha256")):
        blockers.append("live-operation-receipt-log-digest-invalid")
    log_relative = receipt.get("operation_log_path")
    if not isinstance(log_relative, str):
        blockers.append("live-operation-receipt-path-mismatch")
    else:
        operation_log = (run.live_case_dir / log_relative).resolve()
        try:
            operation_log.relative_to(run.live_case_dir.resolve())
        except ValueError:
            blockers.append("live-operation-receipt-path-mismatch")
        else:
            # The receipt remains sufficient after build/session cleanup.  If
            # the transient log still exists, however, any drift is blocking.
            if operation_log.is_file() and common.sha256_file(
                operation_log
            ) != receipt.get("operation_log_sha256"):
                blockers.append("live-operation-receipt-log-drift")
    return list(dict.fromkeys(blockers))


def _live_evidence_blockers(
    run: common.Run,
    evidence: dict[str, Any],
    required_regions: list[str],
) -> list[str]:
    blockers: list[str] = []
    root_hash = common.sha256_file(run.pptx_path)
    bindings = read_json(run.bindings_path)
    if evidence.get("reference_sha256") != run.load_meta()["source_sha256"]:
        blockers.append("live-evidence-reference-mismatch")
    if evidence.get("saved_reopened") is not True:
        blockers.append("live-save-reopen-missing")
    if evidence.get("bindings_complete") is not True:
        blockers.append("live-bindings-incomplete")
    if evidence.get("render_finalizer_bound") is not True:
        blockers.append("live-render-finalizer-unverified")
    blockers.extend(_operation_receipt_blockers(run, evidence))
    if bindings.get("bindings_complete") is not True:
        blockers.append("live-root-bindings-incomplete")
    if bindings.get("saved_reopened") is not True:
        blockers.append("live-root-save-reopen-missing")
    provider_capabilities_path = run.provider_capabilities_path
    if provider_capabilities_path.is_file():
        provider = read_json(provider_capabilities_path).get("powerpoint_live", {})
        if provider.get("arrow_authoring_allowed") is not True and evidence.get("arrow_mutations") is not False:
            blockers.append("live-unverified-arrow-authoring")
    for field, code in (
        ("candidate_sha256", "live-candidate-hash-mismatch"),
        ("reopened_artifact_sha256", "live-reopened-hash-mismatch"),
        ("binding_artifact_sha256", "live-binding-evidence-hash-mismatch"),
    ):
        if evidence.get(field) != root_hash:
            blockers.append(code)
    if bindings.get("artifact_sha256") != root_hash:
        blockers.append("live-binding-artifact-hash-mismatch")
    evidence_hashes = (
        ("bindings_sha256", run.bindings_path, "live-evidence-bindings-mismatch"),
        ("scene_sha256", run.scene_path, "live-evidence-scene-mismatch"),
        (
            "source_scene_snapshot_sha256",
            run.qa_dir / "powerpoint-live-source-scene.json",
            "live-evidence-source-scene-mismatch",
        ),
        (
            "bridge_manifest_sha256",
            run.live_bridge_path,
            "live-evidence-bridge-manifest-mismatch",
        ),
        (
            "powerpoint_live_inventory_sha256",
            run.qa_dir / "powerpoint-live-inventory.json",
            "live-evidence-inventory-file-mismatch",
        ),
        (
            "arrow_readback_sha256",
            run.qa_dir / "powerpoint-arrow-readback.json",
            "live-evidence-arrow-readback-mismatch",
        ),
        (
            "arrow_compile_report_sha256",
            run.qa_dir / "arrow-compile-report.json",
            "live-evidence-arrow-compile-mismatch",
        ),
        (
            "primitive_audit_sha256",
            run.qa_dir / "primitive-audit.json",
            "live-evidence-primitive-audit-mismatch",
        ),
        (
            "layout_audit_sha256",
            run.qa_dir / "layout-audit.json",
            "live-evidence-layout-audit-mismatch",
        ),
        (
            "provider_capabilities_sha256",
            run.qa_dir / "provider-capabilities.json",
            "live-evidence-provider-capabilities-mismatch",
        ),
        (
            "regions_report_sha256",
            run.qa_dir / "regions-report.json",
            "live-evidence-regions-mismatch",
        ),
        ("autofigure_render_sha256", run.render_png, "live-evidence-render-mismatch"),
    )
    for field, path, code in evidence_hashes:
        if not path.is_file() or evidence.get(field) != common.sha256_file(path):
            blockers.append(code)
    math_summary_path = run.qa_dir / "math-summary.json"
    if math_summary_path.is_file():
        if evidence.get("math_summary_sha256") != common.sha256_file(
            math_summary_path
        ):
            blockers.append("live-evidence-math-summary-mismatch")
    elif evidence.get("math_summary_sha256") is not None:
        blockers.append("live-evidence-math-summary-mismatch")
    inventory_path = run.qa_dir / "powerpoint-live-inventory.json"
    if inventory_path.is_file():
        inventory_report = read_json(inventory_path)
        live_inventory = inventory_report.get("live_inventory")
        reopened_inventory = inventory_report.get("reopened_inventory")
        if not isinstance(live_inventory, dict) or not isinstance(
            reopened_inventory, dict
        ):
            blockers.append("live-evidence-inventory-content-missing")
        else:
            live_digest = _inventory_sha256(live_inventory)
            reopened_digest = _inventory_sha256(reopened_inventory)
            if live_digest != inventory_report.get("live_inventory_sha256"):
                blockers.append("live-evidence-live-inventory-digest-mismatch")
            if reopened_digest != inventory_report.get(
                "reopened_inventory_sha256"
            ):
                blockers.append("live-evidence-reopened-inventory-digest-mismatch")
            if live_inventory != reopened_inventory or live_digest != reopened_digest:
                blockers.append("live-evidence-reopened-inventory-content-mismatch")
            if evidence.get("live_inventory_sha256") != live_digest:
                blockers.append("live-evidence-live-inventory-summary-mismatch")
            if evidence.get("reopened_inventory_sha256") != reopened_digest:
                blockers.append("live-evidence-reopened-inventory-summary-mismatch")
        if inventory_report.get("candidate_sha256") != root_hash:
            blockers.append("live-evidence-inventory-candidate-mismatch")
    source_scene_path = run.qa_dir / "powerpoint-live-source-scene.json"
    if source_scene_path.is_file() and run.live_bridge_path.is_file():
        bridge_manifest = read_json(run.live_bridge_path)
        if bridge_manifest.get("source_scene_sha256") != common.sha256_file(
            source_scene_path
        ):
            blockers.append("live-evidence-bridge-source-scene-mismatch")
    region_results = evidence.get("regions", {})
    blockers.extend(f"live-region:{region_id}" for region_id in required_regions if region_results.get(region_id) not in ("REGION_PASS", "pass"))
    return list(dict.fromkeys(blockers))


def write_live_save_reopen_summary(
    run: common.Run, evidence: dict[str, Any]
) -> dict[str, Any]:
    """Write the canonical compact summary for an already verified Live publication.

    The summary is derived only from the published, hash-bound Live evidence and the
    current case-root artifacts.  It must never preserve a historical failed attempt
    after a later candidate has been successfully saved, reopened, and published.
    """

    blockers = _live_evidence_blockers(run, evidence, [])
    allowed_blockers = (
        {"live-render-finalizer-unverified"}
        if evidence.get("render_finalizer_bound") is False
        else set()
    )
    unexpected_blockers = [
        blocker for blocker in blockers if blocker not in allowed_blockers
    ]
    if unexpected_blockers:
        raise common.fail(
            "cannot summarize invalid PowerPoint Live evidence: "
            + ", ".join(unexpected_blockers)
        )
    bindings = read_json(run.bindings_path)
    inventory = read_json(run.qa_dir / "powerpoint-live-inventory.json")
    receipt = read_json(run.qa_dir / "powerpoint-live-operation-receipt.json")
    live_inventory = inventory.get("live_inventory")
    reopened_inventory = inventory.get("reopened_inventory")
    binding_rows = bindings.get("bindings", [])
    native_math_count = sum(
        item.get("object_kind") == "native-math" for item in binding_rows
    )
    summary = {
        "schema_version": "1.2.0",
        "kind": "powerpoint_live_save_reopen_summary",
        "created_at": utc_now(),
        "provider": evidence["provider"],
        "provider_version": evidence.get("server_version"),
        "task_mode": run.load_meta().get("task_mode", "RECONSTRUCT_1TO1"),
        "target_id": evidence["target_id"],
        "session_id": evidence.get("session_id"),
        "reference_sha256": evidence["reference_sha256"],
        "current_root_candidate_sha256": common.sha256_file(run.pptx_path),
        "live_candidate_sha256": evidence["candidate_sha256"],
        "reopened_artifact_sha256": evidence["reopened_artifact_sha256"],
        "live_inventory_sha256": evidence["live_inventory_sha256"],
        "reopened_inventory_sha256": evidence["reopened_inventory_sha256"],
        "live_attempt_saved_reopened": True,
        "live_attempt_inventory_equal": live_inventory == reopened_inventory,
        "live_attempt_bindings_complete": True,
        "saved_reopened": True,
        "bindings_complete": True,
        "bound_object_count": len(binding_rows),
        "native_math_object_count": native_math_count,
        "missing_bound_objects": 0,
        "published_to_case_root": True,
        "publication_mode": evidence.get("publication_mode", "finalizer"),
        "render_finalizer_bound": evidence.get("render_finalizer_bound") is True,
        "live_render_published": evidence.get("live_render_published") is True,
        "strict_live_blockers": blockers,
        "operation_receipt_path": evidence["operation_receipt_path"],
        "operation_receipt_sha256": evidence["operation_receipt_sha256"],
        "operation_log_sha256": receipt["operation_log_sha256"],
        "begin_event_index": receipt["begin_event_index"],
        "operation_event_index": receipt["operation_event_index"],
        "host_reopen_method": evidence["host_reopen_method"],
        "automatic_status": evidence.get("automatic_status"),
        "release_authority": evidence.get("release_authority", "NONE"),
        "region_results": evidence.get("regions", {}),
        "live_layout_audit": {
            "hard_failure_count_after_correction": evidence.get(
                "backend_audit_hard_failure_count"
            )
        },
        "live_evidence_sha256": common.sha256_file(run.live_evidence_path),
        "note": (
            "Canonical summary of the published PowerPoint Live save/reopen. "
            "A save_candidate-only publication is intermediate and retains "
            "live-render-finalizer-unverified; release approval remains "
            "independent of host round-trip success."
        ),
    }
    write_json(run.qa_dir / "live-save-reopen-summary.json", summary)
    return summary


def live_evidence_passes(run: common.Run, required_regions: list[str]) -> tuple[bool, list[str]]:
    if not run.live_evidence_path.is_file():
        return False, ["live-evidence-missing"]
    evidence = read_json(run.live_evidence_path)
    blockers = _live_evidence_blockers(run, evidence, required_regions)
    return not blockers, blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure repair", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--reopened", type=Path)
    parser.add_argument("--render", type=Path)
    parser.add_argument(
        "--save-reopen-only",
        action="store_true",
        help=(
            "publish a verified PowerPoint save_candidate round-trip as "
            "intermediate evidence; never marks the render finalizer-bound"
        ),
    )
    args = parser.parse_args(argv)
    run = common.open_run(args.run_dir)
    set_modes(run, backend_mode="hybrid")
    if args.evidence:
        if args.candidate:
            publisher = (
                publish_live_save_reopen_candidate
                if args.save_reopen_only
                else publish_live_candidate
            )
            publisher(
                run,
                args.evidence,
                args.candidate,
                reopened_path=args.reopened,
                render_path=args.render,
            )
        else:
            if args.save_reopen_only:
                raise common.fail(
                    "--save-reopen-only requires --candidate and --reopened"
                )
            ingest_live_evidence(run, args.evidence)
        sys.stdout.write(f"live evidence 已接收: {run.live_evidence_path}\n")
        return 0
    transition(run, "repairing", "powerpoint-live-repair-requested")
    request = build_live_request(run)
    sys.stdout.write(f"已生成 PowerPoint-live 修复请求，失败区域 {len(request['failed_regions'])} 个: {run.live_request_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
