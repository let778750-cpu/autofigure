from __future__ import annotations

from pathlib import Path

from tools.providers import (
    JournaledMockProvider,
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
