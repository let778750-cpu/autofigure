"""SVG 路径与几何解析：d 属性 → 线段/三次贝塞尔段序列（含 S/Q/T/A 归一化）。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# 段类型: ("M", x, y) ("L", x, y) ("C", x1, y1, x2, y2, x, y) ("Z",)
Segment = tuple

_TOKEN_RE = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class Matrix:
    """2D 仿射矩阵（SVG matrix(a,b,c,d,e,f)）。"""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.a * x + self.c * y + self.e,
            self.b * x + self.d * y + self.f,
        )

    def multiply(self, other: "Matrix") -> "Matrix":
        """self ∘ other（先应用 other，再应用 self）。"""
        return Matrix(
            self.a * other.a + self.c * other.b,
            self.b * other.a + self.d * other.b,
            self.a * other.c + self.c * other.d,
            self.b * other.c + self.d * other.d,
            self.a * other.e + self.c * other.f + self.e,
            self.b * other.e + self.d * other.f + self.f,
        )

    def is_axis_aligned(self, tolerance: float = 1e-6) -> bool:
        return abs(self.b) < tolerance and abs(self.c) < tolerance


def parse_transform(value: str | None) -> Matrix:
    if not value:
        return Matrix()
    result = Matrix()
    for name, args_text in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", value):
        args = [float(a) for a in re.split(r"[\s,]+", args_text.strip()) if a]
        if name == "matrix" and len(args) == 6:
            m = Matrix(*args)
        elif name == "translate":
            m = Matrix(e=args[0], f=args[1] if len(args) > 1 else 0.0)
        elif name == "scale":
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            m = Matrix(a=sx, d=sy)
        elif name == "rotate":
            angle = math.radians(args[0])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            rot = Matrix(a=cos_a, b=sin_a, c=-sin_a, d=cos_a)
            if len(args) == 3:
                cx, cy = args[1], args[2]
                m = Matrix(e=cx, f=cy).multiply(rot).multiply(Matrix(e=-cx, f=-cy))
            else:
                m = rot
        elif name == "skewX":
            m = Matrix(c=math.tan(math.radians(args[0])))
        else:  # skewY
            m = Matrix(b=math.tan(math.radians(args[0])))
        result = result.multiply(m)
    return result


def parse_path_d(d: str) -> list[Segment]:
    """解析 path d 属性，全部归一化为 M/L/C/Z 绝对坐标段。"""
    tokens = _TOKEN_RE.findall(d)
    segments: list[Segment] = []
    i = 0
    x = y = 0.0  # 当前点
    start_x = start_y = 0.0  # 当前子路径起点
    prev_cubic_ctrl: tuple[float, float] | None = None
    prev_quad_ctrl: tuple[float, float] | None = None
    command = ""

    def read_float() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        token = tokens[i]
        if re.fullmatch(r"[A-Za-z]", token):
            command = token
            i += 1
            if command in "Zz":
                segments.append(("Z",))
                x, y = start_x, start_y
                prev_cubic_ctrl = prev_quad_ctrl = None
            continue
        relative = command.islower()
        cmd = command.upper()

        if cmd == "M":
            x1, y1 = read_float(), read_float()
            if relative:
                x1, y1 = x + x1, y + y1
            x, y = x1, y1
            start_x, start_y = x, y
            segments.append(("M", x, y))
            command = "l" if relative else "L"  # 后续隐式为 L
        elif cmd == "L":
            x1, y1 = read_float(), read_float()
            if relative:
                x1, y1 = x + x1, y + y1
            x, y = x1, y1
            segments.append(("L", x, y))
        elif cmd == "H":
            x1 = read_float()
            x = x + x1 if relative else x1
            segments.append(("L", x, y))
        elif cmd == "V":
            y1 = read_float()
            y = y + y1 if relative else y1
            segments.append(("L", x, y))
        elif cmd == "C":
            x1, y1, x2, y2, x3, y3 = (read_float() for _ in range(6))
            if relative:
                x1, y1, x2, y2, x3, y3 = x + x1, y + y1, x + x2, y + y2, x + x3, y + y3
            segments.append(("C", x1, y1, x2, y2, x3, y3))
            prev_cubic_ctrl = (x2, y2)
            x, y = x3, y3
        elif cmd == "S":
            x2, y2, x3, y3 = (read_float() for _ in range(4))
            if relative:
                x2, y2, x3, y3 = x + x2, y + y2, x + x3, y + y3
            x1, y1 = (2 * x - prev_cubic_ctrl[0], 2 * y - prev_cubic_ctrl[1]) if prev_cubic_ctrl else (x, y)
            segments.append(("C", x1, y1, x2, y2, x3, y3))
            prev_cubic_ctrl = (x2, y2)
            x, y = x3, y3
        elif cmd in ("Q", "T"):
            if cmd == "Q":
                qx, qy, x3, y3 = (read_float() for _ in range(4))
                if relative:
                    qx, qy, x3, y3 = x + qx, y + qy, x + x3, y + y3
            else:
                x3, y3 = read_float(), read_float()
                if relative:
                    x3, y3 = x + x3, y + y3
                qx, qy = (2 * x - prev_quad_ctrl[0], 2 * y - prev_quad_ctrl[1]) if prev_quad_ctrl else (x, y)
            c1 = (x + 2.0 / 3.0 * (qx - x), y + 2.0 / 3.0 * (qy - y))
            c2 = (x3 + 2.0 / 3.0 * (qx - x3), y3 + 2.0 / 3.0 * (qy - y3))
            segments.append(("C", c1[0], c1[1], c2[0], c2[1], x3, y3))
            prev_quad_ctrl = (qx, qy)
            x, y = x3, y3
        elif cmd == "A":
            rx, ry, rotation, large_arc, sweep, x3, y3 = (read_float() for _ in range(7))
            if relative:
                x3, y3 = x + x3, y + y3
            for cubic in _arc_to_cubics(x, y, rx, ry, rotation, int(large_arc), int(sweep), x3, y3):
                segments.append(cubic)
            x, y = x3, y3
        else:
            raise ValueError(f"不支持的 path 命令: {command}")

        if cmd not in ("C", "S"):
            prev_cubic_ctrl = None
        if cmd not in ("Q", "T"):
            prev_quad_ctrl = None
    return segments


def _arc_to_cubics(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    rotation_deg: float,
    large_arc: int,
    sweep: int,
    x2: float,
    y2: float,
) -> list[Segment]:
    """SVG 椭圆弧 → 三次贝塞尔近似（端点参数化 → 中心参数化，每段 ≤90°）。"""
    if rx == 0 or ry == 0:
        return [("L", x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation_deg % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    lam = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    denom = (rx * y1p) ** 2 + (ry * x1p) ** 2
    if denom == 0:
        return [("L", x2, y2)]
    num = max(0.0, (rx * ry) ** 2 - denom)
    sign = -1.0 if large_arc == sweep else 1.0
    coef = sign * math.sqrt(num / denom)
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        length = math.hypot(ux, uy) * math.hypot(vx, vy)
        value = max(-1.0, min(1.0, dot / length))
        result = math.acos(value)
        if ux * vy - uy * vx < 0:
            result = -result
        return result

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle(
        (x1p - cxp) / rx,
        (y1p - cyp) / ry,
        (-x1p - cxp) / rx,
        (-y1p - cyp) / ry,
    )
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    count = max(1, math.ceil(abs(delta) / (math.pi / 2)))
    step = delta / count
    segments: list[Segment] = []
    for index in range(count):
        t1 = theta1 + index * step
        t2 = t1 + step
        alpha = 4.0 / 3.0 * math.tan((t2 - t1) / 4.0)

        def point(t: float) -> tuple[float, float]:
            px, py = rx * math.cos(t), ry * math.sin(t)
            return (
                cos_phi * px - sin_phi * py + cx,
                sin_phi * px + cos_phi * py + cy,
            )

        def derivative(t: float) -> tuple[float, float]:
            px, py = -rx * math.sin(t), ry * math.cos(t)
            return (
                cos_phi * px - sin_phi * py,
                sin_phi * px + cos_phi * py,
            )

        p1, p2 = point(t1), point(t2)
        d1, d2 = derivative(t1), derivative(t2)
        segments.append(
            (
                "C",
                p1[0] + alpha * d1[0],
                p1[1] + alpha * d1[1],
                p2[0] - alpha * d2[0],
                p2[1] - alpha * d2[1],
                p2[0],
                p2[1],
            )
        )
    return segments
