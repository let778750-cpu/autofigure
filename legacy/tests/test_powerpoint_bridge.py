from __future__ import annotations

import inspect

import pytest

from tools import build_powerpoint_draw_batch as renderer
from tools.build_powerpoint_draw_batch import (
    DrawBatchError,
    _connector_batch,
    _native_groups_batch,
    _shape_name,
    _text_frame_hygiene_batch,
    _z_order_hygiene_batch,
)
from tools.powerpoint_native_math import (
    NativeMathError,
    _hex_to_office_rgb,
    _minimum_native_math_contrast,
    _rgb_contrast_ratio,
)
from tools.prepare_powerpoint_case import (
    _edge_direction,
    _node_kind,
    _text_spec,
    _visual_carrier_kind,
)
from tools.prepare_powerpoint_case import prepare_powerpoint_case


def _scene() -> dict:
    return {
        "nodes": [
            {"id": "a", "kind": "shape", "x": 0, "y": 10, "width": 20, "height": 20},
            {"id": "b", "kind": "shape", "x": 100, "y": 10, "width": 20, "height": 20},
        ],
        "edges": [{"id": "edge.flow", "semanticRelationId": "semantic::edge.flow"}],
    }


def _edge(**overrides: object) -> dict:
    edge = {
        "id": "edge.flow",
        "from": "a",
        "to": "b",
        "source_anchor": "right",
        "target_anchor": "left",
        "representation": "native_connector",
        "arrow_class": "thin_connector",
        "route": "straight",
        "style": {
            "stroke_color": "#123456",
            "stroke_width_px": 2,
            "dash": "dash",
            "start_arrowhead": "none",
            "end_arrowhead": "triangle",
        },
    }
    edge.update(overrides)
    return edge


def test_renderer_contains_no_case_specific_modularagent_logic() -> None:
    source = inspect.getsource(renderer).casefold()
    assert "modularagent" not in source
    assert "state_reward_trajectory" not in source
    assert "policy_rollout" not in source
    assert "sin(" not in source


def test_unknown_icon_is_rejected_instead_of_approximated() -> None:
    with pytest.raises(DrawBatchError, match="explicit supported shape_kind"):
        _shape_name({"id": "icon.special", "type": "native_shape"})


def test_via_path_uses_explicit_native_line_carriers_and_keeps_topology() -> None:
    spec = {
        "elements": [{"id": "a", "type": "native_shape"}, {"id": "b", "type": "native_shape"}],
        "edges": [_edge(representation="native_line_chain", via=[{"x": 50, "y": 15}, {"x": 70, "y": 35}])],
    }
    scene = _scene()
    scene["nodes"].extend(
        [
            {
                "id": f"carrier.edge.flow.segment.{index}",
                "kind": "line",
                "semanticId": f"semantic::carrier.{index}",
                "styleTokens": {"visualCarrierFor": "edge.flow"},
                "nativePrimitiveSpec": {
                    "primitive": {"points": [{"x": start, "y": 15}, {"x": end, "y": 15}]}
                },
            }
            for index, (start, end) in enumerate(((20, 50), (50, 70), (70, 100)), start=1)
        ]
    )
    scene["edges"][0]["styleTokens"] = {
        "visualCarrierIds": "carrier.edge.flow.segment.1|carrier.edge.flow.segment.2|carrier.edge.flow.segment.3"
    }
    batch = _connector_batch(spec, scene, slide_id=256, scale=1.0)
    operations = batch["operations"]
    assert [item["type"] for item in operations] == [
        "add_line", "add_line", "add_line", "add_connector"
    ]
    assert operations[-1]["name"] == "edge.flow::topology"
    assert operations[-1]["line_width"] == 0
    assert operations[2]["end_arrow"] == "triangle"
    assert operations[0]["dash_style"] == "dash"


def test_explicit_via_path_takes_precedence_over_filled_arrow_shortcut() -> None:
    assert (
        _visual_carrier_kind(
            _edge(
                arrow_class="filled_native",
                via=[{"x": 50, "y": 15}, {"x": 70, "y": 35}],
            )
        )
        == "line_chain"
    )


def test_simple_path_uses_a_native_connector() -> None:
    spec = {
        "elements": [{"id": "a", "type": "native_shape"}, {"id": "b", "type": "native_shape"}],
        "edges": [_edge()],
    }
    operation = _connector_batch(spec, _scene(), slide_id=1, scale=1.0)["operations"][0]
    assert operation["type"] == "add_connector"
    assert operation["source_name"] == "a"
    assert operation["target_name"] == "b"
    assert operation["end_arrow"] == "triangle"


def test_missing_filled_arrow_carrier_is_rejected_instead_of_approximated() -> None:
    spec = {
        "elements": [{"id": "a", "type": "native_shape"}, {"id": "b", "type": "native_shape"}],
        "edges": [_edge(arrow_class="filled_native")],
    }
    with pytest.raises(DrawBatchError, match="requires explicit scene visual carriers"):
        _connector_batch(spec, _scene(), slide_id=256, scale=1.0)


def test_atomic_style_arrow_keeps_invisible_topology_without_visual_duplicate() -> None:
    spec = {
        "elements": [
            {"id": "a", "type": "native_shape"},
            {"id": "b", "type": "native_shape"},
            {"id": "asset.arrow", "type": "reference_atomic_asset"},
        ],
        "edges": [
            _edge(
                representation="reference_atomic_asset",
                arrow_class="reference_styled",
                visual_asset_id="asset.arrow",
            )
        ],
    }
    batch = _connector_batch(spec, _scene(), slide_id=1, scale=1.0)
    assert [item["type"] for item in batch["operations"]] == ["add_connector"]
    assert batch["operations"][0]["line_width"] == 0
    assert batch["operations"][0]["name"] == "edge.flow::topology"
    assert batch["element_ids"] == ["edge.flow"]


def test_text_hygiene_restores_formula_geometry_and_z_order_after_connectors() -> None:
    spec = {"elements": [{"id": "label"}, {"id": "formula"}, {"id": "shape"}]}
    scene = {
        "nodes": [
            {"id": "label", "kind": "text", "x": 1, "y": 2, "width": 3, "height": 4},
            {"id": "formula", "kind": "formula", "x": 1, "y": 2, "width": 3, "height": 4},
            {"id": "shape", "kind": "shape", "x": 1, "y": 2, "width": 3, "height": 4},
        ]
    }
    batch = _text_frame_hygiene_batch(spec, scene, 256)
    assert batch["element_ids"] == ["label", "formula"]
    assert [operation["type"] for operation in batch["operations"]] == [
        "update_shape",
        "update_shape",
        "set_z_order",
    ]
    assert batch["operations"][-1]["shape_name"] == "formula"


def test_rotated_text_uses_transparent_native_shape_supported_by_batch_contract() -> None:
    operation = renderer._text_operation(
        {
            "id": "vertical.label",
            "type": "text",
            "text_style": {"font_family": "Arial", "rotation_deg": 90},
        },
        {
            "kind": "text",
            "label": "Sem",
            "x": 1,
            "y": 2,
            "width": 3,
            "height": 4,
            "textSpec": {"fontSize": 10},
        },
        256,
    )

    assert operation["type"] == "add_shape"
    assert operation["shape"] == "rectangle"
    assert operation["rotation"] == 90
    assert operation["fill_transparency"] == 100


def test_color_contrast_utility_still_meets_black_on_white_guardrail() -> None:
    assert _rgb_contrast_ratio(_hex_to_office_rgb("#000000"), _hex_to_office_rgb("#FFFFFF")) >= 21


def test_native_math_contrast_profiles_are_explicit() -> None:
    assert _minimum_native_math_contrast("standard") == 1.8
    assert _minimum_native_math_contrast("strict") == 4.5
    with pytest.raises(NativeMathError, match="audit_profile"):
        _minimum_native_math_contrast("unknown")


def test_native_group_batch_uses_derived_scene_wrapper() -> None:
    scene = {
        "nodes": [
            {
                "id": "nativegroup.column.1",
                "semanticId": "semantic::nativegroup.column.1",
                "styleTokens": {
                    "nativeGroupFor": "column.1",
                    "nativeGroupMemberIds": "cell.1|label.1",
                },
            }
        ]
    }
    batch = _native_groups_batch(scene, 256)
    assert batch["operations"] == [
        {
            "type": "group_shapes",
            "slide_id": 256,
            "element_id": "nativegroup.column.1",
            "semantic_id": "semantic::nativegroup.column.1",
            "name": "nativegroup.column.1",
            "shape_names": ["cell.1", "label.1"],
        }
    ]


def test_powerpoint_text_spec_preserves_points_and_scales_pixels() -> None:
    point_spec = _text_spec(
        {
            "id": "label",
            "type": "text",
            "text": "Label",
            "font_weight": "bold",
            "text_style": {
                "font_size_pt": 13.5,
                "horizontal_align": "center",
            },
        },
        {},
        0.67179840536,
    )
    assert point_spec["fontSize"] == 13.5
    assert point_spec["fontWeight"] == "semibold"
    assert point_spec["horizontalAlign"] == "center"

    pixel_spec = _text_spec(
        {
            "id": "label",
            "type": "text",
            "text": "Label",
            "text_style": {"font_size_px": 18},
        },
        {},
        0.5,
    )
    assert pixel_spec["fontSize"] == 9.0


def test_powerpoint_formula_text_spec_uses_formula_style_points() -> None:
    text_spec = _text_spec(
        {
            "id": "formula.x",
            "type": "formula",
            "formula_style": {"font_size_pt": 14},
        },
        {"formula.x": {"canonical_latex": "x_1"}},
        0.5,
    )
    assert text_spec["fontSize"] == 14.0
    assert text_spec["text"] == "x_1"


def test_z_order_hygiene_raises_routes_then_ungrouped_leaf_content() -> None:
    spec = {
        "elements": [
            {"id": "panel", "type": "panel", "z_index": 1},
            {"id": "cell", "type": "native_shape", "z_index": 10},
            {"id": "label", "type": "text", "z_index": 20},
            {"id": "free.label", "type": "text", "z_index": 30},
        ]
    }
    scene = {
        "edges": [
            {"id": "edge.direct", "styleTokens": {}},
            {
                "id": "edge.via",
                "styleTokens": {"visualCarrierIds": "carrier.1|carrier.2"},
            },
        ],
        "nodes": [
            {
                "id": "nativegroup.composite",
                "styleTokens": {"nativeGroupMemberIds": "cell|label"},
            }
        ],
    }
    batch = _z_order_hygiene_batch(spec, scene, 256)
    assert [operation["shape_name"] for operation in batch["operations"]] == [
        "edge.direct",
        "carrier.1",
        "carrier.2",
        "nativegroup.composite",
        "free.label",
    ]


def test_edge_direction_honors_explicit_arrowheads() -> None:
    assert _edge_direction({"meaning": "feedback", "style": {"end_arrowhead": "triangle"}}) == "forward"
    assert _edge_direction({"meaning": "interaction", "style": {"end_arrowhead": "triangle"}}) == "bidirectional"
    assert _edge_direction({"meaning": "data_flow", "style": {"start_arrowhead": "triangle", "end_arrowhead": "triangle"}}) == "bidirectional"


def test_case_adapter_default_profile_exists_in_the_managed_plugin_contract() -> None:
    assert inspect.signature(prepare_powerpoint_case).parameters["profile_id"].default == "journal-double-column"


def test_standalone_formula_projects_to_formula_node_not_shape() -> None:
    assert _node_kind({"type": "formula"}) == "formula"
    assert _node_kind({"type": "text", "text_style": {"rotation_deg": 90}}) == "shape"
