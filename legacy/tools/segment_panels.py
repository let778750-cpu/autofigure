#!/usr/bin/env python3
"""Deterministic color-component observations for a raster figure.

The output contains heuristic region candidates for review. Color clustering
cannot by itself establish that a connected component is a semantic panel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import PIL
from PIL import Image

# Keep direct-file execution compatible with ``python -I`` while exposing only
# the resolved sibling tools directory, never the caller's working directory.
TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from analyze_target import (
        atomic_save_image,
        atomic_write_json,
        estimate_background_rgb,
        rgb_hex,
        sha256_file,
    )
except ModuleNotFoundError:  # Support: python -m tools.segment_panels
    from .analyze_target import (
        atomic_save_image,
        atomic_write_json,
        estimate_background_rgb,
        rgb_hex,
        sha256_file,
    )

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.segment_panels
    try:
        from .output_policy import resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the project root.
        from tools.output_policy import resolve_output_path


ALGORITHM_NAME = "color-component-region-candidates"
ALGORITHM_VERSION = "2.1.0"
DEFAULT_RANDOM_SEED = 1729

DEFAULT_PARAMETERS: dict[str, int | float] = {
    "border_width": 4,
    "background_quantization": 16,
    "foreground_distance_l1": 30,
    "saturation_distance_l1": 120,
    "clusters": 14,
    "kmeans_max_iterations": 30,
    "kmeans_epsilon": 1.0,
    "kmeans_attempts": 3,
    "random_seed": DEFAULT_RANDOM_SEED,
    "opencv_threads": 1,
    "min_component_area_ratio": 0.0015,
}


def _validate_parameters(parameters: dict[str, int | float]) -> None:
    if int(parameters["clusters"]) < 1:
        raise ValueError("clusters must be at least 1.")
    if int(parameters["kmeans_attempts"]) < 1:
        raise ValueError("kmeans_attempts must be at least 1.")
    if int(parameters["kmeans_max_iterations"]) < 1:
        raise ValueError("kmeans_max_iterations must be at least 1.")
    if int(parameters["opencv_threads"]) < 1:
        raise ValueError("opencv_threads must be at least 1.")
    if float(parameters["min_component_area_ratio"]) <= 0:
        raise ValueError("min_component_area_ratio must be positive.")
    if int(parameters["saturation_distance_l1"]) <= int(
        parameters["foreground_distance_l1"]
    ):
        raise ValueError(
            "saturation_distance_l1 must exceed foreground_distance_l1."
        )
    seed = int(parameters["random_seed"])
    if not -(2**31) <= seed < 2**31:
        raise ValueError("random_seed must fit in a signed 32-bit integer.")


def _cluster_pixels(
    bgr: np.ndarray,
    ink: np.ndarray,
    parameters: dict[str, int | float],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    pixels = bgr[ink].reshape(-1, 3).astype(np.float32)
    if len(pixels) == 0:
        return np.empty(0, dtype=np.int32), []

    effective_clusters = min(int(parameters["clusters"]), len(pixels))
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        int(parameters["kmeans_max_iterations"]),
        float(parameters["kmeans_epsilon"]),
    )
    cv2.setNumThreads(int(parameters["opencv_threads"]))
    cv2.setRNGSeed(int(parameters["random_seed"]))
    _, labels, centers = cv2.kmeans(
        pixels,
        effective_clusters,
        None,
        criteria,
        int(parameters["kmeans_attempts"]),
        cv2.KMEANS_PP_CENTERS,
    )
    flat_labels = labels.reshape(-1).astype(np.int32)
    centers = np.rint(centers).clip(0, 255).astype(np.uint8)
    cluster_ids, counts = np.unique(flat_labels, return_counts=True)
    total = len(flat_labels)

    clusters: list[dict[str, Any]] = []
    for cluster_id, count in zip(cluster_ids, counts):
        center_bgr = centers[int(cluster_id)]
        center_rgb = center_bgr[::-1]
        clusters.append(
            {
                "cluster": int(cluster_id),
                "bgr": [int(value) for value in center_bgr],
                "hex": rgb_hex(center_rgb),
                "count": int(count),
                "pct": round(float(count) * 100.0 / total, 4),
            }
        )
    clusters.sort(key=lambda item: (-item["count"], item["hex"], item["cluster"]))
    return flat_labels, clusters


def segment_image(
    source: Path,
    output_dir: Path,
    *,
    parameters: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    source = Path(source).expanduser().resolve()
    output_dir = resolve_output_path(output_dir)
    if not source.is_file():
        raise FileNotFoundError(f"Source image does not exist: {source}")

    params = dict(DEFAULT_PARAMETERS)
    if parameters:
        unknown = set(parameters) - set(params)
        if unknown:
            raise ValueError(f"Unknown segmentation parameters: {sorted(unknown)}")
        params.update(parameters)
    _validate_parameters(params)

    with Image.open(source) as opened:
        image = opened.convert("RGB")
    rgb = np.asarray(image, dtype=np.uint8)
    height, width = rgb.shape[:2]
    bgr = rgb[:, :, ::-1].copy()
    output_dir.mkdir(parents=True, exist_ok=True)

    background_rgb = estimate_background_rgb(
        rgb,
        border_width=int(params["border_width"]),
        quantization=int(params["background_quantization"]),
    )
    background = np.asarray(background_rgb, dtype=np.int16)
    distance = np.abs(rgb.astype(np.int16) - background).sum(axis=2)
    foreground_distance = int(params["foreground_distance_l1"])
    saturation_distance = int(params["saturation_distance_l1"])
    ink = distance > foreground_distance
    light = (distance > foreground_distance) & (distance < saturation_distance)
    saturated = distance >= saturation_distance

    flat_labels, clusters = _cluster_pixels(bgr, ink, params)
    label_map = np.full((height, width), -1, dtype=np.int32)
    if flat_labels.size:
        label_map[ink] = flat_labels

    min_area = max(
        1,
        int(round(float(params["min_component_area_ratio"]) * height * width)),
    )
    candidates: list[dict[str, Any]] = []
    for cluster in clusters:
        if cluster["count"] < min_area:
            continue
        mask = (label_map == cluster["cluster"]).astype(np.uint8)
        component_count, _, stats, centroids = cv2.connectedComponentsWithStats(
            mask,
            connectivity=8,
        )
        for component_index in range(1, component_count):
            x, y, component_width, component_height, area = (
                int(value) for value in stats[component_index]
            )
            if area < min_area:
                continue
            aspect = max(component_width, component_height) / max(
                1, min(component_width, component_height)
            )
            candidates.append(
                {
                    "hex": cluster["hex"],
                    "cluster": cluster["cluster"],
                    "area": area,
                    "bbox": [x, y, component_width, component_height],
                    "cx": round(float(centroids[component_index][0])),
                    "cy": round(float(centroids[component_index][1])),
                    "aspect": round(aspect, 1),
                    "status": "heuristic_region_candidate",
                }
            )

    candidates.sort(
        key=lambda item: (
            -item["area"],
            item["bbox"][1],
            item["bbox"][0],
            item["hex"],
        )
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = f"region-candidate-{index:03d}"

    overlay_bgr = bgr.copy()
    for candidate in candidates:
        x, y, component_width, component_height = candidate["bbox"]
        cv2.rectangle(
            overlay_bgr,
            (x, y),
            (min(width - 1, x + component_width - 1), min(height - 1, y + component_height - 1)),
            (0, 0, 255),
            2,
        )
    overlay_rgb = cv2.cvtColor(overlay_bgr, cv2.COLOR_BGR2RGB)
    atomic_save_image(Image.fromarray(overlay_rgb), output_dir / "panels_overlay.png")
    atomic_save_image(
        Image.fromarray((light.astype(np.uint8) * 255), mode="L"),
        output_dir / "mask_light.png",
    )
    atomic_save_image(
        Image.fromarray((saturated.astype(np.uint8) * 255), mode="L"),
        output_dir / "mask_saturated.png",
    )

    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
            "size_bytes": source.stat().st_size,
        },
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "parameters": {
                **params,
                "effective_clusters": len(clusters),
            },
        },
        "runtime": {
            "python": sys.version.split()[0],
            "python_executable": str(Path(sys.executable).resolve()),
            "numpy": np.__version__,
            "pillow": PIL.__version__,
            "opencv": cv2.__version__,
        },
        "canvas": {
            "w": width,
            "h": height,
            "background_hex": rgb_hex(background_rgb),
        },
        "coverage_pct": {
            "ink": round(float(ink.mean()) * 100.0, 4),
            "light_fill": round(float(light.mean()) * 100.0, 4),
            "saturated": round(float(saturated.mean()) * 100.0, 4),
        },
        "clusters": [
            {
                "cluster": cluster["cluster"],
                "hex": cluster["hex"],
                "pct": cluster["pct"],
            }
            for cluster in clusters
        ],
        "region_candidates": candidates,
        "interpretation": {
            "status": "observation_only",
            "verified_panel_count": 0,
            "disclaimer": (
                "Color-cluster components are heuristic region candidates, "
                "not verified semantic panels."
            ),
        },
    }
    atomic_write_json(output_dir / "panels.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic color-component region candidates."
    )
    parser.add_argument("source", type=Path, help="Source PNG/JPEG path.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Directory for panels.json and diagnostic PNGs.",
    )
    parser.add_argument("--clusters", type=int, default=14)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--border-width", type=int, default=4)
    parser.add_argument("--background-quantization", type=int, default=16)
    parser.add_argument("--foreground-distance", type=int, default=30)
    parser.add_argument("--saturation-distance", type=int, default=120)
    parser.add_argument("--min-area-ratio", type=float, default=0.0015)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    overrides = {
        "clusters": args.clusters,
        "random_seed": args.seed,
        "border_width": args.border_width,
        "background_quantization": args.background_quantization,
        "foreground_distance_l1": args.foreground_distance,
        "saturation_distance_l1": args.saturation_distance,
        "min_component_area_ratio": args.min_area_ratio,
    }
    try:
        result = segment_image(args.source, args.output, parameters=overrides)
    except (OSError, RuntimeError, ValueError, cv2.error) as exc:
        print(f"segmentation failed: {exc}", file=sys.stderr)
        return 2

    print(
        "panels.json written with "
        f"{len(result['region_candidates'])} heuristic region candidates; "
        "semantic panel verification remains external."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
