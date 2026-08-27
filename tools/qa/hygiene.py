"""Deterministic negative-echo scan plus a fenced workspace cache cleaner.

交付物只陈述最终采用且已验证的状态(skill 原则 7)。本命令扫描仓库 markdown
中的纠正过程叙事残留(修复前后对照、对已修复旧实现的批评等);修复过程与防重复
踩坑教训的合法归宿是 history/ ADR,不在扫描范围。合同性约束(禁令/必须)与确
定性工具指引(--fix/--calibrate)行是边界而非残留,予以豁免。

``--workspace`` 切换为工作区缓存审计:只读列出项目根内允许清理的缓存/临时产物;
``--workspace --clean`` 仅删除 allowlist 中的条目,绝不进入 ``.venv``、``history/``、
案例输入或任何未知目录;符号链接、reparse point 与越界解析路径直接拒绝。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from tools.core import common

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
    parser.add_argument(
        "--workspace",
        action="store_true",
        help="审计项目根内允许清理的缓存/临时产物(只读)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="与 --workspace 连用:仅删除 allowlist 条目",
    )
    args = parser.parse_args(argv)

    if args.workspace:
        return workspace_main(args)

    findings = scan_root(args.root.resolve())
    if args.json:
        sys.stdout.write(json.dumps({"findings": findings}, ensure_ascii=False, indent=2) + "\n")
    else:
        for finding in findings:
            sys.stderr.write(f"ERROR {finding}\n")
        if not findings:
            sys.stdout.write("hygiene: no negative-echo findings\n")
    return 1 if findings else 0


# --- workspace cache audit -----------------------------------------------------------

# 目录名 allowlist:出现在任何深度都可以整体删除的缓存目录。
WORKSPACE_CACHE_DIR_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".cache"}
)

# 根级 allowlist:只匹配项目根下同名目录(git 已忽略的受控测试临时目录)。
WORKSPACE_ROOT_DIR_PATTERNS = re.compile(r"^\.pytest-tmp-.*$")

# PowerPoint Live 受控 session build 目录的相对路径后缀。
WORKSPACE_BUILD_SUFFIX = ("qa", "powerpoint-live-case", "build")

# 永不进入、永不删除的目录名(出现在路径任一层即跳过)。
WORKSPACE_PROTECTED_DIR_NAMES = frozenset(
    {".git", ".venv", "history", "node_modules", "legacy", "examples"}
)
# examples/ 整体受保护,但其 qa/powerpoint-live-case/build 子目录例外(见下)。

WORKSPACE_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})


def _is_reparse_or_symlink(path: Path) -> bool:
    """Windows reparse point / symlink / junction 检测;解析失败的保守视为越界。"""

    try:
        if path.is_symlink():
            return True
        st = path.lstat()
        return bool(st.st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def _resolve_in_root(path: Path, root: Path) -> Path | None:
    """Resolve path 并确认仍位于 root 内;reparse 链或越界返回 None。"""

    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def discover_workspace_cache(root: Path) -> list[dict[str, object]]:
    """枚举 root 内 allowlist 缓存条目;受保护目录整体跳过。"""

    root = root.resolve()
    entries: list[dict[str, object]] = []

    def protected(parts: tuple[str, ...]) -> bool:
        # examples/ 由专用分支处理(仅放行 PowerPoint Live session build)。
        guard = WORKSPACE_PROTECTED_DIR_NAMES - {"examples"}
        return any(part in guard for part in parts[:-1]) or (
            parts and parts[0] in guard
        )

    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        parts = rel.parts
        if not parts:
            continue
        if protected(parts):
            continue
        if parts[0] == "examples":
            # examples/ 内只允许 PowerPoint Live session build。
            if parts[-3:] == WORKSPACE_BUILD_SUFFIX and path.is_dir():
                entries.append({"path": rel.as_posix(), "kind": "live-build-dir"})
            continue
        if path.is_dir():
            if parts[-1] in WORKSPACE_CACHE_DIR_NAMES:
                entries.append({"path": rel.as_posix(), "kind": "cache-dir"})
            elif len(parts) == 1 and WORKSPACE_ROOT_DIR_PATTERNS.match(parts[0]):
                entries.append({"path": rel.as_posix(), "kind": "pytest-tmp-dir"})
            elif parts[-3:] == WORKSPACE_BUILD_SUFFIX:
                entries.append({"path": rel.as_posix(), "kind": "live-build-dir"})
        elif path.is_file() and path.suffix in WORKSPACE_FILE_SUFFIXES:
            entries.append({"path": rel.as_posix(), "kind": "bytecode-file"})

    return entries


def workspace_main(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"hygiene: root is not a directory: {root}")

    entries = discover_workspace_cache(root)
    if args.json:
        sys.stdout.write(
            json.dumps({"workspace": {"entries": entries, "clean": args.clean}},
                       ensure_ascii=False, indent=2) + "\n"
        )

    if args.clean:
        removed = 0
        for entry in entries:
            target = root / str(entry["path"])
            if _is_reparse_or_symlink(target):
                sys.stderr.write(f"SKIP reparse/symlink: {entry['path']}\n")
                continue
            resolved = _resolve_in_root(target, root)
            if resolved is None:
                sys.stderr.write(f"SKIP out-of-root resolve: {entry['path']}\n")
                continue
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=False)
            else:
                target.unlink()
            removed += 1
        sys.stdout.write(f"hygiene workspace: removed {removed} entr{'y' if removed == 1 else 'ies'}\n")
        return 0

    for entry in entries:
        sys.stdout.write(f"{entry['kind']}: {entry['path']}\n")
    sys.stdout.write(f"hygiene workspace: {len(entries)} cleanable entr{'y' if len(entries) == 1 else 'ies'} (use --clean to remove)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
