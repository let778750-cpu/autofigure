"""Versioned Autofigure v4 contracts and workflow state management.

The v2 command surface remains compatible, but every mutating command now binds
its output to the reference hash and to explicit scene/asset/region/binding
manifests.  These documents are deliberately model- and renderer-independent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "4.0.0"
TASK_MODE = "RECONSTRUCT_1TO1"
INPUT_ROUTES = ("reference-only", "svg-seeded")
PROCESSING_MODES = ("svg_import", "svg_repair", "png_reconstruct")
# One-release import compatibility. Durable metadata uses ``processing_mode`` only.
SOURCE_MODES = PROCESSING_MODES
FIDELITY_PROFILES = ("editable_native", "hybrid_fidelity")
WORKFLOW_STATES = (
    "prepared",
    "ready",
    "candidate",
    "qa_failed",
    "repairing",
    "host_verifying",
    "approved",
)
VALIDATION_STATUSES = ("not_run", "diagnostic", "failed", "passed")

_TRANSITIONS = {
    "prepared": {"ready", "candidate", "repairing", "qa_failed"},
    "ready": {"candidate", "repairing", "qa_failed"},
    "candidate": {"qa_failed", "repairing", "host_verifying", "approved"},
    "qa_failed": {"repairing", "candidate", "ready"},
    "repairing": {"candidate", "qa_failed", "host_verifying", "approved"},
    "host_verifying": {"approved", "qa_failed", "repairing", "candidate"},
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


def _validated_input_route(meta: dict[str, Any], requested: str | None) -> str:
    existing = meta.get("input_route")
    if existing is None:
        if requested is None:
            raise ContractError(
                "legacy run has no input_route; use an explicit migration instead of "
                "inferring provenance from source_mode or existing files"
            )
        existing = requested
    if existing not in INPUT_ROUTES:
        raise ContractError(f"unsupported input route: {existing}")
    if requested is not None and requested != existing:
        raise ContractError(f"input_route is immutable: {existing} -> {requested}")
    return existing


def _validated_processing_mode(meta: dict[str, Any], requested: str | None) -> str:
    mode = requested or meta.get("processing_mode") or meta.get("source_mode")
    if mode not in PROCESSING_MODES:
        raise ContractError(f"unsupported processing mode: {mode or '[missing]'}")
    route = meta.get("input_route")
    if route == "reference-only" and mode != "png_reconstruct":
        raise ContractError("reference-only cases must use png_reconstruct")
    return mode


def _base_document(run, kind: str) -> dict[str, Any]:
    meta = run.load_meta()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "task_mode": TASK_MODE,
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "updated_at": utc_now(),
    }


def _default_scene(run) -> dict[str, Any]:
    meta = run.load_meta()
    return {
        **_base_document(run, "scene"),
        "canvas": {"width": int(meta["width"]), "height": int(meta["height"]), "unit": "px"},
        "canonical_source": "scene",
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
        "microasset_opportunity_map": [],
        "assets": [],
    }


def _default_regions(run) -> dict[str, Any]:
    meta = run.load_meta()
    from tools.assets.reference_inventory import default_inventory

    return {
        **_base_document(run, "regions"),
        "defaults": {"ssim_min": 0.85, "edge_iou_min": 0.75},
        "critical_region_expectation": {"count": 0, "contracts": []},
        "reference_inventory": default_inventory(meta["source_sha256"]),
        "arrow_visual_expectation": {"count": 0, "contracts": []},
        "primitive_expectations": [],
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
    return {
        **_base_document(run, "bindings"),
        "backend": "pptx-offline",
        "bindings": [],
        "logical_group_bindings": [],
    }


def _default_provenance(run) -> dict[str, Any]:
    meta = run.load_meta()
    return {
        **_base_document(run, "provenance"),
        "task_mode": TASK_MODE,
        "input_route": meta["input_route"],
        "reference": {
            "path": "reference.png",
            "sha256": meta["source_sha256"],
            "original_name": meta.get("source_original_name", "unknown"),
        },
        "external_svg_seed": None,
        "candidate_history": [],
        "events": [],
        "comparison_group": None,
    }


def _upgrade_document_identity(run, path: Path) -> None:
    payload = read_json(path)
    meta = run.load_meta()
    if payload.get("case") != meta["case"]:
        raise ContractError(f"contract case mismatch: {path}")
    if payload.get("reference_sha256") != meta["source_sha256"]:
        raise ContractError(f"contract reference hash mismatch: {path}")
    changed = False
    if payload.get("schema_version") != SCHEMA_VERSION:
        payload["schema_version"] = SCHEMA_VERSION
        payload["updated_at"] = utc_now()
        changed = True
    if payload.get("task_mode") != TASK_MODE:
        payload["task_mode"] = TASK_MODE
        payload["updated_at"] = utc_now()
        changed = True
    if changed:
        write_json(path, payload)


def initialize_contracts(
    run,
    *,
    input_route: str | None = None,
    processing_mode: str | None = None,
    fidelity_profile: str | None = None,
) -> dict[str, Any]:
    """Create missing v4 contracts without guessing immutable provenance."""
    meta = run.load_meta()
    route = _validated_input_route(meta, input_route)
    mode = _validated_processing_mode(meta, processing_mode)
    profile = fidelity_profile or meta.get("fidelity_profile") or "editable_native"
    if profile not in FIDELITY_PROFILES:
        raise ContractError(f"unsupported fidelity profile: {profile}")

    changed = False
    if meta.get("schema_version") != SCHEMA_VERSION:
        meta["schema_version"] = SCHEMA_VERSION
        changed = True
    for key, value in (
        ("input_route", route),
        ("processing_mode", mode),
        ("fidelity_profile", profile),
        ("backend_mode", meta.get("backend_mode", "offline")),
        ("task_mode", TASK_MODE),
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
    if "validation" not in meta:
        meta["validation"] = {
            "profile": None,
            "status": "not_run",
            "checked_at": None,
            "blockers": [],
        }
        changed = True
    if "active_revision" not in meta:
        meta["active_revision"] = None
        changed = True
    if "source_mode" in meta:
        meta.pop("source_mode")
        changed = True
    if "source_abspath" in meta:
        legacy_path = Path(str(meta.pop("source_abspath")))
        meta.setdefault("source_original_name", legacy_path.name or "unknown")
        meta.setdefault("reference_path", "reference.png")
        changed = True
    if changed:
        write_json(run.meta_path, meta)

    defaults = (
        (run.scene_path, _default_scene),
        (run.assets_path, _default_assets),
        (run.regions_path, _default_regions),
        (run.bindings_path, _default_bindings),
        (run.provenance_path, _default_provenance),
    )
    for path, factory in defaults:
        if not path.is_file():
            write_json(path, factory(run))
        else:
            _upgrade_document_identity(run, path)
    provenance = read_json(run.provenance_path)
    if provenance.get("input_route") != route:
        raise ContractError(f"provenance input_route mismatch: {run.provenance_path}")
    if provenance.get("task_mode") != TASK_MODE:
        raise ContractError(f"provenance task_mode mismatch: {run.provenance_path}")
    return run.load_meta()


def validate_reference(run) -> dict[str, Any]:
    """Hard-bind every phase to the copied reference bytes and dimensions."""
    from tools.core.common import image_size, sha256_file

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


def set_processing_mode(
    run,
    *,
    processing_mode: str | None = None,
    fidelity_profile: str | None = None,
    backend_mode: str | None = None,
) -> dict[str, Any]:
    meta = initialize_contracts(
        run,
        processing_mode=processing_mode,
        fidelity_profile=fidelity_profile,
    )
    if processing_mode is not None:
        if processing_mode not in PROCESSING_MODES:
            raise ContractError(f"unsupported processing mode: {processing_mode}")
        if meta["input_route"] == "reference-only" and processing_mode != "png_reconstruct":
            raise ContractError("reference-only cases cannot switch away from png_reconstruct")
        meta["processing_mode"] = processing_mode
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


def set_modes(
    run,
    *,
    source_mode: str | None = None,
    processing_mode: str | None = None,
    fidelity_profile: str | None = None,
    backend_mode: str | None = None,
) -> dict[str, Any]:
    """Deprecated compatibility wrapper; ``source_mode`` is never serialized."""
    if source_mode is not None and processing_mode is not None and source_mode != processing_mode:
        raise ContractError("source_mode and processing_mode disagree")
    return set_processing_mode(
        run,
        processing_mode=processing_mode or source_mode,
        fidelity_profile=fidelity_profile,
        backend_mode=backend_mode,
    )


def record_candidate_provenance(
    run,
    source: Path,
    *,
    kind: str,
    origin: str,
    role: str,
    canonical_path: str,
) -> dict[str, Any]:
    meta = initialize_contracts(run)
    if role not in ("external-seed", "reconstruction-candidate", "repair-candidate"):
        raise ContractError(f"unsupported candidate role: {role}")
    if meta["input_route"] == "reference-only" and role == "external-seed":
        raise ContractError("reference-only cases cannot ingest an external SVG seed")
    source = source.resolve()
    record = {
        "kind": kind,
        "role": role,
        "origin": origin,
        "source_name": source.name,
        "canonical_path": canonical_path,
        "sha256": _sha256_candidate(source),
        "ingested_at": utc_now(),
    }
    provenance = read_json(run.provenance_path)
    if role == "external-seed" and provenance.get("external_svg_seed") is not None:
        existing = provenance["external_svg_seed"]
        if existing.get("sha256") != record["sha256"]:
            raise ContractError(
                "svg-seeded cases accept exactly one immutable external seed; "
                "create a new case for a replacement seed"
            )
        return existing
    provenance.setdefault("candidate_history", []).append(record)
    if role == "external-seed" and provenance.get("external_svg_seed") is None:
        provenance["external_svg_seed"] = dict(record)
    provenance.setdefault("events", []).append(
        {
            "event": "candidate-ingested",
            "at": record["ingested_at"],
            "candidate_sha256": record["sha256"],
            "role": role,
            "canonical_path": canonical_path,
        }
    )
    provenance["updated_at"] = utc_now()
    write_json(run.provenance_path, provenance)
    return record


def record_source_gate_provenance(
    run,
    report: dict[str, Any],
    *,
    immutable_external_seed: bool = False,
) -> dict[str, Any]:
    """Append a content-bound gate event and preserve seed admission evidence.

    ``qa/source-gate-report.json`` describes the latest candidate and is
    intentionally replaceable.  The sole external seed is different: its
    admission decision is immutable route evidence and must survive later
    repair-candidate gates for A/B reporting and fallback audits.
    """

    if report.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("source-gate provenance requires schema 4.0.0")
    if report.get("kind") != "source_gate_report":
        raise ContractError("source-gate provenance report kind is invalid")
    decision = report.get("decision")
    if decision not in {"accept", "repair", "reject"}:
        raise ContractError("source-gate provenance decision is invalid")
    route_gate = report.get("route_gate")
    candidate = report.get("candidate")
    if not isinstance(route_gate, dict) or not isinstance(candidate, dict):
        raise ContractError("source-gate provenance identity is incomplete")
    role = route_gate.get("candidate_role")
    candidate_sha256 = candidate.get("sha256")
    if role not in {"external-seed", "reconstruction-candidate", "repair-candidate"}:
        raise ContractError("source-gate provenance candidate role is invalid")
    if not isinstance(candidate_sha256, str) or len(candidate_sha256) != 64:
        raise ContractError("source-gate provenance candidate hash is invalid")
    report_sha256 = hashlib.sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    summary = {
        "candidate_sha256": candidate_sha256,
        "candidate_role": role,
        "decision": decision,
        "pass": report.get("pass") is True,
        "next_action": report.get("next_action"),
        "report_sha256": report_sha256,
        "blockers": [item for item in report.get("blockers", []) if isinstance(item, str)],
        "evaluated_at": report.get("created_at") or utc_now(),
    }
    provenance = read_json(run.provenance_path)
    if immutable_external_seed:
        meta = initialize_contracts(run)
        if meta["input_route"] != "svg-seeded" or role != "external-seed":
            raise ContractError("immutable seed gate applies only to svg-seeded external-seed")
        seed = provenance.get("external_svg_seed")
        if not isinstance(seed, dict) or seed.get("sha256") != candidate_sha256:
            raise ContractError("immutable seed gate does not match external-seed provenance")
        existing = provenance.get("external_seed_gate")
        if isinstance(existing, dict) and existing.get("report_sha256") != report_sha256:
            raise ContractError("immutable external seed gate decision already differs")
        provenance["external_seed_gate"] = dict(summary)
        seed["source_gate"] = dict(summary)
        write_json(run.external_seed_source_gate_report_path, report)
    history = provenance.setdefault("source_gate_history", [])
    identity = (candidate_sha256, role, decision, report_sha256)
    if not any(
        isinstance(item, dict)
        and (
            item.get("candidate_sha256"),
            item.get("candidate_role"),
            item.get("decision"),
            item.get("report_sha256"),
        )
        == identity
        for item in history
    ):
        history.append(dict(summary))
        provenance.setdefault("events", []).append(
            {
                "event": "source-gate-evaluated",
                "at": summary["evaluated_at"],
                "candidate_sha256": candidate_sha256,
                "role": role,
                "decision": decision,
                "report_sha256": report_sha256,
                "immutable_external_seed": immutable_external_seed,
            }
        )
    provenance["updated_at"] = utc_now()
    write_json(run.provenance_path, provenance)
    return summary


def record_seed_unavailable(
    run,
    *,
    reason: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Record an evidence gap without changing immutable ``input_route``."""

    meta = initialize_contracts(run)
    if meta["input_route"] != "svg-seeded":
        raise ContractError("seed_unavailable applies only to svg-seeded cases")
    if run.external_seed_svg.is_file():
        raise ContractError("cannot declare seed_unavailable while external-seed.svg exists")
    if not isinstance(reason, str) or not reason.strip():
        raise ContractError("seed_unavailable requires an explicit reason")
    provenance = read_json(run.provenance_path)
    seed_record = provenance.get("external_svg_seed")
    if isinstance(seed_record, dict):
        seed_record["availability"] = "seed_unavailable"
        seed_record["exact_bytes_available"] = False
        if expected_sha256 and not seed_record.get("sha256"):
            seed_record["sha256"] = expected_sha256
    events = provenance.setdefault("events", [])
    existing = next(
        (
            event
            for event in events
            if isinstance(event, dict) and event.get("event") == "seed-unavailable"
        ),
        None,
    )
    if existing is None:
        existing = {
            "event": "seed-unavailable",
            "at": utc_now(),
            "reason": reason.strip(),
            "expected_sha256": expected_sha256,
            "fallback_processing_mode": "png_reconstruct",
        }
        events.append(existing)
    provenance["updated_at"] = utc_now()
    write_json(run.provenance_path, provenance)
    set_processing_mode(
        run,
        processing_mode="png_reconstruct",
        fidelity_profile="hybrid_fidelity",
    )
    return existing


def _sha256_candidate(path: Path) -> str:
    from tools.core.common import sha256_file

    return sha256_file(path)


def record_validation(run, profile: str, blockers: list[str]) -> dict[str, Any]:
    if profile not in ("standard", "strict"):
        raise ContractError(f"unsupported validation profile: {profile}")
    meta = initialize_contracts(run)
    status = "diagnostic" if profile == "standard" else ("failed" if blockers else "passed")
    meta["validation"] = {
        "profile": profile,
        "status": status,
        "checked_at": utc_now(),
        "blockers": list(dict.fromkeys(blockers)),
    }
    write_json(run.meta_path, meta)
    return meta["validation"]


def migrate_legacy_run(
    run,
    *,
    input_route: str,
    processing_mode: str,
    workflow_state: str | None = None,
) -> dict[str, Any]:
    """Explicitly migrate one legacy case; never infer its original input route."""
    if input_route not in INPUT_ROUTES:
        raise ContractError(f"unsupported input route: {input_route}")
    if processing_mode not in PROCESSING_MODES:
        raise ContractError(f"unsupported processing mode: {processing_mode}")
    meta = run.load_meta()
    existing = meta.get("input_route")
    if existing is not None and existing != input_route:
        raise ContractError(f"input_route is immutable: {existing} -> {input_route}")
    meta["input_route"] = input_route
    meta["processing_mode"] = processing_mode
    meta["task_mode"] = TASK_MODE
    meta.pop("source_mode", None)
    legacy_path = meta.pop("source_abspath", None)
    if legacy_path:
        meta.setdefault("source_original_name", Path(str(legacy_path)).name or "unknown")
    meta.setdefault("reference_path", "reference.png")
    if workflow_state is not None:
        if workflow_state not in WORKFLOW_STATES:
            raise ContractError(f"unsupported workflow state: {workflow_state}")
        workflow = meta.setdefault("workflow", {"state": workflow_state, "revision": 0, "history": []})
        workflow["state"] = workflow_state
        history = workflow.setdefault("history", [])
        if not any(item.get("reason") == "explicit-v3.1-migration" for item in history):
            history.append(
                {"state": workflow_state, "at": utc_now(), "reason": "explicit-v3.1-migration"}
            )
    write_json(run.meta_path, meta)
    return initialize_contracts(
        run,
        input_route=input_route,
        processing_mode=processing_mode,
    )
