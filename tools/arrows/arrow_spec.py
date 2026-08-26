"""Canonical, backend-neutral arrow contracts used by every input route.

The SVG and reference-only routes may infer this contract differently, but the
compiler and QA gates consume the same normalized structure.  The contract is
deliberately independent from PowerPoint object ids and compiler decisions.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

ARROW_SPEC_VERSION = "1.1.0"
HEAD_TYPES = {"none", "open", "triangle", "stealth", "diamond", "oval", "custom"}
HEAD_SIZES = {"sm", "med", "lg"}
PATH_KINDS = {"straight", "polyline", "cubic"}
ROUTING_MODES = {"fixed", "host"}
TOPOLOGY_MODES = {"attached", "declared", "none"}
REPRESENTATIONS = {"line_arrow", "block_arrow"}
BLOCK_AUTOSHAPES = {"leftRightArrow"}
INPUT_ROUTES = {"reference-only", "svg-seeded"}
LINE_CAPS = {"butt", "round", "square"}
LINE_JOINS = {"miter", "round", "bevel"}
DASH_TO_OOXML = {
    "solid": None,
    "square_dot": "dot",
    "round_dot": "dot",
    "dash": "dash",
    "dash_dot": "dashDot",
    "dash_dot_dot": "sysDashDotDot",
    "long_dash": "lgDash",
    "long_dash_dot": "lgDashDot",
    "long_dash_dot_dot": "lgDashDotDot",
    "sys_dash": "sysDash",
    "sys_dot": "sysDot",
    "sys_dash_dot": "sysDashDot",
}
OOXML_TO_DASH = {
    "dash": "dash",
    "dashDot": "dash_dot",
    "sysDashDotDot": "dash_dot_dot",
    "lgDash": "long_dash",
    "lgDashDot": "long_dash_dot",
    "lgDashDotDot": "long_dash_dot_dot",
    "sysDash": "sys_dash",
    "sysDot": "sys_dot",
    "sysDashDot": "sys_dash_dot",
}


def semantic_dash_from_ooxml(value: str | None, line_cap: str | None = None) -> str:
    if value is None or value == "solid":
        return "solid"
    if value == "dot":
        return "round_dot" if line_cap in {"round", "rnd"} else "square_dot"
    return OOXML_TO_DASH.get(value, value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def _point(x: float, y: float) -> dict[str, float]:
    return {"x": float(x), "y": float(y)}


def path_from_segments(segments: Iterable[tuple]) -> dict[str, Any]:
    """Normalize M/L/C path segments to straight, polyline, or cubic.

    Mixed line/cubic paths become cubic paths with exact degenerate cubic
    segments for the line portions.  Multiple subpaths and closed paths are not
    valid line-arrow centerlines and therefore fail explicitly.
    """

    parts = list(segments)
    if not parts or parts[0][0] != "M":
        raise ValueError("arrow path must start with M")
    if any(part[0] == "Z" for part in parts):
        raise ValueError("line-arrow centerline cannot be closed")
    if sum(1 for part in parts if part[0] == "M") != 1:
        raise ValueError("line-arrow centerline must contain one subpath")

    only_lines = all(part[0] in {"M", "L"} for part in parts)
    points = [_point(part[1], part[2]) for part in parts if part[0] in {"M", "L"}]
    if only_lines:
        if len(points) < 2:
            raise ValueError("arrow path requires at least two points")
        return {
            "kind": "straight" if len(points) == 2 else "polyline",
            "coordinate_space": "canvas",
            "points": points,
        }

    start = (float(parts[0][1]), float(parts[0][2]))
    current = start
    cubics: list[dict[str, Any]] = []
    for part in parts[1:]:
        if part[0] == "L":
            end = (float(part[1]), float(part[2]))
            cubics.append(
                {
                    "control1": _point(
                        current[0] + (end[0] - current[0]) / 3,
                        current[1] + (end[1] - current[1]) / 3,
                    ),
                    "control2": _point(
                        current[0] + 2 * (end[0] - current[0]) / 3,
                        current[1] + 2 * (end[1] - current[1]) / 3,
                    ),
                    "end": _point(*end),
                }
            )
            current = end
        elif part[0] == "C":
            end = (float(part[5]), float(part[6]))
            cubics.append(
                {
                    "control1": _point(float(part[1]), float(part[2])),
                    "control2": _point(float(part[3]), float(part[4])),
                    "end": _point(*end),
                }
            )
            current = end
        else:
            raise ValueError(f"unsupported arrow path segment: {part[0]}")
    if not cubics:
        raise ValueError("cubic arrow path has no segments")
    return {
        "kind": "cubic",
        "coordinate_space": "canvas",
        "start": _point(*start),
        "segments": cubics,
    }


def silhouette_from_segments(segments: Iterable[tuple]) -> dict[str, Any]:
    parts = list(segments)
    if not parts or parts[0][0] != "M":
        raise ValueError("block-arrow silhouette must start with M")
    if sum(1 for part in parts if part[0] == "M") != 1 or parts[-1][0] != "Z":
        raise ValueError("block-arrow silhouette must be one closed subpath")
    if any(part[0] not in {"M", "L", "C", "Z"} for part in parts):
        raise ValueError("unsupported block-arrow silhouette segment")
    return {
        "coordinate_space": "canvas",
        "segments": [list(part) for part in parts],
        "closed": True,
    }


def head(
    head_type: str = "none",
    *,
    width: str | None = None,
    length: str | None = None,
    color: str | None = None,
    custom_path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": head_type,
        "width": None if head_type == "none" else (width or "med"),
        "length": None if head_type == "none" else (length or "med"),
        "color": color,
    }
    if custom_path is not None:
        result["custom_path"] = custom_path
    return result


def arrow_direction(spec: dict[str, Any]) -> str:
    start = spec.get("start_head", {}).get("type", "none") != "none"
    end = spec.get("end_head", {}).get("type", "none") != "none"
    if start and end:
        return "bidirectional"
    if start:
        return "backward"
    if end:
        return "forward"
    return "undirected"


def compiler_strategy(spec: dict[str, Any]) -> str:
    """Return the one deterministic PowerPoint representation for a spec."""

    if spec.get("representation") == "block_arrow":
        autoshape = spec.get("autoshape")
        if autoshape is not None:
            if (
                isinstance(autoshape, dict)
                and autoshape.get("subtype") in BLOCK_AUTOSHAPES
                and spec.get("silhouette_path")
            ):
                return "native-block-autoshape"
            return "unsupported"
        if spec.get("silhouette_path"):
            return "single-closed-freeform"
        return "unsupported"

    heads = [spec.get("start_head", {}), spec.get("end_head", {})]
    if any(item.get("type") == "custom" for item in heads):
        return "single-closed-freeform" if spec.get("silhouette_path") else "unsupported"

    body_color = spec.get("body", {}).get("color")
    for item in heads:
        if item.get("type") != "none" and item.get("color") not in {None, body_color}:
            return "single-closed-freeform" if spec.get("silhouette_path") else "unsupported"

    path_kind = spec.get("path", {}).get("kind")
    topology = spec.get("topology", {})
    if spec.get("routing") == "host" and topology.get("mode") == "attached":
        return "native-connector-line-end"
    if path_kind == "straight":
        return "native-line-line-end"
    if path_kind in {"polyline", "cubic"}:
        return "native-freeform-line-end"
    return "unsupported"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _hex_color(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(character in "0123456789abcdefABCDEF" for character in value[1:])
    )


def _validate_point(point: Any, label: str, errors: list[str]) -> None:
    if not isinstance(point, dict) or not _finite_number(point.get("x")) or not _finite_number(point.get("y")):
        errors.append(f"{label}:invalid-point")


def validate_arrow_spec(
    spec: dict[str, Any],
    *,
    expected_input_route: str | None = None,
    expected_reference_sha256: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if spec.get("schema_version") != ARROW_SPEC_VERSION:
        errors.append("schema-version")
    if spec.get("representation") not in REPRESENTATIONS:
        errors.append("representation")
    if spec.get("routing") not in ROUTING_MODES:
        errors.append("routing")
    if spec.get("fallback_policy") != "strict_fail":
        errors.append("fallback-policy")
    if spec.get("single_visible_object") is not True:
        errors.append("single-visible-object")

    routing = spec.get("routing")
    topology = spec.get("topology")
    if not isinstance(topology, dict) or topology.get("mode") not in TOPOLOGY_MODES:
        errors.append("topology")
    else:
        topology_mode = topology.get("mode")
        if topology_mode in {"attached", "declared"} and (
            not topology.get("source_id") or not topology.get("target_id")
        ):
            errors.append("topology-endpoints")
        if topology_mode in {"attached", "declared"}:
            for site in ("source_site", "target_site"):
                value = topology.get(site)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    errors.append(f"topology-{site.replace('_', '-')}")
        if topology_mode == "none" and any(
            topology.get(key) is not None
            for key in ("source_id", "target_id", "source_site", "target_site")
        ):
            errors.append("topology-none-fields")
        if routing in ROUTING_MODES and (
            (routing == "host" and topology_mode != "attached")
            or (routing == "fixed" and topology_mode == "attached")
        ):
            errors.append("routing-topology")

    # Every logical arrow, including a filled block arrow, owns one semantic
    # centerline.  The closed silhouette is visible geometry, not a substitute
    # for the endpoint/tangent contract used by topology and strict readback.
    path = spec.get("path")
    path_error_start = len(errors)
    if not isinstance(path, dict) or path.get("kind") not in PATH_KINDS:
        errors.append("path-kind")
    elif path.get("coordinate_space") != "canvas":
        errors.append("path-coordinate-space")
    elif path.get("kind") in {"straight", "polyline"}:
        points = path.get("points")
        minimum = 2
        if not isinstance(points, list) or len(points) < minimum:
            errors.append("path-points")
        else:
            for index, point in enumerate(points):
                _validate_point(point, f"path.points[{index}]", errors)
            if path.get("kind") == "straight" and len(points) != 2:
                errors.append("straight-point-count")
    elif path.get("kind") == "cubic":
        _validate_point(path.get("start"), "path.start", errors)
        segments = path.get("segments")
        if not isinstance(segments, list) or not segments:
            errors.append("cubic-segments")
        else:
            for index, segment in enumerate(segments):
                if not isinstance(segment, dict):
                    errors.append(f"path.segments[{index}]")
                    continue
                for key in ("control1", "control2", "end"):
                    _validate_point(segment.get(key), f"path.segments[{index}].{key}", errors)
    if len(errors) == path_error_start:
        try:
            start, end = path_endpoints(path)
            start_tangent, end_tangent = path_tangents(path)
            if math.dist(start, end) <= 1e-9:
                errors.append("path-endpoints-degenerate")
            if math.hypot(*start_tangent) <= 1e-9:
                errors.append("path-start-tangent")
            if math.hypot(*end_tangent) <= 1e-9:
                errors.append("path-end-tangent")
        except (IndexError, KeyError, TypeError, ValueError):
            errors.append("path-geometry")

    body = spec.get("body")
    if not isinstance(body, dict) or not _finite_number(body.get("width_px")) or body.get("width_px", 0) <= 0:
        errors.append("body-width")
    else:
        if not _hex_color(body.get("color")):
            errors.append("body-color")
        if body.get("dash") not in DASH_TO_OOXML:
            errors.append("body-dash")
        if body.get("line_cap") not in LINE_CAPS:
            errors.append("body-line-cap")
        if body.get("line_join") not in LINE_JOINS:
            errors.append("body-line-join")

    for side in ("start_head", "end_head"):
        item = spec.get(side)
        if not isinstance(item, dict) or item.get("type") not in HEAD_TYPES:
            errors.append(f"{side}:type")
            continue
        if item.get("type") != "none":
            if item.get("width") not in HEAD_SIZES:
                errors.append(f"{side}:width")
            if item.get("length") not in HEAD_SIZES:
                errors.append(f"{side}:length")
            if not _hex_color(item.get("color")):
                errors.append(f"{side}:color")
        elif any(item.get(key) is not None for key in ("width", "length", "color")):
            errors.append(f"{side}:none-fields")
        if item.get("type") == "custom" and not isinstance(item.get("custom_path"), dict):
            errors.append(f"{side}:custom-path")

    autoshape = spec.get("autoshape")
    if spec.get("representation") == "block_arrow" and autoshape is not None:
        if not isinstance(autoshape, dict):
            errors.append("block-autoshape")
        else:
            if autoshape.get("subtype") not in BLOCK_AUTOSHAPES:
                errors.append("block-autoshape-subtype")
            adjustments = autoshape.get("adjustments")
            if not (
                isinstance(adjustments, list)
                and len(adjustments) == 2
                and all(_finite_number(value) and 0 <= value <= 1 for value in adjustments)
            ):
                errors.append("block-autoshape-adjustments")
            bbox = autoshape.get("bbox")
            if not (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(_finite_number(value) for value in bbox)
                and bbox[2] > 0
                and bbox[3] > 0
            ):
                errors.append("block-autoshape-bbox")
    if spec.get("representation") == "block_arrow" and not spec.get("silhouette_path"):
        errors.append("block-representation")
    if spec.get("representation") == "block_arrow" and spec.get("silhouette_path"):
        silhouette = spec.get("silhouette_path")
        segments = silhouette.get("segments") if isinstance(silhouette, dict) else None
        if not (
            isinstance(segments, list)
            and segments
            and segments[0][0] == "M"
            and segments[-1][0] == "Z"
            and sum(1 for segment in segments if segment[0] == "M") == 1
        ):
            errors.append("block-silhouette")
    evidence = spec.get("source_evidence")
    if not isinstance(evidence, dict):
        errors.append("source-evidence")
    else:
        input_route = evidence.get("input_route")
        reference_sha256 = evidence.get("reference_sha256")
        if input_route not in INPUT_ROUTES:
            errors.append("source-input-route")
        if not (
            isinstance(reference_sha256, str)
            and len(reference_sha256) == 64
            and all(character in "0123456789abcdefABCDEF" for character in reference_sha256)
        ):
            errors.append("source-reference-sha256")
        if expected_input_route is not None and input_route != expected_input_route:
            errors.append("source-input-route-mismatch")
        if (
            expected_reference_sha256 is not None
            and reference_sha256 != expected_reference_sha256
        ):
            errors.append("source-reference-sha256-mismatch")
        if input_route == "reference-only":
            bbox = evidence.get("reference_bbox")
            confidence = evidence.get("confidence")
            if not (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(_finite_number(value) for value in bbox)
                and bbox[2] > 0
                and bbox[3] > 0
            ):
                errors.append("reference-bbox")
            if not _finite_number(confidence) or not 0 <= confidence <= 1:
                errors.append("inference-confidence")
    return list(dict.fromkeys(errors))


def validate_scene_arrow_specs(scene: dict[str, Any]) -> list[str]:
    """Check ArrowSpec identity and resolve topology against the scene.

    The offline scene schema stores every drawable in ``elements`` while the
    live scene schema separates drawable ``nodes`` from ``edges``.  Accept
    either node carrier, but never treat the edge inventory itself as proof
    that a declared source or target exists.
    """

    errors: list[str] = []
    resolvable_ids = {
        item.get("id")
        for collection_name in ("elements", "nodes")
        for item in scene.get(collection_name, [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("id")
    }
    element_specs = {
        item.get("id"): item.get("arrow_spec")
        for item in scene.get("elements", [])
        if isinstance(item, dict) and isinstance(item.get("arrow_spec"), dict)
    }
    edge_specs = {
        item.get("id"): item.get("arrow_spec")
        for item in scene.get("edges", [])
        if isinstance(item, dict) and isinstance(item.get("arrow_spec"), dict)
    }
    for element_id, spec in element_specs.items():
        if element_id not in edge_specs:
            errors.append(f"arrow-scene-edge-missing:{element_id}")
        elif spec_sha256(spec) != spec_sha256(edge_specs[element_id]):
            errors.append(f"arrow-scene-edge-drift:{element_id}")
        topology = spec.get("topology")
        if isinstance(topology, dict) and topology.get("mode") in {
            "attached",
            "declared",
        }:
            for side in ("source", "target"):
                endpoint_id = topology.get(f"{side}_id")
                if endpoint_id not in resolvable_ids:
                    errors.append(
                        f"arrow-scene-topology-{side}-unresolved:"
                        f"{element_id}:{endpoint_id or '[missing]'}"
                    )
    for element_id in edge_specs.keys() - element_specs.keys():
        errors.append(f"arrow-scene-element-missing:{element_id}")
    return list(dict.fromkeys(errors))


def path_endpoints(path: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]]:
    if path["kind"] in {"straight", "polyline"}:
        points = path["points"]
        return (points[0]["x"], points[0]["y"]), (points[-1]["x"], points[-1]["y"])
    start = path["start"]
    end = path["segments"][-1]["end"]
    return (start["x"], start["y"]), (end["x"], end["y"])


def path_tangents(path: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]]:
    start, end = path_endpoints(path)
    if path["kind"] in {"straight", "polyline"}:
        points = path["points"]
        start_next = points[1]
        end_prev = points[-2]
        return (
            (start_next["x"] - start[0], start_next["y"] - start[1]),
            (end[0] - end_prev["x"], end[1] - end_prev["y"]),
        )
    first = path["segments"][0]["control1"]
    last = path["segments"][-1]["control2"]
    return (
        (first["x"] - start[0], first["y"] - start[1]),
        (end[0] - last["x"], end[1] - last["y"]),
    )
