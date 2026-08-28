"""Deterministic geometric repair of the Case05 external seed (Issue #19).

输入 fixture 的原始 external-seed.svg（字节不可变，SHA-256 见 fixture.json），
输出 external-seed-repaired.svg：一个几何层面通过 schema 4.0 source gate 的
候选载体。修复是纯确定性的：

1. 画布显式缩放到冻结参考坐标系（width/height/viewBox 精确等于 reference），
   原始 2048×1291 内容以非均匀 scale 包裹（横向 2100/2048、纵向 1324/1291，
   非均匀度 <0.02%，对插图不可见）。
2. `<use>` 展开：对 defs 中的可复用图标做深拷贝，按 use 的 x/y/transform 包一层
   `<g transform="translate(...)">`，展开后移除 defs 中不再被引用的图标定义。
3. 样式内联：解析 `<style>` 的类规则，把类属性合并为元素上的 presentation
   attributes，随后删除 class 属性与 <style> 块。
4. 稳定 ID：每个可见 drawable 元素按文档序分配确定性 id（seed-auto-NNNN）。

语义合同六属性（data-source-schema-version / data-case / data-reference-sha256 /
data-object-inventory-sha256 / data-stable-element-ids / data-relations-exhaustive）
依赖建案时的冻结束缚（inventory 哈希），由基准 runner 在案例副本上盖章，不在
本几何修复产物中携带——保证 fixture 哈希与建案解耦。

用法（在仓库根执行）：
    python benchmarks/bootstrap/repair_seed.py            # 生成 fixtures/.../external-seed-repaired.svg
    python benchmarks/bootstrap/repair_seed.py --check    # 校验已有产物与原始 seed 的确定性关系
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "05-sting-autophagy"
ORIGINAL = "external-seed.svg"
REPAIRED = "external-seed-repaired.svg"
TARGET_WIDTH = 2100
TARGET_HEIGHT = 1324

DRAWABLE_TAGS = frozenset(
    {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "image"}
)

# presentation attributes we may inline from CSS class rules.
_CSS_PROPS = (
    "font-family",
    "font-size",
    "font-weight",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-dasharray",
    "dominant-baseline",
    "opacity",
)


def _parse_style_block(text: str) -> dict[str, dict[str, str]]:
    """Parse a minimal `.cls{prop:value;...}` stylesheet into a dict."""

    rules: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector, body = match.group(1).strip(), match.group(2)
        for name in (part.strip() for part in selector.split(",")):
            if not name.startswith("."):
                continue
            props: dict[str, str] = {}
            for declaration in body.split(";"):
                prop, _, value = declaration.partition(":")
                prop, value = prop.strip(), value.strip()
                if prop in _CSS_PROPS and value:
                    props[prop] = value
            rules[name[1:]] = props
    return rules


def repair_seed(raw: bytes) -> bytes:
    """Transform the original seed bytes into a gate-ready SVG document."""

    root = ET.fromstring(raw)

    original_width = float(root.get("width", "0"))
    original_height = float(root.get("height", "0"))
    scale_x = TARGET_WIDTH / original_width
    scale_y = TARGET_HEIGHT / original_height

    # 1) canvas: exact frozen reference coordinate system.
    root.set("width", str(TARGET_WIDTH))
    root.set("height", str(TARGET_HEIGHT))
    root.set("viewBox", f"0 0 {TARGET_WIDTH} {TARGET_HEIGHT}")

    defs = root.find("{http://www.w3.org/2000/svg}defs")
    style_element = None
    if defs is not None:
        style_element = defs.find("{http://www.w3.org/2000/svg}style")

    class_rules: dict[str, dict[str, str]] = {}
    if style_element is not None and style_element.text:
        class_rules = _parse_style_block(style_element.text)

    # 2) collect reusable defs groups for <use> expansion.
    reusable: dict[str, ET.Element] = {}
    if defs is not None:
        for child in defs:
            tag = child.tag.rsplit("}", 1)[-1]
            ref = child.get("id")
            if tag == "g" and ref:
                reusable[ref] = child

    def expand_use(element: ET.Element) -> None:
        for child in list(element):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "use":
                href = child.get(
                    "{http://www.w3.org/1999/xlink}href", child.get("href", "")
                )
                target = reusable.get(href.lstrip("#"))
                replacement = None
                if target is not None:
                    replacement = ET.Element(
                        "{http://www.w3.org/2000/svg}g",
                        {"id": child.get("id", "") or ""},
                    )
                    x = float(child.get("x", "0") or 0)
                    y = float(child.get("y", "0") or 0)
                    transform = child.get("transform", "")
                    translate = f"translate({x:g},{y:g})"
                    merged = (
                        f"{transform} {translate}".strip()
                        if transform
                        else translate
                    )
                    if merged:
                        replacement.set("transform", merged)
                    for attr, value in child.attrib.items():
                        if attr.rsplit("}", 1)[-1] in {"href", "x", "y", "id", "transform"}:
                            continue
                        replacement.set(attr, value)
                    replacement.extend(copy.deepcopy(target))
                if replacement is not None:
                    index = list(element).index(child)
                    element.remove(child)
                    element.insert(index, replacement)
                else:
                    element.remove(child)
            else:
                expand_use(child)

    expand_use(root)

    # 3) inline class styles, then strip class attributes and the <style> block.
    def inline_styles(element: ET.Element) -> None:
        for child in element:
            classes = (child.get("class") or "").split()
            if classes:
                for name in classes:
                    for prop, value in class_rules.get(name, {}).items():
                        child.set(prop, value)
                del child.attrib["class"]
            inline = child.get("style")
            if inline:
                for declaration in inline.split(";"):
                    prop, _, value = declaration.partition(":")
                    prop, value = prop.strip(), value.strip()
                    if prop in _CSS_PROPS and value:
                        child.set(prop, value)
                del child.attrib["style"]
            inline_styles(child)

    inline_styles(root)
    if style_element is not None and defs is not None:
        defs.remove(style_element)
    # drop defs groups that are no longer referenced after expansion.
    if defs is not None:
        serialized = ET.tostring(root, encoding="unicode")
        for child in list(defs):
            ref = child.get("id")
            if (
                ref
                and child.tag.rsplit("}", 1)[-1] == "g"
                and f'#{ref}' not in serialized
            ):
                defs.remove(child)

    # 4) stable deterministic ids for every visible drawable element.
    counter = 0

    def assign_ids(element: ET.Element) -> None:
        nonlocal counter
        for child in element:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in DRAWABLE_TAGS and not child.get("id"):
                counter += 1
                child.set("id", f"seed-auto-{counter:04d}")
            assign_ids(child)

    assign_ids(root)

    # wrap original content in the explicit non-uniform rescale group.
    wrapper = ET.Element(
        "{http://www.w3.org/2000/svg}g",
        {"id": "seed-canvas-rescale", "transform": f"scale({scale_x:.9f},{scale_y:.9f})"},
    )
    for child in list(root):
        root.remove(child)
        wrapper.append(child)
    root.append(wrapper)

    ET.indent(root)
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repair_seed", description=__doc__)
    parser.add_argument("--check", action="store_true", help="校验产物可由原始 seed 确定性重建")
    args = parser.parse_args(argv)

    original_path = FIXTURE_DIR / ORIGINAL
    repaired_path = FIXTURE_DIR / REPAIRED
    expected = repair_seed(original_path.read_bytes())

    if args.check:
        actual = repaired_path.read_bytes() if repaired_path.is_file() else b""
        if actual != expected:
            sys.stderr.write("repair_seed: artifact drift — regenerate with repair_seed.py\n")
            return 1
        sys.stdout.write("repair_seed: artifact matches deterministic rebuild\n")
        return 0

    repaired_path.write_bytes(expected)
    sys.stdout.write(f"repair_seed: wrote {repaired_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
