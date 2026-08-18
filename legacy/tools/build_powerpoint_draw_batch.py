"""Build case-neutral native PowerPoint operation batches from Figure Spec v4."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

class DrawBatchError(RuntimeError):
    """Raised when the frozen specification cannot be rendered without guessing."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DrawBatchError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DrawBatchError(f"{label} must be one JSON object")
    return value


def _color(value: Any) -> str | None:
    if value is None or str(value).casefold() == "none":
        return None
    text = str(value)
    if len(text) == 7 and text.startswith("#"):
        return text
    raise DrawBatchError(f"unsupported color token: {value!r}")


def _base_binding(element_id: str, slide_id: int, *, name: str | None = None) -> dict[str, Any]:
    return {
        "slide_id": slide_id,
        "element_id": element_id,
        "semantic_id": f"semantic::{element_id}",
        "name": name or element_id,
    }


def _geometry(node: Mapping[str, Any]) -> dict[str, float]:
    return {
        "left": float(node["x"]),
        "top": float(node["y"]),
        "width": float(node["width"]),
        "height": float(node["height"]),
    }


def _phase_for(
    element: Mapping[str, Any], elements: Mapping[str, Mapping[str, Any]]
) -> str:
    explicit = element.get("region_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if element.get("parent_id") is None or element.get("type") == "background":
        return "foundation"
    current = element
    visited: set[str] = set()
    while current.get("parent_id") is not None:
        parent_id = str(current["parent_id"])
        if parent_id in visited:
            raise DrawBatchError(f"parent cycle while assigning phase for {element['id']}")
        visited.add(parent_id)
        parent = elements.get(parent_id)
        if parent is None:
            raise DrawBatchError(f"{element['id']} references unknown parent {parent_id}")
        if parent.get("parent_id") is None:
            return parent_id
        current = parent
    return "foundation"


def _shape_name(element: Mapping[str, Any]) -> str:
    value = str(element.get("shape_kind", "")).strip()
    aliases = {
        "roundRect": "rounded_rectangle",
        "rounded_rectangle": "rounded_rectangle",
        "rect": "rectangle",
        "rectangle": "rectangle",
        "ellipse": "ellipse",
        "circle": "ellipse",
        "diamond": "diamond",
        "triangle": "triangle",
        "hexagon": "hexagon",
        "trapezoid": "msoShapeTrapezoid",
        "right_arrow": "right_arrow",
        "left_right_arrow": "left_right_arrow",
        "chevron": "chevron",
    }
    if value in aliases:
        return aliases[value]
    if element.get("type") in {"background", "panel"} and not value:
        return "rectangle"
    raise DrawBatchError(
        f"{element.get('id', '<unknown>')} needs an explicit supported shape_kind; "
        "generic icon substitution is forbidden"
    )


def _shape_operation(
    element: Mapping[str, Any], node: Mapping[str, Any], slide_id: int, scale: float
) -> dict[str, Any]:
    element_id = str(element["id"])
    operation: dict[str, Any] = {
        "type": "add_shape",
        **_base_binding(element_id, slide_id),
        **_geometry(node),
        "shape": _shape_name(element),
    }
    fill = _color(element.get("fill"))
    stroke = _color(element.get("stroke"))
    if fill is None:
        operation.update({"fill_color": "#FFFFFF", "fill_transparency": 100})
    else:
        operation["fill_color"] = fill
        if isinstance(element.get("opacity"), (int, float)):
            operation["fill_transparency"] = max(
                0.0, min(100.0, 100.0 - float(element["opacity"]) * 100.0)
            )
    if stroke is None:
        operation["line_width"] = 0
    else:
        operation["line_color"] = stroke
        operation["line_width"] = round(
            float(element.get("stroke_width_px", 1.0)) * scale, 4
        )
        dash = str(element.get("dash", "solid"))
        if dash not in {"", "none", "solid"}:
            operation["dash_style"] = dash if dash in {
                "square_dot", "round_dot", "dash", "dash_dot", "long_dash", "long_dash_dot", "long_dash_dot_dot"
            } else "dash"
    rotation = element.get("rotation_deg")
    if isinstance(rotation, (int, float)) and float(rotation):
        operation["rotation"] = float(rotation)
    return operation


def _native_line_operation(
    element: Mapping[str, Any], slide_id: int, scale: float
) -> dict[str, Any]:
    geometry = element.get("line_geometry")
    if not isinstance(geometry, Mapping):
        raise DrawBatchError(f"{element['id']} native line lacks line_geometry")
    begin = geometry.get("begin")
    end = geometry.get("end")
    if not isinstance(begin, Mapping) or not isinstance(end, Mapping):
        raise DrawBatchError(f"{element['id']} native line endpoints are invalid")
    style = element.get("line_style") if isinstance(element.get("line_style"), Mapping) else {}
    dash = str(style.get("dash", "solid"))
    return {
        "type": "add_line",
        **_base_binding(str(element["id"]), slide_id),
        "begin_x": round(float(begin["x"]) * scale, 6),
        "begin_y": round(float(begin["y"]) * scale, 6),
        "end_x": round(float(end["x"]) * scale, 6),
        "end_y": round(float(end["y"]) * scale, 6),
        "line_color": _color(style.get("color")) or "#68707A",
        "line_width": round(float(style.get("width_px", 2.0)) * scale, 4),
        "dash_style": dash,
        "start_arrow": _arrow(style.get("start_arrowhead")),
        "end_arrow": _arrow(style.get("end_arrowhead")),
    }


def _text_operation(
    element: Mapping[str, Any], node: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    element_id = str(element["id"])
    text_spec = node.get("textSpec") if isinstance(node.get("textSpec"), Mapping) else {}
    style = element.get("text_style") if isinstance(element.get("text_style"), Mapping) else {}
    is_formula = str(node.get("kind")) == "formula"
    formula_style = (
        element.get("formula_style")
        if isinstance(element.get("formula_style"), Mapping)
        else {}
    )
    font_weight = str(element.get("font_weight", ""))
    operation: dict[str, Any] = {
        "type": "add_textbox",
        **_base_binding(element_id, slide_id),
        **_geometry(node),
        "text": str(node.get("label", "")),
        "font_name": "Cambria Math" if is_formula else str(style.get("font_family", "Arial")),
        "font_size": float(text_spec.get("fontSize", 12)),
        "font_color": (
            _color(formula_style.get("color"))
            if is_formula
            else _color(element.get("color"))
        )
        or "#1F2937",
        "bold": font_weight in {"bold", "semibold"},
        "alignment": str(text_spec.get("horizontalAlign", "left")),
        "vertical_alignment": str(text_spec.get("verticalAlign", "middle")),
        "text_autofit": "none",
        "word_wrap": bool(style.get("wrap", False)),
        "line_width": 0,
    }
    rotation = style.get("rotation_deg")
    if isinstance(rotation, (int, float)) and float(rotation):
        # The managed draw-sequence contract intentionally keeps textbox
        # rotation out of its whitelist.  A transparent native rectangle with
        # text is still fully editable and supports PowerPoint rotation.
        operation["type"] = "add_shape"
        operation["shape"] = "rectangle"
        operation["fill_color"] = "#FFFFFF"
        operation["fill_transparency"] = 100
        operation["rotation"] = float(rotation)
    return operation


def _asset_records(case_root: Path) -> dict[str, Mapping[str, Any]]:
    manifest = _load(case_root / "assets" / "asset_manifest.json", "asset manifest")
    return {
        str(record["assetId"]): record
        for record in manifest.get("assets", [])
        if isinstance(record, Mapping)
    }


def _asset_operations(
    element: Mapping[str, Any],
    node: Mapping[str, Any],
    slide_id: int,
    case_root: Path,
    records: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    element_id = str(element["id"])
    if element.get("type") == "reference_atomic_asset":
        route = "reference_atomic_asset"
    else:
        slot = element.get("slot_contract")
        route = (
            "reference_preview"
            if isinstance(slot, Mapping) and slot.get("mode") == "reference_preview"
            else "manual_asset_slot"
        )
    record = records.get(element_id)
    operations: list[dict[str, Any]] = []
    if record is not None:
        image_path = (case_root / str(record["selectedFile"])).resolve(strict=True)
        operations.append(
            {
                "type": "add_image",
                **_base_binding(element_id, slide_id),
                **_geometry(node),
                "image_path": str(image_path),
                "asset_id": element_id,
                "source_sha256": str(record["sha256"]),
                "raster_reason": (
                    "Source-bound atomic visual detail."
                    if route == "reference_atomic_asset"
                    else "Compound reference preview pending replacement."
                ),
                "source_is_tightly_cropped": True,
                "atomic_raster_unit": route == "reference_atomic_asset",
                "contains_reconstructable_content": False,
                "decomposition_note": "Formal text, formulas, axes, legends, and topology remain separate native objects.",
            }
        )
    else:
        operations.append(
            {
                "type": "add_shape",
                **_base_binding(element_id, slide_id),
                **_geometry(node),
                "shape": "rectangle",
                "fill_color": "#F3F4F6",
                "line_color": "#9CA3AF",
                "line_width": 1.0,
                "dash_style": "dash",
                "text": "ASSET SLOT — REPLACE ME",
                "font_name": "Arial",
                "font_size": 8.0,
                "font_color": "#6B7280",
                "alignment": "center",
                "vertical_alignment": "middle",
            }
        )
    operations.append(
        {
            "type": "add_shape",
            **_base_binding(element_id, slide_id, name=f"{element_id}::anchor"),
            **_geometry(node),
            "shape": "rectangle",
            "fill_color": "#FFFFFF",
            "fill_transparency": 100,
            "line_width": 0,
        }
    )
    if route == "reference_preview":
        label_height = min(14.0, max(8.0, float(node["height"]) * 0.15))
        operations.append(
            {
                "type": "add_textbox",
                **_base_binding(
                    element_id,
                    slide_id,
                    name=f"{element_id}::replace-me-label",
                ),
                "left": float(node["x"]),
                "top": float(node["y"]) + float(node["height"]) - label_height,
                "width": float(node["width"]),
                "height": label_height,
                "text": "REFERENCE PREVIEW — REPLACE ME",
                "font_name": "Arial",
                "font_size": 7.0,
                "font_color": "#B91C1C",
                "bold": True,
                "alignment": "center",
                "vertical_alignment": "middle",
                "fill_color": "#FFFFFF",
                "fill_transparency": 10,
                "line_width": 0,
                "word_wrap": False,
                "text_autofit": "shrink_text",
            }
        )
    return operations


def _anchor_point(
    anchor: Any,
    point: Mapping[str, Any] | None,
    node: Mapping[str, Any],
    scale: float,
) -> tuple[float, float]:
    if str(anchor) == "free":
        if not isinstance(point, Mapping):
            raise DrawBatchError("free connector anchor lacks an explicit point")
        return float(point["x"]) * scale, float(point["y"]) * scale
    left, top = float(node["x"]), float(node["y"])
    width, height = float(node["width"]), float(node["height"])
    return {
        "top": (left + width / 2, top),
        "right": (left + width, top + height / 2),
        "bottom": (left + width / 2, top + height),
        "left": (left, top + height / 2),
        "center": (left + width / 2, top + height / 2),
    }.get(str(anchor), (left + width / 2, top + height / 2))


def _nearest_site(
    anchor: Any,
    point: Mapping[str, Any] | None,
    node: Mapping[str, Any],
    other_node: Mapping[str, Any],
    scale: float,
) -> int:
    explicit = {"top": 1, "left": 2, "bottom": 3, "right": 4}
    if anchor in explicit:
        return explicit[str(anchor)]
    point_x, point_y = _anchor_point(anchor, point, node, scale)
    center_x = float(node["x"]) + float(node["width"]) / 2
    center_y = float(node["y"]) + float(node["height"]) / 2
    delta_x, delta_y = point_x - center_x, point_y - center_y
    if not delta_x and not delta_y:
        other_x = float(other_node["x"]) + float(other_node["width"]) / 2
        other_y = float(other_node["y"]) + float(other_node["height"]) / 2
        delta_x, delta_y = other_x - center_x, other_y - center_y
    if abs(delta_x) >= abs(delta_y):
        return 4 if delta_x >= 0 else 2
    return 3 if delta_y >= 0 else 1


def _arrow(value: Any, default: str = "none") -> str:
    normalized = str(value or default)
    aliases = {"arrow": "open", "closed": "triangle", "both": "triangle"}
    result = aliases.get(normalized, normalized)
    return result if result in {"none", "open", "triangle", "stealth", "diamond", "oval"} else default


def _edge_semantic_id(scene_edge: Mapping[str, Any], edge_id: str) -> str:
    legacy = scene_edge.get("semanticRelationId")
    if isinstance(legacy, str) and legacy:
        return legacy
    values = scene_edge.get("semanticRelationIds")
    if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str):
        return values[0]
    raise DrawBatchError(f"{edge_id} must bind exactly one scientific relation")


def _line_style(edge: Mapping[str, Any], scale: float) -> dict[str, Any]:
    style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
    dash = str(style.get("dash", "solid"))
    return {
        "line_color": _color(style.get("stroke_color", style.get("color"))) or "#68707A",
        "line_width": round(float(style.get("stroke_width_px", style.get("width_px", 2.0))) * scale, 4),
        "dash_style": dash if dash in {
            "solid", "square_dot", "round_dot", "dash", "dash_dot", "long_dash", "long_dash_dot", "long_dash_dot_dot"
        } else ("dash" if dash not in {"", "none"} else "solid"),
        "start_arrow": _arrow(style.get("start_arrowhead")),
        "end_arrow": _arrow(style.get("end_arrowhead"), "triangle"),
    }


def _topology_connector_operation(
    edge: Mapping[str, Any],
    scene_edge: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    slide_id: int,
    scale: float,
) -> dict[str, Any]:
    edge_id = str(edge["id"])
    source_node = nodes[str(edge["from"])]
    target_node = nodes[str(edge["to"])]
    source_name = (
        f"{edge['from']}::anchor" if source_node.get("kind") == "asset" else str(edge["from"])
    )
    target_name = (
        f"{edge['to']}::anchor" if target_node.get("kind") == "asset" else str(edge["to"])
    )
    return {
        "type": "add_connector",
        "slide_id": slide_id,
        "element_id": edge_id,
        "semantic_id": _edge_semantic_id(scene_edge, edge_id),
        "name": f"{edge_id}::topology",
        "source_name": source_name,
        "target_name": target_name,
        "source_site": _nearest_site(
            edge.get("source_anchor"), edge.get("source_point"), source_node, target_node, scale
        ),
        "target_site": _nearest_site(
            edge.get("target_anchor"), edge.get("target_point"), target_node, source_node, scale
        ),
        "connector_type": "elbow" if edge.get("via") else "straight",
        "line_color": "#FFFFFF",
        "line_width": 0,
        "start_arrow": "none",
        "end_arrow": "none",
    }


def _connector_batch(
    spec: Mapping[str, Any],
    scene: Mapping[str, Any],
    slide_id: int,
    scale: float,
) -> dict[str, Any]:
    nodes = {str(node["id"]): node for node in scene["nodes"]}
    scene_edges = {str(edge["id"]): edge for edge in scene["edges"]}
    element_by_id = {str(element["id"]): element for element in spec["elements"]}
    operations: list[dict[str, Any]] = []
    element_ids: list[str] = []
    for edge in spec.get("edges", []):
        edge_id = str(edge["id"])
        source_id, target_id = str(edge["from"]), str(edge["to"])
        if source_id not in nodes or target_id not in nodes or edge_id not in scene_edges:
            raise DrawBatchError(f"{edge_id} lacks a scene endpoint or relation binding")
        representation = str(edge.get("representation", "native_connector"))
        arrow_class = str(edge.get("arrow_class", "thin_connector"))
        scene_style = (
            scene_edges[edge_id].get("styleTokens")
            if isinstance(scene_edges[edge_id].get("styleTokens"), Mapping)
            else {}
        )
        carrier_token = str(scene_style.get("visualCarrierIds", ""))
        visual_carrier_ids = [item for item in carrier_token.split("|") if item]
        if visual_carrier_ids:
            invalid = [
                carrier_id
                for carrier_id in visual_carrier_ids
                if carrier_id not in nodes
                or not isinstance(nodes[carrier_id].get("styleTokens"), Mapping)
                or str(nodes[carrier_id]["styleTokens"].get("visualCarrierFor"))
                != edge_id
            ]
            if invalid:
                raise DrawBatchError(f"{edge_id} has invalid visual carriers: {invalid}")
            edge_style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
            for index, carrier_id in enumerate(visual_carrier_ids):
                carrier = nodes[carrier_id]
                carrier_semantic_id = str(carrier["semanticId"])
                if carrier.get("kind") == "shape":
                    carrier_style = (
                        carrier.get("styleTokens")
                        if isinstance(carrier.get("styleTokens"), Mapping)
                        else {}
                    )
                    operations.append(
                        {
                            "type": "add_shape",
                            "slide_id": slide_id,
                            "element_id": carrier_id,
                            "semantic_id": carrier_semantic_id,
                            "name": carrier_id,
                            **_geometry(carrier),
                            "shape": str((carrier.get("shapeSpec") or {}).get("shapeType", "right_arrow")),
                            "fill_color": _color(carrier_style.get("fillColor")) or "#68707A",
                            "line_width": 0,
                            "rotation": float(carrier.get("rotation", 0)),
                        }
                    )
                elif carrier.get("kind") == "line":
                    primitive = ((carrier.get("nativePrimitiveSpec") or {}).get("primitive") or {})
                    points = primitive.get("points") if isinstance(primitive, Mapping) else None
                    if not isinstance(points, list) or len(points) != 2:
                        raise DrawBatchError(f"{carrier_id} line carrier has invalid native points")
                    dash = str(edge_style.get("dash", "solid"))
                    operations.append(
                        {
                            "type": "add_line",
                            "slide_id": slide_id,
                            "element_id": carrier_id,
                            "semantic_id": carrier_semantic_id,
                            "name": carrier_id,
                            "begin_x": float(points[0]["x"]),
                            "begin_y": float(points[0]["y"]),
                            "end_x": float(points[1]["x"]),
                            "end_y": float(points[1]["y"]),
                            "line_color": _color(edge_style.get("stroke_color", edge_style.get("color"))) or "#68707A",
                            "line_width": round(float(edge_style.get("stroke_width_px", edge_style.get("width_px", 2.0))) * scale, 4),
                            "dash_style": dash,
                            "start_arrow": _arrow(edge_style.get("start_arrowhead")) if index == 0 else "none",
                            "end_arrow": _arrow(edge_style.get("end_arrowhead"), "triangle") if index == len(visual_carrier_ids) - 1 else "none",
                        }
                    )
                else:
                    raise DrawBatchError(f"{carrier_id} has unsupported carrier kind {carrier.get('kind')}")
            operations.append(
                _topology_connector_operation(
                    edge, scene_edges[edge_id], nodes, slide_id, scale
                )
            )
            element_ids.extend([*visual_carrier_ids, edge_id])
            continue
        if representation == "reference_atomic_asset":
            asset_id = str(edge.get("visual_asset_id", ""))
            if element_by_id.get(asset_id, {}).get("type") != "reference_atomic_asset":
                raise DrawBatchError(f"{edge_id} style asset binding is invalid")
            operations.append(
                _topology_connector_operation(
                    edge, scene_edges[edge_id], nodes, slide_id, scale
                )
            )
            element_ids.append(edge_id)
            continue
        if arrow_class == "filled_native" or representation == "native_line_chain" or edge.get("via"):
            raise DrawBatchError(
                f"{edge_id} requires explicit scene visual carriers; backend guessing is forbidden"
            )
        else:
            source_node, target_node = nodes[source_id], nodes[target_id]
            source_name = f"{source_id}::anchor" if source_node.get("kind") == "asset" else source_id
            target_name = f"{target_id}::anchor" if target_node.get("kind") == "asset" else target_id
            operation = {
                "type": "add_connector",
                "slide_id": slide_id,
                "element_id": edge_id,
                "semantic_id": _edge_semantic_id(scene_edges[edge_id], edge_id),
                "name": edge_id,
                "source_name": source_name,
                "target_name": target_name,
                "source_site": _nearest_site(
                    edge.get("source_anchor"), edge.get("source_point"), source_node, target_node, scale
                ),
                "target_site": _nearest_site(
                    edge.get("target_anchor"), edge.get("target_point"), target_node, source_node, scale
                ),
                "connector_type": {"straight": "straight", "curve": "curve"}.get(
                    str(edge.get("route")), "elbow"
                ),
                **_line_style(edge, scale),
            }
            operations.append(operation)
        element_ids.append(edge_id)
    return {
        "schema_version": "2.0.0",
        "phase": "connectors",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": element_ids,
        "operations": operations,
    }


def _text_frame_hygiene_batch(
    spec: Mapping[str, Any], scene: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    elements = {str(element["id"]): element for element in spec["elements"]}
    operations: list[dict[str, Any]] = []
    formula_names: list[str] = []
    for node in scene["nodes"]:
        element_id = str(node["id"])
        kind = str(node.get("kind"))
        if kind not in {"text", "formula"}:
            continue
        if element_id not in elements:
            raise DrawBatchError(f"scene text node has no Figure Spec element: {element_id}")
        operations.append(
            {
                "type": "update_shape",
                "slide_id": slide_id,
                "shape_name": element_id,
                **_geometry(node),
            }
        )
        if kind == "formula":
            formula_names.append(element_id)
    # Formula placeholders are created before connectors so their semantic
    # groups remain region-local.  Restore their exact geometry after Office
    # has seen the placeholder text, then put them above late-drawn visual
    # carriers before closed-package OMML injection.
    operations.extend(
        {
            "type": "set_z_order",
            "slide_id": slide_id,
            "shape_name": name,
            "command": "bring_to_front",
        }
        for name in formula_names
    )
    return {
        "schema_version": "2.0.0",
        "phase": "text_frame_hygiene",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": [str(node["id"]) for node in scene["nodes"] if str(node.get("kind")) in {"text", "formula"}],
        "operations": operations,
    }


def _native_groups_batch(scene: Mapping[str, Any], slide_id: int) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    element_ids: list[str] = []
    for node in scene.get("nodes", []):
        if not isinstance(node, Mapping):
            continue
        tokens = node.get("styleTokens")
        if not isinstance(tokens, Mapping) or not tokens.get("nativeGroupMemberIds"):
            continue
        node_id = str(node["id"])
        members = [
            value
            for value in str(tokens["nativeGroupMemberIds"]).split("|")
            if value
        ]
        if len(members) < 2 or len(set(members)) != len(members):
            raise DrawBatchError(f"{node_id} native group members are invalid")
        element_ids.append(node_id)
        operations.append(
            {
                "type": "group_shapes",
                **_base_binding(node_id, slide_id),
                "shape_names": members,
            }
        )
    return {
        "schema_version": "2.0.0",
        "phase": "native_groups",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": element_ids,
        "operations": operations,
    }


def _z_order_hygiene_batch(
    spec: Mapping[str, Any], scene: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    """Place visual routes above containers and below editable leaf content."""

    operations: list[dict[str, Any]] = []
    visual_route_names: list[str] = []
    for edge in scene.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        edge_id = str(edge["id"])
        tokens = edge.get("styleTokens")
        carrier_token = (
            str(tokens.get("visualCarrierIds", ""))
            if isinstance(tokens, Mapping)
            else ""
        )
        carrier_ids = [value for value in carrier_token.split("|") if value]
        visual_route_names.extend(carrier_ids or [edge_id])

    native_group_members: set[str] = set()
    native_group_names: list[str] = []
    for node in scene.get("nodes", []):
        if not isinstance(node, Mapping):
            continue
        tokens = node.get("styleTokens")
        if not isinstance(tokens, Mapping) or not tokens.get("nativeGroupMemberIds"):
            continue
        native_group_names.append(str(node["id"]))
        native_group_members.update(
            value
            for value in str(tokens["nativeGroupMemberIds"]).split("|")
            if value
        )

    # PowerPoint may reopen connectors behind their containing panels. Route
    # carriers are raised first; leaf content (or its native group wrapper) is
    # raised second so endpoints and labels remain readable.
    for shape_name in visual_route_names:
        operations.append(
            {
                "type": "set_z_order",
                "slide_id": slide_id,
                "shape_name": shape_name,
                "command": "bring_to_front",
            }
        )

    leaf_types = {
        "text",
        "formula",
        "native_shape",
        "shape",
        "icon",
        "legend",
        "reference_atomic_asset",
        "manual_asset_slot",
    }
    leaf_ids = [
        str(element["id"])
        for element in sorted(
            spec.get("elements", []),
            key=lambda value: (int(value.get("z_index", 0)), str(value.get("id", ""))),
        )
        if isinstance(element, Mapping)
        and element.get("type") in leaf_types
        and str(element.get("id", "")) not in native_group_members
    ]
    for shape_name in [*native_group_names, *leaf_ids]:
        operations.append(
            {
                "type": "set_z_order",
                "slide_id": slide_id,
                "shape_name": shape_name,
                "command": "bring_to_front",
            }
        )
    return {
        "schema_version": "2.0.0",
        "phase": "z_order_hygiene",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": [*visual_route_names, *native_group_names, *leaf_ids],
        "operations": operations,
    }


def _finalize_batch_bindings(
    batch: dict[str, Any], scene: Mapping[str, Any]
) -> dict[str, Any]:
    if scene.get("schemaVersion") != "2.1.0":
        return batch
    for operation in batch["operations"]:
        semantic_id = operation.pop("semantic_id", None)
        if semantic_id is not None:
            operation["semantic_ids"] = [str(semantic_id)]
    return batch


def build_batch(case_root: Path, phase: str, slide_id: int) -> dict[str, Any]:
    resolved_case = case_root.resolve(strict=True)
    receipt = _load(resolved_case / "case-receipt.json", "case receipt")
    if receipt.get("status") not in {
        "POWERPOINT_CASE_READY",
        "POWERPOINT_CASE_PREPARED_REQUIRES_REVIEW",
    }:
        raise DrawBatchError("case receipt is not a supported prepared PowerPoint case")
    scene = _load(resolved_case / "design" / "scene_graph.json", "scene graph")
    spec_path = Path(str(receipt["figure_spec"]["path"])).resolve(strict=True)
    spec = _load(spec_path, "Figure Spec")
    if spec.get("schema_version") != "4.0":
        raise DrawBatchError("generic renderer requires Figure Spec 4.0; migrate legacy runs first")
    scale = float(receipt["coordinate_mapping"]["scale"])
    if phase == "connectors":
        return _finalize_batch_bindings(
            _connector_batch(spec, scene, slide_id, scale), scene
        )
    if phase == "text_frame_hygiene":
        return _finalize_batch_bindings(
            _text_frame_hygiene_batch(spec, scene, slide_id), scene
        )
    if phase == "native_groups":
        return _finalize_batch_bindings(_native_groups_batch(scene, slide_id), scene)
    if phase == "z_order_hygiene":
        return _finalize_batch_bindings(
            _z_order_hygiene_batch(spec, scene, slide_id), scene
        )
    elements = {str(element["id"]): element for element in spec["elements"]}
    nodes = {str(node["id"]): node for node in scene["nodes"]}
    connector_endpoint_ids = {
        str(edge[endpoint])
        for edge in spec.get("edges", [])
        for endpoint in ("from", "to")
    }
    selected = [
        element for element in spec["elements"] if _phase_for(element, elements) == phase
    ]
    selected.sort(key=lambda element: (int(element["z_index"]), str(element["id"])))
    records = _asset_records(resolved_case)
    operations: list[dict[str, Any]] = []
    rendered_ids: list[str] = []
    for element in selected:
        element_id = str(element["id"])
        node = nodes.get(element_id)
        if node is None:
            raise DrawBatchError(f"scene node missing for {element_id}")
        kind = str(node["kind"])
        if kind == "container" and not any(
            key in element for key in ("shape_kind", "fill", "stroke")
        ):
            if element_id not in connector_endpoint_ids:
                continue
            operations.append(
                {
                    "type": "add_shape",
                    **_base_binding(element_id, slide_id),
                    **_geometry(node),
                    "shape": "rectangle",
                    "fill_color": "#FFFFFF",
                    "fill_transparency": 100,
                    "line_width": 0,
                }
            )
            rendered_ids.append(element_id)
            continue
        if kind == "line":
            operations.append(_native_line_operation(element, slide_id, scale))
        elif kind == "shape" and element.get("type") == "text":
            operations.append(_text_operation(element, node, slide_id))
        elif kind in {"panel", "container", "shape", "legend"}:
            operations.append(_shape_operation(element, node, slide_id, scale))
        elif kind in {"text", "formula"}:
            operations.append(_text_operation(element, node, slide_id))
        elif kind == "asset":
            operations.extend(_asset_operations(element, node, slide_id, resolved_case, records))
        else:
            raise DrawBatchError(f"unsupported node kind for {element_id}: {kind}")
        rendered_ids.append(element_id)
    return _finalize_batch_bindings({
        "schema_version": "2.0.0",
        "phase": phase,
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": rendered_ids,
        "operations": operations,
    }, scene)


def list_elements(case_root: Path) -> list[dict[str, Any]]:
    receipt = _load(case_root.resolve(strict=True) / "case-receipt.json", "case receipt")
    spec = _load(Path(str(receipt["figure_spec"]["path"])), "Figure Spec")
    elements = {str(element["id"]): element for element in spec["elements"]}
    return [
        {
            "id": str(element["id"]),
            "type": str(element["type"]),
            "parent_id": element.get("parent_id"),
            "phase": _phase_for(element, elements),
            "z_index": int(element["z_index"]),
            "render_strategy": element.get("render_strategy"),
            "semantic_role": element.get("semantic_role"),
        }
        for element in spec["elements"]
    ]


def list_phases(case_root: Path) -> list[str]:
    phases = {str(record["phase"]) for record in list_elements(case_root)}
    return [
        "foundation",
        *sorted(phase for phase in phases if phase != "foundation"),
        "connectors",
        "native_groups",
        "z_order_hygiene",
        "text_frame_hygiene",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", required=True, type=Path)
    parser.add_argument("--phase")
    parser.add_argument("--slide-id", type=int, default=256)
    parser.add_argument("--list-elements", action="store_true")
    parser.add_argument("--list-phases", action="store_true")
    parser.add_argument("--operation-offset", type=int, default=0)
    parser.add_argument("--operation-limit", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.list_elements:
            result: Any = list_elements(args.case_root)
        elif args.list_phases:
            result = list_phases(args.case_root)
        elif args.phase:
            result = build_batch(args.case_root, args.phase, args.slide_id)
            if args.operation_offset < 0 or (
                args.operation_limit is not None and args.operation_limit < 1
            ):
                raise DrawBatchError("operation slice requires offset >= 0 and limit >= 1")
            total = int(result["operation_count"])
            end = (
                None
                if args.operation_limit is None
                else args.operation_offset + args.operation_limit
            )
            result["operations"] = result["operations"][args.operation_offset:end]
            result["operation_count"] = len(result["operations"])
            result["total_operation_count"] = total
            result["operation_offset"] = args.operation_offset
        else:
            raise DrawBatchError("provide --phase, --list-elements, or --list-phases")
    except (DrawBatchError, OSError) as exc:
        print(f"POWERPOINT_DRAW_BATCH_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
