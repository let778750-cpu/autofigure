"""svggeom 单元测试：path 解析与矩阵。"""

from __future__ import annotations

import math

from tools.svggeom import Matrix, parse_path_d, parse_transform


def test_parse_moveto_lineto_close():
    segs = parse_path_d("M 10 20 L 30 40 Z")
    assert segs == [("M", 10.0, 20.0), ("L", 30.0, 40.0), ("Z",)]


def test_relative_commands_become_absolute():
    segs = parse_path_d("m 10 10 l 5 5 c 1 1 2 2 3 3")
    assert segs[0] == ("M", 10.0, 10.0)
    assert segs[1] == ("L", 15.0, 15.0)
    assert segs[2][0] == "C"
    assert segs[2][5:] == (18.0, 18.0)


def test_implicit_lineto_after_moveto():
    segs = parse_path_d("M 0 0 10 10 20 0")
    assert [s[0] for s in segs] == ["M", "L", "L"]


def test_h_v_normalize_to_l():
    segs = parse_path_d("M 0 0 H 25 V 30")
    assert segs[1] == ("L", 25.0, 0.0)
    assert segs[2] == ("L", 25.0, 30.0)


def test_quadratic_converts_to_cubic_keeping_endpoints():
    segs = parse_path_d("M 0 0 Q 50 100 100 0")
    assert segs[1][0] == "C"
    assert segs[1][5:] == (100.0, 0.0)
    # 控制点应使曲线中点上移（凸向控制点方向）
    assert segs[1][2] > 0 and segs[1][4] > 0


def test_smooth_cubic_reflects_control_point():
    segs = parse_path_d("M 0 0 C 10 0 20 0 30 0 S 60 0 70 0")
    assert segs[2][0] == "C"
    # 第一段第二控制点 (20,0) → 反射后 S 的第一控制点 (40,0)
    assert segs[2][1:3] == (40.0, 0.0)


def test_arc_endpoints_preserved():
    segs = parse_path_d("M 0 0 A 50 50 0 0 1 100 0")
    assert segs[-1][0] == "C"
    assert math.isclose(segs[-1][5], 100.0, abs_tol=1e-9)
    assert math.isclose(segs[-1][6], 0.0, abs_tol=1e-9)


def test_arc_degenerate_zero_radius_becomes_line():
    segs = parse_path_d("M 0 0 A 0 0 0 0 0 50 50")
    assert segs[-1] == ("L", 50.0, 50.0)


def test_arc_large_sweep_splits_into_multiple_cubics():
    segs = parse_path_d("M 0 0 A 50 50 0 1 1 100 0")
    cubics = [s for s in segs if s[0] == "C"]
    assert len(cubics) >= 2


def test_matrix_multiply_translate_scale():
    m = Matrix(e=10, f=20).multiply(Matrix(a=2, d=3))
    assert m.apply(5, 5) == (20.0, 35.0)


def test_parse_transform_rotate_about_center():
    m = parse_transform("rotate(90 100 100)")
    x, y = m.apply(200, 100)
    assert math.isclose(x, 100, abs_tol=1e-6)
    assert math.isclose(y, 200, abs_tol=1e-6)


def test_parse_transform_chain_order():
    m = parse_transform("translate(10,0) scale(2)")
    assert m.apply(1, 0) == (12.0, 0.0)


def test_axis_aligned_detection():
    assert Matrix(a=2, d=2).is_axis_aligned()
    assert not parse_transform("rotate(45)").is_axis_aligned()
