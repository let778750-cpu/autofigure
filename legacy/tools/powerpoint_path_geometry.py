"""Shared deterministic geometry for PowerPoint edge carriers."""

from __future__ import annotations

from typing import Any, Mapping


def anchor_point(
    box: Mapping[str, Any],
    anchor: str,
    explicit_point: Mapping[str, Any] | None = None,
) -> tuple[float, float]:
    if anchor == "free":
        if not isinstance(explicit_point, Mapping):
            raise ValueError("free edge anchor requires an explicit point")
        return float(explicit_point["x"]), float(explicit_point["y"])
    left = float(box.get("x", box.get("left")))
    top = float(box.get("y", box.get("top")))
    width = float(box.get("w", box.get("width")))
    height = float(box.get("h", box.get("height")))
    return {
        "top": (left + width / 2, top),
        "right": (left + width, top + height / 2),
        "bottom": (left + width / 2, top + height),
        "left": (left, top + height / 2),
        "center": (left + width / 2, top + height / 2),
    }.get(anchor, (left + width / 2, top + height / 2))
