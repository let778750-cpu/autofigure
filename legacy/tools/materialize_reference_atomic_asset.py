"""Extract one deterministic source-bound atomic raster for final PPT use."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:
    from .output_policy import resolve_output_path


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "reference-atomic-asset.schema.json"


class ReferenceAtomicAssetError(ValueError):
    """Raised before an unsafe or non-atomic source crop can be published."""


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
        raise ReferenceAtomicAssetError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReferenceAtomicAssetError(f"{label} must be one JSON object")
    return value


def _bbox(record: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    raw = record.get("bbox_source") or record.get("bbox")
    if isinstance(raw, Mapping) and all(key in raw for key in ("x", "y", "w", "h")):
        return tuple(float(raw[key]) for key in ("x", "y", "w", "h"))
    polygon = record.get("polygon_source") or record.get("polygon")
    if isinstance(polygon, list) and polygon:
        points = [point for point in polygon if isinstance(point, Sequence) and len(point) >= 2]
        if points:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    return None


def _intersection_fraction(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    x0, y0 = max(lx, rx), max(ly, ry)
    x1, y1 = min(lx + lw, rx + rw), min(ly + lh, ry + rh)
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return area / max(1.0, rw * rh)


def _ocr_overlaps(manifest: Mapping[str, Any], crop_bbox: tuple[int, int, int, int]) -> list[str]:
    overlaps: list[str] = []
    for candidate in manifest.get("text_candidates", []):
        if not isinstance(candidate, Mapping):
            continue
        candidate_bbox = _bbox(candidate)
        if candidate_bbox is None:
            continue
        if _intersection_fraction(tuple(float(value) for value in crop_bbox), candidate_bbox) >= 0.1:
            overlaps.append(str(candidate.get("candidate_id", "UNKNOWN")))
    return sorted(set(overlaps))


def _foreground_mask(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    background = np.median(border.astype(np.float32), axis=0)
    delta = np.max(np.abs(rgb.astype(np.float32) - background), axis=2)
    mask = (delta >= 18.0).astype(np.uint8) * 255
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    if min(height, width) >= 12:
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def _component_metrics(mask: np.ndarray) -> tuple[int, float, float, np.ndarray | None]:
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    minimum = max(4, int(binary.size * 0.001))
    components = [index for index in range(1, count) if int(stats[index, cv2.CC_STAT_AREA]) >= minimum]
    total = sum(int(stats[index, cv2.CC_STAT_AREA]) for index in components)
    if not components or total == 0:
        border_fraction = 0.0
        return 0, 0.0, border_fraction, None
    dominant = max(components, key=lambda index: int(stats[index, cv2.CC_STAT_AREA]))
    dominant_area = int(stats[dominant, cv2.CC_STAT_AREA])
    border_values = np.concatenate((binary[0], binary[-1], binary[:, 0], binary[:, -1]))
    border_fraction = float(np.mean(border_values)) if border_values.size else 0.0
    dominant_mask = (labels == dominant).astype(np.uint8) * 255
    dominant_mask = cv2.dilate(dominant_mask, np.ones((3, 3), dtype=np.uint8), iterations=1)
    return len(components), dominant_area / total, border_fraction, dominant_mask


def _validate_receipt(receipt: Mapping[str, Any]) -> None:
    schema = _load_json(SCHEMA_PATH, "reference atomic asset schema")
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(receipt),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        raise ReferenceAtomicAssetError(
            "generated receipt violates schema at /"
            + "/".join(str(part) for part in first.absolute_path)
            + f": {first.message}"
        )


def materialize_reference_atomic_asset(
    source_path: Path,
    expected_source_sha256: str,
    bbox: Sequence[int],
    asset_path: Path,
    receipt_path: Path,
    *,
    role: str,
    semantic_object_count: int,
    rights_basis: str,
    perception_manifest_path: Path | None = None,
    perception_review_receipt_path: Path | None = None,
    source_user_confirmed: bool,
    rotation_deg: float = 0.0,
) -> dict[str, Any]:
    source = source_path.resolve(strict=True)
    if not source_user_confirmed:
        raise ReferenceAtomicAssetError("atomic extraction requires a user-confirmed designated reference")
    if semantic_object_count != 1:
        raise ReferenceAtomicAssetError("atomic asset must contain exactly one reviewed semantic object")
    if role not in {"photo", "texture", "complex_icon", "style_arrow"}:
        raise ReferenceAtomicAssetError(f"unsupported atomic asset role: {role}")
    if not rights_basis.strip():
        raise ReferenceAtomicAssetError("rights_basis must be nonblank")
    actual_source_sha = _sha256(source)
    if actual_source_sha.casefold() != expected_source_sha256.casefold():
        raise ReferenceAtomicAssetError(
            f"source SHA-256 mismatch: expected {expected_source_sha256}, got {actual_source_sha}"
        )
    if len(bbox) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in bbox):
        raise ReferenceAtomicAssetError("bbox must contain four integers: x y w h")
    x, y, width, height = (int(value) for value in bbox)
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ReferenceAtomicAssetError("bbox must have a non-negative origin and positive size")
    asset = resolve_output_path(asset_path)
    receipt = resolve_output_path(receipt_path)
    if asset.suffix.lower() != ".png" or receipt.suffix.lower() != ".json":
        raise ReferenceAtomicAssetError("asset must be PNG and receipt must be JSON")
    if asset.exists() or receipt.exists():
        raise ReferenceAtomicAssetError("refusing to overwrite an existing asset or receipt")
    manifest: dict[str, Any] | None = None
    manifest_sha: str | None = None
    resolved_manifest: Path | None = None
    resolved_review: Path | None = None
    review_sha: str | None = None
    not_text_ids: set[str] = set()
    if perception_manifest_path is not None:
        resolved_manifest = perception_manifest_path.resolve(strict=True)
        manifest = _load_json(resolved_manifest, "perception manifest")
        manifest_sha = _sha256(resolved_manifest)
        source_record = manifest.get("source")
        if not isinstance(source_record, Mapping) or str(source_record.get("sha256", "")).casefold() != actual_source_sha.casefold():
            raise ReferenceAtomicAssetError("perception manifest is not bound to the designated reference")
    if perception_review_receipt_path is not None:
        if resolved_manifest is None or manifest_sha is None:
            raise ReferenceAtomicAssetError(
                "perception review receipt requires the exact perception manifest"
            )
        resolved_review = perception_review_receipt_path.resolve(strict=True)
        review = _load_json(resolved_review, "perception review receipt")
        review_sha = _sha256(resolved_review)
        if (
            review.get("document_type") != "PERCEPTION_REVIEW_RECEIPT"
            or review.get("status") != "PERCEPTION_REVIEW_PASS"
        ):
            raise ReferenceAtomicAssetError("perception review receipt is not a passing receipt")
        raw_binding = review.get("raw_manifest")
        if not isinstance(raw_binding, Mapping):
            raise ReferenceAtomicAssetError("perception review receipt lacks a raw manifest binding")
        if str(raw_binding.get("manifest_sha256", "")).casefold() != manifest_sha.casefold():
            raise ReferenceAtomicAssetError("perception review receipt is stale for this manifest")
        if str(raw_binding.get("source_sha256", "")).casefold() != actual_source_sha.casefold():
            raise ReferenceAtomicAssetError("perception review receipt source is stale")
        not_text_ids = {
            str(decision.get("candidate_id"))
            for decision in review.get("decisions", [])
            if isinstance(decision, Mapping) and decision.get("status") == "NOT_TEXT"
        }
    overlaps = _ocr_overlaps(manifest or {}, (x, y, width, height))
    blocked_overlaps = sorted(set(overlaps) - not_text_ids)
    reviewed_not_text = sorted(set(overlaps) & not_text_ids)
    if blocked_overlaps:
        raise ReferenceAtomicAssetError(
            "atomic candidate overlaps reconstructable OCR candidates: "
            + ", ".join(blocked_overlaps)
        )
    asset.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary_asset: Path | None = None
    temporary_mask: Path | None = None
    temporary_receipt: Path | None = None
    committed = False
    mask_path: Path | None = None
    with Image.open(source) as image:
        if image.format != "PNG":
            raise ReferenceAtomicAssetError(f"source must be PNG, got {image.format or 'unknown'}")
        source_width, source_height = image.size
        source_mode = image.mode
        if x + width > source_width or y + height > source_height:
            raise ReferenceAtomicAssetError("bbox escapes the source canvas")
        crop = image.convert("RGBA").crop((x, y, x + width, y + height))
        rgb = np.asarray(crop, dtype=np.uint8)[:, :, :3]
    mask = _foreground_mask(rgb)
    component_count, dominant_fraction, border_fraction, dominant_mask = _component_metrics(mask)
    if role in {"photo", "texture"}:
        classification = "ISOLATED"
    elif component_count and dominant_fraction >= 0.85:
        classification = "ISOLATED"
    elif dominant_mask is not None and dominant_fraction >= 0.65:
        classification = "SEPARABLE"
    else:
        raise ReferenceAtomicAssetError(
            "candidate is ENTANGLED: deterministic foreground does not isolate one dominant object"
        )
    output_rgba = np.asarray(crop, dtype=np.uint8).copy()
    if classification == "SEPARABLE":
        output_rgba[:, :, 3] = dominant_mask
        mask_path = asset.with_name(f"{asset.stem}.mask.png")
        if mask_path.exists():
            raise ReferenceAtomicAssetError(f"refusing to overwrite existing mask: {mask_path}")
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{asset.stem}.", suffix=".tmp.png", dir=asset.parent, delete=False) as handle:
            temporary_asset = Path(handle.name)
        Image.fromarray(output_rgba, mode="RGBA").save(temporary_asset, format="PNG", optimize=False)
        if mask_path is not None:
            with tempfile.NamedTemporaryFile(prefix=f".{mask_path.stem}.", suffix=".tmp.png", dir=mask_path.parent, delete=False) as handle:
                temporary_mask = Path(handle.name)
            Image.fromarray(dominant_mask, mode="L").save(temporary_mask, format="PNG", optimize=False)
        with Image.open(temporary_asset) as check:
            if check.size != (width, height):
                raise ReferenceAtomicAssetError("decoded asset dimensions differ from the source crop")
            decoded = np.asarray(check.convert("RGBA"), dtype=np.uint8)
        opaque = decoded[:, :, 3] > 0
        mae = float(np.mean(np.abs(decoded[:, :, :3][opaque].astype(np.int16) - output_rgba[:, :, :3][opaque].astype(np.int16)))) if np.any(opaque) else 0.0
        if mae != 0.0:
            raise ReferenceAtomicAssetError("asset RGB changed on opaque pixels")
        os.replace(temporary_asset, asset)
        temporary_asset = None
        if mask_path is not None and temporary_mask is not None:
            os.replace(temporary_mask, mask_path)
            temporary_mask = None
        document = {
            "schema_version": "1.0.0",
            "document_type": "REFERENCE_ATOMIC_ASSET_RECEIPT",
            "status": "MECHANICAL_PASS_REQUIRES_INDEPENDENT_REVIEW",
            "created_at_utc": _now(),
            "source": {"path": str(source), "sha256": actual_source_sha, "width_px": source_width, "height_px": source_height, "pixel_format": source_mode, "user_confirmed": True},
            "candidate": {
                "role": role,
                "bbox_source": {"x": x, "y": y, "w": width, "h": height},
                "semantic_object_count": 1,
                "perception_manifest_path": str(resolved_manifest) if resolved_manifest else None,
                "perception_manifest_sha256": manifest_sha,
                "perception_review_receipt_path": str(resolved_review) if resolved_review else None,
                "perception_review_receipt_sha256": review_sha,
            },
            "classification": {
                "result": classification,
                "algorithm": "deterministic-border-background-v1",
                "foreground_component_count": component_count,
                "dominant_component_fraction": round(dominant_fraction, 8),
                "border_foreground_fraction": round(border_fraction, 8),
                "ocr_overlap_candidate_ids": overlaps,
                "reviewed_not_text_candidate_ids": reviewed_not_text,
            },
            "asset": {"path": str(asset), "sha256": _sha256(asset), "size_bytes": asset.stat().st_size, "width_px": width, "height_px": height, "pixel_format": "RGBA", "media_type": "image/png", "mask_path": str(mask_path) if mask_path else None, "mask_sha256": _sha256(mask_path) if mask_path else None},
            "transform": {"preserve_aspect_ratio": True, "resampled_file": False, "rotation_deg": float(rotation_deg), "translate_x_px": 0.0, "translate_y_px": 0.0},
            "qa": {"opaque_pixel_rgb_mae": 0.0, "decoded_size_matches_crop": True, "crop_loss_detected": False, "edge_discontinuity_requires_review": border_fraction > 0.15},
            "policy": {"derived_from_current_reference": True, "presentation_use": "FINAL_ATOMIC_ASSET", "visible_disclosure_required": False, "qa_similarity_masked": False, "hybrid_editability_credit": True, "replace_before_approval": False, "generative_processing": False},
            "rights_basis": rights_basis.strip(),
        }
        _validate_receipt(document)
        with tempfile.NamedTemporaryFile(
            prefix=f".{receipt.stem}.", suffix=".tmp.json", dir=receipt.parent, delete=False
        ) as handle:
            temporary_receipt = Path(handle.name)
        temporary_receipt.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary_receipt, receipt)
        temporary_receipt = None
        committed = True
        return document
    finally:
        for temporary in (temporary_asset, temporary_mask, temporary_receipt):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if not committed:
            asset.unlink(missing_ok=True)
            if mask_path is not None:
                mask_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--bbox", required=True, nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--asset", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=["photo", "texture", "complex_icon", "style_arrow"])
    parser.add_argument("--semantic-object-count", required=True, type=int)
    parser.add_argument("--rights-basis", required=True)
    parser.add_argument("--perception-manifest", type=Path)
    parser.add_argument("--perception-review-receipt", type=Path)
    parser.add_argument("--source-user-confirmed", action="store_true")
    parser.add_argument("--rotation-deg", type=float, default=0.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = materialize_reference_atomic_asset(
            args.source,
            args.expected_source_sha256,
            args.bbox,
            args.asset,
            args.receipt,
            role=args.role,
            semantic_object_count=args.semantic_object_count,
            rights_basis=args.rights_basis,
            perception_manifest_path=args.perception_manifest,
            perception_review_receipt_path=args.perception_review_receipt,
            source_user_confirmed=args.source_user_confirmed,
            rotation_deg=args.rotation_deg,
        )
    except (OSError, ReferenceAtomicAssetError) as exc:
        print(f"REFERENCE_ATOMIC_ASSET_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "classification": result["classification"]["result"], "asset": result["asset"]["path"], "asset_sha256": result["asset"]["sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
