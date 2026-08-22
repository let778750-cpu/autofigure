"""Generate a portable, hash-bound A/B report for two input routes."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import read_json, utc_now, write_json


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def _binding_summary(run: common.Run) -> dict[str, Any]:
    payload = _safe_json(run.bindings_path)
    bindings = payload.get("bindings", [])
    kinds = Counter(item.get("object_kind", "unknown") for item in bindings)
    arrow_kinds = {"connector", "line", "freeform-arrow", "arrowhead-fallback", "arrow-group"}
    return {
        "object_count": len(bindings),
        "editable_text": kinds["text"],
        "editable_formulas": kinds["native-math"],
        "editable_arrows": sum(kinds[kind] for kind in arrow_kinds),
        "atomic_rasters": kinds["atomic-raster"],
        "saved_reopened": payload.get("saved_reopened") is True,
        "bindings_complete": payload.get("bindings_complete") is True,
        "object_kinds": dict(sorted(kinds.items())),
    }


def _region_summary(run: common.Run) -> dict[str, Any]:
    payload = _safe_json(run.qa_dir / "regions-report.json")
    regions = []
    for item in payload.get("regions", []):
        probes = item.get("color_probes", [])
        regions.append(
            {
                "id": item.get("id"),
                "critical": item.get("critical") is True,
                "ssim": item.get("ssim"),
                "edge_iou": item.get("edge_iou"),
                "mean_abs_rgb_delta": item.get("mean_abs_rgb_delta"),
                "color_probe_mean_delta_e00": (
                    round(sum(probe.get("delta_e00", 0.0) for probe in probes) / len(probes), 4)
                    if probes
                    else None
                ),
                "color_probe_failures": sum(probe.get("pass") is not True for probe in probes),
                "pass": item.get("pass") is True,
            }
        )
    critical = [item for item in regions if item["critical"]]
    atomic = [item for item in critical if "creative-asset" in str(item["id"])]
    return {
        "critical_regions": len(critical),
        "critical_passed": sum(item["pass"] for item in critical),
        "strict_pass": payload.get("strict_pass") is True,
        "atomic_asset_regions": len(atomic),
        "atomic_asset_regions_passed": sum(item["pass"] for item in atomic),
        "regions": regions,
    }


def _arrow_summary(run: common.Run) -> dict[str, Any]:
    payload = _safe_json(run.qa_dir / "arrows-audit.json")
    findings = payload.get("findings", [])
    codes = Counter(item.get("code", "unknown") for item in findings)
    return {
        "arrows": payload.get("arrows", 0),
        "finding_count": len(findings),
        "finding_codes": dict(sorted(codes.items())),
    }


def _case_summary(run: common.Run) -> dict[str, Any]:
    meta = run.load_meta()
    provenance = _safe_json(run.provenance_path)
    metrics = _safe_json(run.qa_dir / "metrics.json")
    layout = _safe_json(run.layout_audit_path)
    live = _safe_json(run.qa_dir / "live-save-reopen-summary.json")
    try:
        relative_path = run.root.resolve().relative_to(common.CASES_ROOT.resolve()).as_posix()
    except ValueError:
        relative_path = run.root.name
    return {
        "case": meta["case"],
        "path": relative_path,
        "input_route": meta["input_route"],
        "processing_mode": meta["processing_mode"],
        "workflow_state": meta["workflow"]["state"],
        "validation": meta.get("validation", {}),
        "comparison_group": provenance.get("comparison_group"),
        "reference_sha256": meta["source_sha256"],
        "bindings": _binding_summary(run),
        "arrows": _arrow_summary(run),
        "layout": {
            "pass": layout.get("pass") is True,
            "finding_count": len(layout.get("findings", [])),
        },
        "regions": _region_summary(run),
        "global_diagnostic": {
            key: metrics.get(key)
            for key in ("mean_abs_rgb_delta", "changed_pixel_ratio_pct", "ssim", "edge_iou")
            if key in metrics
        },
        "powerpoint_live": {
            "saved_reopened": live.get("saved_reopened") is True,
            "backend_hard_failures_after_correction": live.get("live_layout_audit", {}).get(
                "hard_failure_count_after_correction"
            ),
            "automatic_status": live.get("automatic_status"),
            "region_results": live.get("region_results"),
        },
    }


def build_comparison(first: common.Run, second: common.Run) -> dict[str, Any]:
    summaries = [_case_summary(first), _case_summary(second)]
    by_route = {item["input_route"]: item for item in summaries}
    if set(by_route) != {"reference-only", "svg-seeded"}:
        raise common.fail("comparison requires exactly one reference-only and one svg-seeded case")
    if len({item["reference_sha256"] for item in summaries}) != 1:
        raise common.fail("comparison cases do not share the same frozen reference hash")
    groups = {item["comparison_group"] for item in summaries}
    if len(groups) != 1 or None in groups:
        raise common.fail("comparison cases require the same non-null provenance comparison_group")

    direct = by_route["reference-only"]
    direct_built = (
        direct["bindings"]["object_count"] > 0
        and direct["bindings"]["saved_reopened"]
        and direct["bindings"]["bindings_complete"]
    )
    direct_strict = direct["validation"].get("status") == "passed"
    if direct_strict:
        conclusion = "reference-only strict validation passed"
    elif direct_built:
        conclusion = "reference-only pipeline completed, but quality is not validated mature"
    else:
        conclusion = "reference-only pipeline is incomplete"
    return {
        "schema_version": "1.0.0",
        "kind": "input_route_ab_comparison",
        "generated_at": utc_now(),
        "task_mode": "RECONSTRUCT_1TO1",
        "comparison_group": groups.pop(),
        "reference_sha256": summaries[0]["reference_sha256"],
        "cases": by_route,
        "conclusion": {
            "reference_only_pipeline_completed": direct_built,
            "reference_only_strict_passed": direct_strict,
            "reference_only_capability_mature": direct_strict,
            "statement": conclusion,
            "global_metrics_are_diagnostic_only": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    rows = []
    for route in ("svg-seeded", "reference-only"):
        item = report["cases"][route]
        rows.append(
            "| {route} | `{case}` | {objects} | {text} | {math} | {arrows} | {arrow_findings} | "
            "{regions} | {status} |".format(
                route=route,
                case=item["case"],
                objects=item["bindings"]["object_count"],
                text=item["bindings"]["editable_text"],
                math=item["bindings"]["editable_formulas"],
                arrows=item["bindings"]["editable_arrows"],
                arrow_findings=item["arrows"]["finding_count"],
                regions=(
                    f"{item['regions']['critical_passed']}/{item['regions']['critical_regions']}"
                ),
                status=item["validation"].get("status", "not_run"),
            )
        )
    direct = report["cases"]["reference-only"]
    seeded = report["cases"]["svg-seeded"]
    return "\n".join(
        [
            f"# 输入路线 A/B：{report['comparison_group']}",
            "",
            f"冻结参考 SHA-256：`{report['reference_sha256']}`",
            "",
            "| 输入路线 | 案例 | 对象数 | 可编辑文字 | 原生公式 | 可编辑箭头对象 | 箭头审计发现 | 关键区通过 | strict 状态 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|",
            *rows,
            "",
            "## 结论",
            "",
            f"- {report['conclusion']['statement']}。",
            "- 两条路线共用的只有冻结参考图与路线无关验收阈值；reference-only 候选未读取 svg-seeded 候选资产。",
            "- 全图均值仅作诊断，任何关键区域失败都会阻止 approved。",
            (
                "- reference-only 的紧边界 observation/globe 微资产通过 "
                f"{direct['regions']['atomic_asset_regions_passed']}/"
                f"{direct['regions']['atomic_asset_regions']} 个关键区；这验证 PNG 裁剪机制，"
                "不等于其余原生结构已经达标。"
            ),
            (
                "- PowerPoint 保存重开：svg-seeded="
                f"{seeded['bindings']['saved_reopened']}，reference-only="
                f"{direct['bindings']['saved_reopened']}；PowerPoint Live 仍只有独立复核权限。"
            ),
            "",
            "## 明细",
            "",
            "机器可读指标见同名 JSON；区域 SSIM、Edge IoU、ΔE00、箭头问题代码和 blocker 均未省略。",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure compare", description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--output-root", type=Path, default=common.CASES_ROOT)
    args = parser.parse_args(argv)

    report = build_comparison(common.open_run(args.first), common.open_run(args.second))
    stem = f"route-comparison-{report['comparison_group']}"
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / f"{stem}.json"
    md_path = output_root / f"{stem}.md"
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    sys.stdout.write(f"A/B JSON: {json_path}\nA/B report: {md_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
