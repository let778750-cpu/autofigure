"""Project a PASS AutoFigure spec into one PowerPoint contract-v2 case.

This adapter does not reinterpret pixels or change scientific content.  It
creates the case-control documents required by the managed PowerPoint MCP from
one hash-bound Figure Spec and its authorized preflight receipt.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from finalize_perception_review import atomic_write_json, sha256_file  # noqa: E402
from powerpoint_path_geometry import anchor_point  # noqa: E402
from run_state import release_ceiling_for_elements  # noqa: E402


class PowerPointCaseError(RuntimeError):
    """Raised when a frozen AutoFigure spec cannot become one MCP case."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PowerPointCaseError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PowerPointCaseError(f"{label} must be a JSON object")
    return value


def _require_hash(path: Path, expected: str, label: str) -> str:
    actual = sha256_file(path)
    if actual.casefold() != expected.casefold():
        raise PowerPointCaseError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def _resolve(value: str, base: Path) -> Path:
    path = Path(value)
    return path.resolve(strict=True) if path.is_absolute() else (base / path).resolve(strict=True)


def _semantic_id(element_id: str) -> str:
    return f"semantic::{element_id}"


def _relation_id(edge_id: str) -> str:
    return f"relation::{edge_id}"


def _node_kind(element: Mapping[str, Any]) -> str:
    kind = str(element.get("type", ""))
    if kind in {"background", "panel"}:
        return "panel"
    if kind in {"group", "micro_asset"}:
        return "container"
    if kind in {"shape", "native_shape", "icon"}:
        if str(element.get("shape_kind", "")) == "line":
            return "line"
        return "shape"
    if kind == "legend":
        return "legend"
    if kind in {"manual_asset_slot", "reference_atomic_asset"}:
        return "asset"
    if kind == "formula":
        return "formula"
    if kind == "text":
        text_style = element.get("text_style")
        if isinstance(text_style, Mapping) and float(text_style.get("rotation_deg", 0) or 0):
            return "shape"
        runs = element.get("content_runs", [])
        if any(isinstance(run, Mapping) and run.get("kind") == "math" for run in runs):
            return "formula"
        return "text"
    return "shape"


def _plain_or_formula_text(
    element: Mapping[str, Any], formulas_by_element: Mapping[str, Mapping[str, Any]]
) -> str:
    if isinstance(element.get("text"), str):
        return str(element["text"])
    formula = formulas_by_element.get(str(element["id"]))
    return str(formula.get("canonical_latex", "")) if formula else ""


def _scale(value: int | float, factor: float) -> float:
    return round(float(value) * factor, 6)


def _margins(style: Mapping[str, Any], scale: float) -> dict[str, float]:
    value = style.get("margin_px", 0)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = _scale(value, scale)
        return {key: amount for key in ("left", "top", "right", "bottom")}
    if isinstance(value, Mapping):
        return {
            key: _scale(value.get(key, 0), scale)
            for key in ("left", "top", "right", "bottom")
        }
    return {key: 0.0 for key in ("left", "top", "right", "bottom")}


def _text_spec(
    element: Mapping[str, Any],
    formulas_by_element: Mapping[str, Mapping[str, Any]],
    scale: float,
) -> dict[str, Any]:
    raw_style = (
        element.get("formula_style")
        if _node_kind(element) == "formula"
        else element.get("text_style")
    )
    style = raw_style if isinstance(raw_style, Mapping) else {}
    # Figure Spec point sizes are already in PowerPoint's native unit.  Only
    # pixel-valued sizes participate in the source-pixel -> point mapping.
    # Scaling font_size_pt here made every rendered label about one third too
    # small while leaving its measured bbox unchanged.
    if isinstance(style.get("font_size_pt"), (int, float)) and not isinstance(
        style.get("font_size_pt"), bool
    ):
        font_size = round(float(style["font_size_pt"]), 6)
    elif isinstance(style.get("font_size_px"), (int, float)) and not isinstance(
        style.get("font_size_px"), bool
    ):
        font_size = _scale(style["font_size_px"], scale)
    else:
        font_size = 12.0
    align = str(style.get("horizontal_align", element.get("align", "left")))
    font_weight = str(element.get("font_weight", "regular"))
    return {
        "text": _plain_or_formula_text(element, formulas_by_element),
        "fontToken": "font.math" if _node_kind(element) == "formula" else "font.body",
        "fontSize": font_size,
        "fontWeight": "semibold" if font_weight in {"bold", "semibold"} else "normal",
        "horizontalAlign": align if align in {"left", "center", "right"} else "left",
        "verticalAlign": "middle",
        "margins": _margins(style, scale),
        "autofit": "none",
    }


def _style_tokens(element: Mapping[str, Any]) -> dict[str, str | float | bool]:
    keys = (
        "fill",
        "stroke",
        "stroke_width_px",
        "dash",
        "corner_radius_px",
        "color",
        "shape_kind",
        "asset_kind",
        "icon_kind",
        "layer_count",
        "count",
        "state_fill",
        "state_stroke",
        "reward_fill",
        "reward_stroke",
        "policy_fill",
        "action_fill",
        "rotation_deg",
        "opacity",
    )
    return {
        key: value
        for key in keys
        if isinstance((value := element.get(key)), (str, int, float, bool))
    }


def _edge_direction(edge: Mapping[str, Any]) -> str:
    style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
    if (
        style.get("arrowhead") == "both"
        or (
            str(style.get("start_arrowhead", "none")) != "none"
            and str(style.get("end_arrowhead", "none")) != "none"
        )
        or edge.get("meaning") == "interaction"
    ):
        return "bidirectional"
    return "forward"


def _visual_carrier_kind(edge: Mapping[str, Any]) -> str | None:
    """Select one native visual carrier without discarding explicit path geometry."""

    if edge.get("via"):
        return "line_chain"
    if str(edge.get("arrow_class", "thin_connector")) == "filled_native":
        return "filled_shape"
    return None


def _scientific_relation_kind(meaning: str) -> str:
    return {
        "data_flow": "data",
        "control_flow": "control",
        "feedback": "feedback",
        "interaction": "association",
        "association": "association",
    }.get(meaning, "flow")


def _validate_document(document: Mapping[str, Any], schema_path: Path, label: str) -> None:
    schema = _load(schema_path, f"{label} schema")
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in first.absolute_path
        )
        raise PowerPointCaseError(f"{label} rejected at {location}: {first.message}")


def _asset_projection(
    element: Mapping[str, Any],
    *,
    resolved_spec: Path,
    assets_dir: Path,
    bbox: Mapping[str, Any],
    coordinate_scale: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, str]:
    """Project one explicit v4 raster route without inferring scientific content."""
    element_id = str(element["id"])
    element_type = str(element.get("type", ""))
    if element_type == "reference_atomic_asset":
        binding = element.get("asset_binding")
        if not isinstance(binding, Mapping):
            raise PowerPointCaseError(f"{element_id} lacks asset_binding")
        receipt_path = _resolve(str(binding["receipt_path"]), resolved_spec.parent)
        _require_hash(
            receipt_path, str(binding["receipt_sha256"]), f"{element_id} atomic receipt"
        )
        receipt = _load(receipt_path, f"{element_id} atomic receipt")
        if receipt.get("document_type") != "REFERENCE_ATOMIC_ASSET_RECEIPT" or receipt.get("status") != "MECHANICAL_PASS_REQUIRES_INDEPENDENT_REVIEW":
            raise PowerPointCaseError(f"{element_id} atomic receipt has an invalid status")
        source_asset = _resolve(str(binding["asset_path"]), resolved_spec.parent)
        asset_sha = _require_hash(
            source_asset, str(binding["asset_sha256"]), f"{element_id} atomic asset"
        )
        if str(receipt.get("asset", {}).get("sha256", "")).casefold() != asset_sha.casefold():
            raise PowerPointCaseError(f"{element_id} atomic asset differs from its receipt")
        copied_name = f"{element_id}.reference-atomic.png"
        shutil.copyfile(source_asset, assets_dir / copied_name)
        asset_record = {
            "assetId": element_id,
            "responsibilityClass": "verified_source",
            "selectedFile": f"assets/{copied_name}",
            "processingSteps": [
                "Deterministic source-bound crop from the frozen designated reference.",
                "No generative processing or resampling; optional deterministic alpha mask is receipt-bound.",
            ],
            "sha256": asset_sha.lower(),
            "mime": "image/png",
            "width": float(receipt["asset"]["width_px"]),
            "height": float(receipt["asset"]["height_px"]),
            "targetSlot": element_id,
            "embeddedObjectId": None,
            "provenance": {
                "route": "verified_import",
                "status": "verified_source",
                "license": None,
                "rightsBasis": str(receipt["rights_basis"]),
            },
            "reviewStatus": "PENDING",
        }
        node_asset = {
            "assetId": element_id,
            "fitMode": "contain",
            "crop": {"left": 0, "top": 0, "right": 0, "bottom": 0},
            "atomicRasterUnit": True,
            "containsReconstructableContent": False,
        }
        aspect_ratio = float(receipt["asset"]["width_px"]) / float(
            receipt["asset"]["height_px"]
        )
        route = "reference_atomic_asset"
    elif element_type == "manual_asset_slot":
        slot = element.get("slot_contract")
        if not isinstance(slot, Mapping):
            raise PowerPointCaseError(f"{element_id} lacks slot_contract")
        mode = str(slot.get("mode", ""))
        node_asset = {
            "assetId": element_id,
            "fitMode": str(slot.get("fit_mode", "contain")),
            "crop": {"left": 0, "top": 0, "right": 0, "bottom": 0},
            "atomicRasterUnit": True,
            "containsReconstructableContent": False,
        }
        aspect_ratio = float(slot["aspect_ratio"])
        asset_record = None
        route = mode
        if mode == "reference_preview":
            preview = slot.get("preview")
            if not isinstance(preview, Mapping):
                raise PowerPointCaseError(f"{element_id} reference_preview lacks binding")
            manifest_path = _resolve(str(preview["manifest_path"]), resolved_spec.parent)
            _require_hash(
                manifest_path,
                str(preview["manifest_sha256"]),
                f"{element_id} preview manifest",
            )
            preview_manifest = _load(manifest_path, f"{element_id} preview manifest")
            preview_path = _resolve(
                str(preview_manifest["asset"]["path"]), manifest_path.parent
            )
            preview_sha = _require_hash(
                preview_path,
                str(preview_manifest["asset"]["sha256"]),
                f"{element_id} preview image",
            )
            copied_name = f"{element_id}.reference-preview.png"
            shutil.copyfile(preview_path, assets_dir / copied_name)
            asset_record = {
                "assetId": element_id,
                "responsibilityClass": "verified_source",
                "selectedFile": f"assets/{copied_name}",
                "processingSteps": [
                    "Exact-pixel crop from the frozen designated reference.",
                    "Candidate-only preview; visible disclosure and replacement are required.",
                ],
                "sha256": preview_sha.lower(),
                "mime": "image/png",
                "width": float(preview_manifest["asset"]["width_px"]),
                "height": float(preview_manifest["asset"]["height_px"]),
                "targetSlot": element_id,
                "embeddedObjectId": None,
                "provenance": {
                    "route": "verified_import",
                    "status": "verified_source",
                    "license": None,
                    "rightsBasis": "User-supplied designated reference; candidate preview only.",
                },
                "reviewStatus": "PENDING",
            }
        elif mode in {"user_filled", "backfilled_verified"}:
            filled = slot.get("filled_asset")
            if not isinstance(filled, Mapping):
                raise PowerPointCaseError(f"{element_id} {mode} slot lacks filled_asset binding")
            filled_path = _resolve(str(filled["path"]), resolved_spec.parent)
            filled_sha = _require_hash(filled_path, str(filled["sha256"]), f"{element_id} filled asset")
            copied_name = f"{element_id}.{mode}.png"
            shutil.copyfile(filled_path, assets_dir / copied_name)
            asset_record = {
                "assetId": element_id,
                "responsibilityClass": "verified_source" if mode == "backfilled_verified" else "creative_raster",
                "selectedFile": f"assets/{copied_name}",
                "processingSteps": ["Bound slot asset copied without content modification."],
                "sha256": filled_sha.lower(),
                "mime": "image/png",
                "width": float(filled["width_px"]),
                "height": float(filled["height_px"]),
                "targetSlot": element_id,
                "embeddedObjectId": None,
                "provenance": {
                    "route": "verified_import",
                    "status": "verified_source" if mode == "backfilled_verified" else "user_supplied",
                    "license": None,
                    "rightsBasis": str(filled["rights_basis"]),
                },
                "reviewStatus": "PENDING",
            }
        elif mode != "empty":
            raise PowerPointCaseError(f"{element_id} has unsupported slot mode {mode!r}")
    else:
        raise PowerPointCaseError(f"{element_id} is not one supported raster route")
    slot_record = {
        "slotId": element_id,
        "semanticAnchor": _semantic_id(element_id),
        "x": _scale(bbox["x"], coordinate_scale),
        "y": _scale(bbox["y"], coordinate_scale),
        "width": _scale(bbox["w"], coordinate_scale),
        "height": _scale(bbox["h"], coordinate_scale),
        "overlaySafeArea": "Formal text, formulas, axes, legends, and topology remain separate native objects.",
        "expectedAspectRatio": aspect_ratio,
    }
    return node_asset, slot_record, asset_record, route


def prepare_powerpoint_case(
    spec_path: Path,
    preflight_path: Path,
    output_case: Path,
    *,
    project_id: str,
    target_id: str,
    profile_id: str = "journal-double-column",
    schema_root: Path | None = None,
) -> dict[str, Any]:
    if output_case.exists():
        raise PowerPointCaseError(f"output case already exists: {output_case}")
    resolved_spec = spec_path.resolve(strict=True)
    resolved_preflight = preflight_path.resolve(strict=True)
    spec = _load(resolved_spec, "Figure Spec")
    preflight = _load(resolved_preflight, "preflight receipt")
    if preflight.get("status") != "PASS" or preflight.get("receipt", {}).get(
        "authorized_for_drawer"
    ) is not True:
        raise PowerPointCaseError("preflight must be PASS and authorized_for_drawer=true")
    spec_sha = _require_hash(
        resolved_spec,
        str(preflight.get("receipt", {}).get("spec_sha256", "")),
        "Figure Spec",
    )
    if spec.get("mode") != "reconstruct_1to1":
        raise PowerPointCaseError("PowerPoint case adapter currently requires reconstruct_1to1")

    source_path = _resolve(str(spec["source"]["path"]), resolved_spec.parent)
    source_sha = _require_hash(source_path, str(spec["source"]["sha256"]), "reference")
    canvas_path = _resolve(str(spec["canvas"]["pptx_path"]), resolved_spec.parent)
    canvas_sha = _require_hash(canvas_path, str(spec["canvas"]["pptx_sha256"]), "canvas")
    authority_path = _resolve(str(spec["authority"]["path"]), resolved_spec.parent)
    authority_sha = _require_hash(
        authority_path, str(spec["authority"]["sha256"]), "source authority"
    )

    source_width = float(spec["canvas"]["width_px"])
    source_height = float(spec["canvas"]["height_px"])
    slide_width = float(spec["canvas"]["slide_width_emu"]) / 12700.0
    slide_height = float(spec["canvas"]["slide_height_emu"]) / 12700.0
    coordinate_scale = slide_width / source_width
    scaled_source_height = source_height * coordinate_scale
    if abs(scaled_source_height - slide_height) > 0.02:
        raise PowerPointCaseError(
            "canvas requires non-uniform scaling: "
            f"source={source_width}x{source_height}px, "
            f"slide={slide_width}x{slide_height}pt"
        )

    output_case.mkdir(parents=True)
    input_dir = output_case / "input"
    design_dir = output_case / "design"
    assets_dir = output_case / "assets"
    for directory in (input_dir, design_dir, assets_dir):
        directory.mkdir()
    shutil.copyfile(canvas_path, input_dir / "canvas.pptx")

    formulas_by_element = {
        str(formula["element_id"]): formula for formula in spec.get("formulas", [])
    }
    nodes: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    asset_slots: list[dict[str, Any]] = []
    asset_records: list[dict[str, Any]] = []
    preview_asset_ids: list[str] = []
    atomic_asset_ids: list[str] = []
    for element in spec["elements"]:
        element_id = str(element["id"])
        kind = _node_kind(element)
        bbox = element["bbox"]
        node: dict[str, Any] = {
            "id": element_id,
            "semanticId": _semantic_id(element_id),
            "parentId": element.get("parent_id"),
            "kind": kind,
            "x": _scale(bbox["x"], coordinate_scale),
            "y": _scale(bbox["y"], coordinate_scale),
            "width": _scale(bbox["w"], coordinate_scale),
            "height": _scale(bbox["h"], coordinate_scale),
            "editable": True,
            "zIndex": int(element["z_index"]),
        }
        tokens = _style_tokens(element)
        if tokens:
            node["styleTokens"] = tokens
        if kind in {"text", "formula"} or element.get("type") == "text":
            node["label"] = _plain_or_formula_text(element, formulas_by_element)
            node["textSpec"] = _text_spec(
                element, formulas_by_element, coordinate_scale
            )
        if kind in {"panel", "container", "shape", "legend"}:
            node["shapeSpec"] = {
                "shapeType": str(
                    element.get("shape_kind", element.get("asset_kind", "rectangle"))
                ),
                "cornerRadius": _scale(
                    element.get("corner_radius_px", 0), coordinate_scale
                ),
            }
        if kind == "asset":
            node_asset, slot_record, asset_record, asset_route = _asset_projection(
                element,
                resolved_spec=resolved_spec,
                assets_dir=assets_dir,
                bbox=bbox,
                coordinate_scale=coordinate_scale,
            )
            node["assetSpec"] = node_asset
            asset_slots.append(slot_record)
            if asset_record is not None:
                asset_records.append(asset_record)
            if asset_route == "reference_preview":
                preview_asset_ids.append(element_id)
            elif asset_route == "reference_atomic_asset":
                atomic_asset_ids.append(element_id)
        nodes.append(node)
        entities.append(
            {
                "entityId": _semantic_id(element_id),
                "label": str(element.get("text", element.get("semantic_role", element_id))),
                "kind": (
                    "equation"
                    if kind == "formula"
                    else "panel"
                    if kind == "panel"
                    else "annotation"
                    if kind in {"text", "legend"}
                    else "component"
                ),
                "sourceIds": ["reference-image", "source-authority"],
                "required": True,
                "editable": True,
            }
        )

    # Backend-native grouping is intentionally limited to safe leaf
    # composites.  Connector endpoints, formulas, assets, and nested groups
    # remain ungrouped so topology, native-math readback, and provenance stay
    # directly addressable.  The wrapper is a derived backend node; it does
    # not invent a new scientific object.
    endpoints = {
        str(edge[key])
        for edge in spec.get("edges", [])
        if isinstance(edge, Mapping)
        for key in ("from", "to")
        if isinstance(edge.get(key), str)
    }
    children_by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for element in spec["elements"]:
        parent_id = element.get("parent_id")
        if isinstance(parent_id, str):
            children_by_parent.setdefault(parent_id, []).append(element)
    for group in spec["elements"]:
        group_id = str(group["id"])
        children = children_by_parent.get(group_id, [])
        if (
            group.get("type") != "group"
            or group_id in endpoints
            or len(children) < 2
            or any(
                str(child["id"]) in endpoints
                or child.get("type") in {"formula", "reference_atomic_asset", "manual_asset_slot", "group", "panel"}
                for child in children
            )
        ):
            continue
        wrapper_id = f"nativegroup.{group_id}"
        bbox = group["bbox"]
        # Figure Spec groups are semantic containers, not necessarily drawn
        # PowerPoint shapes.  Only their rendered direct children can be
        # passed to PowerPoint's native Group command.
        member_ids = [str(child["id"]) for child in children]
        nodes.append(
            {
                "id": wrapper_id,
                "semanticId": _semantic_id(wrapper_id),
                "parentId": group.get("parent_id"),
                "kind": "container",
                "x": _scale(bbox["x"], coordinate_scale),
                "y": _scale(bbox["y"], coordinate_scale),
                "width": _scale(bbox["w"], coordinate_scale),
                "height": _scale(bbox["h"], coordinate_scale),
                "editable": True,
                "zIndex": int(group["z_index"]) + 500000,
                "styleTokens": {
                    "nativeGroupFor": group_id,
                    "nativeGroupMemberIds": "|".join(member_ids),
                },
            }
        )
        entities.append(
            {
                "entityId": _semantic_id(wrapper_id),
                "label": f"Native PowerPoint group for {group_id}",
                "kind": "component",
                "sourceIds": ["reference-image", "source-authority"],
                "required": True,
                "editable": True,
            }
        )

    scene_edges: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    element_by_id = {str(element["id"]): element for element in spec["elements"]}
    for edge in spec.get("edges", []):
        edge_id = str(edge["id"])
        route = {
            "straight": "straight",
            "curve": "curved",
            "orthogonal": "orthogonal",
            "polyline": "orthogonal",
        }.get(str(edge.get("route")), "orthogonal")
        edge_style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
        relation_id = _relation_id(edge_id)
        scene_edge: dict[str, Any] = {
            "id": edge_id,
            "semanticRelationIds": [relation_id],
            "geometryOnly": False,
            "source": str(edge["from"]),
            "target": str(edge["to"]),
            "direction": _edge_direction(edge),
            "route": route,
            "styleTokens": {
                "strokePattern": "dashed"
                if str(edge_style.get("dash", "solid")) not in {"", "none", "solid"}
                else "solid",
                "strokeColor": str(edge_style.get("stroke_color", "#000000")),
                "strokeWidthPx": float(edge_style.get("stroke_width_px", 1.5)),
                "startArrowhead": str(edge_style.get("start_arrowhead", "none")),
                "endArrowhead": str(edge_style.get("end_arrowhead", "triangle")),
                "representation": str(edge.get("representation", "native_connector")),
                "arrowClass": str(edge.get("arrow_class", "thin_connector")),
            },
        }
        carrier_ids: list[str] = []
        carrier_kind = _visual_carrier_kind(edge)
        if carrier_kind is not None:
            source_box = element_by_id[str(edge["from"])]["bbox"]
            target_box = element_by_id[str(edge["to"])]["bbox"]
            start_source = anchor_point(
                source_box,
                str(edge.get("source_anchor", "right")),
                edge.get("source_point"),
            )
            end_source = anchor_point(
                target_box,
                str(edge.get("target_anchor", "left")),
                edge.get("target_point"),
            )
            start = tuple(_scale(value, coordinate_scale) for value in start_source)
            end = tuple(_scale(value, coordinate_scale) for value in end_source)
            if carrier_kind == "filled_shape":
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = math.hypot(dx, dy)
                if length <= 0:
                    raise PowerPointCaseError(f"{edge_id} filled arrow has zero length")
                style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
                thickness = max(
                    4.0, float(style.get("shaft_width_px", 10.0)) * coordinate_scale
                )
                carrier_id = f"carrier.{edge_id}.filled"
                carrier_ids.append(carrier_id)
                nodes.append(
                    {
                        "id": carrier_id,
                        "semanticId": _semantic_id(carrier_id),
                        "parentId": None,
                        "kind": "shape",
                        "x": round((start[0] + end[0] - length) / 2, 6),
                        "y": round((start[1] + end[1] - thickness) / 2, 6),
                        "width": round(length, 6),
                        "height": round(thickness, 6),
                        "rotation": round(math.degrees(math.atan2(dy, dx)), 6),
                        "editable": True,
                        "zIndex": 900000 + len(nodes),
                        "shapeSpec": {"shapeType": "right_arrow", "cornerRadius": 0},
                        "styleTokens": {
                            "fillColor": str(style.get("fill_color", style.get("stroke_color", "#68707A"))),
                            "visualCarrierFor": edge_id,
                        },
                    }
                )
            else:
                points = [
                    start,
                    *[
                        (
                            _scale(point["x"], coordinate_scale),
                            _scale(point["y"], coordinate_scale),
                        )
                        for point in edge.get("via", [])
                    ],
                    end,
                ]
                for index, (begin, finish) in enumerate(
                    zip(points, points[1:], strict=False), start=1
                ):
                    carrier_id = f"carrier.{edge_id}.segment.{index}"
                    carrier_ids.append(carrier_id)
                    nodes.append(
                        {
                            "id": carrier_id,
                            "semanticId": _semantic_id(carrier_id),
                            "parentId": None,
                            "kind": "line",
                            "x": round(min(begin[0], finish[0]), 6),
                            "y": round(min(begin[1], finish[1]), 6),
                            "width": round(max(abs(finish[0] - begin[0]), 0.01), 6),
                            "height": round(max(abs(finish[1] - begin[1]), 0.01), 6),
                            "editable": True,
                            "zIndex": 900000 + len(nodes),
                            "nativePrimitiveSpec": {
                                "schemaVersion": "1.0.0",
                                "coordinateSpace": "canvas",
                                "primitive": {
                                    "family": "line",
                                    "style": {
                                        "strokeColor": str(
                                            edge_style.get("stroke_color", "#68707A")
                                        ),
                                        "strokeWidthPx": float(
                                            edge_style.get("stroke_width_px", 2.0)
                                        ),
                                    },
                                    "points": [
                                        {"x": round(begin[0], 6), "y": round(begin[1], 6)},
                                        {"x": round(finish[0], 6), "y": round(finish[1], 6)},
                                    ],
                                },
                            },
                            "styleTokens": {"visualCarrierFor": edge_id},
                        }
                    )
            for carrier_id in carrier_ids:
                entities.append(
                    {
                        "entityId": _semantic_id(carrier_id),
                        "label": f"Native visual carrier for {edge_id}",
                        "kind": "annotation",
                        "sourceIds": ["reference-image", "source-authority"],
                        "required": True,
                        "editable": True,
                    }
                )
            scene_edge["styleTokens"]["visualCarrierIds"] = "|".join(carrier_ids)
        if edge.get("via"):
            scene_edge["waypoints"] = [
                {
                    "x": _scale(point["x"], coordinate_scale),
                    "y": _scale(point["y"], coordinate_scale),
                    "role": "bend",
                }
                for point in edge["via"]
            ]
        elif str(edge.get("route")) == "curve":
            raise PowerPointCaseError(
                f"{edge_id} needs an explicit cubic path contract before PowerPoint rendering"
            )
        scene_edges.append(scene_edge)
        relations.append(
            {
                "relationId": _relation_id(edge_id),
                "source": _semantic_id(str(edge["from"])),
                "target": _semantic_id(str(edge["to"])),
                "kind": _scientific_relation_kind(str(edge.get("meaning", "data_flow"))),
                "direction": _edge_direction(edge),
                "sourceIds": ["reference-image", "source-authority"],
            }
        )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scene_version = f"autofg-{spec_sha[:16].lower()}"
    scene_graph = {
        "schemaVersion": "2.1.0",
        "projectId": project_id,
        "version": scene_version,
        "canvas": {
            "width": round(slide_width, 6),
            "height": round(slide_height, 6),
            "unit": "pt",
            "profileId": profile_id,
            "backgroundToken": "color.background.white",
        },
        "nodes": nodes,
        "edges": scene_edges,
        "assetSlots": asset_slots,
    }
    all_semantic_ids = [node["semanticId"] for node in nodes] + [
        semantic_id
        for edge in scene_edges
        for semantic_id in edge["semanticRelationIds"]
    ]
    authored_claims = []
    for index, claim in enumerate(spec.get("claims", []), start=1):
        if isinstance(claim, str) and claim.strip():
            authored_claims.append(
                {
                    "claimId": f"claim-{index:04d}",
                    "text": claim.strip(),
                    "evidenceType": "cited",
                    "sourceIds": ["reference-image", "source-authority"],
                    "mustShow": True,
                }
            )
        elif isinstance(claim, Mapping) and str(claim.get("text", "")).strip():
            authored_claims.append(
                {
                    "claimId": str(claim.get("id", f"claim-{index:04d}")),
                    "text": str(claim["text"]).strip(),
                    "evidenceType": str(claim.get("evidence_type", "cited")),
                    "sourceIds": ["reference-image", "source-authority"],
                    "mustShow": bool(claim.get("must_show", True)),
                }
            )
    unknowns = [
        str(item.get("description", "")).strip()
        for item in spec.get("uncertainties", [])
        if isinstance(item, Mapping) and str(item.get("description", "")).strip()
    ]
    if preview_asset_ids:
        unknowns.append(
            "One or more compound reference previews remain replace-before-approval assets."
        )
    scientific_spec = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "figureRole": str(spec.get("figure_role", "system_architecture")),
        "language": str(spec.get("language", "en")),
        "claims": authored_claims,
        "entities": entities,
        "relations": relations,
        "formulas": [
            {
                "formulaId": str(formula["id"]),
                "latex": str(formula["canonical_latex"]),
                "sourceIds": ["source-authority"],
            }
            for formula in spec.get("formulas", [])
        ],
        "evidenceDeclarations": [
            {
                "evidenceId": "reference-reconstruction",
                "representationClass": "source_backed",
                "underlyingDataStatus": "SOURCE_DATA_SUPPLIED",
                "semanticIds": all_semantic_ids,
                "provenanceRequirement": "REQUIRED_AT_QA",
                "rationale": "All visible scientific content is bound to the frozen paper figure, source authority, and PASS AutoFigure review.",
            }
        ],
        "unknowns": unknowns,
        "approval": {
            "status": "PENDING",
            "notes": f"Generated without self-approval; bound to authority {authority_sha} and Figure Spec {spec_sha}.",
        },
    }
    if isinstance(spec.get("audience"), str) and str(spec["audience"]).strip():
        scientific_spec["audience"] = str(spec["audience"]).strip()
    render_elements = []
    for node in nodes:
        is_asset = node["kind"] == "asset"
        asset_route = (
            "reference_atomic_asset"
            if node["id"] in atomic_asset_ids
            else "reference_preview"
            if node["id"] in preview_asset_ids
            else ""
        )
        renderer = "verified_raster" if is_asset else "backend_native"
        if asset_route == "reference_atomic_asset":
            reason = "Source-bound atomic raster preserves reference-specific visual detail; formal content remains native."
        elif is_asset:
            reason = "Compound or unresolved asset route remains replace-before-approval."
        else:
            reason = "Native editable PowerPoint object required."
        render_elements.append(
            {
                "elementId": node["id"],
                "mustRemainEditable": not is_asset,
                "qualityOwner": "asset" if is_asset else "layout",
                "reason": reason,
                "routes": [
                    {
                        "targetId": target_id,
                        "renderer": renderer,
                        **(
                            {"fallbackRenderer": "manual"}
                            if is_asset and asset_route != "reference_atomic_asset"
                            else {}
                        ),
                    }
                ],
            }
        )
    for edge in scene_edges:
        render_elements.append(
            {
                "elementId": edge["id"],
                "mustRemainEditable": True,
                "qualityOwner": "layout",
                "reason": "Native connector preserves topology and editability.",
                "routes": [{"targetId": target_id, "renderer": "backend_native"}],
            }
        )
    render_plan = {
        "schemaVersion": "2.0.0",
        "projectId": project_id,
        "sceneGraphVersion": scene_version,
        "targets": [
            {
                "targetId": target_id,
                "backendId": "powerpoint",
                "adapterId": "powerpoint-live",
                "required": True,
                "deliverables": ["pptx", "png"],
                "requiredCapabilities": [
                    "text_box",
                    "auto_shape",
                    "free_line_or_arrow",
                    "attached_connector",
                    "picture_or_svg",
                ],
            }
        ],
        "elements": render_elements,
        "deliverables": ["pptx", "png"],
    }
    source_manifest = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "taskMode": "RECONSTRUCT_1TO1",
        "designatedReferenceSourceId": "reference-image",
        "designatedReferenceSha256": source_sha.lower(),
        "referenceUsage": "measurement_only",
        "referenceEmbedded": False,
        "independentReferenceCase": True,
        "sources": [
            {
                "sourceId": "reference-image",
                "kind": "reference_image",
                "pathOrUrl": str(source_path),
                "sha256": source_sha.lower(),
                "authority": "user_supplied",
                "licenseStatus": "unknown",
                "confidentiality": "public",
                "notes": "Frozen designated reference; never embedded as a whole-slide image.",
            },
            {
                "sourceId": "source-authority",
                "kind": "prior_source",
                "pathOrUrl": str(authority_path),
                "sha256": authority_sha.lower(),
                "authority": "primary",
                "licenseStatus": "citation_only",
                "confidentiality": "public",
                "notes": "Frozen AutoFigure authority reviewed by the project user.",
            },
        ],
    }
    asset_manifest = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "assets": asset_records,
    }
    evidence_provenance = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "taskMode": "RECONSTRUCT_1TO1",
        "designatedReference": {
            "sourceId": "reference-image",
            "sha256": source_sha.lower(),
            "usage": "measurement_only",
            "embedded": False,
        },
        "reviewIsolation": {
            "doesNotOverrideAssetManifest": True,
            "doesNotEstablishScientificValidity": True,
            "doesNotEstablishRights": True,
        },
        "reviewStatus": "PENDING",
        "lifecycleStatus": "READY_FOR_REVIEW",
        "records": [
            {
                "evidenceId": "reference-reconstruction",
                "objectIds": [node["id"] for node in nodes]
                + [edge["id"] for edge in scene_edges],
                "evidenceType": "conceptual",
                "basisType": "reference_visual_reconstruction",
                "visualBasis": "Frozen designated reference plus frozen AutoFigure authority and PASS preflight.",
                "underlyingData": {
                    "status": "SOURCE_DATA_SUPPLIED",
                    "sourceIds": ["reference-image", "source-authority"],
                    "datasetPaths": [],
                },
                "scientificUse": "QUALITATIVE_VISUAL_ONLY",
                "interpretationLimit": "This is a source-bound visual reconstruction. Atomic source assets carry no new scientific claims; compound previews remain replace-before-approval.",
                "relatedAssetIds": preview_asset_ids + atomic_asset_ids,
                "reviewStatus": "PENDING",
            }
        ],
    }
    project_state = {
        "schemaVersion": "2.0.0",
        "contractVersion": "2.0.0",
        "projectId": project_id,
        "taskMode": "RECONSTRUCT_1TO1",
        "state": "PLANNED",
        "lastSuccessfulState": "PLANNED",
        "profileId": profile_id,
        "requestedBackendIds": ["powerpoint"],
        "updatedAt": now,
        "blockedReason": None,
        "gates": {
            "environment": "PASS",
            "scientificApproval": "PENDING",
            "assetDirection": "PASS",
            "styleApproval": "PENDING",
            "assetIntegrity": "PENDING",
            "backendAudit": "PENDING",
            "artifactSetIntegrity": "PENDING",
            "backendReadback": "PENDING",
            "crossBackendEquivalence": "PENDING",
            "quality": "PENDING",
            "humanApproval": "PENDING",
            "exportReadback": "PENDING",
        },
        "history": [
            {
                "state": "PLANNED",
                "at": now,
                "evidence": [str(resolved_spec), str(resolved_preflight)],
                "note": "Deterministic projection from an authorized AutoFigure Figure Spec; release approval remains pending.",
            }
        ],
    }

    documents = {
        "project_state.json": project_state,
        "input/source_manifest.json": source_manifest,
        "design/scientific_spec.json": scientific_spec,
        "design/scene_graph.json": scene_graph,
        "design/render_plan.json": render_plan,
        "assets/asset_manifest.json": asset_manifest,
        "assets/evidence_provenance.json": evidence_provenance,
    }
    if schema_root is not None:
        resolved_schemas = schema_root.resolve(strict=True)
        schema_names = {
            "project_state.json": "project-state-v2.schema.json",
            "input/source_manifest.json": "source-manifest.schema.json",
            "design/scientific_spec.json": "scientific-spec.schema.json",
            "design/scene_graph.json": "scene-graph-v2.1.schema.json",
            "design/render_plan.json": "render-plan-v2.schema.json",
            "assets/asset_manifest.json": "asset-manifest.schema.json",
            "assets/evidence_provenance.json": "evidence-provenance.schema.json",
        }
        for relative, document in documents.items():
            _validate_document(
                document,
                resolved_schemas / schema_names[relative],
                relative,
            )
    for relative, document in documents.items():
        atomic_write_json(output_case / relative, document)
    receipt = {
        "schema_version": "1.0.0",
        "document_type": "AUTOFIGURE_POWERPOINT_CASE_RECEIPT",
        "status": "POWERPOINT_CASE_PREPARED_REQUIRES_REVIEW",
        "created_at_utc": now,
        "project_id": project_id,
        "target_id": target_id,
        "task_mode": "RECONSTRUCT_1TO1",
        "figure_spec": {"path": str(resolved_spec), "sha256": spec_sha},
        "preflight": {
            "path": str(resolved_preflight),
            "sha256": sha256_file(resolved_preflight),
        },
        "canvas": {
            "path": str(input_dir / "canvas.pptx"),
            "sha256": canvas_sha,
        },
        "coordinate_mapping": {
            "source_unit": "px",
            "target_unit": "pt",
            "scale": round(coordinate_scale, 12),
            "translate_x": 0.0,
            "translate_y": 0.0,
            "uniform": True,
        },
        "documents": {
            relative: sha256_file(output_case / relative) for relative in documents
        },
        "scene_counts": {
            "nodes": len(nodes),
            "edges": len(scene_edges),
            "asset_slots": len(asset_slots),
        },
        "release_status": release_ceiling_for_elements(spec["elements"]),
    }
    atomic_write_json(output_case / "case-receipt.json", receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--output-case", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--profile-id", default="journal-double-column")
    parser.add_argument("--schema-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        receipt = prepare_powerpoint_case(
            args.spec,
            args.preflight,
            args.output_case,
            project_id=args.project_id,
            target_id=args.target_id,
            profile_id=args.profile_id,
            schema_root=args.schema_root,
        )
    except (PowerPointCaseError, OSError) as exc:
        print(f"POWERPOINT_CASE_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
