"""Prepare or ingest a hash-bound PowerPoint-live regional repair handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools.v2 import common
from tools.v2.contracts import read_json, set_modes, transition, utc_now, write_json


REQUIRED_LIVE_CAPABILITIES = (
    "managed-session", "visible-canvas", "native-connector", "freeform",
    "inspect", "audit", "save-reopen", "object-bindings",
)


def build_live_request(run: common.Run) -> dict[str, Any]:
    from tools.v2.live_bridge import build_powerpoint_live_bridge

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
    request = {
        "schema_version": "1.0.0",
        "kind": "powerpoint_live_repair_request",
        "created_at": utc_now(),
        "case_root": bridge["case_root"],
        "autofigure_case_root": str(run.root),
        "project_id": bridge["project_id"],
        "task_mode": "RECONSTRUCT_1TO1",
        "target_id": bridge["target_id"],
        "reference_sha256": meta["source_sha256"],
        "candidate_pptx": bridge["template_path"],
        "template_path": bridge["template_path"],
        "visible": True,
        "failed_regions": failed_ids,
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
        "scene_element_ids": [item.get("id") for item in scene.get("elements", []) if item.get("id")],
        "bound_element_ids": [item.get("element_id") for item in bindings.get("bindings", []) if item.get("element_id")],
        "scene_compatibility": {
            "source_schema_version": bridge["source_scene_schema_version"],
            "adapter_schema_version": bridge["adapter_scene_schema_version"],
            "adapter_scene_sha256": bridge["adapter_scene_sha256"],
            "bridge_manifest": str(run.live_bridge_path),
            "direct_scene_v3_submission_forbidden": True,
        },
        "completion_contract": {"saved_reopened": True, "bindings_complete": True, "region_results_required": failed_ids, "automatic_release_authority": "NONE"},
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
    write_json(run.live_evidence_path, evidence)
    transition(run, "candidate", "powerpoint-live-evidence-ingested")
    return evidence


def live_evidence_passes(run: common.Run, required_regions: list[str]) -> tuple[bool, list[str]]:
    if not run.live_evidence_path.is_file():
        return False, ["live-evidence-missing"]
    evidence = read_json(run.live_evidence_path)
    blockers: list[str] = []
    if evidence.get("reference_sha256") != run.load_meta()["source_sha256"]:
        blockers.append("live-evidence-reference-mismatch")
    if evidence.get("saved_reopened") is not True:
        blockers.append("live-save-reopen-missing")
    if evidence.get("bindings_complete") is not True:
        blockers.append("live-bindings-incomplete")
    region_results = evidence.get("regions", {})
    blockers.extend(f"live-region:{region_id}" for region_id in required_regions if region_results.get(region_id) not in ("REGION_PASS", "pass"))
    return not blockers, blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure repair", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    run = common.open_run(args.run_dir)
    set_modes(run, backend_mode="hybrid")
    if args.evidence:
        ingest_live_evidence(run, args.evidence)
        sys.stdout.write(f"live evidence 已接收: {run.live_evidence_path}\n")
        return 0
    transition(run, "repairing", "powerpoint-live-repair-requested")
    request = build_live_request(run)
    sys.stdout.write(f"已生成 PowerPoint-live 修复请求，失败区域 {len(request['failed_regions'])} 个: {run.live_request_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
