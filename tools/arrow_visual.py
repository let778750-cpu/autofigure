"""Reference-bound physical visual gates for arrowheads and obstacle clearance.

ArrowSpec/OOXML readback proves what PowerPoint stored.  It cannot prove that a
stored ``sm``/``med``/``lg`` token has the physical size seen in the reference,
or that a head did not move into a neighbouring object.  This module closes
that gap with hash-bound pixel evidence while leaving the OOXML gate unchanged.

Contracts are backend-neutral JSON and may live in ``regions.json`` as
``arrow_visual_contracts`` (or on one region as ``arrow_visual_contract``), or
in an edge's ``arrow_spec.visual_contract``.  Coordinates are supplied by the
case; this module contains no case-specific geometry.
"""

from __future__ import annotations

import copy
import colorsys
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, label

from tools import common
from tools.contracts import SCHEMA_VERSION, read_json, write_json

DEFAULT_GEOMETRY_TOLERANCE_PX = 1.5
DEFAULT_DECLARATION_TOLERANCE_PX = 1.0
MASK_IOU_FLOOR = 0.75
MAX_ARROW_AREA_RELATIVE_TOLERANCE = 0.20
_EXPECTATION_UNSET = object()


def _contract_sha256(contract: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in contract.items()
        if not key.startswith("_document_")
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bounds(bbox: Any, shape: tuple[int, ...], *, label_text: str) -> tuple[int, int, int, int]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"{label_text} must be [x, y, width, height]")
    try:
        x, y, width, height = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label_text} must contain finite numbers") from exc
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        raise ValueError(f"{label_text} must contain finite numbers")
    if width <= 0 or height <= 0:
        raise ValueError(f"{label_text} width and height must be positive")
    x0, y0 = math.floor(x), math.floor(y)
    x1, y1 = math.ceil(x + width), math.ceil(y + height)
    if x0 < 0 or y0 < 0 or x1 > shape[1] or y1 > shape[0]:
        raise ValueError(f"{label_text} lies outside the image canvas")
    return x0, y0, x1, y1


def _bbox_union(bounds: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _geometry(mask: np.ndarray, bbox: tuple[int, int, int, int] | None = None) -> dict | None:
    working = mask
    offset_x = 0
    offset_y = 0
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        working = mask[y0:y1, x0:x1]
        offset_x, offset_y = x0, y0
    rows, columns = np.nonzero(working)
    if not len(rows):
        return None
    left = int(columns.min()) + offset_x
    top = int(rows.min()) + offset_y
    right = int(columns.max()) + offset_x
    bottom = int(rows.max()) + offset_y
    return {
        "bbox": [left, top, right - left + 1, bottom - top + 1],
        "edges": [left, top, right, bottom],
        "area_px": int(working.sum()),
    }


def _fuzzy_mask_iou(reference: np.ndarray, render: np.ndarray) -> float:
    reference_count = int(reference.sum())
    render_count = int(render.sum())
    if reference_count == 0 and render_count == 0:
        return 1.0
    if reference_count == 0 or render_count == 0:
        return 0.0
    reference_match = int(
        np.logical_and(reference, binary_dilation(render)).sum()
    )
    render_match = int(
        np.logical_and(render, binary_dilation(reference)).sum()
    )
    intersection = float(min(reference_match, render_match))
    union = reference_count + render_count - intersection
    return float(intersection / union) if union > 0 else 1.0


def _exact_mask_iou(reference: np.ndarray, render: np.ndarray) -> float:
    """Return exact IoU after placing two already-normalized masks on one canvas."""

    height = max(reference.shape[0], render.shape[0])
    width = max(reference.shape[1], render.shape[1])
    reference_canvas = np.zeros((height, width), dtype=bool)
    render_canvas = np.zeros((height, width), dtype=bool)
    reference_canvas[: reference.shape[0], : reference.shape[1]] = reference
    render_canvas[: render.shape[0], : render.shape[1]] = render
    intersection = int(np.logical_and(reference_canvas, render_canvas).sum())
    union = int(np.logical_or(reference_canvas, render_canvas).sum())
    return float(intersection / union) if union else 1.0


def _tight_normalized_mask(mask: np.ndarray) -> np.ndarray:
    """Remove translation while preserving the exact raster silhouette."""

    rows, columns = np.nonzero(mask)
    if not len(rows):
        return np.zeros((0, 0), dtype=bool)
    return mask[
        int(rows.min()) : int(rows.max()) + 1,
        int(columns.min()) : int(columns.max()) + 1,
    ]


def _canonical_orientation_metrics(
    reference: np.ndarray,
    render: np.ndarray,
) -> dict[str, float | bool]:
    """Compare an asymmetric head against its direct and 180-degree alternatives."""

    reference_tight = _tight_normalized_mask(reference)
    render_tight = _tight_normalized_mask(render)
    if not reference_tight.size or not render_tight.size:
        return {
            "observable": False,
            "direct_iou": 0.0,
            "opposite_iou": 0.0,
            "self_opposite_iou": 1.0,
            "margin": 0.0,
            "pass": False,
        }
    height = max(reference_tight.shape[0], render_tight.shape[0])
    width = max(reference_tight.shape[1], render_tight.shape[1])
    reference_canvas = np.zeros((height, width), dtype=bool)
    render_canvas = np.zeros((height, width), dtype=bool)
    reference_canvas[: reference_tight.shape[0], : reference_tight.shape[1]] = (
        reference_tight
    )
    render_canvas[: render_tight.shape[0], : render_tight.shape[1]] = render_tight
    direct_iou = _exact_mask_iou(reference_canvas, render_canvas)
    opposite_iou = _exact_mask_iou(reference_canvas, np.rot90(render_canvas, 2))
    self_opposite_iou = _exact_mask_iou(
        reference_canvas, np.rot90(reference_canvas, 2)
    )
    observable = 1.0 - self_opposite_iou >= 0.10
    margin = direct_iou - opposite_iou
    passed = not observable or (direct_iou >= 0.55 and margin >= 0.10)
    return {
        "observable": observable,
        "direct_iou": direct_iou,
        "opposite_iou": opposite_iou,
        "self_opposite_iou": self_opposite_iou,
        "margin": margin,
        "pass": passed,
    }


def _head_taper(
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    outward_angle_deg: float,
) -> float | None:
    """Measure whether a head narrows toward its ArrowSpec-directed tip."""

    x0, y0, x1, y1 = bbox
    rows, columns = np.nonzero(mask[y0:y1, x0:x1])
    if not len(rows) or not math.isfinite(outward_angle_deg):
        return None
    radians = math.radians(outward_angle_deg)
    outward = np.array([math.cos(radians), math.sin(radians)], dtype=float)
    normal = np.array([-outward[1], outward[0]], dtype=float)
    points = np.column_stack((columns.astype(float) + x0, rows.astype(float) + y0))
    along = points @ outward
    across = points @ normal
    extent = float(along.max() - along.min())
    if extent <= 0:
        return None
    inner = across[along <= along.min() + 0.25 * extent]
    outer = across[along >= along.max() - 0.25 * extent]
    if not len(inner) or not len(outer):
        return None
    pixel_support = abs(normal[0]) + abs(normal[1])
    inner_span = float(inner.max() - inner.min() + pixel_support)
    outer_span = float(outer.max() - outer.min() + pixel_support)
    denominator = max(inner_span, outer_span)
    if denominator <= 0:
        return None
    return float((inner_span - outer_span) / denominator)


def _geometry_from_bbox(bbox: Any) -> dict:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("expected bbox must be [x, y, width, height]")
    try:
        x, y, width, height = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError("expected bbox must contain finite numbers") from exc
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        raise ValueError("expected bbox must contain finite numbers")
    if width <= 0 or height <= 0:
        raise ValueError("expected bbox width and height must be positive")
    return {
        "bbox": [x, y, width, height],
        "edges": [x, y, x + width - 1.0, y + height - 1.0],
        "area_px": None,
    }


def _border_background(reference: np.ndarray, bbox: tuple[int, int, int, int]) -> list[float]:
    x0, y0, x1, y1 = bbox
    crop = reference[y0:y1, x0:x1, :3]
    border = np.concatenate((crop[0], crop[-1], crop[:, 0], crop[:, -1]), axis=0)
    return [float(value) for value in np.median(border.astype(float), axis=0)]


def _foreground_mask(
    image: np.ndarray,
    mask_contract: dict[str, Any],
    *,
    resolved_background: list[float] | None,
) -> np.ndarray:
    tolerance = float(mask_contract.get("tolerance", 16.0))
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("mask tolerance must be a nonnegative finite number")
    mode = mask_contract.get("mode")
    if mode is None:
        mode = "rgb" if "foreground_rgb" in mask_contract else "background_delta"
    pixels = image[..., :3].astype(float)
    if mode == "rgb":
        color = np.asarray(mask_contract.get("foreground_rgb"), dtype=float)
        if color.shape != (3,) or not np.all(np.isfinite(color)):
            raise ValueError("rgb mask requires foreground_rgb with three finite values")
        return np.max(np.abs(pixels - color), axis=2) <= tolerance
    if mode == "hsv":
        color = np.asarray(mask_contract.get("foreground_rgb"), dtype=float)
        if color.shape != (3,) or not np.all(np.isfinite(color)):
            raise ValueError("hsv mask requires foreground_rgb with three finite values")
        if np.any(color < 0) or np.any(color > 255):
            raise ValueError("hsv foreground_rgb values must lie within [0, 255]")
        try:
            hue_tolerance = float(mask_contract.get("hue_tolerance_deg", 18.0))
            saturation_min = float(mask_contract.get("saturation_min", 0.08))
            value_min = float(mask_contract.get("value_min", 0.0))
            value_max = float(mask_contract.get("value_max", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("hsv mask limits must be numeric") from exc
        if not 0 <= hue_tolerance <= 180:
            raise ValueError("hsv hue_tolerance_deg must lie within [0, 180]")
        if not 0 <= saturation_min <= 1 or not 0 <= value_min <= value_max <= 1:
            raise ValueError("hsv saturation/value limits must lie within [0, 1]")
        target_hue = colorsys.rgb_to_hsv(*(color / 255.0))[0] * 360.0
        hsv = np.asarray(
            Image.fromarray(image[..., :3].astype(np.uint8), mode="RGB").convert("HSV"),
            dtype=float,
        )
        hue = hsv[..., 0] * (360.0 / 255.0)
        saturation = hsv[..., 1] / 255.0
        value = hsv[..., 2] / 255.0
        hue_distance = np.abs((hue - target_hue + 180.0) % 360.0 - 180.0)
        return (
            (hue_distance <= hue_tolerance)
            & (saturation >= saturation_min)
            & (value >= value_min)
            & (value <= value_max)
        )
    if mode == "background_delta":
        color_value = mask_contract.get("background_rgb", resolved_background)
        color = np.asarray(color_value, dtype=float)
        if color.shape != (3,) or not np.all(np.isfinite(color)):
            raise ValueError(
                "background_delta mask requires background_rgb or a derivable background"
            )
        return np.max(np.abs(pixels - color), axis=2) > tolerance
    raise ValueError("mask mode must be rgb, hsv, or background_delta")


def _arrow_component(
    candidate: np.ndarray,
    seed_point: tuple[int, int],
    seed_radius: int,
    measurement_bbox: tuple[int, int, int, int],
) -> tuple[np.ndarray, int]:
    """Keep the unique foreground component touching a shaft seed point."""

    x0, y0, x1, y1 = measurement_bbox
    local = candidate[y0:y1, x0:x1]
    components, _ = label(local, structure=np.ones((3, 3), dtype=np.uint8))
    seed_x, seed_y = seed_point
    sx0 = max(x0, seed_x - seed_radius)
    sy0 = max(y0, seed_y - seed_radius)
    sx1 = min(x1, seed_x + seed_radius + 1)
    sy1 = min(y1, seed_y + seed_radius + 1)
    seed_labels = np.unique(
        components[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0]
    )
    seed_labels = seed_labels[seed_labels != 0]
    selected = np.zeros_like(candidate, dtype=bool)
    if len(seed_labels) == 1:
        selected[y0:y1, x0:x1] = np.isin(components, seed_labels)
    return selected, int(len(seed_labels))


def _seed_point(
    value: Any,
    shape: tuple[int, ...],
    measurement_bbox: tuple[int, int, int, int],
    *,
    label_text: str,
) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label_text} must be [x, y]")
    try:
        x, y = (int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label_text} must contain integer-like values") from exc
    x0, y0, x1, y1 = measurement_bbox
    if x < 0 or y < 0 or x >= shape[1] or y >= shape[0]:
        raise ValueError(f"{label_text} lies outside the image canvas")
    if not (x0 <= x < x1 and y0 <= y < y1):
        raise ValueError(f"{label_text} lies outside the measurement bbox")
    return x, y


def _dimensions(geometry: dict, axis: str) -> tuple[float, float]:
    width = float(geometry["bbox"][2])
    height = float(geometry["bbox"][3])
    if axis == "horizontal":
        return height, width
    if axis == "vertical":
        return width, height
    raise ValueError("axis must be horizontal or vertical")


def _mask_dimensions(
    mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    axis: str,
    axis_angle_deg: Any = None,
) -> tuple[float, float]:
    if axis in {"horizontal", "vertical"}:
        geometry = _geometry(mask, bbox)
        if geometry is None:
            raise ValueError("head mask is empty")
        return _dimensions(geometry, axis)
    if axis != "angle":
        raise ValueError("axis must be horizontal, vertical, or angle")
    try:
        angle = float(axis_angle_deg)
    except (TypeError, ValueError) as exc:
        raise ValueError("angle axis requires a finite axis_angle_deg") from exc
    if not math.isfinite(angle):
        raise ValueError("angle axis requires a finite axis_angle_deg")
    x0, y0, x1, y1 = bbox
    rows, columns = np.nonzero(mask[y0:y1, x0:x1])
    if not len(rows):
        raise ValueError("head mask is empty")
    radians = math.radians(angle)
    tangent = np.array([math.cos(radians), math.sin(radians)], dtype=float)
    normal = np.array([-tangent[1], tangent[0]], dtype=float)
    points = np.column_stack((columns.astype(float) + x0, rows.astype(float) + y0))
    along = points @ tangent
    across = points @ normal
    pixel_support = abs(tangent[0]) + abs(tangent[1])
    length = float(along.max() - along.min() + pixel_support)
    width = float(across.max() - across.min() + pixel_support)
    return width, length


def _shaft_width_at_seed(
    mask: np.ndarray,
    seed: tuple[int, int],
    *,
    axis: str,
    axis_angle_deg: Any,
    probe_half_length_px: float,
) -> float:
    if axis == "horizontal":
        angle = 0.0
    elif axis == "vertical":
        angle = 90.0
    elif axis == "angle":
        try:
            angle = float(axis_angle_deg)
        except (TypeError, ValueError) as exc:
            raise ValueError("angle shaft requires shaft_axis_angle_deg") from exc
    else:
        raise ValueError("axis must be horizontal, vertical, or angle")
    if not math.isfinite(angle) or not math.isfinite(probe_half_length_px):
        raise ValueError("shaft probe values must be finite")
    if probe_half_length_px <= 0:
        raise ValueError("shaft_probe_half_length_px must be positive")
    rows, columns = np.nonzero(mask)
    if not len(rows):
        raise ValueError("shaft mask is empty")
    radians = math.radians(angle)
    tangent = np.array([math.cos(radians), math.sin(radians)], dtype=float)
    normal = np.array([-tangent[1], tangent[0]], dtype=float)
    offsets = np.column_stack(
        (columns.astype(float) - seed[0], rows.astype(float) - seed[1])
    )
    along = offsets @ tangent
    across = offsets @ normal
    selected = across[np.abs(along) <= probe_half_length_px]
    if not len(selected):
        raise ValueError("shaft probe contains no foreground")
    pixel_support = abs(normal[0]) + abs(normal[1])
    return float(selected.max() - selected.min() + pixel_support)


def _edge_errors(expected: dict, actual: dict) -> list[float]:
    return [
        abs(float(left) - float(right))
        for left, right in zip(expected["edges"], actual["edges"], strict=True)
    ]


def _obstacle_metrics(
    arrow_mask: np.ndarray,
    obstacle_bbox: tuple[int, int, int, int],
) -> dict[str, float | int | None]:
    x0, y0, x1, y1 = obstacle_bbox
    intersection = int(arrow_mask[y0:y1, x0:x1].sum())
    rows, columns = np.nonzero(arrow_mask)
    if not len(rows):
        return {"intersection_pixels": intersection, "clearance_px": None}
    # Treat pixels and the obstacle as unit cells.  Adjacent filled cells have
    # zero net whitespace clearance; one empty pixel column yields one pixel.
    dx = np.maximum.reduce(
        (
            np.full(columns.shape, x0, dtype=float) - (columns.astype(float) + 1.0),
            columns.astype(float) - float(x1) + 1.0,
            np.zeros(columns.shape, dtype=float),
        )
    )
    dy = np.maximum.reduce(
        (
            np.full(rows.shape, y0, dtype=float) - (rows.astype(float) + 1.0),
            rows.astype(float) - float(y1) + 1.0,
            np.zeros(rows.shape, dtype=float),
        )
    )
    clearance = 0.0 if intersection else float(np.min(np.hypot(dx, dy)))
    return {"intersection_pixels": intersection, "clearance_px": round(clearance, 4)}


def _blocker(blockers: list[str], contract_id: str, suffix: str) -> None:
    blockers.append(f"arrow-visual:{contract_id}:{suffix}")


def _head_inventory(contracts: list[dict[str, Any]]) -> tuple[dict[str, set[str]], list[str]]:
    """Return the exact logical-arrow/head inventory declared by contracts."""

    inventory: dict[str, set[str]] = {}
    invalid: list[str] = []
    for index, contract in enumerate(contracts, start=1):
        if not isinstance(contract, dict):
            continue
        element_id = str(contract.get("element_id") or contract.get("id") or "")
        heads = contract.get("heads")
        if not isinstance(heads, dict):
            continue
        unknown = sorted(set(heads) - {"start", "end"})
        if unknown:
            invalid.append(element_id or f"contract-{index}")
        sides = {side for side in ("start", "end") if side in heads}
        if element_id in inventory:
            invalid.append(element_id)
        inventory[element_id] = sides
    return inventory, sorted(set(invalid))


def _audit_expectation(
    expectation: Any,
    contracts: list[dict[str, Any]],
    required_heads: dict[str, set[str]] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Fail closed on the exact logical-arrow and head-side inventory."""

    if expectation is _EXPECTATION_UNSET:
        if required_heads:
            return {
                "pass": False,
                "error": "arrow_visual_expectation is required for scene arrowheads",
            }, ["arrow-visual:expectation:missing"]
        return None, []
    if expectation is None:
        return {
            "pass": False,
            "error": "arrow_visual_expectation must not be null",
        }, ["arrow-visual:expectation:invalid"]
    blockers: list[str] = []
    if not isinstance(expectation, dict):
        return {
            "pass": False,
            "error": "arrow_visual_expectation must be an object",
        }, ["arrow-visual:expectation:invalid"]
    expected_contracts = expectation.get("contracts")
    expected_count = expectation.get("count")
    exemption_records = expectation.get("exemptions", [])
    if not isinstance(exemption_records, list) or any(
        not isinstance(value, dict) for value in exemption_records
    ):
        return {
            "pass": False,
            "error": "arrow visual exemptions must be a list of objects",
        }, ["arrow-visual:expectation:invalid-exemptions"]
    exemptions: dict[str, set[str]] = {}
    exemption_details: list[dict[str, Any]] = []
    for item in exemption_records:
        element_id = item.get("element_id")
        sides = item.get("head_sides")
        reason = item.get("reason")
        parent_object_id = item.get("parent_object_id")
        if (
            not isinstance(element_id, str)
            or not element_id
            or element_id in exemptions
            or not isinstance(sides, list)
            or not sides
            or any(side not in {"start", "end"} for side in sides)
            or len(sides) != len(set(sides))
            or reason != "embedded_plot_axis"
            or not isinstance(parent_object_id, str)
            or not parent_object_id
        ):
            return {
                "pass": False,
                "error": (
                    "each exemption needs a unique element_id, valid head_sides, "
                    "reason=embedded_plot_axis, and parent_object_id"
                ),
            }, ["arrow-visual:expectation:invalid-exemptions"]
        exemptions[element_id] = set(sides)
        exemption_details.append(dict(item))
    if (
        expected_count == 0
        and expected_contracts == []
        and not contracts
    ):
        scene_mismatches: list[str] = []
        if required_heads is not None:
            scene_mismatches = sorted(
                set(exemptions) ^ set(required_heads)
                | {
                    element_id
                    for element_id in set(exemptions) & set(required_heads)
                    if exemptions[element_id] != required_heads[element_id]
                }
            )
        zero_blockers = (
            ["arrow-visual:expectation:scene-arrow-spec-mismatch"]
            if scene_mismatches
            else []
        )
        return {
            "expected_count": 0,
            "actual_count": 0,
            "expected_element_ids": [],
            "actual_element_ids": [],
            "expected_heads": {},
            "actual_heads": {},
            "exemptions": exemption_details,
            "exempt_element_ids": list(exemptions),
            "scene_arrow_spec_mismatches": scene_mismatches,
            "pass": not zero_blockers,
        }, zero_blockers
    if (
        not isinstance(expected_contracts, list)
        or not expected_contracts
        or any(not isinstance(value, dict) for value in expected_contracts)
        or not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count <= 0
        or expected_count != len(expected_contracts)
    ):
        return {
            "pass": False,
            "error": (
                "arrow_visual_expectation requires a positive count and the same "
                "number of logical-arrow contract objects"
            ),
        }, ["arrow-visual:expectation:invalid"]

    expected: dict[str, set[str]] = {}
    expected_hashes: dict[str, str] = {}
    for item in expected_contracts:
        element_id = item.get("element_id")
        sides = item.get("head_sides")
        contract_hash = item.get("contract_sha256")
        if (
            not isinstance(element_id, str)
            or not element_id
            or element_id in expected
            or not isinstance(sides, list)
            or not sides
            or any(side not in {"start", "end"} for side in sides)
            or len(sides) != len(set(sides))
            or not isinstance(contract_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", contract_hash)
        ):
            return {
                "pass": False,
                "error": (
                    "every arrow visual expectation needs a unique nonempty "
                    "element_id, unique head_sides drawn from start/end, and a "
                    "canonical contract_sha256"
                ),
            }, ["arrow-visual:expectation:invalid"]
        expected[element_id] = set(sides)
        expected_hashes[element_id] = contract_hash

    if set(expected) & set(exemptions):
        return {
            "pass": False,
            "error": "an arrow cannot be both visually contracted and exempt",
        }, ["arrow-visual:expectation:invalid-exemptions"]

    actual, invalid_contract_ids = _head_inventory(contracts)
    expected_ids = list(expected)
    actual_ids = list(actual)
    missing_ids = sorted(set(expected) - set(actual))
    unexpected_ids = sorted(set(actual) - set(expected))
    side_mismatches = sorted(
        element_id
        for element_id in set(expected) & set(actual)
        if expected[element_id] != actual[element_id]
    )
    actual_hashes = {
        str(item.get("element_id") or item.get("id") or ""): _contract_sha256(item)
        for item in contracts
        if isinstance(item, dict)
    }
    hash_mismatches = sorted(
        element_id
        for element_id in set(expected_hashes) & set(actual_hashes)
        if expected_hashes[element_id] != actual_hashes[element_id]
    )
    if len(contracts) != expected_count:
        blockers.append("arrow-visual:expectation:count-mismatch")
    if missing_ids:
        blockers.append("arrow-visual:expectation:missing-elements")
    if unexpected_ids:
        blockers.append("arrow-visual:expectation:unexpected-elements")
    if invalid_contract_ids:
        blockers.append("arrow-visual:expectation:invalid-contract-heads")
    if side_mismatches:
        blockers.append("arrow-visual:expectation:head-side-mismatch")
    if hash_mismatches:
        blockers.append("arrow-visual:expectation:contract-hash-mismatch")
    scene_mismatches: list[str] = []
    if required_heads is not None:
        covered_heads = {**expected, **exemptions}
        scene_mismatches = sorted(
            set(covered_heads) ^ set(required_heads)
            | {
                element_id
                for element_id in set(covered_heads) & set(required_heads)
                if covered_heads[element_id] != required_heads[element_id]
            }
        )
        if scene_mismatches:
            blockers.append("arrow-visual:expectation:scene-arrow-spec-mismatch")
    return {
        "expected_count": expected_count,
        "actual_count": len(contracts),
        "expected_element_ids": expected_ids,
        "actual_element_ids": actual_ids,
        "expected_heads": {key: sorted(value) for key, value in expected.items()},
        "actual_heads": {key: sorted(value) for key, value in actual.items()},
        "expected_contract_sha256": expected_hashes,
        "actual_contract_sha256": actual_hashes,
        "missing_element_ids": missing_ids,
        "unexpected_element_ids": unexpected_ids,
        "head_side_mismatches": side_mismatches,
        "contract_hash_mismatches": hash_mismatches,
        "invalid_contract_head_ids": invalid_contract_ids,
        "exemptions": exemption_details,
        "exempt_element_ids": list(exemptions),
        "scene_arrow_spec_mismatches": scene_mismatches,
        "pass": not blockers,
    }, blockers


def _expected_head(
    *,
    evidence_kind: str,
    reference_mask: np.ndarray,
    search_bbox: tuple[int, int, int, int],
    head_contract: dict[str, Any],
    axis: str,
) -> tuple[dict | None, float | None, float | None, str | None]:
    if evidence_kind == "reference_pixels":
        geometry = _geometry(reference_mask, search_bbox)
        if geometry is None:
            return None, None, None, "reference head region contains no arrow pixels"
        try:
            width, length = _mask_dimensions(
                reference_mask,
                search_bbox,
                axis,
                head_contract.get("axis_angle_deg"),
            )
        except ValueError as exc:
            return None, None, None, str(exc)
        return geometry, width, length, None
    expected = head_contract.get("expected")
    if not isinstance(expected, dict):
        return None, None, None, "explicit head evidence requires expected geometry"
    try:
        geometry = _geometry_from_bbox(expected.get("bbox"))
        measured_width, measured_length = _dimensions(geometry, axis)
        width = float(expected.get("width_px", measured_width))
        length = float(expected.get("length_px", measured_length))
    except (TypeError, ValueError) as exc:
        return None, None, None, str(exc)
    if not all(math.isfinite(value) and value > 0 for value in (width, length)):
        return None, None, None, "expected head width/length must be positive finite numbers"
    return geometry, width, length, None


def _evaluate_head(
    *,
    contract_id: str,
    element_id: str,
    side: str,
    axis: str,
    shaft_width: float,
    evidence_kind: str,
    evidence_trusted: bool,
    head_contract: dict[str, Any],
    reference_mask: np.ndarray,
    render_mask: np.ndarray,
    image_shape: tuple[int, ...],
    blockers: list[str],
    trusted_lengths: dict[str, float],
    trusted_tolerances: dict[str, float],
    scene_direction: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        search_bbox = _bounds(
            head_contract.get("search_bbox"),
            image_shape,
            label_text=f"{contract_id}.{side}.search_bbox",
        )
    except ValueError as exc:
        _blocker(blockers, contract_id, f"{side}:contract")
        return {"side": side, "pass": False, "error": str(exc)}
    expected, expected_width, expected_length, expected_error = _expected_head(
        evidence_kind=evidence_kind,
        reference_mask=reference_mask,
        search_bbox=search_bbox,
        head_contract=head_contract,
        axis=axis,
    )
    if expected_error:
        _blocker(blockers, contract_id, f"{side}:reference-evidence")
        return {"side": side, "pass": False, "error": expected_error}
    try:
        maximum_search_padding = min(
            float(head_contract.get("max_search_padding_px", 4.0)), 4.0
        )
    except (TypeError, ValueError):
        maximum_search_padding = math.nan
    search_paddings = [
        float(expected["edges"][0]) - search_bbox[0],
        float(expected["edges"][1]) - search_bbox[1],
        (search_bbox[2] - 1) - float(expected["edges"][2]),
        (search_bbox[3] - 1) - float(expected["edges"][3]),
    ]
    search_is_tight = not (
        not math.isfinite(maximum_search_padding)
        or maximum_search_padding < 0
        or min(search_paddings) < 0
        or max(search_paddings) > maximum_search_padding
    )
    if not search_is_tight:
        _blocker(blockers, contract_id, f"{side}:head-search-not-tight")
    actual = _geometry(render_mask, search_bbox)
    if actual is None:
        _blocker(blockers, contract_id, f"{side}:missing-render-head")
        return {
            "side": side,
            "search_bbox": list(search_bbox),
            "expected": expected,
            "pass": False,
            "error": "render head region contains no arrow pixels",
        }
    try:
        expected_width, expected_length = _mask_dimensions(
            reference_mask,
            search_bbox,
            axis,
            head_contract.get("axis_angle_deg"),
        )
        actual_width, actual_length = _mask_dimensions(
            render_mask,
            search_bbox,
            axis,
            head_contract.get("axis_angle_deg"),
        )
    except ValueError as exc:
        _blocker(blockers, contract_id, f"{side}:contract")
        return {"side": side, "pass": False, "error": str(exc)}
    try:
        geometry_cap = min(
            2.0,
            max(1.0, math.hypot(image_shape[1], image_shape[0]) * 0.0025),
        )
        bbox_tolerance = min(
            float(
                head_contract.get(
                    "bbox_tolerance_px", DEFAULT_GEOMETRY_TOLERANCE_PX
                )
            ),
            geometry_cap,
        )
        size_tolerance = min(
            float(
                head_contract.get(
                    "size_tolerance_px", DEFAULT_GEOMETRY_TOLERANCE_PX
                )
            ),
            geometry_cap,
        )
        declaration_tolerance = float(
            head_contract.get(
                "declaration_tolerance_px", DEFAULT_DECLARATION_TOLERANCE_PX
            )
        )
        head_mask_floor = 0.65 if shaft_width <= 2.0 else MASK_IOU_FLOOR
        mask_iou_min = max(
            float(head_contract.get("mask_iou_min", head_mask_floor)),
            head_mask_floor,
        )
        minimum_head_ratio = max(
            float(head_contract.get("min_head_to_shaft_ratio", 1.5)), 1.5
        )
    except (TypeError, ValueError):
        _blocker(blockers, contract_id, f"{side}:contract")
        return {"side": side, "pass": False, "error": "head tolerances must be numeric"}
    if any(
        not math.isfinite(value) or value < 0
        for value in (bbox_tolerance, size_tolerance, declaration_tolerance)
    ):
        _blocker(blockers, contract_id, f"{side}:contract")
        return {"side": side, "pass": False, "error": "head tolerances must be nonnegative"}
    if (
        not math.isfinite(mask_iou_min)
        or not 0 <= mask_iou_min <= 1
        or not math.isfinite(minimum_head_ratio)
    ):
        _blocker(blockers, contract_id, f"{side}:contract")
        return {"side": side, "pass": False, "error": "mask_iou_min must lie within [0, 1]"}
    edge_errors = _edge_errors(expected, actual)
    width_error = abs(actual_width - float(expected_width))
    length_error = abs(actual_length - float(expected_length))
    x0, y0, x1, y1 = search_bbox
    reference_head_mask = reference_mask[y0:y1, x0:x1]
    render_head_mask = render_mask[y0:y1, x0:x1]
    mask_iou = _fuzzy_mask_iou(reference_head_mask, render_head_mask)
    orientation = _canonical_orientation_metrics(
        reference_head_mask, render_head_mask
    )
    scene_taper: dict[str, Any] | None = None
    if isinstance(scene_direction, dict):
        head_type = str(scene_direction.get("head_type") or "none")
        try:
            outward_angle = float(scene_direction.get("outward_angle_deg"))
        except (TypeError, ValueError):
            outward_angle = math.nan
        directional_types = {"triangle", "open", "stealth", "custom"}
        if head_type in directional_types:
            reference_taper = _head_taper(
                reference_mask, search_bbox, outward_angle
            )
            render_taper = _head_taper(render_mask, search_bbox, outward_angle)
            taper_pass = (
                reference_taper is not None
                and render_taper is not None
                and reference_taper >= 0.15
                and render_taper > 0
                and abs(reference_taper - render_taper) <= 0.25
            )
            scene_taper = {
                "head_type": head_type,
                "outward_angle_deg": (
                    round(outward_angle % 360.0, 4)
                    if math.isfinite(outward_angle)
                    else None
                ),
                "reference_taper": (
                    round(reference_taper, 4)
                    if reference_taper is not None
                    else None
                ),
                "render_taper": (
                    round(render_taper, 4) if render_taper is not None else None
                ),
                "minimum_reference_taper": 0.15,
                "maximum_taper_delta": 0.25,
                "pass": taper_pass,
            }
        else:
            scene_taper = {
                "head_type": head_type,
                "outward_angle_deg": (
                    round(outward_angle % 360.0, 4)
                    if math.isfinite(outward_angle)
                    else None
                ),
                "directionally_symmetric": head_type in {"diamond", "oval"},
                "pass": True,
            }
    if max(edge_errors) > bbox_tolerance:
        _blocker(blockers, contract_id, f"{side}:head-bbox")
    if width_error > size_tolerance:
        _blocker(blockers, contract_id, f"{side}:head-width")
    if length_error > size_tolerance:
        _blocker(blockers, contract_id, f"{side}:head-length")
    if mask_iou < mask_iou_min:
        _blocker(blockers, contract_id, f"{side}:head-silhouette")
    taper_pass = scene_taper is None or bool(scene_taper["pass"])
    # Exact silhouette direction is primary.  At very small raster sizes a
    # one-pixel host difference can move the direct-vs-opposite margin just
    # below 0.10; an independently positive ArrowSpec-directed taper may
    # resolve that ambiguity, but can never rescue a reversed taper.
    canonical_pass = bool(orientation["pass"]) or (
        bool(orientation["observable"])
        and float(orientation["direct_iou"]) >= 0.55
        and float(orientation["direct_iou"]) > float(orientation["opposite_iou"])
        and scene_taper is not None
        and bool(scene_taper["pass"])
    )
    orientation_pass = canonical_pass and taper_pass
    if not orientation_pass:
        _blocker(blockers, contract_id, f"{side}:head-direction")
    # Distinctness is a cross-axis contract.  A short, wide block-arrow head
    # is valid and visibly distinct even when its axial length is smaller than
    # 1.5x the shaft.  Using min(width, length) incorrectly rejected exactly
    # that common PowerPoint silhouette and encouraged oversized heads.
    expected_head_ratio = float(expected_width) / shaft_width
    render_head_ratio = actual_width / shaft_width
    if (
        expected_head_ratio < minimum_head_ratio
        or render_head_ratio < minimum_head_ratio
    ):
        _blocker(blockers, contract_id, f"{side}:head-not-distinct-from-shaft")
    if evidence_trusted:
        trusted_key = f"{element_id}:{side}"
        trusted_lengths[trusted_key] = float(expected_length)
        trusted_tolerances[trusted_key] = declaration_tolerance
    passed = (
        max(edge_errors) <= bbox_tolerance
        and width_error <= size_tolerance
        and length_error <= size_tolerance
        and mask_iou >= mask_iou_min
        and orientation_pass
        and search_is_tight
        and expected_head_ratio >= minimum_head_ratio
        and render_head_ratio >= minimum_head_ratio
    )
    return {
        "side": side,
        "search_bbox": list(search_bbox),
        "expected": {
            **expected,
            "width_px": float(expected_width),
            "length_px": float(expected_length),
        },
        "search_padding_px": [round(value, 4) for value in search_paddings],
        "max_search_padding_px": maximum_search_padding,
        "render": {
            **actual,
            "width_px": actual_width,
            "length_px": actual_length,
        },
        "bbox_edge_errors_px": [round(value, 4) for value in edge_errors],
        "bbox_tolerance_px": bbox_tolerance,
        "width_error_px": round(width_error, 4),
        "length_error_px": round(length_error, 4),
        "size_tolerance_px": size_tolerance,
        "mask_iou": round(mask_iou, 4),
        "mask_iou_min": mask_iou_min,
        "canonical_orientation": {
            **{
                key: round(value, 4) if isinstance(value, float) else value
                for key, value in orientation.items()
            },
            "resolved_pass": canonical_pass,
        },
        "scene_taper": scene_taper,
        "expected_head_to_shaft_ratio": round(expected_head_ratio, 4),
        "render_head_to_shaft_ratio": round(render_head_ratio, 4),
        "min_head_to_shaft_ratio": minimum_head_ratio,
        "pass": passed,
    }


def _declared_head_lengths(
    svg_text: str,
    trusted_lengths: dict[str, float],
) -> tuple[list[dict[str, Any]], list[str]]:
    declarations: list[dict[str, Any]] = []
    blockers: list[str] = []
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return [], [f"arrow-visual:svg-parse:{exc}"]
    anonymous = 0
    for element in root.iter():
        local = element.get("data-head-length")
        start = element.get("data-head-length-start")
        end = element.get("data-head-length-end")
        if local is None and start is None and end is None:
            continue
        anonymous += 1
        element_id = element.get("id") or f"anonymous-arrow-{anonymous}"
        style = element.get("style", "")
        active_sides: list[str] = []
        if element.get("marker-start") or re.search(r"marker-start\s*:\s*url\(", style):
            active_sides.append("start")
        if element.get("marker-end") or re.search(r"marker-end\s*:\s*url\(", style):
            active_sides.append("end")
        if not active_sides:
            if element.get("data-start-head-type", "none") != "none":
                active_sides.append("start")
            if element.get("data-end-head-type", "none") != "none":
                active_sides.append("end")
        if not active_sides:
            active_sides = ["start", "end"]
        raw_by_side = {
            side: (start if side == "start" and start is not None else end)
            if side in {"start", "end"} and (start if side == "start" else end) is not None
            else local
            for side in active_sides
        }
        for side, raw in raw_by_side.items():
            if raw is None:
                continue
            key = f"{element_id}:{side}"
            item: dict[str, Any] = {
                "element_id": element_id,
                "side": side,
                "declared": raw,
                "evidence_key": key,
            }
            try:
                declared = float(raw)
                valid = math.isfinite(declared) and declared > 0
            except (TypeError, ValueError):
                declared = math.nan
                valid = False
            if not valid:
                item.update({"pass": False, "error": "declared head length must be positive"})
                blockers.append(f"arrow-visual:{element_id}:{side}:invalid-data-head-length")
            else:
                item.update(
                    {
                        "value_px": declared,
                        **(
                            {"reference_px": trusted_lengths[key]}
                            if key in trusted_lengths
                            else {}
                        ),
                        "pass": False,
                        "error": (
                            "data-head-length is an unscaled SVG self-report; use the "
                            "hash-bound pixel contract instead"
                        ),
                    }
                )
                blockers.append(
                    f"arrow-visual:{element_id}:{side}:deprecated-data-head-length"
                )
            declarations.append(item)
    return declarations, blockers


def evaluate_arrow_visual_contracts(
    *,
    reference: np.ndarray,
    render: np.ndarray,
    reference_sha256: str,
    contracts: list[dict[str, Any]],
    svg_text: str,
    expectation: Any = _EXPECTATION_UNSET,
    required_heads: dict[str, set[str]] | None = None,
    scene_head_directions: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Evaluate already-collected, document-bound visual contracts."""

    if reference.shape != render.shape:
        raise ValueError(f"reference/render size mismatch: {reference.shape} != {render.shape}")
    if reference.ndim != 3 or reference.shape[2] < 3:
        raise ValueError("reference/render arrays must be RGB images")
    records: list[dict[str, Any]] = []
    expectation_report, blockers = _audit_expectation(
        expectation, contracts, required_heads
    )
    trusted_lengths: dict[str, float] = {}
    trusted_tolerances: dict[str, float] = {}
    seen_ids: set[str] = set()
    for index, raw_contract in enumerate(contracts, start=1):
        if not isinstance(raw_contract, dict):
            contract_id = f"contract-{index}"
            _blocker(blockers, contract_id, "contract:not-an-object")
            records.append({"id": contract_id, "pass": False, "error": "not an object"})
            continue
        contract = copy.deepcopy(raw_contract)
        document_hash = contract.pop("_document_reference_sha256", None)
        contract_id = str(contract.get("id") or f"contract-{index}")
        element_id = str(contract.get("element_id") or contract_id)
        if contract_id in seen_ids:
            _blocker(blockers, contract_id, "contract:duplicate-id")
            records.append({"id": contract_id, "pass": False, "error": "duplicate id"})
            continue
        seen_ids.add(contract_id)
        record: dict[str, Any] = {"id": contract_id, "element_id": element_id}
        evidence = contract.get("evidence")
        if not isinstance(evidence, dict):
            _blocker(blockers, contract_id, "evidence:missing")
            record.update({"pass": False, "error": "evidence must be an object"})
            records.append(record)
            continue
        evidence_kind = evidence.get("kind")
        evidence_hash = evidence.get("reference_sha256") or document_hash
        evidence_trusted = evidence_hash == reference_sha256
        if evidence_kind != "reference_pixels":
            _blocker(blockers, contract_id, "evidence:unsupported-kind")
            record.update(
                {
                    "pass": False,
                    "error": "strict arrow visual evidence must use reference_pixels",
                }
            )
            records.append(record)
            continue
        if not evidence_trusted:
            _blocker(blockers, contract_id, "evidence:reference-hash-mismatch")
        record["evidence"] = {
            "kind": evidence_kind,
            "reference_sha256": evidence_hash,
            "reference_hash_match": evidence_hash == reference_sha256,
            **({"basis": evidence.get("basis")} if evidence.get("basis") else {}),
        }
        axis = contract.get("axis")
        if axis not in {"horizontal", "vertical", "angle"}:
            _blocker(blockers, contract_id, "contract:axis")
            record.update(
                {
                    "pass": False,
                    "error": "axis must be horizontal, vertical, or angle",
                }
            )
            records.append(record)
            continue
        try:
            shaft_width = float(contract.get("shaft_width_px"))
            if not math.isfinite(shaft_width) or shaft_width <= 0:
                raise ValueError
            tight_bbox = _bounds(
                contract.get("tight_bbox"),
                reference.shape,
                label_text=f"{contract_id}.tight_bbox",
            )
            obstacle_contracts = contract.get("obstacles", [])
            if not isinstance(obstacle_contracts, list):
                raise ValueError("obstacles must be an array")
            if any(not isinstance(item, dict) for item in obstacle_contracts):
                raise ValueError("every obstacle must be an object")
            obstacle_bounds = [
                _bounds(
                    item.get("bbox"),
                    reference.shape,
                    label_text=f"{contract_id}.obstacle[{obstacle_index}].bbox",
                )
                for obstacle_index, item in enumerate(obstacle_contracts, start=1)
            ]
            measurement_bbox = _bbox_union([tight_bbox, *obstacle_bounds])
            shaft_seed_point = _seed_point(
                contract.get("shaft_seed_point"),
                reference.shape,
                measurement_bbox,
                label_text=f"{contract_id}.shaft_seed_point",
            )
            shaft_seed_radius = int(contract.get("shaft_seed_radius_px", 1))
            if shaft_seed_radius < 0 or shaft_seed_radius > 4:
                raise ValueError("shaft_seed_radius_px must lie within [0, 4]")
            mask_contract = contract.get("mask", {})
            if not isinstance(mask_contract, dict):
                raise ValueError("mask must be an object")
            resolved_background = None
            if mask_contract.get("mode", "background_delta") == "background_delta":
                resolved_background = mask_contract.get("background_rgb")
                if resolved_background is None:
                    resolved_background = _border_background(reference, tight_bbox)
            reference_candidate = _foreground_mask(
                reference,
                mask_contract,
                resolved_background=resolved_background,
            )
            render_candidate = _foreground_mask(
                render,
                mask_contract,
                resolved_background=resolved_background,
            )
            reference_mask, reference_seed_components = _arrow_component(
                reference_candidate,
                shaft_seed_point,
                shaft_seed_radius,
                measurement_bbox,
            )
            render_mask, render_seed_components = _arrow_component(
                render_candidate,
                shaft_seed_point,
                shaft_seed_radius,
                measurement_bbox,
            )
            seed_x, seed_y = shaft_seed_point
            reference_seed_center_foreground = bool(
                reference_candidate[seed_y, seed_x]
            )
            render_seed_center_foreground = bool(render_candidate[seed_y, seed_x])
        except (TypeError, ValueError) as exc:
            _blocker(blockers, contract_id, "contract:invalid")
            record.update({"pass": False, "error": str(exc)})
            records.append(record)
            continue
        reference_geometry = _geometry(reference_mask)
        render_geometry = _geometry(render_mask)
        tight_selection = np.zeros(reference.shape[:2], dtype=bool)
        tight_x0, tight_y0, tight_x1, tight_y1 = tight_bbox
        tight_selection[tight_y0:tight_y1, tight_x0:tight_x1] = True
        reference_extension = (
            binary_dilation(
                reference_mask, structure=np.ones((3, 3), dtype=bool)
            )
            & reference_candidate
            & ~tight_selection
        )
        render_extension = (
            binary_dilation(render_mask, structure=np.ones((3, 3), dtype=bool))
            & render_candidate
            & ~tight_selection
        )
        reference_extension_pixels = int(reference_extension.sum())
        render_extension_pixels = int(render_extension.sum())
        record.update(
            {
                "axis": axis,
                "shaft_width_px": shaft_width,
                "shaft_seed_point": list(shaft_seed_point),
                "shaft_seed_radius_px": shaft_seed_radius,
                "reference_seed_component_count": reference_seed_components,
                "render_seed_component_count": render_seed_components,
                "reference_seed_center_foreground": reference_seed_center_foreground,
                "render_seed_center_foreground": render_seed_center_foreground,
                "tight_bbox": list(tight_bbox),
                "reference_arrow": reference_geometry,
                "render_arrow": render_geometry,
                "reference_tight_bbox_extension_pixels": reference_extension_pixels,
                "render_tight_bbox_extension_pixels": render_extension_pixels,
            }
        )
        if reference_extension_pixels:
            _blocker(blockers, contract_id, "reference:tight-bbox-truncates-component")
        if render_extension_pixels:
            _blocker(blockers, contract_id, "render:tight-bbox-truncates-component")
        try:
            head_values = contract.get("heads")
            first_head = (
                next(
                    (
                        value
                        for side in ("start", "end")
                        if isinstance(head_values, dict)
                        and isinstance((value := head_values.get(side)), dict)
                    ),
                    {},
                )
                if isinstance(head_values, dict)
                else {}
            )
            shaft_axis_angle = contract.get(
                "shaft_axis_angle_deg", first_head.get("axis_angle_deg")
            )
            shaft_probe_half_length = float(
                contract.get("shaft_probe_half_length_px", 2.0)
            )
            shaft_width_tolerance = min(
                float(contract.get("shaft_width_tolerance_px", 1.5)), 1.5
            )
            reference_shaft_width = _shaft_width_at_seed(
                reference_mask,
                shaft_seed_point,
                axis=axis,
                axis_angle_deg=shaft_axis_angle,
                probe_half_length_px=shaft_probe_half_length,
            )
            render_shaft_width = _shaft_width_at_seed(
                render_mask,
                shaft_seed_point,
                axis=axis,
                axis_angle_deg=shaft_axis_angle,
                probe_half_length_px=shaft_probe_half_length,
            )
            shaft_render_error = abs(render_shaft_width - reference_shaft_width)
            shaft_declaration_error = abs(reference_shaft_width - shaft_width)
            shaft_pass = (
                shaft_width_tolerance >= 0
                and shaft_render_error <= shaft_width_tolerance
                and shaft_declaration_error <= shaft_width_tolerance
            )
        except (TypeError, ValueError) as exc:
            reference_shaft_width = None
            render_shaft_width = None
            shaft_render_error = None
            shaft_declaration_error = None
            shaft_width_tolerance = None
            shaft_pass = False
            record["shaft_measurement_error"] = str(exc)
        record["shaft_measurement"] = {
            "reference_width_px": reference_shaft_width,
            "render_width_px": render_shaft_width,
            "render_error_px": (
                round(shaft_render_error, 4)
                if shaft_render_error is not None
                else None
            ),
            "declared_width_px": shaft_width,
            "declaration_error_px": (
                round(shaft_declaration_error, 4)
                if shaft_declaration_error is not None
                else None
            ),
            "tolerance_px": shaft_width_tolerance,
            "pass": shaft_pass,
        }
        if not shaft_pass:
            _blocker(blockers, contract_id, "shaft-width")
        if reference_seed_components != 1:
            _blocker(blockers, contract_id, "reference:shaft-seed-not-unique")
        if render_seed_components != 1:
            _blocker(blockers, contract_id, "render:shaft-seed-not-unique")
        if not reference_seed_center_foreground:
            _blocker(blockers, contract_id, "reference:shaft-seed-center-not-foreground")
        if not render_seed_center_foreground:
            _blocker(blockers, contract_id, "render:shaft-seed-center-not-foreground")
        if reference_geometry is None and evidence_kind == "reference_pixels":
            _blocker(blockers, contract_id, "reference:missing-arrow")
        if render_geometry is None:
            _blocker(blockers, contract_id, "render:missing-arrow")
        if evidence_kind == "reference_pixels" and reference_geometry and render_geometry:
            try:
                geometry_cap = min(
                    2.0,
                    max(
                        1.0,
                        math.hypot(reference.shape[1], reference.shape[0]) * 0.0025,
                    ),
                )
                silhouette_tolerance = min(
                    float(
                        contract.get(
                            "silhouette_bbox_tolerance_px",
                            DEFAULT_GEOMETRY_TOLERANCE_PX,
                        )
                    ),
                    geometry_cap,
                )
            except (TypeError, ValueError):
                silhouette_tolerance = math.nan
            try:
                thin_arrow = shaft_width <= 2.0
                silhouette_mask_floor = 0.55 if thin_arrow else MASK_IOU_FLOOR
                silhouette_area_cap = (
                    0.75 if thin_arrow else MAX_ARROW_AREA_RELATIVE_TOLERANCE
                )
                arrow_mask_iou_min = max(
                    float(
                        contract.get(
                            "arrow_mask_iou_min", silhouette_mask_floor
                        )
                    ),
                    silhouette_mask_floor,
                )
                arrow_area_tolerance = min(
                    float(
                        contract.get(
                            "arrow_area_relative_tolerance",
                            silhouette_area_cap,
                        )
                    ),
                    silhouette_area_cap,
                )
            except (TypeError, ValueError):
                arrow_mask_iou_min = math.nan
                arrow_area_tolerance = math.nan
            if (
                not math.isfinite(silhouette_tolerance)
                or silhouette_tolerance < 0
                or not math.isfinite(arrow_mask_iou_min)
                or not 0 <= arrow_mask_iou_min <= 1
                or not math.isfinite(arrow_area_tolerance)
                or not 0 <= arrow_area_tolerance <= 1
            ):
                _blocker(blockers, contract_id, "contract:silhouette-tolerance")
            else:
                silhouette_errors = _edge_errors(reference_geometry, render_geometry)
                record["silhouette_bbox_edge_errors_px"] = [
                    round(value, 4) for value in silhouette_errors
                ]
                record["silhouette_bbox_tolerance_px"] = silhouette_tolerance
                if max(silhouette_errors) > silhouette_tolerance:
                    _blocker(blockers, contract_id, "silhouette-bbox")
                tight_x0, tight_y0, tight_x1, tight_y1 = tight_bbox
                reference_tight_mask = reference_mask[
                    tight_y0:tight_y1, tight_x0:tight_x1
                ]
                render_tight_mask = render_mask[tight_y0:tight_y1, tight_x0:tight_x1]
                arrow_mask_iou = _fuzzy_mask_iou(
                    reference_tight_mask, render_tight_mask
                )
                reference_area = int(reference_tight_mask.sum())
                render_area = int(render_tight_mask.sum())
                tight_area = int(reference_tight_mask.size)
                reference_fraction = reference_area / max(tight_area, 1)
                render_fraction = render_area / max(tight_area, 1)
                try:
                    maximum_foreground_fraction = min(
                        float(contract.get("max_foreground_fraction", 0.80)),
                        0.80,
                    )
                except (TypeError, ValueError):
                    maximum_foreground_fraction = math.nan
                arrow_area_error = abs(render_area - reference_area) / max(
                    reference_area, 1
                )
                record["silhouette_mask_iou"] = round(arrow_mask_iou, 4)
                record["silhouette_mask_iou_min"] = arrow_mask_iou_min
                record["silhouette_area_relative_error"] = round(
                    arrow_area_error, 4
                )
                record["silhouette_area_relative_tolerance"] = arrow_area_tolerance
                record["reference_foreground_fraction"] = round(
                    reference_fraction, 4
                )
                record["render_foreground_fraction"] = round(render_fraction, 4)
                record["max_foreground_fraction"] = maximum_foreground_fraction
                if arrow_mask_iou < arrow_mask_iou_min:
                    _blocker(blockers, contract_id, "silhouette-mask")
                if arrow_area_error > arrow_area_tolerance:
                    _blocker(blockers, contract_id, "silhouette-area")
                if (
                    not math.isfinite(maximum_foreground_fraction)
                    or maximum_foreground_fraction <= 0
                    or reference_fraction > maximum_foreground_fraction
                    or render_fraction > maximum_foreground_fraction
                ):
                    _blocker(blockers, contract_id, "mask-foreground-density")
        heads = contract.get("heads")
        if not isinstance(heads, dict) or not heads:
            _blocker(blockers, contract_id, "contract:heads")
            record["heads"] = []
        else:
            record["heads"] = []
            unknown_head_sides = sorted(set(heads) - {"start", "end"})
            if unknown_head_sides:
                _blocker(blockers, contract_id, "contract:unsupported-head-side")
                record["unsupported_head_sides"] = unknown_head_sides
            for side in ("start", "end"):
                if side not in heads:
                    continue
                head_contract = heads[side]
                if not isinstance(head_contract, dict):
                    _blocker(blockers, contract_id, f"{side}:contract")
                    record["heads"].append(
                        {"side": side, "pass": False, "error": "head must be an object"}
                    )
                    continue
                record["heads"].append(
                    _evaluate_head(
                        contract_id=contract_id,
                        element_id=element_id,
                        side=side,
                        axis=axis,
                        shaft_width=shaft_width,
                        evidence_kind=evidence_kind,
                        evidence_trusted=evidence_trusted,
                        head_contract=head_contract,
                        reference_mask=reference_mask,
                        render_mask=render_mask,
                        image_shape=reference.shape,
                        blockers=blockers,
                        trusted_lengths=trusted_lengths,
                        trusted_tolerances=trusted_tolerances,
                        scene_direction=(scene_head_directions or {})
                        .get(element_id, {})
                        .get(side),
                    )
                )
        obstacle_records: list[dict[str, Any]] = []
        for obstacle_index, (obstacle, obstacle_bbox) in enumerate(
            zip(obstacle_contracts, obstacle_bounds, strict=True), start=1
        ):
            obstacle_id = str(obstacle.get("id") or f"obstacle-{obstacle_index}")
            reference_metrics = _obstacle_metrics(reference_mask, obstacle_bbox)
            render_metrics = _obstacle_metrics(render_mask, obstacle_bbox)
            try:
                maximum_intersection = int(obstacle.get("max_intersection_pixels", 0))
                tolerance = float(
                    obstacle.get(
                        "clearance_tolerance_px", DEFAULT_GEOMETRY_TOLERANCE_PX
                    )
                )
                if maximum_intersection < 0 or not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError
            except (TypeError, ValueError):
                maximum_intersection = 0
                tolerance = DEFAULT_GEOMETRY_TOLERANCE_PX
                _blocker(blockers, contract_id, f"obstacle:{obstacle_id}:contract")
            expected_clearance = obstacle.get("expected_clearance_px")
            if expected_clearance is None and evidence_kind == "reference_pixels":
                expected_clearance = reference_metrics["clearance_px"]
            minimum_clearance = obstacle.get("min_clearance_px")
            maximum_clearance = obstacle.get("max_clearance_px")
            obstacle_pass = render_metrics["intersection_pixels"] <= maximum_intersection
            if not obstacle_pass:
                _blocker(blockers, contract_id, f"obstacle:{obstacle_id}:pixel-intersection")
            actual_clearance = render_metrics["clearance_px"]
            clearance_error = None
            try:
                if expected_clearance is not None and actual_clearance is not None:
                    expected_value = float(expected_clearance)
                    clearance_error = abs(float(actual_clearance) - expected_value)
                    if clearance_error > tolerance:
                        obstacle_pass = False
                        _blocker(
                            blockers,
                            contract_id,
                            f"obstacle:{obstacle_id}:clearance-reference",
                        )
                if minimum_clearance is not None and (
                    actual_clearance is None
                    or float(actual_clearance) < float(minimum_clearance)
                ):
                    obstacle_pass = False
                    _blocker(blockers, contract_id, f"obstacle:{obstacle_id}:clearance-min")
                if maximum_clearance is not None and (
                    actual_clearance is None
                    or float(actual_clearance) > float(maximum_clearance)
                ):
                    obstacle_pass = False
                    _blocker(blockers, contract_id, f"obstacle:{obstacle_id}:clearance-max")
            except (TypeError, ValueError):
                obstacle_pass = False
                _blocker(blockers, contract_id, f"obstacle:{obstacle_id}:contract")
            obstacle_records.append(
                {
                    "id": obstacle_id,
                    "bbox": list(obstacle_bbox),
                    "reference": reference_metrics,
                    "render": render_metrics,
                    "max_intersection_pixels": maximum_intersection,
                    "expected_clearance_px": expected_clearance,
                    "clearance_error_px": (
                        round(clearance_error, 4) if clearance_error is not None else None
                    ),
                    "clearance_tolerance_px": tolerance,
                    "min_clearance_px": minimum_clearance,
                    "max_clearance_px": maximum_clearance,
                    "pass": obstacle_pass,
                }
            )
        record["obstacles"] = obstacle_records
        record["pass"] = not any(
            item.startswith(f"arrow-visual:{contract_id}:") for item in blockers
        )
        records.append(record)
    declarations, declaration_blockers = _declared_head_lengths(
        svg_text, trusted_lengths
    )
    blockers.extend(declaration_blockers)
    blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "arrow_visual_audit",
        "reference_sha256": reference_sha256,
        "contract_count": len(contracts),
        "expectation": expectation_report,
        "records": records,
        "declared_head_lengths": declarations,
        "trusted_head_lengths": trusted_lengths,
        "blockers": blockers,
        "pass": not blockers,
    }


def _collect_contracts(regions: dict[str, Any], scene: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []

    def add(raw: Any, *, document_hash: Any, default_bbox: Any = None, element_id: Any = None) -> None:
        if isinstance(raw, list):
            for item in raw:
                add(
                    item,
                    document_hash=document_hash,
                    default_bbox=default_bbox,
                    element_id=element_id,
                )
            return
        if not isinstance(raw, dict):
            collected.append(raw)
            return
        contract = copy.deepcopy(raw)
        contract["_document_reference_sha256"] = document_hash
        if default_bbox is not None:
            contract.setdefault("tight_bbox", default_bbox)
        if element_id is not None:
            contract.setdefault("element_id", element_id)
        collected.append(contract)

    add(
        regions.get("arrow_visual_contracts", []),
        document_hash=regions.get("reference_sha256"),
    )
    for region in regions.get("regions", []):
        if not isinstance(region, dict):
            continue
        if "arrow_visual_contract" in region:
            add(
                region["arrow_visual_contract"],
                document_hash=regions.get("reference_sha256"),
                default_bbox=region.get("bbox"),
            )
        if "arrow_visual_contracts" in region:
            add(
                region["arrow_visual_contracts"],
                document_hash=regions.get("reference_sha256"),
                default_bbox=region.get("bbox"),
            )
    for edge in scene.get("edges", []):
        if not isinstance(edge, dict):
            continue
        spec = edge.get("arrow_spec")
        if isinstance(spec, dict) and "visual_contract" in spec:
            add(
                spec["visual_contract"],
                document_hash=scene.get("reference_sha256"),
                element_id=edge.get("id"),
            )
    return collected


def _sample_arrow_path(path: Any) -> list[tuple[float, float]]:
    if not isinstance(path, dict):
        return []
    kind = path.get("kind")
    if kind in {"straight", "polyline"}:
        points = path.get("points")
        if not isinstance(points, list):
            return []
        try:
            return [(float(point["x"]), float(point["y"])) for point in points]
        except (KeyError, TypeError, ValueError):
            return []
    if kind != "cubic" or not isinstance(path.get("start"), dict):
        return []
    try:
        current = (float(path["start"]["x"]), float(path["start"]["y"]))
    except (KeyError, TypeError, ValueError):
        return []
    sampled = [current]
    segments = path.get("segments")
    if not isinstance(segments, list):
        return []
    try:
        for segment in segments:
            control1 = (float(segment["control1"]["x"]), float(segment["control1"]["y"]))
            control2 = (float(segment["control2"]["x"]), float(segment["control2"]["y"]))
            end = (float(segment["end"]["x"]), float(segment["end"]["y"]))
            start = current
            for index in range(1, 65):
                t = index / 64.0
                inverse = 1.0 - t
                sampled.append(
                    (
                        inverse**3 * start[0]
                        + 3 * inverse**2 * t * control1[0]
                        + 3 * inverse * t**2 * control2[0]
                        + t**3 * end[0],
                        inverse**3 * start[1]
                        + 3 * inverse**2 * t * control1[1]
                        + 3 * inverse * t**2 * control2[1]
                        + t**3 * end[1],
                    )
                )
            current = end
    except (KeyError, TypeError, ValueError):
        return []
    return sampled


def _scene_head_directions(
    scene: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Derive directed head tangents exclusively from each ArrowSpec path."""

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for edge in scene.get("edges", []):
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str):
            continue
        spec = edge.get("arrow_spec")
        if not isinstance(spec, dict):
            continue
        sampled = _sample_arrow_path(spec.get("path"))
        if len(sampled) < 2:
            continue
        directions: dict[str, dict[str, Any]] = {}
        for side in ("start", "end"):
            head = spec.get(f"{side}_head")
            if not isinstance(head, dict) or head.get("type", "none") == "none":
                continue
            if side == "start":
                dx = sampled[0][0] - sampled[1][0]
                dy = sampled[0][1] - sampled[1][1]
            else:
                dx = sampled[-1][0] - sampled[-2][0]
                dy = sampled[-1][1] - sampled[-2][1]
            if dx == 0 and dy == 0:
                continue
            directions[side] = {
                "head_type": str(head.get("type") or "none"),
                "outward_angle_deg": math.degrees(math.atan2(dy, dx)) % 360.0,
            }
        if directions:
            result[edge["id"]] = directions
    return result


def _distance_to_polyline(point: tuple[float, float], path: list[tuple[float, float]]) -> float:
    if not path:
        return math.inf
    if len(path) == 1:
        return math.dist(point, path[0])
    minimum = math.inf
    px, py = point
    for start, end in zip(path, path[1:], strict=False):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            distance = math.dist(point, start)
        else:
            projection = max(
                0.0,
                min(1.0, ((px - start[0]) * dx + (py - start[1]) * dy) / length_squared),
            )
            closest = (start[0] + projection * dx, start[1] + projection * dy)
            distance = math.dist(point, closest)
        minimum = min(minimum, distance)
    return minimum


def _audit_scene_path_bindings(
    contracts: list[dict[str, Any]],
    scene: dict[str, Any],
    image_shape: tuple[int, ...],
    evaluation_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bind every visual component seed to its ArrowSpec centerline."""

    specs = {
        edge.get("id"): edge.get("arrow_spec")
        for edge in scene.get("edges", [])
        if isinstance(edge, dict) and isinstance(edge.get("id"), str)
    }
    maximum_tolerance = min(
        3.0, math.hypot(image_shape[1], image_shape[0]) * 0.0035
    )
    evaluated_by_id = {
        record.get("id"): record
        for record in evaluation_records
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    records: list[dict[str, Any]] = []
    blockers: list[str] = []
    for index, contract in enumerate(contracts, start=1):
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or f"contract-{index}")
        element_id = str(contract.get("element_id") or contract_id)
        spec = specs.get(element_id)
        sampled = _sample_arrow_path(spec.get("path") if isinstance(spec, dict) else None)
        try:
            seed = tuple(float(value) for value in contract.get("shaft_seed_point", []))
            if len(seed) != 2 or not all(math.isfinite(value) for value in seed):
                raise ValueError
            requested_tolerance = float(
                contract.get("scene_path_tolerance_px", maximum_tolerance)
            )
            if not math.isfinite(requested_tolerance) or requested_tolerance < 0:
                raise ValueError
            tolerance = min(requested_tolerance, maximum_tolerance)
        except (TypeError, ValueError):
            seed = (math.nan, math.nan)
            tolerance = maximum_tolerance
        distance = _distance_to_polyline(seed, sampled)
        seed_pass = bool(sampled) and math.isfinite(distance) and distance <= tolerance
        if not seed_pass:
            blockers.append(f"arrow-visual:{contract_id}:scene-path-binding")
        try:
            tight_bbox = contract["tight_bbox"]
            tight_x0, tight_y0, tight_width, tight_height = (
                float(value) for value in tight_bbox
            )
            tight_x1 = tight_x0 + tight_width
            tight_y1 = tight_y0 + tight_height
            path_inside_tight = bool(sampled) and all(
                tight_x0 - tolerance <= x <= tight_x1 + tolerance
                and tight_y0 - tolerance <= y <= tight_y1 + tolerance
                for x, y in sampled
            )
        except (KeyError, TypeError, ValueError):
            path_inside_tight = False
        if not path_inside_tight:
            blockers.append(f"arrow-visual:{contract_id}:tight-bbox-path-coverage")
        head_bindings: list[dict[str, Any]] = []
        heads = contract.get("heads") if isinstance(contract.get("heads"), dict) else {}
        for side in ("start", "end"):
            if side not in heads:
                continue
            head = heads[side]
            endpoint = sampled[0] if side == "start" and sampled else sampled[-1] if sampled else None
            endpoint_pass = False
            tangent_error = None
            if isinstance(head, dict) and endpoint is not None:
                try:
                    hx, hy, hwidth, hheight = (
                        float(value) for value in head["search_bbox"]
                    )
                    endpoint_pass = (
                        hx - tolerance <= endpoint[0] <= hx + hwidth + tolerance
                        and hy - tolerance <= endpoint[1] <= hy + hheight + tolerance
                    )
                    tangent_start, tangent_end = (
                        (sampled[0], sampled[1])
                        if side == "start"
                        else (sampled[-2], sampled[-1])
                    )
                    tangent_angle = math.degrees(
                        math.atan2(
                            tangent_end[1] - tangent_start[1],
                            tangent_end[0] - tangent_start[0],
                        )
                    )
                    if contract.get("axis") == "horizontal":
                        declared_angle = 0.0
                    elif contract.get("axis") == "vertical":
                        declared_angle = 90.0
                    else:
                        declared_angle = float(head.get("axis_angle_deg"))
                    raw_error = abs((declared_angle - tangent_angle) % 180.0)
                    tangent_error = min(raw_error, 180.0 - raw_error)
                except (KeyError, IndexError, TypeError, ValueError):
                    endpoint_pass = False
            tangent_pass = tangent_error is not None and tangent_error <= 3.0
            if not endpoint_pass:
                blockers.append(
                    f"arrow-visual:{contract_id}:{side}:scene-endpoint-coverage"
                )
            if not tangent_pass:
                blockers.append(
                    f"arrow-visual:{contract_id}:{side}:scene-tangent-mismatch"
                )
            head_bindings.append(
                {
                    "side": side,
                    "scene_endpoint": list(endpoint) if endpoint is not None else None,
                    "search_bbox_contains_endpoint": endpoint_pass,
                    "axis_tangent_error_deg": (
                        round(tangent_error, 4) if tangent_error is not None else None
                    ),
                    "axis_tangent_tolerance_deg": 3.0,
                    "pass": endpoint_pass and tangent_pass,
                }
            )
        evaluated = evaluated_by_id.get(contract_id, {})
        reference_arrow = evaluated.get("reference_arrow")
        component_inside_tight = False
        if isinstance(reference_arrow, dict):
            try:
                left, top, right, bottom = (
                    float(value) for value in reference_arrow["edges"]
                )
                component_inside_tight = (
                    tight_x0 - tolerance <= left
                    and tight_y0 - tolerance <= top
                    and right <= tight_x1 + tolerance
                    and bottom <= tight_y1 + tolerance
                )
            except (KeyError, TypeError, ValueError, UnboundLocalError):
                component_inside_tight = False
        if not component_inside_tight:
            blockers.append(
                f"arrow-visual:{contract_id}:tight-bbox-component-coverage"
            )
        passed = (
            seed_pass
            and path_inside_tight
            and component_inside_tight
            and all(item["pass"] for item in head_bindings)
        )
        records.append(
            {
                "id": contract_id,
                "element_id": element_id,
                "shaft_seed_point": list(seed),
                "distance_to_arrow_spec_path_px": (
                    round(distance, 4) if math.isfinite(distance) else None
                ),
                "tolerance_px": round(tolerance, 4),
                "path_inside_tight_bbox": path_inside_tight,
                "reference_component_inside_tight_bbox": component_inside_tight,
                "heads": head_bindings,
                "pass": passed,
            }
        )
    return records, blockers


def audit_arrow_visual_contracts(run: common.Run) -> dict[str, Any]:
    """Evaluate and persist the current case's physical arrow evidence."""

    regions = read_json(run.regions_path)
    scene = read_json(run.scene_path)
    contracts = _collect_contracts(regions, scene)
    required_heads: dict[str, set[str]] = {}
    for edge in scene.get("edges", []):
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str):
            continue
        spec = edge.get("arrow_spec")
        if not isinstance(spec, dict):
            continue
        sides = {
            side
            for side in ("start", "end")
            if isinstance(spec.get(f"{side}_head"), dict)
            and spec[f"{side}_head"].get("type", "none") != "none"
        }
        if sides:
            required_heads[edge["id"]] = sides
    with Image.open(run.source_png) as reference_image, Image.open(run.render_png) as render_image:
        reference = np.asarray(reference_image.convert("RGB"), dtype=np.uint8)
        render = np.asarray(render_image.convert("RGB"), dtype=np.uint8)
    report = evaluate_arrow_visual_contracts(
        reference=reference,
        render=render,
        reference_sha256=run.load_meta()["source_sha256"],
        contracts=contracts,
        svg_text=run.redraw_svg.read_text(encoding="utf-8"),
        expectation=regions.get("arrow_visual_expectation"),
        required_heads=required_heads,
        scene_head_directions=_scene_head_directions(scene),
    )
    if "arrow_visual_expectation" not in regions:
        report["expectation"] = {
            "pass": False,
            "error": "arrow_visual_expectation field is missing",
        }
        report["blockers"] = list(
            dict.fromkeys(
                [*report["blockers"], "arrow-visual:expectation:field-missing"]
            )
        )
    scene_bindings, scene_binding_blockers = _audit_scene_path_bindings(
        contracts, scene, reference.shape, report["records"]
    )
    report["scene_path_bindings"] = scene_bindings
    report["blockers"] = list(
        dict.fromkeys([*report["blockers"], *scene_binding_blockers])
    )
    report["pass"] = not report["blockers"]
    write_json(run.qa_dir / "arrow-visual-report.json", report)
    return report
