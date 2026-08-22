"""Deterministic negative-echo scan over deliverable markdown documents.

交付物只陈述最终采用且已验证的状态(SKILL.md 原则 7)。本命令扫描仓库 markdown
中的纠正过程叙事残留(修复前后对照、对已修复旧实现的批评等);修复过程与防重复
踩坑教训的合法归宿是 history/ ADR,不在扫描范围。合同性约束(禁令/必须)与确
定性工具指引(--fix/--calibrate)行是边界而非残留,予以豁免。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from tools import common

# 不扫描的目录:ADR 归宿、已归档 v1、会话与虚拟环境目录。
EXCLUDED_DIRS = frozenset(
    {"history", "legacy", ".claude", "plans", ".git", ".venv", "node_modules", "__pycache__"}
)

# (规则名, 行级正则) — 纠正过程叙事模式。
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fix-before-after", re.compile(r"修复前|修复后")),
    ("no-longer", re.compile(r"不再|已去掉|已删除")),
    ("old-implementation", re.compile(r"旧转换器|旧管线|优于旧")),
    ("correction-history", re.compile(r"曾因此")),
)

# "本轮修复"类叙事仅在 QA 状态/检查报告文件中额外适用。
ROUND_FIX = re.compile(r"本轮.{0,6}修复")
ROUND_FIX_FILES = re.compile(r"QA_STATUS|check-report")

# 行级豁免:合同性约束与确定性工具指引。
CONSTRAINT_LINE = re.compile(r"禁止|不得|必须")
TOOL_GUIDE_LINE = re.compile(r"--fix|--calibrate")


def scan_file(path: Path, root: Path) -> list[str]:
    """返回单个 markdown 文件的负面回声发现;行级豁免在此应用。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"negative-echo:unreadable:{path.relative_to(root)}:{exc}"]
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if CONSTRAINT_LINE.search(line) or TOOL_GUIDE_LINE.search(line):
            continue
        matched = [name for name, pattern in RULES if pattern.search(line)]
        if ROUND_FIX_FILES.search(path.name) and ROUND_FIX.search(line):
            matched.append("round-fix")
        relative = path.relative_to(root).as_posix()
        findings.extend(
            f"negative-echo:{name}:{relative}:{lineno}:{line.strip()}" for name in matched
        )
    return findings


def scan_root(root: Path) -> list[str]:
    """递归扫描 root 下(排除 EXCLUDED_DIRS)的全部 markdown 文件。"""
    if not root.is_dir():
        raise SystemExit(f"hygiene: root is not a directory: {root}")
    findings: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        findings.extend(scan_file(path, root))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure hygiene", description=__doc__)
    parser.add_argument("--root", type=Path, default=common.PROJECT_ROOT)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出发现清单")
    args = parser.parse_args(argv)

    findings = scan_root(args.root.resolve())
    if args.json:
        sys.stdout.write(json.dumps({"findings": findings}, ensure_ascii=False, indent=2) + "\n")
    else:
        for finding in findings:
            sys.stderr.write(f"ERROR {finding}\n")
        if not findings:
            sys.stdout.write("hygiene: no negative-echo findings\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
