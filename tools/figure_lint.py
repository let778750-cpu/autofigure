#!/usr/bin/env python3
"""figure_lint.py — 科研图像复刻量化诊断器（Quality Signal，非硬门）。

对齐 references/02-qa-gates.md（Quality Signals）：
  - mean_abs_rgb_delta / SSIM / ROI 差异仅作「哪里差」的诊断与回归标定依据，
    不构成 pass/fail 硬门。硬门是 Core Gates（文字逐字/拓扑/无重叠/可编辑等布尔项）。
  - 起始诊断阈值：benchmark delta<=18 且 top_roi_loss<5%；strict delta<=3 且 changed_ratio<=3%。
    该值由真实图回归标定后固定，禁止当作万能标准。
  - SSIM 仅作副指标报告。
  - 尺寸不一致时，共同区域裁切只用于定位差异；结果必须失败，且不代表允许裁切或缩放后通过。

用法：
  python figure_lint.py 参考图.png 渲染图.png [--mode benchmark|strict] [--pretty]
  python figure_lint.py 参考图.png 渲染图.png \
    --diff-out examples/generated/runs/<run_id>/qa/diff.png        # 输出差异热图
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.figure_lint
    try:
        from .output_policy import resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the project root.
        from tools.output_policy import resolve_output_path


def load_rgb(path: Path) -> np.ndarray:
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"), dtype=np.uint8)
    except OSError as exc:
        raise RuntimeError(f"无法打开图片 {path}: {exc}") from exc


def align(a: np.ndarray, b: np.ndarray):
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    h, w = min(ha, hb), min(wa, wb)
    return a[:h, :w], b[:h, :w], (ha, wa), (hb, wb)


def pixel_mean_delta(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a.astype(np.float32) - b.astype(np.float32)).mean(axis=2)


def top_roi(diff: np.ndarray, tile_size: int, hot_threshold: float):
    """按 tile 找最大高差异区域，返回 (bbox{x,y,w,h}, mean, loss_pct)。"""
    h, w = diff.shape
    th = tw = tile_size
    gh = (h + th - 1) // th
    gw = (w + tw - 1) // tw
    pad_h, pad_w = gh * th - h, gw * tw - w
    padded = np.pad(diff, ((0, pad_h), (0, pad_w)), constant_values=0.0)
    tiles = padded.reshape(gh, th, gw, tw).transpose(0, 2, 1, 3).reshape(gh, gw, th * tw)
    tile_sum = tiles.sum(axis=2)
    tile_mean = tile_sum / (th * tw)

    total = float(diff.sum())
    hot = tile_mean > hot_threshold

    # 4-连通分量
    def components(mask):
        seen = np.zeros_like(mask, dtype=bool)
        comps = []
        for r in range(mask.shape[0]):
            for c in range(mask.shape[1]):
                if not mask[r, c] or seen[r, c]:
                    continue
                stack = [(r, c)]
                cells = []
                while stack:
                    y, x = stack.pop()
                    if seen[y, x]:
                        continue
                    seen[y, x] = True
                    cells.append((y, x))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not seen[ny, nx]:
                            stack.append((ny, nx))
                comps.append(cells)
        return comps

    def _roi_box(x0, y0, x1, y1, roi_diff, loss):
        return {
            "x": int(x0), "y": int(y0), "w": int(x1 - x0), "h": int(y1 - y0),
        }, round(float(roi_diff.mean()), 4), round(float(loss) * 100.0 / total, 4) if total else 0.0

    comps = components(hot)
    if comps:
        best = max(comps, key=lambda c: float(sum(tile_sum[y, x] for y, x in c)))
        loss = float(sum(tile_sum[y, x] for y, x in best))
        ys = [y for y, _ in best]
        xs = [x for _, x in best]
        x0, y0 = min(xs) * tw, min(ys) * th
        x1, y1 = min((max(xs) + 1) * tw, w), min((max(ys) + 1) * th, h)
        return _roi_box(x0, y0, x1, y1, diff[y0:y1, x0:x1], loss)

    # 无 hot tile → 取最差单个 tile
    idx = int(np.argmax(tile_mean))
    ty, tx = divmod(idx, gw)
    x0, y0 = tx * tw, ty * th
    x1, y1 = min(x0 + tw, w), min(y0 + th, h)
    loss = float(diff[y0:y1, x0:x1].sum())
    return _roi_box(x0, y0, x1, y1, diff[y0:y1, x0:x1], loss)


def try_ssim(a: np.ndarray, b: np.ndarray):
    try:
        from skimage.metrics import structural_similarity
    except ImportError:
        return None, "skimage 未安装，跳过 SSIM。"
    try:
        score = structural_similarity(a, b, channel_axis=2)
    except TypeError:
        score = structural_similarity(a, b, multichannel=True)
    except Exception as exc:
        return None, f"SSIM 失败: {exc}"
    return round(float(score), 4), None


def save_diff_heat(a: np.ndarray, b: np.ndarray, out_path: Path):
    out_path = resolve_output_path(out_path)
    diff = pixel_mean_delta(a, b)
    heat = np.clip(diff * 3.0, 0, 255).astype(np.uint8)
    base = b.astype(np.float32)
    out = np.zeros_like(base)
    out[:, :, 0] = np.clip(base[:, :, 0] * 0.58 + heat * 0.42, 0, 255)
    out[:, :, 1] = np.clip(base[:, :, 1] * 0.58 - heat * 0.12, 0, 255)
    out[:, :, 2] = np.clip(base[:, :, 2] * 0.58 - heat * 0.12, 0, 255)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out.astype(np.uint8), "RGB").save(out_path)


def lint(target: np.ndarray, final: np.ndarray, mode: str, tile_size: int) -> dict:
    a, b, size_a, size_b = align(target, final)
    diff = pixel_mean_delta(a, b)
    n = diff.size

    mean_abs_rgb_delta = round(float(diff.mean()), 4)
    rmse = round(float(np.sqrt((diff * diff).mean())), 4)
    pixels_over = {
        10: round(float((diff > 10).sum()) * 100.0 / n, 4),
        25: round(float((diff > 25).sum()) * 100.0 / n, 4),
        50: round(float((diff > 50).sum()) * 100.0 / n, 4),
    }
    changed_pixel_ratio = round(float((diff >= 1.0).sum()) * 100.0 / n, 4)

    roi_bbox, roi_mean, roi_loss_pct = top_roi(diff, tile_size, hot_threshold=25.0)
    ssim, ssim_note = try_ssim(a, b)

    if mode == "strict":
        # changed_pixel_ratio is already expressed in percentage points
        # (0..100), so the 3% threshold is 3.0 rather than the fraction 0.03.
        l2_pass = mean_abs_rgb_delta <= 3.0 and changed_pixel_ratio <= 3.0
        threshold_desc = "mean_abs_rgb_delta<=3 且 changed_pixel_ratio<=3%"
    else:
        l2_pass = mean_abs_rgb_delta <= 18.0 and roi_loss_pct < 5.0
        threshold_desc = "mean_abs_rgb_delta<=18 且 top_roi_loss<5%"

    if size_a != size_b:
        l2_pass = False

    summary = (
        f"mode={mode} | mean_abs_rgb_delta={mean_abs_rgb_delta} | "
        f"top_roi_loss={roi_loss_pct}% | changed_pixel_ratio={changed_pixel_ratio}% | "
        f"ssim={ssim if ssim is not None else 'N/A'} | diagnostic={'PASS' if l2_pass else 'WATCH'}"
    )

    notes = [] if ssim_note is None else [ssim_note]
    if size_a != size_b:
        notes.append(
            "尺寸不一致：差异指标仅基于左上角共同区域作诊断；"
            "不得据此通过，也不代表允许裁切或缩放对齐。"
        )

    result = {
        "mode": mode,
        "diagnostic_pass": l2_pass,
        "threshold": threshold_desc,
        "sizes": {"target": {"w": size_a[1], "h": size_a[0]}, "final": {"w": size_b[1], "h": size_b[0]}},
        "size_mismatch": size_a != size_b,
        "mean_abs_rgb_delta": mean_abs_rgb_delta,
        "rmse_rgb_delta": rmse,
        "pixels_over_delta_10_pct": pixels_over[10],
        "pixels_over_delta_25_pct": pixels_over[25],
        "pixels_over_delta_50_pct": pixels_over[50],
        "changed_pixel_ratio_pct": changed_pixel_ratio,
        "top_roi": {"bbox": roi_bbox, "mean_abs_rgb_delta": roi_mean, "loss_contribution_pct": roi_loss_pct},
        "ssim": ssim,
        "notes": notes,
        "summary": summary,
    }
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="科研图像复刻量化校验（delta 主指标 + SSIM 副指标）")
    parser.add_argument("target", help="参考图（用户满意的 PNG）路径")
    parser.add_argument("final", help="渲染图（PPT 导出 PNG）路径")
    parser.add_argument("--mode", choices=["benchmark", "strict"], default="benchmark",
                        help="基准模式(<=18,<5%%) 或 严格1:1模式(<=3,<=3%%)")
    parser.add_argument("--tile-size", type=int, default=40, help="ROI 分块大小(px)")
    parser.add_argument("--diff-out", help="输出差异热图路径")
    parser.add_argument("--pretty", action="store_true", help="美化 JSON 输出")
    args = parser.parse_args()

    tp, fp = Path(args.target), Path(args.final)
    for p in (tp, fp):
        if not p.exists():
            print(json.dumps({"passed": False, "errors": [f"文件不存在: {p}"], "summary": f"文件不存在: {p}"},
                             ensure_ascii=False))
            return 2

    try:
        target = load_rgb(tp)
        final = load_rgb(fp)
        result = lint(target, final, args.mode, args.tile_size)
        if args.diff_out:
            a, b, _, _ = align(target, final)
            save_diff_heat(a, b, Path(args.diff_out))
            result["diff_out"] = args.diff_out
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"passed": False, "errors": [str(exc)], "summary": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["diagnostic_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
