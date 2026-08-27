"""Composition-level audits for canonical ArrowSpec centerlines.

An individual arrow can be a perfectly valid native PowerPoint line while the
composition is still wrong.  The canonical example is a bidirectional relation
implemented as two reciprocal, almost completely overlapping single-headed
lines: one shaft paints through the other line's arrowhead.  This module finds
that ambiguity before a renderer or readback report can accidentally bless the
two objects independently.

The detector is deliberately backend-neutral.  It accepts ArrowSpec mappings
from a source scene or reconstructed ArrowSpec-shaped readback records and
returns stable JSON-serializable findings.  There is intentionally no
``allow_overlap`` escape hatch: two independent reverse relations must occupy
separate visible lanes, while one bidirectional relation must be one ArrowSpec
with both native heads.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from typing import Any, TypeAlias

Point: TypeAlias = tuple[float, float]
ArrowSpec: TypeAlias = Mapping[str, Any]
ArrowRecord: TypeAlias = tuple[str, ArrowSpec] | Mapping[str, Any]

DEFAULT_OVERLAP_THRESHOLD = 0.80
DEFAULT_CENTERLINE_TOLERANCE_PX = 2.0
DEFAULT_DIRECTION_TOLERANCE_DEG = 3.0
DEFAULT_SAMPLE_STEP_PX = 2.0
DEFAULT_CUBIC_FLATNESS_PX = 0.5


def _point(value: Any) -> Point:
    if not isinstance(value, Mapping):
        raise ValueError("arrow path point must be a mapping")
    x = value.get("x")
    y = value.get("y")
    if (
        not isinstance(x, (int, float))
        or isinstance(x, bool)
        or not math.isfinite(x)
        or not isinstance(y, (int, float))
        or isinstance(y, bool)
        or not math.isfinite(y)
    ):
        raise ValueError("arrow path point must contain finite x/y values")
    return float(x), float(y)


def _distance(first: Point, second: Point) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _distance_to_line(point: Point, start: Point, end: Point) -> float:
    length = _distance(start, end)
    if length == 0:
        return _distance(point, start)
    return abs(
        (end[0] - start[0]) * (start[1] - point[1])
        - (start[0] - point[0]) * (end[1] - start[1])
    ) / length


def _split_cubic(
    start: Point,
    control1: Point,
    control2: Point,
    end: Point,
) -> tuple[tuple[Point, Point, Point, Point], tuple[Point, Point, Point, Point]]:
    start_control1 = ((start[0] + control1[0]) / 2, (start[1] + control1[1]) / 2)
    control1_control2 = (
        (control1[0] + control2[0]) / 2,
        (control1[1] + control2[1]) / 2,
    )
    control2_end = ((control2[0] + end[0]) / 2, (control2[1] + end[1]) / 2)
    left_middle = (
        (start_control1[0] + control1_control2[0]) / 2,
        (start_control1[1] + control1_control2[1]) / 2,
    )
    right_middle = (
        (control1_control2[0] + control2_end[0]) / 2,
        (control1_control2[1] + control2_end[1]) / 2,
    )
    midpoint = (
        (left_middle[0] + right_middle[0]) / 2,
        (left_middle[1] + right_middle[1]) / 2,
    )
    return (
        (start, start_control1, left_middle, midpoint),
        (midpoint, right_middle, control2_end, end),
    )


def _flatten_cubic(
    start: Point,
    control1: Point,
    control2: Point,
    end: Point,
    *,
    flatness_px: float,
    depth: int = 0,
) -> list[Point]:
    if depth >= 16 or max(
        _distance_to_line(control1, start, end),
        _distance_to_line(control2, start, end),
    ) <= flatness_px:
        return [start, end]
    left, right = _split_cubic(start, control1, control2, end)
    left_points = _flatten_cubic(*left, flatness_px=flatness_px, depth=depth + 1)
    right_points = _flatten_cubic(*right, flatness_px=flatness_px, depth=depth + 1)
    return left_points[:-1] + right_points


def _without_duplicate_neighbors(points: Iterable[Point]) -> tuple[Point, ...]:
    result: list[Point] = []
    for point in points:
        if not result or _distance(result[-1], point) > 1e-9:
            result.append(point)
    if len(result) < 2:
        raise ValueError("arrow path must have at least two distinct points")
    return tuple(result)


def flatten_arrow_path(
    spec: ArrowSpec,
    *,
    cubic_flatness_px: float = DEFAULT_CUBIC_FLATNESS_PX,
) -> tuple[Point, ...]:
    """Return a line-arrow centerline as a polyline.

    Straight and polyline paths preserve their declared vertices.  Cubic paths
    are adaptively subdivided until both control points are within
    ``cubic_flatness_px`` of their segment chord.
    """

    if cubic_flatness_px <= 0 or not math.isfinite(cubic_flatness_px):
        raise ValueError("cubic_flatness_px must be a positive finite number")
    if spec.get("representation") != "line_arrow":
        raise ValueError("composition audit only accepts line_arrow specs")
    path = spec.get("path")
    if not isinstance(path, Mapping):
        raise ValueError("line_arrow spec is missing path")
    kind = path.get("kind")
    if kind in {"straight", "polyline"}:
        values = path.get("points")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError("straight/polyline path requires points")
        return _without_duplicate_neighbors(_point(value) for value in values)
    if kind != "cubic":
        raise ValueError(f"unsupported line_arrow path kind: {kind!r}")

    current = _point(path.get("start"))
    values = path.get("segments")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise ValueError("cubic path requires segments")
    points: list[Point] = [current]
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("cubic path segment must be a mapping")
        control1 = _point(value.get("control1"))
        control2 = _point(value.get("control2"))
        end = _point(value.get("end"))
        flattened = _flatten_cubic(
            current,
            control1,
            control2,
            end,
            flatness_px=cubic_flatness_px,
        )
        points.extend(flattened[1:])
        current = end
    return _without_duplicate_neighbors(points)


def _densify(points: Sequence[Point], step_px: float) -> tuple[Point, ...]:
    result: list[Point] = [points[0]]
    for start, end in zip(points, points[1:]):
        length = _distance(start, end)
        pieces = max(1, math.ceil(length / step_px))
        for index in range(1, pieces + 1):
            fraction = index / pieces
            result.append(
                (
                    start[0] + (end[0] - start[0]) * fraction,
                    start[1] + (end[1] - start[1]) * fraction,
                )
            )
    return tuple(result)


def _path_length(points: Sequence[Point]) -> float:
    return sum(_distance(start, end) for start, end in zip(points, points[1:]))


def _semantic_unit(spec: ArrowSpec, points: Sequence[Point]) -> Point | None:
    start_head = spec.get("start_head")
    end_head = spec.get("end_head")
    start_type = start_head.get("type", "none") if isinstance(start_head, Mapping) else "none"
    end_type = end_head.get("type", "none") if isinstance(end_head, Mapping) else "none"
    start_present = start_type != "none"
    end_present = end_type != "none"
    if start_present == end_present:
        # A double-ended object is already the required one-object
        # representation; an undirected line is not a reciprocal arrow.
        return None
    dx = points[-1][0] - points[0][0]
    dy = points[-1][1] - points[0][1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    sign = -1.0 if start_present else 1.0
    return sign * dx / length, sign * dy / length


def _point_segment_distance_and_unit(
    point: Point,
    start: Point,
    end: Point,
) -> tuple[float, Point]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    squared_length = dx * dx + dy * dy
    if squared_length <= 1e-18:
        return _distance(point, start), (0.0, 0.0)
    fraction = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / squared_length
    fraction = min(1.0, max(0.0, fraction))
    closest = (start[0] + fraction * dx, start[1] + fraction * dy)
    length = math.sqrt(squared_length)
    return _distance(point, closest), (dx / length, dy / length)


def _nearest_segment(point: Point, target: Sequence[Point]) -> tuple[float, Point]:
    best_distance = math.inf
    best_unit = (0.0, 0.0)
    for start, end in zip(target, target[1:]):
        distance, unit = _point_segment_distance_and_unit(point, start, end)
        if distance < best_distance:
            best_distance = distance
            best_unit = unit
    return best_distance, best_unit


def _matched_length(
    source: Sequence[Point],
    target: Sequence[Point],
    *,
    centerline_tolerance_px: float,
    tangent_cosine: float,
) -> tuple[float, float]:
    matched = 0.0
    maximum_error = 0.0
    for start, end in zip(source, source[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        distance, target_unit = _nearest_segment(midpoint, target)
        source_unit = (dx / length, dy / length)
        tangent_alignment = abs(
            source_unit[0] * target_unit[0] + source_unit[1] * target_unit[1]
        )
        if distance <= centerline_tolerance_px and tangent_alignment >= tangent_cosine:
            matched += length
            maximum_error = max(maximum_error, distance)
    return matched, maximum_error


def _record_pairs(
    arrows: Mapping[str, ArrowSpec] | Iterable[ArrowRecord],
) -> list[tuple[str, ArrowSpec]]:
    if isinstance(arrows, Mapping):
        records: Iterable[ArrowRecord] = arrows.items()
    else:
        records = arrows
    result: list[tuple[str, ArrowSpec]] = []
    for record in records:
        if isinstance(record, tuple) and len(record) == 2:
            arrow_id, spec = record
        elif isinstance(record, Mapping):
            arrow_id = record.get("id")
            spec = record.get("arrow_spec")
            if spec is None and record.get("representation") == "line_arrow":
                spec = record
        else:
            continue
        if not isinstance(arrow_id, str) or not arrow_id or not isinstance(spec, Mapping):
            continue
        if spec.get("representation") == "line_arrow":
            result.append((arrow_id, spec))
    return sorted(result, key=lambda item: item[0])


def reciprocal_overlap_finding(
    first_id: str,
    first_spec: ArrowSpec,
    second_id: str,
    second_spec: ArrowSpec,
    *,
    overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    centerline_tolerance_px: float = DEFAULT_CENTERLINE_TOLERANCE_PX,
    direction_tolerance_deg: float = DEFAULT_DIRECTION_TOLERANCE_DEG,
    sample_step_px: float = DEFAULT_SAMPLE_STEP_PX,
    cubic_flatness_px: float = DEFAULT_CUBIC_FLATNESS_PX,
) -> dict[str, Any] | None:
    """Return one blocker finding when two ArrowSpecs form a reciprocal overlap."""

    if not 0 < overlap_threshold <= 1:
        raise ValueError("overlap_threshold must be in (0, 1]")
    if centerline_tolerance_px < 0 or not math.isfinite(centerline_tolerance_px):
        raise ValueError("centerline_tolerance_px must be a finite non-negative number")
    if not 0 <= direction_tolerance_deg < 90:
        raise ValueError("direction_tolerance_deg must be in [0, 90)")
    if sample_step_px <= 0 or not math.isfinite(sample_step_px):
        raise ValueError("sample_step_px must be a positive finite number")

    ordered = sorted(((first_id, first_spec), (second_id, second_spec)), key=lambda item: item[0])
    (first_id, first_spec), (second_id, second_spec) = ordered
    try:
        first_path = flatten_arrow_path(first_spec, cubic_flatness_px=cubic_flatness_px)
        second_path = flatten_arrow_path(second_spec, cubic_flatness_px=cubic_flatness_px)
    except ValueError:
        # Structural ArrowSpec validation owns malformed-path findings.  This
        # composition audit must remain safe to run on partially built scenes.
        return None

    first_unit = _semantic_unit(first_spec, first_path)
    second_unit = _semantic_unit(second_spec, second_path)
    if first_unit is None or second_unit is None:
        return None
    opposition_cosine = first_unit[0] * second_unit[0] + first_unit[1] * second_unit[1]
    tangent_cosine = math.cos(math.radians(direction_tolerance_deg))
    if opposition_cosine > -tangent_cosine:
        return None

    first_dense = _densify(first_path, sample_step_px)
    second_dense = _densify(second_path, sample_step_px)
    first_length = _path_length(first_dense)
    second_length = _path_length(second_dense)
    maximum_length = max(first_length, second_length)
    if maximum_length <= 1e-9:
        return None

    direct_errors = (
        _distance(first_dense[0], second_dense[0]),
        _distance(first_dense[-1], second_dense[-1]),
    )
    reverse_errors = (
        _distance(first_dense[0], second_dense[-1]),
        _distance(first_dense[-1], second_dense[0]),
    )
    endpoint_errors = (
        direct_errors if sum(direct_errors) <= sum(reverse_errors) else reverse_errors
    )
    endpoint_error = max(endpoint_errors)
    endpoint_tolerance = centerline_tolerance_px + maximum_length * (1 - overlap_threshold)
    if endpoint_error > endpoint_tolerance + 1e-9:
        return None

    first_matched, first_error = _matched_length(
        first_dense,
        second_dense,
        centerline_tolerance_px=centerline_tolerance_px,
        tangent_cosine=tangent_cosine,
    )
    second_matched, second_error = _matched_length(
        second_dense,
        first_dense,
        centerline_tolerance_px=centerline_tolerance_px,
        tangent_cosine=tangent_cosine,
    )
    overlap_ratio = min(first_matched, second_matched) / maximum_length
    if overlap_ratio + 1e-12 < overlap_threshold:
        return None

    return {
        "code": "reciprocal-arrow-overlap",
        "severity": "blocker",
        "arrow_ids": [first_id, second_id],
        "path_kinds": [first_spec.get("path", {}).get("kind"), second_spec.get("path", {}).get("kind")],
        "overlap_ratio": round(overlap_ratio, 6),
        "overlap_threshold": overlap_threshold,
        "endpoint_error_px": round(endpoint_error, 6),
        "centerline_error_px": round(max(first_error, second_error), 6),
        "opposition_cosine": round(opposition_cosine, 6),
        "required_representation": "one-bidirectional-visible-object-matching-reference",
    }


def find_reciprocal_arrow_overlaps(
    arrows: Mapping[str, ArrowSpec] | Iterable[ArrowRecord],
    *,
    overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    centerline_tolerance_px: float = DEFAULT_CENTERLINE_TOLERANCE_PX,
    direction_tolerance_deg: float = DEFAULT_DIRECTION_TOLERANCE_DEG,
    sample_step_px: float = DEFAULT_SAMPLE_STEP_PX,
    cubic_flatness_px: float = DEFAULT_CUBIC_FLATNESS_PX,
) -> list[dict[str, Any]]:
    """Find all reciprocal overlaps in a source or readback ArrowSpec set.

    Accepted inputs are ``{id: spec}``, ``[(id, spec), ...]``, scene-style
    records with ``id`` and ``arrow_spec``, or direct ArrowSpec records that
    also carry an ``id``.  Findings and pair order are stable by arrow id.
    """

    records = _record_pairs(arrows)
    findings: list[dict[str, Any]] = []
    for (first_id, first_spec), (second_id, second_spec) in combinations(records, 2):
        finding = reciprocal_overlap_finding(
            first_id,
            first_spec,
            second_id,
            second_spec,
            overlap_threshold=overlap_threshold,
            centerline_tolerance_px=centerline_tolerance_px,
            direction_tolerance_deg=direction_tolerance_deg,
            sample_step_px=sample_step_px,
            cubic_flatness_px=cubic_flatness_px,
        )
        if finding is not None:
            findings.append(finding)
    return findings
