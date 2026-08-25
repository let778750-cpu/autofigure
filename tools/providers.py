"""Safe provider protocol for native PowerPoint, live MCP, and optional add-ins."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from tools.contracts import read_json, utc_now, write_json


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


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _external_arrow_probe(
    server_sha256: str | None,
    bridge_sha256: str | None,
) -> dict[str, Any]:
    """Validate hash-bound evidence emitted by an MCP-driven matrix probe."""

    explicit = os.environ.get("AUTOFIGURE_POWERPOINT_ARROW_PROBE")
    if not explicit:
        return {"verified": False, "path": None, "reason": "probe-evidence-missing"}
    path = Path(explicit).expanduser().resolve()
    if not path.is_file():
        return {"verified": False, "path": str(path), "reason": "probe-evidence-not-found"}
    try:
        report = read_json(path)
    except (OSError, ValueError) as exc:
        return {"verified": False, "path": str(path), "reason": f"probe-evidence-invalid:{exc}"}
    endpoint_rows = report.get("endpoint_matrix", [])
    paths = report.get("paths", {})
    dashes = report.get("dashes", {})
    required_paths = {"straight", "elbow", "curve", "mixed_line_cubic_freeform"}
    required_dashes = {
        "solid",
        "square_dot",
        "round_dot",
        "dash",
        "dash_dot",
        "dash_dot_dot",
        "long_dash",
        "long_dash_dot",
        "long_dash_dot_dot",
        "sys_dash",
        "sys_dot",
        "sys_dash_dot",
    }
    endpoint_pass = (
        isinstance(endpoint_rows, list)
        and len(endpoint_rows) >= 90
        and all(isinstance(row, dict) and row.get("status") == "PASS" for row in endpoint_rows)
    )
    verified = all(
        (
            report.get("schema_version") == "1.0.0",
            report.get("status") == "PASS",
            report.get("server_sha256") == server_sha256,
            report.get("bridge_sha256") == bridge_sha256,
            report.get("open_triangle_enum_self_test") == "PASS",
            endpoint_pass,
            required_paths.issubset({key for key, value in paths.items() if value == "PASS"}),
            required_dashes.issubset({key for key, value in dashes.items() if value == "PASS"}),
            report.get("autoshape_subtype_readback") == "PASS",
            report.get("autoshape_adjustments_roundtrip") == "PASS",
            report.get("save_reopen_roundtrip") == "PASS",
        )
    )
    return {
        "verified": verified,
        "path": str(path),
        "sha256": _file_sha256(path),
        "reason": None if verified else "probe-matrix-incomplete-or-hash-mismatched",
    }


def powerpoint_live_arrow_capabilities() -> dict[str, Any]:
    """Fail-closed local capability probe; version alone never enables writes."""

    server = _discover_powerpoint_live_server()
    bridge = None if server is None else server.with_name("powerpoint-bridge.ps1")
    server_text = "" if server is None else server.read_text(encoding="utf-8", errors="replace")
    bridge_text = "" if bridge is None or not bridge.is_file() else bridge.read_text(
        encoding="utf-8", errors="replace"
    )
    version_match = re.search(r'const\s+SERVER_VERSION\s*=\s*["\']([^"\']+)', server_text)
    version = version_match.group(1) if version_match else None

    # Verify the exact Office enum contract instead of trusting friendly names.
    open_correct = bool(re.search(r'["\']?open["\']?\s*\{\s*return\s+3\b', bridge_text, re.IGNORECASE))
    triangle_correct = bool(
        re.search(r'["\']?triangle["\']?\s*\{\s*return\s+2\b', bridge_text, re.IGNORECASE)
    )
    enum_mapping_verified = open_correct and triangle_correct
    head_size_write_verified = all(
        token in bridge_text
        for token in (
            "BeginArrowheadWidth",
            "BeginArrowheadLength",
            "EndArrowheadWidth",
            "EndArrowheadLength",
        )
    ) and all(
        token in server_text
        for token in (
            "begin_arrow_width",
            "begin_arrow_length",
            "end_arrow_width",
            "end_arrow_length",
        )
    )
    # A writer is not verified unless the same fields are present in inventory/readback code.
    readback_markers = [bridge_text.count(token) for token in (
        "BeginArrowheadWidth",
        "BeginArrowheadLength",
        "EndArrowheadWidth",
        "EndArrowheadLength",
    )]
    head_size_readback_verified = head_size_write_verified and all(count >= 2 for count in readback_markers)
    connector_paths_verified = all(token in server_text for token in ('"straight"', '"elbow"', '"curve"'))
    dash_tokens = (
        "solid",
        "square_dot",
        "round_dot",
        "dash",
        "dash_dot",
        "dash_dot_dot",
        "long_dash",
        "long_dash_dot",
        "long_dash_dot_dot",
        "sys_dash",
        "sys_dot",
        "sys_dash_dot",
    )
    dash_matrix_verified = all(token in server_text and token in bridge_text for token in dash_tokens)
    adjustment_occurrences = bridge_text.count(".Adjustments")
    autoshape_adjustments_write_verified = adjustment_occurrences >= 1 and "adjustments" in server_text
    autoshape_adjustments_readback_verified = adjustment_occurrences >= 2
    autoshape_subtype_readback_verified = bridge_text.count("AutoShapeType") >= 2
    connector_true_path_readback_verified = (
        "ConnectorFormat.Type" in bridge_text
        and "Get-LineEndpointPath" in bridge_text
        and "non straight" not in bridge_text.lower()
    )
    mixed_freeform_path_readback_verified = (
        "Nodes" in bridge_text
        and "cubic" in bridge_text.lower()
        and "line" in bridge_text.lower()
        and "normalized" in bridge_text.lower()
    )
    server_sha256 = _file_sha256(server)
    bridge_sha256 = _file_sha256(bridge)
    external_probe = _external_arrow_probe(server_sha256, bridge_sha256)
    live_matrix_probe_verified = external_probe["verified"] is True
    known_bad = version in {"2.1.1"}
    checks = {
        "known_bad_version": known_bad,
        "enum_mapping_verified": enum_mapping_verified,
        "head_size_write_verified": head_size_write_verified,
        "head_size_readback_verified": head_size_readback_verified,
        "required_path_kinds_verified": connector_paths_verified,
        "dash_matrix_verified": dash_matrix_verified,
        "autoshape_adjustments_write_verified": autoshape_adjustments_write_verified,
        "autoshape_adjustments_readback_verified": autoshape_adjustments_readback_verified,
        "autoshape_subtype_readback_verified": autoshape_subtype_readback_verified,
        "connector_true_path_readback_verified": connector_true_path_readback_verified,
        "mixed_freeform_path_readback_verified": mixed_freeform_path_readback_verified,
        "live_matrix_probe_verified": live_matrix_probe_verified,
    }
    allowed = (
        not known_bad
        and enum_mapping_verified
        and head_size_write_verified
        and head_size_readback_verified
        and connector_paths_verified
        and dash_matrix_verified
        and autoshape_adjustments_write_verified
        and autoshape_adjustments_readback_verified
        and autoshape_subtype_readback_verified
        and connector_true_path_readback_verified
        and mixed_freeform_path_readback_verified
        and live_matrix_probe_verified
    )
    deny_reasons = [
        key
        for key, value in checks.items()
        if (key == "known_bad_version" and value)
        or (key != "known_bad_version" and not value)
    ]
    fingerprint_payload = {
        "version": version,
        "server_sha256": server_sha256,
        "bridge_sha256": bridge_sha256,
        "checks": checks,
        "external_probe_sha256": external_probe.get("sha256"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "provider_id": "powerpoint-live",
        "server_version": version,
        "server_sha256": fingerprint_payload["server_sha256"],
        "bridge_sha256": fingerprint_payload["bridge_sha256"],
        "external_probe": external_probe,
        "capability_fingerprint_sha256": fingerprint,
        **checks,
        "required_endpoint_styles": ["none", "open", "triangle", "stealth", "diamond", "oval"],
        "required_head_widths": ["sm", "med", "lg"],
        "required_head_lengths": ["sm", "med", "lg"],
        "required_dash_styles": list(dash_tokens),
        "required_connector_paths": ["straight", "elbow", "curve"],
        "arrow_authoring_allowed": allowed,
        "allowed_arrow_operations": ["inspect", "audit", "save-reopen"] if not allowed else [
            "inspect",
            "audit",
            "save-reopen",
            "create",
            "replace",
        ],
        "deny_reasons": deny_reasons,
    }


def write_case_capabilities(run) -> dict[str, Any]:
    capabilities = powerpoint_live_arrow_capabilities()
    report_path = run.provider_capabilities_path
    created_at = utc_now()
    if report_path.is_file():
        previous = read_json(report_path)
        if (
            previous.get("powerpoint_live", {}).get("capability_fingerprint_sha256")
            == capabilities.get("capability_fingerprint_sha256")
        ):
            created_at = previous.get("created_at") or created_at
    report = {
        "schema_version": "1.0.0",
        "kind": "provider_capabilities",
        "created_at": created_at,
        "case": run.root.name,
        "reference_sha256": run.load_meta()["source_sha256"],
        "powerpoint_live": capabilities,
    }
    write_json(report_path, report)
    return report


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
