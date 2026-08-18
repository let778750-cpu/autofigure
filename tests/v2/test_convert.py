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
