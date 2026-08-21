from __future__ import annotations

from pathlib import Path

from tools.v2.providers import JournaledMockProvider, provider_catalog


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
