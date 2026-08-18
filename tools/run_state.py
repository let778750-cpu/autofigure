"""Maintain one hash-bound current state and append-only event ledger per run."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "run-state.schema.json"
STATE_FILE = "run-state.json"
EVENT_FILE = "run-events.jsonl"

TRANSITIONS: dict[str, set[str]] = {
    "REFERENCE_FROZEN": {"PERCEPTION_COMPLETE", "STALLED"},
    "PERCEPTION_COMPLETE": {"REVIEWED", "STALLED"},
    "REVIEWED": {"SPEC_DRAFT", "STALLED"},
    "SPEC_DRAFT": {"SPEC_FROZEN", "STALLED"},
    "SPEC_FROZEN": {"PREFLIGHT_PASS", "SPEC_DRAFT", "STALLED"},
    "PREFLIGHT_PASS": {"RENDERED", "SPEC_DRAFT", "STALLED"},
    "RENDERED": {"MECHANICAL_PASS", "SPEC_DRAFT", "STALLED"},
    "MECHANICAL_PASS": {"INDEPENDENT_REVIEW_PASS", "RENDERED", "STALLED"},
    "INDEPENDENT_REVIEW_PASS": {"RELEASE_CANDIDATE", "RENDERED", "STALLED"},
    "RELEASE_CANDIDATE": {"APPROVED", "RENDERED", "STALLED"},
    "APPROVED": set(),
    "STALLED": set(),
}


class RunStateError(RuntimeError):
    """Raised when the run ledger or requested state transition is invalid."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunStateError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunStateError(f"{label} must be one JSON object")
    return value


def _validate(document: Mapping[str, Any], schema_path: Path = DEFAULT_SCHEMA) -> None:
    schema = _load_json(schema_path.resolve(strict=True), "run-state schema")
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in first.absolute_path
        )
        raise RunStateError(f"run state rejected at {location}: {first.message}")


def _atomic_write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _event_binding(event_path: Path, count: int) -> dict[str, Any]:
    return {"path": str(event_path), "sha256": _sha256(event_path), "event_count": count}


def _read_events(event_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = event_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RunStateError(f"cannot read run event log: {event_path}: {exc}") from exc
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            raise RunStateError(f"blank line in run event log at {index}")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunStateError(f"invalid event JSON at line {index}: {exc}") from exc
        if not isinstance(event, dict) or event.get("sequence") != index:
            raise RunStateError(f"event sequence mismatch at line {index}")
        events.append(event)
    if not events:
        raise RunStateError("run event log is empty")
    return events


def _evidence_records(paths: Sequence[Path], run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    resolved_run = run_dir.resolve(strict=True)
    for requested in paths:
        resolved = requested.resolve(strict=True)
        try:
            resolved.relative_to(resolved_run)
        except ValueError as exc:
            raise RunStateError(f"event evidence escapes the run directory: {resolved}") from exc
        if not resolved.is_file():
            raise RunStateError(f"event evidence is not a file: {resolved}")
        records.append({"path": str(resolved), "sha256": _sha256(resolved)})
    return records


def initialize_run_state(
    run_dir: Path,
    source_path: Path,
    source_sha256: str,
    *,
    policy_profile: str = "standard",
    run_id: str | None = None,
) -> dict[str, Any]:
    resolved_run = run_dir.resolve(strict=True)
    resolved_source = source_path.resolve(strict=True)
    state_path = resolved_run / STATE_FILE
    event_path = resolved_run / EVENT_FILE
    if state_path.exists() or event_path.exists():
        raise RunStateError(f"run state already exists in {resolved_run}")
    actual_source_sha = _sha256(resolved_source)
    if actual_source_sha.casefold() != source_sha256.casefold():
        raise RunStateError(
            f"source SHA-256 mismatch: expected {source_sha256}, got {actual_source_sha}"
        )
    identifier = run_id or resolved_run.name
    timestamp = _now()
    event = {
        "schema_version": "1.0.0",
        "sequence": 1,
        "event_id": str(uuid.uuid4()),
        "at_utc": timestamp,
        "from_state": None,
        "to_state": "REFERENCE_FROZEN",
        "actor": "runner",
        "stage": "reference",
        "evidence": [{"path": str(resolved_source), "sha256": actual_source_sha}],
        "note": "Frozen designated reference and initialized the only current run lineage.",
    }
    event_path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    state = {
        "schema_version": "1.0.0",
        "document_type": "AUTOFIGURE_RUN_STATE",
        "run_id": identifier,
        "mode": "RECONSTRUCT_1TO1",
        "policy_profile": policy_profile,
        "source": {"path": str(resolved_source), "sha256": actual_source_sha},
        "current_state": "REFERENCE_FROZEN",
        "last_successful_state": "REFERENCE_FROZEN",
        "release_ceiling": "INDEPENDENT_REVIEW_REQUIRED",
        "approval_status": "PENDING",
        "event_log": _event_binding(event_path, 1),
        "updated_at_utc": timestamp,
    }
    _validate(state)
    _atomic_write(state_path, state)
    return state


def load_run_state(run_dir: Path) -> dict[str, Any]:
    resolved_run = run_dir.resolve(strict=True)
    state_path = resolved_run / STATE_FILE
    state = _load_json(state_path, "run state")
    _validate(state)
    event_path = Path(str(state["event_log"]["path"])).resolve(strict=True)
    if event_path != (resolved_run / EVENT_FILE).resolve():
        raise RunStateError("run state points to a non-canonical event log")
    events = _read_events(event_path)
    binding = _event_binding(event_path, len(events))
    if binding != state["event_log"]:
        raise RunStateError("run-state event binding is stale")
    if events[-1].get("to_state") != state["current_state"]:
        raise RunStateError("run-state current_state differs from the final event")
    if state["run_id"] != resolved_run.name:
        raise RunStateError("run-state run_id differs from its directory name")
    return state


def release_ceiling_for_elements(elements: Sequence[Mapping[str, Any]]) -> str:
    has_preview = False
    has_slot = False
    for element in elements:
        if element.get("type") != "manual_asset_slot":
            continue
        slot = element.get("slot_contract")
        if not isinstance(slot, Mapping):
            has_slot = True
            continue
        mode = slot.get("mode")
        has_preview = has_preview or mode == "reference_preview"
        has_slot = has_slot or mode != "reference_preview"
    if has_preview:
        return "CANDIDATE_WITH_REFERENCE_PREVIEWS"
    if has_slot:
        return "CANDIDATE_WITH_SLOTS"
    return "CANDIDATE"


def advance_run_state(
    run_dir: Path,
    to_state: str,
    *,
    actor: str,
    stage: str,
    evidence_paths: Sequence[Path],
    note: str,
    release_ceiling: str | None = None,
) -> dict[str, Any]:
    resolved_run = run_dir.resolve(strict=True)
    state = load_run_state(resolved_run)
    current = str(state["current_state"])
    if to_state not in TRANSITIONS.get(current, set()):
        raise RunStateError(f"illegal run-state transition: {current} -> {to_state}")
    if to_state not in {"STALLED"} and not evidence_paths:
        raise RunStateError(f"{to_state} requires at least one hash-bound evidence file")
    if to_state == "APPROVED" and actor != "user":
        raise RunStateError("only actor=user may advance a run to APPROVED")
    if actor == "runner" and to_state in {"INDEPENDENT_REVIEW_PASS", "RELEASE_CANDIDATE", "APPROVED"}:
        raise RunStateError(f"actor=runner cannot self-authorize {to_state}")
    records = _evidence_records(evidence_paths, resolved_run)
    event_path = resolved_run / EVENT_FILE
    events = _read_events(event_path)
    timestamp = _now()
    event = {
        "schema_version": "1.0.0",
        "sequence": len(events) + 1,
        "event_id": str(uuid.uuid4()),
        "at_utc": timestamp,
        "from_state": current,
        "to_state": to_state,
        "actor": actor,
        "stage": stage,
        "evidence": records,
        "note": note,
    }
    with event_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    next_state = dict(state)
    next_state["current_state"] = to_state
    if to_state != "STALLED":
        next_state["last_successful_state"] = to_state
    if release_ceiling is not None:
        next_state["release_ceiling"] = release_ceiling
    if to_state == "APPROVED":
        next_state["approval_status"] = "HUMAN_APPROVED"
    next_state["event_log"] = _event_binding(event_path, len(events) + 1)
    next_state["updated_at_utc"] = timestamp
    _validate(next_state)
    _atomic_write(resolved_run / STATE_FILE, next_state)
    return next_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--source", required=True, type=Path)
    init.add_argument("--source-sha256", required=True)
    init.add_argument("--policy-profile", choices=["standard", "strict"], default="standard")
    advance = subparsers.add_parser("advance")
    advance.add_argument("--run-dir", required=True, type=Path)
    advance.add_argument("--to-state", required=True, choices=sorted(TRANSITIONS))
    advance.add_argument("--actor", required=True, choices=["runner", "designer", "drawer", "reviewer", "user"])
    advance.add_argument("--stage", required=True)
    advance.add_argument("--evidence", action="append", type=Path, default=[])
    advance.add_argument("--note", required=True)
    advance.add_argument("--release-ceiling", choices=["INDEPENDENT_REVIEW_REQUIRED", "CANDIDATE", "CANDIDATE_WITH_SLOTS", "CANDIDATE_WITH_REFERENCE_PREVIEWS"])
    status = subparsers.add_parser("status")
    status.add_argument("--run-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            state = initialize_run_state(
                args.run_dir,
                args.source,
                args.source_sha256,
                policy_profile=args.policy_profile,
            )
        elif args.command == "advance":
            state = advance_run_state(
                args.run_dir,
                args.to_state,
                actor=args.actor,
                stage=args.stage,
                evidence_paths=args.evidence,
                note=args.note,
                release_ceiling=args.release_ceiling,
            )
        else:
            state = load_run_state(args.run_dir)
    except (OSError, RunStateError) as exc:
        print(f"RUN_STATE_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
