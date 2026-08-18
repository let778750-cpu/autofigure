"""Migrate a Figure Spec 3.0 document to the case-neutral 4.0 contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from render_strategy import classify_edge, classify_element  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "figure-spec.schema.json"


class FigureSpecMigrationError(RuntimeError):
    """Raised when a legacy spec cannot be migrated without inventing content."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FigureSpecMigrationError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FigureSpecMigrationError(f"{label} must be one JSON object")
    return value


def _validate(document: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
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
        raise FigureSpecMigrationError(f"{label} rejected at {location}: {first.message}")


def _geometry_source(element: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(element.get("geometry_source"), Mapping):
        return copy.deepcopy(dict(element["geometry_source"]))
    evidence = set(str(item) for item in element.get("source_evidence", []))
    if "manual_measurement" in evidence:
        return {"kind": "manual_measurement"}
    if "target_visual" in evidence:
        return {"kind": "target_visual"}
    return {"kind": "designer_authored"}


def _render_strategy(element: Mapping[str, Any]) -> str:
    if isinstance(element.get("render_strategy"), str):
        return str(element["render_strategy"])
    element_type = str(element.get("type", ""))
    legacy = str(element.get("strategy", "native_editable"))
    legacy_result = {
        "native_editable": "native_preferred",
        "manual_asset_slot": "manual_asset_slot",
        "source_ambiguity": "source_ambiguity",
    }.get(legacy)
    if legacy_result is not None:
        return "native_required" if element_type in {"text", "formula"} else legacy_result
    return classify_element(element)


def _edge_style(edge: Mapping[str, Any]) -> dict[str, Any]:
    legacy = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
    arrowhead = str(legacy.get("arrowhead", "triangle"))
    start = "triangle" if arrowhead == "both" else "none"
    end = "triangle" if arrowhead in {"both", "triangle", "arrow"} else "none"
    return {
        **dict(legacy),
        "start_arrowhead": str(legacy.get("start_arrowhead", start)),
        "end_arrowhead": str(legacy.get("end_arrowhead", end)),
        "stroke_color": str(legacy.get("stroke_color", legacy.get("color", "#000000"))),
        "stroke_width_px": float(legacy.get("stroke_width_px", legacy.get("width_px", 1.5))),
        "dash": str(legacy.get("dash", "solid")),
        "cap": str(legacy.get("cap", "round")),
        "join": str(legacy.get("join", "round")),
    }


def upgrade_elements(elements: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return v4 elements without mutating the authored scene records."""
    upgraded: list[dict[str, Any]] = []
    for original in elements:
        element = copy.deepcopy(dict(original))
        legacy_type = str(element.get("type", ""))
        if legacy_type == "micro_asset":
            element["type"] = "group"
        elif legacy_type == "shape":
            element["type"] = "native_shape"
        element["render_strategy"] = _render_strategy(original)
        element["geometry_source"] = _geometry_source(original)
        element["review_risk"] = str(
            original.get(
                "review_risk",
                "critical"
                if original.get("criticality") == "critical" or legacy_type == "formula"
                else "ordinary",
            )
        )
        upgraded.append(element)
    return upgraded


def upgrade_edges(edges: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return explicit v4 arrow and route records."""
    upgraded: list[dict[str, Any]] = []
    for original in edges:
        edge = copy.deepcopy(dict(original))
        representation, arrow_class = classify_edge(edge)
        edge.setdefault("representation", representation)
        edge.setdefault("arrow_class", arrow_class)
        edge["style"] = _edge_style(original)
        upgraded.append(edge)
    return upgraded


def migrate_spec(document: Mapping[str, Any], *, source_path: Path | None = None) -> dict[str, Any]:
    if document.get("schema_version") != "3.0":
        raise FigureSpecMigrationError("input Figure Spec must have schema_version=3.0")
    migrated = copy.deepcopy(dict(document))
    migrated["schema_version"] = "4.0"
    migrated["policy_profile"] = "standard"
    migrated["elements"] = upgrade_elements(migrated.get("elements", []))
    migrated["edges"] = upgrade_edges(migrated.get("edges", []))
    migrated["migration"] = {
        "from_schema_version": "3.0",
        "method": "deterministic_case_neutral_v1",
        **(
            {"source_path": str(source_path), "source_sha256": _sha256(source_path)}
            if source_path is not None
            else {}
        ),
    }
    return migrated


def migrate_file(input_path: Path, output_path: Path, *, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    if output_path.exists():
        raise FigureSpecMigrationError(f"output already exists: {output_path}")
    resolved_input = input_path.resolve(strict=True)
    schema = _load(schema_path.resolve(strict=True), "Figure Spec schema")
    Draft202012Validator.check_schema(schema)
    document = _load(resolved_input, "Figure Spec v3")
    _validate(document, schema, "input Figure Spec v3")
    migrated = migrate_spec(document, source_path=resolved_input)
    _validate(migrated, schema, "migrated Figure Spec v4")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(migrated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return migrated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        migrated = migrate_file(args.input, args.output, schema_path=args.schema)
    except (OSError, FigureSpecMigrationError) as exc:
        print(f"FIGURE_SPEC_MIGRATION_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "SPEC_V4_MIGRATED", "elements": len(migrated["elements"]), "edges": len(migrated["edges"]), "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
