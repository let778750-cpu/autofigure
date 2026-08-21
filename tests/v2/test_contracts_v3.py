from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tools.v2 import common
from tools.v2.contracts import ContractError, read_json, set_modes, transition


def _run(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-source.png"
    Image.new("RGB", (40, 30), "white").save(reference)
    return common.create_run(reference, case="case", cases_root=tmp_path / "examples")


def test_create_run_initializes_hash_bound_v3_contracts(tmp_path: Path):
    run = _run(tmp_path)
    meta = run.load_meta()
    assert meta["schema_version"] == "3.0.0"
    assert meta["workflow"]["state"] == "prepared"
    for path, kind in (
        (run.scene_path, "scene"),
        (run.assets_path, "assets"),
        (run.regions_path, "regions"),
        (run.bindings_path, "bindings"),
    ):
        payload = read_json(path)
        assert payload["kind"] == kind
        assert payload["reference_sha256"] == meta["source_sha256"]


def test_state_machine_and_mode_switch_are_explicit(tmp_path: Path):
    run = _run(tmp_path)
    set_modes(
        run,
        source_mode="png_reconstruct",
        fidelity_profile="hybrid_fidelity",
        backend_mode="hybrid",
    )
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
