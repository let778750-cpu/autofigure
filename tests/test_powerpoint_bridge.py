from __future__ import annotations

import pytest

from tools.build_powerpoint_draw_batch import (
    POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE,
    POWERPOINT_TEXT_FRAME_MARGINS,
    TRAJECTORY_FORMULA_COLORS,
    _compound_container_operations,
    _formula_box,
    _projected_formula_operations,
    _routing_detail_batch,
    _text_frame_geometry,
    _text_frame_hygiene_batch,
    _trajectory_layout,
)
from tools.prepare_native_math_plan import NativeMathPlanError, _split_sequence
from tools.powerpoint_native_math import _hex_to_office_rgb, _rgb_contrast_ratio
from tools.prepare_powerpoint_case import _edge_direction


def test_trajectory_layout_reconstructs_individual_native_tokens() -> None:
    node = {"x": 100.0, "y": 50.0, "width": 300.0, "height": 120.0}
    imagination = _trajectory_layout("state_reward_trajectory", node)
    rollout = _trajectory_layout("policy_rollout", node)
    assert [len(imagination[key]) for key in ("states", "rewards")] == [4, 3]
    assert [len(rollout[key]) for key in ("states", "actions", "policies")] == [4, 3, 3]
    for geometry in imagination["states"] + imagination["rewards"]:
        assert geometry["left"] >= node["x"]
        assert geometry["top"] >= node["y"]
        assert geometry["left"] + geometry["width"] <= node["x"] + node["width"]
        assert geometry["top"] + geometry["height"] <= node["y"] + node["height"]


def test_trajectory_compound_stays_ungrouped_for_formula_underlay_evidence() -> None:
    element = {
        "id": "asset.imagination-trajectory",
        "asset_kind": "state_reward_trajectory",
        "state_fill": "#CFE3FA",
        "state_stroke": "#6F9ED8",
        "reward_fill": "#F3A1A1",
        "reward_stroke": "#B95A5A",
    }
    node = {"x": 100.0, "y": 50.0, "width": 300.0, "height": 120.0}
    operations = _compound_container_operations(element, node, 256, 0.67)
    assert operations is not None
    assert not any(operation["type"] == "group_shapes" for operation in operations)
    names = {str(operation.get("name")) for operation in operations}
    assert "asset.imagination-trajectory" in names
    assert {f"asset.imagination-trajectory::state-{index}" for index in range(1, 5)} <= names
    assert {f"asset.imagination-trajectory::reward-{index}" for index in range(1, 4)} <= names
    assert not any(operation["type"] == "add_connector" for operation in operations)
    assert sum("flow-" in str(operation.get("name", "")) for operation in operations) == 6


def test_projected_formula_parts_bind_back_to_frozen_parent_element() -> None:
    element = {"id": "formula.reward-sequence"}
    nodes = {
        "asset.imagination-trajectory": {
            "x": 100.0,
            "y": 50.0,
            "width": 300.0,
            "height": 120.0,
        }
    }
    operations = _projected_formula_operations(element, nodes, 256)
    assert operations is not None
    assert [operation["name"] for operation in operations] == [
        "formula.reward-sequence::part-1",
        "formula.reward-sequence::part-2",
        "formula.reward-sequence::part-3",
    ]
    assert {operation["element_id"] for operation in operations} == {
        "formula.reward-sequence"
    }
    assert all(
        "fill_color" not in operation and "fill_transparency" not in operation
        for operation in operations
    )


def test_powerpoint_text_frame_expansion_preserves_left_aligned_anchor() -> None:
    content = {"left": 100.0, "top": 50.0, "width": 80.0, "height": 20.0}
    frame = _text_frame_geometry(content, "left")
    assert frame["left"] + POWERPOINT_TEXT_FRAME_MARGINS["left"] == content["left"]
    assert frame["top"] + POWERPOINT_TEXT_FRAME_MARGINS["top"] == content["top"]
    assert (
        frame["width"]
        - POWERPOINT_TEXT_FRAME_MARGINS["left"]
        - POWERPOINT_TEXT_FRAME_MARGINS["right"]
        == content["width"] + POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE
    )
    assert (
        frame["height"]
        - POWERPOINT_TEXT_FRAME_MARGINS["top"]
        - POWERPOINT_TEXT_FRAME_MARGINS["bottom"]
        == content["height"]
    )


@pytest.mark.parametrize(
    ("alignment", "expected_left_shift"),
    [
        ("left", 0.0),
        ("center", POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE / 2.0),
        ("right", POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE),
    ],
)
def test_powerpoint_text_frame_reserve_preserves_alignment_anchor(
    alignment: str, expected_left_shift: float
) -> None:
    content = {"left": 100.0, "top": 50.0, "width": 80.0, "height": 20.0}
    frame = _text_frame_geometry(content, alignment)
    assert frame["left"] == pytest.approx(
        content["left"]
        - POWERPOINT_TEXT_FRAME_MARGINS["left"]
        - expected_left_shift
    )
    assert frame["width"] == pytest.approx(
        content["width"]
        + POWERPOINT_TEXT_FRAME_MARGINS["left"]
        + POWERPOINT_TEXT_FRAME_MARGINS["right"]
        + POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE
    )


def test_text_frame_hygiene_updates_text_and_text_icon_without_touching_formula() -> None:
    spec = {
        "elements": [
            {"id": "text.label"},
            {"id": "formula.value"},
            {"id": "icon.reward", "icon_kind": "trophy"},
        ]
    }
    scene = {
        "nodes": [
            {
                "id": "text.label",
                "kind": "text",
                "x": 1,
                "y": 2,
                "width": 3,
                "height": 4,
            },
            {
                "id": "formula.value",
                "kind": "formula",
                "x": 5,
                "y": 6,
                "width": 7,
                "height": 8,
            },
            {
                "id": "icon.reward",
                "kind": "shape",
                "x": 9,
                "y": 10,
                "width": 11,
                "height": 12,
            },
        ]
    }
    batch = _text_frame_hygiene_batch(spec, scene, 256)
    assert batch["element_ids"] == ["text.label", "icon.reward"]
    assert [operation["shape_name"] for operation in batch["operations"]] == [
        "text.label",
        "icon.reward",
    ]


def test_circular_formula_uses_nearly_the_full_native_token_width() -> None:
    token = {"left": 20.0, "top": 30.0, "width": 28.0, "height": 28.0}
    formula = _formula_box(token, circular=True)
    assert formula["width"] == pytest.approx(token["width"] * 0.96)
    assert formula["height"] == pytest.approx(token["height"] * 0.80)


def test_routing_detail_reconstructs_two_native_dotted_paths() -> None:
    scene = {
        "nodes": [
            {
                "id": "region.modular-fusion",
                "x": 300.0,
                "y": 120.0,
                "width": 300.0,
                "height": 180.0,
            }
        ]
    }
    batch = _routing_detail_batch(scene, 256)
    operations = batch["operations"]
    assert batch["operation_count"] == 50
    assert all(operation["type"] == "add_shape" for operation in operations)
    assert {operation["element_id"] for operation in operations} == {
        "region.modular-fusion"
    }
    assert sum(operation["fill_color"] == "#3569B0" for operation in operations) == 25
    assert sum(operation["fill_color"] == "#D92A2A" for operation in operations) == 25


def test_trajectory_formula_colors_meet_text_contrast_guardrail() -> None:
    backgrounds = {
        "state": "#CFE3FA",
        "reward": "#F3A1A1",
        "action": "#F6C391",
        "policy": "#C4B7E8",
    }
    for role, background in backgrounds.items():
        ratio = _rgb_contrast_ratio(
            _hex_to_office_rgb(TRAJECTORY_FORMULA_COLORS[role]),
            _hex_to_office_rgb(background),
        )
        assert ratio >= 4.5


def test_native_math_projection_split_is_lossless_and_brace_aware() -> None:
    assert _split_sequence(
        r"(z_t^\tau,z_{t+1}^\tau,z_{t+2}^\tau,\ldots,z_{t+h}^\tau)", 4
    ) == [
        r"z_t^\tau",
        r"z_{t+1}^\tau",
        r"z_{t+2}^\tau",
        r"z_{t+h}^\tau",
    ]
    with pytest.raises(NativeMathPlanError, match="expected 4 parts"):
        _split_sequence(r"(r_t,r_{t+1},r_{t+2})", 4)


def test_edge_direction_only_marks_interaction_as_bidirectional() -> None:
    assert _edge_direction({"meaning": "feedback", "style": {"arrowhead": "triangle"}}) == "forward"
    assert _edge_direction({"meaning": "interaction", "style": {"arrowhead": "triangle"}}) == "bidirectional"
    assert _edge_direction({"meaning": "data_flow", "style": {"arrowhead": "both"}}) == "bidirectional"
