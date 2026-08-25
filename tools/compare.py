"""Generate a portable, hash-bound A/B report for two input routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import read_json, utc_now, write_json


NON_NATIVE_BINDING_KINDS = {"arrow-group", "arrowhead-fallback", "atomic-raster"}


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def _binding_summary(run: common.Run) -> dict[str, Any]:
    payload = _safe_json(run.bindings_path)
    bindings = [item for item in payload.get("bindings", []) if isinstance(item, dict)]
    kinds = Counter(item.get("object_kind", "unknown") for item in bindings)
    arrow_kinds = {"connector", "line", "freeform-arrow", "arrowhead-fallback", "arrow-group"}
    native_editable = [
        item
        for item in bindings
        if item.get("editable") is True
        and item.get("object_kind", "unknown") not in NON_NATIVE_BINDING_KINDS
    ]
    unknown_editability = [item for item in bindings if not isinstance(item.get("editable"), bool)]
    non_native = [
        item
        for item in bindings
        if item.get("editable") is False
        or item.get("object_kind", "unknown") in NON_NATIVE_BINDING_KINDS
    ]
    coverage = round(100.0 * len(native_editable) / len(bindings), 4) if bindings else None
    return {
        "object_count": len(bindings),
        "editable_text": kinds["text"],
        "editable_formulas": kinds["native-math"],
        "editable_arrows": sum(kinds[kind] for kind in arrow_kinds),
        "atomic_rasters": kinds["atomic-raster"],
        "saved_reopened": payload.get("saved_reopened") is True,
        "package_reopened": payload.get("package_reopened") is True,
        "bindings_complete": payload.get("bindings_complete") is True,
        "object_kinds": dict(sorted(kinds.items())),
        "native_object_coverage": {
            "native_editable_objects": len(native_editable),
            "total_bound_objects": len(bindings),
            "coverage_pct": coverage,
            "non_native_or_fallback_objects": len(non_native),
            "unknown_editability_objects": len(unknown_editability),
            "excluded_object_kinds": sorted(NON_NATIVE_BINDING_KINDS),
            "definition": (
                "bindings with editable=true excluding raster and multi-object arrow fallback kinds"
            ),
        },
    }


def _region_summary(run: common.Run) -> dict[str, Any]:
    payload = _safe_json(run.qa_dir / "regions-report.json")
    regions = []
    for item in payload.get("regions", []):
        probes = item.get("color_probes", [])
        regions.append(
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "critical": item.get("critical") is True,
                "ssim": item.get("ssim"),
                "edge_iou": item.get("edge_iou"),
                "mean_abs_rgb_delta": item.get("mean_abs_rgb_delta"),
                "thresholds": item.get("thresholds", {}),
                "color_probe_mean_delta_e00": (
                    round(sum(probe.get("delta_e00", 0.0) for probe in probes) / len(probes), 4)
                    if probes
                    else None
                ),
                "color_probe_failures": sum(probe.get("pass") is not True for probe in probes),
                "pass": item.get("pass") is True,
            }
        )
    critical = [item for item in regions if item["critical"]]

    def numeric_values(key: str) -> list[float]:
        return [
            float(item[key])
            for item in critical
            if isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool)
        ]

    ssim_values = numeric_values("ssim")
    edge_values = numeric_values("edge_iou")
    rgb_values = numeric_values("mean_abs_rgb_delta")
    delta_values = numeric_values("color_probe_mean_delta_e00")
    return {
        "critical_regions": len(critical),
        "critical_passed": sum(item["pass"] for item in critical),
        "strict_pass": payload.get("strict_pass") is True,
        "critical_metrics": {
            "failed_region_ids": [item["id"] for item in critical if not item["pass"]],
            "min_ssim": min(ssim_values) if ssim_values else None,
            "min_edge_iou": min(edge_values) if edge_values else None,
            "max_mean_abs_rgb_delta": max(rgb_values) if rgb_values else None,
            "max_color_probe_mean_delta_e00": max(delta_values) if delta_values else None,
            "regions": critical,
        },
        "regions": regions,
    }


def _source_gate_summary(run: common.Run) -> dict[str, Any]:
    payload = _safe_json(run.source_gate_report_path)
    route_gate = payload.get("route_gate") if isinstance(payload.get("route_gate"), dict) else {}
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    blockers = [item for item in payload.get("blockers", []) if isinstance(item, str)]
    return {
        "report_present": bool(payload),
        "decision": payload.get("decision", "not_run"),
        "pass": payload.get("pass") is True,
        "next_action": payload.get("next_action"),
        "input_route": route_gate.get("input_route"),
        "candidate_role": route_gate.get("candidate_role"),
        "seed_gate_status": route_gate.get("seed_gate_status"),
        "candidate_sha256": candidate.get("sha256"),
        "blockers": blockers,
    }


def _seed_summary(run: common.Run, provenance: dict[str, Any]) -> dict[str, Any]:
    meta = run.load_meta()
    if meta.get("input_route") == "reference-only":
        return {
            "applicable": False,
            "availability": "not_applicable",
            "provenance_recorded": False,
            "declared_sha256": None,
            "actual_sha256": None,
            "hash_matches": None,
            "exact_bytes_available": False,
            "admission_blocker_count": 0,
            "admission_evidence_verified": None,
        }

    record = (
        provenance.get("external_svg_seed")
        if isinstance(provenance.get("external_svg_seed"), dict)
        else {}
    )
    gate_report = _safe_json(run.external_seed_source_gate_report_path)
    gate_record = (
        provenance.get("external_seed_gate")
        if isinstance(provenance.get("external_seed_gate"), dict)
        else {}
    )
    declared_sha256 = record.get("sha256")
    actual_sha256 = common.sha256_file(run.external_seed_svg) if run.external_seed_svg.is_file() else None
    # Source-gate provenance binds the normalized JSON payload, not the
    # pretty-printed file bytes (see contracts.record_source_gate_provenance).
    gate_report_sha256 = (
        hashlib.sha256(
            json.dumps(
                gate_report,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if gate_report
        else None
    )
    gate_candidate = (
        gate_report.get("candidate") if isinstance(gate_report.get("candidate"), dict) else {}
    )
    gate_reference = (
        gate_report.get("reference") if isinstance(gate_report.get("reference"), dict) else {}
    )
    gate_route = (
        gate_report.get("route_gate")
        if isinstance(gate_report.get("route_gate"), dict)
        else {}
    )
    admission_evidence_verified = bool(
        gate_report
        and gate_report.get("schema_version") == "4.0.0"
        and gate_report.get("kind") == "source_gate_report"
        and gate_route.get("candidate_role") == "external-seed"
        and isinstance(actual_sha256, str)
        and gate_candidate.get("sha256") == actual_sha256
        and gate_candidate.get("expected_sha256") == actual_sha256
        and gate_reference.get("actual_sha256") == meta.get("source_sha256")
        and gate_record.get("candidate_sha256") == actual_sha256
        and gate_record.get("report_sha256") == gate_report_sha256
    )
    hash_matches = (
        actual_sha256 == declared_sha256
        if isinstance(actual_sha256, str) and isinstance(declared_sha256, str)
        else None
    )
    unavailable_declared = record.get("availability") == "seed_unavailable" or any(
        isinstance(event, dict) and event.get("event") == "seed-unavailable"
        for event in provenance.get("events", [])
    )
    if unavailable_declared:
        availability = "seed_unavailable"
    elif actual_sha256 is None:
        availability = "missing_bytes" if record else "missing_provenance"
    elif not isinstance(declared_sha256, str):
        availability = "unbound_bytes"
    elif hash_matches:
        availability = "available"
    else:
        availability = "hash_mismatch"
    admission_blockers = [
        item
        for item in (
            gate_report.get("blockers", [])
            if gate_report
            else gate_record.get("blockers", [])
        )
        if isinstance(item, str)
    ]
    reported_admission_decision = gate_report.get("decision") or gate_record.get("decision")
    return {
        "applicable": True,
        "availability": availability,
        "provenance_recorded": bool(record),
        "declared_sha256": declared_sha256,
        "actual_sha256": actual_sha256,
        "hash_matches": hash_matches,
        "exact_bytes_available": availability == "available",
        "admission_report_present": bool(gate_report),
        "admission_reported_decision": reported_admission_decision,
        "admission_decision": (
            reported_admission_decision if admission_evidence_verified else "unverified"
        ),
        "admission_evidence_verified": admission_evidence_verified,
        "admission_pass": (
            gate_report.get("pass") is True
            if gate_report
            else gate_record.get("pass") is True
            if gate_record
            else None
        ),
        "admission_next_action": gate_report.get("next_action") or gate_record.get("next_action"),
        "admission_blocker_count": len(admission_blockers),
        "admission_blockers": admission_blockers,
        "admission_report_sha256": gate_record.get("report_sha256"),
        "admission_report_actual_sha256": gate_report_sha256,
    }


def _candidate_activity_summary(provenance: dict[str, Any]) -> dict[str, Any]:
    history = [
        item for item in provenance.get("candidate_history", []) if isinstance(item, dict)
    ]
    roles = Counter(str(item.get("role", "unknown")) for item in history)
    hashes = [item.get("sha256") for item in history if isinstance(item.get("sha256"), str)]
    hash_counts = Counter(hashes)
    events = [item for item in provenance.get("events", []) if isinstance(item, dict)]
    event_types = Counter(str(item.get("event", "unknown")) for item in events)
    explicit_reuse = sum("reuse" in str(item.get("event", "")).lower() for item in events)
    duplicate_ingestions = sum(count - 1 for count in hash_counts.values() if count > 1)
    repair_events = sum(
        item.get("role") == "repair-candidate"
        or "repair" in str(item.get("event", "")).lower()
        for item in events
    )
    repair_ingestions = roles["repair-candidate"]
    return {
        "candidate_ingestions": len(history),
        "role_counts": dict(sorted(roles.items())),
        "unique_candidate_hashes": len(hash_counts),
        "reused_candidate_ingestions": duplicate_ingestions,
        "reuse_event_count": explicit_reuse,
        "repair_event_count": repair_events,
        "repair_candidate_ingestions": repair_ingestions,
        "provenance_event_counts": dict(sorted(event_types.items())),
    }


def _blocker_summary(run: common.Run, meta: dict[str, Any]) -> dict[str, Any]:
    inventory = _safe_json(run.blockers_path)
    inventory_items = [
        item for item in inventory.get("blockers", []) if isinstance(item, str)
    ]
    validation = meta.get("validation") if isinstance(meta.get("validation"), dict) else {}
    validation_items = [
        item for item in validation.get("blockers", []) if isinstance(item, str)
    ]
    sources: dict[str, list[str]] = {}
    if inventory_items:
        sources[run.blockers_path.name] = inventory_items
    if validation_items:
        sources["run.json:validation"] = validation_items
    if run.qa_dir.is_dir():
        for path in sorted(run.qa_dir.glob("*.json")):
            if path == run.blockers_path:
                continue
            payload = _safe_json(path)
            report_items = [
                item for item in payload.get("blockers", []) if isinstance(item, str)
            ]
            report_items.extend(
                item
                for item in payload.get("strict_live_blockers", [])
                if isinstance(item, str)
            )
            if report_items:
                sources[path.name] = list(dict.fromkeys(report_items))
    evidence_items = list(dict.fromkeys(item for values in sources.values() for item in values))
    # qa/blockers.json is the final strict inventory for the active revision.
    # Other reports remain useful evidence, but immutable seed-admission findings
    # and stale intermediate reports must not inflate the current strict count.
    strict_items = inventory_items if inventory else validation_items
    return {
        "count": len(strict_items),
        "items": strict_items,
        "strict_count": len(strict_items),
        "strict_items": strict_items,
        "all_evidence_count": len(evidence_items),
        "all_evidence_items": evidence_items,
        "sources": sources,
        "inventory_present": bool(inventory),
        "inventory_kind": inventory.get("kind"),
        "inventory_revision_id": inventory.get("revision_id"),
        "validation_profile": validation.get("profile"),
        "validation_status": validation.get("status", "not_run"),
    }


def _live_summary(run: common.Run, bindings: dict[str, Any]) -> dict[str, Any]:
    live = _safe_json(run.qa_dir / "live-save-reopen-summary.json")
    candidate_sha256 = live.get("live_candidate_sha256")
    reopened_sha256 = live.get("reopened_artifact_sha256")
    hashes_match = (
        candidate_sha256 == reopened_sha256
        if isinstance(candidate_sha256, str) and isinstance(reopened_sha256, str)
        else None
    )
    current_pptx_sha256 = common.sha256_file(run.pptx_path) if run.pptx_path.is_file() else None
    current_artifact_hashes_match = bool(
        isinstance(current_pptx_sha256, str)
        and candidate_sha256 == current_pptx_sha256
        and reopened_sha256 == current_pptx_sha256
    )
    current_revision_saved_reopened = bool(
        bindings["saved_reopened"]
        and bindings["package_reopened"]
        and current_artifact_hashes_match
    )
    return {
        "report_present": bool(live),
        "bindings_package_reopened": bindings["package_reopened"],
        "bindings_saved_reopened": bindings["saved_reopened"],
        "live_attempt_saved_reopened": live.get("live_attempt_saved_reopened") is True,
        "live_summary_saved_reopened": live.get("saved_reopened") is True,
        # The case-root bindings are the publication authority.  A historical
        # Live attempt may prove that some candidate reopened, but it cannot
        # certify that the currently published artifact did.
        "saved_reopened": current_revision_saved_reopened,
        "package_reopened": bindings["package_reopened"],
        "bindings_complete": bindings["bindings_complete"],
        "live_summary_agrees_with_bindings": (
            live.get("saved_reopened") is bindings["saved_reopened"] if live else None
        ),
        "live_candidate_sha256": candidate_sha256,
        "reopened_artifact_sha256": reopened_sha256,
        "candidate_reopened_hashes_match": hashes_match,
        "current_pptx_sha256": current_pptx_sha256,
        "current_artifact_hashes_match": current_artifact_hashes_match,
        "published_to_case_root": live.get("published_to_case_root") is True,
        "automatic_status": live.get("automatic_status"),
        "strict_live_blockers": [
            item for item in live.get("strict_live_blockers", []) if isinstance(item, str)
        ],
        "backend_hard_failures_after_correction": live.get("live_layout_audit", {}).get(
            "hard_failure_count_after_correction"
        ),
        "region_results": live.get("region_results"),
    }


def _asset_summary(run: common.Run) -> dict[str, Any]:
    audit = _safe_json(run.qa_dir / "asset-spec-audit.json")
    receipt = _safe_json(run.qa_dir / "asset-contract-receipt.json")
    audit_contract = audit.get("asset_contract_sha256")
    receipt_contract = receipt.get("asset_contract_sha256")
    hashes_match = (
        audit_contract == receipt_contract
        if isinstance(audit_contract, str) and isinstance(receipt_contract, str)
        else None
    )
    meta = run.load_meta()
    active = meta.get("active_revision") if isinstance(meta.get("active_revision"), dict) else {}
    current_pptx_sha256 = common.sha256_file(run.pptx_path) if run.pptx_path.is_file() else None
    lineage_closed = bool(
        audit
        and receipt
        and audit.get("pass") is True
        and receipt.get("status") == "PASS"
        and hashes_match is True
        and audit.get("reference_sha256") == meta.get("source_sha256")
        and receipt.get("reference_sha256") == meta.get("source_sha256")
        and audit.get("revision_id") == active.get("revision_id")
        and audit.get("scene_sha256") == active.get("scene_sha256")
        and audit.get("compiler_fingerprint") == active.get("compiler_fingerprint")
        and audit.get("pptx_sha256") == current_pptx_sha256
    )
    return {
        "audit_present": bool(audit),
        "audit_pass": audit.get("pass") is True,
        "asset_spec_count": audit.get("asset_spec_count"),
        "logical_group_binding_count": audit.get("logical_group_binding_count"),
        "member_binding_count": audit.get("member_binding_count"),
        "pptx_readback_count": audit.get("pptx_readback_count"),
        "opportunity_count": audit.get("opportunity_count"),
        "blocker_count": len(audit.get("blockers", [])),
        "receipt_present": bool(receipt),
        "receipt_status": receipt.get("status"),
        "asset_contract_sha256": audit_contract or receipt_contract,
        "receipt_contract_sha256": receipt_contract,
        "audit_receipt_hashes_match": hashes_match,
        "lineage_closed": lineage_closed,
        "current_pptx_sha256": current_pptx_sha256,
        "inventory_sha256": audit.get("inventory_sha256") or receipt.get("inventory_sha256"),
    }


def _revision_summary(
    run: common.Run,
    meta: dict[str, Any],
    binding_payload: dict[str, Any],
    blockers: dict[str, Any],
) -> dict[str, Any]:
    active = meta.get("active_revision") if isinstance(meta.get("active_revision"), dict) else {}
    receipt = _safe_json(run.revision_receipt_path)
    binding_revision = (
        binding_payload.get("scene_revision")
        if isinstance(binding_payload.get("scene_revision"), dict)
        else {}
    )
    identities = [
        (payload.get("revision_id"), payload.get("scene_sha256"), payload.get("compiler_fingerprint"))
        for payload in (active, receipt, binding_revision)
        if payload
    ]
    identity_complete = len(identities) == 3 and all(all(value for value in row) for row in identities)
    workflow = meta.get("workflow") if isinstance(meta.get("workflow"), dict) else {}
    return {
        "active_revision": active or None,
        "revision_receipt": receipt or None,
        "bindings_scene_revision": binding_revision or None,
        "identity_complete": identity_complete,
        "identities_match": identity_complete and len(set(identities)) == 1,
        "workflow_revision": workflow.get("revision"),
        "workflow_history_count": len(workflow.get("history", [])),
        "lineage_blockers": [
            item for item in blockers["items"] if item.startswith("lineage:")
        ],
    }


def _arrow_summary(run: common.Run) -> dict[str, Any]:
    # ArrowSpec compilation and OOXML readback are authoritative for logical
    # arrows.  The legacy SVG audit only counts marker-bearing paths, so it
    # legitimately reports zero for a single closed block-arrow freeform.  Do
    # not let that diagnostic erase native arrows from route comparisons.
    compile_report = _safe_json(run.qa_dir / "arrow-compile-report.json")
    readback_report = _safe_json(run.qa_dir / "powerpoint-arrow-readback.json")
    visual_report = _safe_json(run.qa_dir / "arrow-visual-report.json")
    legacy_audit = _safe_json(run.qa_dir / "arrows-audit.json")
    findings = legacy_audit.get("findings", [])
    codes = Counter(item.get("code", "unknown") for item in findings)
    compile_count = compile_report.get("arrow_count")
    readback_count = readback_report.get("arrow_count")
    logical_count = (
        compile_count
        if isinstance(compile_count, int)
        else legacy_audit.get("arrows", 0)
    )
    return {
        "arrows": logical_count,
        "logical_arrow_count": logical_count,
        "compile_report_present": bool(compile_report),
        "compile_pass": compile_report.get("pass") is True,
        "compiled_arrow_count": compile_count,
        "readback_report_present": bool(readback_report),
        "readback_pass": readback_report.get("pass") is True,
        "readback_arrow_count": readback_count,
        "compile_readback_count_match": (
            compile_count == readback_count
            if isinstance(compile_count, int) and isinstance(readback_count, int)
            else None
        ),
        "visual_report_present": bool(visual_report),
        "visual_pass": visual_report.get("pass") is True,
        "visual_blocker_count": len(visual_report.get("blockers", [])),
        "legacy_svg_marker_arrow_count": legacy_audit.get("arrows", 0),
        "legacy_finding_count": len(findings),
        "legacy_finding_codes": dict(sorted(codes.items())),
    }


def _inventory_truth_summary(run: common.Run) -> dict[str, Any]:
    """Read-only view of the freeze receipt's truth hashes; no gating here."""

    receipt = _safe_json(run.qa_dir / "reference-inventory-receipt.json")
    return {
        "inventory_sha256": receipt.get("inventory_sha256"),
        "oracle_sha256": receipt.get("oracle_sha256"),
    }


def _case_summary(run: common.Run) -> dict[str, Any]:
    meta = run.load_meta()
    provenance = _safe_json(run.provenance_path)
    binding_payload = _safe_json(run.bindings_path)
    bindings = _binding_summary(run)
    blockers = _blocker_summary(run, meta)
    metrics = _safe_json(run.qa_dir / "metrics.json")
    layout = _safe_json(run.layout_audit_path)
    try:
        relative_path = run.root.resolve().relative_to(common.CASES_ROOT.resolve()).as_posix()
    except ValueError:
        relative_path = run.root.name
    return {
        "case": meta["case"],
        "path": relative_path,
        "input_route": meta["input_route"],
        "processing_mode": meta["processing_mode"],
        "workflow_state": meta["workflow"]["state"],
        "validation": meta.get("validation", {}),
        "comparison_group": provenance.get("comparison_group"),
        "reference_sha256": meta["source_sha256"],
        "reference_inventory": _inventory_truth_summary(run),
        "source_gate": _source_gate_summary(run),
        "seed": _seed_summary(run, provenance),
        "candidate_activity": _candidate_activity_summary(provenance),
        "bindings": bindings,
        "assets": _asset_summary(run),
        "arrows": _arrow_summary(run),
        "layout": {
            "pass": layout.get("pass") is True,
            "finding_count": len(layout.get("findings", [])),
        },
        "regions": _region_summary(run),
        "global_diagnostic": {
            key: metrics.get(key)
            for key in ("mean_abs_rgb_delta", "changed_pixel_ratio_pct", "ssim", "edge_iou")
            if key in metrics
        },
        "blockers": blockers,
        "powerpoint_live": _live_summary(run, bindings),
        "revision_lineage": _revision_summary(run, meta, binding_payload, blockers),
    }


def build_comparison(first: common.Run, second: common.Run) -> dict[str, Any]:
    summaries = [_case_summary(first), _case_summary(second)]
    by_route = {item["input_route"]: item for item in summaries}
    if set(by_route) != {"reference-only", "svg-seeded"}:
        raise common.fail("comparison requires exactly one reference-only and one svg-seeded case")
    if len({item["reference_sha256"] for item in summaries}) != 1:
        raise common.fail("comparison cases do not share the same frozen reference hash")
    groups = {item["comparison_group"] for item in summaries}
    if len(groups) != 1 or None in groups:
        raise common.fail("comparison cases require the same non-null provenance comparison_group")

    direct = by_route["reference-only"]
    direct_built = (
        direct["bindings"]["object_count"] > 0
        and direct["bindings"]["saved_reopened"]
        and direct["bindings"]["bindings_complete"]
    )
    direct_strict = direct["validation"].get("status") == "passed"
    if direct_strict:
        conclusion = "reference-only strict validation passed"
    elif direct_built:
        conclusion = "reference-only pipeline completed, but quality is not validated mature"
    else:
        conclusion = "reference-only pipeline is incomplete"
    return {
        "schema_version": "2.1.0",
        "kind": "input_route_ab_comparison",
        "generated_at": utc_now(),
        "task_mode": "RECONSTRUCT_1TO1",
        "comparison_group": groups.pop(),
        "reference_sha256": summaries[0]["reference_sha256"],
        "cases": by_route,
        "conclusion": {
            "reference_only_pipeline_completed": direct_built,
            "reference_only_strict_passed": direct_strict,
            "reference_only_capability_mature": direct_strict,
            "statement": conclusion,
            "global_metrics_are_diagnostic_only": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    def short_hash(value: Any) -> str:
        return f"`{value[:12]}`" if isinstance(value, str) and value else "n/a"

    rows = []
    for route in ("svg-seeded", "reference-only"):
        item = report["cases"][route]
        rows.append(
            "| {route} | `{case}` | `{mode}` | {gate} | {seed} | {admission} | {native} | "
            "{assets} | {regions} | {blockers} | {seed_blockers} | {reopened} | {revision} | "
            "{status} |".format(
                route=route,
                case=item["case"],
                mode=item["processing_mode"],
                gate=item["source_gate"]["decision"],
                seed=item["seed"]["availability"],
                admission=(
                    item["seed"].get("admission_decision")
                    if item["seed"].get("applicable")
                    else "n/a"
                ),
                native=(
                    f"{item['bindings']['native_object_coverage']['coverage_pct']}%"
                    if item["bindings"]["native_object_coverage"]["coverage_pct"] is not None
                    else "n/a"
                ),
                assets=(
                    f"{item['assets']['asset_spec_count']} specs / "
                    f"{item['assets']['pptx_readback_count']} readbacks "
                    f"({'PASS' if item['assets']['audit_pass'] else 'FAIL'})"
                    if item["assets"]["audit_present"]
                    else "missing"
                ),
                regions=(
                    f"{item['regions']['critical_passed']}/{item['regions']['critical_regions']}"
                ),
                blockers=item["blockers"]["strict_count"],
                seed_blockers=item["seed"].get("admission_blocker_count", 0),
                reopened=item["powerpoint_live"]["saved_reopened"],
                revision=(
                    "closed"
                    if item["revision_lineage"]["identities_match"]
                    else "unverified"
                ),
                status=item["validation"].get("status", "not_run"),
            )
        )
    direct = report["cases"]["reference-only"]
    seeded = report["cases"]["svg-seeded"]
    return "\n".join(
        [
            f"# 输入路线 A/B：{report['comparison_group']}",
            "",
            f"冻结参考 SHA-256：`{report['reference_sha256']}`",
            "",
            "| 输入路线 | 案例 | processing mode | 最新 source gate | seed | 原始 seed 准入 | 原生对象覆盖率 | AssetSpec | 关键区通过 | strict blockers | seed 准入 blockers | 正式保存重开 | revision lineage | strict 状态 |",
            "|---|---|---|---|---|---|---:|---|---:|---:|---:|---|---|---|",
            *rows,
            "",
            "## 结论",
            "",
            f"- {report['conclusion']['statement']}。",
            "- 两条路线共用的只有冻结参考图与路线无关验收阈值；reference-only 候选未读取 svg-seeded 候选资产。",
            (
                "- 冻结真值哈希（receipt inventory / reference oracle）：svg-seeded="
                f"{short_hash(seeded['reference_inventory']['inventory_sha256'])}/"
                f"{short_hash(seeded['reference_inventory']['oracle_sha256'])}，reference-only="
                f"{short_hash(direct['reference_inventory']['inventory_sha256'])}/"
                f"{short_hash(direct['reference_inventory']['oracle_sha256'])}；"
                "同参考图的两条路线必须收敛到同一 oracle。"
            ),
            "- 全图均值仅作诊断，任何关键区域失败都会阻止 approved。",
            (
                "- source gate：svg-seeded 当前候选="
                f"{seeded['source_gate']['decision']}，不可变原始 seed 准入="
                f"{seeded['seed'].get('admission_decision')}（"
                f"{seeded['seed'].get('admission_blocker_count', 0)} blockers）；"
                f"reference-only 当前候选={direct['source_gate']['decision']}。"
            ),
            (
                "- 候选活动（复用/修复）：svg-seeded="
                f"{seeded['candidate_activity']['reuse_event_count']}/"
                f"{seeded['candidate_activity']['repair_event_count']}，reference-only="
                f"{direct['candidate_activity']['reuse_event_count']}/"
                f"{direct['candidate_activity']['repair_event_count']}。"
            ),
            (
                "- PowerPoint 保存重开：svg-seeded="
                f"{seeded['powerpoint_live']['saved_reopened']}，reference-only="
                f"{direct['powerpoint_live']['saved_reopened']}（均以案例根 bindings 为准）；"
                "保存重开只证明宿主兼容，"
                "不替代独立质量验收。"
            ),
            (
                "- AssetSpec：svg-seeded="
                f"{seeded['assets']['asset_spec_count']} specs/"
                f"{seeded['assets']['pptx_readback_count']} readbacks/"
                f"pass={seeded['assets']['audit_pass']}，reference-only="
                f"{direct['assets']['asset_spec_count']} specs/"
                f"{direct['assets']['pptx_readback_count']} readbacks/"
                f"pass={direct['assets']['audit_pass']}；合同 receipt 与 audit hash "
                f"闭合分别为 {seeded['assets']['lineage_closed']} / "
                f"{direct['assets']['lineage_closed']}。"
            ),
            "",
            "## 明细",
            "",
            *[
                (
                    f"- {route}: critical min SSIM="
                    f"{item['regions']['critical_metrics']['min_ssim']}, min Edge IoU="
                    f"{item['regions']['critical_metrics']['min_edge_iou']}, strict blockers="
                    f"{item['blockers']['strict_items']}, seed-admission blockers="
                    f"{item['seed'].get('admission_blockers', [])}, revision="
                    f"{item['revision_lineage']['active_revision'] and item['revision_lineage']['active_revision'].get('revision_id')}"
                )
                for route, item in (("svg-seeded", seeded), ("reference-only", direct))
            ],
            "机器可读 JSON 保留每个关键区域的阈值与指标、完整 blocker 清单、候选事件及修订链证据。",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure compare", description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--output-root", type=Path, default=common.CASES_ROOT)
    args = parser.parse_args(argv)

    report = build_comparison(common.open_run(args.first), common.open_run(args.second))
    stem = f"route-comparison-{report['comparison_group']}"
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / f"{stem}.json"
    md_path = output_root / f"{stem}.md"
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    sys.stdout.write(f"A/B JSON: {json_path}\nA/B report: {md_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
