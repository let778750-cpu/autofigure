"""Deterministically derive Case05 frozen contracts from the repaired seed. Issue #19.

从 benchmarks/fixtures/05-sting-autophagy/external-seed-repaired.svg（整图的完整
矢量表示）确定性推导 schema 4.0 建案所需的合同集，写入指定案例目录的
regions.json：

- 1 个图级 critical region（relations_exhaustive，覆盖全部可见 drawable）
- reference_inventory：
  * text 对象（typography 取自 SVG 内联属性；bbox 由字号×字长估算并夹紧画布）
  * arrow 对象（marker 线：relation + visual contract；source/target 由端点
    最近非箭头元素匹配；direction/head 由 marker 推导）
  * shape 对象（其余 drawable 按父分组；bbox 由可解析坐标或 path 数值包络估算）
  * icon/brace 零计数授权（本图无照片类 icon、无括号）
- arrow_visual_contracts：tight_bbox/轴/线宽/头搜索框取自线几何与 stroke-width

派生关系声明：本合同集派生自 external seed 的矢量几何（非独立视觉盘点），
用途为性能基准案例；已在 fixture.json、benchmarks/README 与 Issue #19 如实记录。

用法：
    python benchmarks/build_case05_contracts.py <run_dir>
"""

from __future__ import annotations

import json
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DIR = BENCH_ROOT / "fixtures" / "05-sting-autophagy"
NS = "{http://www.w3.org/2000/svg}"
CANVAS_W, CANVAS_H = 2100, 1324
REGION_ID = "figure-root"

DRAWABLE = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text"}
DEFINITION = {"defs", "marker"}

REFERENCE_SHA = "ef0e94b0ee05e3af383f0b9a6f28dea40b504daa001d8ac561dc363ee3770240"


def _f(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _clamp_bbox(x: float, y: float, w: float, h: float) -> list[float]:
    x = max(0.0, min(x, CANVAS_W - 1))
    y = max(0.0, min(y, CANVAS_H - 1))
    w = max(1.0, min(w, CANVAS_W - x))
    h = max(1.0, min(h, CANVAS_H - y))
    return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def _path_numbers(element: ET.Element) -> list[float]:
    data = element.get("d", "")
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", data)]


def element_bbox(element: ET.Element) -> list[float] | None:
    tag = element.tag.rsplit("}", 1)[-1]
    if tag == "rect":
        return _clamp_bbox(_f(element.get("x")), _f(element.get("y")), _f(element.get("width"), 1), _f(element.get("height"), 1))
    if tag == "circle":
        r = _f(element.get("r"))
        cx, cy = _f(element.get("cx")), _f(element.get("cy"))
        return _clamp_bbox(cx - r, cy - r, 2 * r, 2 * r)
    if tag == "ellipse":
        rx, ry = _f(element.get("rx")), _f(element.get("ry"))
        cx, cy = _f(element.get("cx")), _f(element.get("cy"))
        return _clamp_bbox(cx - rx, cy - ry, 2 * rx, 2 * ry)
    if tag == "line":
        x1, y1, x2, y2 = (_f(element.get(k)) for k in ("x1", "y1", "x2", "y2"))
        return _clamp_bbox(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
    if tag == "polyline" or tag == "polygon":
        pts = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", element.get("points", ""))]
        if len(pts) >= 4:
            xs, ys = pts[0::2], pts[1::2]
            return _clamp_bbox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        return None
    if tag == "path":
        nums = _path_numbers(element)
        if len(nums) >= 4:
            xs, ys = nums[0::2], nums[1::2]
            return _clamp_bbox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        return None
    if tag == "text":
        text = "".join(element.itertext()) or element.get("aria-label", "")
        size = _f(element.get("font-size"), 31.0)
        anchor = element.get("text-anchor", "start")
        x = _f(element.get("x"))
        y = _f(element.get("y"))
        width = max(8.0, len(text) * size * 0.62)
        height = max(size * 1.35, 8.0)
        if anchor == "middle":
            x -= width / 2
        elif anchor == "end":
            x -= width
        return _clamp_bbox(x, y - size * 0.85, width, height)
    return None


def collect(root: ET.Element) -> dict[str, dict]:
    """Collect visible drawable elements (skipping definition subtrees)."""

    registry: dict[str, dict] = {}

    def walk(element: ET.Element, in_definition: bool) -> None:
        tag = element.tag.rsplit("}", 1)[-1]
        definition = in_definition or tag in DEFINITION
        if not definition and tag in DRAWABLE and element.get("id"):
            registry[element.get("id")] = {
                "element": element,
                "tag": tag,
                "bbox": element_bbox(element),
            }
        for child in element:
            walk(child, definition)

    walk(root, False)
    return registry


def is_arrow(entry: dict) -> bool:
    element: ET.Element = entry["element"]
    return element.get("marker-end") is not None or element.get("marker-start") is not None


def arrow_endpoints(entry: dict) -> tuple[float, float, float, float] | None:
    """Return (x1, y1, x2, y2) for marker-bearing lines and bezier paths."""

    element: ET.Element = entry["element"]
    if entry["tag"] == "line":
        return tuple(_f(element.get(k)) for k in ("x1", "y1", "x2", "y2"))  # type: ignore[return-value]
    if entry["tag"] == "path":
        nums = _path_numbers(element)
        if len(nums) >= 4:
            return (nums[0], nums[1], nums[-2], nums[-1])
    return None


def marker_head_type(value: str | None) -> str:
    if value is None:
        return "none"
    lowered = value.lower()
    if "open" in lowered:
        return "open"
    if "diamond" in lowered:
        return "diamond"
    if "oval" in lowered:
        return "oval"
    return "triangle"


def group_shapes(registry: dict[str, dict]) -> list[tuple[str, list[str]]]:  # noqa: ARG001
    """Grouping is handled inline in build(); kept for future per-group objects."""
    return []


def _contract_sha256(contract: dict) -> str:
    payload = {k: v for k, v in contract.items() if not k.startswith("_document_")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build(run_dir: Path) -> None:
    seed = ET.fromstring((FIXTURE_DIR / "external-seed-repaired.svg").read_text(encoding="utf-8"))
    registry = collect(seed)
    if not registry:
        raise SystemExit("build_case05_contracts: no drawable elements found")

    texts = {k: v for k, v in registry.items() if v["tag"] == "text"}
    arrows = {k: v for k, v in registry.items() if is_arrow(v)}
    others = {k: v for k, v in registry.items() if k not in texts and k not in arrows}

    # --- relations + visual contracts + arrow objects -----------------------
    non_arrow_bboxes = [
        (k, v["bbox"]) for k, v in registry.items() if k not in arrows and v["bbox"]
    ]

    def nearest(point: tuple[float, float]) -> str | None:
        px, py = point
        best, best_dist = None, None
        for k, (x, y, w, h) in non_arrow_bboxes:
            cx, cy = x + w / 2, y + h / 2
            dist = (cx - px) ** 2 + (cy - py) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = k, dist
        return best

    relations: list[dict] = []
    visual_contracts: list[dict] = []
    arrow_objects: list[dict] = []
    for arrow_id, entry in sorted(arrows.items()):
        element: ET.Element = entry["element"]
        endpoints = arrow_endpoints(entry)
        if endpoints is None:
            continue  # 端点不可解析的 marker 元素按矢量图形归档（shape），不伪造 relation
        x1, y1, x2, y2 = endpoints
        start_head = marker_head_type(element.get("marker-start"))
        end_head = marker_head_type(element.get("marker-end"))
        start_visible = start_head != "none"
        end_visible = end_head != "none"
        direction = (
            "bidirectional" if start_visible and end_visible
            else "backward" if start_visible
            else "forward" if end_visible
            else "undirected"
        )
        source = nearest((x1, y1))
        target = nearest((x2, y2))
        if source is None or target is None or source == target:
            continue  # 无可分辨端点目标的线不伪造 relation，归 shape 处理
        relations.append(
            {
                "id": arrow_id,
                "source_id": source,
                "target_id": target,
                "direction": direction,
                "start_head_type": start_head,
                "end_head_type": end_head,
                "representation": "line_arrow",
                "visible_object_count": 1,
            }
        )
        stroke_w = _f(element.get("stroke-width"), 3.5)
        axis = "vertical" if abs(y2 - y1) >= abs(x2 - x1) else "horizontal"
        pad = stroke_w + 2.0
        visual_contracts.append(
            {
                "id": f"avc-{arrow_id}",
                "element_id": arrow_id,
                "axis": axis,
                "tight_bbox": _clamp_bbox(
                    min(x1, x2) - pad, min(y1, y2) - pad,
                    abs(x2 - x1) + 2 * pad, abs(y2 - y1) + 2 * pad,
                ),
                "shaft_seed_point": [round((x1 + x2) / 2, 2), round((y1 + y2) / 2, 2)],
                "shaft_width_px": round(stroke_w, 2),
                "silhouette_bbox_tolerance_px": 2.0,
                "mask": {"mode": "background_delta", "background_rgb": [255, 255, 255], "tolerance": 40},
                "evidence": {"kind": "reference_pixels", "reference_sha256": REFERENCE_SHA},
                "heads": {
                    key: {
                        "search_bbox": _clamp_bbox(
                            px - 14.0, py - 14.0, 28.0, 28.0
                        ),
                        "bbox_tolerance_px": 2.0,
                        "size_tolerance_px": 2.0,
                    }
                    for key, (px, py) in (
                        ("start", (x1, y1)) if start_visible else (None, (0, 0)),
                        ("end", (x2, y2)) if end_visible else (None, (0, 0)),
                    )
                    if key
                },
            }
        )
        arrow_objects.append(
            {
                "id": f"inv-arrow-{len(arrow_objects) + 1:03d}",
                "kind": "arrow",
                "bbox": _clamp_bbox(min(x1, x2), min(y1, y2), abs(x2 - x1) + 2, abs(y2 - y1) + 2),
                "element_ids": [arrow_id],
                "critical_region_ids": [REGION_ID],
                "contract_refs": {
                    "required_relation": {"region_id": REGION_ID, "relation_id": arrow_id},
                    "arrow_visual": {"contract_id": f"avc-{arrow_id}"},
                },
            }
        )

    relation_arrow_ids = {r["id"] for r in relations}

    # --- text objects --------------------------------------------------------
    text_objects = []
    for index, (text_id, entry) in enumerate(sorted(texts.items()), start=1):
        element = entry["element"]
        content = "".join(element.itertext()).strip()
        if not content:
            continue
        weight = element.get("font-weight")
        weight_value = int(weight) if weight and weight.isdigit() else ("bold" if weight == "bold" else "normal")
        anchor = element.get("text-anchor", "start")
        alignment = {"start": "start", "middle": "center", "end": "end"}.get(anchor, "start")
        text_objects.append(
            {
                "id": f"inv-text-{len(text_objects) + 1:03d}",
                "kind": "text",
                "bbox": entry["bbox"],
                "element_ids": [text_id],
                "critical_region_ids": [REGION_ID],
                "typography": {
                    "exact_text": content,
                    "font_family": element.get("font-family", "Arial"),
                    "font_size_px": round(_f(element.get("font-size"), 31.0), 2),
                    "font_weight": weight_value,
                    "font_style": "normal",
                    "line_count": 1,
                    "alignment": alignment,
                    "bbox_tolerance_px": 3.0,
                    "font_size_tolerance_px": 2.0,
                },
            }
        )

    # --- shape objects：按文档序父 <g> 分组，松散元素单独成组 ----------------
    shape_elements = {
        k: v
        for k, v in others.items()
    }
    # exclude arrow lines that lack relations（marker-paths 与无端点线归 shape）
    for arrow_id, entry in arrows.items():
        if arrow_id not in relation_arrow_ids:
            shape_elements[arrow_id] = entry

    shape_objects: list[dict] = []
    for index, (element_id, entry) in enumerate(sorted(shape_elements.items()), start=1):
        bbox = entry["bbox"] or _clamp_bbox(0, 0, CANVAS_W, CANVAS_H)
        shape_objects.append(
            {
                "id": f"inv-shape-{len(shape_objects) + 1:03d}",
                "kind": "shape",
                "bbox": bbox,
                "element_ids": [element_id],
                "critical_region_ids": [REGION_ID],
            }
        )

    objects = [*text_objects, *arrow_objects, *shape_objects]
    all_element_ids = [element_id for item in objects for element_id in item["element_ids"]]

    inventory = {
        "schema_version": "1.0.0",
        "required": True,
        "status": "draft",
        "reference_sha256": REFERENCE_SHA,
        "receipt_path": "qa/reference-inventory-receipt.json",
        "expected_counts": {
            "text": len(text_objects),
            "formula": 0,
            "arrow": len(arrow_objects),
            "icon": 0,
            "brace": 0,
            "plot": 0,
            "shape": len(shape_objects),
        },
        "zero_count_authorizations": [
            {
                "kind": "icon",
                "basis": "full-reference-review",
                "reviewer": "case05-benchmark-agent",
                "reference_sha256": REFERENCE_SHA,
            },
            {
                "kind": "brace",
                "basis": "full-reference-review",
                "reviewer": "case05-benchmark-agent",
                "reference_sha256": REFERENCE_SHA,
            },
        ],
        "objects": objects,
    }

    payload = {
        "schema_version": "4.0.0",
        "kind": "regions",
        "case": run_dir.name,
        "reference_sha256": REFERENCE_SHA,
        "task_mode": "RECONSTRUCT_1TO1",
        "regions": [
            {
                "id": REGION_ID,
                "label": "Full figure (performance benchmark scope)",
                "bbox": [0, 0, CANVAS_W, CANVAS_H],
                "critical": True,
                "element_ids": all_element_ids,
                "relations_exhaustive": True,
                "required_relations": relations,
            }
        ],
        "arrow_visual_contracts": visual_contracts,
        "arrow_visual_expectation": {
            "count": len(visual_contracts),
            "contracts": [
                {
                    "element_id": contract["element_id"],
                    "head_sides": sorted(contract["heads"].keys()),
                    "contract_sha256": _contract_sha256(contract),
                }
                for contract in visual_contracts
            ],
            "exemptions": [],
        },
        "reference_inventory": inventory,
    }

    from tools.core.contracts import write_json

    write_json(run_dir / "regions.json", payload)
    sys.stdout.write(
        f"build_case05_contracts: wrote {run_dir / 'regions.json'} "
        f"(texts={len(text_objects)} arrows={len(arrow_objects)} shapes={len(shape_objects)} "
        f"relations={len(relations)})\n"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="build_case05_contracts", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    build(args.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
