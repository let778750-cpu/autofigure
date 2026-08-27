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
