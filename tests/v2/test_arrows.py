"""arrows 合同测试：箭头结构审计与确定性修复（纯离线，不需要 PowerPoint）。"""

from __future__ import annotations

import re

from tools.v2.arrows import audit_svg_text, fix_svg_text, render_report

SVG_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120" viewBox="0 0 200 120">'
    "<defs>{markers}</defs>{body}</svg>"
)


def _svg(markers: str, body: str) -> str:
    return SVG_HEAD.format(markers=markers, body=body)


def _solid_marker(mid: str, size: float, ref_x: float, color: str = "#777777") -> str:
    half = size / 2
    return (
        f'<marker id="{mid}" markerWidth="{size:g}" markerHeight="{size:g}" refX="{ref_x:g}"'
        f' refY="{half:g}" orient="auto" markerUnits="userSpaceOnUse">'
        f'<path d="M0,0 L{size:g},{half:g} L0,{size:g} Z" fill="{color}" stroke="none"/></marker>'
    )


BOX = '<rect x="150" y="40" width="40" height="40" fill="#FFFFFF" stroke="#000000"/>'
START_BOX = '<rect x="0" y="40" width="10" height="40" fill="#FFFFFF" stroke="#000000"/>'


def _codes(audit: dict) -> dict[str, int]:
    return audit["counts"]


def test_good_arrow_reports_nothing():
    svg = _svg(
        _solid_marker("arr", 12, 12),  # refX = 尖端 x = 12
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        + BOX
        + START_BOX,
    )
    audit = audit_svg_text(svg)
    assert audit["arrows"] == 1
    assert _codes(audit) == {}  # 比例 12/4=3 在带内；两端点均落在矩形边缘
    assert render_report(audit)[0].startswith("## 箭头结构审计")


def test_refx_mismatch_flagged_as_f1():
    svg = _svg(
        _solid_marker("arr", 12, 10),  # 尖端 x=12, refX=10 → +2px 偏差
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        + BOX,
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F1"] == 1
    assert audit["findings"][0]["endpoint"] == [146, 60]


def test_head_ratio_out_of_band_flagged_as_f2():
    svg = _svg(
        _solid_marker("arr", 20, 20),
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#777777" stroke-width="2" marker-end="url(#arr)"/>'
        + BOX,
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F2"] == 1  # 20/2 = 10 > 4.0
    assert audit["ratio_stats"]["max"] == 10.0


def test_floating_endpoint_flagged_as_f3():
    svg = _svg(
        _solid_marker("arr", 12, 12),
        '<line x1="10" y1="60" x2="90" y2="60" stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        + BOX,  # 矩形在 x=150，端点 90 悬空
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F3"] == 1


def test_non_auto_orient_flagged_as_w4():
    marker = _solid_marker("arr", 12, 12).replace('orient="auto"', 'orient="45"')
    svg = _svg(
        marker,
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        + BOX,
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["W4"] == 1


def test_hand_drawn_feather_cluster_detected():
    # 主杆 1159,305 → 1159,255（长 50），端点处 3 根 ±20-75° 短箭羽
    body = [
        '<line x1="1159" y1="305" x2="1159" y2="255" stroke="#111" stroke-width="3"/>',
        '<line x1="1159" y1="271" x2="1151" y2="262.1" stroke="#111" stroke-width="3"/>',
        '<line x1="1159" y1="271" x2="1167" y2="262.1" stroke="#111" stroke-width="3"/>',
        '<line x1="1159" y1="278" x2="1153" y2="266" stroke="#111" stroke-width="3"/>',
        # 干扰：远处无关短线（不成簇）
        '<line x1="20" y1="20" x2="35" y2="25" stroke="#111" stroke-width="3"/>',
    ]
    svg = _svg("", "".join(body))
    audit = audit_svg_text(svg)
    assert audit["counts"]["feather"] == 1
    assert audit["findings"][0]["endpoint"] == [1159, 255]


def test_icon_legs_not_mistaken_for_feathers():
    # 十字/电阻类小图标：短线互不成 ±20-75° 簇
    body = [
        '<line x1="100" y1="50" x2="110" y2="50" stroke="#111" stroke-width="2"/>',
        '<line x1="105" y1="45" x2="105" y2="55" stroke="#111" stroke-width="2"/>',
    ]
    svg = _svg("", "".join(body))
    audit = audit_svg_text(svg)
    assert audit["counts"].get("feather", 0) == 0


def test_fix_aligns_refx_and_is_idempotent():
    svg = _svg(
        _solid_marker("arr", 12, 10, color="#EF7D2B"),
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#EF7D2B" stroke-width="4" marker-end="url(#arr)"/>'
        + BOX,
    )
    fixed, fixes = fix_svg_text(svg)
    assert fixes == [{"marker": "arr", "refX": [10, "12"], "refY": [6, "6"]}]
    assert 'refX="12"' in fixed
    # 样式零改动：marker 定义之外逐字节一致
    assert re.sub(r"<marker.*?</marker>", "", svg, flags=re.S) == re.sub(
        r"<marker.*?</marker>", "", fixed, flags=re.S
    )
    assert 'fill="#EF7D2B"' in fixed
    # 幂等
    _, fixes2 = fix_svg_text(fixed)
    assert fixes2 == []
    assert audit_svg_text(fixed)["counts"].get("F1", 0) == 0


def test_fix_clamp_ratio_scales_head():
    svg = _svg(
        _solid_marker("big", 20, 18),
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#777777" stroke-width="2" marker-end="url(#big)"/>'
        + BOX,
    )
    fixed, fixes = fix_svg_text(svg, clamp_ratio=True)
    assert fixes and fixes[0].get("head_scale", 1) < 1  # 头长 20 → ≤ 4.0×中位线宽 2 = 8
    audit = audit_svg_text(fixed)
    assert audit["ratio_stats"]["max"] <= 4.0 + 0.01
    # 颜色保留
    assert 'fill="#777777"' in fixed


def test_calibrate_overrides_band():
    """原图校准：大头部细杆（比例超带）按校准值豁免 F2；偏离校准值才报，fix 缩放到校准值。"""
    svg = _svg(
        _solid_marker("gold", 10, 10, color="#8d6a00"),
        '<line x1="10" y1="60" x2="146" y2="60" stroke="#8d6a00" stroke-width="1.8"'
        ' marker-end="url(#gold)"/>'
        + BOX,
    )
    assert audit_svg_text(svg)["counts"]["F2"] == 1  # 10/1.8=5.6 超带，无校准时必报
    # 校准值=当前头长 → 豁免，且校准表进入审计结果
    calibrated = audit_svg_text(svg, calibrate={"gold": 10.0})
    assert calibrated["counts"].get("F2", 0) == 0
    assert calibrated["calibrate"] == {"gold": 10.0}
    # 校准值 6 → 偏离 4 > ±1 → 报；fix 等比缩放到 6 后归零（颜色保留）
    deviated = audit_svg_text(svg, calibrate={"gold": 6.0})
    assert deviated["counts"]["F2"] == 1
    fixed, fixes = fix_svg_text(svg, calibrate={"gold": 6.0})
    assert fixes[0]["head_scale"] == 0.6
    assert 'fill="#8d6a00"' in fixed
    assert audit_svg_text(fixed, calibrate={"gold": 6.0})["counts"].get("F2", 0) == 0


def test_element_head_length_is_per_arrow_calibration():
    svg = _svg(
        _solid_marker("gold", 10, 10, color="#8d6a00"),
        '<line id="a1" data-head-length="10" x1="10" y1="40" x2="146" y2="40" '
        'stroke="#8d6a00" stroke-width="1.8" marker-end="url(#gold)"/>'
        '<line id="a2" x1="10" y1="80" x2="146" y2="80" '
        'stroke="#8d6a00" stroke-width="1.8" marker-end="url(#gold)"/>'
        + BOX,
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F2"] == 1
    assert audit["calibration_scope"] == {"a1:end": "element"}


def test_owned_ellipsis_does_not_trigger_label_collision():
    svg = _svg(
        _solid_marker("arr", 12, 12),
        '<line id="skip-edge" x1="10" y1="60" x2="146" y2="60" '
        'stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        '<text id="skip-label" data-owner-id="skip-edge" x="70" y="65" '
        'font-size="16">...</text>'
        + BOX,
    )
    assert audit_svg_text(svg)["counts"].get("F6", 0) == 0


def test_case01_regression_baseline():
    """真实案例回归：仓库内交付的 01 必须保持零 F1；人为回退一个 marker 验证审计能抓住。"""
    from pathlib import Path

    svg = Path("examples/svg-seeded/01-modular-agent/redraw.svg").read_text(
        encoding="utf-8"
    )
    current = audit_svg_text(svg)
    assert current["arrows"] >= 40
    assert current["counts"].get("F1", 0) == 0  # 2026-08-21 arrows --fix 后的仓库状态守卫
    # 回退 arr-gray 的 refX（重现修复前的系统性偏差），审计应命中且修复归零
    broken = re.sub(r'(<marker\s+id="arr-gray"[^>]*?)refX="[\d.]+"', r'\1refX="2"', svg, count=1)
    assert broken != svg, "未找到 arr-gray 的 refX 属性"
    assert audit_svg_text(broken)["counts"]["F1"] >= 1
    fixed, _ = fix_svg_text(broken)
    assert audit_svg_text(fixed)["counts"].get("F1", 0) == 0


def test_f3_does_not_use_arrow_own_path_as_boundary():
    svg = _svg(
        _solid_marker("arr", 12, 12),
        '<path d="M10,60 L90,60" stroke="#777777" stroke-width="4" '
        'fill="none" marker-end="url(#arr)"/>',
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F3"] == 1


def test_nested_transform_is_applied_to_arrow_and_target():
    svg = _svg(
        _solid_marker("arr", 12, 12),
        '<g transform="translate(30 0)">'
        '<rect id="target" x="150" y="40" width="40" height="40"/>'
        '<line id="edge" x1="10" y1="60" x2="150" y2="60" '
        'stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        '</g>',
    )
    audit = audit_svg_text(svg)
    assert audit["counts"].get("F3", 0) == 0
    finding = next((item for item in audit["findings"] if item["element"] == "edge"), None)
    assert finding is None


def test_declared_target_identity_cannot_be_satisfied_by_decoy():
    svg = _svg(
        _solid_marker("arr", 12, 12),
        '<rect id="declared" x="150" y="10" width="40" height="20"/>'
        '<rect id="decoy" x="150" y="40" width="40" height="40"/>'
        '<line id="edge" data-target-id="declared" x1="10" y1="60" x2="150" y2="60" '
        'stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>',
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F5"] == 1


def test_per_arrow_calibration_clones_shared_marker_idempotently():
    svg = _svg(
        _solid_marker("shared", 12, 12),
        '<line id="a1" x1="10" y1="40" x2="146" y2="40" stroke="#777777" '
        'stroke-width="4" marker-end="url(#shared)"/>'
        '<line id="a2" x1="10" y1="80" x2="146" y2="80" stroke="#777777" '
        'stroke-width="4" marker-end="url(#shared)"/>'
        '<rect x="150" y="20" width="40" height="80"/>',
    )
    fixed, fixes = fix_svg_text(svg, calibrate={"a1": 6.0})
    assert any(item.get("arrow") == "a1" for item in fixes)
    assert 'id="shared--a1-end"' in fixed
    assert 'id="a2"' in fixed and 'marker-end="url(#shared)"' in fixed
    assert audit_svg_text(fixed, calibrate={"a1": 6.0})["counts"].get("F2", 0) == 0
    fixed_again, fixes_again = fix_svg_text(fixed, calibrate={"a1": 6.0})
    assert fixes_again == []
    assert fixed_again == fixed


def test_reference_path_and_label_collision_are_reported():
    svg = _svg(
        _solid_marker("arr", 12, 12),
        '<rect x="150" y="40" width="40" height="40"/>'
        '<line id="edge" data-reference-d="M10,20 L150,60" x1="10" y1="60" x2="150" y2="60" '
        'stroke="#777777" stroke-width="4" marker-end="url(#arr)"/>'
        '<text id="label" x="70" y="65" font-size="16">collision</text>',
    )
    audit = audit_svg_text(svg)
    assert audit["counts"]["F7"] == 1
    assert audit["counts"]["F6"] == 1
