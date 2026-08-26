from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import (
    ASSET_REPRESENTATIONS,
    CANDIDATE_ORIGINS,
    SCHEMA_VERSION,
    TRACE_ELIGIBILITY_VALUES,
    ContractError,
    initialize_contracts,
    migrate_legacy_run,
    read_json,
    record_candidate_provenance,
    record_source_gate_provenance,
    record_validation,
    set_processing_mode,
    transition,
)


def _run(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-source.png"
    Image.new("RGB", (40, 30), "white").save(reference)
    seed = tmp_path / "seed.svg"
    seed.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30" '
        'viewBox="0 0 40 30"><rect width="40" height="30"/></svg>',
        encoding="utf-8",
    )
    run = common.create_run(
        reference,
        case="case",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    shutil.copy2(seed, run.external_seed_svg)
    record_candidate_provenance(
        run,
        run.external_seed_svg,
        kind="svg",
        origin="test",
        role="external-seed",
        canonical_path="external-seed.svg",
    )
    return run


def test_create_run_initializes_hash_bound_v4_scene_contracts(tmp_path: Path):
    run = _run(tmp_path)
    meta = run.load_meta()
    assert SCHEMA_VERSION == "4.0.0"
    assert meta["schema_version"] == SCHEMA_VERSION
    assert meta["input_route"] == "svg-seeded"
    assert meta["processing_mode"] == "svg_import"
    assert meta["validation"]["status"] == "not_run"
    assert meta["workflow"]["state"] == "prepared"
    assert meta["active_revision"] is None
    for path, kind in (
        (run.scene_path, "scene"),
        (run.assets_path, "assets"),
        (run.regions_path, "regions"),
        (run.bindings_path, "bindings"),
        (run.provenance_path, "provenance"),
    ):
        payload = read_json(path)
        assert payload["kind"] == kind
        assert payload["reference_sha256"] == meta["source_sha256"]
        assert payload["schema_version"] == SCHEMA_VERSION
    assert read_json(run.scene_path)["canonical_source"] == "scene"
    provenance = read_json(run.provenance_path)
    assert provenance["external_svg_seed"]["sha256"] == common.sha256_file(
        run.external_seed_svg
    )


def test_external_seed_gate_is_immutable_and_survives_later_candidate_reports(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    seed_sha256 = common.sha256_file(run.external_seed_svg)
    report = {
        "schema_version": "4.0.0",
        "kind": "source_gate_report",
        "created_at": "20260824T000000Z",
        "decision": "repair",
        "pass": False,
        "next_action": "repair-source-and-rerun-gate",
        "route_gate": {
            "input_route": "svg-seeded",
            "candidate_role": "external-seed",
            "seed_gate_status": "awaiting",
        },
        "candidate": {"sha256": seed_sha256},
        "blockers": ["source-gate:semantic-metadata:missing-element-ids"],
    }

    first = record_source_gate_provenance(
        run,
        report,
        immutable_external_seed=True,
    )
    second = record_source_gate_provenance(
        run,
        report,
        immutable_external_seed=True,
    )

    assert second == first
    assert read_json(run.external_seed_source_gate_report_path) == report
    provenance = read_json(run.provenance_path)
    assert provenance["external_seed_gate"]["decision"] == "repair"
    assert provenance["external_svg_seed"]["source_gate"] == first
    assert len(provenance["source_gate_history"]) == 1
    assert sum(
        item.get("event") == "source-gate-evaluated"
        for item in provenance["events"]
    ) == 1

    changed = {**report, "decision": "accept", "pass": True, "blockers": []}
    with pytest.raises(ContractError, match="already differs"):
        record_source_gate_provenance(
            run,
            changed,
            immutable_external_seed=True,
        )
    assert len(provenance["candidate_history"]) == 1


def test_state_machine_and_mode_switch_are_explicit(tmp_path: Path):
    run = _run(tmp_path)
    set_processing_mode(
        run,
        processing_mode="png_reconstruct",
        fidelity_profile="hybrid_fidelity",
        backend_mode="hybrid",
    )
    assert run.load_meta()["input_route"] == "svg-seeded"
    transition(run, "repairing", "fallback")
    transition(run, "candidate", "candidate-built")
    transition(run, "approved", "strict-pass")
    assert run.load_meta()["workflow"]["state"] == "approved"
    with pytest.raises(ContractError, match="invalid workflow transition"):
        transition(run, "candidate", "illegal-rebuild")


def test_open_run_rejects_reference_hash_drift(tmp_path: Path):
    run = _run(tmp_path)
    Image.new("RGB", (40, 30), "black").save(run.source_png)
    with pytest.raises(SystemExit, match="reference hash mismatch"):
        common.open_run(run.root)
    assert json.loads(run.meta_path.read_text(encoding="utf-8"))["workflow"]["state"] == "prepared"


def test_input_route_is_immutable(tmp_path: Path):
    run = _run(tmp_path)
    with pytest.raises(ContractError, match="input_route is immutable"):
        initialize_contracts(run, input_route="reference-only")


def test_svg_seed_is_exactly_one_immutable_provenance_input(tmp_path: Path):
    run = _run(tmp_path)
    original = record_candidate_provenance(
        run,
        run.external_seed_svg,
        kind="svg",
        origin="test",
        role="external-seed",
        canonical_path="external-seed.svg",
    )
    provenance = read_json(run.provenance_path)
    assert original == provenance["external_svg_seed"]
    assert len(provenance["candidate_history"]) == 1

    replacement = tmp_path / "replacement.svg"
    replacement.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>',
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="exactly one immutable external seed"):
        record_candidate_provenance(
            run,
            replacement,
            kind="svg",
            origin="test",
            role="external-seed",
            canonical_path="external-seed.svg",
        )


def test_reference_only_mode_cannot_switch_to_seed_processing(tmp_path: Path):
    reference = tmp_path / "reference-only.png"
    Image.new("RGB", (40, 30), "white").save(reference)
    run = common.create_run(
        reference,
        case="reference-only-case",
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )
    assert run.load_meta()["processing_mode"] == "png_reconstruct"
    with pytest.raises(ContractError, match="must use png_reconstruct"):
        set_processing_mode(run, processing_mode="svg_import")


def test_legacy_route_requires_explicit_migration(tmp_path: Path):
    run = _run(tmp_path)
    meta = run.load_meta()
    meta.pop("input_route")
    meta["source_mode"] = meta.pop("processing_mode")
    run.meta_path.write_text(json.dumps(meta), encoding="utf-8")
    run.provenance_path.unlink()

    with pytest.raises(ContractError, match="explicit migration"):
        initialize_contracts(run)

    migrated = migrate_legacy_run(
        run,
        input_route="svg-seeded",
        processing_mode="svg_import",
        workflow_state="candidate",
    )
    assert migrated["input_route"] == "svg-seeded"
    assert migrated["processing_mode"] == "svg_import"
    assert "source_mode" not in migrated


def test_validation_summary_distinguishes_diagnostic_and_strict(tmp_path: Path):
    run = _run(tmp_path)
    standard = record_validation(run, "standard", ["region:demo"])
    assert standard["status"] == "diagnostic"
    strict = record_validation(run, "strict", ["region:demo"])
    assert strict["status"] == "failed"
    passed = record_validation(run, "strict", [])
    assert passed["status"] == "passed"


def test_atomic_vector_contract_vocabulary():
    assert ASSET_REPRESENTATIONS == ("atomic-raster", "atomic-vector")
    assert TRACE_ELIGIBILITY_VALUES == ("photographic", "flat-illustration", "ambiguous")
    assert CANDIDATE_ORIGINS == (
        "web-vlm",
        "local-vlm",
        "codex",
        "human",
        "vtracer-provider",
        "unknown",
    )
