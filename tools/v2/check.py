"""autofigure check — verify-light 三件套：文本比对 + figure_lint 诊断 + 对照预览。

全部为 advisory（供人审），不设自动 gate——验收判据永远是人审 + 本报告。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.v2 import common

SVG_NS = "{http://www.w3.org/2000/svg}"
PADDLE_PYTHON = Path(r"D:\paddle ocr\env\python.exe")
OCR_CONFIG = common.PROJECT_ROOT / "legacy" / "ocr-config.json"
FIGURE_LINT = common.PROJECT_ROOT / "tools" / "figure_lint.py"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum() or ch in "τπƒαβγδεζηθικλμνξρσυφχψω")


def _svg_texts(svg_path: Path) -> list[str]:
    root = ET.parse(svg_path).getroot()
    texts: list[str] = []
    for element in root.iter(f"{SVG_NS}text"):
        parts = [element.text or ""]
        for tspan in element:
            parts.append(tspan.text or "")
            parts.append(tspan.tail or "")
        joined = "".join(parts).strip()
        if joined:
            texts.append(joined)
    return texts


def _match_texts(
    svg_texts: list[str], ocr_texts: list[str]
) -> tuple[list[str], list[str]]:
    """归一化精确 + 包含匹配，剩余项再做 difflib 模糊匹配（OCR l/I/破折号噪声容忍）。"""
    import difflib

    ocr_norm = [(t, _normalize(t)) for t in ocr_texts if _normalize(t)]
    svg_norm = [(t, _normalize(t)) for t in svg_texts if _normalize(t)]
    used_ocr: set[int] = set()
    unmatched_svg: list[str] = []
    for text, norm in svg_norm:
        hit = None
        for idx, (_, onorm) in enumerate(ocr_norm):
            if norm == onorm or (len(norm) >= 4 and norm in onorm) or (len(onorm) >= 4 and onorm in norm):
                hit = idx
                break
        if hit is None:
            unmatched_svg.append((text, norm))
        else:
            used_ocr.add(hit)

    # 模糊轮：SVG 剩余项与 OCR 剩余项做最佳比率匹配
    remaining_ocr = [(idx, text, norm) for idx, (text, norm) in enumerate(ocr_norm) if idx not in used_ocr]
    final_unmatched_svg: list[str] = []
    for text, norm in unmatched_svg:
        best_idx, best_ratio = None, 0.0
        for idx, _, onorm in remaining_ocr:
            if idx in used_ocr:
                continue
            ratio = difflib.SequenceMatcher(None, norm, onorm).ratio()
            if ratio > best_ratio:
                best_idx, best_ratio = idx, ratio
        if best_idx is not None and best_ratio >= 0.8:
            used_ocr.add(best_idx)
        else:
            final_unmatched_svg.append(text)
    unmatched_ocr = [text for idx, (text, _) in enumerate(ocr_norm) if idx not in used_ocr]
    return final_unmatched_svg, unmatched_ocr


def _run_ocr(run: common.Run, out_json: Path) -> list[str]:
    if not PADDLE_PYTHON.is_file():
        raise common.fail(f"Paddle 解释器不存在: {PADDLE_PYTHON}")
    helper = Path(__file__).with_name("ocr_texts.py")
    command = [
        str(PADDLE_PYTHON), "-I", "-B", "-X", "utf8",
        str(helper), str(OCR_CONFIG), str(run.source_png), str(out_json),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if result.returncode != 0 or not out_json.is_file():
        tail = (result.stderr or result.stdout or "")[-800:]
        raise common.fail(f"OCR 执行失败（这步只读 Paddle runtime，不重装模型）:\n{tail}")
    return json.loads(out_json.read_text(encoding="utf-8"))


def _run_figure_lint(run: common.Run) -> dict:
    out_png = run.qa_dir / "diff.png"
    command = [
        sys.executable, "-B", "-X", "utf8", str(FIGURE_LINT),
        str(run.source_png), str(run.render_png), "--diff-out", str(out_png), "--pretty",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    try:
        metrics = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise common.fail(f"figure_lint 输出解析失败:\n{(result.stderr or '')[-500:]}") from exc
    (run.qa_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metrics


def _build_preview(run: common.Run) -> Path:
    from PIL import Image, ImageDraw

    with Image.open(run.source_png) as ref, Image.open(run.render_png) as ren:
        ref_img, ren_img = ref.convert("RGB"), ren.convert("RGB")
        width = max(ref_img.width, ren_img.width)
        height = ref_img.height + ren_img.height + 30
        canvas = Image.new("RGB", (width, height), (220, 20, 20))
        canvas.paste(ref_img, (0, 20))
        canvas.paste(ren_img, (0, ref_img.height + 30))
        draw = ImageDraw.Draw(canvas)
        draw.text((4, 4), "REFERENCE", fill=(255, 255, 255))
        draw.text((4, ref_img.height + 24), "RENDER", fill=(255, 255, 255))
        out = run.qa_dir / "preview.png"
        canvas.save(out)
        return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure check", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--skip-ocr", action="store_true", help="跳过 Paddle OCR 文本比对")
    parser.add_argument("--re-ocr", action="store_true", help="忽略缓存的 OCR 结果重新识别")
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    if not run.pptx_path.is_file() or not run.render_png.is_file():
        raise common.fail("缺少 PPTX 或 render.png，请先运行 autofigure convert")
    run.qa_dir.mkdir(exist_ok=True)

    metrics = _run_figure_lint(run)
    preview = _build_preview(run)

    unmatched_svg: list[str] = []
    unmatched_ocr: list[str] = []
    if not args.skip_ocr:
        ocr_json = run.qa_dir / "ocr-texts.json"
        if args.re_ocr or not ocr_json.is_file():
            _run_ocr(run, ocr_json)
        ocr_texts = json.loads(ocr_json.read_text(encoding="utf-8"))
        unmatched_svg, unmatched_ocr = _match_texts(_svg_texts(run.redraw_svg), ocr_texts)

    report = run.qa_dir / "check-report.md"
    lines = [
        f"# check 报告（advisory，非门禁） — {run.root.name}",
        "",
        "## 像素诊断（figure_lint，软信号）",
        f"- mean_abs_rgb_delta: {metrics.get('mean_abs_rgb_delta')}",
        f"- changed_pixel_ratio: {metrics.get('changed_pixel_ratio_pct')}%",
        f"- top_roi: {metrics.get('top_roi')}",
        f"- ssim: {metrics.get('ssim')}",
        f"- diff 图: {run.qa_dir / 'diff.png'}",
        f"- 对照预览: {preview}",
        "",
        "## 文本比对（SVG 文字 vs 参考图 OCR）",
        f"- SVG 侧未匹配 {len(unmatched_svg)} 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）",
        f"- OCR 侧未匹配 {len(unmatched_ocr)} 条（可能：VLM 漏画 / OCR 误识）",
        "",
        "### SVG 侧未匹配",
        *[f"- {t}" for t in unmatched_svg],
        "",
        "### OCR 侧未匹配",
        *[f"- {t}" for t in unmatched_ocr],
        "",
        "> OCR 对公式/上下标本身不可靠，逐条人工判断，不以本报告自动放行或拦截。",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sys.stdout.write(f"像素诊断 mean={metrics.get('mean_abs_rgb_delta')} top_roi_loss={metrics.get('top_roi', {}).get('loss_contribution_pct')}%\n")
    sys.stdout.write(f"文本比对: SVG 侧未匹配 {len(unmatched_svg)} / OCR 侧未匹配 {len(unmatched_ocr)}\n")
    sys.stdout.write(f"报告: {report}\n预览: {preview}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
