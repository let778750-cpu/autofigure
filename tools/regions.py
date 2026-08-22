"""Region-level visual gates; global metrics never override a critical failure."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image

from tools import common
from tools.contracts import SCHEMA_VERSION, read_json, write_json


def _crop(array: np.ndarray, bbox: list[int]) -> np.ndarray:
    x, y, width, height = (int(v) for v in bbox)
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid region bbox: {bbox}")
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(array.shape[1], x + width), min(array.shape[0], y + height)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"region bbox outside canvas: {bbox}")
    return array[y0:y1, x0:x1]


def _ssim(reference: np.ndarray, render: np.ndarray) -> float:
    from skimage.metrics import structural_similarity

    smallest = min(reference.shape[0], reference.shape[1])
    win_size = min(7, smallest if smallest % 2 else smallest - 1)
    if win_size < 3:
        return 1.0 if np.array_equal(reference, render) else 0.0
    return float(
        structural_similarity(reference, render, channel_axis=2, data_range=255, win_size=win_size)
    )


def _edge_iou(reference: np.ndarray, render: np.ndarray) -> float:
    from skimage.feature import canny
    from scipy.ndimage import binary_dilation

    ref_gray = np.dot(reference[..., :3], np.array([0.299, 0.587, 0.114])) / 255.0
    ren_gray = np.dot(render[..., :3], np.array([0.299, 0.587, 0.114])) / 255.0
    ref_edges = canny(ref_gray, sigma=1.0)
    ren_edges = canny(ren_gray, sigma=1.0)
    ref_count = int(ref_edges.sum())
    ren_count = int(ren_edges.sum())
    if ref_count == 0 and ren_count == 0:
        return 1.0
    if ref_count == 0 or ren_count == 0:
        return 0.0
    # PowerPoint and PNG references can rasterize the same vector boundary on
    # adjacent pixels. A two-pixel symmetric tolerance is 0.13% of the case-01
    # diagonal: it measures topology rather than anti-aliasing phase while still
    # penalizing missing or extra edges.
    ref_match = np.logical_and(ref_edges, binary_dilation(ren_edges, iterations=2)).sum()
    ren_match = np.logical_and(ren_edges, binary_dilation(ref_edges, iterations=2)).sum()
    intersection = (float(ref_match) + float(ren_match)) / 2.0
    union = ref_count + ren_count - intersection
    return float(intersection / union) if union > 0 else 1.0


def _mean_rgb(array: np.ndarray, point: list[int], radius: int) -> np.ndarray:
    x, y = (int(v) for v in point)
    y0, y1 = max(0, y - radius), min(array.shape[0], y + radius + 1)
    x0, x1 = max(0, x - radius), min(array.shape[1], x + radius + 1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"color probe outside canvas: {point}")
    return array[y0:y1, x0:x1, :3].mean(axis=(0, 1))


def _delta_e(reference_rgb: np.ndarray, render_rgb: np.ndarray) -> float:
    from skimage.color import deltaE_ciede2000, rgb2lab

    pair = np.array([[reference_rgb, render_rgb]], dtype=float) / 255.0
    lab = rgb2lab(pair)
    return float(deltaE_ciede2000(lab[:, :1], lab[:, 1:])[0, 0])


def _evaluate_probes(
    reference: np.ndarray,
    render: np.ndarray,
    probes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    results: list[dict[str, Any]] = []
    passed = True
    for probe in probes:
        radius = int(probe.get("radius", 2))
        ref_rgb = _mean_rgb(reference, probe["point"], radius)
        ren_rgb = _mean_rgb(render, probe["point"], radius)
        delta = _delta_e(ref_rgb, ren_rgb)
        maximum = float(probe.get("max_delta_e", 5.0))
        item_pass = delta <= maximum
        passed = passed and item_pass
        results.append(
            {
                "id": probe.get("id", f"probe-{len(results) + 1}"),
                "point": probe["point"],
                "delta_e00": round(delta, 4),
                "max_delta_e": maximum,
                "pass": item_pass,
            }
        )
    return results, passed


def evaluate_regions(run: common.Run) -> dict[str, Any]:
    payload = read_json(run.regions_path)
    with Image.open(run.source_png) as ref_image, Image.open(run.render_png) as ren_image:
        reference = np.asarray(ref_image.convert("RGB"), dtype=np.uint8)
        render = np.asarray(ren_image.convert("RGB"), dtype=np.uint8)
    if reference.shape != render.shape:
        raise common.fail(f"reference/render size mismatch: {reference.shape} != {render.shape}")

    defaults = payload.get("defaults", {})
    results: list[dict[str, Any]] = []
    critical_count = 0
    critical_pass = True
    for region in payload.get("regions", []):
        ref_crop = _crop(reference, region["bbox"])
        ren_crop = _crop(render, region["bbox"])
        mean_delta = float(np.abs(ref_crop.astype(float) - ren_crop.astype(float)).mean())
        ssim = _ssim(ref_crop, ren_crop)
        edge_iou = _edge_iou(ref_crop, ren_crop)
        thresholds = {**defaults, **region.get("thresholds", {})}
        region_pass = (
            ssim >= float(thresholds.get("ssim_min", 0.85))
            and edge_iou >= float(thresholds.get("edge_iou_min", 0.75))
        )
        probes, probes_pass = _evaluate_probes(reference, render, region.get("color_probes", []))
        region_pass = region_pass and probes_pass
        critical = bool(region.get("critical", False))
        if critical:
            critical_count += 1
            critical_pass = critical_pass and region_pass
        results.append(
            {
                "id": region["id"],
                "label": region.get("label", region["id"]),
                "bbox": region["bbox"],
                "critical": critical,
                "mean_abs_rgb_delta": round(mean_delta, 4),
                "ssim": round(ssim, 4),
                "edge_iou": round(edge_iou, 4),
                "thresholds": thresholds,
                "color_probes": probes,
                "pass": region_pass,
            }
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "region_evaluation",
        "reference_sha256": run.load_meta()["source_sha256"],
        "critical_regions": critical_count,
        "strict_pass": critical_count > 0 and critical_pass,
        "blockers": ([] if critical_count else ["regions:no-critical-regions"])
        + [f"region:{item['id']}" for item in results if item["critical"] and not item["pass"]],
        "regions": results,
    }
    write_json(run.qa_dir / "regions-report.json", report)
    return report


def normalized_distance_limit(width: int, height: int, fraction: float) -> float:
    """Convert a canvas-diagonal fraction to pixels for arrow acceptance tests."""
    return math.hypot(width, height) * fraction
