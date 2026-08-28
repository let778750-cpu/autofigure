"""Drive the Case05 formal-case construction through the real pipeline. Issue #19.

前置：examples/svg-seeded/05-sting-autophagy 已 prepare + 生成合同 + freeze 成功
（见 build_case05_contracts.py 与 README 流程）。本脚本执行剩余真实管线步骤：

1. 以 freeze receipt 的 inventory_sha256 给修复 seed 的副本盖章语义合同
2. ingest（repair-candidate；原 seed 在 freeze 时获 repair 决策——几何修复已过、
   语义待盖章，诚实走 svg_repair 通道）
3. convert（PowerPoint COM fresh render）→ math → check(standard)

用法：
    python benchmarks/bootstrap/build_case05_cases.py <case_dir> ingest
    python benchmarks/bootstrap/build_case05_cases.py <case_dir> pipeline
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BENCH_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DIR = BENCH_ROOT / "fixtures" / "05-sting-autophagy"
REFERENCE_SHA = "ef0e94b0ee05e3af383f0b9a6f28dea40b504daa001d8ac561dc363ee3770240"


def stamp_candidate(case_dir: Path, target: Path) -> Path:
    from tools.core.contracts import read_json

    receipt = read_json(case_dir / "qa" / "reference-inventory-receipt.json")
    meta = read_json(case_dir / "run.json")
    root = ET.fromstring((FIXTURE_DIR / "external-seed-repaired.svg").read_text(encoding="utf-8"))
    # SVG_AUTHORING_CONTRACT 第 1 条：语义六属性由 <svg> 根元素携带。
    root.set("data-source-schema-version", "4.0.0")
    root.set("data-case", meta["case"])
    root.set("data-reference-sha256", meta["source_sha256"])
    root.set("data-object-inventory-sha256", receipt["inventory_sha256"])
    root.set("data-stable-element-ids", "true")
    root.set("data-relations-exhaustive", "true")
    body = ET.tostring(root, encoding="unicode")
    target.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def main(argv: list[str] | None = None) -> int:
    from tools.core import common
    from tools.pipeline import check as check_mod
    from tools.pipeline import convert as convert_mod
    from tools.pipeline import ingest as ingest_mod
    from tools.pipeline import math as math_mod

    case_dir = Path(argv[0] if argv else ".")
    step = argv[1] if len(argv) > 1 else "all"
    run = common.open_run(case_dir)

    if step in ("ingest", "all"):
        stamped = stamp_candidate(run.root, run.root / "stamped-repaired-seed.svg")
        code = ingest_mod.main(
            [
                str(run.root),
                str(stamped),
                "--kind",
                "svg",
                "--candidate-role",
                "repair-candidate",
                "--candidate-origin",
                "web-vlm",
            ]
        )
        sys.stdout.write(f"build_case05_cases: ingest exit={code}\n")
        if code != 0:
            return code
        staged_seed = run.root / "stamped-repaired-seed.svg"
        if staged_seed.is_file():
            staged_seed.unlink()  # 摄取完成后的临时盖章文件不留在案例根
    if step in ("pipeline", "all"):
        for name, module, args in (
            ("convert", convert_mod, [str(run.root)]),
            ("math", math_mod, [str(run.root)]),
            ("check", check_mod, [str(run.root), "--profile", "standard"]),
        ):
            code = module.main(args)
            sys.stdout.write(f"build_case05_cases: {name} exit={code}\n")
            if code not in (0, 2):
                return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
