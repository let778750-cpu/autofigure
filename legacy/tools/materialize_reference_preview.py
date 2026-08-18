#!/usr/bin/env python3
"""Create an exact, hash-bound target crop for a temporary PPT preview slot.

The crop is candidate-only presentation material.  It never receives native
editability credit, is excluded from similarity scoring, and must be replaced
before approval.  Subjective decisions (whether the region is truly
non-native and free of reconstructable text/formulas/topology) stay in the
Figure Spec slot contract; this tool only proves pixel provenance.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.materialize_reference_preview
    from .output_policy import resolve_output_path


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "reference-preview-asset.schema.json"


class ReferencePreviewError(ValueError):
    """Raised before a non-compliant preview asset can be published."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_schema() -> dict[str, Any]:
    try:
        value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferencePreviewError(f"Cannot load reference-preview schema: {exc}") from exc
    if not isinstance(value, dict):
        raise ReferencePreviewError("reference-preview schema root must be an object")
    return value


def _validate_receipt(receipt: dict[str, Any]) -> None:
    validator = Draft202012Validator(_load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(receipt), key=lambda item: list(item.absolute_path))
    if errors:
        details = "; ".join(
            f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
            for error in errors
        )
        raise ReferencePreviewError(f"generated receipt violates schema: {details}")


def _exclusive_publish(temporary: Path, destination: Path) -> None:
    if destination.exists():
        raise ReferencePreviewError(f"refusing to overwrite existing output: {destination}")
    os.replace(temporary, destination)


def materialize_reference_preview(
    source_path: str | Path,
    expected_source_sha256: str,
    bbox: Sequence[int],
    asset_path: str | Path,
    receipt_path: str | Path,
    *,
    source_user_confirmed: bool,
) -> dict[str, Any]:
    """Crop exact source pixels and publish a schema-valid provenance receipt."""
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise ReferencePreviewError(f"source PNG does not exist: {source}")
    if not source_user_confirmed:
        raise ReferencePreviewError("reference-preview materialization requires user-confirmed source selection")
    expected = str(expected_source_sha256).strip().upper()
    if len(expected) != 64 or any(character not in "0123456789ABCDEF" for character in expected):
        raise ReferencePreviewError("expected source SHA-256 must be 64 hexadecimal characters")
    actual_source_sha = sha256_file(source)
    if actual_source_sha != expected:
        raise ReferencePreviewError(
            f"source SHA-256 mismatch: expected {expected}, observed {actual_source_sha}"
        )
    if len(bbox) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in bbox):
        raise ReferencePreviewError("bbox must contain exactly four integers: x y w h")
    x, y, width, height = (int(value) for value in bbox)
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ReferencePreviewError("bbox must have non-negative origin and positive size")

    asset = resolve_output_path(asset_path)
    receipt = resolve_output_path(receipt_path)
    if asset.suffix.lower() != ".png":
        raise ReferencePreviewError("reference-preview asset must use a .png suffix")
    if receipt.suffix.lower() != ".json":
        raise ReferencePreviewError("reference-preview receipt must use a .json suffix")
    if asset == receipt:
        raise ReferencePreviewError("asset and receipt paths must be distinct")
    if asset.exists() or receipt.exists():
        existing = asset if asset.exists() else receipt
        raise ReferencePreviewError(f"refusing to overwrite existing output: {existing}")
    asset.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)

    temporary_asset: Path | None = None
    temporary_receipt: Path | None = None
    try:
        with Image.open(source) as image:
            if image.format != "PNG":
                raise ReferencePreviewError(
                    f"reference-preview source must be PNG, got {image.format or 'unknown'}"
                )
            source_width, source_height = image.size
            pixel_format = image.mode
            if x + width > source_width or y + height > source_height:
                raise ReferencePreviewError(
                    f"bbox {(x, y, width, height)} escapes source canvas {source_width}x{source_height}"
                )
            crop = image.crop((x, y, x + width, y + height))
            with tempfile.NamedTemporaryFile(
                prefix=f".{asset.stem}.", suffix=".tmp.png", dir=asset.parent, delete=False
            ) as handle:
                temporary_asset = Path(handle.name)
            crop.save(temporary_asset, format="PNG", optimize=False)

        with Image.open(temporary_asset) as rendered:
            if rendered.size != (width, height):
                raise ReferencePreviewError(
                    f"published crop dimensions changed: expected {width}x{height}, got {rendered.size}"
                )
            output_mode = rendered.mode

        receipt_document = {
            "schema_version": "1.0.0",
            "document_type": "REFERENCE_PREVIEW_ASSET",
            "status": "PREVIEW_ONLY_REPLACE_BEFORE_APPROVAL",
            "created_at_utc": utc_now(),
            "source": {
                "path": str(source),
                "sha256": actual_source_sha,
                "width_px": source_width,
                "height_px": source_height,
                "pixel_format": pixel_format,
                "user_confirmed": True,
            },
            "crop": {
                "bbox_source": {"x": x, "y": y, "w": width, "h": height},
                "padding_px": 0,
                "resampling": "NONE_EXACT_PIXELS",
                "lossless": True,
            },
            "asset": {
                "path": str(asset),
                "sha256": sha256_file(temporary_asset),
                "size_bytes": temporary_asset.stat().st_size,
                "width_px": width,
                "height_px": height,
                "pixel_format": output_mode,
                "media_type": "image/png",
            },
            "policy": {
                "derived_from_current_reference": True,
                "presentation_use": "PREVIEW_ONLY",
                "visible_disclosure_required": True,
                "qa_similarity_masked": True,
                "native_coverage_credit": False,
                "replace_before_approval": True,
            },
        }
        _validate_receipt(receipt_document)
        with tempfile.NamedTemporaryFile(
            prefix=f".{receipt.stem}.", suffix=".tmp.json", dir=receipt.parent, delete=False
        ) as handle:
            temporary_receipt = Path(handle.name)
            handle.write(json.dumps(receipt_document, ensure_ascii=False, indent=2).encode("utf-8"))
            handle.write(b"\n")

        _exclusive_publish(temporary_asset, asset)
        temporary_asset = None
        _exclusive_publish(temporary_receipt, receipt)
        temporary_receipt = None
        return receipt_document
    finally:
        for temporary in (temporary_asset, temporary_receipt):
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--bbox", required=True, nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--asset", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--source-user-confirmed", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = materialize_reference_preview(
            args.source,
            args.expected_source_sha256,
            args.bbox,
            args.asset,
            args.receipt,
            source_user_confirmed=args.source_user_confirmed,
        )
    except (OSError, ReferencePreviewError) as exc:
        print(f"REFERENCE_PREVIEW_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "asset": result["asset"]["path"],
                "asset_sha256": result["asset"]["sha256"],
                "bbox_source": result["crop"]["bbox_source"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
