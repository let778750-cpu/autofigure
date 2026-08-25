"""Explicitly migrate a legacy case into schema 4 evidence without approving it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools import common
from tools.contracts import (
    read_json,
    record_seed_unavailable,
    transition,
    utc_now,
    write_json,
)
from tools.revisions import (
    bind_canonical_svg,
    materialize_svg,
    read_svg_text_exact,
    stamp_active_revision,
)


def _write_unverified_gate(run: common.Run, blocker: str) -> dict:
    meta = run.load_meta()
    scene = read_json(run.scene_path)
    carrier = scene.get("canonical_svg", {})
    candidate_sha = carrier.get("sha256")
    receipt_path = run.qa_dir / "reference-inventory-receipt.json"
    inventory_sha = (
        read_json(receipt_path).get("inventory_sha256")
        if receipt_path.is_file()
        else None
    )
    decision = "reject" if blocker.startswith("source-gate:seed:") else "repair"
    report = {
        "schema_version": "4.0.0",
        "kind": "source_gate_report",
        "created_at": utc_now(),
        "case": meta["case"],
        "decision": decision,
        "pass": False,
        "next_action": (
            "reconstruct-from-reference"
            if decision == "reject"
            else "establish-verifiable-read-manifest-and-rerun-gate"
        ),
        "route_gate": {
            "input_route": meta["input_route"],
            "candidate_role": "legacy-unverified-candidate",
            "seed_gate_status": (
                "rejected" if meta["input_route"] == "svg-seeded" else "forbidden"
            ),
        },
        "candidate": {
            "path_base": "case-root",
            "source_name": "scene.json:canonical_svg",
            "kind": "svg",
            "sha256": candidate_sha,
        },
        "reference": {
            "path_base": "case-root",
            "path": "reference.png",
            "expected_sha256": meta["source_sha256"],
            "actual_sha256": common.sha256_file(run.source_png),
        },
        "reference_inventory_sha256": inventory_sha,
        "checks": [],
        "findings": [
            {
                "category": "route",
                "decision": decision,
                "code": blocker,
                "message": "legacy construction evidence is insufficient for schema 4 admission",
            }
        ],
        "blockers": [blocker],
        "reject_reasons": [blocker] if decision == "reject" else [],
        "repair_reasons": [blocker] if decision == "repair" else [],
        "repair_actions": [],
    }
    write_json(run.source_gate_report_path, report)
    return report


def migrate_case(
    run: common.Run,
    *,
    seed_unavailable: bool = False,
    expected_seed_sha256: str | None = None,
) -> dict:
    meta = run.load_meta()
    scene = read_json(run.scene_path)
    imported_legacy_carrier = False
    if not isinstance(scene.get("canonical_svg"), dict) and run.redraw_svg.is_file():
        bind_canonical_svg(
            scene,
            read_svg_text_exact(run.redraw_svg),
            source_role="legacy-unverified-candidate",
            source_sha256=common.sha256_file(run.redraw_svg),
        )
        write_json(run.scene_path, scene)
        materialize_svg(run, scene)
        imported_legacy_carrier = True
    elif isinstance(scene.get("canonical_svg"), dict):
        materialize_svg(run, scene)
    if seed_unavailable:
        record_seed_unavailable(
            run,
            reason="legacy provenance does not retain recoverable exact seed bytes",
            expected_sha256=expected_seed_sha256,
        )
        gate = _write_unverified_gate(run, "source-gate:seed:unavailable")
    elif run.source_gate_report_path.is_file():
        gate = read_json(run.source_gate_report_path)
    else:
        gate = _write_unverified_gate(
            run, "source-gate:isolation:read-manifest-unverified"
        )
    revision = stamp_active_revision(run)
    state = run.load_meta()["workflow"]["state"]
    if state != "qa_failed":
        transition(run, "qa_failed", "schema-v4-migration-unverified")
    else:
        transition(run, "qa_failed", "schema-v4-migration-evidence-refreshed")
    report = {
        "schema_version": "4.0.0",
        "kind": "schema_v4_migration_report",
        "case": meta["case"],
        "input_route": meta["input_route"],
        "processing_mode": run.load_meta()["processing_mode"],
        "imported_legacy_carrier": imported_legacy_carrier,
        "source_gate_decision": gate.get("decision"),
        "source_gate_blockers": gate.get("blockers", []),
        "revision_id": revision["revision_id"],
        "scene_sha256": revision["scene_sha256"],
        "status": "implemented_unverified",
        "approved": False,
    }
    write_json(run.qa_dir / "schema-v4-migration-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure migrate-v4", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--seed-unavailable", action="store_true")
    parser.add_argument("--expected-seed-sha256")
    args = parser.parse_args(argv)
    run = common.open_run(args.run_dir)
    report = migrate_case(
        run,
        seed_unavailable=args.seed_unavailable,
        expected_seed_sha256=args.expected_seed_sha256,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
