"""autofigure arrows — 箭头结构审计与确定性几何修复（advisory，非门禁）。

为什么需要它：箭头缺陷（头线脱开 / 偏轴 / 比例失调）在像素指标上不可分辨——
一支箭头 ≈ 画布 0.04%，修好修坏 mean 只动 ~0.06（噪声级），OCR 文本比对也不覆盖。
该缺陷类对整个反馈回路不可见，本模块给 check 补上结构层面的"眼睛"。

审计口径与 convert 的放置语义镜像（canvas(p) = v + R(θ)·(p − ref)）：
- F1 锚点未对齐尖端：marker refX/refY ≠ 三角尖端局部坐标（convert 忠实复刻，
  尖端越出端点 tipX−refX px，底边沉入 refX px）
- F2 头/线宽比例失调：head_len / stroke-width 超出 [RATIO_MIN, RATIO_MAX] 带；
  若该 marker 给了原图校准值（--calibrate ID=LEN），改按"头长偏离校准值 > CAL_TOL"
  判定——合同总则是"以原图为准"，原图本身就是大头部细杆的风格时比例带让位
- F3 端点悬空：箭头线端点距最近形状边缘 > DOCK_TOL px（合同要求落在形状边缘/间隙）
- W4 orient 非 auto：convert 忽略该属性值，按 auto 处理，记 warning
- feather 手折箭羽：无 marker 的手绘箭头（主干 + 短线束箭羽，03 案例模式），只报告不修复

--fix 确定性修复，只动几何不动任何样式（颜色 / 填充 / 线宽一律不变）：
- refX/refY 对齐三角尖端（尖端恰好落在端点上）
- --clamp-ratio：头长超比例带时等比缩放 marker（按使用方的中位线宽定目标）
- --calibrate ID=LEN：按原图实测头长校准（优先于 --clamp-ratio，可放大可缩小）
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.core import common
from tools.pipeline.convert import (
    SVG_NS,
    _element_style,
    _parse_style_attr,
)
from tools.core.svggeom import Matrix, parse_path_d, parse_transform

RATIO_MIN, RATIO_MAX = 1.5, 4.0
REF_TOL = 0.5          # refX/refY 与尖端局部坐标容差（px）
CAL_TOL = 1.0          # 头长与原图校准值的容差（px）
DOCK_TOL = 6.0         # 端点距最近形状边缘的悬空阈值（px）
FEATHER_LEN_MAX = 45.0  # 箭羽最大长度（px）
FEATHER_RADIUS = 18.0   # 箭羽端点距主杆端点的聚簇半径（px）
FEATHER_ANGLE_MIN, FEATHER_ANGLE_MAX = 20.0, 75.0  # 箭羽与主杆方向夹角带（°）
ARROW_TAGS = (f"{SVG_NS}line", f"{SVG_NS}path", f"{SVG_NS}polyline", f"{SVG_NS}polygon")


def _embedded_plot_axis_ids_from_payload(payload: object) -> set[str]:
    """Return explicitly contracted plot-axis IDs from a case contract payload.

    The exemption is intentionally data-driven. It is accepted only from the
    same ``arrow_visual_expectation.exemptions`` records that the frozen
    reference-inventory gate validates; an ID or case-name pattern is never
    inferred here. Reading the same contract shape from ``scene.json`` keeps
    the audit compatible with scene-owned contracts as well as ``regions.json``.
    """

    if not isinstance(payload, dict):
        return set()
    expectation = payload.get("arrow_visual_expectation")
    if not isinstance(expectation, dict):
        return set()
    exemptions = expectation.get("exemptions")
    if not isinstance(exemptions, list):
        return set()
    return {
        element_id
        for record in exemptions
        if isinstance(record, dict)
        and record.get("reason") == "embedded_plot_axis"
        and isinstance((element_id := record.get("element_id")), str)
        and element_id
    }


def _embedded_plot_geometry_groups_from_payload(
    payload: object,
) -> tuple[frozenset[str], ...]:
    """Return plot member sets backed by an explicit plot-axis exemption.

    A plot is eligible only when a frozen reference-inventory ``kind=plot``
    object owns the exempt axis named by ``parent_object_id``. This prevents a
    candidate from globally suppressing feather detection merely by calling an
    arbitrary line a plot member.
    """

    if not isinstance(payload, dict):
        return ()
    inventory = payload.get("reference_inventory")
    expectation = payload.get("arrow_visual_expectation")
    if not isinstance(inventory, dict) or not isinstance(expectation, dict):
        return ()
    objects = inventory.get("objects")
    exemptions = expectation.get("exemptions")
    if not isinstance(objects, list) or not isinstance(exemptions, list):
        return ()
    plot_members = {
        object_id: frozenset(element_ids)
        for record in objects
        if isinstance(record, dict)
        and record.get("kind") == "plot"
        and isinstance((object_id := record.get("id")), str)
        and object_id
        and isinstance((element_ids := record.get("element_ids")), list)
        and element_ids
        and all(isinstance(element_id, str) and element_id for element_id in element_ids)
    }
    eligible_plot_ids = {
        parent_id
        for record in exemptions
        if isinstance(record, dict)
        and record.get("reason") == "embedded_plot_axis"
        and isinstance((parent_id := record.get("parent_object_id")), str)
        and isinstance((axis_id := record.get("element_id")), str)
        and axis_id in plot_members.get(parent_id, frozenset())
    }
    return tuple(
        sorted(
            (plot_members[plot_id] for plot_id in eligible_plot_ids),
            key=lambda members: tuple(sorted(members)),
        )
    )


def _case_embedded_plot_contract(
    run: common.Run,
) -> tuple[set[str], tuple[frozenset[str], ...]]:
    """Load explicit plot-axis and plot-membership contracts for a case."""

    element_ids: set[str] = set()
    geometry_groups: set[frozenset[str]] = set()
    for path in (run.regions_path, run.scene_path):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise common.fail(f"无法读取箭头审计合同 {path}: {exc}") from exc
        element_ids.update(_embedded_plot_axis_ids_from_payload(payload))
        geometry_groups.update(_embedded_plot_geometry_groups_from_payload(payload))
    return element_ids, tuple(
        sorted(geometry_groups, key=lambda members: tuple(sorted(members)))
    )


def _segment_vertices(segments: list[tuple]) -> list[tuple[float, float]]:
    vertices: list[tuple[float, float]] = []
    for segment in segments:
        if segment[0] in {"M", "L"}:
            vertices.append((segment[1], segment[2]))
        elif segment[0] == "C":
            vertices.append((segment[5], segment[6]))
    return vertices


def _start_tangent(segments: list[tuple]) -> tuple[float, float] | None:
    start: tuple[float, float] | None = None
    for segment in segments:
        if segment[0] == "M":
            start = (segment[1], segment[2])
        elif start is not None and segment[0] in {"L", "C"}:
            direction = (segment[1] - start[0], segment[2] - start[1])
            return direction if math.hypot(*direction) > 1e-9 else None
    return None


def _end_tangent(segments: list[tuple]) -> tuple[float, float] | None:
    previous: tuple[float, float] | None = None
    direction: tuple[float, float] | None = None
    for segment in segments:
        if segment[0] == "M":
            previous = (segment[1], segment[2])
        elif segment[0] == "L":
            if previous is not None:
                direction = (segment[1] - previous[0], segment[2] - previous[1])
            previous = (segment[1], segment[2])
        elif segment[0] == "C":
            direction = (segment[5] - segment[3], segment[6] - segment[4])
            previous = (segment[5], segment[6])
    if direction is not None and math.hypot(*direction) <= 1e-9:
        return None
    return direction


def _chord(
    vertices: list[tuple[float, float]],
    forward: bool,
) -> tuple[float, float] | None:
    """Return the stable path chord used when an endpoint tangent degenerates."""

    del forward  # A chord has the same authored direction at either endpoint.
    if len(vertices) < 2:
        return None
    first, last = vertices[0], vertices[-1]
    direction = (last[0] - first[0], last[1] - first[1])
    return None if math.hypot(*direction) <= 1e-9 else direction


# ---------------------------------------------------------------- 解析


def _marker_ref(el: ET.Element, attr: str) -> str | None:
    ref = el.get(attr) or _parse_style_attr(el.get("style")).get(attr)
    match = re.match(r"url\(#([^)]+)\)", ref or "")
    return match.group(1) if match else None


def _marker_defs(root: ET.Element) -> dict[str, ET.Element]:
    return {
        el.get("id"): el
        for el in root.iter(f"{SVG_NS}marker")
        if el.get("id")
    }


def _marker_geometry(marker: ET.Element) -> dict | None:
    """三角 marker 几何：尖 = 到对边中点距离最大的顶点（箭头三角形总是细长，此判据
    对等腰斜边等长的情况稳健——"最长边为底"在等腰时会误判）。"""
    for child in marker:
        if child.tag != f"{SVG_NS}path":
            continue
        points = [
            (seg[1], seg[2])
            for seg in parse_path_d(child.get("d", ""))
            if seg[0] in ("M", "L")
        ]
        if len(points) != 3:
            return None
        tip_index = max(
            range(3),
            key=lambda i: math.dist(
                points[i],
                _mid(points[(i + 1) % 3], points[(i + 2) % 3]),
            ),
        )
        tip = points[tip_index]
        base = [points[(tip_index + 1) % 3], points[(tip_index + 2) % 3]]
        base_mid = _mid(*base)
        return {
            "tip": tip,
            "base_mid": base_mid,
            "head_len": math.dist(base_mid, tip),
            "refX": float(marker.get("refX", 0)),
            "refY": float(marker.get("refY", 0)),
        }
    return None


def _mid(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _stroke_width(el: ET.Element) -> float:
    try:
        return float(_element_style(el, {}).get("stroke-width", "1"))
    except ValueError:
        return 1.0


def _arrow_records(root: ET.Element) -> list[dict]:
    """带 marker 引用的线/路径 → 端点、切线、线宽、引用。"""
    records: list[dict] = []
    for el in root.iter():
        if el.tag not in ARROW_TAGS:
            continue
        refs = {side: _marker_ref(el, f"marker-{side}") for side in ("start", "end")}
        if not any(refs.values()):
            continue
        if el.tag == f"{SVG_NS}line":
            segments = [
                ("M", float(el.get("x1", 0)), float(el.get("y1", 0))),
                ("L", float(el.get("x2", 0)), float(el.get("y2", 0))),
            ]
        else:
            segments = parse_path_d(el.get("d") or _poly_points(el))
        vertices = _segment_vertices(segments)
        if len(vertices) < 2:
            continue
        records.append({
            "element": el,
            "tag": el.tag.replace(SVG_NS, ""),
            "segments": segments,
            "start": vertices[0],
            "end": vertices[-1],
            "start_dir": _start_tangent(segments) or _chord(vertices, forward=True),
            "end_dir": _end_tangent(segments) or _chord(vertices, forward=False),
            "sw": _stroke_width(el),
            "refs": refs,
        })
    return records


def _poly_points(el: ET.Element) -> str:
    nums = re.split(r"[\s,]+", (el.get("points") or "").strip())
    if not nums or len(nums) % 2 or len(nums) < 4:
        return ""
    pairs = [f"{nums[i]},{nums[i + 1]}" for i in range(0, len(nums), 2)]
    d = f"M {pairs[0]} " + " ".join(f"L {p}" for p in pairs[1:])
    if el.tag == f"{SVG_NS}polygon":
        d += " Z"
    return d


def _walk_svg_geometry(
    element: ET.Element,
    parent_style: dict[str, str] | None = None,
    parent_matrix: Matrix = Matrix(),
):
    style = _element_style(element, parent_style or {})
    transform_text = element.get("transform")
    matrix = parent_matrix.multiply(parse_transform(transform_text))
    yield element, style, matrix, _transform_is_valid(transform_text, matrix)
    for child in element:
        yield from _walk_svg_geometry(child, style, matrix)


def _transform_is_valid(value: str | None, matrix: Matrix) -> bool:
    if value:
        remainder = re.sub(
            r"(?:matrix|translate|scale|rotate|skewX|skewY)\s*\([^)]*\)",
            "",
            value,
        )
        if remainder.strip(" ,\t\r\n"):
            return False
    return abs(matrix.a * matrix.d - matrix.b * matrix.c) > 1e-9


def _transform_segments_v3(segments: list[tuple], matrix: Matrix) -> list[tuple]:
    result: list[tuple] = []
    for part in segments:
        if part[0] in ("M", "L"):
            result.append((part[0], *matrix.apply(part[1], part[2])))
        elif part[0] == "C":
            c1 = matrix.apply(part[1], part[2])
            c2 = matrix.apply(part[3], part[4])
            end = matrix.apply(part[5], part[6])
            result.append(("C", *c1, *c2, *end))
        else:
            result.append(part)
    return result


def _element_segments(el: ET.Element) -> list[tuple]:
    if el.tag == f"{SVG_NS}line":
        return [
            ("M", float(el.get("x1", 0)), float(el.get("y1", 0))),
            ("L", float(el.get("x2", 0)), float(el.get("y2", 0))),
        ]
    return parse_path_d(el.get("d") or _poly_points(el))


def _arrow_records_v3(root: ET.Element) -> list[dict]:
    records: list[dict] = []
    for el, style, matrix, transform_valid in _walk_svg_geometry(root):
        if el.tag not in ARROW_TAGS:
            continue
        refs = {side: _marker_ref(el, f"marker-{side}") for side in ("start", "end")}
        if not any(refs.values()):
            continue
        segments = _transform_segments_v3(_element_segments(el), matrix)
        vertices = _segment_vertices(segments)
        if len(vertices) < 2:
            continue
        scale = math.sqrt(abs(matrix.a * matrix.d - matrix.b * matrix.c))
        try:
            stroke_width = float(style.get("stroke-width", "1")) * scale
        except ValueError:
            stroke_width = scale
        element_id = el.get("id") or f"arrow-{len(records) + 1:04d}"
        reference_d = el.get("data-reference-d")
        records.append(
            {
                "element": el,
                "id": element_id,
                "tag": el.tag.replace(SVG_NS, ""),
                "segments": segments,
                "start": vertices[0],
                "end": vertices[-1],
                "start_dir": _start_tangent(segments) or _chord(vertices, forward=True),
                "end_dir": _end_tangent(segments) or _chord(vertices, forward=False),
                "sw": stroke_width,
                "scale": scale,
                "matrix": matrix,
                "refs": refs,
                "source_id": el.get("data-source-id"),
                "target_id": el.get("data-target-id"),
                "transform_valid": transform_valid,
                "reference_segments": parse_path_d(reference_d) if reference_d else None,
            }
        )
    return records


# ---------------------------------------------------------------- F3 形状边缘


def _point_seg_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    abx, aby = b[0] - a[0], b[1] - a[1]
    apx, apy = p[0] - a[0], p[1] - a[1]
    denom = abx * abx + aby * aby
    if denom < 1e-9:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / denom))
    return math.hypot(apx - t * abx, apy - t * aby)


class _EdgeIndex:
    """F3 用：全部几何形状的边缘（矩形边 / 圆 / 椭圆 / 路径折线段）。"""

    def __init__(self, root: ET.Element):
        self.segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self.ellipses: list[tuple[tuple[float, float], float, float]] = []
        for el in root.iter():
            if el.tag == f"{SVG_NS}rect":
                x, y = float(el.get("x", 0)), float(el.get("y", 0))
                w, h = float(el.get("width", 0)), float(el.get("height", 0))
                if w > 0 and h > 0:
                    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
                    self.segments.extend(
                        (corners[i], corners[(i + 1) % 4]) for i in range(4)
                    )
            elif el.tag == f"{SVG_NS}circle":
                self.ellipses.append(
                    ((float(el.get("cx", 0)), float(el.get("cy", 0))), float(el.get("r", 0)), float(el.get("r", 0)))
                )
            elif el.tag == f"{SVG_NS}ellipse":
                self.ellipses.append(
                    ((float(el.get("cx", 0)), float(el.get("cy", 0))), float(el.get("rx", 0)), float(el.get("ry", 0)))
                )
            elif el.tag in (f"{SVG_NS}path", f"{SVG_NS}polyline", f"{SVG_NS}polygon"):
                d = el.get("d") or _poly_points(el)
                if not d:
                    continue
                pts = _segment_vertices(parse_path_d(d))
                self.segments.extend(zip(pts, pts[1:]))

    def distance(self, p: tuple[float, float]) -> float:
        best = min((_point_seg_dist(p, a, b) for a, b in self.segments), default=1e9)
        for center, rx, ry in self.ellipses:
            dx, dy = p[0] - center[0], p[1] - center[1]
            if rx <= 0 or ry <= 0:
                best = min(best, math.hypot(dx, dy))
            else:
                # 近似：径向归一化偏差 × 短半轴（圆时精确为 |dist − r|）
                best = min(best, abs(math.hypot(dx / rx, dy / ry) - 1.0) * min(rx, ry))
        return best


def _flatten_segments(segments: list[tuple], curve_steps: int = 24) -> list[tuple]:
    flattened: list[tuple] = []
    current: tuple[float, float] | None = None
    start: tuple[float, float] | None = None
    for part in segments:
        if part[0] == "M":
            current = (part[1], part[2])
            start = current
        elif part[0] == "L" and current is not None:
            end = (part[1], part[2])
            flattened.append((current, end))
            current = end
        elif part[0] == "C" and current is not None:
            p0 = current
            p1 = (part[1], part[2])
            p2 = (part[3], part[4])
            p3 = (part[5], part[6])
            previous = p0
            for index in range(1, curve_steps + 1):
                t = index / curve_steps
                mt = 1.0 - t
                point = (
                    mt**3 * p0[0]
                    + 3 * mt**2 * t * p1[0]
                    + 3 * mt * t**2 * p2[0]
                    + t**3 * p3[0],
                    mt**3 * p0[1]
                    + 3 * mt**2 * t * p1[1]
                    + 3 * mt * t**2 * p2[1]
                    + t**3 * p3[1],
                )
                flattened.append((previous, point))
                previous = point
            current = p3
        elif part[0] == "Z" and current is not None and start is not None:
            flattened.append((current, start))
            current = start
    return flattened


class _EdgeIndexV3:
    """Identity-aware boundary index; arrow paths never dock to themselves."""

    def __init__(
        self,
        root: ET.Element,
        excluded_element_ids: set[str] | frozenset[str] = frozenset(),
    ):
        self.edges: list[dict] = []
        anonymous = 0
        for el, style, matrix, _ in _walk_svg_geometry(root):
            if el.tag not in {
                f"{SVG_NS}rect",
                f"{SVG_NS}circle",
                f"{SVG_NS}ellipse",
                f"{SVG_NS}polygon",
                f"{SVG_NS}path",
                f"{SVG_NS}polyline",
                f"{SVG_NS}line",
            }:
                continue
            if el.get("id") in excluded_element_ids:
                continue
            if _marker_ref(el, "marker-start") or _marker_ref(el, "marker-end"):
                continue
            role = el.get("data-role", "")
            is_explicit_boundary = el.get("data-audit-boundary") == "true" or role in {
                "node",
                "target",
                "boundary",
                "container",
            }
            fill = style.get("fill")
            if el.tag in {f"{SVG_NS}line", f"{SVG_NS}polyline"} and not is_explicit_boundary:
                continue
            if el.tag == f"{SVG_NS}path" and fill in (None, "none", "transparent"):
                if not is_explicit_boundary:
                    continue
            anonymous += 1
            owner_id = el.get("id") or f"boundary-{anonymous:04d}"
            local_segments: list[tuple]
            if el.tag == f"{SVG_NS}rect":
                x = float(el.get("x", 0))
                y = float(el.get("y", 0))
                width = float(el.get("width", 0))
                height = float(el.get("height", 0))
                rx = min(float(el.get("rx", el.get("ry", 0)) or 0), width / 2)
                ry = min(float(el.get("ry", el.get("rx", 0)) or 0), height / 2)
                local_segments = _rounded_rect_segments(x, y, width, height, rx, ry)
            elif el.tag in {f"{SVG_NS}circle", f"{SVG_NS}ellipse"}:
                cx = float(el.get("cx", 0))
                cy = float(el.get("cy", 0))
                rx = float(el.get("r", el.get("rx", 0)))
                ry = float(el.get("r", el.get("ry", 0)))
                points = [
                    (
                        cx + rx * math.cos(2 * math.pi * index / 72),
                        cy + ry * math.sin(2 * math.pi * index / 72),
                    )
                    for index in range(72)
                ]
                local_segments = [("M", *points[0])]
                local_segments.extend(("L", *point) for point in points[1:])
                local_segments.append(("Z",))
            else:
                local_segments = _element_segments(el)
            transformed = _transform_segments_v3(local_segments, matrix)
            for start, end in _flatten_segments(transformed):
                self.edges.append(
                    {"owner": el, "owner_id": owner_id, "start": start, "end": end}
                )

    def distance(
        self,
        point: tuple[float, float],
        *,
        exclude: ET.Element | None = None,
        expected_id: str | None = None,
    ) -> tuple[float, str | None]:
        candidates = [
            edge
            for edge in self.edges
            if edge["owner"] is not exclude
            and (expected_id is None or edge["owner_id"] == expected_id)
        ]
        if not candidates:
            return 1e9, None
        nearest = min(
            candidates,
            key=lambda edge: _point_seg_dist(point, edge["start"], edge["end"]),
        )
        return (
            _point_seg_dist(point, nearest["start"], nearest["end"]),
            nearest["owner_id"],
        )


def _rounded_rect_segments(
    x: float,
    y: float,
    width: float,
    height: float,
    rx: float,
    ry: float,
) -> list[tuple]:
    if rx <= 0 or ry <= 0:
        return [
            ("M", x, y),
            ("L", x + width, y),
            ("L", x + width, y + height),
            ("L", x, y + height),
            ("Z",),
        ]
    points: list[tuple[float, float]] = []
    for cx, cy, start_angle in (
        (x + width - rx, y + ry, -90),
        (x + width - rx, y + height - ry, 0),
        (x + rx, y + height - ry, 90),
        (x + rx, y + ry, 180),
    ):
        points.extend(
            (
                cx + rx * math.cos(math.radians(start_angle + 90 * step / 8)),
                cy + ry * math.sin(math.radians(start_angle + 90 * step / 8)),
            )
            for step in range(9)
        )
    return [("M", *points[0]), *[("L", *point) for point in points[1:]], ("Z",)]


# ---------------------------------------------------------------- 审计


def _audit_svg_text_legacy(svg_text: str, calibrate: dict[str, float] | None = None) -> dict:
    root = ET.fromstring(svg_text)
    defs = _marker_defs(root)
    geometry = {mid: _marker_geometry(el) for mid, el in defs.items()}
    records = _arrow_records_v3(root)
    edges = _EdgeIndexV3(root)
    calibrate = calibrate or {}

    findings: list[dict] = []
    ratios: list[float] = []
    marker_refs = 0
    for index, rec in enumerate(records):
        for side, vertex in (("start", rec["start"]), ("end", rec["end"])):
            mid = rec["refs"][side]
            if not mid:
                continue
            marker_refs += 1
            geo = geometry.get(mid)
            if geo is None:
                findings.append(_finding("W4", index, rec, side, vertex, mid, "marker 定义不是 3 点三角，无法审计"))
                continue
            if defs[mid].get("orient", "auto") not in ("auto", None):
                findings.append(_finding(
                    "W4", index, rec, side, vertex, mid,
                    f"orient=\"{defs[mid].get('orient')}\" 被 convert 按 auto 处理",
                ))
            dx = geo["tip"][0] - geo["refX"]
            dy = geo["tip"][1] - geo["refY"]
            if abs(dx) > REF_TOL or abs(dy) > REF_TOL:
                findings.append(_finding(
                    "F1", index, rec, side, vertex, mid,
                    f"refX={geo['refX']:g} refY={geo['refY']:g} 与尖端 ({geo['tip'][0]:g},{geo['tip'][1]:g})"
                    f" 偏差 ({dx:+.1f},{dy:+.1f})px",
                ))
            ratio = geo["head_len"] / rec["sw"] if rec["sw"] > 0 else 0.0
            ratios.append(ratio)
            if mid in calibrate:
                target = calibrate[mid]
                if abs(geo["head_len"] - target) > CAL_TOL:
                    findings.append(_finding(
                        "F2", index, rec, side, vertex, mid,
                        f"头长 {geo['head_len']:g} 偏离原图校准值 {target:g}"
                        f"（容差 ±{CAL_TOL:g}，比例带让位于原图）",
                    ))
            elif not RATIO_MIN <= ratio <= RATIO_MAX:
                findings.append(_finding(
                    "F2", index, rec, side, vertex, mid,
                    f"头长 {geo['head_len']:g} / 线宽 {rec['sw']:g} = {ratio:.1f}"
                    f"（合理带 [{RATIO_MIN:g}, {RATIO_MAX:g}]，建议头长 ≤{RATIO_MAX * rec['sw']:g}）",
                ))
            dock = edges.distance(vertex)
            if dock > DOCK_TOL:
                findings.append(_finding(
                    "F3", index, rec, side, vertex, mid,
                    f"端点距最近形状边缘 {dock:.1f}px（> {DOCK_TOL:g}，应落在形状边缘/间隙）",
                ))

    findings.extend(_find_feathers(root))

    counts: dict[str, int] = {}
    for item in findings:
        counts[item["code"]] = counts.get(item["code"], 0) + 1
    return {
        "arrows": len(records),
        "marker_refs": marker_refs,
        "marker_defs": len(defs),
        "findings": findings,
        "counts": counts,
        **({"calibrate": calibrate} if calibrate else {}),
        "ratio_stats": {
            "median": round(statistics.median(ratios), 2) if ratios else None,
            "min": round(min(ratios), 2) if ratios else None,
            "max": round(max(ratios), 2) if ratios else None,
            "band": [RATIO_MIN, RATIO_MAX],
        },
    }


def _percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _nearest_segment_distance(
    point: tuple[float, float],
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> float:
    return min((_point_seg_dist(point, start, end) for start, end in segments), default=1e9)


def _angle_error(first: tuple[float, float], second: tuple[float, float]) -> float:
    first_len = math.hypot(*first)
    second_len = math.hypot(*second)
    if first_len <= 1e-9 or second_len <= 1e-9:
        return 180.0
    cosine = max(
        -1.0,
        min(1.0, (first[0] * second[0] + first[1] * second[1]) / (first_len * second_len)),
    )
    return math.degrees(math.acos(cosine))


def _reference_metrics(rec: dict, diagonal: float) -> dict | None:
    reference = rec.get("reference_segments")
    if not reference:
        return None
    if rec["element"].get("data-reference-space") == "local":
        reference = _transform_segments_v3(reference, rec["matrix"])
    current_edges = _flatten_segments(rec["segments"])
    reference_edges = _flatten_segments(reference)
    if not current_edges or not reference_edges:
        return None
    current_points = [current_edges[0][0], *[edge[1] for edge in current_edges]]
    reference_points = [reference_edges[0][0], *[edge[1] for edge in reference_edges]]
    deviations = [
        _nearest_segment_distance(point, reference_edges) for point in current_points
    ] + [_nearest_segment_distance(point, current_edges) for point in reference_points]
    reference_vertices = _segment_vertices(reference)
    endpoint_error = max(
        math.dist(rec["start"], reference_vertices[0]),
        math.dist(rec["end"], reference_vertices[-1]),
    )
    angle_error = _angle_error(
        rec["end_dir"],
        _end_tangent(reference) or _chord(reference_vertices, forward=False),
    )
    centerline_limit = diagonal * 0.0035
    endpoint_limit = diagonal * 0.0025
    return {
        "element_id": rec["id"],
        "centerline_p95": round(_percentile95(deviations), 4),
        "centerline_limit": round(centerline_limit, 4),
        "endpoint_error": round(endpoint_error, 4),
        "endpoint_limit": round(endpoint_limit, 4),
        "head_angle_error": round(angle_error, 4),
        "head_angle_limit": 3.0,
    }


def _segment_intersection(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
    *,
    strict: bool,
) -> tuple[float, float] | None:
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) <= 1e-9:
        return None
    offset = (c[0] - a[0], c[1] - a[1])
    t = (offset[0] * s[1] - offset[1] * s[0]) / denominator
    u = (offset[0] * r[1] - offset[1] * r[0]) / denominator
    margin = 1e-5 if strict else -1e-5
    if margin < t < 1.0 - margin and margin < u < 1.0 - margin:
        return (a[0] + t * r[0], a[1] + t * r[1])
    return None


def _text_boxes(root: ET.Element) -> list[dict]:
    boxes: list[dict] = []
    for element, style, matrix, _ in _walk_svg_geometry(root):
        if element.tag != f"{SVG_NS}text" or element.get("data-allow-arrow-overlap") == "true":
            continue
        content = "".join(element.itertext()).strip()
        if not content:
            continue
        try:
            font_size = float(style.get("font-size", "16"))
        except ValueError:
            font_size = 16.0
        x = float(re.split(r"[\s,]+", element.get("x", "0").strip())[0])
        y = float(re.split(r"[\s,]+", element.get("y", "0").strip())[0])
        width = max(font_size * 0.55 * len(content), font_size * 0.5)
        anchor = style.get("text-anchor", "start")
        left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
        corners = [
            matrix.apply(left, y - font_size),
            matrix.apply(left + width, y - font_size),
            matrix.apply(left + width, y + font_size * 0.25),
            matrix.apply(left, y + font_size * 0.25),
        ]
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        boxes.append(
            {
                "id": element.get("id") or f"text-{len(boxes) + 1:04d}",
                "owner_id": element.get("data-owner-id"),
                "bbox": (min(xs), min(ys), max(xs), max(ys)),
            }
        )
    return boxes


def _line_hits_box(start, end, bbox) -> tuple[float, float] | None:
    left, top, right, bottom = bbox
    if left <= start[0] <= right and top <= start[1] <= bottom:
        return start
    if left <= end[0] <= right and top <= end[1] <= bottom:
        return end
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for index in range(4):
        hit = _segment_intersection(
            start,
            end,
            corners[index],
            corners[(index + 1) % 4],
            strict=False,
        )
        if hit:
            return hit
    return None


def _finding_v3(
    code: str,
    index: int,
    rec: dict,
    side: str,
    vertex,
    marker: str | None,
    detail: str,
) -> dict:
    x, y = vertex
    return {
        "code": code,
        "element": rec["id"],
        "element_index": index,
        "tag": rec["tag"],
        "side": side,
        "endpoint": [round(x), round(y)],
        "bbox": [round(x - 24), round(y - 24), 48, 48],
        "marker": marker,
        "detail": detail,
    }


def audit_svg_text(
    svg_text: str,
    calibrate: dict[str, float] | None = None,
    *,
    embedded_plot_axis_ids: set[str] | frozenset[str] | None = None,
    embedded_plot_geometry_groups: tuple[frozenset[str], ...]
    | list[set[str]]
    | None = None,
) -> dict:
    """Audit transformed arrow topology against identity-aware target boundaries."""
    root = ET.fromstring(svg_text)
    defs = _marker_defs(root)
    geometry = {marker_id: _marker_geometry(marker) for marker_id, marker in defs.items()}
    records = _arrow_records_v3(root)
    plot_axis_ids = frozenset(embedded_plot_axis_ids or ())
    plot_geometry_groups = tuple(
        frozenset(group)
        for group in (embedded_plot_geometry_groups or ())
        if group
    )
    edges = _EdgeIndexV3(root, plot_axis_ids)
    calibrate = calibrate or {}
    view_box = [
        float(value)
        for value in re.split(r"[\s,]+", root.get("viewBox", "0 0 1 1").strip())
        if value
    ]
    diagonal = math.hypot(view_box[2], view_box[3]) if len(view_box) == 4 else 1.0

    findings: list[dict] = []
    ratios: list[float] = []
    arrow_metrics: list[dict] = []
    calibration_scope: dict[str, str] = {}
    marker_refs = 0
    for index, rec in enumerate(records):
        is_embedded_plot_axis = rec["id"] in plot_axis_ids
        if not rec["transform_valid"]:
            findings.append(
                _finding_v3(
                    "F8",
                    index,
                    rec,
                    "path",
                    rec["start"],
                    None,
                    "transform is singular or contains unsupported syntax",
                )
            )
        for side, vertex in (("start", rec["start"]), ("end", rec["end"])):
            marker_id = rec["refs"][side]
            if marker_id:
                marker_refs += 1
                marker = defs.get(marker_id)
                marker_geometry = geometry.get(marker_id)
                if marker is None or marker_geometry is None:
                    findings.append(
                        _finding_v3(
                            "W4",
                            index,
                            rec,
                            side,
                            vertex,
                            marker_id,
                            "marker is missing or is not a three-point arrowhead",
                        )
                    )
                else:
                    if marker.get("orient", "auto") not in ("auto", "auto-start-reverse"):
                        findings.append(
                            _finding_v3(
                                "W4",
                                index,
                                rec,
                                side,
                                vertex,
                                marker_id,
                                f"unsupported marker orientation: {marker.get('orient')}",
                            )
                        )
                    marker_scale = (
                        rec["sw"]
                        if marker.get("markerUnits", "strokeWidth") == "strokeWidth"
                        else rec["scale"]
                    )
                    dx = (marker_geometry["tip"][0] - marker_geometry["refX"]) * marker_scale
                    dy = (marker_geometry["tip"][1] - marker_geometry["refY"]) * marker_scale
                    if abs(dx) > REF_TOL or abs(dy) > REF_TOL:
                        findings.append(
                            _finding_v3(
                                "F1",
                                index,
                                rec,
                                side,
                                vertex,
                                marker_id,
                                f"arrowhead tip/ref mismatch ({dx:+.2f}, {dy:+.2f}) px",
                            )
                        )
                    head_length = marker_geometry["head_len"] * marker_scale
                    ratio = head_length / rec["sw"] if rec["sw"] > 0 else 0.0
                    ratios.append(ratio)
                    side_key = f"{rec['id']}:{side}"
                    local_raw = (
                        rec["element"].get(f"data-head-length-{side}")
                        or rec["element"].get("data-head-length")
                    )
                    key = next(
                        (
                            candidate
                            for candidate in (side_key, rec["id"], marker_id)
                            if candidate in calibrate
                        ),
                        None,
                    )
                    target = calibrate[key] if key else None
                    local_rejected = False
                    if local_raw is not None:
                        try:
                            local_value = float(local_raw)
                            if not math.isfinite(local_value) or local_value <= 0:
                                raise ValueError
                        except ValueError:
                            findings.append(
                                _finding_v3(
                                    "F2",
                                    index,
                                    rec,
                                    side,
                                    vertex,
                                    marker_id,
                                    f"invalid per-arrow head calibration {local_raw!r}",
                                )
                            )
                            local_rejected = True
                        else:
                            if key is None:
                                findings.append(
                                    _finding_v3(
                                        "F2",
                                        index,
                                        rec,
                                        side,
                                        vertex,
                                        marker_id,
                                        "self-reported head calibration has no "
                                        "reference-bound evidence",
                                    )
                                )
                                local_rejected = True
                            elif abs(local_value - float(target)) > CAL_TOL:
                                findings.append(
                                    _finding_v3(
                                        "F2",
                                        index,
                                        rec,
                                        side,
                                        vertex,
                                        marker_id,
                                        f"self-reported head calibration {local_value:.2f}px "
                                        f"differs from reference evidence {float(target):.2f}px",
                                    )
                                )
                    if key:
                        calibration_scope[side_key] = (
                            "reference-arrow"
                            if key in {side_key, rec["id"]}
                            else "reference-marker"
                        )
                    if target is not None:
                        if abs(head_length - target) > CAL_TOL:
                            findings.append(
                                _finding_v3(
                                    "F2",
                                    index,
                                    rec,
                                    side,
                                    vertex,
                                    marker_id,
                                    f"head length {head_length:.2f}px differs from "
                                    f"per-arrow calibration {target:.2f}px",
                                )
                            )
                    elif not local_rejected and not RATIO_MIN <= ratio <= RATIO_MAX:
                        findings.append(
                            _finding_v3(
                                "F2",
                                index,
                                rec,
                                side,
                                vertex,
                                marker_id,
                                f"head/stroke ratio {ratio:.2f} is outside "
                                f"[{RATIO_MIN:g}, {RATIO_MAX:g}]",
                            )
                        )

            expected_id = rec["source_id"] if side == "start" else rec["target_id"]
            if (
                not is_embedded_plot_axis
                and (marker_id or expected_id)
                and rec["element"].get("data-allow-floating") != "true"
            ):
                expected_distance, expected_owner = edges.distance(
                    vertex,
                    exclude=rec["element"],
                    expected_id=expected_id,
                )
                global_distance, global_owner = edges.distance(
                    vertex,
                    exclude=rec["element"],
                )
                gap_name = "data-source-gap" if side == "start" else "data-target-gap"
                gap_value = rec["element"].get(gap_name)
                expected_gap = float(gap_value) if gap_value is not None else None
                gap_tolerance = float(rec["element"].get("data-gap-tolerance", "2"))
                dock_distance = expected_distance if expected_id else global_distance
                dock_error = (
                    abs(expected_distance - expected_gap)
                    if expected_id and expected_gap is not None
                    else dock_distance
                )
                dock_limit = gap_tolerance if expected_gap is not None else DOCK_TOL
                if expected_id and expected_owner is None:
                    findings.append(
                        _finding_v3(
                            "F5",
                            index,
                            rec,
                            side,
                            vertex,
                            marker_id,
                            f"declared target identity {expected_id!r} does not exist",
                        )
                    )
                elif expected_id and dock_error > dock_limit:
                    findings.append(
                        _finding_v3(
                            "F5",
                            index,
                            rec,
                            side,
                            vertex,
                            marker_id,
                            f"endpoint clearance is {expected_distance:.2f}px from declared "
                            f"object {expected_id!r} (expected {expected_gap or 0:.2f}px); "
                            f"nearest object is {global_owner!r}",
                        )
                    )
                if dock_error > dock_limit:
                    findings.append(
                        _finding_v3(
                            "F3",
                            index,
                            rec,
                            side,
                            vertex,
                            marker_id,
                            f"endpoint boundary/gap error is {dock_error:.2f}px "
                            f"(limit {dock_limit:.2f}px)",
                        )
                    )

        metrics = _reference_metrics(rec, diagonal)
        if metrics:
            arrow_metrics.append(metrics)
            if (
                metrics["centerline_p95"] > metrics["centerline_limit"]
                or metrics["endpoint_error"] > metrics["endpoint_limit"]
            ):
                findings.append(
                    _finding_v3(
                        "F7",
                        index,
                        rec,
                        "path",
                        rec["end"],
                        rec["refs"]["end"],
                        "reference path deviation exceeds the normalized fidelity limit",
                    )
                )
            if metrics["head_angle_error"] > metrics["head_angle_limit"]:
                findings.append(
                    _finding_v3(
                        "F10",
                        index,
                        rec,
                        "end",
                        rec["end"],
                        rec["refs"]["end"],
                        f"arrowhead tangent differs by {metrics['head_angle_error']:.2f} degrees",
                    )
                )

    if root.get("data-check-label-collisions", "true") != "false":
        for index, rec in enumerate(records):
            ignored_owners = {rec["id"], rec["source_id"], rec["target_id"]}
            ignored_owners.discard(None)
            for box in _text_boxes(root):
                if box["owner_id"] in ignored_owners:
                    continue
                hit = next(
                    (
                        point
                        for start, end in _flatten_segments(rec["segments"])
                        if (point := _line_hits_box(start, end, box["bbox"])) is not None
                    ),
                    None,
                )
                if hit:
                    findings.append(
                        _finding_v3(
                            "F6",
                            index,
                            rec,
                            "path",
                            hit,
                            None,
                            f"arrow centerline intersects text box {box['id']!r}",
                        )
                    )
                    break

    for first_index, first in enumerate(records):
        if (
            first["id"] in plot_axis_ids
            or first["element"].get("data-allow-crossing") == "true"
        ):
            continue
        for second in records[first_index + 1 :]:
            if (
                second["id"] in plot_axis_ids
                or second["element"].get("data-allow-crossing") == "true"
            ):
                continue
            shared_ids = {first["source_id"], first["target_id"]} & {
                second["source_id"],
                second["target_id"],
            }
            shared_ids.discard(None)
            if shared_ids:
                continue
            crossing = next(
                (
                    point
                    for a, b in _flatten_segments(first["segments"])
                    for c, d in _flatten_segments(second["segments"])
                    if (point := _segment_intersection(a, b, c, d, strict=True)) is not None
                ),
                None,
            )
            if crossing:
                findings.append(
                    _finding_v3(
                        "F9",
                        first_index,
                        first,
                        "path",
                        crossing,
                        None,
                        f"arrow path crosses {second['id']!r}",
                    )
                )

    findings.extend(
        _find_feathers(
            root,
            excluded_element_ids=plot_axis_ids,
            embedded_plot_geometry_groups=plot_geometry_groups,
        )
    )
    counts: dict[str, int] = {}
    for item in findings:
        counts[item["code"]] = counts.get(item["code"], 0) + 1
    return {
        "audit_version": "3.0.0",
        "arrows": len(records),
        "marker_refs": marker_refs,
        "marker_defs": len(defs),
        "findings": findings,
        "counts": counts,
        "arrow_metrics": arrow_metrics,
        "embedded_plot_axis_exemptions": sorted(plot_axis_ids),
        "embedded_plot_geometry_groups": [
            sorted(group) for group in plot_geometry_groups
        ],
        **({"calibrate": calibrate} if calibrate else {}),
        **({"calibration_scope": calibration_scope} if calibration_scope else {}),
        "ratio_stats": {
            "median": round(statistics.median(ratios), 2) if ratios else None,
            "min": round(min(ratios), 2) if ratios else None,
            "max": round(max(ratios), 2) if ratios else None,
            "band": [RATIO_MIN, RATIO_MAX],
        },
    }


def _finding(code: str, index: int, rec: dict, side: str, vertex, mid: str, detail: str) -> dict:
    x, y = vertex
    return {
        "code": code,
        "element": index,
        "tag": rec["tag"],
        "side": side,
        "endpoint": [round(x), round(y)],
        "bbox": [round(x - 24), round(y - 24), 48, 48],
        "marker": mid,
        "detail": detail,
    }


def _find_feathers(
    root: ET.Element,
    *,
    excluded_element_ids: set[str] | frozenset[str] = frozenset(),
    embedded_plot_geometry_groups: tuple[frozenset[str], ...] = (),
) -> list[dict]:
    """手折箭羽检测：无 marker 主杆端点附近 ±20-75° 的短线束（≥2 根）。"""
    bare: list[dict] = []
    for el in root.iter(f"{SVG_NS}line"):
        if el.get("id") in excluded_element_ids:
            continue
        if _marker_ref(el, "marker-start") or _marker_ref(el, "marker-end"):
            continue
        p1 = (float(el.get("x1", 0)), float(el.get("y1", 0)))
        p2 = (float(el.get("x2", 0)), float(el.get("y2", 0)))
        length = math.dist(p1, p2)
        if length < 1e-6:
            continue
        bare.append(
            {
                "el": el,
                "id": el.get("id"),
                "p1": p1,
                "p2": p2,
                "len": length,
            }
        )

    findings: list[dict] = []
    consumed: set[int] = set()
    for i, shaft in enumerate(bare):
        if shaft["len"] < 20.0:  # 过短的线不作主杆（图标/装饰腿）
            continue
        for vertex, away in ((shaft["p2"], shaft["p1"]), (shaft["p1"], shaft["p2"])):
            shaft_dir = (vertex[0] - away[0], vertex[1] - away[1])
            feathers = []
            for j, cand in enumerate(bare):
                if j == i or cand["len"] > min(FEATHER_LEN_MAX, shaft["len"] / 3):
                    continue
                for cp, cother in ((cand["p1"], cand["p2"]), (cand["p2"], cand["p1"])):
                    if math.dist(cp, vertex) > FEATHER_RADIUS:
                        continue
                    fdir = (vertex[0] - cother[0], vertex[1] - cother[1])
                    angle = math.degrees(math.atan2(
                        abs(shaft_dir[0] * fdir[1] - shaft_dir[1] * fdir[0]),
                        shaft_dir[0] * fdir[0] + shaft_dir[1] * fdir[1],
                    ))
                    if FEATHER_ANGLE_MIN <= angle <= FEATHER_ANGLE_MAX:
                        feathers.append((j, cp))
                        break
            if len(feathers) >= 2:
                cluster_ids = {
                    shaft["id"],
                    *(bare[index]["id"] for index, _ in feathers),
                }
                if None not in cluster_ids and any(
                    cluster_ids.issubset(group)
                    for group in embedded_plot_geometry_groups
                ):
                    continue
                consumed.update(j for j, _ in feathers)
                xs = [p[0] for _, p in feathers] + [vertex[0]]
                ys = [p[1] for _, p in feathers] + [vertex[1]]
                findings.append({
                    "code": "feather",
                    "element": i,
                    "tag": "line",
                    "side": "end" if vertex == shaft["p2"] else "start",
                    "endpoint": [round(vertex[0]), round(vertex[1])],
                    "bbox": [round(min(xs)) - 6, round(min(ys)) - 6,
                             round(max(xs) - min(xs)) + 12, round(max(ys) - min(ys)) + 12],
                    "marker": None,
                    "detail": f"手折箭羽 {len(feathers)} 根（无 marker 手绘箭头，建议改用 marker 定义；只审计不自动修）",
                })
    return findings


# ---------------------------------------------------------------- 修复


def fix_svg_text(
    svg_text: str,
    clamp_ratio: bool = False,
    calibrate: dict[str, float] | None = None,
) -> tuple[str, list[dict]]:
    """确定性几何修复：refX/refY 对齐尖端；可选头长校准/限幅。只动 marker 定义，不动样式。"""
    try:
        from lxml import etree as letree
    except ImportError as exc:  # pragma: no cover - python-pptx 依赖链保证存在
        raise common.fail(f"修复需要 lxml（python-pptx 依赖）: {exc}")

    root = letree.fromstring(svg_text.encode("utf-8"))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    usage: dict[str, list[float]] = {}
    plain_root = ET.fromstring(svg_text)
    records = _arrow_records_v3(plain_root)
    for rec in records:
        for mid in rec["refs"].values():
            if mid:
                usage.setdefault(mid, []).append(rec["sw"])

    fixes: list[dict] = []
    lxml_arrows = root.xpath(
        ".//*[local-name()='line' or local-name()='path' or "
        "local-name()='polyline' or local-name()='polygon']"
        "[@marker-start or @marker-end or contains(@style, 'marker-start') or "
        "contains(@style, 'marker-end')]"
    )
    for rec, arrow in zip(records, lxml_arrows):
        for side in ("start", "end"):
            marker_id = rec["refs"][side]
            if not marker_id:
                continue
            side_key = f"{rec['id']}:{side}"
            calibration_key = next(
                (
                    candidate
                    for candidate in (side_key, rec["id"], marker_id)
                    if candidate in (calibrate or {})
                ),
                None,
            )
            if calibration_key is None:
                continue
            target = (calibrate or {})[calibration_key]
            plain_marker = _find_plain_marker(plain_root, marker_id)
            geometry = _marker_geometry(plain_marker)
            if geometry is None or geometry["head_len"] <= 0:
                continue
            marker_scale = (
                rec["sw"]
                if plain_marker.get("markerUnits", "strokeWidth") == "strokeWidth"
                else rec["scale"]
            )
            current_length = geometry["head_len"] * marker_scale
            if abs(target - current_length) <= 0.05:
                continue
            candidates = root.xpath(".//svg:marker[@id=$marker_id]", namespaces=ns, marker_id=marker_id)
            if not candidates:
                continue
            safe_arrow_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", rec["id"])
            suffix = f"--{safe_arrow_id}-{side}"
            if marker_id.endswith(suffix):
                dedicated = candidates[0]
                new_marker_id = marker_id
            else:
                dedicated = copy.deepcopy(candidates[0])
                new_marker_id = f"{marker_id}{suffix}"
                dedicated.set("id", new_marker_id)
                candidates[0].getparent().append(dedicated)
            scale = target / current_length
            tip_x = geometry["tip"][0] * scale
            tip_y = geometry["tip"][1] * scale
            dedicated.set("refX", _fmt(tip_x))
            dedicated.set("refY", _fmt(tip_y))
            for dimension in ("markerWidth", "markerHeight"):
                if dedicated.get(dimension):
                    dedicated.set(dimension, _fmt(float(dedicated.get(dimension)) * scale))
            for child in dedicated.findall("svg:path", ns):
                child.set("d", _scale_path_d(child.get("d", ""), scale))
            _set_lxml_marker_reference(arrow, side, new_marker_id)
            fixes.append(
                {
                    "arrow": rec["id"],
                    "side": side,
                    "marker": [marker_id, new_marker_id],
                    "head_scale": round(scale, 3),
                    "calibration": target,
                }
            )

    for marker in root.findall(".//svg:marker", ns):
        mid = marker.get("id")
        geo = _marker_geometry(_find_plain_marker(plain_root, mid))
        if geo is None:
            continue
        ref_x, ref_y = geo["refX"], geo["refY"]
        tip_x, tip_y = geo["tip"]
        scale = 1.0
        if mid in (calibrate or {}):
            target = calibrate[mid]
            if geo["head_len"] > 0 and abs(target - geo["head_len"]) > 0.05:
                scale = target / geo["head_len"]
        elif clamp_ratio:
            sws = usage.get(mid, [])
            target = RATIO_MAX * statistics.median(sws) if sws else None
            if target and geo["head_len"] > target > 0:
                scale = target / geo["head_len"]
        moved = abs(tip_x - ref_x) > REF_TOL or abs(tip_y - ref_y) > REF_TOL or scale != 1.0
        if not moved:
            continue
        new_tip_x, new_tip_y = tip_x * scale, tip_y * scale
        marker.set("refX", _fmt(new_tip_x))
        marker.set("refY", _fmt(new_tip_y))
        if scale != 1.0:
            marker.set("markerWidth", _fmt(float(marker.get("markerWidth", 0)) * scale))
            marker.set("markerHeight", _fmt(float(marker.get("markerHeight", 0)) * scale))
            for child in marker.findall("svg:path", ns):
                child.set("d", _scale_path_d(child.get("d", ""), scale))
        fixes.append({
            "marker": mid,
            "refX": [ref_x, _fmt(new_tip_x)],
            "refY": [ref_y, _fmt(new_tip_y)],
            **({"head_scale": round(scale, 3)} if scale != 1.0 else {}),
        })
    return letree.tostring(root, encoding="unicode"), fixes


def _set_lxml_marker_reference(element, side: str, marker_id: str) -> None:
    attribute = f"marker-{side}"
    style = element.get("style")
    if style and attribute in _parse_style_attr(style):
        rewritten = []
        for item in style.split(";"):
            if ":" not in item:
                rewritten.append(item)
                continue
            key, value = item.split(":", 1)
            rewritten.append(f"{key}:url(#{marker_id})" if key.strip() == attribute else item)
        element.set("style", ";".join(rewritten))
    else:
        element.set(attribute, f"url(#{marker_id})")


def _find_plain_marker(plain_root: ET.Element, mid: str | None) -> ET.Element:
    for el in plain_root.iter(f"{SVG_NS}marker"):
        if el.get("id") == mid:
            return el
    return ET.Element(f"{SVG_NS}marker")


def _fmt(value: float) -> str:
    return f"{value:g}"


def _scale_path_d(d: str, scale: float) -> str:
    parts: list[str] = []
    for seg in parse_path_d(d):
        if seg[0] in ("M", "L"):
            parts.append(f"{seg[0]} {_fmt(seg[1] * scale)},{_fmt(seg[2] * scale)}")
        elif seg[0] == "C":
            parts.append(
                f"C {_fmt(seg[1] * scale)},{_fmt(seg[2] * scale)} {_fmt(seg[3] * scale)},{_fmt(seg[4] * scale)}"
                f" {_fmt(seg[5] * scale)},{_fmt(seg[6] * scale)}"
            )
        else:
            parts.append("Z")
    return " ".join(parts)


# ---------------------------------------------------------------- 报告与入口


def render_report(audit: dict) -> list[str]:
    counts = audit.get("counts", {})
    stats = audit.get("ratio_stats", {})
    lines = [
        "## 箭头结构审计（arrows，advisory）",
        "",
        f"- 箭头单元 {audit.get('arrows', 0)}（marker 引用 {audit.get('marker_refs', 0)} 处，"
        f"marker 定义 {audit.get('marker_defs', 0)} 个）；头/线宽比例中位数 {stats.get('median')}"
        f"（合理带 {stats.get('band')}）",
        *(
            [f"- 原图校准：{'、'.join(f'{k}={v:g}px' for k, v in audit['calibrate'].items())}"
             "（F2 按校准值 ±1px 判定，比例带让位于原图实测）"]
            if audit.get("calibrate") else []
        ),
        f"- F1 锚点未对齐尖端 {counts.get('F1', 0)} 处 · F2 头/线宽比例失调 {counts.get('F2', 0)} 处 ·"
        f" F3 端点悬空 {counts.get('F3', 0)} 处 · orient 非 auto {counts.get('W4', 0)} 处 ·"
        f" 手折箭羽 {counts.get('feather', 0)} 组",
        "",
    ]
    if audit.get("findings"):
        lines.append("### 逐条发现")
        for item in audit["findings"]:
            marker_part = f" marker={item['marker']}" if item.get("marker") else ""
            lines.append(
                f"- [{item['code']}] {item['tag']}#{item['element']} {item['side']}"
                f" 端点 ({item['endpoint'][0]},{item['endpoint'][1]}){marker_part}: {item['detail']}"
            )
        lines.append("")
    lines.append("> 箭头几何为定位辅助，不以本节自动放行或拦截；修复用 autofigure arrows --fix"
                 "（几何归一，不改样式），头长限幅加 --clamp-ratio，按原图实测校准加"
                 " --calibrate ID=LEN，改后需重跑 convert/math/check。")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure arrows", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--fix", action="store_true", help="确定性修复（refX/refY 对齐尖端；不改样式）")
    parser.add_argument("--clamp-ratio", action="store_true", help="配合 --fix：头长超出比例带时等比限幅")
    parser.add_argument(
        "--calibrate", action="append", default=[], metavar="ID=LEN",
        help="原图实测头长校准，如 arr-gold=9.6（可重复；审计改按校准值±1px 判 F2，"
             "配合 --fix 时头长缩放到校准值，优先于 --clamp-ratio）",
    )
    args = parser.parse_args(argv)

    calibrate: dict[str, float] = {}
    for item in args.calibrate:
        mid, sep, raw = item.partition("=")
        try:
            if not sep or not mid or float(raw) <= 0:
                raise ValueError
            calibrate[mid] = float(raw)
        except ValueError:
            raise common.fail(f"--calibrate 格式应为 ID=LEN（正数），收到: {item}") from None

    run = common.open_run(args.run_dir)
    if not run.redraw_svg.is_file():
        raise common.fail(f"缺少 redraw.svg: {run.redraw_svg}")
    run.qa_dir.mkdir(exist_ok=True)

    svg_text = run.redraw_svg.read_text(encoding="utf-8")
    plot_axis_ids, plot_geometry_groups = _case_embedded_plot_contract(run)
    before = audit_svg_text(
        svg_text,
        calibrate=calibrate,
        embedded_plot_axis_ids=plot_axis_ids,
        embedded_plot_geometry_groups=plot_geometry_groups,
    )
    payload: dict = {"svg": str(run.redraw_svg), "phase": "audit", **before}

    if args.fix:
        new_text, fixes = fix_svg_text(svg_text, clamp_ratio=args.clamp_ratio, calibrate=calibrate)
        run.redraw_svg.write_text(new_text, encoding="utf-8")
        after = audit_svg_text(
            new_text,
            calibrate=calibrate,
            embedded_plot_axis_ids=plot_axis_ids,
            embedded_plot_geometry_groups=plot_geometry_groups,
        )
        payload = {
            "svg": str(run.redraw_svg),
            "phase": "fix",
            "fixes": fixes,
            "before": {k: before[k] for k in ("counts", "ratio_stats")},
            **{k: after[k] for k in (
                "arrows", "marker_refs", "marker_defs", "findings", "counts", "ratio_stats", "calibrate",
            ) if k in after},
        }
        out = run.qa_dir / "arrows-audit.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        counts_b, counts_a = before["counts"], payload["counts"]
        sys.stdout.write(
            f"修复 {len(fixes)} 个 marker 定义；发现 {counts_b} → {counts_a}\n"
            f"SVG 已更新，请重跑 convert → math → check 刷新交付物。\n"
        )
        return 0

    out = run.qa_dir / "arrows-audit.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        f"箭头单元 {payload['arrows']}，发现 {payload['counts']}；"
        f"比例中位数 {payload['ratio_stats']['median']}（带 {payload['ratio_stats']['band']}）\n"
        f"明细: {out}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
