"""Deterministic semantic vector primitives and object-level QA gates."""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import read_json, utc_now, write_json
from tools.svggeom import Matrix, parse_path_d, parse_transform

SVG_NS = "{http://www.w3.org/2000/svg}"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PPTX_NS = {"p": P_NS, "a": A_NS}
EMU_PER_PX = 9525.0
BRACE_SPEC_VERSION = "1.0.0"
BRACE_GENERATOR = "brace_v1"
BRACE_ORIENTATIONS = {"over", "under", "left", "right"}
SOURCE_PATH_TOLERANCE_PX = 0.01
BRACE_CUSP_CENTER_TOLERANCE_PX = 0.01
BRACE_CUSP_CENTER_TOLERANCE_NORMALIZED = 1e-4
BRACE_SYMMETRY_TOLERANCE_PX = 0.01


def _validate_central_cusp(length: float, cusp_offset: float) -> None:
    """Require a geometrically central cusp in both absolute and relative units."""

    center_error_px = abs(cusp_offset - length / 2.0)
    center_error_normalized = center_error_px / length
    if (
        center_error_px > BRACE_CUSP_CENTER_TOLERANCE_PX
        or center_error_normalized > BRACE_CUSP_CENTER_TOLERANCE_NORMALIZED
    ):
        raise ValueError(
            "brace cusp must be centered: "
            f"absolute error {center_error_px:.6f}px exceeds "
            f"{BRACE_CUSP_CENTER_TOLERANCE_PX}px or normalized error "
            f"{center_error_normalized:.8f} exceeds "
            f"{BRACE_CUSP_CENTER_TOLERANCE_NORMALIZED}"
        )


def _fmt(value: float) -> str:
    rounded = round(value, 4)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.4f}".rstrip("0").rstrip(".")


def _map_brace_point(
    x: float,
    y: float,
    orientation: str,
    along: float,
    perpendicular: float,
) -> tuple[float, float]:
    if orientation == "under":
        return x + along, y + perpendicular
    if orientation == "over":
        return x + along, y - perpendicular
    if orientation == "right":
        return x + perpendicular, y + along
    return x - perpendicular, y + along


def canonical_brace_segments(
    x: float,
    y: float,
    length: float,
    depth: float,
    orientation: str,
    *,
    cusp_offset: float | None = None,
    terminal: float = 4.0,
    shoulder: float | None = None,
    outer_radius: float = 6.0,
    cusp_radius: float = 5.0,
    cusp_neck: float | None = None,
) -> list[tuple]:
    """Return the one canonical two-lobe brace for every orientation.

    The local basis is an underbrace. Over/left/right are only reflected or
    rotated mappings of that basis; they are never separately hand-authored.
    Two subpaths intentionally meet at the cusp tip so PowerPoint receives one
    editable freeform with the exact ``MLCLCLMLCLCL`` command signature.
    """

    values = (x, y, length, depth, terminal, outer_radius, cusp_radius)
    if orientation not in BRACE_ORIENTATIONS:
        raise ValueError(f"unsupported brace orientation: {orientation}")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("brace geometry must be finite")
    if length <= 0 or depth <= 0:
        raise ValueError("brace length and depth must be positive")
    cusp_offset = length / 2.0 if cusp_offset is None else cusp_offset
    shoulder = float(round(5.0 * depth / 9.0)) if shoulder is None else shoulder
    cusp_neck = depth - 3.0 if cusp_neck is None else cusp_neck
    parameters = (cusp_offset, shoulder, cusp_neck)
    if not all(math.isfinite(value) for value in parameters):
        raise ValueError("brace parameters must be finite")
    _validate_central_cusp(length, cusp_offset)
    if not (outer_radius + cusp_radius < cusp_offset < length - outer_radius - cusp_radius):
        raise ValueError("brace cusp must leave two non-degenerate lobes")
    if not (0 < terminal < shoulder < cusp_neck < depth):
        raise ValueError("brace depth parameters must satisfy 0 < terminal < shoulder < neck < depth")

    # Q commands become exact cubic equivalents because converter/readback
    # normalize every path to M/L/C.
    def q_as_cubic(
        start: tuple[float, float],
        control: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple:
        c1 = (
            start[0] + (control[0] - start[0]) * 2.0 / 3.0,
            start[1] + (control[1] - start[1]) * 2.0 / 3.0,
        )
        c2 = (
            end[0] + (control[0] - end[0]) * 2.0 / 3.0,
            end[1] + (control[1] - end[1]) * 2.0 / 3.0,
        )
        return ("C", *c1, *c2, *end)

    local: list[tuple] = [
        ("M", 0.0, 0.0),
        ("L", 0.0, terminal),
        q_as_cubic((0.0, terminal), (0.0, shoulder), (outer_radius, shoulder)),
        ("L", cusp_offset - cusp_radius, shoulder),
        q_as_cubic(
            (cusp_offset - cusp_radius, shoulder),
            (cusp_offset, shoulder),
            (cusp_offset, cusp_neck),
        ),
        ("L", cusp_offset, depth),
        ("M", cusp_offset, depth),
        ("L", cusp_offset, cusp_neck),
        q_as_cubic(
            (cusp_offset, cusp_neck),
            (cusp_offset, shoulder),
            (cusp_offset + cusp_radius, shoulder),
        ),
        ("L", length - outer_radius, shoulder),
        q_as_cubic(
            (length - outer_radius, shoulder),
            (length, shoulder),
            (length, terminal),
        ),
        ("L", length, 0.0),
    ]

    result: list[tuple] = []
    for segment in local:
        if segment[0] in {"M", "L"}:
            point = _map_brace_point(x, y, orientation, segment[1], segment[2])
            result.append((segment[0], *point))
        else:
            p1 = _map_brace_point(x, y, orientation, segment[1], segment[2])
            p2 = _map_brace_point(x, y, orientation, segment[3], segment[4])
            p3 = _map_brace_point(x, y, orientation, segment[5], segment[6])
            result.append(("C", *p1, *p2, *p3))
    return result


def segments_to_d(segments: list[tuple]) -> str:
    tokens: list[str] = []
    for segment in segments:
        if segment[0] in {"M", "L"}:
            tokens.append(f"{segment[0]} {_fmt(segment[1])} {_fmt(segment[2])}")
        elif segment[0] == "C":
            tokens.append("C " + " ".join(_fmt(value) for value in segment[1:]))
        else:
            raise ValueError(f"unsupported canonical segment: {segment[0]}")
    return " ".join(tokens)


def _point_on_axis(
    x: float,
    y: float,
    orientation: str,
    along: float,
    perpendicular: float,
) -> dict[str, float]:
    px, py = _map_brace_point(x, y, orientation, along, perpendicular)
    return {"x": px, "y": py}


def brace_spec(
    *,
    x: float,
    y: float,
    length: float,
    depth: float,
    orientation: str,
    stroke: str,
    stroke_width: float,
    cusp_offset: float | None = None,
    terminal: float = 4.0,
    shoulder: float | None = None,
    outer_radius: float = 6.0,
    cusp_radius: float = 5.0,
    cusp_neck: float | None = None,
) -> dict[str, Any]:
    cusp_offset = length / 2.0 if cusp_offset is None else cusp_offset
    shoulder = float(round(5.0 * depth / 9.0)) if shoulder is None else shoulder
    cusp_neck = depth - 3.0 if cusp_neck is None else cusp_neck
    segments = canonical_brace_segments(
        x,
        y,
        length,
        depth,
        orientation,
        cusp_offset=cusp_offset,
        terminal=terminal,
        shoulder=shoulder,
        outer_radius=outer_radius,
        cusp_radius=cusp_radius,
        cusp_neck=cusp_neck,
    )
    return {
        "schema_version": BRACE_SPEC_VERSION,
        "kind": "brace",
        "generator": BRACE_GENERATOR,
        "orientation": orientation,
        "coordinate_space": "svg-local",
        "axis_start": _point_on_axis(x, y, orientation, 0.0, 0.0),
        "axis_end": _point_on_axis(x, y, orientation, length, 0.0),
        "cusp_axis": _point_on_axis(x, y, orientation, cusp_offset, 0.0),
        "cusp_tip": _point_on_axis(x, y, orientation, cusp_offset, depth),
        "length_px": length,
        "depth_px": depth,
        "terminal_px": terminal,
        "shoulder_px": shoulder,
        "outer_radius_px": outer_radius,
        "cusp_radius_px": cusp_radius,
        "cusp_neck_px": cusp_neck,
        "double_lobe": True,
        "central_cusp": True,
        "expected_subpaths": 2,
        "expected_command_signature": "MLCLCLMLCLCL",
        "stroke": {
            "fill": "none",
            "color": stroke.upper(),
            "width_px": stroke_width,
            "line_cap": "round",
        },
        "path": {"coordinate_space": "svg-local", "d": segments_to_d(segments)},
    }


def transform_brace_spec(spec: dict[str, Any], matrix: Matrix) -> dict[str, Any]:
    """Resolve a local brace contract into canvas coordinates."""

    resolved = deepcopy(spec)
    for key in ("axis_start", "axis_end", "cusp_axis", "cusp_tip"):
        point = resolved[key]
        point["x"], point["y"] = matrix.apply(float(point["x"]), float(point["y"]))
    segments = parse_path_d(spec["path"]["d"])
    transformed: list[tuple] = []
    for segment in segments:
        if segment[0] in {"M", "L"}:
            transformed.append((segment[0], *matrix.apply(segment[1], segment[2])))
        elif segment[0] == "C":
            p1 = matrix.apply(segment[1], segment[2])
            p2 = matrix.apply(segment[3], segment[4])
            p3 = matrix.apply(segment[5], segment[6])
            transformed.append(("C", *p1, *p2, *p3))
        else:
            transformed.append(segment)
    resolved["coordinate_space"] = "canvas"
    resolved["path"] = {"coordinate_space": "canvas", "d": segments_to_d(transformed)}
    return resolved


def _brace_transform_contract(
    local_spec: dict[str, Any], resolved_spec: dict[str, Any], matrix: Matrix
) -> dict[str, Any]:
    """Reject skew/nonuniform scale and verify the final canvas-facing orientation."""

    first_norm = math.hypot(matrix.a, matrix.b)
    second_norm = math.hypot(matrix.c, matrix.d)
    denominator = max(first_norm * second_norm, 1e-12)
    orthogonality_error = abs(matrix.a * matrix.c + matrix.b * matrix.d) / denominator
    scale_relative_error = abs(first_norm - second_norm) / max(
        first_norm, second_norm, 1e-12
    )
    similarity_pass = (
        first_norm > 0
        and second_norm > 0
        and orthogonality_error <= 1e-6
        and scale_relative_error <= 1e-6
    )
    cusp_axis = resolved_spec["cusp_axis"]
    cusp_tip = resolved_spec["cusp_tip"]
    cusp_vector = (
        float(cusp_tip["x"]) - float(cusp_axis["x"]),
        float(cusp_tip["y"]) - float(cusp_axis["y"]),
    )
    cusp_norm = math.hypot(*cusp_vector)
    expected_vector = {
        "under": (0.0, 1.0),
        "over": (0.0, -1.0),
        "right": (1.0, 0.0),
        "left": (-1.0, 0.0),
    }[local_spec["orientation"]]
    if cusp_norm <= 0:
        orientation_error_deg = math.inf
    else:
        cosine = max(
            -1.0,
            min(
                1.0,
                (
                    cusp_vector[0] * expected_vector[0]
                    + cusp_vector[1] * expected_vector[1]
                )
                / cusp_norm,
            ),
        )
        orientation_error_deg = math.degrees(math.acos(cosine))
    orientation_pass = orientation_error_deg <= 0.01
    return {
        "matrix": {
            "a": matrix.a,
            "b": matrix.b,
            "c": matrix.c,
            "d": matrix.d,
            "e": matrix.e,
            "f": matrix.f,
        },
        "orthogonality_error": orthogonality_error,
        "scale_relative_error": scale_relative_error,
        "similarity_transform_pass": similarity_pass,
        "cusp_vector": [cusp_vector[0], cusp_vector[1]],
        "orientation_error_deg": orientation_error_deg,
        "orientation_pass": orientation_pass,
        "pass": similarity_pass and orientation_pass,
    }


def _number(element: ET.Element, name: str, default: float | None = None) -> float:
    value = element.get(name)
    if value is None:
        if default is None:
            raise ValueError(f"{element.get('id') or 'brace'}: missing {name}")
        return default
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{element.get('id') or 'brace'}: {name} must be finite")
    return parsed


def materialize_brace(element: ET.Element) -> dict[str, Any] | None:
    if element.get("data-primitive-kind") != "brace":
        return None
    orientation = element.get("data-brace-orientation", "")
    x = _number(element, "data-brace-x")
    y = _number(element, "data-brace-y")
    length = _number(element, "data-brace-length")
    depth = _number(element, "data-brace-depth")
    stroke_width = float(element.get("stroke-width", "1"))
    spec = brace_spec(
        x=x,
        y=y,
        length=length,
        depth=depth,
        orientation=orientation,
        stroke=element.get("stroke", "#000000"),
        stroke_width=stroke_width,
        cusp_offset=_number(element, "data-brace-cusp-offset", length / 2.0),
        terminal=_number(element, "data-brace-terminal", 4.0),
        shoulder=_number(element, "data-brace-shoulder", float(round(5.0 * depth / 9.0))),
        outer_radius=_number(element, "data-brace-outer-radius", 6.0),
        cusp_radius=_number(element, "data-brace-cusp-radius", 5.0),
        cusp_neck=_number(element, "data-brace-cusp-neck", depth - 3.0),
    )
    element.set("d", spec["path"]["d"])
    return spec


def _segments_close(actual: list[tuple], expected: list[tuple], tolerance: float = 0.25) -> bool:
    if len(actual) != len(expected):
        return False
    for left, right in zip(actual, expected, strict=True):
        if left[0] != right[0] or len(left) != len(right):
            return False
        for a, b in zip(left[1:], right[1:], strict=True):
            if abs(float(a) - float(b)) > tolerance:
                return False
    return True


def _command_signature(segments: list[tuple]) -> str:
    return "".join(segment[0] for segment in segments)


def _brace_local_point(
    point: tuple[float, float], spec: dict[str, Any]
) -> tuple[float, float]:
    """Map an SVG-local brace point back to its canonical along/depth basis."""

    start = spec["axis_start"]
    dx = float(point[0]) - float(start["x"])
    dy = float(point[1]) - float(start["y"])
    orientation = spec["orientation"]
    if orientation == "under":
        return dx, dy
    if orientation == "over":
        return dx, -dy
    if orientation == "right":
        return dy, dx
    return dy, -dx


def _brace_symmetry_contract(
    segments: list[tuple], spec: dict[str, Any]
) -> dict[str, Any]:
    """Verify two mirrored lobes and one geometrically central shared cusp."""

    length = float(spec["length_px"])
    malformed = (
        _command_signature(segments) != "MLCLCLMLCLCL" or len(segments) != 12
    )
    if malformed:
        return {
            "center_tolerance_px": BRACE_CUSP_CENTER_TOLERANCE_PX,
            "center_tolerance_normalized": BRACE_CUSP_CENTER_TOLERANCE_NORMALIZED,
            "symmetry_tolerance_px": BRACE_SYMMETRY_TOLERANCE_PX,
            "center_error_px": math.inf,
            "center_error_normalized": math.inf,
            "cusp_join_error_px": math.inf,
            "neck_join_error_px": math.inf,
            "lobe_mirror_error_px": math.inf,
            "central_cusp_pass": False,
            "double_lobe_mirror_pass": False,
            "pass": False,
        }

    def endpoint(index: int) -> tuple[float, float]:
        segment = segments[index]
        return _brace_local_point((float(segment[-2]), float(segment[-1])), spec)

    def control(index: int, offset: int) -> tuple[float, float]:
        segment = segments[index]
        return _brace_local_point(
            (float(segment[offset]), float(segment[offset + 1])), spec
        )

    def mirrored(point: tuple[float, float]) -> tuple[float, float]:
        return length - point[0], point[1]

    def distance(left: tuple[float, float], right: tuple[float, float]) -> float:
        return math.hypot(left[0] - right[0], left[1] - right[1])

    cusp_left = endpoint(5)
    cusp_right = endpoint(6)
    neck_left = endpoint(4)
    neck_right = endpoint(7)
    center_error_px = max(
        abs(cusp_left[0] - length / 2.0),
        abs(cusp_right[0] - length / 2.0),
        abs(neck_left[0] - length / 2.0),
        abs(neck_right[0] - length / 2.0),
    )
    center_error_normalized = center_error_px / length
    cusp_join_error = distance(cusp_left, cusp_right)
    neck_join_error = distance(neck_left, neck_right)

    # The right lobe is traversed cusp-to-terminal.  Compare the left lobe to
    # its reversed, axis-reflected counterpart, including cubic controls.
    mirror_pairs = [
        (endpoint(0), mirrored(endpoint(11))),
        (endpoint(1), mirrored(endpoint(10))),
        (control(2, 1), mirrored(control(10, 3))),
        (control(2, 3), mirrored(control(10, 1))),
        (endpoint(2), mirrored(endpoint(9))),
        (endpoint(3), mirrored(endpoint(8))),
        (control(4, 1), mirrored(control(8, 3))),
        (control(4, 3), mirrored(control(8, 1))),
        (endpoint(4), mirrored(endpoint(7))),
        (endpoint(5), mirrored(endpoint(6))),
    ]
    lobe_mirror_error = max(distance(left, right) for left, right in mirror_pairs)
    central_cusp_pass = (
        center_error_px <= BRACE_CUSP_CENTER_TOLERANCE_PX
        and center_error_normalized <= BRACE_CUSP_CENTER_TOLERANCE_NORMALIZED
        and cusp_join_error <= BRACE_SYMMETRY_TOLERANCE_PX
        and neck_join_error <= BRACE_SYMMETRY_TOLERANCE_PX
    )
    double_lobe_mirror_pass = lobe_mirror_error <= BRACE_SYMMETRY_TOLERANCE_PX
    return {
        "center_tolerance_px": BRACE_CUSP_CENTER_TOLERANCE_PX,
        "center_tolerance_normalized": BRACE_CUSP_CENTER_TOLERANCE_NORMALIZED,
        "symmetry_tolerance_px": BRACE_SYMMETRY_TOLERANCE_PX,
        "center_error_px": center_error_px,
        "center_error_normalized": center_error_normalized,
        "cusp_join_error_px": cusp_join_error,
        "neck_join_error_px": neck_join_error,
        "lobe_mirror_error_px": lobe_mirror_error,
        "central_cusp_pass": central_cusp_pass,
        "double_lobe_mirror_pass": double_lobe_mirror_pass,
        "pass": central_cusp_pass and double_lobe_mirror_pass,
    }


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _brace_hash_contract(
    canonical_hash: Any,
    legacy_hash: Any,
    expected_hash: str,
) -> dict[str, bool]:
    """Evaluate canonical BraceSpec and its temporary PrimitiveSpec alias.

    A valid legacy hash may stand in only when the canonical field is absent.
    That migration path lets the remaining fidelity gates run, but callers must
    still emit an explicit migration finding.  When both hashes exist they are
    required to match exactly.
    """

    canonical_hash_pass = canonical_hash == expected_hash
    legacy_hash_pass = legacy_hash is None or legacy_hash == expected_hash
    alias_hash_match = (
        canonical_hash is None
        or legacy_hash is None
        or canonical_hash == legacy_hash
    )
    migration_alias_used = canonical_hash is None and legacy_hash == expected_hash
    effective_hash_pass = (
        (canonical_hash_pass or migration_alias_used)
        and legacy_hash_pass
        and alias_hash_match
    )
    return {
        "canonical_hash_pass": canonical_hash_pass,
        "legacy_hash_pass": legacy_hash_pass,
        "alias_hash_match": alias_hash_match,
        "migration_alias_used": migration_alias_used,
        "effective_hash_pass": effective_hash_pass,
    }


def _spec_value_hash(value: Any) -> Any:
    """Hash a spec object while preserving invalid non-object sentinels."""

    return _sha256_json(value) if isinstance(value, dict) else value


def _svg_braces(root: ET.Element) -> list[tuple[ET.Element, Matrix]]:
    result: list[tuple[ET.Element, Matrix]] = []

    def walk(element: ET.Element, parent: Matrix) -> None:
        matrix = parent.multiply(parse_transform(element.get("transform")))
        if element.tag == f"{SVG_NS}path" and (
            element.get("data-primitive-kind") == "brace"
            or re.search(r"(?:brace|bracket)", element.get("id", ""), re.IGNORECASE)
        ):
            result.append((element, matrix))
        for child in element:
            walk(child, matrix)

    walk(root, Matrix())
    return result


def _declared_primitive_expectations(run: common.Run) -> list[dict[str, Any]]:
    """Read fail-closed primitive inventory expectations from regions.json.

    Expectations are case-owned visual contracts, not facts inferred from the
    candidate.  At present only the canonical brace primitive has a complete
    source/scene/PPTX readback implementation, so unsupported kinds fail instead
    of receiving a misleading count-only PASS.
    """

    payload = read_json(run.regions_path)
    if "primitive_expectations" not in payload:
        return [
            {
                "kind": "[missing]",
                "count": None,
                "primitives": [],
                "error": "primitive_expectations field is missing",
            }
        ]
    raw = payload.get("primitive_expectations", [])
    if not isinstance(raw, list):
        return [
            {
                "kind": "[invalid]",
                "count": None,
                "primitives": [],
                "error": "primitive_expectations must be an array",
            }
        ]
    expectations: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            expectations.append(
                {
                    "kind": "[invalid]",
                    "count": None,
                    "primitives": [],
                    "error": f"primitive_expectations[{index}] must be an object",
                }
            )
            continue
        kind = item.get("kind")
        count = item.get("count")
        primitives = item.get("primitives")
        error = None
        if kind != "brace":
            error = f"unsupported primitive expectation kind: {kind!r}"
        elif not isinstance(count, int) or isinstance(count, bool) or count < 1:
            error = "primitive expectation count must be a positive integer"
        elif not isinstance(primitives, list) or not primitives:
            error = "primitive expectation primitives must be a nonempty array"
        else:
            primitive_errors: list[str] = []
            primitive_ids: list[str] = []
            for primitive_index, primitive in enumerate(primitives):
                if not isinstance(primitive, dict):
                    primitive_errors.append(
                        f"primitives[{primitive_index}] must be an object"
                    )
                    continue
                element_id = primitive.get("element_id")
                spec_hash = primitive.get("primitive_spec_sha256")
                if not isinstance(element_id, str) or not element_id:
                    primitive_errors.append(
                        f"primitives[{primitive_index}].element_id must be a nonempty string"
                    )
                else:
                    primitive_ids.append(element_id)
                if not isinstance(spec_hash, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", spec_hash
                ):
                    primitive_errors.append(
                        f"primitives[{primitive_index}].primitive_spec_sha256 must be "
                        "a lowercase SHA-256 digest"
                    )
            if len(primitive_ids) != len(set(primitive_ids)):
                primitive_errors.append("primitive expectation element_ids must be unique")
            if len(primitives) != count:
                primitive_errors.append(
                    "primitive expectation count must equal len(primitives)"
                )
            error = "; ".join(primitive_errors) or None
        expectations.append(
            {
                "kind": kind,
                "count": count,
                "primitives": primitives if isinstance(primitives, list) else [],
                "error": error,
            }
        )
    return expectations


def _audit_primitive_expectations(
    expectations: list[dict[str, Any]],
    candidates: list[tuple[ET.Element, Matrix]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    actual_by_kind: dict[str, list[dict[str, Any]]] = {"brace": []}
    for element, matrix in candidates:
        if element.get("data-primitive-kind") != "brace":
            continue
        element_id = element.get("id") or "[missing-id]"
        spec_hash = None
        try:
            local_spec = materialize_brace(deepcopy(element))
            assert local_spec is not None
            spec_hash = _sha256_json(transform_brace_spec(local_spec, matrix))
        except (AssertionError, TypeError, ValueError):
            pass
        actual_by_kind["brace"].append(
            {
                "element_id": element_id,
                "brace_spec_sha256": spec_hash,
                "primitive_spec_sha256": spec_hash,
            }
        )

    brace_candidates = actual_by_kind["brace"]
    if brace_candidates and not any(
        expectation.get("kind") == "brace" and expectation.get("error") is None
        for expectation in expectations
    ):
        findings.append(
            {
                "code": "P12_EXPECTATION",
                "element": "[brace]",
                "message": (
                    "canonical brace primitives require an exact, case-owned "
                    "primitive_expectations contract"
                ),
            }
        )
        records.append(
            {
                "kind": "brace",
                "expected_count": None,
                "actual_count": len(brace_candidates),
                "expected_primitives": [],
                "actual_primitives": brace_candidates,
                "missing_element_ids": [],
                "unexpected_element_ids": [
                    item["element_id"] for item in brace_candidates
                ],
                "duplicate_element_ids": [],
                "hash_mismatches": [],
                "pass": False,
            }
        )
    for expectation in expectations:
        kind = expectation.get("kind", "[invalid]")
        error = expectation.get("error")
        expected_primitives = expectation.get("primitives", [])
        expected_ids = [
            item.get("element_id")
            for item in expected_primitives
            if isinstance(item, dict) and isinstance(item.get("element_id"), str)
        ]
        actual_primitives = actual_by_kind.get(kind, [])
        actual_ids = [item["element_id"] for item in actual_primitives]
        missing = sorted(set(expected_ids) - set(actual_ids))
        unexpected = sorted(set(actual_ids) - set(expected_ids))
        duplicates = sorted(
            value for value in set(actual_ids) if actual_ids.count(value) > 1
        )
        count_pass = (
            isinstance(expectation.get("count"), int)
            and len(actual_ids) == expectation["count"]
        )
        ids_pass = not missing and not unexpected and not duplicates
        expected_hashes = {
            item["element_id"]: item.get("primitive_spec_sha256")
            for item in expected_primitives
            if isinstance(item, dict) and isinstance(item.get("element_id"), str)
        }
        actual_hashes = {
            item["element_id"]: item.get("primitive_spec_sha256")
            for item in actual_primitives
        }
        hash_mismatches = sorted(
            element_id
            for element_id in set(expected_ids) & set(actual_ids)
            if expected_hashes.get(element_id) != actual_hashes.get(element_id)
        )
        hashes_pass = not hash_mismatches
        if error:
            findings.append(
                {
                    "code": "P12_EXPECTATION",
                    "element": f"[{kind}]",
                    "message": error,
                }
            )
        elif not count_pass or not ids_pass or not hashes_pass:
            findings.append(
                {
                    "code": "P12_EXPECTATION",
                    "element": f"[{kind}]",
                    "message": (
                        f"expected {expectation['count']} canonical {kind} primitive(s) "
                        f"with exact ids {expected_ids}; found {len(actual_ids)} ids "
                        f"{actual_ids}; missing={missing}, unexpected={unexpected}, "
                        f"duplicates={duplicates}, hash_mismatches={hash_mismatches}"
                    ),
                }
            )
        records.append(
            {
                "kind": kind,
                "expected_count": expectation.get("count"),
                "actual_count": len(actual_ids),
                "expected_primitives": expected_primitives,
                "actual_primitives": actual_primitives,
                "missing_element_ids": missing,
                "unexpected_element_ids": unexpected,
                "duplicate_element_ids": duplicates,
                "hash_mismatches": hash_mismatches,
                "pass": error is None and count_pass and ids_pass and hashes_pass,
            }
        )
    return records, findings


def _fill_readback(properties: ET.Element | None) -> dict[str, Any] | None:
    if properties is None:
        return None
    if properties.find("a:noFill", PPTX_NS) is not None:
        return {"kind": "none", "color": None}
    solid = properties.find("a:solidFill", PPTX_NS)
    if solid is not None:
        color = solid.find("a:srgbClr", PPTX_NS)
        return {
            "kind": "solid",
            "color": None if color is None else f"#{color.get('val', '').upper()}",
        }
    for tag, kind in (
        ("a:gradFill", "gradient"),
        ("a:blipFill", "picture"),
        ("a:pattFill", "pattern"),
        ("a:grpFill", "group"),
    ):
        if properties.find(tag, PPTX_NS) is not None:
            return {"kind": kind, "color": None}
    return None


def _stroke_readback(properties: ET.Element | None) -> dict[str, Any] | None:
    line = None if properties is None else properties.find("a:ln", PPTX_NS)
    if line is None:
        return None
    color = line.find("./a:solidFill/a:srgbClr", PPTX_NS)
    width = line.get("w")
    try:
        width_px = None if width is None else float(width) / EMU_PER_PX
    except ValueError:
        width_px = None
    raw_cap = line.get("cap")
    return {
        "color": None if color is None else f"#{color.get('val', '').upper()}",
        "width_px": width_px,
        # Missing cap is deliberately not defaulted: primitive QA is fail-closed.
        "line_cap": {"rnd": "round", "sq": "square", "flat": "butt"}.get(
            raw_cap, raw_cap
        ),
    }


def _read_pptx_primitive_properties(path: Path) -> list[dict[str, Any]]:
    """Read native identity, style, and cNvPr description without inferred defaults."""

    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("ppt/slides/slide1.xml"))
    tree = root.find("./p:cSld/p:spTree", PPTX_NS)
    if tree is None:
        raise ValueError("PowerPoint slide has no shape tree")
    records: list[dict[str, Any]] = []

    def visit(node: ET.Element, parent_group_id: int | None = None) -> None:
        local = node.tag.rsplit("}", 1)[-1]
        if local == "sp":
            identity = node.find("./p:nvSpPr/p:cNvPr", PPTX_NS)
            properties = node.find("./p:spPr", PPTX_NS)
            kind = "shape"
        elif local == "cxnSp":
            identity = node.find("./p:nvCxnSpPr/p:cNvPr", PPTX_NS)
            properties = node.find("./p:spPr", PPTX_NS)
            kind = "connector"
        elif local == "grpSp":
            identity = node.find("./p:nvGrpSpPr/p:cNvPr", PPTX_NS)
            properties = node.find("./p:grpSpPr", PPTX_NS)
            kind = "group"
        else:
            return
        if identity is None:
            return
        raw_shape_id = identity.get("id")
        try:
            shape_id = None if raw_shape_id is None else int(raw_shape_id)
        except ValueError:
            shape_id = None
        records.append(
            {
                "shape_id": shape_id,
                "shape_name": identity.get("name"),
                "description": identity.get("descr"),
                "ooxml_kind": kind,
                "parent_group_id": parent_group_id,
                "fill": _fill_readback(properties),
                "stroke": _stroke_readback(properties),
            }
        )
        if kind == "group":
            for child in node:
                if child.tag in {
                    f"{{{P_NS}}}sp",
                    f"{{{P_NS}}}cxnSp",
                    f"{{{P_NS}}}grpSp",
                }:
                    visit(child, shape_id)

    for child in tree:
        if child.tag in {f"{{{P_NS}}}sp", f"{{{P_NS}}}cxnSp", f"{{{P_NS}}}grpSp"}:
            visit(child)
    return records


def _identity_index(records: list[dict[str, Any]]) -> dict[tuple[int, str], list[dict[str, Any]]]:
    result: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for item in records:
        shape_id = item.get("shape_id")
        shape_name = item.get("shape_name")
        if isinstance(shape_id, int) and isinstance(shape_name, str) and shape_name:
            result.setdefault((shape_id, shape_name), []).append(item)
    return result


def _style_passes(
    expected: dict[str, Any], actual: dict[str, Any] | None
) -> tuple[bool, bool]:
    if not isinstance(actual, dict):
        return False, False
    fill = actual.get("fill")
    expected_fill = expected.get("fill")
    if expected_fill == "none":
        fill_pass = isinstance(fill, dict) and fill.get("kind") == "none"
    else:
        fill_pass = (
            isinstance(fill, dict)
            and fill.get("kind") == "solid"
            and fill.get("color") == str(expected_fill).upper()
        )
    stroke = actual.get("stroke")
    width = None if not isinstance(stroke, dict) else stroke.get("width_px")
    expected_width = expected.get("width_px")
    stroke_pass = (
        isinstance(stroke, dict)
        and stroke.get("color") == str(expected.get("color")).upper()
        and isinstance(width, (int, float))
        and isinstance(expected_width, (int, float))
        and math.isfinite(float(width))
        and math.isfinite(float(expected_width))
        and math.isclose(float(width), float(expected_width), abs_tol=0.001)
        and stroke.get("line_cap") == expected.get("line_cap")
    )
    return fill_pass, stroke_pass


def _description_contract(
    description: Any, element_id: str, expected_hash: str
) -> dict[str, bool]:
    empty = {
        "identity_pass": False,
        "canonical_hash_pass": False,
        "legacy_hash_pass": False,
        "alias_hash_match": True,
        "migration_alias_used": False,
        "effective_hash_pass": False,
    }
    if not isinstance(description, str) or not description:
        return empty
    try:
        payload = json.loads(description)
    except (TypeError, ValueError):
        return empty
    if not isinstance(payload, dict):
        return empty
    return {
        "identity_pass": payload.get("autofigure_element_id") == element_id,
        **_brace_hash_contract(
            payload.get("brace_spec_sha256"),
            payload.get("primitive_spec_sha256"),
            expected_hash,
        ),
    }


def audit_primitives(run: common.Run) -> dict[str, Any]:
    from tools.pptx_arrows import read_pptx_inventory

    root = ET.parse(run.redraw_svg).getroot()
    scene = read_json(run.scene_path)
    bindings = read_json(run.bindings_path)
    findings: list[dict[str, Any]] = []
    try:
        inventory = read_pptx_inventory(run.pptx_path)
        primitive_properties = _read_pptx_primitive_properties(run.pptx_path)
    except (ET.ParseError, KeyError, OSError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        inventory = []
        primitive_properties = []
        findings.append(
            {
                "code": "P0",
                "element": "[pptx-readback]",
                "message": f"saved PPTX primitive readback unavailable: {exc}",
            }
        )
    inventory_by_identity = _identity_index(inventory)
    properties_by_identity = _identity_index(primitive_properties)
    scene_by_id = {item.get("id"): item for item in scene.get("elements", [])}
    bindings_by_id: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings.get("bindings", []):
        bindings_by_id.setdefault(binding.get("element_id", ""), []).append(binding)

    candidates = _svg_braces(root)
    expectations = _declared_primitive_expectations(run)
    expectation_records, expectation_findings = _audit_primitive_expectations(
        expectations, candidates
    )
    findings.extend(expectation_findings)
    records: list[dict[str, Any]] = []
    for element, matrix in candidates:
        element_id = element.get("id") or "[missing-id]"
        actual_d = element.get("d", "")
        if element.get("data-primitive-kind") != "brace":
            findings.append(
                {
                    "code": "P1",
                    "element": element_id,
                    "message": "brace-like path has no canonical primitive contract",
                }
            )
            continue
        try:
            local_spec = materialize_brace(element)
            assert local_spec is not None
            expected_spec = transform_brace_spec(local_spec, matrix)
            transform_contract = _brace_transform_contract(
                local_spec, expected_spec, matrix
            )
            actual_local_segments = parse_path_d(actual_d)
            expected_local_segments = parse_path_d(local_spec["path"]["d"])
            expected_canvas_segments = parse_path_d(expected_spec["path"]["d"])
        except (AssertionError, TypeError, ValueError) as exc:
            findings.append(
                {"code": "P2", "element": element_id, "message": f"invalid brace contract: {exc}"}
            )
            continue
        expected_hash = _sha256_json(expected_spec)

        if not transform_contract["pass"]:
            findings.append(
                {
                    "code": "P13_TRANSFORM",
                    "element": element_id,
                    "message": (
                        "brace transform must be a similarity transform and preserve "
                        "the declared canvas-facing orientation"
                    ),
                    "metrics": transform_contract,
                }
            )

        signature = _command_signature(actual_local_segments)
        symmetry_contract = _brace_symmetry_contract(actual_local_segments, local_spec)
        if not symmetry_contract["pass"]:
            findings.append(
                {
                    "code": "P14_SYMMETRY",
                    "element": element_id,
                    "message": (
                        "brace must have a central shared cusp and two mirrored lobes "
                        "within the declared absolute and normalized tolerances"
                    ),
                    "metrics": symmetry_contract,
                }
            )
        source_path_pass = (
            signature == expected_spec["expected_command_signature"]
            and _segments_close(
                actual_local_segments,
                expected_local_segments,
                tolerance=SOURCE_PATH_TOLERANCE_PX,
            )
        )
        if not source_path_pass:
            findings.append(
                {
                    "code": "P3",
                    "element": element_id,
                    "message": "brace path is not the canonical two-lobe/cusp geometry",
                }
            )
        scene_element = scene_by_id.get(element_id, {})
        scene_contract = _brace_hash_contract(
            _spec_value_hash(scene_element.get("brace_spec")),
            _spec_value_hash(scene_element.get("primitive_spec")),
            expected_hash,
        )
        scene_pass = scene_contract["effective_hash_pass"]
        if (
            not scene_contract["canonical_hash_pass"]
            and not scene_contract["migration_alias_used"]
        ):
            findings.append(
                {
                    "code": "P4",
                    "element": element_id,
                    "message": (
                        "scene brace_spec is missing or stale; legacy primitive_spec "
                        "is accepted only as an explicit migration alias"
                    ),
                }
            )
        scene_geometry = scene_element.get("geometry")
        scene_geometry_d = (
            scene_geometry.get("d") if isinstance(scene_geometry, dict) else None
        )
        try:
            scene_geometry_segments = parse_path_d(scene_geometry_d)
        except (TypeError, ValueError):
            scene_geometry_segments = []
        scene_geometry_signature = _command_signature(scene_geometry_segments)
        scene_geometry_signature_pass = (
            scene_geometry_signature == expected_spec["expected_command_signature"]
        )
        scene_geometry_pass = scene_geometry_signature_pass and _segments_close(
            scene_geometry_segments,
            expected_local_segments,
            tolerance=SOURCE_PATH_TOLERANCE_PX,
        )
        if not source_path_pass or not scene_geometry_pass:
            findings.append(
                {
                    "code": "P11_SOURCE_PATH",
                    "element": element_id,
                    "message": (
                        "SVG/scene source geometry is missing, has a different command signature, "
                        "or differs from the canonical BraceSpec path by more than 0.01 px"
                    ),
                }
            )
        rows = bindings_by_id.get(element_id, [])
        visible = [item for item in rows if item.get("object_kind") not in {"arrow-group"}]
        object_pass = (
            len(visible) == 1
            and visible[0].get("object_kind") == "freeform"
            and visible[0].get("editable") is True
            and visible[0].get("readback_found") is True
        )
        if not object_pass:
            findings.append(
                {
                    "code": "P5",
                    "element": element_id,
                    "message": (
                        "brace must bind one found, editable native freeform object, "
                        f"found {len(visible)} binding row(s)"
                    ),
                }
            )
        row = visible[0] if len(visible) == 1 else None
        shape_id = None if row is None else row.get("shape_id")
        shape_name = None if row is None else row.get("shape_name")
        identity_complete = (
            isinstance(shape_id, int) and isinstance(shape_name, str) and bool(shape_name)
        )
        identity = (shape_id, shape_name) if identity_complete else None
        native_rows = [] if identity is None else inventory_by_identity.get(identity, [])
        property_rows = [] if identity is None else properties_by_identity.get(identity, [])
        native_identity_pass = (
            identity_complete
            and len(native_rows) == 1
            and len(property_rows) == 1
            and native_rows[0].get("ooxml_kind") == "shape"
            and property_rows[0].get("ooxml_kind") == "shape"
        )
        if not native_identity_pass:
            findings.append(
                {
                    "code": "P7",
                    "element": element_id,
                    "message": "saved PPTX object did not match the exact (shape_id, shape_name) binding",
                }
            )
        native = native_rows[0] if native_identity_pass else None
        native_properties = property_rows[0] if native_identity_pass else None
        native_segments = None if native is None else native.get("segments")
        native_pass = isinstance(native_segments, list) and _segments_close(
            native_segments, expected_canvas_segments
        )
        if not native_pass:
            findings.append(
                {
                    "code": "P6",
                    "element": element_id,
                    "message": "saved PPTX freeform path does not match the canonical brace",
                }
            )
        fill_pass, stroke_pass = _style_passes(
            expected_spec.get("stroke", {}), native_properties
        )
        if not fill_pass:
            findings.append(
                {
                    "code": "P8",
                    "element": element_id,
                    "message": "saved PPTX brace fill is missing, unreadable, or differs from BraceSpec",
                }
            )
        if not stroke_pass:
            findings.append(
                {
                    "code": "P9",
                    "element": element_id,
                    "message": (
                        "saved PPTX brace stroke color, width, or cap is missing, unreadable, "
                        "or differs from BraceSpec"
                    ),
                }
            )
        binding_contract = _brace_hash_contract(
            None if row is None else row.get("brace_spec_sha256"),
            None if row is None else row.get("primitive_spec_sha256"),
            expected_hash,
        )
        description_contract = _description_contract(
            None if native_properties is None else native_properties.get("description"),
            element_id,
            expected_hash,
        )
        binding_hash_pass = binding_contract["effective_hash_pass"]
        description_identity_pass = description_contract["identity_pass"]
        embedded_hash_pass = description_contract["effective_hash_pass"]
        migration_locations = [
            location
            for location, contract in (
                ("scene", scene_contract),
                ("bindings", binding_contract),
                ("pptx-description", description_contract),
            )
            if contract["migration_alias_used"]
        ]
        alias_mismatch_locations = [
            location
            for location, contract in (
                ("scene", scene_contract),
                ("bindings", binding_contract),
                ("pptx-description", description_contract),
            )
            if not contract["alias_hash_match"]
        ]
        if migration_locations:
            findings.append(
                {
                    "code": "P15_BRACE_SPEC_MIGRATION",
                    "element": element_id,
                    "message": (
                        "legacy primitive_spec alias accepted for migration at "
                        f"{migration_locations}; schema 4 requires canonical brace_spec / "
                        "brace_spec_sha256 without changing the brace geometry"
                    ),
                    "locations": migration_locations,
                }
            )
        if alias_mismatch_locations:
            findings.append(
                {
                    "code": "P16_BRACE_SPEC_ALIAS",
                    "element": element_id,
                    "message": (
                        "canonical BraceSpec and legacy PrimitiveSpec alias hashes differ at "
                        f"{alias_mismatch_locations}"
                    ),
                    "locations": alias_mismatch_locations,
                }
            )
        if not binding_hash_pass or not description_identity_pass or not embedded_hash_pass:
            findings.append(
                {
                    "code": "P10",
                    "element": element_id,
                    "message": (
                        "BraceSpec hash or element identity is missing, unreadable, or stale in "
                        "bindings/cNvPr description; legacy aliases are accepted only for migration"
                    ),
                }
            )
        records.append(
            {
                "element_id": element_id,
                "orientation": expected_spec["orientation"],
                "command_signature": signature,
                "expected_subpaths": 2,
                "brace_spec_sha256": expected_hash,
                "primitive_spec_sha256": expected_hash,
                "transform_contract": transform_contract,
                "symmetry_contract": symmetry_contract,
                "path_pass": source_path_pass,
                "source_path_pass": source_path_pass,
                "scene_geometry_signature": scene_geometry_signature,
                "scene_geometry_signature_pass": scene_geometry_signature_pass,
                "scene_geometry_pass": scene_geometry_pass,
                "scene_pass": scene_pass,
                "scene_brace_spec_hash_pass": scene_contract["canonical_hash_pass"],
                "scene_legacy_hash_pass": scene_contract["legacy_hash_pass"],
                "scene_alias_hash_match": scene_contract["alias_hash_match"],
                "object_count": len(visible),
                "object_pass": object_pass,
                "native_identity_pass": native_identity_pass,
                "powerpoint_path_pass": native_pass,
                "powerpoint_fill_pass": fill_pass,
                "powerpoint_stroke_pass": stroke_pass,
                "binding_hash_pass": binding_hash_pass,
                "binding_brace_spec_hash_pass": binding_contract["canonical_hash_pass"],
                "binding_legacy_hash_pass": binding_contract["legacy_hash_pass"],
                "binding_alias_hash_match": binding_contract["alias_hash_match"],
                "description_identity_pass": description_identity_pass,
                "embedded_hash_pass": embedded_hash_pass,
                "embedded_brace_spec_hash_pass": description_contract[
                    "canonical_hash_pass"
                ],
                "embedded_legacy_hash_pass": description_contract["legacy_hash_pass"],
                "embedded_alias_hash_match": description_contract["alias_hash_match"],
                "brace_spec_migration_locations": migration_locations,
                "brace_spec_alias_mismatch_locations": alias_mismatch_locations,
            }
        )

    report = {
        "schema_version": "1.0.0",
        "kind": "vector_primitive_audit",
        "created_at": bindings.get("updated_at") or utc_now(),
        "case": run.root.name,
        "reference_sha256": run.load_meta()["source_sha256"],
        "svg_sha256": common.sha256_file(run.redraw_svg),
        "scene_sha256": common.sha256_file(run.scene_path),
        "artifact_sha256": common.sha256_file(run.pptx_path),
        "primitive_count": len(candidates),
        "expectations": expectation_records,
        "records": records,
        "findings": findings,
        "pass": not findings,
    }
    write_json(run.qa_dir / "primitive-audit.json", report)
    return report


def strict_blockers(report: dict[str, Any]) -> list[str]:
    return [
        f"primitive:{item.get('code', 'unknown')}:{item.get('element', '[missing-id]')}"
        for item in report.get("findings", [])
    ]
