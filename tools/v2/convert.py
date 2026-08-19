"""autofigure convert — 把 VLM 重绘的 SVG 转换为原生可编辑 PPTX。

映射合同（references/v2-prompt-contract.md）：
- rect/circle/ellipse/line/polyline/polygon/path → 原生形状 / custGeom 自由曲线（保留三次贝塞尔）
- text/tspan → 原生文本框 runs（italic、字号、颜色、baseline-shift → 上下标）
- linearGradient → a:gradFill；stroke-dasharray → prstDash；marker → 自由曲线箭头
- <rect id="atomic:*"> 占位符 → 从参考图裁剪对应 bbox 嵌入为位图
- <image> 容错：按 bbox 从参考图裁剪替代并记 warning；覆盖画布 ≥50% 直接拒绝（防整图截图冒充矢量）
- <g> 无变换时转原生分组，其余情况拍平（当前 VLM 输出实测无 <g>）
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from tools.v2 import common
from tools.v2.svggeom import Matrix, parse_path_d, parse_transform

SVG_NS = "{http://www.w3.org/2000/svg}"
EMU_PER_PX = 9525
PT_PER_PX = 0.75  # 96 dpi
BASELINE_ASCENT = 0.95  # 实测标定：文本框顶到首行基线（Arial em 比例，含半行距）

NAMED_COLORS = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "green": "#008000",
    "blue": "#0000FF", "gray": "#808080", "grey": "#808080", "orange": "#FFA500",
    "yellow": "#FFFF00", "purple": "#800080", "brown": "#A52A2A", "pink": "#FFC0CB",
    "none": None, "transparent": None,
}

INHERITED_STYLE_KEYS = (
    "fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity",
    "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "opacity",
    "font-size", "font-family", "font-style", "font-weight", "text-anchor",
)


# ---------------------------------------------------------------- 样式与颜色


def _parse_style_attr(style: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if style:
        for item in style.split(";"):
            if ":" in item:
                key, value = item.split(":", 1)
                result[key.strip()] = value.strip()
    return result


def _element_style(element: ET.Element, parent_style: dict[str, str]) -> dict[str, str]:
    style = dict(parent_style)
    inline = _parse_style_attr(element.get("style"))
    for key in INHERITED_STYLE_KEYS:
        if element.get(key) is not None:
            style[key] = element.get(key)  # presentation attribute
        if key in inline:
            style[key] = inline[key]
    for key in ("fill-rule", "stroke-linecap", "stroke-linejoin"):
        if key in inline:
            style[key] = inline[key]
    return style


def parse_color(value: str | None) -> tuple[str, float] | None:
    """→ (hex, alpha) 或 None（none/未指定）。"""
    if value is None:
        return None
    value = value.strip()
    if value in NAMED_COLORS:
        named = NAMED_COLORS[value]
        return (named, 1.0) if named else None
    if value.startswith("#"):
        hexpart = value[1:]
        if len(hexpart) == 3:
            hexpart = "".join(ch * 2 for ch in hexpart)
        if len(hexpart) == 6:
            return ("#" + hexpart.upper(), 1.0)
        if len(hexpart) == 8:
            return ("#" + hexpart[:6].upper(), int(hexpart[6:], 16) / 255.0)
    match = re.match(r"rgba?\(([^)]*)\)", value)
    if match:
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) >= 3:
            r, g, b = (int(float(p)) for p in parts[:3])
            alpha = float(parts[3]) if len(parts) > 3 else 1.0
            return (f"#{r:02X}{g:02X}{b:02X}", alpha)
    return None


def _opacity(style: dict[str, str], key: str) -> float:
    try:
        return float(style.get(key, "1"))
    except ValueError:
        return 1.0


# ---------------------------------------------------------------- OOXML 辅助


def _px(value: float) -> Emu:
    return Emu(round(value * EMU_PER_PX))


def _insert_before_any(sp_pr, element, successor_tags: tuple[str, ...]) -> None:
    """按 OOXML spPr 子元素顺序插入：fill < ln < effectLst < scene3d < sp3d < extLst。"""
    for tag in successor_tags:
        sibling = sp_pr.find(qn(tag))
        if sibling is not None:
            sibling.addprevious(element)
            return
    sp_pr.append(element)


_FILL_SUCCESSORS = ("a:ln", "a:effectLst", "a:effectDag", "a:scene3d", "a:sp3d", "a:extLst")
_LINE_SUCCESSORS = ("a:effectLst", "a:effectDag", "a:scene3d", "a:sp3d", "a:extLst")


def _set_solid_fill(sp_pr, hex_color: str, alpha: float) -> None:
    for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill"):
        for child in sp_pr.findall(qn(tag)):
            sp_pr.remove(child)
    fill = sp_pr.makeelement(qn("a:solidFill"), {})
    color = sp_pr.makeelement(qn("a:srgbClr"), {"val": hex_color[1:]})
    if alpha < 1.0:
        color.append(sp_pr.makeelement(qn("a:alpha"), {"val": str(round(alpha * 100000))}))
    fill.append(color)
    _insert_before_any(sp_pr, fill, _FILL_SUCCESSORS)


def _set_no_fill(sp_pr) -> None:
    for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill"):
        for child in sp_pr.findall(qn(tag)):
            sp_pr.remove(child)
    _insert_before_any(sp_pr, sp_pr.makeelement(qn("a:noFill"), {}), _FILL_SUCCESSORS)


def _set_gradient_fill(sp_pr, stops: list[tuple[float, str, float]], angle_deg: float) -> None:
    for tag in ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill"):
        for child in sp_pr.findall(qn(tag)):
            sp_pr.remove(child)
    grad = sp_pr.makeelement(qn("a:gradFill"), {})
    gs_lst = grad.makeelement(qn("a:gsLst"), {})
    for pos, hex_color, alpha in stops:
        gs = gs_lst.makeelement(qn("a:gs"), {"pos": str(round(pos * 100000))})
        color = gs_lst.makeelement(qn("a:srgbClr"), {"val": hex_color[1:]})
        if alpha < 1.0:
            color.append(gs_lst.makeelement(qn("a:alpha"), {"val": str(round(alpha * 100000))}))
        gs.append(color)
        gs_lst.append(gs)
    grad.append(gs_lst)
    angle = round(math.degrees(angle_deg) * 60000) % 21600000
    grad.append(grad.makeelement(qn("a:lin"), {"ang": str(angle), "scaled": "1"}))
    _insert_before_any(sp_pr, grad, _FILL_SUCCESSORS)


def _apply_line(sp_pr, style: dict[str, str], default_width: float = 1.0) -> None:
    """按 style 在 spPr 上设置 a:ln（颜色/宽度/虚线/圆角）。无 stroke 则不建 a:ln。"""
    stroke = parse_color(style.get("stroke"))
    if stroke is None:
        return
    hex_color, stroke_alpha = stroke
    alpha = stroke_alpha * _opacity(style, "stroke-opacity") * _opacity(style, "opacity")
    try:
        width = float(style.get("stroke-width", default_width))
    except ValueError:
        width = default_width
    old = sp_pr.find(qn("a:ln"))
    if old is not None:
        sp_pr.remove(old)
    line = sp_pr.makeelement(qn("a:ln"), {"w": str(round(width * EMU_PER_PX))})
    if style.get("stroke-linecap") in ("round", "square"):
        line.set("cap", "rnd" if style["stroke-linecap"] == "round" else "sq")
    fill = line.makeelement(qn("a:solidFill"), {})
    color = line.makeelement(qn("a:srgbClr"), {"val": hex_color[1:]})
    if alpha < 1.0:
        color.append(line.makeelement(qn("a:alpha"), {"val": str(round(alpha * 100000))}))
    fill.append(color)
    line.append(fill)
    dash = _dash_preset(style.get("stroke-dasharray"), width, style.get("stroke-linecap"))
    if dash:
        line.append(line.makeelement(qn("a:prstDash"), {"val": dash}))
    _insert_before_any(sp_pr, line, _LINE_SUCCESSORS)


def _dash_preset(dasharray: str | None, width: float, linecap: str | None) -> str | None:
    if not dasharray or dasharray.strip() in ("none", ""):
        return None
    try:
        parts = [float(p) for p in re.split(r"[\s,]+", dasharray.strip()) if p]
    except ValueError:
        return None
    if not parts or parts[0] <= 0:
        return None
    first = parts[0]
    if first <= width * 1.5:
        # OOXML 无 roundDot/squareDot：圆点用 dot，方点用 sysDot
        return "dot" if linecap == "round" else "sysDot"
    if first <= 4 * width:
        return "sysDash"
    if first <= 8 * width:
        return "dash"
    return "lgDash"


def _disable_shadow(shape) -> None:
    shape.shadow.inherit = False


def _next_shape_id(slide) -> int:
    return slide.shapes._next_shape_id


# ---------------------------------------------------------------- 渐变与 marker 定义


def _collect_defs(tree: ET.Element) -> dict[str, ET.Element]:
    defs: dict[str, ET.Element] = {}
    for element in tree.iter():
        element_id = element.get("id")
        if element_id:
            defs[element_id] = element
    return defs


def _gradient_stops(grad: ET.Element) -> list[tuple[float, str, float]]:
    stops: list[tuple[float, str, float]] = []
    for stop in grad.findall(f"{SVG_NS}stop"):
        style = _parse_style_attr(stop.get("style"))
        offset = stop.get("offset", "0")
        pos = float(offset[:-1]) / 100.0 if offset.endswith("%") else float(offset)
        color_value = stop.get("stop-color") or style.get("stop-color", "#000000")
        parsed = parse_color(color_value) or ("#000000", 1.0)
        opacity = stop.get("stop-opacity") or style.get("stop-opacity")
        alpha = parsed[1] * (float(opacity) if opacity is not None else 1.0)
        stops.append((max(0.0, min(1.0, pos)), parsed[0], alpha))
    return stops


def _gradient_angle(grad: ET.Element) -> float:
    def num(key: str, default: float) -> float:
        raw = grad.get(key)
        if raw is None:
            return default
        return float(raw[:-1]) / 100.0 if raw.endswith("%") else float(raw)

    x1, y1, x2, y2 = num("x1", 0.0), num("y1", 0.0), num("x2", 1.0), num("y2", 0.0)
    return math.atan2(y2 - y1, x2 - x1)


def _resolve_fill_url(style: dict[str, str], defs: dict[str, ET.Element]) -> ET.Element | None:
    fill = style.get("fill", "")
    match = re.match(r"url\(#([^)]+)\)", fill or "")
    if match:
        return defs.get(match.group(1))
    return None


# ---------------------------------------------------------------- 形状发射


class ConvertContext:
    def __init__(self, slide, defs: dict[str, ET.Element], source_png: Path | None, canvas_area: float):
        self.slide = slide
        self.defs = defs
        self.source_png = source_png
        self.canvas_area = canvas_area
        self.warnings: list[str] = []
        self.counts: dict[str, int] = {}

    def bump(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _apply_fill_and_line(sp_pr, style: dict[str, str], ctx: ConvertContext) -> None:
    grad = _resolve_fill_url(style, ctx.defs)
    if grad is not None and grad.tag == f"{SVG_NS}linearGradient":
        stops = _gradient_stops(grad)
        if stops:
            _set_gradient_fill(sp_pr, stops, _gradient_angle(grad))
        else:
            _set_no_fill(sp_pr)
    else:
        if grad is not None:
            ctx.warn(f"暂不支持的渐变类型: {grad.tag}，按无填充处理")
        fill = parse_color(style.get("fill", "#000000"))
        if fill is None:
            _set_no_fill(sp_pr)
        else:
            hex_color, fill_alpha = fill
            alpha = fill_alpha * _opacity(style, "fill-opacity") * _opacity(style, "opacity")
            _set_solid_fill(sp_pr, hex_color, alpha)
    _apply_line(sp_pr, style)


def _emit_rect(ctx: ConvertContext, el: ET.Element, style: dict[str, str], matrix: Matrix) -> None:
    x = float(el.get("x", 0))
    y = float(el.get("y", 0))
    w = float(el.get("width", 0))
    h = float(el.get("height", 0))
    if w <= 0 or h <= 0:
        return
    element_id = el.get("id", "")
    if element_id.startswith("atomic:"):
        _emit_atomic(ctx, element_id, x, y, w, h, matrix)
        return
    if not matrix.is_axis_aligned():
        segments = [("M", *matrix.apply(x, y)), ("L", *matrix.apply(x + w, y)),
                    ("L", *matrix.apply(x + w, y + h)), ("L", *matrix.apply(x, y + h)), ("Z",)]
        _emit_freeform(ctx, segments, style)
        return
    (x0, y0), (x1, y1) = matrix.apply(x, y), matrix.apply(x + w, y + h)
    rx = float(el.get("rx", el.get("ry", 0)) or 0)
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rx > 0 else MSO_SHAPE.RECTANGLE
    shape = ctx.slide.shapes.add_shape(kind, _px(min(x0, x1)), _px(min(y0, y1)), _px(abs(x1 - x0)), _px(abs(y1 - y0)))
    if rx > 0:
        try:
            shape.adjustments[0] = min(0.5, rx / min(w, h))
        except (IndexError, AttributeError):
            pass
    _disable_shadow(shape)
    _apply_fill_and_line(shape._element.spPr, style, ctx)
    ctx.bump("rect")


def _emit_ellipse(ctx: ConvertContext, el: ET.Element, style: dict[str, str], matrix: Matrix) -> None:
    if el.tag == f"{SVG_NS}circle":
        cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
        rx = ry = float(el.get("r", 0))
    else:
        cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
        rx, ry = float(el.get("rx", 0)), float(el.get("ry", 0))
    if rx <= 0 or ry <= 0:
        return
    x0, y0 = matrix.apply(cx - rx, cy - ry)
    x1, y1 = matrix.apply(cx + rx, cy + ry)
    shape = ctx.slide.shapes.add_shape(
        MSO_SHAPE.OVAL, _px(min(x0, x1)), _px(min(y0, y1)), _px(abs(x1 - x0)), _px(abs(y1 - y0))
    )
    _disable_shadow(shape)
    _apply_fill_and_line(shape._element.spPr, style, ctx)
    ctx.bump("ellipse")


def _emit_line(ctx: ConvertContext, el: ET.Element, style: dict[str, str], matrix: Matrix) -> None:
    x1, y1 = matrix.apply(float(el.get("x1", 0)), float(el.get("y1", 0)))
    x2, y2 = matrix.apply(float(el.get("x2", 0)), float(el.get("y2", 0)))
    connector = ctx.slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, _px(x1), _px(y1), _px(x2), _px(y2)
    )
    _disable_shadow(connector)
    _apply_line(connector._element.spPr, style)
    ctx.bump("line")
    _emit_markers(ctx, el, style, [(x1, y1), (x2, y2)])


def _emit_poly(ctx: ConvertContext, el: ET.Element, style: dict[str, str], matrix: Matrix, close: bool) -> None:
    points = [float(p) for p in re.split(r"[\s,]+", (el.get("points") or "").strip()) if p]
    if len(points) < 4:
        return
    segments: list[tuple] = []
    first = matrix.apply(points[0], points[1])
    segments.append(("M", *first))
    for i in range(2, len(points) - 1, 2):
        segments.append(("L", *matrix.apply(points[i], points[i + 1])))
    if close:
        segments.append(("Z",))
    _emit_freeform(ctx, segments, style)
    _emit_markers(ctx, el, style, [matrix.apply(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)])


def _emit_path(ctx: ConvertContext, el: ET.Element, style: dict[str, str], matrix: Matrix) -> None:
    d = el.get("d")
    if not d:
        return
    segments = parse_path_d(d)
    if matrix != Matrix():
        segments = _transform_segments(segments, matrix)
    _emit_freeform(ctx, segments, style)
    _emit_markers(ctx, el, style, _segment_vertices(segments))


def _transform_segments(segments: list[tuple], matrix: Matrix) -> list[tuple]:
    result: list[tuple] = []
    for seg in segments:
        if seg[0] == "M" or seg[0] == "L":
            result.append((seg[0], *matrix.apply(seg[1], seg[2])))
        elif seg[0] == "C":
            p1 = matrix.apply(seg[1], seg[2])
            p2 = matrix.apply(seg[3], seg[4])
            p3 = matrix.apply(seg[5], seg[6])
            result.append(("C", p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]))
        else:
            result.append(seg)
    return result


def _segment_vertices(segments: list[tuple]) -> list[tuple[float, float]]:
    vertices: list[tuple[float, float]] = []
    for seg in segments:
        if seg[0] in ("M", "L"):
            vertices.append((seg[1], seg[2]))
        elif seg[0] == "C":
            vertices.append((seg[5], seg[6]))
    return vertices


def _emit_freeform(ctx: ConvertContext, segments: list[tuple], style: dict[str, str]) -> None:
    # bbox 必须包含贝塞尔控制点：控制多边形永远包住曲线本体，
    # 否则坐标越出声明的 path w/h，PowerPoint 会判定文件损坏。
    xs: list[float] = []
    ys: list[float] = []
    for s in segments:
        if s[0] in ("M", "L"):
            xs.append(s[1])
            ys.append(s[2])
        elif s[0] == "C":
            xs.extend((s[1], s[3], s[5]))
            ys.extend((s[2], s[4], s[6]))
    if not xs:
        return
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max(max_x - min_x, 0.01)
    h = max(max_y - min_y, 0.01)

    def rel(x: float, y: float) -> tuple[int, int]:
        return (round((x - min_x) * EMU_PER_PX), round((y - min_y) * EMU_PER_PX))

    path_xml = [f'<a:path w="{round(w * EMU_PER_PX)}" h="{round(h * EMU_PER_PX)}">']
    for seg in segments:
        if seg[0] == "M":
            x, y = rel(seg[1], seg[2])
            path_xml.append(f'<a:moveTo><a:pt x="{x}" y="{y}"/></a:moveTo>')
        elif seg[0] == "L":
            x, y = rel(seg[1], seg[2])
            path_xml.append(f'<a:lnTo><a:pt x="{x}" y="{y}"/></a:lnTo>')
        elif seg[0] == "C":
            x1, y1 = rel(seg[1], seg[2])
            x2, y2 = rel(seg[3], seg[4])
            x3, y3 = rel(seg[5], seg[6])
            path_xml.append(
                f'<a:cubicBezTo><a:pt x="{x1}" y="{y1}"/><a:pt x="{x2}" y="{y2}"/>'
                f'<a:pt x="{x3}" y="{y3}"/></a:cubicBezTo>'
            )
        elif seg[0] == "Z":
            path_xml.append("<a:close/>")
    path_xml.append("</a:path>")

    shape_id = _next_shape_id(ctx.slide)
    xml = (
        '<p:sp xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<p:nvSpPr><p:cNvPr id="{shape_id}" name="freeform-{shape_id}"/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        "<p:spPr>"
        f'<a:xfrm><a:off x="{round(min_x * EMU_PER_PX)}" y="{round(min_y * EMU_PER_PX)}"/>'
        f'<a:ext cx="{round(w * EMU_PER_PX)}" cy="{round(h * EMU_PER_PX)}"/></a:xfrm>'
        "<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
        '<a:rect l="0" t="0" r="0" b="0"/>'
        f"<a:pathLst>{''.join(path_xml)}</a:pathLst></a:custGeom>"
        "</p:spPr></p:sp>"
    )
    sp = _parse_sp_xml(xml)
    ctx.slide.shapes._spTree.append(sp)
    sp_pr = sp.find(qn("p:spPr"))
    _apply_fill_and_line(sp_pr, style, ctx)
    ctx.bump("freeform")


def _parse_sp_xml(xml: str):
    from lxml import etree

    return etree.fromstring(xml.encode("utf-8"))


def _emit_markers(
    ctx: ConvertContext,
    el: ET.Element,
    style: dict[str, str],
    vertices: list[tuple[float, float]],
) -> None:
    if len(vertices) < 2:
        return
    for attr, point_index, neighbor_index in (
        ("marker-start", 0, 1),
        ("marker-end", len(vertices) - 1, len(vertices) - 2),
    ):
        ref = el.get(attr) or _parse_style_attr(el.get("style")).get(attr)
        match = re.match(r"url\(#([^)]+)\)", ref or "")
        if not match:
            continue
        marker = ctx.defs.get(match.group(1))
        if marker is None:
            continue
        px, py = vertices[point_index]
        nx, ny = vertices[neighbor_index]
        angle = math.atan2(py - ny, px - nx)
        _draw_marker(ctx, marker, px, py, angle, style)


def _draw_marker(
    ctx: ConvertContext, marker: ET.Element, x: float, y: float, angle: float, line_style: dict[str, str]
) -> None:
    ref_x = float(marker.get("refX", 0))
    ref_y = float(marker.get("refY", 0))
    placement = (
        Matrix(e=x, f=y)
        .multiply(Matrix(a=math.cos(angle), b=math.sin(angle), c=-math.sin(angle), d=math.cos(angle)))
        .multiply(Matrix(e=-ref_x, f=-ref_y))
    )
    for child in marker:
        if child.tag != f"{SVG_NS}path":
            continue
        marker_style = _element_style(child, {})
        segments = parse_path_d(child.get("d", ""))
        segments = _transform_segments(segments, placement)
        _emit_freeform(ctx, segments, marker_style)
    ctx.bump("marker")


def _emit_atomic(ctx: ConvertContext, element_id: str, x: float, y: float, w: float, h: float, matrix: Matrix) -> None:
    if ctx.source_png is None:
        ctx.warn(f"{element_id}: 缺少参考图，无法裁剪嵌入")
        return
    from PIL import Image

    x0, y0 = matrix.apply(x, y)
    x1, y1 = matrix.apply(x + w, y + h)
    left, top = round(min(x0, x1)), round(min(y0, y1))
    right, bottom = round(max(x0, x1)), round(max(y0, y1))
    with Image.open(ctx.source_png) as image:
        crop = image.crop((left, top, right, bottom))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
        buffer.seek(0)
    ctx.slide.shapes.add_picture(buffer, _px(left), _px(top), _px(right - left), _px(bottom - top))
    ctx.bump("atomic")


IMAGE_MAX_AREA_RATIO = 0.5  # 护栏：<image> 覆盖画布 ≥ 该比例即拒绝（防整图截图冒充矢量交付）


def _emit_image(ctx: ConvertContext, el: ET.Element, matrix: Matrix) -> None:
    """<image> 合同容错：不读内嵌数据，按 bbox 从参考图裁剪替代（等价 atomic 占位符）。"""
    x = float(el.get("x", 0))
    y = float(el.get("y", 0))
    w = float(el.get("width", 0))
    h = float(el.get("height", 0))
    if w <= 0 or h <= 0:
        ctx.warn("<image> 缺少有效 x/y/width/height，已跳过")
        return
    ratio = w * h / ctx.canvas_area
    if ratio >= IMAGE_MAX_AREA_RATIO:
        raise common.fail(
            f"<image> 覆盖画布 {ratio:.0%}（≥{IMAGE_MAX_AREA_RATIO:.0%}），疑似整图截图冒充矢量，已拒绝"
        )
    ctx.warn('<image> 已按 bbox 从参考图裁剪替代（建议改用 <rect id="atomic:*"> 占位符）')
    _emit_atomic(ctx, "<image>", x, y, w, h, matrix)


# ---------------------------------------------------------------- 文本

_CHAR_WIDTHS = {
    " ": 0.28, "i": 0.28, "l": 0.28, "j": 0.3, "t": 0.33, "f": 0.33, "r": 0.36,
    "m": 0.85, "w": 0.8, "I": 0.3, "M": 0.85, "W": 0.9,
    ".": 0.3, ",": 0.3, ":": 0.33, ";": 0.33, "'": 0.25, "(": 0.36, ")": 0.36,
    "-": 0.36, "…": 0.8, "τ": 0.5, "π": 0.55, "ƒ": 0.4,
}


def _estimate_width(text: str, font_size: float) -> float:
    total = 0.0
    for ch in text:
        if ch in _CHAR_WIDTHS:
            total += _CHAR_WIDTHS[ch]
        elif ch.isupper():
            total += 0.67
        elif ch.isdigit():
            total += 0.56
        elif ord(ch) >= 0x2E80:
            total += 1.0
        else:
            total += 0.52
    return total * font_size


def _collect_text_runs(el: ET.Element, base_style: dict[str, str]) -> list[dict]:
    """把 <text> 的文字与 <tspan> 子节点拍平为 run 序列。"""
    runs: list[dict] = []

    def add_run(text: str, style: dict[str, str], shift: str | None) -> None:
        if text:
            runs.append({"text": text, "style": dict(style), "shift": shift})

    if el.text:
        add_run(el.text, base_style, None)
    for tspan in el:
        if tspan.tag != f"{SVG_NS}tspan":
            continue
        style = dict(base_style)
        inline = _parse_style_attr(tspan.get("style"))
        for key in ("font-size", "font-family", "font-style", "font-weight", "fill"):
            if tspan.get(key) is not None:
                style[key] = tspan.get(key)
            if key in inline:
                style[key] = inline[key]
        shift = tspan.get("baseline-shift") or inline.get("baseline-shift")
        if tspan.text:
            add_run(tspan.text, style, shift)
        if tspan.tail:
            add_run(tspan.tail, base_style, None)
    return runs


def _emit_text(ctx: ConvertContext, el: ET.Element, style: dict[str, str], matrix: Matrix) -> None:
    x = float(el.get("x", 0))
    y = float(el.get("y", 0))
    try:
        font_size = float(style.get("font-size", "16"))
    except ValueError:
        font_size = 16.0
    runs = _collect_text_runs(el, style)
    full_text = "".join(r["text"] for r in runs)
    if not full_text.strip():
        return

    anchor = style.get("text-anchor", "start")
    est_w = max(_estimate_width(full_text, font_size), 1.0)
    box_h = font_size * 1.25

    rotation_deg = 0.0
    rot_match = re.fullmatch(r"rotate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)[\s,]+(-?[\d.]+)\s*\)", el.get("transform", "").strip())
    if rot_match:
        rotation_deg = float(rot_match.group(1))
        cx, cy = float(rot_match.group(2)), float(rot_match.group(3))
        cx, cy = matrix.apply(cx, cy)
        left, top = cx - est_w / 2, cy - box_h / 2
        align = PP_ALIGN.CENTER
        vertical = MSO_ANCHOR.MIDDLE
    else:
        x, y = matrix.apply(x, y)
        if anchor == "middle":
            left, align = x - est_w / 2, PP_ALIGN.CENTER
        elif anchor == "end":
            left, align = x - est_w, PP_ALIGN.RIGHT
        else:
            left, align = x, PP_ALIGN.LEFT
        top = y - font_size * BASELINE_ASCENT
        vertical = MSO_ANCHOR.TOP

    textbox = ctx.slide.shapes.add_textbox(_px(left), _px(top), _px(est_w), _px(box_h))
    _disable_shadow(textbox)
    if rotation_deg:
        textbox.rotation = rotation_deg
    frame = textbox.text_frame
    frame.word_wrap = False
    frame.vertical_anchor = vertical
    for margin in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(frame, margin, 0)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = align

    for run_info in runs:
        run = paragraph.add_run()
        run.text = run_info["text"]
        rstyle = run_info["style"]
        try:
            size = float(rstyle.get("font-size", font_size))
        except ValueError:
            size = font_size
        run.font.size = Pt(size * PT_PER_PX)
        family = (rstyle.get("font-family") or "Arial").split(",")[0].strip()
        run.font.name = family
        if str(rstyle.get("font-style", "")).lower() in ("italic", "oblique"):
            run.font.italic = True
        if str(rstyle.get("font-weight", "")).lower() in ("bold", "600", "700", "800", "900"):
            run.font.bold = True
        color = parse_color(rstyle.get("fill", "#000000"))
        if color:
            run.font.color.rgb = RGBColor.from_string(color[0][1:])
        if run_info["shift"] in ("super", "sup"):
            run._r.get_or_add_rPr().set("baseline", "30000")
        elif run_info["shift"] == "sub":
            run._r.get_or_add_rPr().set("baseline", "-25000")
    ctx.bump("text")


# ---------------------------------------------------------------- 树遍历与主流程


def _walk(ctx: ConvertContext, element: ET.Element, style: dict[str, str], matrix: Matrix) -> None:
    tag = element.tag
    if tag in (f"{SVG_NS}defs", f"{SVG_NS}marker", f"{SVG_NS}linearGradient", f"{SVG_NS}radialGradient"):
        return
    own_style = _element_style(element, style)
    own_matrix = matrix.multiply(parse_transform(element.get("transform")))
    if tag == f"{SVG_NS}svg" or tag == f"{SVG_NS}g":
        for child in element:
            _walk(ctx, child, own_style, own_matrix)
    elif tag == f"{SVG_NS}rect":
        _emit_rect(ctx, element, own_style, own_matrix)
    elif tag in (f"{SVG_NS}circle", f"{SVG_NS}ellipse"):
        _emit_ellipse(ctx, element, own_style, own_matrix)
    elif tag == f"{SVG_NS}line":
        _emit_line(ctx, element, own_style, own_matrix)
    elif tag == f"{SVG_NS}polyline":
        _emit_poly(ctx, element, own_style, own_matrix, close=False)
    elif tag == f"{SVG_NS}polygon":
        _emit_poly(ctx, element, own_style, own_matrix, close=True)
    elif tag == f"{SVG_NS}path":
        _emit_path(ctx, element, own_style, own_matrix)
    elif tag == f"{SVG_NS}text":
        _emit_text(ctx, element, own_style, own_matrix)
    elif tag == f"{SVG_NS}image":
        _emit_image(ctx, element, own_matrix)
    else:
        ctx.warn(f"未知元素已跳过: {tag}")


def convert(run: common.Run) -> dict:
    if not run.redraw_svg.is_file():
        raise common.fail(f"未找到 SVG: {run.redraw_svg}（先把 GPT 输出保存到这里）")
    meta = run.load_meta()
    width, height = int(meta["width"]), int(meta["height"])

    tree = ET.parse(run.redraw_svg)
    root = tree.getroot()
    view_box = root.get("viewBox", "")
    if view_box:
        parts = [float(p) for p in re.split(r"[\s,]+", view_box.strip())]
        if len(parts) == 4 and (round(parts[2]) != width or round(parts[3]) != height):
            raise common.fail(
                f"SVG viewBox {parts[2]}x{parts[3]} 与参考图 {width}x{height} 不一致，违反输出合同"
            )

    prs = Presentation()
    prs.slide_width = _px(width)
    prs.slide_height = _px(height)
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    defs = _collect_defs(root)
    source_png = run.source_png if run.source_png.is_file() else None
    ctx = ConvertContext(slide, defs, source_png, float(width * height))
    _walk(ctx, root, {}, Matrix())

    run.qa_dir.mkdir(exist_ok=True)
    prs.save(run.pptx_path)

    # 读回统计（机械验收的基础）
    reopened = Presentation(run.pptx_path)
    readback_texts = 0
    for shape in reopened.slides[0].shapes:
        if shape.has_text_frame and shape.text_frame.text.strip():
            readback_texts += 1
    summary = {
        "svg": str(run.redraw_svg),
        "pptx": str(run.pptx_path),
        "slide_count": len(reopened.slides._sldIdLst),
        "shape_count": len(reopened.slides[0].shapes),
        "textbox_with_text": readback_texts,
        "emitted": ctx.counts,
        "warnings": ctx.warnings,
    }
    (run.qa_dir / "convert-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure convert", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--no-render", action="store_true", help="跳过 PowerPoint fresh render")
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    summary = convert(run)
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if summary["warnings"]:
        sys.stdout.write(f"注意：{len(summary['warnings'])} 条 warning，详见 convert-summary.json\n")
    sys.stdout.write(f"PPTX 已生成: {run.pptx_path}\n")

    if not args.no_render:
        from tools.v2 import render_export

        meta = run.load_meta()
        render_export.render(run.pptx_path, run.render_png, int(meta["width"]), int(meta["height"]))
        sys.stdout.write(f"fresh render: {run.render_png}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
