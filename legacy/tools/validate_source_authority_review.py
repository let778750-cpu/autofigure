#!/usr/bin/env python3
"""Read back and validate a source-authority human-review package."""

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


TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from validate_source_authority import SourceAuthorityError, validate_authority
except ModuleNotFoundError:  # Support: python -m tools.validate_source_authority_review
    from .validate_source_authority import SourceAuthorityError, validate_authority


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTHORITY_SCHEMA = PROJECT_ROOT / "schemas" / "source-authority.schema.json"
DEFAULT_REVIEW_SCHEMA = PROJECT_ROOT / "schemas" / "source-authority-review.schema.json"
RENDERER_PATH = TOOLS_DIRECTORY / "render_source_authority_review.py"


class ReviewValidationError(RuntimeError):
    """Raised when a persisted review package is stale, modified, or misplaced."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewValidationError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewValidationError(f"{label} must be a JSON object")
    return value


def _validate_schema(document: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in first.absolute_path
        )
        raise ReviewValidationError(
            f"review schema rejected manifest at {location}: {first.message}"
        )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_recorded_path(raw: str, *, project_root: Path, label: str) -> Path:
    requested = Path(raw)
    if not requested.is_absolute() and ".." in requested.parts:
        raise ReviewValidationError(f"{label} contains '..'")
    candidate = requested if requested.is_absolute() else project_root / requested
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ReviewValidationError(f"cannot resolve {label}: {raw}") from exc
    return resolved


def _review_value(item: Mapping[str, Any]) -> str | None:
    kind = str(item["kind"])
    if kind == "TEXT":
        value = item["text"]
    elif kind == "FORMULA":
        value = item["canonical_latex"]
    elif kind in {"SEMANTIC_REGION", "MANUAL_ASSET"}:
        value = item["label"]
    else:
        relation = item["relation"]
        value = (
            f"{relation['from_subject_id']} -> {relation['to_subject_id']} "
            f"({relation['direction']}/{relation['meaning']})"
        )
    return str(value) if value is not None else None


def _expected_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "authority_item_id": item["authority_item_id"],
            "subject_id": item["subject_id"],
            "kind": item["kind"],
            "disposition": item["disposition"],
            "criticality": item["criticality"],
            "bbox_source": item["bbox_source"],
            "review_value": _review_value(item),
            "notes": item["notes"],
        }
        for item in items
    ]


def _expected_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "confirmed": sum(item["disposition"] == "CONFIRMED" for item in items),
        "inconclusive": sum(item["disposition"] == "INCONCLUSIVE" for item in items),
        "manual_assets": sum(item["kind"] == "MANUAL_ASSET" for item in items),
        "relations": sum(item["kind"] == "RELATION" for item in items),
    }


def validate_review_package(
    manifest_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    authority_schema_path: Path = DEFAULT_AUTHORITY_SCHEMA,
    review_schema_path: Path = DEFAULT_REVIEW_SCHEMA,
) -> dict[str, Any]:
    project_root = project_root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    manifest = _load_object(manifest_path, label="review manifest")
    review_schema_path = review_schema_path.resolve(strict=True)
    review_schema = _load_object(review_schema_path, label="review schema")
    _validate_schema(manifest, review_schema)

    if _is_within(manifest_path, project_root):
        expected_manifest = (
            project_root
            / "examples"
            / "generated"
            / "runs"
            / str(manifest["run_id"])
            / "authority-review"
            / "review-manifest.json"
        ).resolve(strict=False)
        if manifest_path != expected_manifest:
            raise ReviewValidationError("project-local review manifest is not run-bound")

    authority_path = _resolve_recorded_path(
        str(manifest["authority"]["path"]),
        project_root=project_root,
        label="authority path",
    )
    authority_validation = validate_authority(
        authority_path,
        schema_path=authority_schema_path,
        project_root=project_root,
    )
    authority = _load_object(authority_path, label="source authority")
    authority_record = manifest["authority"]
    expected_authority_record = {
        "path": authority_record["path"],
        "sha256": authority_validation["authority_sha256"],
        "authority_id": authority["authority_id"],
        "status": authority["status"],
        "item_count": len(authority["items"]),
    }
    if authority_record != expected_authority_record:
        raise ReviewValidationError("authority record is stale or inconsistent")

    source_path = _resolve_recorded_path(
        str(manifest["source"]["path"]),
        project_root=project_root,
        label="source path",
    )
    if source_path != Path(authority_validation["source_path"]).resolve(strict=True):
        raise ReviewValidationError("review source path differs from the authority binding")
    with Image.open(source_path) as source:
        source_size = source.size
        source_mode = source.mode
    expected_source_record = {
        "path": manifest["source"]["path"],
        "sha256": authority_validation["source_sha256"],
        "width_px": source_size[0],
        "height_px": source_size[1],
        "pixel_format": source_mode,
    }
    if manifest["source"] != expected_source_record:
        raise ReviewValidationError("source record is stale or inconsistent")

    renderer = manifest["renderer"]
    if renderer["script_sha256"] != _sha256_file(RENDERER_PATH):
        raise ReviewValidationError("review renderer hash is stale")
    if renderer["schema_sha256"] != _sha256_file(review_schema_path):
        raise ReviewValidationError("review schema hash is stale")
    if manifest["counts"] != _expected_counts(authority["items"]):
        raise ReviewValidationError("review counts do not match the authority")
    if manifest["items"] != _expected_items(authority["items"]):
        raise ReviewValidationError("review index does not exactly project the authority")

    package_dir = manifest_path.parent
    expected_names = {"authority-overlay.png", "review-manifest.json"}
    actual_names = {path.name for path in package_dir.iterdir()}
    if actual_names != expected_names:
        raise ReviewValidationError(
            f"review package directory is not clean: expected {sorted(expected_names)}, "
            f"got {sorted(actual_names)}"
        )
    overlay_record = manifest["outputs"]["overlay"]
    overlay_path = (package_dir / str(overlay_record["file"])).resolve(strict=True)
    if overlay_path.parent != package_dir:
        raise ReviewValidationError("overlay path escapes the review package")
    if _sha256_file(overlay_path) != str(overlay_record["sha256"]).upper():
        raise ReviewValidationError("overlay SHA-256 does not match the manifest")
    with Image.open(overlay_path) as overlay:
        overlay_size = overlay.size
        overlay_mode = overlay.mode
    if overlay_size != (
        int(overlay_record["width_px"]),
        int(overlay_record["height_px"]),
    ):
        raise ReviewValidationError("overlay dimensions do not match the manifest")
    if overlay_mode != overlay_record["pixel_format"]:
        raise ReviewValidationError("overlay pixel format does not match the manifest")

    return {
        "document_type": "SOURCE_AUTHORITY_REVIEW_VALIDATION",
        "schema_version": "1.0.0",
        "status": "PASS",
        "run_id": manifest["run_id"],
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "authority_sha256": authority_validation["authority_sha256"],
        "source_sha256": authority_validation["source_sha256"],
        "overlay_sha256": _sha256_file(overlay_path),
        "item_count": len(authority["items"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--authority-schema", type=Path, default=DEFAULT_AUTHORITY_SCHEMA)
    parser.add_argument("--review-schema", type=Path, default=DEFAULT_REVIEW_SCHEMA)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = validate_review_package(
            args.manifest,
            project_root=args.project_root,
            authority_schema_path=args.authority_schema,
            review_schema_path=args.review_schema,
        )
    except (ReviewValidationError, SourceAuthorityError, OSError) as exc:
        print(f"SOURCE_AUTHORITY_REVIEW_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
