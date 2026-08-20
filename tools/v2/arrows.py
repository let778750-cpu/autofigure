"""autofigure arrows — 箭头结构审计与确定性几何修复（advisory，非门禁）。

为什么需要它：箭头缺陷（头线脱开 / 偏轴 / 比例失调）在像素指标上不可分辨——
一支箭头 ≈ 画布 0.04%，修好修坏 mean 只动 ~0.06（噪声级），OCR 文本比对也不覆盖。
该缺陷类对整个反馈回路不可见，本模块给 check 补上结构层面的"眼睛"。

审计口径与 convert 的放置语义镜像（canvas(p) = v + R(θ)·(p − ref)）：
- F1 锚点未对齐尖端：marker refX/refY ≠ 三角尖端局部坐标（convert 忠实复刻，
  尖端越出端点 tipX−refX px，底边沉入 refX px）
- F2 头/线宽比例失调：head_len / stroke-width 超出 [RATIO_MIN, RATIO_MAX] 带
- F3 端点悬空：箭头线端点距最近形状边缘 > DOCK_TOL px（合同要求落在形状边缘/间隙）
- W4 orient 非 auto：convert 忽略该属性值，按 auto 处理，记 warning
- feather 手折箭羽：无 marker 的手绘箭头（主干 + 短线束箭羽，03 案例模式），只报告不修复

--fix 确定性修复，只动几何不动任何样式（颜色 / 填充 / 线宽一律不变）：
- refX/refY 对齐三角尖端（尖端恰好落在端点上）
- --clamp-ratio：头长超比例带时等比缩放 marker（按使用方的中位线宽定目标）
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.v2 import common
from tools.v2.convert import (
    SVG_NS,
    _chord,
    _element_style,
    _end_tangent,
    _parse_style_attr,
    _segment_vertices,
    _start_tangent,
)
from tools.v2.svggeom import parse_path_d

RATIO_MIN, RATIO_MAX = 1.5, 4.0
REF_TOL = 0.5          # refX/refY 与尖端局部坐标容差（px）
DOCK_TOL = 6.0         # 端点距最近形状边缘的悬空阈值（px）
FEATHER_LEN_MAX = 45.0  # 箭羽最大长度（px）
FEATHER_RADIUS = 18.0   # 箭羽端点距主杆端点的聚簇半径（px）
FEATHER_ANGLE_MIN, FEATHER_ANGLE_MAX = 20.0, 75.0  # 箭羽与主杆方向夹角带（°）
ARROW_TAGS = (f"{SVG_NS}line", f"{SVG_NS}path", f"{SVG_NS}polyline", f"{SVG_NS}polygon")


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


# ---------------------------------------------------------------- 审计


def audit_svg_text(svg_text: str) -> dict:
    root = ET.fromstring(svg_text)
    defs = _marker_defs(root)
    geometry = {mid: _marker_geometry(el) for mid, el in defs.items()}
    records = _arrow_records(root)
    edges = _EdgeIndex(root)

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
            if not RATIO_MIN <= ratio <= RATIO_MAX:
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


def _find_feathers(root: ET.Element) -> list[dict]:
    """手折箭羽检测：无 marker 主杆端点附近 ±20-75° 的短线束（≥2 根）。"""
    bare: list[dict] = []
    for el in root.iter(f"{SVG_NS}line"):
        if _marker_ref(el, "marker-start") or _marker_ref(el, "marker-end"):
            continue
        p1 = (float(el.get("x1", 0)), float(el.get("y1", 0)))
        p2 = (float(el.get("x2", 0)), float(el.get("y2", 0)))
        length = math.dist(p1, p2)
        if length < 1e-6:
            continue
        bare.append({"el": el, "p1": p1, "p2": p2, "len": length})

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


def fix_svg_text(svg_text: str, clamp_ratio: bool = False) -> tuple[str, list[dict]]:
    """确定性几何修复：refX/refY 对齐尖端；可选头长限幅。只动 marker 定义，不动样式。"""
    try:
        from lxml import etree as letree
    except ImportError as exc:  # pragma: no cover - python-pptx 依赖链保证存在
        raise common.fail(f"修复需要 lxml（python-pptx 依赖）: {exc}")

    root = letree.fromstring(svg_text.encode("utf-8"))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    usage: dict[str, list[float]] = {}
    plain_root = ET.fromstring(svg_text)
    for rec in _arrow_records(plain_root):
        for mid in rec["refs"].values():
            if mid:
                usage.setdefault(mid, []).append(rec["sw"])

    fixes: list[dict] = []
    for marker in root.findall(".//svg:marker", ns):
        mid = marker.get("id")
        geo = _marker_geometry(_find_plain_marker(plain_root, mid))
        if geo is None:
            continue
        ref_x, ref_y = geo["refX"], geo["refY"]
        tip_x, tip_y = geo["tip"]
        scale = 1.0
        if clamp_ratio:
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
                 "（几何归一，不改样式），头长限幅加 --clamp-ratio，改后需重跑 convert/math/check。")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure arrows", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--fix", action="store_true", help="确定性修复（refX/refY 对齐尖端；不改样式）")
    parser.add_argument("--clamp-ratio", action="store_true", help="配合 --fix：头长超出比例带时等比限幅")
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    if not run.redraw_svg.is_file():
        raise common.fail(f"缺少 redraw.svg: {run.redraw_svg}")
    run.qa_dir.mkdir(exist_ok=True)

    svg_text = run.redraw_svg.read_text(encoding="utf-8")
    before = audit_svg_text(svg_text)
    payload: dict = {"svg": str(run.redraw_svg), "phase": "audit", **before}

    if args.fix:
        new_text, fixes = fix_svg_text(svg_text, clamp_ratio=args.clamp_ratio)
        run.redraw_svg.write_text(new_text, encoding="utf-8")
        after = audit_svg_text(new_text)
        payload = {
            "svg": str(run.redraw_svg),
            "phase": "fix",
            "fixes": fixes,
            "before": {k: before[k] for k in ("counts", "ratio_stats")},
            **{k: after[k] for k in ("arrows", "marker_refs", "marker_defs", "findings", "counts", "ratio_stats")},
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
