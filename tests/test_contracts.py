from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import (
    ContractError,
    initialize_contracts,
    migrate_legacy_run,
    read_json,
    record_validation,
    set_processing_mode,
    transition,
)


def _run(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-source.png"
    Image.new("RGB", (40, 30), "white").save(reference)
    return common.create_run(
        reference,
        case="case",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )


def test_create_run_initializes_hash_bound_v3_contracts(tmp_path: Path):
    run = _run(tmp_path)
    meta = run.load_meta()
    assert meta["schema_version"] == "3.1.0"
    assert meta["input_route"] == "svg-seeded"
    assert meta["processing_mode"] == "svg_import"
    assert meta["validation"]["status"] == "not_run"
    assert meta["workflow"]["state"] == "prepared"
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
