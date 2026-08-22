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

from tools import common

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
    metrics["diff_out"] = "qa/diff.png"
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
        out = run.preview_png
        canvas.save(out)
        return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure check", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--skip-ocr", action="store_true", help="跳过 Paddle OCR 文本比对")
    parser.add_argument("--re-ocr", action="store_true", help="忽略缓存的 OCR 结果重新识别")
    parser.add_argument("--profile", choices=("standard", "strict"), default="standard")
    parser.add_argument("--require-live", action="store_true", help="strict 模式要求 PowerPoint-live 保存重开证据")
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    if not run.pptx_path.is_file() or not run.render_png.is_file():
        raise common.fail("缺少 PPTX 或 render.png，请先运行 autofigure convert")
    run.qa_dir.mkdir(exist_ok=True)

    metrics = _run_figure_lint(run)
    preview = _build_preview(run)

    from tools.regions import evaluate_regions

    regions = evaluate_regions(run)
    from tools.layout import audit_layout

    layout_report = audit_layout(run)

    # 每次 check 都对当前 SVG 重新审计，禁止复用陈旧箭头报告；保留既有原图校准表。
    arrows_json = run.qa_dir / "arrows-audit.json"
    calibrate: dict[str, float] = {}
    if arrows_json.is_file():
        old_audit = json.loads(arrows_json.read_text(encoding="utf-8"))
        calibrate = old_audit.get("calibrate", {})
    from tools.arrows import audit_svg_text

    arrow_audit = audit_svg_text(run.redraw_svg.read_text(encoding="utf-8"), calibrate=calibrate)
    arrows_json.write_text(
        json.dumps({"svg": "redraw.svg", "phase": "audit", **arrow_audit}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    unmatched_svg: list[str] = []
    unmatched_ocr: list[str] = []
    if not args.skip_ocr:
        ocr_json = run.qa_dir / "ocr-texts.json"
        if args.re_ocr or not ocr_json.is_file():
            _run_ocr(run, ocr_json)
        ocr_texts = json.loads(ocr_json.read_text(encoding="utf-8"))
        unmatched_svg, unmatched_ocr = _match_texts(_svg_texts(run.redraw_svg), ocr_texts)

    report = run.report_md
    lines = [
        f"# check 报告（{args.profile}） — {run.root.name}",
        "",
        "## 像素诊断（figure_lint，软信号）",
        f"- mean_abs_rgb_delta: {metrics.get('mean_abs_rgb_delta')}",
        f"- changed_pixel_ratio: {metrics.get('changed_pixel_ratio_pct')}%",
        f"- top_roi: {metrics.get('top_roi')}",
        f"- ssim: {metrics.get('ssim')}",
        "- diff 图: qa/diff.png",
        "- 对照预览: preview.png",
        f"- 关键区域 strict_pass: {regions['strict_pass']}（{regions['critical_regions']} 个关键区域）",
        "- 区域明细: qa/regions-report.json",
        f"- 布局合同: {'PASS' if layout_report['pass'] else 'FAIL'}（{len(layout_report['findings'])} 项）",
        "- 布局明细: qa/layout-audit.json",
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
    ]

    if arrows_json.is_file():
        from tools.arrows import render_report

        audit = json.loads(arrows_json.read_text(encoding="utf-8"))
        lines.extend(render_report(audit) + [""])

    from tools.contracts import read_json

    strict_blockers = list(regions.get("blockers", []))
    if args.profile == "strict" and regions.get("critical_regions", 0) == 0:
        strict_blockers.append("regions:no-critical-regions")
    from tools.layout import strict_blockers as layout_strict_blockers

    strict_blockers.extend(layout_strict_blockers(layout_report))
    strict_blockers.extend(
        f"arrow:{item['code']}:{item['element']}"
        for item in arrow_audit.get("findings", [])
        if item.get("code") in {"F1", "F2", "F3", "F5", "F6", "F7", "F8", "F9", "F10"}
    )
    assets = read_json(run.assets_path)
    strict_blockers.extend(
        f"asset:{item.get('id', '[missing-id]')}:authorization-unverified"
        for item in assets.get("assets", [])
        if item.get("source") == "reference_crop" and item.get("authorized") is not True
    )
    bindings = read_json(run.bindings_path)
    if bindings.get("artifact_sha256") != common.sha256_file(run.pptx_path):
        strict_blockers.append("bindings:artifact-hash-mismatch")
    if bindings.get("saved_reopened") is not True:
        strict_blockers.append("bindings:save-reopen-not-verified")
    if bindings.get("bindings_complete") is not True:
        strict_blockers.append("bindings:incomplete")
    require_live = args.require_live or run.load_meta().get("backend_mode") == "hybrid"
    if args.profile == "strict" and require_live:
        from tools.repair import live_evidence_passes

        failed_regions = [
            item["id"] for item in regions["regions"] if item["critical"] and not item["pass"]
        ]
        _, live_blockers = live_evidence_passes(run, failed_regions)
        strict_blockers.extend(live_blockers)
    strict_blockers = list(dict.fromkeys(strict_blockers))

    from tools.contracts import record_validation

    record_validation(run, args.profile, strict_blockers)

    lines.extend(
        [
            "",
            f"## 验收状态（{args.profile}）",
            f"- blockers: {len(strict_blockers)}",
            *[f"- {item}" for item in strict_blockers],
            "",
        ]
    )
    if args.profile == "standard":
        lines.append("> standard 结果为诊断；只有 strict 零 blocker 才能进入 approved。")
    else:
        lines.append("> strict 使用关键区域、箭头结构与可选 live 回读共同门禁；全图均值不能覆盖局部失败。")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sys.stdout.write(f"像素诊断 mean={metrics.get('mean_abs_rgb_delta')} top_roi_loss={metrics.get('top_roi', {}).get('loss_contribution_pct')}%\n")
    sys.stdout.write(f"文本比对: SVG 侧未匹配 {len(unmatched_svg)} / OCR 侧未匹配 {len(unmatched_ocr)}\n")
    sys.stdout.write(f"报告: {report}\n预览: {preview}\n")
    if args.profile == "strict":
        from tools.contracts import transition

        if strict_blockers:
            transition(run, "qa_failed", "strict-check-failed", details={"blockers": strict_blockers})
            return 2
        transition(run, "approved", "strict-check-passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
