"""convert 合同测试：SVG 元素 → 原生 PPTX 对象读回（不需要 PowerPoint）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

from tools.v2 import common
from tools.v2.convert import convert

VALID_DASHES = {
    "solid", "dot", "sysDash", "sysDot", "sysDashDot", "sysDashDotDot",
    "lgDash", "lgDashDot", "lgDashDotDot", "dash", "dashDot",
}


@pytest.fixture()
def run_factory(tmp_path: Path):
    def make(svg: str, size: tuple[int, int] = (200, 100)) -> common.Run:
        source = tmp_path / "ref.png"
        Image.new("RGB", size, (240, 240, 240)).save(source)
        run = common.create_run(source, case="case", cases_root=tmp_path / "examples")
        run.qa_dir.mkdir(exist_ok=True)
        run.redraw_svg.write_text(svg, encoding="utf-8")
        return run

    return make


def _shapes(run: common.Run):
    return list(Presentation(run.pptx_path).slides[0].shapes)


def test_rect_with_radius_fill_and_stroke(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<rect x="10" y="10" width="50" height="30" rx="6" fill="#AABBCC" stroke="#112233" stroke-width="2"/></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    xml = shape._element.xml
    assert 'prst="roundRect"' in xml
    assert 'srgbClr val="AABBCC"' in xml
    assert 'srgbClr val="112233"' in xml
    # spPr 子元素顺序：fill 必须在 effectLst 之前（theme 蓝色回归防护）
    sp_pr = shape._element.spPr
    tags = [child.tag for child in sp_pr]
    assert tags.index(qn("a:solidFill")) < tags.index(qn("a:effectLst"))


def test_linear_gradient_becomes_gradfill(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        "<defs><linearGradient id=\"g\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">"
        '<stop offset="0" stop-color="#FF0000" stop-opacity="0.5"/>'
        '<stop offset="1" stop-color="#0000FF"/></linearGradient></defs>'
        '<rect x="0" y="0" width="100" height="50" fill="url(#g)"/></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    grad = shape._element.spPr.find(qn("a:gradFill"))
    assert grad is not None
    stops = grad.findall(f"{qn('a:gsLst')}/{qn('a:gs')}")
    assert len(stops) == 2
    first = stops[0].find(qn("a:srgbClr"))
    assert first.get("val") == "FF0000"
    assert first.find(qn("a:alpha")) is not None  # stop-opacity 保留
    lin = grad.find(qn("a:lin"))
    assert lin is not None and lin.get("ang") == str(90 * 60000)  # 垂直向下


def test_text_runs_italic_bold_and_baseline_shift(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<text x="20" y="50" font-family="Times New Roman, serif" font-size="20" fill="#111111">'
        '<tspan font-style="italic">z</tspan>'
        '<tspan baseline-shift="super" font-size="14" font-style="italic">τ</tspan>'
        '<tspan baseline-shift="sub" font-size="14" font-style="italic">t+1</tspan></text></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    assert shape.has_text_frame
    runs = shape.text_frame.paragraphs[0].runs
    assert [r.text for r in runs] == ["z", "τ", "t+1"]
    assert all(r.font.italic for r in runs)
    assert runs[0].font.name == "Times New Roman"
    assert runs[1]._r.get_or_add_rPr().get("baseline") == "30000"
    assert runs[2]._r.get_or_add_rPr().get("baseline") == "-25000"


def test_dasharray_maps_to_valid_ooxml_preset(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<path d="M 0 50 C 50 0 150 100 200 50" stroke="#2d5ea8" stroke-width="2.8" fill="none"'
        ' stroke-linecap="round" stroke-dasharray="2 4"/></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    dash = shape._element.spPr.find(f"{qn('a:ln')}/{qn('a:prstDash')}")
    assert dash is not None
    assert dash.get("val") in VALID_DASHES  # roundDot/squareDot 曾致 PowerPoint 判损坏


def test_freeform_bbox_includes_bezier_control_points(run_factory):
    # 控制点 (300,-50) 越出端点 bbox——曾致 PowerPoint 判文件损坏
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<path d="M 0 50 C 100 -200 300 200 199 50" stroke="#000000" fill="none"/></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    sp_pr = shape._element.spPr
    path = sp_pr.find(f"{qn('a:custGeom')}/{qn('a:pathLst')}/{qn('a:path')}")
    assert path is not None
    width = int(path.get("w"))
    height = int(path.get("h"))
    xs = [int(pt.get("x")) for pt in path.iter(qn("a:pt"))]
    ys = [int(pt.get("y")) for pt in path.iter(qn("a:pt"))]
    assert min(xs) >= 0 and max(xs) <= width
    assert min(ys) >= 0 and max(ys) <= height


def test_cubic_preserved_as_cubicbezto(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<path d="M 0 0 C 50 50 100 50 150 0" stroke="#000000" fill="none"/></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    cubic = shape._element.spPr.find(f".//{qn('a:cubicBezTo')}")
    assert cubic is not None


def test_atomic_placeholder_crops_reference(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<rect id="atomic:photo" x="20" y="20" width="40" height="30" '
        'fill="#EEEEEE" stroke="#999999" stroke-dasharray="4 3"/></svg>'
    )
    convert(run)
    (shape,) = _shapes(run)
    assert shape.shape_type == MSO_SHAPE_TYPE.PICTURE
    assert shape.width > 0 and shape.height > 0


def test_marker_drawn_as_freeform(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<defs><marker id="arr" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"'
        ' markerUnits="userSpaceOnUse"><path d="M1,1 L8,5 L1,9" fill="none" stroke="#000000"/></marker></defs>'
        '<line x1="10" y1="50" x2="190" y2="50" stroke="#000000" marker-end="url(#arr)"/></svg>'
    )
    convert(run)
    shapes = _shapes(run)
    assert len(shapes) == 2  # line + marker 箭头
    kinds = {s.shape_type for s in shapes}
    assert MSO_SHAPE_TYPE.FREEFORM in kinds or any("freeform" in s.name for s in shapes)


def _triangle_abs_points(shapes):
    """返回第一个 3 顶点自由三角形在画布上的绝对坐标（px）。"""
    emu = 9525.0
    for shape in shapes:
        sp_pr = shape._element.find(qn("p:spPr"))
        if sp_pr is None:
            continue
        geom = sp_pr.find(qn("a:custGeom"))
        if geom is None:
            continue
        path = geom.find(f"{qn('a:pathLst')}/{qn('a:path')}")
        pts = [(int(pt.get("x")), int(pt.get("y"))) for pt in path.iter(qn("a:pt"))]
        if len(pts) != 3:
            continue
        off = sp_pr.find(f"{qn('a:xfrm')}/{qn('a:off')}")
        ox, oy = int(off.get("x")), int(off.get("y"))
        return [((ox + x) / emu, (oy + y) / emu) for x, y in pts]
    return None


def _tip_and_base_mid(pts):
    cx = sum(p[0] for p in pts) / 3
    cy = sum(p[1] for p in pts) / 3
    tip = max(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    base = [p for p in pts if p is not tip]
    mid = ((base[0][0] + base[1][0]) / 2, (base[0][1] + base[1][1]) / 2)
    return tip, mid


def test_curved_path_marker_end_follows_end_tangent(run_factory):
    # 末端切线 = end − 第二控制点 = (0,-80) 竖直向上；旧实现按弦方向（atan2(-80,160)≈-26.6°）
    # 放置导致三角横甩脱开（01 案例 π/a 圆间曲线箭头 43-47° 偏差的真实根因）
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<defs><marker id="arr" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"'
        ' markerUnits="userSpaceOnUse"><path d="M0,0 L12,6 L0,12 Z" fill="#000000" stroke="none"/></marker></defs>'
        '<path d="M 20 100 C 100 100 180 100 180 20" stroke="#000000" fill="none" marker-end="url(#arr)"/></svg>'
    )
    convert(run)
    pts = _triangle_abs_points(_shapes(run))
    assert pts is not None
    tip, base_mid = _tip_and_base_mid(pts)
    # 头轴应沿切线竖直向上：tip 与底边中心同 x，tip 在上方
    assert abs(tip[0] - base_mid[0]) < 1.0
    assert tip[1] < base_mid[1]
    # 锚点 (refX,refY)=(10,6) 落在端点 (180,20)：base_mid ≈ 端点沿切线下沉 10px
    assert abs(base_mid[0] - 180.0) < 1.0 and abs(base_mid[1] - 30.0) < 1.0
    # 尖端 ≈ 端点上方 2px（tipX 12 − refX 10）
    assert abs(tip[0] - 180.0) < 1.0 and abs(tip[1] - 18.0) < 1.0


def test_marker_start_oriented_along_travel(run_factory):
    # SVG 语义：orient="auto" 在 marker-start 处沿行进方向放置；
    # 定义朝 -x 的起始箭头（尖端 local x=0）→ 尖端应落在起点外侧（左侧）
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<defs><marker id="sarr" markerWidth="12" markerHeight="12" refX="2" refY="6" orient="auto"'
        ' markerUnits="userSpaceOnUse"><path d="M12,0 L0,6 L12,12 Z" fill="#000000" stroke="none"/></marker></defs>'
        '<line x1="10" y1="50" x2="190" y2="50" stroke="#000000" marker-start="url(#sarr)"/></svg>'
    )
    convert(run)
    pts = _triangle_abs_points(_shapes(run))
    assert pts is not None
    tip, base_mid = _tip_and_base_mid(pts)
    # 尖端在底边左侧（朝线外），且尖端 ≈ 起点 (10,50) 左移 2px；底边沉入线内 10px
    assert tip[0] < base_mid[0]
    assert abs(tip[0] - 8.0) < 1.0 and abs(tip[1] - 50.0) < 1.0
    assert abs(base_mid[0] - 20.0) < 1.0 and abs(base_mid[1] - 50.0) < 1.0



def test_viewbox_mismatch_rejected(run_factory, tmp_path: Path):
    source = tmp_path / "ref2.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(source)
    run = common.create_run(source, case="case2", cases_root=tmp_path / "examples2")
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">'
        '<rect x="0" y="0" width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="viewBox"):
        convert(run)


def test_summary_counts_and_readback(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
        '<rect x="0" y="0" width="200" height="100" fill="#FFFFFF"/>'
        '<text x="10" y="50" font-size="16">hello</text></svg>'
    )
    summary = convert(run)
    assert summary["textbox_with_text"] == 1
    assert summary["shape_count"] == 2
    on_disk = json.loads((run.qa_dir / "convert-summary.json").read_text(encoding="utf-8"))
    assert on_disk["shape_count"] == 2


def test_data_uri_image_tolerated_as_atomic_crop(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        ' width="200" height="100" viewBox="0 0 200 100">'
        '<image x="20" y="20" width="40" height="30" preserveAspectRatio="none"'
        ' xlink:href="data:image/png;base64,iVBORw0KGgo="/></svg>'
    )
    summary = convert(run)
    (shape,) = _shapes(run)
    assert shape.shape_type == MSO_SHAPE_TYPE.PICTURE  # 按 bbox 从参考图裁剪，不读内嵌数据
    assert summary["emitted"].get("atomic") == 1
    assert any("<image>" in w for w in summary["warnings"])


def test_oversized_image_rejected_as_canvas_cheat(run_factory):
    run = run_factory(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        ' width="200" height="100" viewBox="0 0 200 100">'
        '<image x="0" y="0" width="200" height="100" xlink:href="data:image/png;base64,iVBORw0KGgo="/></svg>'
    )
    with pytest.raises(SystemExit, match="整图截图"):
        convert(run)
