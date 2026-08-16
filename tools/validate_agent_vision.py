#!/usr/bin/env python3
"""Validate an agent-filled native-vision response and stamp it as evidence.

The response is untrusted input: every structural, binding, coverage, bounds,
and self-consistency check happens here, never in the agent.  The stamped
``AGENT_VISION_OBSERVATIONS`` document is the only vision artifact downstream
fusion accepts, and it remains candidate evidence without text or coordinate
authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from evidence_metrics import latex_samples_self_consistent
    from prepare_agent_vision_task import (
        EXIT_CONTRACT_REJECTED,
        EXIT_OK,
        TaskPackageError,
        load_json_object,
        load_schema,
        sha256_file,
        validate_json,
    )
    from prepare_agent_vision_task import verify_task_package_file
except ModuleNotFoundError:  # Support: python -m tools.validate_agent_vision
    from .evidence_metrics import latex_samples_self_consistent
    from .prepare_agent_vision_task import (
        EXIT_CONTRACT_REJECTED,
        EXIT_OK,
        TaskPackageError,
        load_json_object,
        load_schema,
        sha256_file,
        validate_json,
    )
    from .prepare_agent_vision_task import verify_task_package_file

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.validate_agent_vision
    try:
        from .output_policy import resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the root.
        from tools.output_policy import resolve_output_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESPONSE_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "agent-vision.schema.json"

CHECK_TASK_PACKAGE_SCHEMA = "TASK_PACKAGE_SCHEMA"
CHECK_CROP_HASHES = "TASK_PACKAGE_CROP_HASHES"
CHECK_RESPONSE_SCHEMA = "RESPONSE_SCHEMA"
CHECK_BINDING = "TASK_PACKAGE_BINDING"
CHECK_COVERAGE = "QUERY_COVERAGE_EXACT"
CHECK_COORDINATES = "COORDINATES_IN_CANVAS"
CHECK_PANEL_LIMIT = "PANEL_LIMIT"
CHECK_SELECTION_BOUNDS = "CONFLICT_SELECTION_BOUNDS"
CHECK_SELF_CONSISTENCY = "FORMULA_SELF_CONSISTENCY_COMPUTED"

MIN_PANEL_AREA_PX = 16


class ResponseRejected(TaskPackageError):
    """The agent response failed a fail-closed check."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResponseRejected(message)


def _validate_bindings(package: Mapping[str, Any], response: Mapping[str, Any], package_path: Path) -> None:
    binding = response["task_package"]
    _require(
        str(binding["sha256"]).upper() == sha256_file(package_path),
        f"response task_package.sha256 does not match {package_path}",
    )
    _require(str(binding["run_id"]) == str(package["run_id"]), "response run_id mismatch")
    _require(
        str(binding["source_sha256"]).upper() == str(package["source"]["sha256"]).upper(),
        "response source_sha256 mismatch",
    )


def _validate_coverage(package: Mapping[str, Any], response: Mapping[str, Any]) -> None:
    package_queries = {q["query_id"]: q["task_type"] for q in package["queries"]}
    response_ids = [q["query_id"] for q in response["queries"]]
    duplicates = sorted(cid for cid, count in Counter(response_ids).items() if count > 1)
    _require(not duplicates, f"duplicate response query ids: {duplicates}")
    missing = sorted(set(package_queries) - set(response_ids))
    extra = sorted(set(response_ids) - set(package_queries))
    _require(not missing and not extra, f"query coverage mismatch: missing={missing}, extra={extra}")
    for query in response["queries"]:
        expected_type = package_queries[query["query_id"]]
        _require(
            query["task_type"] == expected_type,
            f"{query['query_id']} task_type {query['task_type']!r} does not match package {expected_type!r}",
        )


def _validate_structure_observation(query: Mapping[str, Any], width: int, height: int, max_panels: int) -> None:
    structure = query.get("structure")
    _require(structure is not None, f"{query['query_id']} OBSERVED structure query lacks a structure payload")
    panels = structure["panels"]
    _require(len(panels) <= max_panels, f"{query['query_id']} exceeds the panel limit ({max_panels})")
    ranks = [panel["reading_order_rank"] for panel in panels]
    _require(
        len(ranks) == len(set(ranks)),
        f"{query['query_id']} has duplicate reading_order_rank values",
    )
    for panel in panels:
        box = panel["bbox_source"]
        _require(
            0 <= box["x0"] < box["x1"] <= width and 0 <= box["y0"] < box["y1"] <= height,
            f"{query['query_id']} panel {panel['panel_id']} bbox escapes the canvas: {box}",
        )
        area = (box["x1"] - box["x0"]) * (box["y1"] - box["y0"])
        _require(
            area >= MIN_PANEL_AREA_PX,
            f"{query['query_id']} panel {panel['panel_id']} bbox is degenerate: {box}",
        )


def _validate_conflict_observation(query: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    conflict = query.get("conflict")
    _require(conflict is not None, f"{query['query_id']} OBSERVED conflict query lacks a conflict payload")
    decision = conflict["decision"]
    _require(
        decision in ("SELECT", "REJECT_ALL"),
        f"{query['query_id']} OBSERVED conflict decision must be SELECT or REJECT_ALL",
    )
    selections_count = len(payload["selections"])
    if decision == "SELECT":
        index = conflict["selected_index"]
        _require(
            isinstance(index, int) and 0 <= index < selections_count,
            f"{query['query_id']} SELECT requires an index within [0, {selections_count})",
        )
    else:
        _require(
            conflict["selected_index"] is None,
            f"{query['query_id']} REJECT_ALL must not carry a selected_index",
        )


def _compute_formula_self_consistency(
    query: Mapping[str, Any], samples_required: int
) -> str:
    formula = query.get("formula")
    _require(formula is not None, f"{query['query_id']} OBSERVED formula query lacks a formula payload")
    samples = formula["samples"]
    indices = sorted(sample["sample_index"] for sample in samples)
    _require(
        len(samples) == samples_required and indices == list(range(1, samples_required + 1)),
        f"{query['query_id']} requires exactly {samples_required} independent samples",
    )
    latex_values = [str(sample["latex"]) for sample in samples]
    return "SELF_CONSISTENT_K3" if latex_samples_self_consistent(latex_values) else "INCONSISTENT"


def _validate_miss_scan_observation(query: Mapping[str, Any]) -> None:
    miss = query.get("miss_scan")
    _require(miss is not None, f"{query['query_id']} OBSERVED miss-scan query lacks a miss_scan payload")
    _require(
        isinstance(miss["contains_text"], bool),
        f"{query['query_id']} OBSERVED miss-scan requires a boolean contains_text",
    )
    _require(
        miss["contains_text"] or miss["text_hypothesis"] is None,
        f"{query['query_id']} text_hypothesis requires contains_text=true",
    )


def validate_response(
    package_path: Path,
    response_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    package = verify_task_package_file(package_path)
    response = load_json_object(response_path, "agent-vision response")
    response_schema = load_schema(RESPONSE_SCHEMA_PATH, "agent-vision response schema")

    _require(
        response.get("document_type") == "AGENT_VISION_RESPONSE",
        "response document_type must be AGENT_VISION_RESPONSE (validator stamps OBSERVATIONS)",
    )
    _require(response.get("validation") is None, "response must not pre-fill its own validation block")
    checks = [CHECK_TASK_PACKAGE_SCHEMA, CHECK_CROP_HASHES]
    validate_json(response, response_schema, "agent-vision response")
    checks.append(CHECK_RESPONSE_SCHEMA)

    _validate_bindings(package, response, package_path)
    checks.append(CHECK_BINDING)
    _validate_coverage(package, response)
    checks.append(CHECK_COVERAGE)

    payload_by_query = {
        q["query_id"]: q["payload"] for q in package["queries"]
    }
    width = int(package["source"]["width_px"])
    height = int(package["source"]["height_px"])
    max_panels = int(package["limits"]["max_panel_proposals"])
    formula_samples_required = int(package["limits"]["formula_samples"])

    stamped_queries: list[dict[str, Any]] = []
    for query in response["queries"]:
        query_id = query["query_id"]
        payload = payload_by_query[query_id]
        stamped = dict(query)
        observed = query["observation_status"] == "OBSERVED"
        if observed:
            if query["task_type"] == "STRUCTURE_GLOBAL":
                _validate_structure_observation(query, width, height, max_panels)
            elif query["task_type"] == "CONFLICT_ARBITRATION":
                _validate_conflict_observation(query, payload)
            elif query["task_type"] == "FORMULA_TRANSCRIPTION":
                stamped_formula = dict(query["formula"])
                stamped_formula["self_consistency"] = _compute_formula_self_consistency(
                    query, formula_samples_required
                )
                stamped["formula"] = stamped_formula
            else:
                _validate_miss_scan_observation(query)
        else:
            for field in ("structure", "conflict", "miss_scan"):
                _require(
                    query.get(field) is None,
                    f"{query_id} NOT_OBSERVABLE must leave '{field}' null",
                )
            if query.get("formula") is not None:
                _require(
                    query["formula"].get("samples") == [],
                    f"{query_id} NOT_OBSERVABLE must not carry formula samples",
                )
        stamped_queries.append(stamped)
    checks.extend([CHECK_COORDINATES, CHECK_PANEL_LIMIT, CHECK_SELECTION_BOUNDS, CHECK_SELF_CONSISTENCY])

    stamped: dict[str, Any] = dict(response)
    stamped["document_type"] = "AGENT_VISION_OBSERVATIONS"
    stamped["queries"] = stamped_queries
    stamped["validation"] = {
        "validated_at_utc": utc_now(),
        "task_package_sha256": sha256_file(package_path),
        "checks_passed": checks,
    }
    validate_json(stamped, response_schema, "stamped agent-vision observations")

    destination = Path(resolve_output_path(output_path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(stamped, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return stamped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an agent-vision response and stamp hash-bound observations."
    )
    parser.add_argument("--task-package", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        stamped = validate_response(
            package_path=Path(args.task_package),
            response_path=Path(args.response),
            output_path=Path(args.output),
        )
    except (TaskPackageError, ResponseRejected) as exc:
        print(f"AGENT_VISION_RESPONSE_REJECTED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_REJECTED

    counts = Counter(q["observation_status"] for q in stamped["queries"])
    print(
        json.dumps(
            {
                "status": "AGENT_VISION_OBSERVATIONS_STAMPED",
                "query_count": len(stamped["queries"]),
                "observed": counts["OBSERVED"],
                "not_observable": counts["NOT_OBSERVABLE"],
                "output": str(Path(args.output).resolve()),
                "sha256": sha256_file(args.output),
            },
            ensure_ascii=False,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
