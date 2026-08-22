"""Safe provider protocol for native PowerPoint, live MCP, and optional add-ins."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from tools.contracts import read_json, write_json


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    role: str
    selected: bool
    executable: bool
    status: str
    capabilities: tuple[str, ...]
    reason: str


class ProviderAdapter(Protocol):
    provider_id: str

    def discover(self) -> ProviderStatus: ...
    def health(self) -> dict[str, Any]: ...
    def capabilities(self) -> list[str]: ...
    def execute(self, operation: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...
    def inspect(self, transaction_id: str) -> dict[str, Any]: ...
    def undo(self, transaction_id: str) -> dict[str, Any]: ...


_CATALOG = (
    ProviderStatus("powerpoint-native", "core", True, True, "selected", ("shape", "connector", "freeform", "group", "align", "readback", "render"), "Primary provider; add-ins are not required."),
    ProviderStatus("powerpoint-live", "hybrid-repair", True, False, "selected-health-required", ("managed-session", "visible-canvas", "inspect", "audit", "save-reopen", "bindings"), "Invoked by an MCP-capable agent from a hash-bound repair request."),
    ProviderStatus("onekeytools10", "isolated-pilot", False, False, "candidate-not-installed", ("vertex", "shape-split", "radial-lines", "gradient", "emf", "alignment"), "Only pilot candidate; installer/API/licensing remain unverified."),
    ProviderStatus("islide", "manual-assets", False, False, "optional-manual-only", ("components", "icons", "diagrams"), "Cloud/licensing dependency; not a button-level automation API."),
    ProviderStatus("threed-tools", "optional-3d", False, False, "on-demand-unverified", ("sphere", "cube", "3d-helper"), "Out of scope until a concrete 3D case exists."),
    ProviderStatus("okplus", "excluded", False, False, "excluded-overlap", (), "Overlaps native MCP and OneKey."),
    ProviderStatus("lvyhtools", "excluded", False, False, "excluded-no-api", (), "No verified automation API."),
    ProviderStatus("officeplus", "excluded", False, False, "excluded-style-drift", (), "Beautification can alter reference style."),
    ProviderStatus("animation-master", "excluded", False, False, "excluded-static-scope", (), "Animation is outside static-figure scope."),
    ProviderStatus("pocket-animation", "excluded", False, False, "excluded-static-scope", (), "Animation is outside static-figure scope."),
)


def _discover_powerpoint_live_server() -> Path | None:
    explicit = os.environ.get("AUTOFIGURE_POWERPOINT_SERVER")
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() else None
    cache = (
        Path.home()
        / ".codex"
        / "plugins"
        / "cache"
        / "ai-scientific-illustration-tools"
        / "drawio-scientific-illustrator"
    )
    if not cache.is_dir():
        return None
    candidates = [
        path / "scripts" / "powerpoint-server.mjs"
        for path in cache.iterdir()
        if path.is_dir() and (path / "scripts" / "powerpoint-server.mjs").is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def provider_catalog(host: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    host = host or host_health()
    result = [asdict(item) for item in _CATALOG]
    for item in result:
        if item["provider_id"] == "powerpoint-native":
            item["executable"] = bool((host.get("powerpoint") or {}).get("exists"))
            item["status"] = "selected" if item["executable"] else "selected-unavailable"
        elif item["provider_id"] == "powerpoint-live":
            item["executable"] = bool(host.get("node") and host.get("powerpoint_live_server"))
            item["status"] = (
                "selected-external-mcp" if item["executable"] else "selected-unavailable"
            )
    return result


def host_health() -> dict[str, Any]:
    server = _discover_powerpoint_live_server()
    result: dict[str, Any] = {
        "platform": platform.system(),
        "node": shutil.which("node"),
        "powerpoint": None,
        "office": None,
        "powerpoint_live_server": str(server) if server else None,
    }
    if os.name != "nt":
        return result
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration") as key:
            result["office"] = {
                "platform": winreg.QueryValueEx(key, "Platform")[0],
                "version": winreg.QueryValueEx(key, "VersionToReport")[0],
                "products": winreg.QueryValueEx(key, "ProductReleaseIds")[0],
            }
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\POWERPNT.EXE") as key:
            candidate = Path(winreg.QueryValueEx(key, "")[0])
            result["powerpoint"] = {"path": str(candidate), "exists": candidate.is_file()}
    except OSError as exc:
        result["discovery_error"] = str(exc)
    return result


class JournaledMockProvider:
    """Reference adapter proving execute/inspect/undo and idempotency semantics."""

    provider_id = "mock"

    def __init__(self, journal_path: Path):
        self.journal_path = journal_path
        if not journal_path.is_file():
            write_json(journal_path, {"transactions": []})

    def discover(self) -> ProviderStatus:
        return ProviderStatus("mock", "test", True, True, "healthy", ("echo",), "test adapter")

    def health(self) -> dict[str, Any]:
        return {"healthy": True}

    def capabilities(self) -> list[str]:
        return ["echo"]

    def execute(self, operation: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        journal = read_json(self.journal_path)
        for transaction in journal["transactions"]:
            if transaction["idempotency_key"] == idempotency_key:
                return transaction
        transaction = {"transaction_id": f"tx-{len(journal['transactions']) + 1}", "idempotency_key": idempotency_key, "operation": operation, "status": "applied"}
        journal["transactions"].append(transaction)
        write_json(self.journal_path, journal)
        return transaction

    def inspect(self, transaction_id: str) -> dict[str, Any]:
        for transaction in read_json(self.journal_path)["transactions"]:
            if transaction["transaction_id"] == transaction_id:
                return transaction
        raise KeyError(transaction_id)

    def undo(self, transaction_id: str) -> dict[str, Any]:
        journal = read_json(self.journal_path)
        for transaction in journal["transactions"]:
            if transaction["transaction_id"] == transaction_id:
                transaction["status"] = "undone"
                write_json(self.journal_path, journal)
                return transaction
        raise KeyError(transaction_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure providers", description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    host = host_health()
    payload = {
        "schema_version": "1.0.0",
        "host": host,
        "providers": provider_catalog(host),
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        for item in payload["providers"]:
            selected = "selected" if item["selected"] else "not selected"
            sys.stdout.write(f"{item['provider_id']}: {item['status']} ({selected})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
