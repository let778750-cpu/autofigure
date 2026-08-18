"""Case-neutral classification-first routing for Figure Spec v4 elements and edges."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class RenderStrategyError(ValueError):
    """Raised when visual responsibility is ambiguous or internally inconsistent."""


NATIVE_REQUIRED_TYPES = {"text", "formula"}
REFERENCE_ROLES = {"photo", "texture", "complex_icon", "style_arrow"}
FORMAL_ROLE_MARKERS = (
    "text",
    "formula",
    "number",
    "unit",
    "axis",
    "legend_label",
    "data_label",
    "connector_label",
)


def classify_element(element: Mapping[str, Any]) -> str:
    """Select a representation before drawing; do not use failed rendering as the classifier."""
    element_type = str(element.get("type", ""))
    role = str(element.get("asset_role", element.get("semantic_role", ""))).casefold()
    if element_type == "manual_asset_slot":
        return "manual_asset_slot"
    if element_type == "reference_atomic_asset":
        return "reference_atomic_asset"
    if element_type in NATIVE_REQUIRED_TYPES or any(marker in role for marker in FORMAL_ROLE_MARKERS):
        return "native_required"
    if role in REFERENCE_ROLES:
        return "reference_atomic_asset"
    if str(element.get("disposition", "")) in {"INCONCLUSIVE", "UNREADABLE"}:
        return "source_ambiguity"
    if element_type in {
        "background",
        "panel",
        "group",
        "native_shape",
        "shape",
        "icon",
        "plot",
        "legend",
    }:
        return "native_preferred"
    raise RenderStrategyError(
        f"{element.get('id', '<unknown>')} has no deterministic render-strategy classification"
    )


def classify_edge(edge: Mapping[str, Any]) -> tuple[str, str]:
    style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
    if edge.get("visual_asset_id") or style.get("complex_style") is True:
        return "reference_atomic_asset", "style_asset"
    if style.get("filled_arrow") is True or str(style.get("shape", "")) in {
        "block_arrow",
        "chevron",
        "wedge",
    }:
        return "native_connector", "filled_native"
    return (
        "native_line_chain" if edge.get("via") else "native_connector",
        "thin_connector",
    )


def validate_render_strategy_contract(
    elements: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> None:
    by_id = {str(element.get("id", "")): element for element in elements}
    for element in elements:
        element_id = str(element.get("id", "<unknown>"))
        expected = classify_element(element)
        actual = str(element.get("render_strategy", ""))
        if actual != expected:
            raise RenderStrategyError(
                f"{element_id} render_strategy={actual!r} conflicts with classification={expected!r}"
            )
        if actual == "reference_atomic_asset" and not isinstance(element.get("asset_binding"), Mapping):
            raise RenderStrategyError(f"{element_id} lacks a source-bound atomic asset receipt")
        if actual == "manual_asset_slot" and not isinstance(element.get("slot_contract"), Mapping):
            raise RenderStrategyError(f"{element_id} lacks a manual slot contract")
    for edge in edges:
        edge_id = str(edge.get("id", "<unknown>"))
        expected_representation, expected_class = classify_edge(edge)
        actual_representation = str(edge.get("representation", ""))
        actual_class = str(edge.get("arrow_class", ""))
        if actual_representation != expected_representation or actual_class != expected_class:
            raise RenderStrategyError(
                f"{edge_id} routing ({actual_representation}, {actual_class}) conflicts with "
                f"classification ({expected_representation}, {expected_class})"
            )
        if actual_representation == "reference_atomic_asset":
            asset_id = str(edge.get("visual_asset_id", ""))
            asset = by_id.get(asset_id)
            if asset is None or asset.get("type") != "reference_atomic_asset":
                raise RenderStrategyError(
                    f"{edge_id} visual_asset_id must reference one reference_atomic_asset"
                )


__all__ = [
    "RenderStrategyError",
    "classify_edge",
    "classify_element",
    "validate_render_strategy_contract",
]
