"""check 文本比对单元测试（纯函数，不触 OCR/PowerPoint）。"""

from __future__ import annotations

from pathlib import Path

from tools.core import common
from tools.pipeline.check import _match_texts, _normalize, _qa_report_hashes, _svg_texts


def test_normalize_strips_case_space_and_punct():
    assert _normalize("Task 1: Turn on lamp") == "task1turnonlamp"
    assert _normalize("  z_t+1  ") == "zt1"
    assert _normalize("τ") == "τ"


def test_match_exact_and_containment():
    svg = ["Task-Guided Expert Allocator", "mapping ƒ_map", "observation"]
    ocr = ["task-guided expert allocator", "mapping", "observation"]
    unmatched_svg, unmatched_ocr = _match_texts(svg, ocr)
    assert unmatched_svg == []
    assert unmatched_ocr == []


def test_match_reports_both_sides():
    svg = ["semantic expert", "typo-here"]
    ocr = ["semantic expert", "ocr-only text"]
    unmatched_svg, unmatched_ocr = _match_texts(svg, ocr)
    assert unmatched_svg == ["typo-here"]
    assert unmatched_ocr == ["ocr-only text"]


def test_svg_texts_joins_tspans(tmp_path: Path):
    svg = tmp_path / "t.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<text x="0" y="0"><tspan font-style="italic">z</tspan>'
        '<tspan baseline-shift="sub">t+1</tspan></text>'
        '<text x="1" y="1">plain</text></svg>',
        encoding="utf-8",
    )
    assert _svg_texts(svg) == ["zt+1", "plain"]


def test_repair_evidence_hashes_fresh_asset_spec_audit(tmp_path: Path):
    run = common.Run(tmp_path)
    run.qa_dir.mkdir()
    audit = run.qa_dir / "asset-spec-audit.json"
    audit.write_text('{"kind":"asset_spec_audit","pass":true}\n', encoding="utf-8")
    receipt = run.qa_dir / "asset-contract-receipt.json"
    receipt.write_text(
        '{"kind":"asset_contract_freeze_receipt","status":"PASS"}\n',
        encoding="utf-8",
    )

    hashes = _qa_report_hashes(run)

    assert hashes["asset-spec-audit.json"] == common.sha256_file(audit)
    assert hashes["asset-contract-receipt.json"] == common.sha256_file(receipt)


def test_qa_report_hashes_exclude_derived_qa_status(tmp_path: Path):
    # qa-status.json 是 check 尾段重写的派生读出，其哈希由 release manifest 绑定；
    # 进入本名单会使 repair plan 与 qa-status 互相哈希追逐。
    run = common.Run(tmp_path)
    run.qa_dir.mkdir()
    status = run.qa_dir / "qa-status.json"
    status.write_text('{"kind":"qa_status"}\n', encoding="utf-8")

    assert "qa-status.json" not in _qa_report_hashes(run)
