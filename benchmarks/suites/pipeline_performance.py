"""Pipeline 性能基准套件（Case05 gate 阶梯 + 跨案例管线基线）— Issue #19.

原 ``run_case05.py`` 更名归位：其真实职责从来不是"Case05 专属脚本"，
而是跨案例的 pipeline benchmark runner（8 个正式案例、两条输入路线）。
三层结构（全部在外部临时目录执行，不改写 examples/ 正式案例证据）：

1. **Case05 source-gate 微基准（修复阶梯）**：三个候选变体各自过
   `evaluate_source_gate`——原始 external-seed.svg、external-seed-repaired.svg
   （几何修复、未盖章语义合同）、external-seed-repaired-stamped（盖章语义合同），
   预期决策依次为 repair→repair(仅剩语义)→accept。gate 可独立于建案执行
   （显式传入期望哈希/画布/语义元数据）。
2. **确定性核心管线基线**：全部正式案例（两条输入路线）的临时副本上
   顺序执行 convert → math → check(standard)，记录逐阶段耗时/资源与产物哈希。
   这是"项目当前性能"的真实基线；案例级 Case05 基准见下。

**结构性边界（如实记录）**：当前管线的 freeze/inventory 授权
（reference-inventory 的对象盘点、typography、arrow visual contracts）按项目
范式必须经视觉审阅（`zero_count_authorizations` 的 basis 固定为
`full-reference-review`），且 normalize_source/ingest 均以冻结 inventory 为前置，
无法在无视觉能力的会话中诚实合成。Case05 的案例级
（建案→freeze→ingest→convert→math→check）基准在合同集编写完成后补跑；
reference-only 路线作者候选同理。

用法：
    python benchmarks/suites/pipeline_performance.py                    # 全部层
    python benchmarks/suites/pipeline_performance.py --tiers gate       # 仅指定层（gate/pipeline）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import psutil

BENCH_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BENCH_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.core.contracts import read_json  # noqa: E402
from tools.pipeline import check as check_mod  # noqa: E402
from tools.pipeline import convert as convert_mod  # noqa: E402
from tools.pipeline import math as math_mod  # noqa: E402
from tools.qa.source_gate import evaluate_source_gate  # noqa: E402

FIXTURE_DIR = BENCH_ROOT / "fixtures" / "05-sting-autophagy"
RESULTS_JSON = BENCH_ROOT / "results" / "pipeline-suite.json"
RESULTS_MD = BENCH_ROOT / "results" / "pipeline-suite.md"
REFERENCE_SHA = "ef0e94b0ee05e3af383f0b9a6f28dea40b504daa001d8ac561dc363ee3770240"
# reference.png 的单一真值在 examples 正式案例（Examples own truth）；fixture
# 目录的历史字节相同副本已删除，这里以 manifest 引用 + SHA 锁定。
EXAMPLE_REFERENCE = (
    PROJECT_ROOT / "examples" / "svg-seeded" / "05-sting-autophagy" / "reference.png"
)
CASES = (
    "svg-seeded/01-modular-agent",
    "svg-seeded/02-thinking-diffusion",
    "svg-seeded/03-llmind",
    "svg-seeded/04-pareto-conditioned-diffusion",
    "svg-seeded/05-sting-autophagy",
    "reference-only/01-modular-agent-reference-only",
    "reference-only/02-thinking-diffusion-reference-only",
    "reference-only/04-pareto-conditioned-diffusion-reference-only",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture() -> dict:
    fixture = read_json(FIXTURE_DIR / "fixture.json")
    for name, record in fixture["immutable_inputs"].items():
        # 案例事实（如 reference.png）以 path 字段引用 examples 正式案例的
        # 单一真值（manifest 引用 + SHA 锁定）；推导链物证仍留在 fixture 目录。
        if record.get("path"):
            path = PROJECT_ROOT / record["path"]
        else:
            path = FIXTURE_DIR / name
        if not path.is_file():
            raise SystemExit(f"benchmark: fixture missing: {name} -> {path}")
        actual = sha256_file(path)
        if actual != record["sha256"]:
            raise SystemExit(f"benchmark: fixture hash drift: {name}: {actual}")
    return fixture


class ResourceMonitor:
    """Sample the current process tree RSS while a callable executes."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.proc = psutil.Process(os.getpid())
        self._stop = threading.Event()
        self.peak_rss = 0
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                total = self.proc.memory_info().rss
                for child in self.proc.children(recursive=True):
                    try:
                        total += child.memory_info().rss
                    except psutil.Error:
                        continue
                self.peak_rss = max(self.peak_rss, total)
            except psutil.Error:
                return
            self._stop.wait(self.interval)

    def __enter__(self) -> "ResourceMonitor":
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def measure(fn, *args, **kwargs) -> dict:
    io_before = psutil.Process(os.getpid()).io_counters()
    cpu_before = time.process_time()
    monitor = ResourceMonitor()
    wall_before = time.perf_counter()
    with monitor:
        result = fn(*args, **kwargs)
    wall = time.perf_counter() - wall_before
    cpu = time.process_time() - cpu_before
    io_after = psutil.Process(os.getpid()).io_counters()
    metrics = {
        "wall_seconds": round(wall, 4),
        "cpu_seconds": round(cpu, 4),
        "peak_rss_bytes": monitor.peak_rss,
        "read_bytes": io_after.read_bytes - io_before.read_bytes,
        "write_bytes": io_after.write_bytes - io_before.write_bytes,
    }
    return metrics, result


def stats_of(values: list[float]) -> dict:
    return {
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def stamp_semantic_contract(candidate: Path, target: Path) -> Path:
    """Stamp the six semantic attributes with fixture-declared values.

    object_inventory_sha256 使用 fixture 声明的占位 inventory 哈希（32 个 '0'
    之外的合法 digest 形式：本基准在 gate 层直接传入一致的 expected 值，建案
    场景则由 runner 在 freeze 后以真实 receipt 盖章——两种形态都在报告中标明）。
    """

    root = ET.fromstring(candidate.read_text(encoding="utf-8"))
    # SVG_AUTHORING_CONTRACT 第 1 条：语义六属性由 <svg> 根元素携带。
    target_element = root
    placeholder_inventory = hashlib.sha256(b"case05-benchmark-fixture-inventory").hexdigest()
    target_element.set("data-source-schema-version", "4.0.0")
    target_element.set("data-case", "05-sting-autophagy-bench")
    target_element.set("data-reference-sha256", REFERENCE_SHA)
    target_element.set("data-object-inventory-sha256", placeholder_inventory)
    target_element.set("data-stable-element-ids", "true")
    target_element.set("data-relations-exhaustive", "true")
    body = ET.tostring(root, encoding="unicode")
    target.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n", encoding="utf-8")
    return target


def tier_gate(workspace: Path) -> dict:
    repaired = FIXTURE_DIR / "external-seed-repaired.svg"
    repaired_stamped = stamp_semantic_contract(
        repaired, workspace / "external-seed-repaired-stamped.svg"
    )
    placeholder_inventory = hashlib.sha256(b"case05-benchmark-fixture-inventory").hexdigest()
    semantic = {
        "semantic_schema_version": "4.0.0",
        "case": "05-sting-autophagy-bench",
        "reference_sha256": REFERENCE_SHA,
        "object_inventory_sha256": placeholder_inventory,
        "stable_element_ids": True,
        "relations_exhaustive": True,
    }
    results = {}
    for label, candidate, metadata in (
        ("original-seed", FIXTURE_DIR / "external-seed.svg", None),
        ("repaired-seed-unstamped", repaired, None),
        ("repaired-seed-stamped", repaired_stamped, semantic),
    ):
        decisions = []
        walls = []
        blockers: list[str] = []
        for _ in range(5):
            metrics, report = measure(
                evaluate_source_gate,
                candidate,
                reference_path=EXAMPLE_REFERENCE,
                input_route="svg-seeded",
                candidate_role="external-seed",
                expected_reference_sha256=REFERENCE_SHA,
                expected_canvas=(2100, 1324),
                semantic_metadata=metadata,
                expected_case="05-sting-autophagy-bench",
                expected_inventory_sha256=placeholder_inventory,
                expected_candidate_sha256=sha256_file(candidate),
            )
            decisions.append(report["decision"])
            walls.append(metrics["wall_seconds"])
            blockers = report.get("blockers", [])
        results[label] = {
            "decision": decisions[0],
            "decisions_stable": len(set(decisions)) == 1,
            "blockers": blockers[:20],
            "wall_seconds_stats": stats_of(walls),
        }
    return results


def tier_pipeline(workspace: Path) -> dict:
    out: dict[str, dict] = {}
    for case_rel in CASES:
        source = PROJECT_ROOT / "examples" / case_rel
        target = workspace / "examples" / case_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        run_dir = target
        stages: dict[str, dict] = {}
        stages["convert"] = measure(convert_mod.main, [str(run_dir)])[0]
        stages["math"] = measure(math_mod.main, [str(run_dir)])[0]
        stages["check"] = measure(
            check_mod.main, [str(run_dir), "--profile", "standard"]
        )[0]
        out[case_rel] = {
            "stages": stages,
            "artifact_sha256": {
                name: sha256_file(run_dir / name)
                for name in ("redraw.pptx", "bindings.json", "render.png")
                if (run_dir / name).is_file()
            },
        }
        sys.stdout.write(
            f"benchmark: pipeline {case_rel} convert={stages['convert']['wall_seconds']}s "
            f"math={stages['math']['wall_seconds']}s check={stages['check']['wall_seconds']}s\n"
        )
    return out


def render_markdown(payload: dict) -> str:
    lines = [
        "# Pipeline 基准报告（Case05 gate 阶梯 + 跨案例管线基线）",
        "",
        "生成自 pipeline-suite JSON；数字一律机器采集，单样本如实标注，不伪造分位数。",
        "",
        "## Fixture 校验",
        "",
        "| 输入 | SHA-256（前 16 位） |",
        "|---|---|",
    ]
    for name, digest in payload["fixture_verification"].items():
        lines.append(f"| `{name}` | `{digest[:16]}` |")
    lines += [
        "",
        "## 1. Case05 source-gate 修复阶梯（5 次采样）",
        "",
        "| 候选 | 决策 | wall median (s) | min | max |",
        "|---|---|---|---|---|",
    ]
    for label, row in payload["tiers"]["gate"].items():
        stats = row["wall_seconds_stats"]
        lines.append(
            f"| {label} | `{row['decision']}` | {stats['median']} | {stats['min']} | {stats['max']} |"
        )
    lines += [
        "",
        "## 2. 确定性核心管线基线（8 个正式案例副本，各 1 次 cold）",
        "",
        "| 案例 | convert (s) | math (s) | check (s) |",
        "|---|---|---|---|",
    ]
    for case_rel, row in payload["tiers"].get("pipeline", {}).items():
        stages = row["stages"]
        lines.append(
            f"| `{case_rel}` | {stages['convert']['wall_seconds']} "
            f"| {stages['math']['wall_seconds']} | {stages['check']['wall_seconds']} |"
        )
    case_level = payload.get("case_level", {})
    lines += [
        "",
        "## 案例级 Case05 状态",
        "",
        "- svg-seeded：`" + str(case_level.get("svg_seeded", {}).get("status"))
        + "` — " + str(case_level.get("svg_seeded", {}).get("note")),
        "- reference-only：`" + str(case_level.get("reference_only", {}).get("status"))
        + "` — " + str(case_level.get("reference_only", {}).get("note")),
        "",
        "测量边界：" + payload["environment"]["measurement_scope"],
        "",
        "性能基准不授予 `approved`；任何失败如实保留。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure-benchmark-pipeline", description=__doc__)
    parser.add_argument("--tiers", default="gate,normalize,pipeline")
    args = parser.parse_args(argv)

    fixture = verify_fixture()
    requested = {item.strip() for item in args.tiers.split(",") if item.strip()}
    tiers: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="autofigure-bench-case05-") as temp:
        workspace = Path(temp)
        if "gate" in requested:
            tiers["gate"] = tier_gate(workspace)
            sys.stdout.write("benchmark: gate tier done\n")
        if "pipeline" in requested:
            tiers["pipeline"] = tier_pipeline(workspace)
            sys.stdout.write("benchmark: pipeline tier done\n")

    payload = {
        "schema_version": "1.2.0",
        "kind": "benchmark_report",
        "suite": "pipeline-suite-v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "psutil": psutil.__version__,
            "measurement_scope": (
                "当前进程及其子进程树（RSS/IO/CPU-wall）；PowerPoint COM 服务器经 "
                "RPC 激活、不在子进程树内时其资源不计入——如实记录测量边界"
            ),
        },
        "fixture_verification": {
            name: record["sha256"] for name, record in fixture["immutable_inputs"].items()
        },
        "tiers": tiers,
        "case_level": {
            "svg_seeded": {
                "status": "implemented",
                "case": "examples/svg-seeded/05-sting-autophagy",
                "note": (
                    "完整案例已落地（prepare→合同生成→freeze 391 对象→ingest 盖章变体"
                    " gate accept→convert→math→check standard），管线数字见 pipeline 层"
                    " svg-seeded/05-sting-autophagy 行。"
                ),
            },
            "reference_only": {
                "status": "case-frozen-awaiting-candidate",
                "case": "examples/reference-only/05-sting-autophagy-reference-only",
                "note": (
                    "案例合同已冻结；重绘候选须仅依据 fixture reference 由视觉执行者"
                    "产出，候选落地后补跑该路线管线与作者阶段耗时（Issue #19）。"
                ),
            },
        },
    }

    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    RESULTS_MD.write_text(render_markdown(payload), encoding="utf-8")
    sys.stdout.write(f"benchmark: wrote {RESULTS_JSON}\nbenchmark: wrote {RESULTS_MD}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
