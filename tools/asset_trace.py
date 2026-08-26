"""Deterministic microasset trace eligibility and vtracer trace execution.

This module implements the microasset-level source-authoring helpers from
``docs/ILLUSTRATOR_VECTOR_AUTHORING_PLAN.md`` Phase 1/2:

* :func:`compute_trace_eligibility` classifies an authorized reference crop as
  ``photographic`` / ``flat-illustration`` / ``ambiguous`` from deterministic
  image statistics, so freeze can decide whether the vtracer channel may be
  attempted at all (photos stay on the atomic-raster layer).
* :func:`run_vtracer_trace` executes the locked-parameter vtracer trace and
  mechanically fills a missing ``viewBox`` from the pixel dimensions.
* :func:`check_svg_contract_subset` statically verifies that a traced SVG is
  pure path stacking inside the SVG authoring contract subset.

All functions are case-neutral: they take explicit paths/images and never
reference case IDs or fixed pixel coordinates.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from tools.contracts import TRACE_ELIGIBILITY_VALUES

# 权威枚举归口 tools.contracts.TRACE_ELIGIBILITY_VALUES;此处保留原名 alias
# 供既有调用方 import,两处不得再各自维护字面量。
TRACE_ELIGIBILITY_CLASSES = TRACE_ELIGIBILITY_VALUES

# Locked vtracer parameters, identical to the Phase 0 pilot
# (docs/vtracer-pilot/README.md §1: colormode=color, hierarchical=stacked,
# color_precision=6, path_precision=3, mode spline/polygon).
VTRACER_LOCKED_PARAMETERS = {
    "colormode": "color",
    "hierarchical": "stacked",
    "color_precision": 6,
    "path_precision": 3,
}
VTRACER_DEFAULT_MODE = "spline"

# Eligibility thresholds — 初始值,待 freeze 流程用更多样本校准后冻结。
# 出处:docs/vtracer-pilot/README.md 试点(globe 平面插画 61 色/SSIM 0.81–0.82
# vs observation 照片 94 色+连续调);对两个试点裁剪按本模块定义实测,4 bit 量化
# 唯一色数为 globe 57 与 observation 384,两组之间取整定档。
_FLAT_MAX_UNIQUE_COLORS = 128
_PHOTO_MIN_UNIQUE_COLORS = 256
# 高梯度判据:0–255 灰度上梯度幅值 ≥ 32 视为硬边像素;平面插画必有清晰色块边界
# (试点 globe 高梯度占比 0.348,observation 0.267;合成纯色块图 ≥0.11,平滑渐变 ≈0)。
_HIGH_GRADIENT_MAGNITUDE = 32.0
_FLAT_MIN_HIGH_GRADIENT_FRACTION = 0.05
# 局部方差窗口(像素),与 gradient/local-variance 统计同作 freeze 校准输入。
_LOCAL_VARIANCE_WINDOW = 5

# 合同子集校验:描摹产物必须是纯 path 堆叠(docs/vtracer-pilot/README.md §2:
# 无 <image>、无 mask、无渐变、无文字转路径、无 mesh)。允许元素白名单之外的
# 任何标签(含 image/mask/meshgradient/text/script/foreignObject/use/动画与
# 滤镜原语等)一律拒绝;blend mode、mask、filter 以属性/样式形式出现同样拒绝。
_ALLOWED_ELEMENTS = frozenset({"svg", "g", "path"})
_FORBIDDEN_ATTRIBUTES = frozenset({"mask", "filter"})
_FORBIDDEN_STYLE_TOKENS = ("mix-blend-mode", "blend-mode", "mask", "filter")

_DIMENSION_RE = re.compile(r"\s*([-+]?(?:\d*\.\d+|\d+\.?))(?:px)?\s*")
_ROOT_TAG_RE = re.compile(r"<svg\b[^>]*>")


class AssetTraceError(ValueError):
    """Raised when trace inputs or outputs cannot satisfy the contract."""

    def __init__(self, errors: list[str] | tuple[str, ...] | str):
        values = [errors] if isinstance(errors, str) else list(errors)
        self.errors = tuple(dict.fromkeys(str(value) for value in values))
        super().__init__("; ".join(self.errors))


def _load_rgb(image: str | Path | Image.Image) -> np.ndarray:
    try:
        if isinstance(image, Image.Image):
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
        with Image.open(image) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except (OSError, TypeError, ValueError) as exc:
        raise AssetTraceError(f"asset-trace:image-unreadable:{exc}") from exc


def _gray(rgb: np.ndarray) -> np.ndarray:
    return np.asarray(Image.fromarray(rgb, "RGB").convert("L"), dtype=np.float32)


def _gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    axes = [
        np.gradient(gray, axis=axis) if gray.shape[axis] >= 2 else np.zeros_like(gray)
        for axis in (0, 1)
    ]
    return np.hypot(axes[0], axes[1])


def compute_trace_eligibility(image: str | Path | Image.Image) -> dict[str, Any]:
    """Classify one microasset reference crop from deterministic statistics.

    ``image`` is a PNG path or PIL image. The returned mapping carries the
    classification plus every statistic and the active thresholds, so freeze
    receipts stay self-describing when thresholds are recalibrated later.
    """

    rgb = _load_rgb(image)
    height, width = rgb.shape[:2]
    if width == 0 or height == 0:
        raise AssetTraceError("asset-trace:image-empty")

    quantized = (rgb >> 4).reshape(-1, 3)
    unique_colors = int(np.unique(quantized, axis=0).shape[0])

    gray = _gray(rgb)
    magnitude = _gradient_magnitude(gray)
    mean_gradient = float(magnitude.mean())
    high_gradient_fraction = float((magnitude >= _HIGH_GRADIENT_MAGNITUDE).mean())

    from scipy.ndimage import uniform_filter

    mean = uniform_filter(gray, size=_LOCAL_VARIANCE_WINDOW)
    variance = uniform_filter(gray * gray, size=_LOCAL_VARIANCE_WINDOW) - mean * mean
    local_variance_mean = float(np.clip(variance, 0.0, None).mean())

    statistics = {
        "width": int(width),
        "height": int(height),
        "unique_colors_4bit": unique_colors,
        "mean_gradient_magnitude": round(mean_gradient, 6),
        "high_gradient_fraction": round(high_gradient_fraction, 6),
        "local_variance_mean": round(local_variance_mean, 6),
    }
    thresholds = {
        "flat_max_unique_colors": _FLAT_MAX_UNIQUE_COLORS,
        "photo_min_unique_colors": _PHOTO_MIN_UNIQUE_COLORS,
        "high_gradient_magnitude": _HIGH_GRADIENT_MAGNITUDE,
        "flat_min_high_gradient_fraction": _FLAT_MIN_HIGH_GRADIENT_FRACTION,
        "local_variance_window": _LOCAL_VARIANCE_WINDOW,
    }

    # 主判据是 4 bit 量化唯一色数(试点两类样本唯一稳定分离的信号:
    # globe 57 vs observation 384);色数落在平面档还要求存在硬边像素占比,
    # 以排除「色数不高但无清晰边界」的连续调内容(如平滑渐变)被误判为插画。
    if unique_colors >= _PHOTO_MIN_UNIQUE_COLORS:
        classification = "photographic"
    elif (
        unique_colors <= _FLAT_MAX_UNIQUE_COLORS
        and high_gradient_fraction >= _FLAT_MIN_HIGH_GRADIENT_FRACTION
    ):
        classification = "flat-illustration"
    else:
        classification = "ambiguous"
    return {
        "classification": classification,
        "statistics": statistics,
        "thresholds": thresholds,
    }


def vtracer_engine_version() -> str:
    """Return the installed vtracer distribution version, or raise if absent."""

    try:
        return importlib.metadata.version("vtracer")
    except importlib.metadata.PackageNotFoundError as exc:
        raise AssetTraceError("asset-trace:vtracer-not-installed") from exc


def _parse_dimension(value: str | None) -> float | None:
    if value is None:
        return None
    match = _DIMENSION_RE.fullmatch(value)
    return float(match.group(1)) if match else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _fill_root_geometry(svg_text: str, width: int, height: int) -> str:
    """Mechanically complete the root ``<svg>`` geometry declarations.

    vtracer emits ``width``/``height`` equal to the pixel size but no
    ``viewBox`` (docs/vtracer-pilot/README.md §2); the missing ``viewBox`` is
    filled as ``0 0 <width> <height>``. Present-but-divergent geometry is a
    contract deviation, not something to silently rewrite.
    """

    match = _ROOT_TAG_RE.search(svg_text)
    if match is None:
        raise AssetTraceError("asset-trace:svg-root-missing")
    root_tag = match.group(0)
    root = ET.fromstring(svg_text)
    if _local_name(root.tag) != "svg":
        raise AssetTraceError("asset-trace:svg-root-invalid")

    declared_width = _parse_dimension(root.get("width"))
    declared_height = _parse_dimension(root.get("height"))
    for name, declared, expected in (
        ("width", declared_width, float(width)),
        ("height", declared_height, float(height)),
    ):
        if declared is not None and not math.isclose(declared, expected, abs_tol=1e-6):
            raise AssetTraceError(f"asset-trace:svg-{name}-mismatch")

    declared_viewbox = root.get("viewBox")
    expected_viewbox = f"0 0 {width} {height}"
    if declared_viewbox is not None:
        tokens = re.split(r"[\s,]+", declared_viewbox.strip())
        try:
            values = [float(token) for token in tokens]
        except ValueError:
            values = []
        expected_values = [0.0, 0.0, float(width), float(height)]
        if len(values) != 4 or not all(
            math.isclose(actual, wanted, abs_tol=1e-6)
            for actual, wanted in zip(values, expected_values)
        ):
            raise AssetTraceError("asset-trace:svg-viewbox-mismatch")

    insertions = [] if declared_width is not None else [f'width="{width}"']
    if declared_height is None:
        insertions.append(f'height="{height}"')
    if declared_viewbox is None:
        insertions.append(f'viewBox="{expected_viewbox}"')
    if not insertions:
        return svg_text
    completed = root_tag[:-1].rstrip()
    if completed.endswith("/"):
        completed = completed[:-1].rstrip() + " " + " ".join(insertions) + "/>"
    else:
        completed = completed + " " + " ".join(insertions) + ">"
    patched = svg_text[: match.start()] + completed + svg_text[match.end() :]
    if declared_viewbox is None:
        reparsed = ET.fromstring(patched)
        if reparsed.get("viewBox") != expected_viewbox:
            raise AssetTraceError("asset-trace:svg-viewbox-fill-failed")
    return patched


def run_vtracer_trace(
    input_png: str | Path,
    output_svg: str | Path,
    *,
    mode: str = VTRACER_DEFAULT_MODE,
) -> dict[str, Any]:
    """Trace one authorized microasset crop with the locked vtracer profile.

    The trace is a pure function of the input bytes plus locked parameters;
    repeated runs on the same input produce identical output bytes. The
    output SVG is completed with a mechanical ``viewBox`` and rejected if it
    leaves the contract subset.
    """

    input_path = Path(input_png)
    output_path = Path(output_svg)
    if not input_path.is_file():
        raise AssetTraceError(f"asset-trace:input-missing:{input_path}")
    engine_version = vtracer_engine_version()
    try:
        import vtracer
    except ModuleNotFoundError as exc:
        raise AssetTraceError("asset-trace:vtracer-not-installed") from exc

    rgb = _load_rgb(input_path)
    height, width = rgb.shape[:2]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        vtracer.convert_image_to_svg_py(
            str(input_path),
            str(output_path),
            mode=mode,
            **VTRACER_LOCKED_PARAMETERS,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # Rust 核心 panic 经 PyO3 暴露为 BaseException
        raise AssetTraceError(f"asset-trace:vtracer-failed:{exc}") from exc
    try:
        svg_text = output_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssetTraceError(f"asset-trace:output-unreadable:{exc}") from exc
    patched = _fill_root_geometry(svg_text, width, height)
    if patched != svg_text:
        output_path.write_text(patched, encoding="utf-8")

    violations = check_svg_contract_subset(output_path)
    if violations:
        raise AssetTraceError(
            [f"asset-trace:contract-subset:{violation}" for violation in violations]
        )
    return {
        "input_png": str(input_path),
        "output_svg": str(output_path),
        "width": int(width),
        "height": int(height),
        "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "trace_engine": "vtracer",
        "trace_engine_version": engine_version,
        "trace_method": f"vtracer-color-stacked-{mode}",
        "mode": mode,
        "parameters": {"mode": mode, **VTRACER_LOCKED_PARAMETERS},
    }


def check_svg_contract_subset(svg_path: str | Path) -> list[str]:
    """Return contract-subset violations for one traced SVG (empty = pass).

    The traced artifact must be pure path stacking: only ``svg``/``g``/``path``
    elements, and no mask/filter/blend-mode constructs in attributes or inline
    styles. Anything else (``<image>``, ``<mask>``, mesh gradients, ``<text>``,
    ``<script>``, ``<foreignObject>``, animation/filter primitives, ...) is
    reported as one violation token per offending construct.
    """

    path = Path(svg_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ["unreadable"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ["unparseable"]
    if _local_name(root.tag) != "svg":
        return ["root-not-svg"]

    violations: set[str] = set()
    for element in root.iter():
        if not isinstance(element.tag, str):
            violations.add("non-element-node")
            continue
        name = _local_name(element.tag)
        if name not in _ALLOWED_ELEMENTS:
            violations.add(f"forbidden-element:{name}")
        for attribute, value in element.attrib.items():
            attribute_name = _local_name(attribute)
            if attribute_name in _FORBIDDEN_ATTRIBUTES:
                violations.add(f"forbidden-attribute:{attribute_name}")
            if attribute_name == "style":
                normalized = re.sub(r"\s+", "", value).lower()
                for token in _FORBIDDEN_STYLE_TOKENS:
                    if f"{token}:" in normalized:
                        violations.add(f"forbidden-style:{token}")
    return sorted(violations)
