#!/usr/bin/env python3
"""Deterministic, provenance-bound structural analysis for a raster figure.

The CLI accepts an explicit source image and output directory. It records the
source hash, algorithm version, and all behavior-affecting parameters so that
diagnostic output can be reproduced instead of becoming an implicit baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import PIL
import scipy
from PIL import Image
from scipy import ndimage

# ``python -I`` intentionally removes the script directory from ``sys.path``.
# Re-add only this trusted, resolved tools directory so the CLI can import its
# sibling output-policy module without inheriting user-site or CWD modules.
TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.analyze_target
    try:
        from .output_policy import resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the project root.
        from tools.output_policy import resolve_output_path


ALGORITHM_NAME = "structural-image-analysis"
ALGORITHM_VERSION = "2.1.0"

DEFAULT_PARAMETERS: dict[str, int | float] = {
    "border_width": 4,
    "background_quantization": 16,
    "foreground_distance_l1": 24,
    "palette_quantization": 24,
    "palette_min_fraction": 0.002,
    "band_density_threshold": 0.02,
    "band_merge_gap": 8,
    "component_downsample": 4,
    "long_run_min_fraction": 0.25,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write UTF-8 JSON through a same-directory temporary file and replace."""
    path = resolve_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_save_image(image: Image.Image, path: Path) -> None:
    """Atomically replace a PNG so interrupted reruns do not leave half-files."""
    path = resolve_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".png",
        dir=path.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        image.save(temporary_path, format="PNG")
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _border_pixels(rgb: np.ndarray, border_width: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    effective_width = min(border_width, max(1, height // 2), max(1, width // 2))
    return np.concatenate(
        [
            rgb[:effective_width, :, :].reshape(-1, 3),
            rgb[-effective_width:, :, :].reshape(-1, 3),
            rgb[:, :effective_width, :].reshape(-1, 3),
            rgb[:, -effective_width:, :].reshape(-1, 3),
        ]
    )


def estimate_background_rgb(
    rgb: np.ndarray,
    *,
    border_width: int = 4,
    quantization: int = 16,
) -> tuple[int, int, int]:
    """Estimate the real border color without returning its quantized proxy.

    Quantization is used only to select the dominant border-color bucket. The
    returned value is the per-channel median of original pixels in that bucket.
    Consequently a #FEFEFE canvas remains #FEFEFE rather than becoming #F0F0F0
    and being misclassified as foreground by a subsequent distance threshold.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.size == 0:
        raise ValueError("Expected a non-empty HxWx3 RGB array.")
    if border_width < 1:
        raise ValueError("border_width must be at least 1.")
    if not 1 <= quantization <= 256:
        raise ValueError("background quantization must be between 1 and 256.")

    border = _border_pixels(rgb, border_width).astype(np.int16)
    buckets = border // quantization
    unique_buckets, counts = np.unique(buckets, axis=0, return_counts=True)
    dominant_bucket = unique_buckets[int(np.argmax(counts))]
    members = np.all(buckets == dominant_bucket, axis=1)
    original_members = border[members]
    if original_members.size == 0:
        original_members = border
    background = np.rint(np.median(original_members, axis=0)).astype(np.uint8)
    return tuple(int(value) for value in background)


def rgb_hex(rgb: Sequence[int]) -> str:
    return "#" + "".join(f"{int(value):02X}" for value in rgb)


def foreground_mask(
    rgb: np.ndarray,
    background_rgb: Sequence[int],
    distance_l1: int,
) -> np.ndarray:
    background = np.asarray(background_rgb, dtype=np.int16)
    distance = np.abs(rgb.astype(np.int16) - background).sum(axis=2)
    return distance > distance_l1


def bands(
    density: np.ndarray,
    *,
    threshold: float = 0.02,
    min_gap: int = 8,
) -> list[list[int]]:
    """Return hot half-open intervals, merging gaps shorter than min_gap."""
    hot = density > threshold
    found: list[list[int]] = []
    index = 0
    while index < len(hot):
        if not hot[index]:
            index += 1
            continue
        end = index
        while end < len(hot) and hot[end]:
            end += 1
        found.append([index, end])
        index = end

    merged: list[list[int]] = []
    for start, end in found:
        if merged and start - merged[-1][1] < min_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def long_runs(mask: np.ndarray, axis: int, min_len: int) -> list[list[int]]:
    runs: list[list[int]] = []
    scan = mask if axis == 0 else mask.T
    for primary_index, row in enumerate(scan):
        index = 0
        while index < len(row):
            if not row[index]:
                index += 1
                continue
            end = index
            while end < len(row) and row[end]:
                end += 1
            if end - index >= min_len:
                runs.append([int(primary_index), int(index), int(end)])
            index = end
    return runs


def _palette(
    rgb: np.ndarray,
    background_rgb: Sequence[int],
    *,
    quantization: int,
    min_fraction: float,
    background_distance_l1: int,
) -> list[dict[str, int | float | str]]:
    values = rgb.astype(np.int16)
    quantized = np.clip(
        ((values + quantization // 2) // quantization) * quantization,
        0,
        255,
    ).astype(np.uint8)
    colors, counts = np.unique(quantized.reshape(-1, 3), axis=0, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    total = rgb.shape[0] * rgb.shape[1]
    background = np.asarray(background_rgb, dtype=np.int16)
    palette: list[dict[str, int | float | str]] = []
    for index in order:
        color = colors[index]
        fraction = float(counts[index]) / total
        if fraction < min_fraction:
            continue
        distance = int(np.abs(color.astype(np.int16) - background).sum())
        if distance <= background_distance_l1:
            continue
        palette.append(
            {
                "hex": rgb_hex(color),
                "pct": round(fraction * 100.0, 2),
                "dist_bg": distance,
            }
        )
    return palette


def analyze_image(
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
            raise ValueError(f"Unknown analysis parameters: {sorted(unknown)}")
        params.update(parameters)
    if int(params["component_downsample"]) < 1:
        raise ValueError("component_downsample must be at least 1.")
    if float(params["long_run_min_fraction"]) <= 0:
        raise ValueError("long_run_min_fraction must be positive.")

    with Image.open(source) as opened:
        image = opened.convert("RGB")
    rgb = np.asarray(image, dtype=np.uint8)
    height, width = rgb.shape[:2]
    output_dir.mkdir(parents=True, exist_ok=True)

    background_rgb = estimate_background_rgb(
        rgb,
        border_width=int(params["border_width"]),
        quantization=int(params["background_quantization"]),
    )
    non_background = foreground_mask(
        rgb,
        background_rgb,
        int(params["foreground_distance_l1"]),
    )
    row_density = non_background.mean(axis=1)
    column_density = non_background.mean(axis=0)
    horizontal_bands = bands(
        row_density,
        threshold=float(params["band_density_threshold"]),
        min_gap=int(params["band_merge_gap"]),
    )
    vertical_bands = bands(
        column_density,
        threshold=float(params["band_density_threshold"]),
        min_gap=int(params["band_merge_gap"]),
    )

    downsample = int(params["component_downsample"])
    labels, component_count = ndimage.label(non_background[::downsample, ::downsample])
    component_sizes = ndimage.sum(
        np.ones_like(labels),
        labels,
        index=range(1, int(component_count) + 1),
    )
    component_sizes_desc = sorted(
        (int(size) for size in component_sizes),
        reverse=True,
    )

    horizontal_runs = long_runs(
        non_background,
        0,
        max(1, int(width * float(params["long_run_min_fraction"]))),
    )
    vertical_runs = long_runs(
        non_background,
        1,
        max(1, int(height * float(params["long_run_min_fraction"]))),
    )

    crop_boxes = {
        "qTL": (0, 0, width // 2, height // 2),
        "qTR": (width // 2, 0, width, height // 2),
        "qBL": (0, height // 2, width // 2, height),
        "qBR": (width // 2, height // 2, width, height),
    }
    for name, box in crop_boxes.items():
        atomic_save_image(image.crop(box), output_dir / f"{name}.png")

    palette = _palette(
        rgb,
        background_rgb,
        quantization=int(params["palette_quantization"]),
        min_fraction=float(params["palette_min_fraction"]),
        background_distance_l1=int(params["foreground_distance_l1"]),
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
            "parameters": params,
        },
        "runtime": {
            "python": sys.version.split()[0],
            "python_executable": str(Path(sys.executable).resolve()),
            "numpy": np.__version__,
            "pillow": PIL.__version__,
            "scipy": scipy.__version__,
        },
        "canvas": {
            "w": width,
            "h": height,
            "background_hex": rgb_hex(background_rgb),
        },
        "palette_top": palette[:25],
        "h_bands": horizontal_bands,
        "v_bands": vertical_bands,
        "connected_components": int(component_count),
        "component_sizes_top": component_sizes_desc[:20],
        "long_h_runs": horizontal_runs,
        "long_v_runs": vertical_runs,
    }
    atomic_write_json(output_dir / "inventory.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic structural analysis of an explicit raster source."
    )
    parser.add_argument("source", type=Path, help="Source PNG/JPEG path.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Directory for inventory.json and quadrant crops.",
    )
    parser.add_argument("--border-width", type=int, default=4)
    parser.add_argument("--background-quantization", type=int, default=16)
    parser.add_argument("--foreground-distance", type=int, default=24)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    overrides = {
        "border_width": args.border_width,
        "background_quantization": args.background_quantization,
        "foreground_distance_l1": args.foreground_distance,
    }
    try:
        result = analyze_image(args.source, args.output, parameters=overrides)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"analysis failed: {exc}", file=sys.stderr)
        return 2

    canvas = result["canvas"]
    print(
        f"inventory.json written: {canvas['w']}x{canvas['h']} "
        f"background={canvas['background_hex']} source={result['source']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
