"""Versioned Autofigure v3 contracts and workflow state management.

The v2 command surface remains compatible, but every mutating command now binds
its output to the reference hash and to explicit scene/asset/region/binding
manifests.  These documents are deliberately model- and renderer-independent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "3.0.0"
SOURCE_MODES = ("svg_import", "svg_repair", "png_reconstruct")
FIDELITY_PROFILES = ("editable_native", "hybrid_fidelity")
WORKFLOW_STATES = ("prepared", "candidate", "qa_failed", "repairing", "approved")

_TRANSITIONS = {
    "prepared": {"candidate", "repairing", "qa_failed"},
    "candidate": {"qa_failed", "repairing", "approved"},
    "qa_failed": {"repairing", "candidate"},
    "repairing": {"candidate", "qa_failed", "approved"},
    "approved": {"repairing", "qa_failed"},
}


class ContractError(ValueError):
    """Raised when a frozen run contract is missing, stale, or inconsistent."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON contract {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON contract must be an object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _meta_source_mode(run, requested: str | None) -> str:
    if requested and requested != "auto":
        if requested not in SOURCE_MODES:
            raise ContractError(f"unsupported source mode: {requested}")
        return requested
    return "svg_import" if run.redraw_svg.is_file() else "png_reconstruct"


def _base_document(run, kind: str) -> dict[str, Any]:
    meta = run.load_meta()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "updated_at": utc_now(),
    }


def _default_scene(run) -> dict[str, Any]:
    meta = run.load_meta()
    return {
        **_base_document(run, "scene"),
        "canvas": {"width": int(meta["width"]), "height": int(meta["height"]), "unit": "px"},
        "elements": [],
        "edges": [],
    }


def _default_assets(run) -> dict[str, Any]:
    return {
        **_base_document(run, "assets"),
        "policy": {
            "formal_content_native": True,
            "authorized_atomic_raster_only": True,
            "whole_reference_forbidden": True,
        },
        "assets": [],
    }


def _default_regions(run) -> dict[str, Any]:
    meta = run.load_meta()
    return {
        **_base_document(run, "regions"),
        "defaults": {"ssim_min": 0.85, "edge_iou_min": 0.75},
        "regions": [
            {
                "id": "whole-canvas",
                "label": "Whole canvas (diagnostic only)",
                "bbox": [0, 0, int(meta["width"]), int(meta["height"])],
                "critical": False,
            }
        ],
    }


def _default_bindings(run) -> dict[str, Any]:
    return {**_base_document(run, "bindings"), "backend": "pptx-offline", "bindings": []}


def initialize_contracts(
    run,
    *,
    source_mode: str | None = None,
    fidelity_profile: str | None = None,
) -> dict[str, Any]:
    """Create missing v3 contracts and upgrade run.json without discarding v2 keys."""
    meta = run.load_meta()
    mode = _meta_source_mode(run, source_mode or meta.get("source_mode"))
    profile = fidelity_profile or meta.get("fidelity_profile") or "editable_native"
    if profile not in FIDELITY_PROFILES:
        raise ContractError(f"unsupported fidelity profile: {profile}")

    changed = False
    if meta.get("schema_version") != SCHEMA_VERSION:
        meta["schema_version"] = SCHEMA_VERSION
        changed = True
    for key, value in (
        ("source_mode", mode),
        ("fidelity_profile", profile),
        ("backend_mode", meta.get("backend_mode", "offline")),
    ):
        if meta.get(key) != value:
            meta[key] = value
            changed = True
    if "workflow" not in meta:
        meta["workflow"] = {
            "state": "prepared",
            "revision": 0,
            "history": [{"state": "prepared", "at": meta.get("created_at", utc_now()), "reason": "run-created"}],
        }
        changed = True
    if changed:
        write_json(run.meta_path, meta)

    defaults = (
        (run.scene_path, _default_scene),
        (run.assets_path, _default_assets),
        (run.regions_path, _default_regions),
        (run.bindings_path, _default_bindings),
    )
    for path, factory in defaults:
        if not path.is_file():
            write_json(path, factory(run))
    return run.load_meta()


def validate_reference(run) -> dict[str, Any]:
    """Hard-bind every phase to the copied reference bytes and dimensions."""
    from tools.v2.common import image_size, sha256_file

    meta = run.load_meta()
    if not run.source_png.is_file():
        raise ContractError(f"reference image is missing: {run.source_png}")
    actual_sha = sha256_file(run.source_png)
    expected_sha = meta.get("source_sha256")
    if not expected_sha or actual_sha != expected_sha:
        raise ContractError(
            f"reference hash mismatch: expected {expected_sha or '[missing]'}, got {actual_sha}"
        )
    actual_size = image_size(run.source_png)
    expected_size = (int(meta.get("width", -1)), int(meta.get("height", -1)))
    if actual_size != expected_size:
        raise ContractError(f"reference size mismatch: expected {expected_size}, got {actual_size}")
    return {"sha256": actual_sha, "width": actual_size[0], "height": actual_size[1]}


def transition(run, new_state: str, reason: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    if new_state not in WORKFLOW_STATES:
        raise ContractError(f"unsupported workflow state: {new_state}")
    meta = initialize_contracts(run)
    workflow = meta["workflow"]
    current = workflow["state"]
    if new_state != current and new_state not in _TRANSITIONS[current]:
        raise ContractError(f"invalid workflow transition: {current} -> {new_state}")
    if new_state != current:
        workflow["revision"] = int(workflow.get("revision", 0)) + 1
        workflow["state"] = new_state
    workflow.setdefault("history", []).append(
        {"state": new_state, "at": utc_now(), "reason": reason, **({"details": details} if details else {})}
    )
    write_json(run.meta_path, meta)
    return meta


def set_modes(
    run,
    *,
    source_mode: str | None = None,
    fidelity_profile: str | None = None,
    backend_mode: str | None = None,
) -> dict[str, Any]:
    meta = initialize_contracts(run, source_mode=source_mode, fidelity_profile=fidelity_profile)
    if source_mode is not None:
        if source_mode not in SOURCE_MODES:
            raise ContractError(f"unsupported source mode: {source_mode}")
        meta["source_mode"] = source_mode
    if fidelity_profile is not None:
        if fidelity_profile not in FIDELITY_PROFILES:
            raise ContractError(f"unsupported fidelity profile: {fidelity_profile}")
        meta["fidelity_profile"] = fidelity_profile
    if backend_mode is not None:
        if backend_mode not in ("offline", "hybrid"):
            raise ContractError(f"unsupported backend mode: {backend_mode}")
        meta["backend_mode"] = backend_mode
    write_json(run.meta_path, meta)
    return meta
