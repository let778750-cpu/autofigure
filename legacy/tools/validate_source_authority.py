#!/usr/bin/env python3
"""Validate a source-authority document and its frozen reference binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = PROJECT_ROOT / "schemas" / "source-authority.schema.json"


class SourceAuthorityError(RuntimeError):
    """Raised when authority evidence is incomplete or not reproducibly bound."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceAuthorityError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceAuthorityError(f"{label} must be a JSON object: {path}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _format_json_path(parts: Sequence[Any]) -> str:
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts
    )


def _validate_schema(document: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
        .iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        raise SourceAuthorityError(
            f"schema rejected authority at {_format_json_path(list(first.absolute_path))}: "
            f"{first.message}"
        )


def _resolve_source(project_root: Path, relative_path: str) -> Path:
    requested = Path(relative_path)
    if requested.is_absolute() or ".." in requested.parts:
        raise SourceAuthorityError("source.relative_path must be project-relative without '..'")
    root = project_root.resolve(strict=True)
    try:
        source = (root / requested).resolve(strict=True)
    except OSError as exc:
        raise SourceAuthorityError(f"bound source cannot be resolved: {relative_path}") from exc
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise SourceAuthorityError("source.relative_path escapes the project root") from exc
    if not source.is_file():
        raise SourceAuthorityError(f"bound source is not a file: {source}")
    return source


def validate_authority(
    authority_path: Path,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    document = _load_object(authority_path, "source authority")
    schema = _load_object(schema_path, "source authority schema")
    _validate_schema(document, schema)

    source_record = document["source"]
    source_path = _resolve_source(project_root, str(source_record["relative_path"]))
    actual_hash = _sha256_file(source_path)
    if actual_hash != str(source_record["sha256"]).upper():
        raise SourceAuthorityError("source SHA-256 does not match the authority binding")

    with Image.open(source_path) as image:
        actual_size = image.size
        actual_mode = image.mode
    expected_size = (int(source_record["width_px"]), int(source_record["height_px"]))
    if actual_size != expected_size:
        raise SourceAuthorityError(
            f"source dimensions do not match: expected {expected_size}, got {actual_size}"
        )
    if actual_mode != str(source_record["pixel_format"]):
        raise SourceAuthorityError(
            f"source pixel format does not match: expected {source_record['pixel_format']}, "
            f"got {actual_mode}"
        )

    items = document["items"]
    item_ids = [str(item["authority_item_id"]) for item in items]
    duplicate_ids = sorted({item_id for item_id in item_ids if item_ids.count(item_id) > 1})
    if duplicate_ids:
        raise SourceAuthorityError(f"duplicate authority_item_id values: {duplicate_ids}")

    subject_ids = {
        str(item["subject_id"]) for item in items if str(item["kind"]) != "RELATION"
    }
    canvas_w, canvas_h = expected_size
    for item in items:
        bbox = item["bbox_source"]
        if bbox is not None and (
            float(bbox["x"]) + float(bbox["w"]) > canvas_w
            or float(bbox["y"]) + float(bbox["h"]) > canvas_h
        ):
            raise SourceAuthorityError(
                f"{item['authority_item_id']} bbox_source exceeds the source canvas"
            )
        if item["kind"] == "FORMULA" and item["canonical_latex"] is not None:
            latex = str(item["canonical_latex"])
            actual_latex_hash = hashlib.sha256(latex.encode("utf-8")).hexdigest()
            if actual_latex_hash.lower() != str(item["latex_sha256"]).lower():
                raise SourceAuthorityError(
                    f"{item['authority_item_id']} canonical LaTeX hash is inconsistent"
                )
        if item["kind"] == "RELATION":
            relation = item["relation"]
            missing = sorted(
                {
                    str(relation["from_subject_id"]),
                    str(relation["to_subject_id"]),
                }
                - subject_ids
            )
            if missing:
                raise SourceAuthorityError(
                    f"{item['authority_item_id']} relation references unknown subjects: {missing}"
                )

    return {
        "document_type": "SOURCE_AUTHORITY_VALIDATION",
        "schema_version": "1.0.0",
        "status": "PASS",
        "authority_id": document["authority_id"],
        "authority_status": document["status"],
        "authority_path": str(authority_path.resolve(strict=True)),
        "authority_sha256": _sha256_file(authority_path.resolve(strict=True)),
        "source_path": str(source_path),
        "source_sha256": actual_hash,
        "item_count": len(items),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", required=True, type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = validate_authority(
            args.authority,
            schema_path=args.schema,
            project_root=args.project_root,
        )
    except SourceAuthorityError as exc:
        print(f"SOURCE_AUTHORITY_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
