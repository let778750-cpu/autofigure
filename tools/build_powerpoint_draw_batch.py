"""Build deterministic PowerPoint MCP operation batches from one frozen case.

The script never opens or edits PowerPoint.  It only translates the case-bound
scene and Figure Spec into published ``powerpoint_draw_sequence`` operations so
each small drawing pass can be inspected and replayed through the managed MCP.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


class DrawBatchError(RuntimeError):
    """Raised when a case cannot be translated without guessing."""


FOUNDATION_IDS = {
    "canvas.background",
    "region.modular-joint",
    "region.behavior-learning",
    "region.joint-optimization",
}

TRAJECTORY_FORMULA_PROJECTIONS: dict[str, tuple[str, str, int]] = {
    "formula.task-imagination-states": (
        "asset.imagination-trajectory",
        "state",
        4,
    ),
    "formula.reward-sequence": ("asset.imagination-trajectory", "reward", 3),
    "formula.rollout-states": ("asset.rollout-trajectory", "state", 4),
    "formula.action-sequence": ("asset.rollout-trajectory", "action", 3),
}

TRAJECTORY_FORMULA_COLORS = {
    "state": "#17324D",
    "reward": "#6F1D1D",
    "action": "#713900",
    "policy": "#33236B",
}

# PowerPoint creates text boxes with these native insets.  The scene graph
# describes the intended *content* box, so expand the invisible text-frame
# geometry around that box instead of relying on text overflowing its inner
# frame.  This keeps the rendered text at the frozen coordinates while making
# later edits stable under PowerPoint's own overflow diagnostics.
POWERPOINT_TEXT_FRAME_MARGINS = {
    "left": 7.2,
    "right": 7.2,
    "top": 3.6,
    "bottom": 3.6,
}

# PowerPoint and the deterministic preflight do not use exactly the same font
# metric engine.  Keep a small horizontal reserve in native text frames so a
# glyph-bound rounding difference does not become a saved-deck overflow.  The
# reserve is placed according to alignment, preserving the frozen text anchor.
POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE = 3.0


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DrawBatchError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DrawBatchError(f"{label} must be a JSON object")
    return value


def _descends_from(
    element: Mapping[str, Any], ancestor_id: str, by_id: Mapping[str, Mapping[str, Any]]
) -> bool:
    parent = element.get("parent_id")
    seen: set[str] = set()
    while isinstance(parent, str):
        if parent == ancestor_id:
            return True
        if parent in seen:
            raise DrawBatchError(f"parent cycle detected at {parent}")
        seen.add(parent)
        parent_element = by_id.get(parent)
        parent = parent_element.get("parent_id") if parent_element else None
    return False


def _phase_for(
    element: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]
) -> str:
    element_id = str(element["id"])
    if element_id in FOUNDATION_IDS:
        return "foundation"
    if _descends_from(element, "region.modular-joint", by_id):
        return "modular_joint"
    if _descends_from(element, "region.behavior-learning", by_id):
        return "behavior"
    if _descends_from(element, "region.joint-optimization", by_id):
        return "joint_optimization"
    bbox = element["bbox"]
    if float(bbox["y"]) < 107:
        return "top"
    return "lower"


def _color(value: Any) -> str | None:
    if not isinstance(value, str) or value.casefold() == "none":
        return None
    if len(value) == 7 and value.startswith("#"):
        return value
    raise DrawBatchError(f"unsupported color token: {value!r}")


def _shape_name(element: Mapping[str, Any]) -> str:
    icon_kind = element.get("icon_kind")
    if icon_kind in {"person_circle", "globe"}:
        return "ellipse"
    if icon_kind == "hand_action":
        return "right_arrow"
    value = str(element.get("shape_kind", "rectangle"))
    return {
        "roundRect": "rounded_rectangle",
        "rounded_rectangle": "rounded_rectangle",
        "rect": "rectangle",
        "rectangle": "rectangle",
        "ellipse": "ellipse",
        "circle": "ellipse",
        "diamond": "diamond",
        "hexagon": "hexagon",
        "trapezoid": "msoShapeTrapezoid",
        "right_arrow": "right_arrow",
        "left_right_arrow": "left_right_arrow",
    }.get(value, "rounded_rectangle" if element.get("corner_radius_px") else "rectangle")


def _base_binding(element_id: str, slide_id: int) -> dict[str, Any]:
    return {
        "slide_id": slide_id,
        "element_id": element_id,
        "semantic_id": f"semantic::{element_id}",
        "name": element_id,
    }


def _geometry(node: Mapping[str, Any]) -> dict[str, float]:
    return {
        "left": float(node["x"]),
        "top": float(node["y"]),
        "width": float(node["width"]),
        "height": float(node["height"]),
    }


def _text_frame_geometry(
    geometry: Mapping[str, float], horizontal_alignment: str = "left"
) -> dict[str, float]:
    """Expand a content box by native insets plus font-metric reserve."""
    left_margin = POWERPOINT_TEXT_FRAME_MARGINS["left"]
    right_margin = POWERPOINT_TEXT_FRAME_MARGINS["right"]
    top_margin = POWERPOINT_TEXT_FRAME_MARGINS["top"]
    bottom_margin = POWERPOINT_TEXT_FRAME_MARGINS["bottom"]
    reserve = POWERPOINT_TEXT_FRAME_HORIZONTAL_RESERVE
    alignment = str(horizontal_alignment).casefold()
    if alignment == "right":
        reserve_left = reserve
    elif alignment == "center":
        reserve_left = reserve / 2.0
    else:
        reserve_left = 0.0
    return {
        "left": float(geometry["left"]) - left_margin - reserve_left,
        "top": float(geometry["top"]) - top_margin,
        "width": float(geometry["width"]) + left_margin + right_margin + reserve,
        "height": float(geometry["height"]) + top_margin + bottom_margin,
    }


def _trajectory_layout(
    asset_kind: str, node: Mapping[str, Any]
) -> dict[str, list[dict[str, float]]]:
    """Return source-faithful native token geometry inside a trajectory asset."""
    left = float(node["x"])
    top = float(node["y"])
    width = float(node["width"])
    height = float(node["height"])
    state_width = width * 0.12
    state_height = height * (0.30 if asset_kind == "state_reward_trajectory" else 0.22)
    state_top = top + height * 0.06
    state_x = (0.02, 0.28, 0.54, 0.86)
    states = [
        {
            "left": left + width * fraction,
            "top": state_top,
            "width": state_width,
            "height": state_height,
        }
        for fraction in state_x
    ]
    if asset_kind == "state_reward_trajectory":
        diameter = height * 0.38
        lower_top = top + height * 0.60
        rewards = [
            {
                "left": state["left"] + (state_width - diameter) / 2,
                "top": lower_top,
                "width": diameter,
                "height": diameter,
            }
            for state in states[:3]
        ]
        return {"states": states, "rewards": rewards, "actions": [], "policies": []}
    action_diameter = height * 0.27
    actions = [
        {
            "left": left + width * fraction,
            "top": top + height * 0.44,
            "width": action_diameter,
            "height": action_diameter,
        }
        for fraction in (0.15, 0.42, 0.69)
    ]
    policy_diameter = height * 0.23
    policies = [
        {
            "left": state["left"] + (state_width - policy_diameter) / 2,
            "top": top + height * 0.76,
            "width": policy_diameter,
            "height": policy_diameter,
        }
        for state in states[:3]
    ]
    return {"states": states, "rewards": [], "actions": actions, "policies": policies}


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
    if fill:
        operation["fill_color"] = fill
    else:
        operation["fill_color"] = "#FFFFFF"
        operation["fill_transparency"] = 100
    if stroke:
        operation["line_color"] = stroke
        operation["line_width"] = round(
            float(element.get("stroke_width_px", 1.0)) * scale, 4
        )
    else:
        operation["line_width"] = 0
    if element.get("dash"):
        operation["dash_style"] = "round_dot"
    icon_kind = element.get("icon_kind")
    if icon_kind:
        icon_text = {
            "person_circle": "●",
            "trophy": "★",
            "hand_action": "",
            "globe": "◎",
        }.get(str(icon_kind), "")
        if icon_text:
            operation.update(
                {
                    "text": icon_text,
                    "font_name": "Arial",
                    "font_size": max(7.0, float(node["height"]) * 0.44),
                    "font_color": "#111111"
                    if icon_kind == "person_circle"
                    else "#345B82",
                    "alignment": "center",
                    "vertical_alignment": "middle",
                    "text_autofit": "shrink_text",
                    "word_wrap": False,
                }
            )
    return operation


def _part_shape(
    element_id: str,
    slide_id: int,
    name: str,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    shape: str,
    fill: str,
    stroke: str | None,
    line_width: float,
    transparency: float = 0,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "type": "add_shape",
        **_base_binding(element_id, slide_id),
        "name": name,
        "left": round(left, 6),
        "top": round(top, 6),
        "width": round(width, 6),
        "height": round(height, 6),
        "shape": shape,
        "fill_color": fill,
        "fill_transparency": transparency,
        "line_width": line_width,
    }
    if stroke and line_width > 0:
        operation["line_color"] = stroke
    return operation


def _part_textbox(
    element_id: str,
    slide_id: int,
    name: str,
    geometry: Mapping[str, float],
    *,
    text: str,
    font_size: float,
    font_color: str,
    bold: bool = False,
) -> dict[str, Any]:
    return {
        "type": "add_textbox",
        **_base_binding(element_id, slide_id),
        "name": name,
        **{key: round(float(value), 6) for key, value in geometry.items()},
        "text": text,
        "font_name": "Cambria Math",
        "font_size": font_size,
        "font_color": font_color,
        "bold": bold,
        "alignment": "center",
        "vertical_alignment": "middle",
        "text_autofit": "none",
        "word_wrap": False,
        "line_width": 0,
    }


def _part_arrow_between(
    element_id: str,
    slide_id: int,
    name: str,
    source: Mapping[str, float],
    target: Mapping[str, float],
    *,
    color: str = "#9A7610",
    width: float = 1.0,
) -> dict[str, Any]:
    source_x = float(source["left"]) + float(source["width"]) / 2
    source_y = float(source["top"]) + float(source["height"]) / 2
    target_x = float(target["left"]) + float(target["width"]) / 2
    target_y = float(target["top"]) + float(target["height"]) / 2
    delta_x = target_x - source_x
    delta_y = target_y - source_y
    distance = math.hypot(delta_x, delta_y)
    if distance <= 0:
        raise DrawBatchError(f"zero-length trajectory arrow: {name}")
    unit_x, unit_y = delta_x / distance, delta_y / distance
    source_trim = min(float(source["width"]), float(source["height"])) * 0.45
    target_trim = min(float(target["width"]), float(target["height"])) * 0.45
    start_x = source_x + unit_x * source_trim
    start_y = source_y + unit_y * source_trim
    end_x = target_x - unit_x * target_trim
    end_y = target_y - unit_y * target_trim
    length = max(width * 3, math.hypot(end_x - start_x, end_y - start_y))
    operation = _part_shape(
        element_id,
        slide_id,
        name,
        left=(start_x + end_x) / 2 - length / 2,
        top=(start_y + end_y) / 2 - width / 2,
        width=length,
        height=width,
        shape="right_arrow",
        fill=color,
        stroke=None,
        line_width=0,
    )
    operation["rotation"] = round(math.degrees(math.atan2(delta_y, delta_x)), 4)
    return operation


def _group_operation(element_id: str, slide_id: int, names: list[str]) -> dict[str, Any]:
    return {
        "type": "group_shapes",
        **_base_binding(element_id, slide_id),
        "shape_names": names,
    }


def _compound_icon_operations(
    element: Mapping[str, Any], node: Mapping[str, Any], slide_id: int, scale: float
) -> list[dict[str, Any]] | None:
    icon_kind = element.get("icon_kind")
    if icon_kind not in {"person_circle", "trophy", "globe"}:
        return None
    element_id = str(element["id"])
    left = float(node["x"])
    top = float(node["y"])
    width = float(node["width"])
    height = float(node["height"])
    if icon_kind == "trophy":
        return [
            {
                "type": "add_shape",
                **_base_binding(element_id, slide_id),
                **_text_frame_geometry(_geometry(node), "center"),
                "shape": "rectangle",
                "fill_color": "#FFFFFF",
                "fill_transparency": 100,
                "line_width": 0,
                "text": "★",
                "font_name": "Arial",
                "font_size": max(8.0, height * 0.62),
                "font_color": "#8A741E",
                "alignment": "center",
                "vertical_alignment": "middle",
                "text_autofit": "shrink_text",
                "word_wrap": False,
            }
        ]

    base = _part_shape(
        element_id,
        slide_id,
        element_id,
        left=left,
        top=top,
        width=width,
        height=height,
        shape="ellipse",
        fill=_color(element.get("fill")) or "#FFFFFF",
        stroke=_color(element.get("stroke")) or "#18345D",
        line_width=max(0.65, scale),
    )
    operations = [base]
    if icon_kind == "person_circle":
        head = min(width, height) * 0.25
        operations.extend(
            [
                _part_shape(
                    element_id,
                    slide_id,
                    f"{element_id}::head",
                    left=left + (width - head) / 2,
                    top=top + height * 0.18,
                    width=head,
                    height=head,
                    shape="ellipse",
                    fill="#111111",
                    stroke=None,
                    line_width=0,
                ),
                _part_shape(
                    element_id,
                    slide_id,
                    f"{element_id}::body",
                    left=left + width * 0.24,
                    top=top + height * 0.48,
                    width=width * 0.52,
                    height=height * 0.36,
                    shape="ellipse",
                    fill="#111111",
                    stroke=None,
                    line_width=0,
                ),
            ]
        )
    else:
        operations.extend(
            [
                _part_shape(
                    element_id,
                    slide_id,
                    f"{element_id}::land-1",
                    left=left + width * 0.18,
                    top=top + height * 0.22,
                    width=width * 0.34,
                    height=height * 0.24,
                    shape="ellipse",
                    fill="#4F9B45",
                    stroke=None,
                    line_width=0,
                ),
                _part_shape(
                    element_id,
                    slide_id,
                    f"{element_id}::land-2",
                    left=left + width * 0.50,
                    top=top + height * 0.50,
                    width=width * 0.28,
                    height=height * 0.22,
                    shape="ellipse",
                    fill="#4F9B45",
                    stroke=None,
                    line_width=0,
                ),
            ]
        )
    return operations


def _compound_container_operations(
    element: Mapping[str, Any], node: Mapping[str, Any], slide_id: int, scale: float
) -> list[dict[str, Any]] | None:
    asset_kind = element.get("asset_kind")
    if asset_kind not in {
        "token_stack",
        "task_embedding_triplet",
        "token_column",
        "token_column_gradient",
        "vertical_ellipsis",
        "state_reward_trajectory",
        "policy_rollout",
    }:
        return None
    element_id = str(element["id"])
    left = float(node["x"])
    top = float(node["y"])
    width = float(node["width"])
    height = float(node["height"])
    operations: list[dict[str, Any]] = []
    names: list[str] = []

    if asset_kind == "token_stack":
        count = max(2, min(6, int(element.get("layer_count", 4))))
        dx = min(width * 0.08, 4.5)
        dy = min(height * 0.08, 3.0)
        layer_width = width - dx * (count - 1)
        layer_height = height - dy * (count - 1)
        fill = _color(element.get("fill")) or "#EAA15B"
        stroke = _color(element.get("stroke")) or "#B96A2D"
        for index in range(count):
            name = f"{element_id}::layer-{index + 1}"
            names.append(name)
            operations.append(
                _part_shape(
                    element_id,
                    slide_id,
                    name,
                    left=left + dx * index,
                    top=top + dy * index,
                    width=layer_width,
                    height=layer_height,
                    shape="rounded_rectangle",
                    fill=fill,
                    stroke=stroke,
                    line_width=max(0.55, scale),
                )
            )

    elif asset_kind in {
        "task_embedding_triplet",
        "token_column",
        "token_column_gradient",
    }:
        outline = f"{element_id}::outline"
        names.append(outline)
        operations.append(
            _part_shape(
                element_id,
                slide_id,
                outline,
                left=left,
                top=top,
                width=width,
                height=height,
                shape="rectangle",
                fill="#FFFFFF",
                stroke=_color(element.get("stroke")) or "#18345D",
                line_width=max(0.85, scale * 1.5),
            )
        )
        count = max(2, min(5, int(element.get("layer_count", 3))))
        horizontal = asset_kind == "task_embedding_triplet"
        if horizontal:
            diameter = min(height * 0.72, width / (count + 0.45))
            gap = (width - diameter * count) / (count + 1)
        else:
            diameter = min(width * 0.72, height / (count + 0.45))
            gap = (height - diameter * count) / (count + 1)
        for index in range(count):
            name = f"{element_id}::token-{index + 1}"
            names.append(name)
            fill = _color(element.get("fill")) or "#B6B4B5"
            if asset_kind == "token_column_gradient":
                fill = ("#F39B58", "#6CB2B5", "#F39B58")[index % 3]
            token_left = (
                left + gap + index * (diameter + gap)
                if horizontal
                else left + (width - diameter) / 2
            )
            token_top = (
                top + (height - diameter) / 2
                if horizontal
                else top + gap + index * (diameter + gap)
            )
            operations.append(
                _part_shape(
                    element_id,
                    slide_id,
                    name,
                    left=token_left,
                    top=token_top,
                    width=diameter,
                    height=diameter,
                    shape="ellipse",
                    fill=fill,
                    stroke=None,
                    line_width=0,
                )
            )

    elif asset_kind == "vertical_ellipsis":
        anchor = f"{element_id}::bounds"
        names.append(anchor)
        operations.append(
            _part_shape(
                element_id,
                slide_id,
                anchor,
                left=left,
                top=top,
                width=width,
                height=height,
                shape="rectangle",
                fill="#FFFFFF",
                stroke=None,
                line_width=0,
                transparency=100,
            )
        )
        diameter = min(width * 0.42, height * 0.15)
        for index, fraction in enumerate((0.0, 0.5, 1.0), start=1):
            name = f"{element_id}::dot-{index}"
            names.append(name)
            operations.append(
                _part_shape(
                    element_id,
                    slide_id,
                    name,
                    left=left + (width - diameter) / 2,
                    top=top + (height - diameter) * fraction,
                    width=diameter,
                    height=diameter,
                    shape="ellipse",
                    fill="#737B86",
                    stroke=None,
                    line_width=0,
                )
            )

    else:
        # Keep trajectory primitives ungrouped.  Native Office Math auditing
        # must be able to identify the exact opaque state/action/reward circle
        # below each formula; PowerPoint exposes a group's fill as an
        # indeterminate sentinel even when all of its children are solid.
        anchor = element_id
        names.append(anchor)
        operations.append(
            _part_shape(
                element_id,
                slide_id,
                anchor,
                left=left,
                top=top,
                width=width,
                height=height,
                shape="rectangle",
                fill="#F6F6F6",
                stroke=None,
                line_width=0,
            )
        )
        layout = _trajectory_layout(str(asset_kind), node)
        state_fill = _color(element.get("state_fill")) or "#8FC0E8"
        state_stroke = _color(element.get("state_stroke")) or "#4E83BC"
        gold = "#9A7610"
        for index, geometry in enumerate(layout["states"], start=1):
            name = f"{element_id}::state-{index}"
            names.append(name)
            operations.append(
                _part_shape(
                    element_id,
                    slide_id,
                    name,
                    **geometry,
                    shape="rounded_rectangle",
                    fill=state_fill,
                    stroke=state_stroke,
                    line_width=max(0.45, scale * 0.75),
                )
            )
        for index in range(1, 4):
            operations.append(
                _part_arrow_between(
                    element_id,
                    slide_id,
                    f"{element_id}::state-flow-{index}",
                    layout["states"][index - 1],
                    layout["states"][index],
                    color=gold,
                    width=max(0.75, scale * 1.25),
                )
            )
        dot_diameter = max(1.2, height * 0.025)
        for dot_index, fraction in enumerate((0.71, 0.735, 0.76), start=1):
            operations.append(
                _part_shape(
                    element_id,
                    slide_id,
                    f"{element_id}::ellipsis-{dot_index}",
                    left=left + width * fraction,
                    top=layout["states"][0]["top"]
                    + layout["states"][0]["height"] * 0.48,
                    width=dot_diameter,
                    height=dot_diameter,
                    shape="ellipse",
                    fill="#6F7680",
                    stroke=None,
                    line_width=0,
                )
            )
        if asset_kind == "state_reward_trajectory":
            reward_fill = _color(element.get("reward_fill")) or "#F3A1A1"
            reward_stroke = _color(element.get("reward_stroke")) or "#B95A5A"
            for index, geometry in enumerate(layout["rewards"], start=1):
                name = f"{element_id}::reward-{index}"
                names.append(name)
                operations.append(
                    _part_shape(
                        element_id,
                        slide_id,
                        name,
                        **geometry,
                        shape="ellipse",
                        fill=reward_fill,
                        stroke=reward_stroke,
                        line_width=max(0.45, scale * 0.75),
                    )
                )
                operations.append(
                    _part_arrow_between(
                        element_id,
                        slide_id,
                        f"{element_id}::reward-flow-{index}",
                        layout["states"][index - 1],
                        geometry,
                        color=gold,
                        width=max(0.75, scale * 1.2),
                    )
                )
        else:
            action_fill = _color(element.get("action_fill")) or "#C75B10"
            policy_fill = _color(element.get("policy_fill")) or "#8FAEE3"
            for index, geometry in enumerate(layout["actions"], start=1):
                action_name = f"{element_id}::action-{index}"
                names.append(action_name)
                operations.append(
                    _part_shape(
                        element_id,
                        slide_id,
                        action_name,
                        **geometry,
                        shape="ellipse",
                        fill=action_fill,
                        stroke="#A24A0B",
                        line_width=max(0.4, scale * 0.7),
                    )
                )
            for index, geometry in enumerate(layout["policies"], start=1):
                policy_name = f"{element_id}::policy-{index}"
                names.append(policy_name)
                operations.append(
                    _part_shape(
                        element_id,
                        slide_id,
                        policy_name,
                        **geometry,
                        shape="ellipse",
                        fill=policy_fill,
                        stroke="#6F8FC6",
                        line_width=max(0.4, scale * 0.7),
                    )
                )
                operations.extend(
                    [
                        _part_arrow_between(
                            element_id,
                            slide_id,
                            f"{element_id}::state-policy-{index}",
                            layout["states"][index - 1],
                            geometry,
                            color=gold,
                            width=max(0.75, scale * 1.2),
                        ),
                        _part_arrow_between(
                            element_id,
                            slide_id,
                            f"{element_id}::policy-action-{index}",
                            geometry,
                            layout["actions"][index - 1],
                            color=gold,
                            width=max(0.75, scale * 1.2),
                        ),
                        _part_arrow_between(
                            element_id,
                            slide_id,
                            f"{element_id}::action-state-{index + 1}",
                            layout["actions"][index - 1],
                            layout["states"][index],
                            color=gold,
                            width=max(0.75, scale * 1.2),
                        ),
                    ]
                )

    if asset_kind not in {
        "task_embedding_triplet",
        "state_reward_trajectory",
        "policy_rollout",
    }:
        operations.append(_group_operation(element_id, slide_id, names))
    return operations


def _formula_box(
    geometry: Mapping[str, float], *, circular: bool
) -> dict[str, float]:
    # Circular formula labels need almost the full token diameter for
    # multi-character subscripts such as a_{t+1}.  The box is transparent, so
    # using a 2% horizontal inset does not alter the visible circle.
    inset_x = float(geometry["width"]) * (0.02 if circular else 0.04)
    inset_y = float(geometry["height"]) * (0.10 if circular else 0.04)
    return {
        "left": float(geometry["left"]) + inset_x,
        "top": float(geometry["top"]) + inset_y,
        "width": float(geometry["width"]) - inset_x * 2,
        "height": float(geometry["height"]) - inset_y * 2,
    }


def _projected_formula_operations(
    element: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    slide_id: int,
) -> list[dict[str, Any]] | None:
    element_id = str(element["id"])
    projection = TRAJECTORY_FORMULA_PROJECTIONS.get(element_id)
    if projection is None:
        return None
    asset_id, role, count = projection
    asset_node = nodes.get(asset_id)
    if asset_node is None:
        raise DrawBatchError(f"trajectory asset node missing for {element_id}: {asset_id}")
    asset_kind = (
        "state_reward_trajectory"
        if asset_id == "asset.imagination-trajectory"
        else "policy_rollout"
    )
    layout = _trajectory_layout(asset_kind, asset_node)
    geometry_key = {"state": "states", "reward": "rewards", "action": "actions"}[role]
    geometries = layout[geometry_key]
    if len(geometries) != count:
        raise DrawBatchError(
            f"trajectory projection count mismatch for {element_id}: {len(geometries)} != {count}"
        )
    operations: list[dict[str, Any]] = []
    for index, geometry in enumerate(geometries, start=1):
        box = _formula_box(geometry, circular=role in {"reward", "action"})
        operations.append(
            _part_textbox(
                element_id,
                slide_id,
                f"{element_id}::part-{index}",
                box,
                text="?",
                font_size=max(6.0, float(geometry["height"]) * 0.43),
                font_color="#FFFFFF",
            )
        )
    return operations


def _text_operation(
    element: Mapping[str, Any], node: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    element_id = str(element["id"])
    text_spec = node.get("textSpec", {})
    style = element.get("text_style", {})
    is_formula = node.get("kind") == "formula"
    operation: dict[str, Any] = {
        "type": "add_textbox",
        **_base_binding(element_id, slide_id),
        **(
            _geometry(node)
            if is_formula
            else _text_frame_geometry(
                _geometry(node), str(text_spec.get("horizontalAlign", "left"))
            )
        ),
        "text": str(node.get("label", "")),
        "font_name": "Cambria Math" if is_formula else str(style.get("font_family", "Arial")),
        "font_size": float(text_spec.get("fontSize", 12)),
        "font_color": _color(element.get("color")) or "#1F2937",
        "bold": bool(
            "heading" in str(element.get("semantic_role", ""))
            or "encoder" in str(element.get("semantic_role", ""))
            or element_id
            in {
                "text.mapping",
                "text.expert-allocator",
                "text.task-1",
                "text.task-2",
                "text.rollout",
                "text.joint-optimization",
            }
        ),
        "alignment": str(text_spec.get("horizontalAlign", "left")),
        "vertical_alignment": "middle",
        "text_autofit": "none",
        "word_wrap": bool(style.get("wrap", False)),
    }
    return operation


def _asset_operation(
    element: Mapping[str, Any], node: Mapping[str, Any], slide_id: int, case_root: Path
) -> dict[str, Any]:
    element_id = str(element["id"])
    manifest = _load(case_root / "assets" / "asset_manifest.json", "asset manifest")
    records = {
        str(record["assetId"]): record for record in manifest.get("assets", [])
    }
    record = records.get(element_id)
    if not record:
        raise DrawBatchError(f"asset record missing for {element_id}")
    image_path = (case_root / str(record["selectedFile"])).resolve(strict=True)
    return {
        "type": "add_image",
        **_base_binding(element_id, slide_id),
        **_geometry(node),
        "image_path": str(image_path),
        "asset_id": element_id,
        "source_sha256": str(record["sha256"]),
        "raster_reason": "Irreducible photographic observation montage preview.",
        "source_is_tightly_cropped": True,
        "atomic_raster_unit": True,
        "contains_reconstructable_content": False,
        "decomposition_note": "All reconstructable labels and connectors remain separate native objects.",
    }


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
    center_x = float(node["x"]) + float(node["width"]) / 2
    center_y = float(node["y"]) + float(node["height"]) / 2
    if point:
        point_x = float(point["x"]) * scale
        point_y = float(point["y"]) * scale
    else:
        point_x = float(other_node["x"]) + float(other_node["width"]) / 2
        point_y = float(other_node["y"]) + float(other_node["height"]) / 2
    delta_x = point_x - center_x
    delta_y = point_y - center_y
    if abs(delta_x) >= abs(delta_y):
        return 4 if delta_x >= 0 else 2
    return 3 if delta_y >= 0 else 1


def _connector_batch(
    spec: Mapping[str, Any],
    scene: Mapping[str, Any],
    slide_id: int,
    scale: float,
) -> dict[str, Any]:
    nodes = {str(node["id"]): node for node in scene["nodes"]}
    scene_edges = {str(edge["id"]): edge for edge in scene["edges"]}
    operations: list[dict[str, Any]] = []
    element_ids: list[str] = []
    endpoint_names = {
        "region.task-embedding": "region.task-embedding::outline",
        "asset.observation-montage": "asset.observation-montage::anchor",
    }
    for edge in spec.get("edges", []):
        edge_id = str(edge["id"])
        source_id = str(edge["from"])
        target_id = str(edge["to"])
        source_node = nodes[source_id]
        target_node = nodes[target_id]
        style = edge.get("style", {})
        scene_edge = scene_edges[edge_id]
        operation: dict[str, Any] = {
            "type": "add_connector",
            "slide_id": slide_id,
            "element_id": edge_id,
            "semantic_id": str(scene_edge["semanticRelationId"]),
            "name": edge_id,
            "source_name": endpoint_names.get(source_id, source_id),
            "target_name": endpoint_names.get(target_id, target_id),
            "source_site": _nearest_site(
                edge.get("source_anchor"),
                edge.get("source_point"),
                source_node,
                target_node,
                scale,
            ),
            "target_site": _nearest_site(
                edge.get("target_anchor"),
                edge.get("target_point"),
                target_node,
                source_node,
                scale,
            ),
            "connector_type": "straight"
            if edge.get("route") == "straight"
            else "elbow",
            "line_color": _color(style.get("color")) or "#68707A",
            "line_width": round(float(style.get("width_px", 2.0)) * scale, 4),
            "start_arrow": "triangle"
            if edge.get("meaning") == "interaction"
            else "none",
            "end_arrow": "triangle",
        }
        if style.get("dash"):
            operation["dash_style"] = "dash"
        operations.append(operation)
        element_ids.append(edge_id)
    operations.extend(
        {
            "type": "set_z_order",
            "slide_id": slide_id,
            "shape_name": edge_id,
            "command": "bring_to_front",
        }
        for edge_id in element_ids
    )
    # PowerPoint's automatic elbow route for the long feedback edge crosses
    # the interior modules.  Keep it attached, but place it directly above the
    # canvas and below every panel so only the reference-like exterior segment
    # remains visible.
    operations.extend(
        [
            {
                "type": "set_z_order",
                "slide_id": slide_id,
                "shape_name": "edge.behavior-to-joint-objective",
                "command": "send_to_back",
            },
            {
                "type": "set_z_order",
                "slide_id": slide_id,
                "shape_name": "canvas.background",
                "command": "send_to_back",
            },
        ]
    )
    return {
        "schema_version": "1.0.0",
        "phase": "connectors",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": element_ids,
        "operations": operations,
    }


def _routing_detail_batch(
    scene: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    nodes = {str(node["id"]): node for node in scene["nodes"]}
    node = nodes.get("region.modular-fusion")
    if node is None:
        raise DrawBatchError("region.modular-fusion is missing from the scene graph")
    left = float(node["x"])
    top = float(node["y"])
    width = float(node["width"])
    height = float(node["height"])
    diameter = max(0.9, width * 0.0045)
    operations: list[dict[str, Any]] = []
    element_ids: list[str] = []
    for route_name, color, phase in (
        ("task-1", "#3569B0", 0.0),
        ("task-2", "#D92A2A", math.pi),
    ):
        for index in range(25):
            fraction = index / 24
            x = left + width * (0.12 + fraction * 0.76)
            y = top + height * (
                0.52 + 0.24 * math.sin(fraction * math.tau * 1.35 + phase)
            )
            name = f"region.modular-fusion::route::{route_name}::{index + 1}"
            operations.append(
                _part_shape(
                    "region.modular-fusion",
                    slide_id,
                    name,
                    left=x - diameter / 2,
                    top=y - diameter / 2,
                    width=diameter,
                    height=diameter,
                    shape="ellipse",
                    fill=color,
                    stroke=None,
                    line_width=0,
                )
            )
            element_ids.append(name)
    return {
        "schema_version": "1.0.0",
        "phase": "routing_detail",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": element_ids,
        "operations": operations,
    }


def _formula_geometry_batch(
    spec: Mapping[str, Any], scene: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    elements = {str(element["id"]): element for element in spec["elements"]}
    nodes = {str(node["id"]): node for node in scene["nodes"]}
    operations: list[dict[str, Any]] = []
    element_ids: list[str] = []
    for node in scene["nodes"]:
        if node.get("kind") != "formula":
            continue
        element_id = str(node["id"])
        element = elements[element_id]
        text_spec = node.get("textSpec", {})
        targets: list[tuple[str, dict[str, float], float, str, str]] = []
        projection = TRAJECTORY_FORMULA_PROJECTIONS.get(element_id)
        if projection is not None:
            asset_id, role, count = projection
            asset_node = nodes[asset_id]
            asset_kind = (
                "state_reward_trajectory"
                if asset_id == "asset.imagination-trajectory"
                else "policy_rollout"
            )
            layout = _trajectory_layout(asset_kind, asset_node)
            geometry_key = {
                "state": "states",
                "reward": "rewards",
                "action": "actions",
            }[role]
            geometries = layout[geometry_key]
            if len(geometries) != count:
                raise DrawBatchError(f"projection geometry mismatch for {element_id}")
            for index, geometry in enumerate(geometries, start=1):
                targets.append(
                    (
                        f"{element_id}::part-{index}",
                        _formula_box(geometry, circular=role in {"reward", "action"}),
                        max(6.0, float(geometry["height"]) * 0.43),
                        TRAJECTORY_FORMULA_COLORS[role],
                        "center",
                    )
                )
        elif element_id in {"formula.policy.1", "formula.policy.2", "formula.policy.3"}:
            index = int(element_id.rsplit(".", 1)[1]) - 1
            rollout = _trajectory_layout("policy_rollout", nodes["asset.rollout-trajectory"])
            geometry = rollout["policies"][index]
            targets.append(
                (
                    element_id,
                    _formula_box(geometry, circular=True),
                    max(6.0, float(geometry["height"]) * 0.48),
                    TRAJECTORY_FORMULA_COLORS["policy"],
                    "center",
                )
            )
        else:
            targets.append(
                (
                    element_id,
                    _geometry(node),
                    float(text_spec.get("fontSize", 12)),
                    _color(element.get("color")) or "#1F2937",
                    str(text_spec.get("horizontalAlign", "left")),
                )
            )
        for shape_name, geometry, font_size, font_color, alignment in targets:
            operations.append(
                {
                    "type": "update_shape",
                    "slide_id": slide_id,
                    "shape_name": shape_name,
                    **geometry,
                    "font_name": "Cambria Math",
                    "font_size": font_size,
                    "font_color": font_color,
                    "alignment": alignment,
                    "vertical_alignment": "middle",
                    "text_autofit": "none",
                    "word_wrap": False,
                }
            )
            operations.append(
                {
                    "type": "set_z_order",
                    "slide_id": slide_id,
                    "shape_name": shape_name,
                    "command": "bring_to_front",
                }
            )
            element_ids.append(shape_name)
    return {
        "schema_version": "1.0.0",
        "phase": "formula_geometry",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": element_ids,
        "operations": operations,
    }


def _text_frame_hygiene_batch(
    spec: Mapping[str, Any], scene: Mapping[str, Any], slide_id: int
) -> dict[str, Any]:
    """Resize existing native text frames around their frozen content boxes."""
    elements = {str(element["id"]): element for element in spec["elements"]}
    operations: list[dict[str, Any]] = []
    element_ids: list[str] = []
    for node in scene["nodes"]:
        element_id = str(node["id"])
        element = elements.get(element_id)
        if element is None:
            raise DrawBatchError(f"scene node has no Figure Spec element: {element_id}")
        is_text = str(node.get("kind")) == "text"
        is_text_icon = str(element.get("icon_kind", "")) == "trophy"
        if not is_text and not is_text_icon:
            continue
        operations.append(
            {
                "type": "update_shape",
                "slide_id": slide_id,
                "shape_name": element_id,
                **_text_frame_geometry(
                    _geometry(node),
                    "center"
                    if is_text_icon
                    else str(node.get("textSpec", {}).get("horizontalAlign", "left")),
                ),
            }
        )
        element_ids.append(element_id)
    return {
        "schema_version": "1.0.0",
        "phase": "text_frame_hygiene",
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": element_ids,
        "operations": operations,
    }


def build_batch(case_root: Path, phase: str, slide_id: int) -> dict[str, Any]:
    resolved_case = case_root.resolve(strict=True)
    receipt = _load(resolved_case / "case-receipt.json", "case receipt")
    if receipt.get("status") != "POWERPOINT_CASE_READY":
        raise DrawBatchError("case receipt is not POWERPOINT_CASE_READY")
    scene = _load(resolved_case / "design" / "scene_graph.json", "scene graph")
    spec_path = Path(str(receipt["figure_spec"]["path"])).resolve(strict=True)
    spec = _load(spec_path, "Figure Spec")
    scale = float(receipt["coordinate_mapping"]["scale"])
    if phase == "connectors":
        return _connector_batch(spec, scene, slide_id, scale)
    if phase == "routing_detail":
        return _routing_detail_batch(scene, slide_id)
    if phase == "formula_geometry":
        return _formula_geometry_batch(spec, scene, slide_id)
    if phase == "text_frame_hygiene":
        return _text_frame_hygiene_batch(spec, scene, slide_id)
    elements = {str(element["id"]): element for element in spec["elements"]}
    nodes = {str(node["id"]): node for node in scene["nodes"]}
    selected = [
        element
        for element in spec["elements"]
        if _phase_for(element, elements) == phase
    ]
    selected.sort(key=lambda element: (int(element["z_index"]), str(element["id"])))
    operations: list[dict[str, Any]] = []
    for element in selected:
        element_id = str(element["id"])
        node = nodes.get(element_id)
        if not node:
            raise DrawBatchError(f"scene node missing for {element_id}")
        kind = str(node["kind"])
        if kind in {"panel", "container", "shape", "legend"}:
            icon_compound = (
                _compound_icon_operations(element, node, slide_id, scale)
                if kind == "shape"
                else None
            )
            if icon_compound:
                operations.extend(icon_compound)
                continue
            compound = (
                _compound_container_operations(element, node, slide_id, scale)
                if kind == "container"
                else None
            )
            if compound:
                operations.extend(compound)
                continue
            operation = _shape_operation(element, node, slide_id, scale)
        elif kind in {"text", "formula"}:
            projected = (
                _projected_formula_operations(element, nodes, slide_id)
                if kind == "formula"
                else None
            )
            if projected:
                operations.extend(projected)
                continue
            operation = _text_operation(element, node, slide_id)
        elif kind == "asset":
            operation = _asset_operation(element, node, slide_id, resolved_case)
            operations.append(operation)
            operations.append(
                _part_shape(
                    element_id,
                    slide_id,
                    f"{element_id}::anchor",
                    left=float(node["x"]),
                    top=float(node["y"]),
                    width=float(node["width"]),
                    height=float(node["height"]),
                    shape="rectangle",
                    fill="#FFFFFF",
                    stroke=None,
                    line_width=0,
                    transparency=100,
                )
            )
            continue
        else:
            raise DrawBatchError(f"unsupported node kind for {element_id}: {kind}")
        operations.append(operation)
        rotation = element.get("text_style", {}).get("rotation_deg")
        if kind in {"text", "formula"} and isinstance(rotation, (int, float)):
            operations.append(
                {
                    "type": "update_shape",
                    "slide_id": slide_id,
                    "shape_name": element_id,
                    "rotation": float(rotation),
                }
            )
    return {
        "schema_version": "1.0.0",
        "phase": phase,
        "slide_id": slide_id,
        "operation_count": len(operations),
        "element_ids": [str(element["id"]) for element in selected],
        "operations": operations,
    }


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
            "asset_kind": element.get("asset_kind"),
            "icon_kind": element.get("icon_kind"),
            "semantic_role": element.get("semantic_role"),
        }
        for element in spec["elements"]
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", required=True, type=Path)
    parser.add_argument(
        "--phase",
        choices=(
            "foundation",
            "top",
            "modular_joint",
            "behavior",
            "joint_optimization",
            "lower",
            "connectors",
            "routing_detail",
            "formula_geometry",
            "text_frame_hygiene",
        ),
    )
    parser.add_argument("--slide-id", type=int, default=256)
    parser.add_argument("--list-elements", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.list_elements:
            result: Any = list_elements(args.case_root)
        elif args.phase:
            result = build_batch(args.case_root, args.phase, args.slide_id)
        else:
            raise DrawBatchError("either --phase or --list-elements is required")
    except (DrawBatchError, OSError) as exc:
        print(f"POWERPOINT_DRAW_BATCH_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
