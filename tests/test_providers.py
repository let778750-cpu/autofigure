from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path

import pytest
from PIL import Image

from tools.core.contracts import read_json
from tools.providers.providers import (
    JournaledMockProvider,
    VtracerAdapter,
    powerpoint_live_arrow_capabilities,
    provider_catalog,
)


def test_catalog_keeps_addins_out_of_core_selection():
    host = {
        "node": "node",
        "powerpoint": {"exists": True},
        "powerpoint_live_server": "server.mjs",
    }
    catalog = {item["provider_id"]: item for item in provider_catalog(host)}
    assert catalog["powerpoint-native"]["selected"] is True
    assert catalog["powerpoint-live"]["executable"] is True
    assert catalog["onekeytools10"]["selected"] is False
    assert catalog["islide"]["status"] == "optional-manual-only"
    assert catalog["animation-master"]["role"] == "excluded"


def test_provider_execute_is_idempotent_inspectable_and_undoable(tmp_path: Path):
    provider = JournaledMockProvider(tmp_path / "journal.json")
    first = provider.execute({"op": "echo", "value": 1}, "same-key")
    second = provider.execute({"op": "echo", "value": 2}, "same-key")
    assert second == first
    assert provider.inspect(first["transaction_id"])["status"] == "applied"
    assert provider.undo(first["transaction_id"])["status"] == "undone"


def test_live_arrow_authoring_is_fail_closed_without_hash_bound_matrix_probe(monkeypatch):
    monkeypatch.delenv("AUTOFIGURE_POWERPOINT_ARROW_PROBE", raising=False)
    capabilities = powerpoint_live_arrow_capabilities()
    assert capabilities["arrow_authoring_allowed"] is False
    assert capabilities["allowed_arrow_operations"] == ["inspect", "audit", "save-reopen"]
    assert capabilities["live_matrix_probe_verified"] is False
    assert "live_matrix_probe_verified" in capabilities["deny_reasons"]


def _write_flat_png(path: Path, size: int = 16) -> Path:
    Image.new("RGB", (size, size), (24, 90, 200)).save(path)
    return path


def _trace_operation(tmp_path: Path, name: str = "flat") -> dict:
    input_png = _write_flat_png(tmp_path / f"{name}.png")
    return {
        "op": "trace",
        "input_png": str(input_png),
        "output_svg": str(tmp_path / f"{name}.svg"),
    }


def test_catalog_registers_vtracer_as_unselected_source_authoring_pilot():
    host = {"node": None, "powerpoint": None, "powerpoint_live_server": None}
    catalog = {item["provider_id"]: item for item in provider_catalog(host)}
    entry = catalog["vtracer"]
    assert entry["role"] == "source-authoring"
    assert entry["selected"] is False
    assert entry["status"] == "candidate-pilot"
    assert entry["executable"] is True
    assert list(entry["capabilities"]) == [
        "trace",
        "svg-fragment-export",
        "path-normalize",
        "svg-contract-check",
    ]


def test_vtracer_health_reports_installed_engine_version(tmp_path: Path):
    provider = VtracerAdapter(tmp_path / "journal.json")
    health = provider.health()
    assert health["healthy"] is True
    assert health["engine"] == "vtracer"
    assert health["engine_version"] == importlib.metadata.version("vtracer")
    assert health["foreground_switch"] is False
    assert health["reason"] is None
    status = provider.discover()
    assert status.provider_id == "vtracer"
    assert status.role == "source-authoring"
    assert status.selected is False
    assert status.executable is True
    assert status.status == "candidate-pilot"
    assert provider.capabilities() == [
        "trace",
        "svg-fragment-export",
        "path-normalize",
        "svg-contract-check",
    ]


def test_vtracer_health_reports_unavailable_when_import_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tools.providers._vtracer_engine_version", lambda: None)
    provider = VtracerAdapter(tmp_path / "journal.json")
    health = provider.health()
    assert health["healthy"] is False
    assert health["engine_version"] is None
    assert health["reason"] == "vtracer-import-failed"
    status = provider.discover()
    assert status.executable is False
    assert status.status == "candidate-pilot-unavailable"
    with pytest.raises(RuntimeError, match="not importable"):
        provider.execute(_trace_operation(tmp_path), "no-engine")


def test_vtracer_execute_trace_records_hashes_params_and_engine(tmp_path: Path):
    provider = VtracerAdapter(tmp_path / "journal.json")
    operation = _trace_operation(tmp_path)
    transaction = provider.execute(operation, "trace-key")
    assert transaction["transaction_id"] == "tx-1"
    assert transaction["idempotency_key"] == "trace-key"
    assert transaction["operation"] == operation
    assert transaction["status"] == "applied"
    result = transaction["result"]
    output_svg = Path(result["output_svg"])
    assert output_svg.is_file()
    assert result["input_sha256"] == hashlib.sha256(
        Path(operation["input_png"]).read_bytes()
    ).hexdigest()
    assert result["output_sha256"] == hashlib.sha256(output_svg.read_bytes()).hexdigest()
    assert result["engine"] == "vtracer"
    assert result["engine_version"] == importlib.metadata.version("vtracer")
    assert result["params"] == {
        "colormode": "color",
        "hierarchical": "stacked",
        "mode": "spline",
        "color_precision": 6,
        "path_precision": 3,
    }
    assert provider.inspect("tx-1") == transaction


def test_vtracer_execute_replay_returns_existing_transaction_without_rerun(tmp_path: Path):
    provider = VtracerAdapter(tmp_path / "journal.json")
    first = provider.execute(_trace_operation(tmp_path), "same-key")
    output_svg = Path(first["result"]["output_svg"])
    output_svg.unlink()
    second = provider.execute(
        {**_trace_operation(tmp_path), "params": {"mode": "polygon"}}, "same-key"
    )
    assert second == first
    assert not output_svg.is_file()
    assert len(read_json(provider.journal_path)["transactions"]) == 1


def test_vtracer_trace_output_is_byte_deterministic(tmp_path: Path):
    provider = VtracerAdapter(tmp_path / "journal.json")
    input_png = _write_flat_png(tmp_path / "flat.png")
    first = provider.execute(
        {"op": "trace", "input_png": str(input_png), "output_svg": str(tmp_path / "a.svg")},
        "key-a",
    )
    second = provider.execute(
        {"op": "trace", "input_png": str(input_png), "output_svg": str(tmp_path / "b.svg")},
        "key-b",
    )
    assert first["transaction_id"] != second["transaction_id"]
    assert first["result"]["output_sha256"] == second["result"]["output_sha256"]
    assert (tmp_path / "a.svg").read_bytes() == (tmp_path / "b.svg").read_bytes()


def test_vtracer_undo_is_trivial_and_idempotent(tmp_path: Path):
    provider = VtracerAdapter(tmp_path / "journal.json")
    transaction = provider.execute(_trace_operation(tmp_path), "undo-key")
    assert provider.inspect(transaction["transaction_id"])["status"] == "applied"
    undone = provider.undo(transaction["transaction_id"])
    assert undone["status"] == "undone"
    assert provider.undo(transaction["transaction_id"]) == undone
    assert len(read_json(provider.journal_path)["transactions"]) == 1
    with pytest.raises(KeyError):
        provider.inspect("missing-tx")
    with pytest.raises(KeyError):
        provider.undo("missing-tx")


def test_vtracer_execute_rejects_unknown_operation_and_params(tmp_path: Path):
    provider = VtracerAdapter(tmp_path / "journal.json")
    input_png = _write_flat_png(tmp_path / "flat.png")
    with pytest.raises(ValueError, match="unsupported vtracer operation"):
        provider.execute(
            {
                "op": "resize",
                "input_png": str(input_png),
                "output_svg": str(tmp_path / "x.svg"),
            },
            "bad-op",
        )
    with pytest.raises(ValueError, match="unsupported vtracer parameters"):
        provider.execute(
            {
                "op": "trace",
                "input_png": str(input_png),
                "output_svg": str(tmp_path / "y.svg"),
                "params": {"bogus": 1},
            },
            "bad-params",
        )
    assert read_json(provider.journal_path)["transactions"] == []
