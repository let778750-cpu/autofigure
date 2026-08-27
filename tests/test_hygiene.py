"""tools.qa.hygiene 的规则、豁免与 CLI 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from tools.qa import hygiene


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rules(findings: list[str]) -> set[str]:
    return {item.split(":")[1] for item in findings}


def test_clean_document_has_no_findings(tmp_path):
    _write(tmp_path, "README.md", "# 案例 01\n\n容器越界 `0 px`,中心距 `38/37 px`,布局报告 `PASS`。\n")
    assert hygiene.scan_root(tmp_path) == []


def test_fix_before_after_narrative_flagged(tmp_path):
    _write(tmp_path, "QA_STATUS.md", "- 修复前:越界 `27.96 px`;修复后:越界 `0 px`。\n")
    assert _rules(hygiene.scan_root(tmp_path)) == {"fix-before-after"}


def test_stale_implementation_references_flagged(tmp_path):
    _write(
        tmp_path,
        "notes.md",
        "旧转换器只裁到画布;优于旧管线;不再从语义猜测;已去掉占位;曾因此过度缩小。\n",
    )
    assert {"old-implementation", "no-longer", "correction-history"} <= _rules(
        hygiene.scan_root(tmp_path)
    )


def test_round_fix_restricted_to_status_reports(tmp_path):
    _write(tmp_path, "QA_STATUS.md", "本轮布局修复全部归零。\n")
    _write(tmp_path, "plain.md", "本轮布局修复全部归零。\n")
    assert _rules(hygiene.scan_root(tmp_path)) == {"round-fix"}


def test_constraint_line_is_exempt(tmp_path):
    _write(tmp_path, "SKILL.md", "交付物不携带修复前后对照;未验证项必须标注,安全边界必须保留。\n")
    assert hygiene.scan_root(tmp_path) == []


def test_tool_guide_line_is_exempt(tmp_path):
    _write(tmp_path, "check-report.md", "几何偏差可用 `autofigure arrows --fix` 确定性修复后重跑 convert。\n")
    assert hygiene.scan_root(tmp_path) == []


def test_history_and_legacy_are_excluded(tmp_path):
    _write(tmp_path, "history/adr.md", "修复前越界 `27.96 px`。\n")
    _write(tmp_path, "legacy/old.md", "优于旧管线的 30 轮迭代。\n")
    assert hygiene.scan_root(tmp_path) == []


def test_main_exit_codes(tmp_path):
    doc = _write(tmp_path, "doc.md", "修复后越界 `0 px`。\n")
    assert hygiene.main(["--root", str(tmp_path)]) == 1
    doc.write_text("越界 `0 px`。\n", encoding="utf-8")
    assert hygiene.main(["--root", str(tmp_path)]) == 0


def test_main_json_output(tmp_path, capsys):
    _write(tmp_path, "doc.md", "不再支持该写法。\n")
    assert hygiene.main(["--root", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert [item.split(":")[1] for item in payload["findings"]] == ["no-longer"]


def _make_workspace(tmp_path: Path) -> None:
    (tmp_path / "tools" / "__pycache__").mkdir(parents=True)
    (tmp_path / "tools" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".cache" / "ruff").mkdir(parents=True)
    (tmp_path / ".pytest-tmp-abc123").mkdir()
    (tmp_path / "somedir" / "__pycache__").mkdir(parents=True)
    (tmp_path / "loose.pyc").write_bytes(b"")
    (tmp_path / "keep.txt").write_text("stay\n", encoding="utf-8")
    (tmp_path / "random-dir").mkdir()
    # 受保护:虚拟环境、本地历史与未知正式目录永不进入。
    (tmp_path / ".venv" / "__pycache__").mkdir(parents=True)
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "adr.md").write_text("x", encoding="utf-8")
    # examples/ 内只允许 PowerPoint Live session build。
    build = tmp_path / "examples" / "svg-seeded" / "case" / "qa" / "powerpoint-live-case" / "build"
    build.mkdir(parents=True)
    (build / "candidates").mkdir()
    (tmp_path / "examples" / "svg-seeded" / "case" / "reference.png").write_bytes(b"png")


def test_workspace_discovery_lists_only_allowlist(tmp_path, capsys):
    _make_workspace(tmp_path)
    assert hygiene.main(["--root", str(tmp_path), "--workspace"]) == 0
    listed = {
        (line.split(": ", 1)[0], line.split(": ", 1)[1])
        for line in capsys.readouterr().out.splitlines()
        if ": " in line and not line.startswith("hygiene workspace")
    }
    paths = {path for _, path in listed}
    assert "tools/__pycache__" in paths
    assert ".pytest_cache" in paths
    assert ".cache" in paths
    assert ".pytest-tmp-abc123" in paths
    assert "somedir/__pycache__" in paths
    assert "loose.pyc" in paths
    assert "examples/svg-seeded/case/qa/powerpoint-live-case/build" in paths
    # 未知目录、正式内容与受保护目录绝不列出。
    assert "random-dir" not in paths
    assert "keep.txt" not in paths
    assert not any(path.startswith(".venv/") for path in paths)
    assert not any(path.startswith("history") for path in paths)
    assert not any(path.endswith("reference.png") for path in paths)


def test_workspace_clean_removes_only_allowlist(tmp_path, capsys):
    _make_workspace(tmp_path)
    assert hygiene.main(["--root", str(tmp_path), "--workspace", "--clean"]) == 0
    assert "removed 7" in capsys.readouterr().out
    assert not (tmp_path / "tools" / "__pycache__").exists()
    assert not (tmp_path / ".pytest_cache").exists()
    assert not (tmp_path / ".cache").exists()
    assert not (tmp_path / ".pytest-tmp-abc123").exists()
    assert not (tmp_path / "loose.pyc").exists()
    assert not (tmp_path / "examples" / "svg-seeded" / "case" / "qa" / "powerpoint-live-case" / "build").exists()
    # 受保护与未知内容原样保留。
    assert (tmp_path / ".venv" / "__pycache__").exists()
    assert (tmp_path / "history" / "adr.md").exists()
    assert (tmp_path / "random-dir").exists()
    assert (tmp_path / "keep.txt").exists()
    assert (tmp_path / "examples" / "svg-seeded" / "case" / "reference.png").exists()


def test_workspace_clean_rejects_out_of_root(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    real_root = tmp_path.resolve()
    outside = tmp_path.parent / "af-hygiene-outside"
    outside.mkdir(exist_ok=True)
    try:
        # 用越界 root 触发保护分支成本高;直接对解析保护函数做单元断言。
        assert hygiene._resolve_in_root(outside, real_root) is None
        assert hygiene._resolve_in_root(tmp_path / "__pycache__", real_root) is not None
    finally:
        outside.rmdir()
