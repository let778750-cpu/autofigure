from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from tools.core import common
from tools.qa.compare import build_comparison, main as compare_main
from tools.core.contracts import read_json, write_json


def _case(tmp_path: Path, route: str, case: str) -> common.Run:
    reference = tmp_path / "reference.png"
    if not reference.is_file():
        Image.new("RGB", (80, 50), "white").save(reference)
    run = common.create_run(
        reference,
        case=case,
        cases_root=tmp_path / "examples",
        input_route=route,
    )
    provenance = read_json(run.provenance_path)
    provenance["comparison_group"] = "controlled-ab"
    candidate_sha = "a" * 64
    provenance["candidate_history"] = [
        {"role": "reconstruction-candidate", "sha256": candidate_sha},
        {"role": "reconstruction-candidate", "sha256": candidate_sha},
        {"role": "repair-candidate", "sha256": "b" * 64},
    ]
    provenance["events"] = [
        {"event": "candidate-ingested", "role": "repair-candidate"},
        {"event": "candidate-reused"},
    ]
    if route == "svg-seeded":
        run.external_seed_svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        seed_sha = common.sha256_file(run.external_seed_svg)
        provenance["external_svg_seed"] = {
            "role": "external-seed",
            "sha256": seed_sha,
            "availability": "available",
        }
        provenance["external_seed_gate"] = {
            "decision": "repair",
            "pass": False,
            "next_action": "repair-source-and-rerun-gate",
            "blockers": ["source-gate:semantic-metadata:missing-element-ids"],
            "candidate_sha256": seed_sha,
        }
        immutable_gate = {
            "schema_version": "4.0.0",
            "kind": "source_gate_report",
            "decision": "repair",
            "pass": False,
            "next_action": "repair-source-and-rerun-gate",
            "route_gate": {
                "input_route": "svg-seeded",
                "candidate_role": "external-seed",
                "seed_gate_status": "awaiting",
            },
            "candidate": {
                "sha256": seed_sha,
                "expected_sha256": seed_sha,
            },
            "reference": {
                "actual_sha256": run.load_meta()["source_sha256"],
            },
            "blockers": ["source-gate:semantic-metadata:missing-element-ids"],
        }
        write_json(run.external_seed_source_gate_report_path, immutable_gate)
        provenance["external_seed_gate"]["report_sha256"] = hashlib.sha256(
            json.dumps(
                immutable_gate,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    write_json(run.provenance_path, provenance)
    bindings = read_json(run.bindings_path)
    bindings.update(
        {
            "package_reopened": True,
            "saved_reopened": True,
            "bindings_complete": True,
        }
    )
    bindings["bindings"] = [
        {"object_kind": "text", "editable": True},
        {"object_kind": "native-math", "editable": True},
        {"object_kind": "connector", "editable": True},
        {"object_kind": "atomic-raster", "editable": False},
    ]
    bindings["scene_revision"] = {
        "revision_id": "scene-test",
        "scene_sha256": "c" * 64,
        "compiler_fingerprint": "d" * 64,
    }
    run.pptx_path.write_bytes(b"current-pptx")
    current_pptx_sha = common.sha256_file(run.pptx_path)
    write_json(run.bindings_path, bindings)
    write_json(
        run.qa_dir / "regions-report.json",
        {
            "strict_pass": False,
            "regions": [
                {
                    "id": "critical",
                    "critical": True,
                    "ssim": 0.7,
                    "edge_iou": 0.8,
                    "mean_abs_rgb_delta": 10,
                    "pass": False,
                }
            ],
        },
    )
    write_json(run.qa_dir / "arrows-audit.json", {"arrows": 1, "findings": []})
    write_json(
        run.qa_dir / "arrow-compile-report.json",
        {"arrow_count": 2, "records": [{}, {}], "blockers": [], "pass": True},
    )
    write_json(
        run.qa_dir / "powerpoint-arrow-readback.json",
        {"arrow_count": 2, "records": [{}, {}], "blockers": [], "pass": True},
    )
    write_json(
        run.qa_dir / "arrow-visual-report.json",
        {"contract_count": 2, "records": [{}, {}], "blockers": [], "pass": True},
    )
    write_json(run.layout_audit_path, {"pass": True, "findings": []})
    write_json(
        run.source_gate_report_path,
        {
            "decision": "accept",
            "pass": True,
            "next_action": "normalize-candidate-to-scene",
            "route_gate": {
                "input_route": route,
                "candidate_role": (
                    "external-seed" if route == "svg-seeded" else "reconstruction-candidate"
                ),
                "seed_gate_status": "accepted" if route == "svg-seeded" else "forbidden",
            },
            "candidate": {"sha256": candidate_sha},
            "blockers": [],
        },
    )
    write_json(
        run.blockers_path,
        {
            "kind": "strict_blocker_inventory",
            "revision_id": "scene-test",
            "blockers": ["layout:generic-object"],
        },
    )
    write_json(
        run.qa_dir / "visual-contracts-report.json",
        {"blockers": ["visual-contract:generic-object"]},
    )
    write_json(
        run.qa_dir / "live-save-reopen-summary.json",
        {
            "live_attempt_saved_reopened": True,
            # Deliberately stale/contradictory: case-root bindings must remain
            # authoritative in the A/B report.
            "saved_reopened": False,
            "bindings_complete": True,
            "live_candidate_sha256": current_pptx_sha,
            "reopened_artifact_sha256": current_pptx_sha,
            "published_to_case_root": True,
            "strict_live_blockers": [],
        },
    )
    write_json(
        run.qa_dir / "asset-spec-audit.json",
        {
            "asset_spec_count": 2,
            "logical_group_binding_count": 2,
            "member_binding_count": 6,
            "pptx_readback_count": 6,
            "opportunity_count": 2,
            "asset_contract_sha256": "1" * 64,
            "inventory_sha256": "2" * 64,
            "reference_sha256": run.load_meta()["source_sha256"],
            "revision_id": "scene-test",
            "scene_sha256": "c" * 64,
            "compiler_fingerprint": "d" * 64,
            "pptx_sha256": current_pptx_sha,
            "blockers": [],
            "pass": True,
        },
    )
    write_json(
        run.qa_dir / "asset-contract-receipt.json",
        {
            "status": "PASS",
            "asset_contract_sha256": "1" * 64,
            "inventory_sha256": "2" * 64,
            "reference_sha256": run.load_meta()["source_sha256"],
        },
    )
    revision = {
        "revision_id": "scene-test",
        "scene_sha256": "c" * 64,
        "compiler_fingerprint": "d" * 64,
    }
    meta = run.load_meta()
    meta["active_revision"] = revision
    write_json(run.meta_path, meta)
    write_json(run.revision_receipt_path, revision)
    return run


def test_comparison_is_hash_and_group_bound(tmp_path: Path, monkeypatch) -> None:
    seeded = _case(tmp_path, "svg-seeded", "seeded")
    direct = _case(tmp_path, "reference-only", "direct")
    monkeypatch.setattr(common, "CASES_ROOT", tmp_path / "examples")
    report = build_comparison(seeded, direct)
    assert report["comparison_group"] == "controlled-ab"
    assert report["conclusion"]["reference_only_pipeline_completed"] is True
    assert report["conclusion"]["reference_only_capability_mature"] is False
    seeded_summary = report["cases"]["svg-seeded"]
    direct_summary = report["cases"]["reference-only"]
    assert report["schema_version"] == "2.1.0"
    assert seeded_summary["source_gate"]["decision"] == "accept"
    assert seeded_summary["seed"]["availability"] == "available"
    assert seeded_summary["seed"]["hash_matches"] is True
    assert seeded_summary["seed"]["admission_decision"] == "repair"
    assert seeded_summary["seed"]["admission_evidence_verified"] is True
    assert seeded_summary["seed"]["admission_blocker_count"] == 1
    assert direct_summary["seed"]["availability"] == "not_applicable"
    assert direct_summary["processing_mode"] == "png_reconstruct"
    assert direct_summary["candidate_activity"]["reuse_event_count"] == 1
    assert direct_summary["candidate_activity"]["reused_candidate_ingestions"] == 1
    assert direct_summary["candidate_activity"]["repair_event_count"] == 1
    assert direct_summary["bindings"]["native_object_coverage"]["coverage_pct"] == 75.0
    assert direct_summary["arrows"]["logical_arrow_count"] == 2
    assert direct_summary["arrows"]["legacy_svg_marker_arrow_count"] == 1
    assert direct_summary["arrows"]["compile_readback_count_match"] is True
    assert direct_summary["arrows"]["compile_pass"] is True
    assert direct_summary["arrows"]["readback_pass"] is True
    assert direct_summary["regions"]["critical_metrics"]["min_ssim"] == 0.7
    assert direct_summary["blockers"]["items"] == ["layout:generic-object"]
    assert direct_summary["blockers"]["all_evidence_items"] == [
        "layout:generic-object",
        "visual-contract:generic-object",
    ]
    assert direct_summary["blockers"]["sources"]["visual-contracts-report.json"] == [
        "visual-contract:generic-object"
    ]
    assert direct_summary["powerpoint_live"]["candidate_reopened_hashes_match"] is True
    assert direct_summary["powerpoint_live"]["saved_reopened"] is True
    assert direct_summary["reference_inventory"] == {
        "inventory_sha256": None,
        "oracle_sha256": None,
    }
    assert direct_summary["powerpoint_live"]["live_summary_saved_reopened"] is False
    assert direct_summary["powerpoint_live"]["live_summary_agrees_with_bindings"] is False
    assert direct_summary["assets"]["asset_spec_count"] == 2
    assert direct_summary["assets"]["pptx_readback_count"] == 6
    assert direct_summary["assets"]["audit_receipt_hashes_match"] is True
    assert direct_summary["assets"]["lineage_closed"] is True
    assert direct_summary["revision_lineage"]["identities_match"] is True


def test_seed_admission_becomes_unverified_when_immutable_report_drifts(tmp_path: Path, monkeypatch) -> None:
    seeded = _case(tmp_path, "svg-seeded", "seeded")
    direct = _case(tmp_path, "reference-only", "direct")
    monkeypatch.setattr(common, "CASES_ROOT", tmp_path / "examples")
    payload = read_json(seeded.external_seed_source_gate_report_path)
    payload["decision"] = "accept"
    write_json(seeded.external_seed_source_gate_report_path, payload)

    report = build_comparison(seeded, direct)
    seed = report["cases"]["svg-seeded"]["seed"]
    assert seed["admission_reported_decision"] == "accept"
    assert seed["admission_decision"] == "unverified"
    assert seed["admission_evidence_verified"] is False


def test_compare_cli_writes_portable_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    seeded = _case(tmp_path, "svg-seeded", "seeded")
    direct = _case(tmp_path, "reference-only", "direct")
    monkeypatch.setattr(common, "CASES_ROOT", tmp_path / "examples")
    output = tmp_path / "reports"
    assert compare_main([str(seeded.root), str(direct.root), "--output-root", str(output)]) == 0
    payload = read_json(output / "route-comparison-controlled-ab.json")
    assert payload["cases"]["reference-only"]["path"] == "reference-only/direct"
    markdown = (output / "route-comparison-controlled-ab.md").read_text(encoding="utf-8")
    assert "source gate" in markdown
    assert "原生对象覆盖率" in markdown
    assert "原始 seed 准入" in markdown
    assert "AssetSpec" in markdown
    assert "observation/globe" not in markdown
