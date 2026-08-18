#!/usr/bin/env python3
"""Deterministic Phase-1 geometry observations for OCR-guided PNG reconstruction.

This stage measures pixels; it never promotes OCR text, layout semantics, true
font baselines, panels, or connectors.  Every box uses half-open source-pixel
coordinates ``[x0, y0, x1, y1)``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    # ``python -I tools/geometry_refinement.py`` intentionally omits the script
    # directory.  Trust only this resolved, project-owned tools directory.
    sys.path.insert(0, str(TOOLS_DIR))

import cv2  # noqa: E402
import jsonschema  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from scipy.ndimage import distance_transform_edt  # noqa: E402
from skimage.filters import threshold_sauvola  # noqa: E402

from output_policy import OutputPolicyError, resolve_output_path  # noqa: E402


SCHEMA_VERSION = "1.0.0"
ALGORITHM_ID = "ocr_guided_phase1_geometry_refinement"
ALGORITHM_VERSION = "1.0.0"
RANDOM_SEED = 24638
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "geometry-manifest.schema.json"
OCR_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
HOST_RECEIPT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "host-runtime-receipt.schema.json"

EXIT_OK = 0
EXIT_CONTRACT_REJECTED = 2
EXIT_INCONCLUSIVE = 3
EXIT_INTERNAL_ERROR = 4

# Versioned constants, deliberately not user-tunable until calibrated against a
# gold corpus.  The complete set is copied into every manifest.
PARAMETERS: dict[str, int | float] = {
    "bbox_padding_px": 4,
    "sauvola_window_px": 15,
    "sauvola_k": 0.20,
    "lab_min_delta": 7.0,
    "lab_background_mad_multiplier": 4.0,
    "foreground_vote_min": 2,
    "minimum_ink_area_px": 2,
    "maximum_foreground_fraction": 0.78,
    "minimum_pairwise_mask_iou": 0.30,
    "line_contamination_span_fraction": 0.78,
    "line_contamination_max_thickness_px": 3,
    "vertical_text_aspect_ratio": 1.35,
    "multiline_gap_fraction": 0.16,
    "minimum_baseline_components": 3,
    "baseline_minimum_ocr_confidence": 0.70,
    "baseline_max_spread_px": 2.0,
    "baseline_max_spread_height_fraction": 0.15,
    "baseline_max_angle_degrees": 3.0,
    "baseline_minimum_inlier_fraction": 0.75,
    "baseline_maximum_rmse_px": 1.5,
    "minimum_edge_uncertainty_px": 1.0,
    "same_alignment_tolerance_px": 2.0,
    "local_pair_max_gap_height_multiplier": 4.0,
    "local_pair_absolute_max_gap_px": 160,
    "canny_low_threshold": 40,
    "canny_high_threshold": 120,
    "text_mask_dilation_px": 2,
    "frame_minimum_width_px": 16,
    "frame_minimum_height_px": 10,
    "frame_side_support_fraction": 0.30,
    "frame_minimum_supported_sides": 3,
    "frame_minimum_lsd_sides": 2,
    "frame_deduplication_iou": 0.82,
    "frame_border_exclusion_px": 1,
    "frame_maximum_interior_foreground_fraction": 0.35,
    "frame_maximum_stroke_to_min_dimension_ratio": 0.20,
    "frame_maximum_aspect_ratio": 8.0,
    "grid_cell_size_relative_tolerance": 0.20,
    "grid_cell_adjacency_tolerance_px": 3.0,
    "grid_cell_minimum_cluster_size": 4,
    "frame_nested_contour_minimum_iou": 0.65,
    "frame_nested_contour_center_tolerance_px": 2.5,
    "frame_nested_contour_maximum_inset_px": 4.0,
}

FORMULA_PATTERN = re.compile(r"(?:\\[A-Za-z]+|[=∑∫√≈≠≤≥±×÷^_{}])")
ID_PATTERN = re.compile(r"^T[0-9]{4,}$")


class GeometryContractError(ValueError):
    """Raised before outputs are committed when a binding is not trustworthy."""


@dataclass
class CandidateMask:
    candidate_id: str
    detector_bbox: tuple[int, int, int, int]
    roi_bbox: tuple[int, int, int, int]
    ink_bbox: tuple[int, int, int, int] | None
    mask: np.ndarray | None
    diagnostic_mask: np.ndarray | None
    label: int | None
    edge_uncertainty: dict[str, float] | None
    baseline_y: float | None
    baseline_uncertainty: float | None


def _reject_constant(value: str) -> None:
    raise GeometryContractError(f"non-finite JSON constant is forbidden: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise GeometryContractError(f"non-finite JSON number is forbidden: {value}")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GeometryContractError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except Exception as exc:
        raise GeometryContractError(f"unable to read {label}: {path}: {exc}") from exc
    return parse_json_bytes(payload, label=label)


def parse_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        raw = payload.decode("utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except GeometryContractError:
        raise
    except Exception as exc:
        raise GeometryContractError(f"unable to parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise GeometryContractError(f"{label} root must be an object")
    return value


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _file_binding(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    record: dict[str, Any] = {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }
    if relative_to is not None:
        record["relative_path"] = resolved.relative_to(relative_to.resolve()).as_posix()
    return record


def _payload_binding(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve(strict=True)),
        "size_bytes": len(payload),
        "sha256": sha256_bytes(payload),
    }


def _validate_json_schema(value: Mapping[str, Any], schema_path: Path, *, label: str) -> None:
    schema = load_json(schema_path, label=f"{label} schema")
    _validate_json_value(value, schema, label=label)


def _validate_json_value(
    value: Mapping[str, Any], schema: Mapping[str, Any], *, label: str
) -> None:
    try:
        jsonschema.Draft202012Validator(
            schema,
            format_checker=jsonschema.FormatChecker(),
        ).validate(value)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path) or "<root>"
        raise GeometryContractError(f"{label} schema rejected {location}: {exc.message}") from exc


def verify_geometry_manifest_file(manifest_path: Path) -> dict[str, Any]:
    """Strictly parse and schema-validate one immutable geometry manifest.

    This is a read-only interface for the PowerShell gate.  It deliberately
    uses the project-owned canonical schema and rejects duplicate JSON keys,
    NaN/Infinity constants, non-object roots, schema violations, and either
    file changing while validation is in progress.
    """

    manifest = _require_file(manifest_path, label="geometry manifest")
    schema = _require_file(SCHEMA_PATH, label="canonical geometry schema")
    manifest_bytes = manifest.read_bytes()
    schema_bytes = schema.read_bytes()
    value = parse_json_bytes(manifest_bytes, label="geometry manifest")
    schema_value = parse_json_bytes(schema_bytes, label="geometry manifest schema")
    _validate_json_value(value, schema_value, label="geometry manifest")
    _require_unchanged(manifest, manifest_bytes, label="geometry manifest")
    _require_unchanged(schema, schema_bytes, label="geometry manifest schema")
    return value


def _require_file(path: str | os.PathLike[str], *, label: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise GeometryContractError(f"{label} is not a file: {resolved}")
    return resolved


def _verify_binding(record: Mapping[str, Any], *, label: str) -> Path:
    path = _require_file(str(record.get("path", "")), label=label)
    expected_size = record.get("size_bytes")
    expected_hash = str(record.get("sha256", "")).upper()
    if path.stat().st_size != expected_size or sha256_file(path) != expected_hash:
        raise GeometryContractError(f"{label} file binding is stale: {path}")
    return path


def _require_unchanged(path: Path, payload: bytes, *, label: str) -> None:
    try:
        current = path.read_bytes()
    except Exception as exc:
        raise GeometryContractError(f"{label} became unreadable during processing: {exc}") from exc
    if current != payload:
        raise GeometryContractError(f"{label} changed during geometry processing")


def _verify_current_record(path: Path, record: Mapping[str, Any], *, label: str) -> None:
    try:
        size = path.stat().st_size
        digest = sha256_file(path)
    except Exception as exc:
        raise GeometryContractError(f"{label} became unreadable before commit: {exc}") from exc
    if size != int(record["size_bytes"]) or digest != str(record["sha256"]).upper():
        raise GeometryContractError(f"{label} changed before geometry manifest commit")


def _validate_inputs(
    source_path: Path,
    ocr_path: Path,
    receipt_path: Path,
    *,
    source_bytes: bytes,
    ocr_bytes: bytes,
    receipt_bytes: bytes,
    require_isolated_runtime: bool,
) -> tuple[Image.Image, np.ndarray, dict[str, Any], dict[str, Any], dict[str, Any]]:
    ocr = parse_json_bytes(ocr_bytes, label="OCR manifest")
    receipt = parse_json_bytes(receipt_bytes, label="host runtime receipt")
    _validate_json_schema(ocr, OCR_SCHEMA_PATH, label="OCR manifest")
    _validate_json_schema(receipt, HOST_RECEIPT_SCHEMA_PATH, label="host runtime receipt")

    if receipt["status"] != "PASS" or any(not item["passed"] for item in receipt["checks"]):
        raise GeometryContractError("host runtime receipt is not an all-checks PASS")
    isolation = receipt["isolation"]
    if not all(
        isolation[key]
        for key in ("required", "isolated", "ignore_environment", "no_user_site", "safe_path")
    ):
        raise GeometryContractError("host runtime receipt does not prove isolated execution")

    canonical_bindings = {
        "runtime_config": PROJECT_ROOT / "host-runtime.json",
        "requirements": PROJECT_ROOT / "requirements.txt",
        "receipt_schema": HOST_RECEIPT_SCHEMA_PATH,
        "validator": TOOLS_DIR / "validate_host_runtime.py",
    }
    for key, binding in receipt["bindings"].items():
        bound_path = _verify_binding(binding, label=f"host receipt binding {key}")
        if key not in canonical_bindings or bound_path != canonical_bindings[key].resolve():
            raise GeometryContractError(f"host receipt {key} is not the canonical project binding")

    source_hash = sha256_bytes(source_bytes)
    try:
        image = Image.open(io.BytesIO(source_bytes))
        image.load()
    except Exception as exc:
        raise GeometryContractError(f"input image cannot be decoded: {exc}") from exc
    if image.format != "PNG":
        raise GeometryContractError(f"input must be a PNG, got {image.format!r}")
    source_mode = image.mode
    rgb_image = image.convert("RGB")
    rgb = np.asarray(rgb_image, dtype=np.uint8)
    height, width = rgb.shape[:2]

    ocr_source = ocr["source"]
    if str(ocr_source["sha256"]).upper() != source_hash:
        raise GeometryContractError("OCR manifest source SHA-256 does not match input PNG")
    if (int(ocr_source["width_px"]), int(ocr_source["height_px"])) != (width, height):
        raise GeometryContractError("OCR manifest source dimensions do not match input PNG")
    if int(ocr_source["size_bytes"]) != len(source_bytes):
        raise GeometryContractError("OCR manifest source size_bytes does not match input PNG")
    if str(ocr_source["pixel_mode"]) != source_mode:
        raise GeometryContractError("OCR manifest source pixel_mode does not match input PNG")
    if str(ocr_source["format"]).upper() != "PNG":
        raise GeometryContractError("OCR manifest source format is not PNG")

    current_ocr_schema_hash = sha256_file(OCR_SCHEMA_PATH)
    configured_schema_hash = str(ocr["configuration"]["manifest_schema_sha256"]).upper()
    if configured_schema_hash != current_ocr_schema_hash:
        raise GeometryContractError("OCR manifest is bound to a different perception schema")

    context = receipt["context"]
    expected_context = {"run_id": ocr["run_id"], "source_sha256": source_hash}
    actual_context = {
        "run_id": context["run_id"],
        "source_sha256": str(context["source_sha256"]).upper()
        if context["source_sha256"] is not None
        else None,
    }
    if actual_context != expected_context:
        raise GeometryContractError("host receipt context does not match OCR run/source")

    runtime = receipt["runtime"]
    actual_python = Path(sys.executable).resolve(strict=False)
    expected_python = Path(runtime["python_executable"]).resolve(strict=False)
    if actual_python != expected_python:
        raise GeometryContractError(
            f"current interpreter does not match host receipt: {actual_python} != {expected_python}"
        )
    if platform.python_version() != runtime["python_version"]:
        raise GeometryContractError("current Python version does not match host receipt")
    if Path(sys.prefix).resolve(strict=False) != Path(runtime["prefix"]).resolve(strict=False):
        raise GeometryContractError("current sys.prefix does not match host receipt")
    if require_isolated_runtime and not (
        sys.flags.isolated
        and sys.flags.ignore_environment
        and sys.flags.no_user_site
        and sys.flags.safe_path
    ):
        raise GeometryContractError("geometry CLI must run under Host Python isolated mode (-I)")

    source_record = {
        "path": str(source_path),
        "sha256": source_hash,
        "size_bytes": len(source_bytes),
        "width_px": width,
        "height_px": height,
        "pixel_mode": source_mode,
        "format": "PNG",
    }
    return rgb_image, rgb, ocr, receipt, source_record


def _box_record(box: tuple[int, int, int, int]) -> dict[str, int]:
    return {"x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]}


def _bbox_from_mask(
    mask: np.ndarray, *, offset_x: int, offset_y: int
) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return (
        int(xs.min()) + offset_x,
        int(ys.min()) + offset_y,
        int(xs.max()) + 1 + offset_x,
        int(ys.max()) + 1 + offset_y,
    )


def _candidate_detector_geometry(
    candidate: Mapping[str, Any], width: int, height: int
) -> tuple[tuple[int, int, int, int] | None, list[list[float]], list[str]]:
    flags: list[str] = []
    polygon_value = candidate.get("polygon_source")
    polygon: list[list[float]] = []
    if isinstance(polygon_value, list) and len(polygon_value) >= 4:
        for point in polygon_value:
            if not isinstance(point, list) or len(point) != 2:
                polygon = []
                break
            x, y = float(point[0]), float(point[1])
            if not math.isfinite(x) or not math.isfinite(y):
                polygon = []
                break
            polygon.append([min(max(x, 0.0), float(width)), min(max(y, 0.0), float(height))])
    if polygon:
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        x0, y0 = math.floor(min(xs)), math.floor(min(ys))
        x1, y1 = math.ceil(max(xs)), math.ceil(max(ys))
    else:
        flags.append("PRIMARY_POLYGON_INVALID_BBOX_FALLBACK")
        box = candidate.get("bbox_source")
        if not isinstance(box, Mapping):
            return None, [], [*flags, "PRIMARY_GEOMETRY_MISSING"]
        try:
            x0 = math.floor(float(box["x"]))
            y0 = math.floor(float(box["y"]))
            x1 = math.ceil(float(box["x"]) + float(box["w"]))
            y1 = math.ceil(float(box["y"]) + float(box["h"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, [], [*flags, "PRIMARY_GEOMETRY_INVALID"]
        polygon = [
            [float(x0), float(y0)],
            [float(x1), float(y0)],
            [float(x1), float(y1)],
            [float(x0), float(y1)],
        ]
    unclipped = (x0, y0, x1, y1)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if (x0, y0, x1, y1) != unclipped:
        flags.append("PRIMARY_GEOMETRY_CLIPPED_TO_SOURCE")
    if x1 <= x0 or y1 <= y0:
        return None, polygon, [*flags, "EMPTY_PRIMARY_ROI"]
    return (x0, y0, x1, y1), polygon, flags


def _odd_window(limit: int) -> int | None:
    if limit < 3:
        return None
    configured = int(PARAMETERS["sauvola_window_px"])
    value = min(configured, limit if limit % 2 else limit - 1)
    return max(3, value)


def _choose_otsu_polarity(
    gray: np.ndarray, target: np.ndarray, background_gray: float
) -> tuple[np.ndarray, str]:
    values = gray[target]
    if values.size < 2 or int(values.min()) == int(values.max()):
        return np.zeros_like(target), "none"
    threshold, _unused = cv2.threshold(
        values.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    dark = (gray <= threshold) & target
    light = (gray > threshold) & target

    def score(mask: np.ndarray) -> tuple[float, float]:
        count = int(mask.sum())
        if count == 0:
            return (-math.inf, math.inf)
        difference = float(np.median(np.abs(gray[mask].astype(np.float32) - background_gray)))
        fraction = count / max(int(target.sum()), 1)
        return (difference, -fraction)

    if score(dark) >= score(light):
        return dark, "dark"
    return light, "light"


def _remove_line_contamination(mask: np.ndarray) -> tuple[np.ndarray, bool]:
    cleaned = mask.astype(np.uint8).copy()
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(cleaned, 8)
    contaminated = False
    roi_height, roi_width = mask.shape
    max_thickness = int(PARAMETERS["line_contamination_max_thickness_px"])
    span_fraction = float(PARAMETERS["line_contamination_span_fraction"])
    for label in range(1, component_count):
        x, y, width, height, _area = [int(value) for value in stats[label]]
        horizontal_line = width >= span_fraction * roi_width and height <= max_thickness
        vertical_line = height >= span_fraction * roi_height and width <= max_thickness
        touches = sum((x == 0, y == 0, x + width == roi_width, y + height == roi_height))
        frame_fragment = touches >= 3
        if horizontal_line or vertical_line or frame_fragment:
            cleaned[labels == label] = 0
            contaminated = True
    return cleaned.astype(bool), contaminated


def _pairwise_iou(masks: Sequence[np.ndarray]) -> float:
    values: list[float] = []
    for left_index in range(len(masks)):
        for right_index in range(left_index + 1, len(masks)):
            union = int(np.logical_or(masks[left_index], masks[right_index]).sum())
            if union:
                intersection = int(np.logical_and(masks[left_index], masks[right_index]).sum())
                values.append(intersection / union)
    return max(values, default=0.0)


def _line_band_count(mask: np.ndarray) -> int:
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return 0
    gaps = np.diff(rows)
    minimum_gap = max(2, int(round(mask.shape[0] * float(PARAMETERS["multiline_gap_fraction"]))))
    return 1 + int(np.sum(gaps > minimum_gap))


def _component_bottom_points(
    mask: np.ndarray, offset_x: int, offset_y: int
) -> list[tuple[float, float]]:
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    bottoms: list[tuple[float, float]] = []
    for label in range(1, count):
        x, y, width, height, area = [int(value) for value in stats[label]]
        if area >= 2 and width >= 1 and height >= 2:
            bottoms.append((float(offset_x + x + width / 2.0), float(offset_y + y + height)))
    return bottoms


def _normalize_ocr_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _detector_repeatability(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_ocr_text(candidate.get("normalized_text", candidate.get("text", "")))
    edges: list[tuple[float, float, float, float]] = []
    for observation in candidate.get("observations", []):
        if (
            not isinstance(observation, Mapping)
            or _normalize_ocr_text(observation.get("text", "")) != normalized
        ):
            continue
        box = observation.get("bbox_source")
        if not isinstance(box, Mapping):
            continue
        try:
            x0, y0 = float(box["x"]), float(box["y"])
            x1, y1 = x0 + float(box["w"]), y0 + float(box["h"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            edges.append((x0, y0, x1, y1))

    def spread(index: int) -> float:
        values = [edge[index] for edge in edges]
        return round(max(values) - min(values), 3) if values else 0.0

    return {
        "supporting_observation_count": len(edges),
        "left": spread(0),
        "top": spread(1),
        "right": spread(2),
        "bottom": spread(3),
    }


def _formula_like(candidate: Mapping[str, Any]) -> bool:
    flags = {str(flag) for flag in candidate.get("review_flags", [])}
    return "FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE" in flags or bool(
        FORMULA_PATTERN.search(str(candidate.get("text", "")))
    )


def _ocr_conflict(candidate: Mapping[str, Any]) -> bool:
    flags = {str(flag) for flag in candidate.get("review_flags", [])}
    verification = candidate.get("verification", {})
    return (
        "OCR_CONFLICT" in flags
        or candidate.get("confidence_band") == "OCR_CONFLICT"
        or (isinstance(verification, Mapping) and verification.get("status") == "CONFLICT")
    )


def _extract_candidate(
    rgb: np.ndarray,
    candidate: Mapping[str, Any],
    *,
    mask_label: int,
) -> tuple[dict[str, Any], CandidateMask, np.ndarray | None]:
    height, width = rgb.shape[:2]
    candidate_id = str(candidate["candidate_id"])
    detector, polygon, flags = _candidate_detector_geometry(candidate, width, height)
    text = str(candidate.get("text", ""))
    confidence = float(candidate.get("ocr_confidence", 0.0))
    primary_observation_id = str(candidate.get("primary_observation_id", ""))
    input_bbox = candidate.get("bbox_source")
    input_polygon = candidate.get("polygon_source")
    repeatability = _detector_repeatability(candidate)
    conflict = _ocr_conflict(candidate)
    if conflict:
        flags.append("OCR_CONFLICT")
    formula = _formula_like(candidate)
    if formula:
        flags.append("FORMULA_LIKE")

    empty_baseline = {
        "status": "INCONCLUSIVE",
        "meaning": "INK_BOTTOM_ALIGNMENT_ONLY",
        "y_source_px": None,
        "uncertainty_px": None,
        "support_component_count": 0,
        "endpoints_source": None,
        "angle_degrees": None,
        "inlier_fraction": None,
        "rmse_px": None,
        "method": "CONNECTED_COMPONENT_BOTTOM_ROBUST_LINEAR_FIT",
        "reason": "INK_NOT_MEASURED",
    }
    if detector is None:
        record = {
            "candidate_id": candidate_id,
            "primary_observation_id": primary_observation_id,
            "text": text,
            "ocr_confidence": confidence,
            "input_bbox_source": dict(input_bbox) if isinstance(input_bbox, Mapping) else None,
            "input_polygon_source": input_polygon if isinstance(input_polygon, list) else [],
            "detector_bbox": None,
            "detector_polygon": polygon,
            "detector_repeatability_px": repeatability,
            "roi_bbox": None,
            "status": "INCONCLUSIVE",
            "ink_bbox": None,
            "ink_area_px": 0,
            "mask_label": None,
            "method_evidence": [],
            "edge_uncertainty_px": None,
            "baseline": empty_baseline,
            "quality_flags": sorted(set(flags)),
            "reasons": ["NO_NONEMPTY_PRIMARY_ROI"],
        }
        state = CandidateMask(
            candidate_id,
            (0, 0, 0, 0),
            (0, 0, 0, 0),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        return record, state, None

    x0, y0, x1, y1 = detector
    pad = int(PARAMETERS["bbox_padding_px"])
    rx0, ry0, rx1, ry1 = (
        max(0, x0 - pad),
        max(0, y0 - pad),
        min(width, x1 + pad),
        min(height, y1 + pad),
    )
    crop = rgb[ry0:ry1, rx0:rx1]
    crop_height, crop_width = crop.shape[:2]
    target = np.zeros((crop_height, crop_width), dtype=np.uint8)
    translated = np.asarray(
        [
            [
                min(max(int(round(point[0])) - rx0, 0), crop_width - 1),
                min(max(int(round(point[1])) - ry0, 0), crop_height - 1),
            ]
            for point in polygon
        ],
        dtype=np.int32,
    )
    if translated.shape[0] >= 3:
        cv2.fillPoly(target, [translated], 1)
        detector_clip = np.zeros_like(target)
        detector_clip[y0 - ry0 : y1 - ry0, x0 - rx0 : x1 - rx0] = 1
        target &= detector_clip
    if int(target.sum()) < 2:
        target[y0 - ry0 : y1 - ry0, x0 - rx0 : x1 - rx0] = 1
        flags.append("POLYGON_RASTER_FALLBACK")
    target_bool = target.astype(bool)

    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    lab = cv2.cvtColor(crop, cv2.COLOR_RGB2LAB).astype(np.float32)
    background_region = ~target_bool
    if int(background_region.sum()) < 8:
        background_region = np.zeros_like(target_bool)
        background_region[[0, -1], :] = True
        background_region[:, [0, -1]] = True
        flags.append("WEAK_LOCAL_BACKGROUND_SAMPLE")
    background_gray = float(np.median(gray[background_region]))
    background_lab = np.median(lab[background_region], axis=0)

    otsu, polarity = _choose_otsu_polarity(gray, target_bool, background_gray)
    window = _odd_window(min(crop_height, crop_width))
    if window is None or polarity == "none":
        sauvola = np.zeros_like(target_bool)
    elif polarity == "dark":
        local_threshold = threshold_sauvola(
            gray,
            window_size=window,
            k=float(PARAMETERS["sauvola_k"]),
        )
        sauvola = (gray < local_threshold) & target_bool
    else:
        inverse = 255 - gray
        local_threshold = threshold_sauvola(
            inverse,
            window_size=window,
            k=float(PARAMETERS["sauvola_k"]),
        )
        sauvola = (inverse < local_threshold) & target_bool

    delta = np.linalg.norm(lab - background_lab, axis=2)
    background_delta = delta[background_region]
    median_delta = float(np.median(background_delta)) if background_delta.size else 0.0
    mad_delta = (
        float(np.median(np.abs(background_delta - median_delta))) if background_delta.size else 0.0
    )
    lab_threshold = max(
        float(PARAMETERS["lab_min_delta"]),
        median_delta + float(PARAMETERS["lab_background_mad_multiplier"]) * max(mad_delta, 0.5),
    )
    lab_mask = (delta > lab_threshold) & target_bool

    method_masks: list[tuple[str, np.ndarray]] = []
    contamination = False
    for name, raw_mask in (
        ("OTSU_GRAY", otsu),
        ("SAUVOLA_GRAY", sauvola),
        ("LAB_BACKGROUND_DELTA", lab_mask),
    ):
        cleaned, line_removed = _remove_line_contamination(raw_mask)
        contamination = contamination or line_removed
        method_masks.append((name, cleaned))
    if contamination:
        flags.append("FRAME_OR_LINE_CONTAMINATION_REMOVED")

    votes = np.sum(np.stack([mask for _name, mask in method_masks], axis=0), axis=0)
    consensus = votes >= int(PARAMETERS["foreground_vote_min"])
    ambiguity = ((votes > 0) & (votes < len(method_masks))).astype(np.uint8)
    ink_bbox = _bbox_from_mask(consensus, offset_x=rx0, offset_y=ry0)
    ink_area = int(consensus.sum())
    target_area = max(int(target_bool.sum()), 1)
    foreground_fraction = ink_area / target_area
    pair_iou = _pairwise_iou([mask for _name, mask in method_masks])

    evidence: list[dict[str, Any]] = []
    method_boxes: list[tuple[int, int, int, int]] = []
    for name, mask in method_masks:
        box = _bbox_from_mask(mask, offset_x=rx0, offset_y=ry0)
        if box is not None:
            method_boxes.append(box)
        evidence.append(
            {
                "method": name,
                "status": "MEASURED" if box is not None else "INCONCLUSIVE",
                "area_px": int(mask.sum()),
                "bbox_source": _box_record(box) if box is not None else None,
            }
        )

    reasons: list[str] = []
    measured = True
    if ink_bbox is None or ink_area < int(PARAMETERS["minimum_ink_area_px"]):
        measured = False
        reasons.append("NO_STABLE_FOREGROUND_CONSENSUS")
    if foreground_fraction > float(PARAMETERS["maximum_foreground_fraction"]):
        measured = False
        flags.append("FOREGROUND_DOMINATES_ROI")
        reasons.append("FOREGROUND_OCCUPANCY_TOO_HIGH")
    if pair_iou < float(PARAMETERS["minimum_pairwise_mask_iou"]):
        measured = False
        flags.append("LOW_MASK_METHOD_AGREEMENT")
        reasons.append("MASK_METHODS_DISAGREE")
    diagnostic_mask = consensus if measured else None
    touches_primary_boundary = bool(
        ink_bbox is not None
        and (ink_bbox[0] <= x0 or ink_bbox[1] <= y0 or ink_bbox[2] >= x1 or ink_bbox[3] >= y1)
    )
    if touches_primary_boundary:
        flags.append("INK_TOUCHES_PRIMARY_BOUNDARY")

    uncertainty: dict[str, float] | None = None
    if ink_bbox is not None and method_boxes:
        minimum = float(PARAMETERS["minimum_edge_uncertainty_px"])
        uncertainty = {
            "left": max(
                minimum,
                float(repeatability["left"]),
                max(abs(box[0] - ink_bbox[0]) for box in method_boxes),
            ),
            "top": max(
                minimum,
                float(repeatability["top"]),
                max(abs(box[1] - ink_bbox[1]) for box in method_boxes),
            ),
            "right": max(
                minimum,
                float(repeatability["right"]),
                max(abs(box[2] - ink_bbox[2]) for box in method_boxes),
            ),
            "bottom": max(
                minimum,
                float(repeatability["bottom"]),
                max(abs(box[3] - ink_bbox[3]) for box in method_boxes),
            ),
        }

    detector_width, detector_height = x1 - x0, y1 - y0
    normalized_length = len("".join(text.split()))
    vertical = (
        detector_height > detector_width * float(PARAMETERS["vertical_text_aspect_ratio"])
        and normalized_length > 1
    )
    multiline = _line_band_count(consensus) > 1 if measured else False
    if vertical:
        flags.append("VERTICAL_ORIENTATION")
    if multiline:
        flags.append("MULTILINE_INK_LAYOUT")
    if confidence < float(PARAMETERS["baseline_minimum_ocr_confidence"]):
        flags.append("LOW_OCR_CONFIDENCE")

    # These classes are intentionally diagnostic-only in Phase 1.  Their local
    # method evidence remains visible, but no reliable ink label/bbox is emitted.
    strict_degradation_reasons: list[str] = []
    if conflict:
        strict_degradation_reasons.append("OCR_CONFLICT_REQUIRES_REVIEW")
    if formula:
        strict_degradation_reasons.append("FORMULA_LIKE_GEOMETRY_INCONCLUSIVE")
    if vertical:
        strict_degradation_reasons.append("VERTICAL_TEXT_GEOMETRY_INCONCLUSIVE")
    if multiline:
        strict_degradation_reasons.append("MULTILINE_TEXT_GEOMETRY_INCONCLUSIVE")
    if contamination:
        strict_degradation_reasons.append("FRAME_OR_LINE_CONTAMINATION_INCONCLUSIVE")
    if touches_primary_boundary:
        strict_degradation_reasons.append("POSSIBLE_OCR_DETECTOR_TRUNCATION")
    if strict_degradation_reasons:
        measured = False
        reasons.extend(strict_degradation_reasons)
        # Mark every foreground hypothesis as ambiguous; none may become a
        # stable atlas label for a strict-degradation candidate.
        ambiguity = (votes > 0).astype(np.uint8)

    baseline = dict(empty_baseline)
    baseline_blockers: list[str] = []
    if not measured:
        baseline_blockers.append("INK_NOT_MEASURED")
    if conflict:
        baseline_blockers.append("OCR_CONFLICT")
    if formula:
        baseline_blockers.append("FORMULA_LIKE")
    if vertical:
        baseline_blockers.append("VERTICAL_ORIENTATION")
    if multiline:
        baseline_blockers.append("MULTILINE_INK_LAYOUT")
    if contamination:
        baseline_blockers.append("FRAME_OR_LINE_CONTAMINATION")
    if confidence < float(PARAMETERS["baseline_minimum_ocr_confidence"]):
        baseline_blockers.append("LOW_OCR_CONFIDENCE")

    bottom_points = _component_bottom_points(consensus, rx0, ry0) if measured else []
    baseline["support_component_count"] = len(bottom_points)
    if len(bottom_points) < int(PARAMETERS["minimum_baseline_components"]):
        baseline_blockers.append("INSUFFICIENT_COMPONENT_SUPPORT")
    if not baseline_blockers:
        points = np.asarray(bottom_points, dtype=np.float64)
        slope, intercept = np.polyfit(points[:, 0], points[:, 1], 1)
        predicted = slope * points[:, 0] + intercept
        residuals = points[:, 1] - predicted
        allowed_residual = max(
            float(PARAMETERS["baseline_max_spread_px"]),
            detector_height * float(PARAMETERS["baseline_max_spread_height_fraction"]),
        )
        inliers = np.abs(residuals) <= allowed_residual
        inlier_fraction = float(np.mean(inliers))
        if int(np.sum(inliers)) >= int(PARAMETERS["minimum_baseline_components"]):
            slope, intercept = np.polyfit(points[inliers, 0], points[inliers, 1], 1)
            predicted_inliers = slope * points[inliers, 0] + intercept
            rmse = float(np.sqrt(np.mean((points[inliers, 1] - predicted_inliers) ** 2)))
        else:
            rmse = math.inf
        angle = float(math.degrees(math.atan(float(slope))))
        line_x0 = float(min(point[0] for point in bottom_points))
        line_x1 = float(max(point[0] for point in bottom_points))
        center_x = (line_x0 + line_x1) / 2.0
        center_y = float(slope * center_x + intercept)
        line_endpoints = [
            [round(line_x0, 3), round(float(slope * line_x0 + intercept), 3)],
            [round(line_x1, 3), round(float(slope * line_x1 + intercept), 3)],
        ]
        line_passes = (
            abs(angle) <= float(PARAMETERS["baseline_max_angle_degrees"])
            and inlier_fraction >= float(PARAMETERS["baseline_minimum_inlier_fraction"])
            and rmse <= float(PARAMETERS["baseline_maximum_rmse_px"])
        )
        if line_passes:
            baseline.update(
                {
                    "status": "MEASURED",
                    "y_source_px": round(center_y, 3),
                    "uncertainty_px": round(
                        max(1.0, rmse + 1.0, float(repeatability["bottom"])), 3
                    ),
                    "endpoints_source": line_endpoints,
                    "angle_degrees": round(angle, 6),
                    "inlier_fraction": round(inlier_fraction, 6),
                    "rmse_px": round(rmse, 6),
                    "reason": None,
                }
            )
        else:
            baseline.update(
                {
                    "endpoints_source": line_endpoints,
                    "angle_degrees": round(angle, 6),
                    "inlier_fraction": round(inlier_fraction, 6),
                    "rmse_px": round(rmse, 6) if math.isfinite(rmse) else None,
                }
            )
            baseline_blockers.append("INK_BOTTOM_LINE_FIT_UNRELIABLE")
    if baseline_blockers:
        baseline["reason"] = ";".join(dict.fromkeys(baseline_blockers))

    status = "MEASURED" if measured else "INCONCLUSIVE"
    assigned_label = mask_label if measured else None
    record = {
        "candidate_id": candidate_id,
        "primary_observation_id": primary_observation_id,
        "text": text,
        "ocr_confidence": confidence,
        "input_bbox_source": dict(input_bbox) if isinstance(input_bbox, Mapping) else None,
        "input_polygon_source": input_polygon if isinstance(input_polygon, list) else [],
        "detector_bbox": _box_record(detector),
        "detector_polygon": polygon,
        "detector_repeatability_px": repeatability,
        "roi_bbox": _box_record((rx0, ry0, rx1, ry1)),
        "status": status,
        "ink_bbox": _box_record(ink_bbox) if measured and ink_bbox is not None else None,
        "ink_area_px": ink_area if measured else 0,
        "mask_label": assigned_label,
        "method_evidence": evidence,
        "edge_uncertainty_px": uncertainty if measured else None,
        "baseline": baseline,
        "quality_flags": sorted(set(flags)),
        "reasons": reasons,
    }
    state = CandidateMask(
        candidate_id,
        detector,
        (rx0, ry0, rx1, ry1),
        ink_bbox if measured else None,
        consensus if measured else None,
        diagnostic_mask,
        assigned_label,
        uncertainty if measured else None,
        baseline["y_source_px"] if baseline["status"] == "MEASURED" else None,
        baseline["uncertainty_px"] if baseline["status"] == "MEASURED" else None,
    )
    return record, state, ambiguity


def _signed_interval_gap(a0: int, a1: int, b0: int, b1: int) -> float:
    return float(max(a0, b0) - min(a1, b1))


def _minimum_mask_distance(left: CandidateMask, right: CandidateMask) -> float:
    assert left.mask is not None and right.mask is not None
    lx0, ly0, lx1, ly1 = left.roi_bbox
    rx0, ry0, rx1, ry1 = right.roi_bbox
    x0, y0, x1, y1 = min(lx0, rx0), min(ly0, ry0), max(lx1, rx1), max(ly1, ry1)
    first = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    second = np.zeros_like(first)
    first[ly0 - y0 : ly1 - y0, lx0 - x0 : lx1 - x0] |= left.mask
    second[ry0 - y0 : ry1 - y0, rx0 - x0 : rx1 - x0] |= right.mask
    if np.any(first & second):
        return 0.0
    distances = distance_transform_edt(~first)
    return float(np.min(distances[second]))


def _build_neighbor_pairs(states: Sequence[CandidateMask]) -> list[dict[str, Any]]:
    reliable = [
        state for state in states if state.baseline_y is not None and state.ink_bbox is not None
    ]
    reliable.sort(key=lambda item: (float(item.baseline_y), item.ink_bbox[0], item.candidate_id))
    groups: list[list[CandidateMask]] = []
    tolerance = float(PARAMETERS["same_alignment_tolerance_px"])
    for state in reliable:
        placed = False
        for group in groups:
            reference = float(np.median([item.baseline_y for item in group]))
            combined_uncertainty = max(
                tolerance,
                float(state.baseline_uncertainty or 0.0),
                max(float(item.baseline_uncertainty or 0.0) for item in group),
            )
            if abs(float(state.baseline_y) - reference) <= combined_uncertainty:
                group.append(state)
                placed = True
                break
        if not placed:
            groups.append([state])

    pairs: list[dict[str, Any]] = []
    for group in groups:
        ordered = sorted(group, key=lambda item: (item.ink_bbox[0], item.candidate_id))
        for left, right in zip(ordered, ordered[1:], strict=False):
            assert left.ink_bbox is not None and right.ink_bbox is not None
            horizontal = _signed_interval_gap(
                left.ink_bbox[0], left.ink_bbox[2], right.ink_bbox[0], right.ink_bbox[2]
            )
            vertical = _signed_interval_gap(
                left.ink_bbox[1], left.ink_bbox[3], right.ink_bbox[1], right.ink_bbox[3]
            )
            local_gap_limit = max(
                24.0,
                min(
                    float(PARAMETERS["local_pair_absolute_max_gap_px"]),
                    float(PARAMETERS["local_pair_max_gap_height_multiplier"])
                    * max(
                        left.detector_bbox[3] - left.detector_bbox[1],
                        right.detector_bbox[3] - right.detector_bbox[1],
                    ),
                ),
            )
            if horizontal > local_gap_limit or vertical >= 0:
                continue
            distance = _minimum_mask_distance(left, right)
            assert left.edge_uncertainty is not None and right.edge_uncertainty is not None
            uncertainty = max(
                left.edge_uncertainty["right"] + right.edge_uncertainty["left"],
                left.edge_uncertainty["top"] + right.edge_uncertainty["top"],
                left.edge_uncertainty["bottom"] + right.edge_uncertainty["bottom"],
            )
            pairs.append(
                {
                    "pair_id": "",
                    "candidate_a_id": left.candidate_id,
                    "candidate_b_id": right.candidate_id,
                    "relationship": "LOCAL_PAIR_UNVERIFIED_CONTAINER",
                    "status": "MEASURED",
                    "signed_horizontal_gap_px": round(horizontal, 3),
                    "signed_vertical_gap_px": round(vertical, 3),
                    "minimum_ink_distance_px": round(distance, 3),
                    "uncertainty_px": round(uncertainty, 3),
                }
            )
    pairs.sort(key=lambda item: (item["candidate_a_id"], item["candidate_b_id"]))
    for index, pair in enumerate(pairs, start=1):
        pair["pair_id"] = f"G{index:04d}"
    return pairs


def _degrade_overlapping_candidate_masks(
    records: Sequence[dict[str, Any]],
    states: Sequence[CandidateMask],
    ambiguity_atlas: np.ndarray,
) -> None:
    """Fail closed when multiple OCR candidates claim any source ink pixel.

    A single-channel label atlas cannot losslessly encode multiple owners for a
    pixel.  First-writer-wins would therefore make a later MEASURED record
    unverifiable (and can leave its advertised label with zero pixels).  Phase
    1 treats every participant in an overlap as diagnostic-only instead.
    """

    height, width = ambiguity_atlas.shape
    coverage = np.zeros((height, width), dtype=np.uint32)
    for state in states:
        if state.mask is None:
            continue
        x0, y0, x1, y1 = state.roi_bbox
        if state.mask.shape != (y1 - y0, x1 - x0):
            raise RuntimeError(f"candidate {state.candidate_id} mask/ROI shape mismatch")
        coverage[y0:y1, x0:x1] += state.mask.astype(np.uint32)

    overlapping_indices: list[int] = []
    for index, state in enumerate(states):
        if state.mask is None:
            continue
        x0, y0, x1, y1 = state.roi_bbox
        if np.any(state.mask & (coverage[y0:y1, x0:x1] > 1)):
            overlapping_indices.append(index)

    for index in overlapping_indices:
        record = records[index]
        state = states[index]
        assert state.mask is not None
        x0, y0, x1, y1 = state.roi_bbox
        ambiguity_atlas[y0:y1, x0:x1][state.mask] = 255

        record["status"] = "INCONCLUSIVE"
        record["ink_bbox"] = None
        record["ink_area_px"] = 0
        record["mask_label"] = None
        record["edge_uncertainty_px"] = None
        record["quality_flags"] = sorted({*record["quality_flags"], "OVERLAPPING_CANDIDATE_MASKS"})
        record["reasons"] = list(
            dict.fromkeys([*record["reasons"], "CANDIDATE_MASK_OWNERSHIP_AMBIGUOUS"])
        )
        baseline = record["baseline"]
        baseline.update(
            {
                "status": "INCONCLUSIVE",
                "y_source_px": None,
                "uncertainty_px": None,
                "support_component_count": 0,
                "endpoints_source": None,
                "angle_degrees": None,
                "inlier_fraction": None,
                "rmse_px": None,
                "reason": "OVERLAPPING_CANDIDATE_MASKS",
            }
        )

        # Keep diagnostic_mask for ambiguity evidence and frame suppression,
        # but remove every field that could promote this mask to geometry.
        state.ink_bbox = None
        state.mask = None
        state.label = None
        state.edge_uncertainty = None
        state.baseline_y = None
        state.baseline_uncertainty = None


def _bbox_iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    ix0, iy0 = max(left[0], right[0]), max(left[1], right[1])
    ix1, iy1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    left_area = max(0, left[2] - left[0]) * max(0, left[3] - left[1])
    right_area = max(0, right[2] - right[0]) * max(0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _nested_contour_inset(
    left: tuple[int, int, int, int], right: tuple[int, int, int, int]
) -> tuple[tuple[int, int, int, int], float] | None:
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    outer, inner = (left, right) if left_area >= right_area else (right, left)
    if not (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    ):
        return None
    outer_center = ((outer[0] + outer[2]) / 2.0, (outer[1] + outer[3]) / 2.0)
    inner_center = ((inner[0] + inner[2]) / 2.0, (inner[1] + inner[3]) / 2.0)
    center_tolerance = float(PARAMETERS["frame_nested_contour_center_tolerance_px"])
    if (
        abs(outer_center[0] - inner_center[0]) > center_tolerance
        or abs(outer_center[1] - inner_center[1]) > center_tolerance
        or _bbox_iou(outer, inner) < float(PARAMETERS["frame_nested_contour_minimum_iou"])
    ):
        return None
    insets = [
        float(inner[0] - outer[0]),
        float(inner[1] - outer[1]),
        float(outer[2] - inner[2]),
        float(outer[3] - inner[3]),
    ]
    inset = float(np.median(insets))
    if (
        inset <= 0
        or max(insets) > float(PARAMETERS["frame_nested_contour_maximum_inset_px"])
        or max(insets) - min(insets) > 2.0
    ):
        return None
    return outer, inset


def _side_support(
    edges: np.ndarray, box: tuple[int, int, int, int]
) -> tuple[list[str], dict[str, float]]:
    x0, y0, x1, y1 = box
    height, width = edges.shape
    inset_x = max(1, int((x1 - x0) * 0.15))
    inset_y = max(1, int((y1 - y0) * 0.15))

    def density(region: np.ndarray) -> float:
        return float(np.mean(region > 0)) if region.size else 0.0

    top = edges[max(0, y0 - 1) : min(height, y0 + 2), min(x1, x0 + inset_x) : max(x0, x1 - inset_x)]
    bottom = edges[
        max(0, y1 - 2) : min(height, y1 + 1), min(x1, x0 + inset_x) : max(x0, x1 - inset_x)
    ]
    left = edges[min(y1, y0 + inset_y) : max(y0, y1 - inset_y), max(0, x0 - 1) : min(width, x0 + 2)]
    right = edges[
        min(y1, y0 + inset_y) : max(y0, y1 - inset_y), max(0, x1 - 2) : min(width, x1 + 1)
    ]
    values = {
        "top": density(top),
        "right": density(right),
        "bottom": density(bottom),
        "left": density(left),
    }
    threshold = float(PARAMETERS["frame_side_support_fraction"])
    return [name for name, value in values.items() if value >= threshold], values


def _lsd_supported_sides(lines: np.ndarray | None, box: tuple[int, int, int, int]) -> list[str]:
    if lines is None:
        return []
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    supported: set[str] = set()
    for raw in lines.reshape(-1, 4):
        ax, ay, bx, by = [float(value) for value in raw]
        dx, dy = abs(bx - ax), abs(by - ay)
        if dx >= max(6.0, width * 0.45) and dy <= 2.0:
            y = (ay + by) / 2.0
            if abs(y - y0) <= 3.0:
                supported.add("top")
            if abs(y - (y1 - 1)) <= 3.0:
                supported.add("bottom")
        if dy >= max(6.0, height * 0.45) and dx <= 2.0:
            x = (ax + bx) / 2.0
            if abs(x - x0) <= 3.0:
                supported.add("left")
            if abs(x - (x1 - 1)) <= 3.0:
                supported.add("right")
    return sorted(supported)


def _edge_band_width(
    gradient: np.ndarray, box: tuple[int, int, int, int]
) -> tuple[float | None, float | None]:
    x0, y0, x1, y1 = box
    height, width = gradient.shape
    samples: list[float] = []
    for horizontal, coordinate, start, end in (
        (True, y0, x0, x1),
        (True, y1 - 1, x0, x1),
        (False, x0, y0, y1),
        (False, x1 - 1, y0, y1),
    ):
        if horizontal:
            low, high = max(0, coordinate - 4), min(height, coordinate + 5)
            strip = gradient[low:high, start:end]
            profile = np.mean(strip, axis=1) if strip.size else np.array([])
        else:
            low, high = max(0, coordinate - 4), min(width, coordinate + 5)
            strip = gradient[start:end, low:high]
            profile = np.mean(strip, axis=0) if strip.size else np.array([])
        if profile.size and float(profile.max()) > 0:
            support = profile >= float(profile.max()) * 0.45
            peak = int(np.argmax(profile))
            left = peak
            right = peak
            while left > 0 and support[left - 1]:
                left -= 1
            while right + 1 < support.size and support[right + 1]:
                right += 1
            samples.append(float(right - left + 1))
    if not samples:
        return None, None
    center = float(np.median(samples))
    uncertainty = max(1.0, max(abs(value - center) for value in samples) + 1.0)
    return round(center, 3), round(uncertainty, 3)


def _interior_foreground_fraction(cleaned_lab: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    image_height, image_width = cleaned_lab.shape[:2]
    pad = 3
    ox0, oy0 = max(0, x0 - pad), max(0, y0 - pad)
    ox1, oy1 = min(image_width, x1 + pad), min(image_height, y1 + pad)
    outer = cleaned_lab[oy0:oy1, ox0:ox1]
    outside = np.ones(outer.shape[:2], dtype=bool)
    outside[y0 - oy0 : y1 - oy0, x0 - ox0 : x1 - ox0] = False
    samples = outer[outside]
    if samples.size == 0:
        return 1.0
    background = np.median(samples.astype(np.float32), axis=0)
    inset = max(2, min(5, min(x1 - x0, y1 - y0) // 5))
    interior = cleaned_lab[y0 + inset : y1 - inset, x0 + inset : x1 - inset]
    if interior.size == 0:
        return 1.0
    delta = np.linalg.norm(interior.astype(np.float32) - background, axis=2)
    return float(np.mean(delta > float(PARAMETERS["lab_min_delta"])))


def _downgrade_grid_like_cells(records: list[dict[str, Any]]) -> None:
    if len(records) < int(PARAMETERS["grid_cell_minimum_cluster_size"]):
        return
    size_tolerance = float(PARAMETERS["grid_cell_size_relative_tolerance"])
    adjacency_tolerance = float(PARAMETERS["grid_cell_adjacency_tolerance_px"])
    graph: list[set[int]] = [set() for _record in records]

    def dimensions(record: Mapping[str, Any]) -> tuple[float, float, float, float]:
        box = record["bbox_source"]
        width = float(box["x1"] - box["x0"])
        height = float(box["y1"] - box["y0"])
        return width, height, (box["x0"] + box["x1"]) / 2.0, (box["y0"] + box["y1"]) / 2.0

    values = [dimensions(record) for record in records]
    for left_index in range(len(records)):
        left_width, left_height, left_cx, left_cy = values[left_index]
        for right_index in range(left_index + 1, len(records)):
            right_width, right_height, right_cx, right_cy = values[right_index]
            if (
                abs(left_width - right_width) / max(left_width, right_width, 1.0) > size_tolerance
                or abs(left_height - right_height) / max(left_height, right_height, 1.0)
                > size_tolerance
            ):
                continue
            horizontal_neighbor = (
                abs(left_cy - right_cy) <= adjacency_tolerance
                and abs(abs(left_cx - right_cx) - (left_width + right_width) / 2.0)
                <= adjacency_tolerance
            )
            vertical_neighbor = (
                abs(left_cx - right_cx) <= adjacency_tolerance
                and abs(abs(left_cy - right_cy) - (left_height + right_height) / 2.0)
                <= adjacency_tolerance
            )
            if horizontal_neighbor or vertical_neighbor:
                graph[left_index].add(right_index)
                graph[right_index].add(left_index)

    visited: set[int] = set()
    minimum_size = int(PARAMETERS["grid_cell_minimum_cluster_size"])
    for start in range(len(records)):
        if start in visited:
            continue
        component: list[int] = []
        pending = [start]
        visited.add(start)
        while pending:
            current = pending.pop()
            component.append(current)
            for neighbor in graph[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
        if len(component) < minimum_size:
            continue
        centers_x = sorted(values[index][2] for index in component)
        centers_y = sorted(values[index][3] for index in component)
        x_groups = 1 + sum(
            right - left > adjacency_tolerance
            for left, right in zip(centers_x, centers_x[1:], strict=False)
        )
        y_groups = 1 + sum(
            right - left > adjacency_tolerance
            for left, right in zip(centers_y, centers_y[1:], strict=False)
        )
        if x_groups < 2 or y_groups < 2:
            continue
        component_boxes = [records[index]["bbox_source"] for index in component]
        envelope = (
            min(box["x0"] for box in component_boxes),
            min(box["y0"] for box in component_boxes),
            max(box["x1"] for box in component_boxes),
            max(box["y1"] for box in component_boxes),
        )
        affected = set(component)
        for index, record in enumerate(records):
            box = record["bbox_source"]
            if (
                box["x0"] <= envelope[0] + adjacency_tolerance
                and box["y0"] <= envelope[1] + adjacency_tolerance
                and box["x1"] >= envelope[2] - adjacency_tolerance
                and box["y1"] >= envelope[3] - adjacency_tolerance
            ):
                affected.add(index)
        for index in affected:
            record = records[index]
            record["status"] = "INCONCLUSIVE"
            if "GRID_LIKE_REPEATED_CELLS" not in record["quality_flags"]:
                record["quality_flags"].append("GRID_LIKE_REPEATED_CELLS")
            if "REGULAR_GRID_CELLS_ARE_NOT_INDIVIDUAL_FRAMES" not in record["reasons"]:
                record["reasons"].append("REGULAR_GRID_CELLS_ARE_NOT_INDIVIDUAL_FRAMES")


def _detect_frames(
    rgb: np.ndarray,
    text_mask: np.ndarray,
    uncertain_text_zones: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if text_mask.shape != gray.shape:
        raise RuntimeError("text ink mask/source shape mismatch")
    if uncertain_text_zones is not None and uncertain_text_zones.shape != gray.shape:
        raise RuntimeError("uncertain text zone/source shape mismatch")
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    radius = int(PARAMETERS["text_mask_dilation_px"])
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), dtype=np.uint8)
    expanded_text = cv2.dilate(text_mask.astype(np.uint8), kernel)
    cleaned_gray = cv2.inpaint(gray, expanded_text, 3, cv2.INPAINT_TELEA)
    cleaned_lab = np.stack(
        [
            cv2.inpaint(lab[:, :, channel], expanded_text, 3, cv2.INPAINT_TELEA)
            for channel in range(3)
        ],
        axis=2,
    )
    edge_layers = [
        cv2.Canny(
            cleaned_gray,
            int(PARAMETERS["canny_low_threshold"]),
            int(PARAMETERS["canny_high_threshold"]),
        )
    ]
    for channel in range(3):
        edge_layers.append(
            cv2.Canny(
                cleaned_lab[:, :, channel],
                int(PARAMETERS["canny_low_threshold"]),
                int(PARAMETERS["canny_high_threshold"]),
            )
        )
    edges = np.maximum.reduce(edge_layers)
    edges[expanded_text > 0] = 0
    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    lines = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(edges)[0]
    sobel_x = cv2.Sobel(cleaned_gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(cleaned_gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(sobel_x, sobel_y)
    image_height, image_width = gray.shape
    proposals: list[tuple[float, tuple[int, int, int, int], dict[str, Any]]] = []
    for contour in contours:
        x, y, width, height = [int(value) for value in cv2.boundingRect(contour)]
        box = (x, y, x + width, y + height)
        if width < int(PARAMETERS["frame_minimum_width_px"]) or height < int(
            PARAMETERS["frame_minimum_height_px"]
        ):
            continue
        border = int(PARAMETERS["frame_border_exclusion_px"])
        if (
            x <= border
            and y <= border
            and x + width >= image_width - border
            and y + height >= image_height - border
        ):
            continue
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            continue
        approximation = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        contour_area = abs(float(cv2.contourArea(contour)))
        rectangularity = contour_area / max(width * height, 1)
        if len(approximation) < 4:
            continue
        sides, side_values = _side_support(edges, box)
        lsd_sides = _lsd_supported_sides(lines, box)
        if len(sides) < int(PARAMETERS["frame_minimum_supported_sides"]):
            continue
        if not (4 <= len(approximation) <= 12 or rectangularity >= 0.55):
            continue
        closed_contour = bool(
            len(sides) == 4 and contour_area > 0 and cv2.isContourConvex(approximation)
        )
        stroke_width, stroke_uncertainty = _edge_band_width(gradient, box)
        interior_fraction = _interior_foreground_fraction(cleaned_lab, box)
        stroke_ratio = (
            float(stroke_width) / max(1.0, float(min(width, height)))
            if stroke_width is not None
            else math.inf
        )
        quality_flags: list[str] = []
        reasons: list[str] = []
        if interior_fraction > float(PARAMETERS["frame_maximum_interior_foreground_fraction"]):
            quality_flags.append("HIGH_INTERIOR_FOREGROUND_OCCUPANCY")
            reasons.append("FILLED_OR_INTERNALLY_OCCUPIED_RECTANGLE")
        if stroke_ratio > float(PARAMETERS["frame_maximum_stroke_to_min_dimension_ratio"]):
            quality_flags.append("THICK_EDGE_NOT_FRAME_LIKE")
            reasons.append("EDGE_TRANSITION_TOO_THICK_FOR_FRAME")
        aspect_ratio = max(width / max(height, 1), height / max(width, 1))
        if aspect_ratio > float(PARAMETERS["frame_maximum_aspect_ratio"]):
            quality_flags.append("EXTREME_ASPECT_RATIO")
            reasons.append("EXTREME_ASPECT_RATIO_NOT_FRAME_LIKE")
        measured = (
            len(sides) == 4
            and len(lsd_sides) >= int(PARAMETERS["frame_minimum_lsd_sides"])
            and closed_contour
            and not reasons
        )
        uncertainty = round(max(1.0, (4 - len(sides)) + (1.0 - min(rectangularity, 1.0)) * 2.0), 3)
        record = {
            "frame_id": "",
            "status": "MEASURED" if measured else "INCONCLUSIVE",
            "semantic_role": "UNVERIFIED",
            "shape_hint": "RECTANGLE_OR_ROUNDED_RECTANGLE",
            "bbox_source": _box_record(box),
            "corners_source": [
                [float(x), float(y)],
                [float(x + width), float(y)],
                [float(x + width), float(y + height)],
                [float(x), float(y + height)],
            ],
            "uncertainty_px": uncertainty,
            "contour_rectangularity": round(rectangularity, 6),
            "contour_vertex_count": int(len(approximation)),
            "closed_contour_evidence": closed_contour,
            "contour_pair_count": 1,
            "paired_contour_inset_px": None,
            "interior_foreground_fraction": round(interior_fraction, 6),
            "quality_flags": quality_flags,
            "reasons": reasons,
            "edge_supported_sides": sides,
            "edge_side_density": {key: round(value, 6) for key, value in side_values.items()},
            "lsd_supported_sides": lsd_sides,
            "stroke": {
                "status": "MEASURED" if stroke_width is not None else "INCONCLUSIVE",
                "width_px": stroke_width,
                "uncertainty_px": stroke_uncertainty,
                "method": "EDGE_TRANSITION_BAND",
            },
        }
        score = len(sides) + len(lsd_sides) + rectangularity
        proposals.append((score, box, record))

    selected: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for _score, box, record in sorted(proposals, key=lambda item: (-item[0], item[1])):
        merged = False
        duplicate = False
        for selected_index, (existing_box, existing_record) in enumerate(selected):
            nested = _nested_contour_inset(box, existing_box)
            if nested is not None:
                outer_box, inset = nested
                box_area = (box[2] - box[0]) * (box[3] - box[1])
                existing_area = (existing_box[2] - existing_box[0]) * (
                    existing_box[3] - existing_box[1]
                )
                outer_record = record if box_area >= existing_area else existing_record
                outer_record["bbox_source"] = _box_record(outer_box)
                outer_record["corners_source"] = [
                    [float(outer_box[0]), float(outer_box[1])],
                    [float(outer_box[2]), float(outer_box[1])],
                    [float(outer_box[2]), float(outer_box[3])],
                    [float(outer_box[0]), float(outer_box[3])],
                ]
                outer_record["contour_pair_count"] = (
                    int(existing_record.get("contour_pair_count", 1)) + 1
                )
                outer_record["paired_contour_inset_px"] = round(inset, 3)
                outer_record["stroke"] = {
                    "status": "MEASURED",
                    "width_px": round(inset, 3),
                    "uncertainty_px": 1.0,
                    "method": "PAIRED_NESTED_CONTOURS",
                }
                selected[selected_index] = (outer_box, outer_record)
                merged = True
                break
            if _bbox_iou(box, existing_box) >= float(PARAMETERS["frame_deduplication_iou"]):
                duplicate = True
                break
        if merged or duplicate:
            continue
        selected.append((box, record))
    selected.sort(key=lambda item: (item[0][1], item[0][0], item[0][3], item[0][2]))
    results = [record for _box, record in selected]
    _downgrade_grid_like_cells(results)
    if uncertain_text_zones is not None:
        uncertain = uncertain_text_zones.astype(bool)
        band = max(1, int(PARAMETERS["text_mask_dilation_px"]))
        for record in results:
            box = record["bbox_source"]
            x0, y0, x1, y1 = box["x0"], box["y0"], box["x1"], box["y1"]
            boundary = np.zeros((y1 - y0, x1 - x0), dtype=bool)
            boundary[:band, :] = True
            boundary[-band:, :] = True
            boundary[:, :band] = True
            boundary[:, -band:] = True
            if np.any(uncertain[y0:y1, x0:x1] & boundary):
                record["status"] = "INCONCLUSIVE"
                if "TEXT_DETECTOR_ZONE_OVERLAP" not in record["quality_flags"]:
                    record["quality_flags"].append("TEXT_DETECTOR_ZONE_OVERLAP")
                if "UNRESOLVED_TEXT_DETECTOR_CROSSES_FRAME_EDGE" not in record["reasons"]:
                    record["reasons"].append("UNRESOLVED_TEXT_DETECTOR_CROSSES_FRAME_EDGE")
    for index, record in enumerate(results, start=1):
        record["frame_id"] = f"F{index:04d}"
    return results


def _png_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG", compress_level=9, optimize=False)
    return buffer.getvalue()


def _render_overlay(
    rgb: np.ndarray,
    text_records: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
) -> bytes:
    image = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(image)
    by_id = {str(item["candidate_id"]): item for item in text_records}
    for item in text_records:
        detector = item["detector_bbox"]
        if detector is not None:
            draw.rectangle(
                [
                    detector["x0"],
                    detector["y0"],
                    max(detector["x0"], detector["x1"] - 1),
                    max(detector["y0"], detector["y1"] - 1),
                ],
                outline=(255, 170, 0) if item["status"] == "MEASURED" else (220, 30, 30),
                width=1,
            )
            draw.text(
                (detector["x0"] + 1, detector["y0"] + 1),
                str(item["candidate_id"]),
                fill=(30, 30, 30),
            )
        ink = item["ink_bbox"]
        if ink is not None:
            draw.rectangle(
                [
                    ink["x0"],
                    ink["y0"],
                    max(ink["x0"], ink["x1"] - 1),
                    max(ink["y0"], ink["y1"] - 1),
                ],
                outline=(0, 190, 210),
                width=1,
            )
        baseline = item["baseline"]
        if baseline["status"] == "MEASURED" and ink is not None:
            y = float(baseline["y_source_px"])
            draw.line([(ink["x0"], y), (ink["x1"], y)], fill=(210, 0, 180), width=1)
    for pair in pairs:
        left = by_id[pair["candidate_a_id"]]["ink_bbox"]
        right = by_id[pair["candidate_b_id"]]["ink_bbox"]
        if left is not None and right is not None:
            draw.line(
                [
                    ((left["x0"] + left["x1"]) / 2, (left["y0"] + left["y1"]) / 2),
                    ((right["x0"] + right["x1"]) / 2, (right["y0"] + right["y1"]) / 2),
                ],
                fill=(30, 80, 240),
                width=1,
            )
    for frame in frames:
        box = frame["bbox_source"]
        draw.rectangle(
            [box["x0"], box["y0"], max(box["x0"], box["x1"] - 1), max(box["y0"], box["y1"] - 1)],
            outline=(0, 150, 60),
            width=2,
        )
        draw.text((box["x0"] + 2, box["y0"] + 2), frame["frame_id"], fill=(0, 100, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=9, optimize=False)
    return buffer.getvalue()


def _artifact_record(
    relative_path: str,
    payload: bytes,
    *,
    width: int,
    height: int,
    encoding: str,
) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "size_bytes": len(payload),
        "sha256": sha256_bytes(payload),
        "media_type": "image/png",
        "width_px": width,
        "height_px": height,
        "encoding": encoding,
    }


def _atomic_write_fresh(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is an atomic create-if-absent operation on the
        # supported local filesystems.  Unlike rename(), it cannot replace a
        # raced destination on POSIX or Windows.
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def build_geometry_manifest(
    *,
    source_path: Path,
    ocr_path: Path,
    receipt_path: Path,
    require_isolated_runtime: bool = True,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    script_bytes_before = Path(__file__).resolve().read_bytes()
    schema_bytes_before = SCHEMA_PATH.resolve().read_bytes()
    ocr_schema_bytes_before = OCR_SCHEMA_PATH.resolve().read_bytes()
    host_schema_bytes_before = HOST_RECEIPT_SCHEMA_PATH.resolve().read_bytes()
    source_bytes_before = source_path.read_bytes()
    ocr_bytes_before = ocr_path.read_bytes()
    receipt_bytes_before = receipt_path.read_bytes()
    _rgb_image, rgb, ocr, receipt, source = _validate_inputs(
        source_path,
        ocr_path,
        receipt_path,
        source_bytes=source_bytes_before,
        ocr_bytes=ocr_bytes_before,
        receipt_bytes=receipt_bytes_before,
        require_isolated_runtime=require_isolated_runtime,
    )
    cv2.setNumThreads(1)
    cv2.setRNGSeed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    candidates = ocr["text_candidates"]
    identifiers = [str(item["candidate_id"]) for item in candidates]
    if len(set(identifiers)) != len(identifiers) or any(
        ID_PATTERN.fullmatch(value) is None for value in identifiers
    ):
        raise GeometryContractError("OCR candidate IDs must be unique canonical T identifiers")
    if len(candidates) > np.iinfo(np.uint16).max - 1:
        raise GeometryContractError("too many OCR candidates for uint16 label atlas")

    height, width = rgb.shape[:2]
    label_atlas = np.zeros((height, width), dtype=np.uint16)
    ambiguity_atlas = np.zeros((height, width), dtype=np.uint8)
    text_union = np.zeros((height, width), dtype=np.uint8)
    uncertain_text_zones = np.zeros((height, width), dtype=np.uint8)
    text_records: list[dict[str, Any]] = []
    states: list[CandidateMask] = []
    for index, candidate in enumerate(candidates, start=1):
        record, state, local_ambiguity = _extract_candidate(rgb, candidate, mask_label=index)
        text_records.append(record)
        states.append(state)
        x0, y0, x1, y1 = state.roi_bbox
        if local_ambiguity is not None and x1 > x0 and y1 > y0:
            ambiguity_atlas[y0:y1, x0:x1][local_ambiguity.astype(bool)] = 255
        if state.diagnostic_mask is not None:
            # Suppress only observed foreground, never the full OCR detector
            # rectangle: nearby frame/connector pixels may legitimately pass
            # through the detector box.  _detect_frames applies the fixed text
            # dilation needed for anti-aliased fringes.
            text_union[y0:y1, x0:x1][state.diagnostic_mask] = 1
        else:
            dx0, dy0, dx1, dy1 = state.detector_bbox
            if dx1 > dx0 and dy1 > dy0:
                # This is risk evidence only.  It is never inpainted or erased;
                # a detected frame crossing the unresolved zone is downgraded.
                uncertain_text_zones[dy0:dy1, dx0:dx1] = 1

    _degrade_overlapping_candidate_masks(text_records, states, ambiguity_atlas)
    for state in states:
        if state.mask is None:
            continue
        x0, y0, x1, y1 = state.roi_bbox
        assert state.label is not None
        region = label_atlas[y0:y1, x0:x1]
        overlap = state.mask & (region != 0)
        if np.any(overlap):
            raise RuntimeError("candidate mask overlap survived ownership degradation")
        region[state.mask] = np.uint16(state.label)

    neighbor_pairs = _build_neighbor_pairs(states)
    frame_candidates = _detect_frames(rgb, text_union, uncertain_text_zones)
    measured_ink = sum(item["status"] == "MEASURED" for item in text_records)
    baseline_count = sum(item["baseline"]["status"] == "MEASURED" for item in text_records)
    measured_frames = sum(item["status"] == "MEASURED" for item in frame_candidates)
    status = (
        "GEOMETRY_OBSERVATIONS_READY"
        if measured_ink or measured_frames
        else "GEOMETRY_INCONCLUSIVE"
    )
    degradations: list[str] = []
    if measured_ink < len(text_records):
        degradations.append("ONE_OR_MORE_TEXT_INK_OBSERVATIONS_INCONCLUSIVE")
    if measured_frames == 0:
        degradations.append("NO_MEASURED_FRAME_OBSERVATIONS")
    if status == "GEOMETRY_INCONCLUSIVE":
        degradations.append("NO_MEASURED_GEOMETRY_OBSERVATIONS")

    atlas_bytes = _png_bytes(label_atlas)
    ambiguity_bytes = _png_bytes(ambiguity_atlas)
    overlay_bytes = _render_overlay(rgb, text_records, neighbor_pairs, frame_candidates)
    artifacts = {
        "overlay": _artifact_record(
            "geometry-overlay.png", overlay_bytes, width=width, height=height, encoding="rgb8_png"
        ),
        "label_atlas": {
            **_artifact_record(
                "geometry-label-atlas.png",
                atlas_bytes,
                width=width,
                height=height,
                encoding="uint16_label_png",
            ),
            "background_label": 0,
        },
        "ambiguity_mask": {
            **_artifact_record(
                "geometry-ambiguity-mask.png",
                ambiguity_bytes,
                width=width,
                height=height,
                encoding="uint8_binary_png",
            ),
            "ambiguous_value": 255,
        },
    }

    runtime = receipt["runtime"]
    ocr_binding = _payload_binding(ocr_path, ocr_bytes_before)
    receipt_binding = _payload_binding(receipt_path, receipt_bytes_before)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": ocr["run_id"],
        "created_at_utc": ocr["created_at_utc"],
        "status": status,
        "mode": "observation_only",
        "degradations": degradations,
        "coordinate_system": {
            "origin": "TOP_LEFT",
            "unit": "SOURCE_PIXEL",
            "box_convention": "HALF_OPEN_X0_Y0_X1_Y1",
            "pixel_distance_reference": "PIXEL_CENTER_EUCLIDEAN",
        },
        "policy": {
            "promotion_allowed": False,
            "human_review_required": True,
            "ocr_text_is_ground_truth": False,
            "ink_bottom_alignment_is_font_baseline": False,
            "frame_semantics_verified": False,
            "arrow_detection_performed": False,
        },
        "source": source,
        "inputs": {
            "ocr_manifest": {
                **ocr_binding,
                "schema_version": ocr["schema_version"],
                "run_id": ocr["run_id"],
                "source_sha256": str(ocr["source"]["sha256"]).upper(),
            },
            "host_runtime_receipt": {
                **receipt_binding,
                "schema_version": receipt["schema_version"],
                "status": receipt["status"],
                "context": {
                    "run_id": receipt["context"]["run_id"],
                    "source_sha256": str(receipt["context"]["source_sha256"]).upper(),
                },
                "runtime": {
                    "runtime_id": runtime["runtime_id"],
                    "python_executable": runtime["python_executable"],
                    "python_version": runtime["python_version"],
                },
            },
        },
        "implementation": {
            "algorithm_id": ALGORITHM_ID,
            "version": ALGORITHM_VERSION,
            "random_seed": RANDOM_SEED,
            "parameters": dict(PARAMETERS),
            "script": _file_binding(Path(__file__), relative_to=PROJECT_ROOT),
            "schema": _file_binding(SCHEMA_PATH, relative_to=PROJECT_ROOT),
        },
        "runtime": {
            "runtime_id": runtime["runtime_id"],
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": platform.python_version(),
            "isolated": bool(sys.flags.isolated),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "scipy": importlib.metadata.version("scipy"),
            "scikit_image": importlib.metadata.version("scikit-image"),
            "pillow": importlib.metadata.version("Pillow"),
        },
        "text_geometry": text_records,
        "neighbor_pairs": neighbor_pairs,
        "frame_candidates": frame_candidates,
        "summary": {
            "candidate_count": len(text_records),
            "measured_ink_count": measured_ink,
            "inconclusive_ink_count": len(text_records) - measured_ink,
            "reliable_ink_bottom_alignment_count": baseline_count,
            "neighbor_pair_count": len(neighbor_pairs),
            "frame_candidate_count": len(frame_candidates),
            "measured_frame_count": measured_frames,
            "ambiguous_pixel_count": int(np.count_nonzero(ambiguity_atlas == 255)),
            "degradations": degradations,
        },
        "artifacts": artifacts,
    }
    _validate_json_schema(manifest, SCHEMA_PATH, label="geometry manifest")
    if (
        Path(__file__).resolve().read_bytes() != script_bytes_before
        or SCHEMA_PATH.resolve().read_bytes() != schema_bytes_before
        or OCR_SCHEMA_PATH.resolve().read_bytes() != ocr_schema_bytes_before
        or HOST_RECEIPT_SCHEMA_PATH.resolve().read_bytes() != host_schema_bytes_before
    ):
        raise GeometryContractError("script or schema changed during geometry processing")
    _require_unchanged(source_path, source_bytes_before, label="source PNG")
    _require_unchanged(ocr_path, ocr_bytes_before, label="OCR manifest")
    _require_unchanged(receipt_path, receipt_bytes_before, label="host runtime receipt")
    for key, binding in receipt["bindings"].items():
        _verify_binding(binding, label=f"host receipt binding {key}")
    return manifest, {
        "geometry-overlay.png": overlay_bytes,
        "geometry-label-atlas.png": atlas_bytes,
        "geometry-ambiguity-mask.png": ambiguity_bytes,
    }


def run_geometry_refinement(
    *,
    source_path: Path,
    ocr_manifest_path: Path,
    host_runtime_receipt_path: Path,
    output_dir: Path,
    project_root: Path = PROJECT_ROOT,
    require_isolated_runtime: bool = True,
) -> tuple[Path, dict[str, Any]]:
    source = _require_file(source_path, label="input PNG")
    ocr = _require_file(ocr_manifest_path, label="OCR manifest")
    receipt = _require_file(host_runtime_receipt_path, label="host runtime receipt")
    authorized_output = resolve_output_path(output_dir, project_root=project_root)
    canonical_names = (
        "geometry-manifest.json",
        "geometry-overlay.png",
        "geometry-label-atlas.png",
        "geometry-ambiguity-mask.png",
    )
    existing = [name for name in canonical_names if (authorized_output / name).exists()]
    if existing:
        raise GeometryContractError(
            f"refusing to reuse geometry output files; create a fresh run: {existing}"
        )
    manifest, payloads = build_geometry_manifest(
        source_path=source,
        ocr_path=ocr,
        receipt_path=receipt,
        require_isolated_runtime=require_isolated_runtime,
    )
    manifest_bytes = _json_bytes(manifest)
    authorized_output.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    manifest_path = authorized_output / "geometry-manifest.json"
    try:
        for name, payload in payloads.items():
            destination = authorized_output / name
            _atomic_write_fresh(destination, payload)
            created.append(destination)
        _verify_current_record(source, manifest["source"], label="source PNG")
        _verify_current_record(
            ocr,
            manifest["inputs"]["ocr_manifest"],
            label="OCR manifest",
        )
        _verify_current_record(
            receipt,
            manifest["inputs"]["host_runtime_receipt"],
            label="host runtime receipt",
        )
        _atomic_write_fresh(manifest_path, manifest_bytes)
        created.append(manifest_path)
    except Exception:
        for destination in reversed(created):
            destination.unlink(missing_ok=True)
        raise
    return manifest_path, manifest


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-manifest",
        help="Read-only strict validation of an existing geometry manifest",
    )
    parser.add_argument("--input", help="Frozen source PNG")
    parser.add_argument("--ocr-manifest", help="Bound OCR perception manifest")
    parser.add_argument("--host-runtime-receipt", help="PASS host runtime receipt")
    parser.add_argument("--output-dir", help="Geometry stage output directory")
    parser.add_argument(
        "--project-root", default=str(PROJECT_ROOT), help="Project root for output policy"
    )
    args = parser.parse_args(argv)
    generation_fields = ("input", "ocr_manifest", "host_runtime_receipt", "output_dir")
    supplied = [field for field in generation_fields if getattr(args, field) is not None]
    if args.verify_manifest is not None:
        if supplied:
            parser.error("--verify-manifest cannot be combined with generation arguments")
    else:
        missing = [field.replace("_", "-") for field in generation_fields if field not in supplied]
        if missing:
            parser.error("generation mode requires " + ", ".join(f"--{field}" for field in missing))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.verify_manifest is not None:
        try:
            manifest = verify_geometry_manifest_file(Path(args.verify_manifest))
        except (GeometryContractError, OutputPolicyError) as exc:
            print(f"GEOMETRY_CONTRACT_REJECTED: {exc}", file=sys.stderr)
            return EXIT_CONTRACT_REJECTED
        except Exception as exc:
            print(f"GEOMETRY_INTERNAL_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        print(f"GEOMETRY_MANIFEST_VERIFIED: {Path(args.verify_manifest).resolve()}")
        return EXIT_OK
    try:
        manifest_path, manifest = run_geometry_refinement(
            source_path=Path(args.input),
            ocr_manifest_path=Path(args.ocr_manifest),
            host_runtime_receipt_path=Path(args.host_runtime_receipt),
            output_dir=Path(args.output_dir),
            project_root=Path(args.project_root),
            require_isolated_runtime=True,
        )
    except (GeometryContractError, OutputPolicyError) as exc:
        print(f"GEOMETRY_CONTRACT_REJECTED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_REJECTED
    except Exception as exc:
        print(f"GEOMETRY_INTERNAL_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    print(f"{manifest['status']}: {manifest_path}")
    return EXIT_OK if manifest["status"] == "GEOMETRY_OBSERVATIONS_READY" else EXIT_INCONCLUSIVE


if __name__ == "__main__":
    raise SystemExit(main())
