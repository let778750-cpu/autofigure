from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools.core import common
from tools.pipeline.check import _source_gate_blockers
from tools.qa.compare import build_comparison, render_markdown
from tools.core.contracts import read_json, write_json
from tools.pipeline.prepare import main as prepare_main
from tools.assets.reference_inventory import (
    OBJECT_KINDS,
    RECEIPT_PATH,
    freeze_inventory,
    inventory_blockers,
)
from tools.assets.reference_oracle import (
    load_oracle,
    oracle_matches,
    oracle_path,
    oracle_sha256,
    write_oracle,
)


def _reference(tmp_path: Path) -> Path:
    path = tmp_path / "reference.png"
    if not path.is_file():
        Image.new("RGB", (160, 100), "white").save(path)
    return path


def _run(tmp_path: Path, case: str, route: str) -> common.Run:
    cases_root = tmp_path / "examples"
    if route == "reference-only":
        assert (
            prepare_main(
                [
                    str(_reference(tmp_path)),
                    "--case",
                    case,
                    "--cases-root",
                    str(cases_root),
                    "--input-route",
                    "reference-only",
                ]
            )
            == 0
        )
        return common.open_run(cases_root / route / case)
    return common.create_run(
        _reference(tmp_path),
        case=case,
        cases_root=cases_root,
        input_route=route,
    )


def _configure_text_inventory(run: common.Run) -> None:
    payload = read_json(run.regions_path)
    payload["regions"] = [
        {
            "id": "title-region",
            "label": "Title",
            "bbox": [0, 0, 160, 50],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": ["title"],
        },
        {
            "id": "whole-canvas",
            "label": "Whole canvas (diagnostic only)",
            "bbox": [0, 0, 160, 100],
            "critical": False,
        },
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {kind: 0 for kind in OBJECT_KINDS}
    inventory["expected_counts"]["text"] = 1
    inventory["zero_count_authorizations"] = [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "oracle-test-reviewer",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("arrow", "icon", "brace")
    ]
    inventory["objects"] = [
        {
            "id": "title",
            "kind": "text",
            "bbox": [18, 10, 124, 28],
            "element_ids": ["title"],
            "critical_region_ids": ["title-region"],
            "typography": {
                "exact_text": "Frozen title",
                "font_family": "Arial",
                "font_size_px": 18,
                "font_weight": "normal",
                "font_style": "normal",
                "line_count": 1,
                "alignment": "left",
                "bbox_tolerance_px": 1,
                "font_size_tolerance_px": 0.5,
            },
        }
    ]
    write_json(run.regions_path, payload)


def _tamper_oracle_counts(run: common.Run) -> dict:
    """Rewrite the oracle with shifted counts but a self-consistent hash."""

    path = oracle_path(run)
    oracle = load_oracle(path)
    oracle["inventory"]["expected_counts"]["text"] += 1
    oracle["oracle_sha256"] = oracle_sha256(oracle)
    write_oracle(path, oracle)
    return oracle


def test_shared_reference_converges_on_one_oracle_across_routes(tmp_path: Path):
    direct = _run(tmp_path, "oracle-direct", "reference-only")
    _configure_text_inventory(direct)
    direct_receipt = freeze_inventory(direct)

    seeded = _run(tmp_path, "oracle-seeded", "svg-seeded")
    _configure_text_inventory(seeded)
    seeded_receipt = freeze_inventory(seeded)

    path = oracle_path(direct)
    assert path == oracle_path(seeded)
    assert path.is_file()
    assert path.parent.parent.name == "oracles"
    oracle = load_oracle(path)
    assert oracle["kind"] == "reference_oracle"
    assert oracle["reference_sha256"] == direct.load_meta()["source_sha256"]
    assert direct_receipt["oracle_sha256"] == oracle["oracle_sha256"]
    assert seeded_receipt["oracle_sha256"] == oracle["oracle_sha256"]
    assert oracle_matches(oracle, read_json(seeded.regions_path)["reference_inventory"])
    assert inventory_blockers(direct) == []
    assert inventory_blockers(seeded) == []

    # 真值未变时重 freeze 复用同一 oracle，不发生覆盖。
    assert freeze_inventory(direct)["oracle_sha256"] == oracle["oracle_sha256"]
    assert load_oracle(path) == oracle


def test_oracle_inventory_mismatch_refuses_second_freeze(tmp_path: Path):
    direct = _run(tmp_path, "oracle-a", "reference-only")
    _configure_text_inventory(direct)
    freeze_inventory(direct)
    _tamper_oracle_counts(direct)

    seeded = _run(tmp_path, "oracle-b", "svg-seeded")
    _configure_text_inventory(seeded)
    with pytest.raises(SystemExit, match="oracle:inventory-mismatch"):
        freeze_inventory(seeded)
    # 拒绝是 fail-closed 的：状态停在 prepared，不生成 receipt。
    assert seeded.load_meta()["workflow"]["state"] == "prepared"
    assert not (seeded.root / RECEIPT_PATH).exists()
    # 已冻结案例的 inventory 与 oracle 漂移同样被 validate 检出。
    assert "oracle:inventory-mismatch" in inventory_blockers(direct)


def test_oracle_with_corrupted_self_hash_is_invalid(tmp_path: Path):
    run = _run(tmp_path, "oracle-corrupt", "reference-only")
    _configure_text_inventory(run)
    freeze_inventory(run)

    path = oracle_path(run)
    oracle = load_oracle(path)
    oracle["inventory"]["expected_counts"]["text"] += 1
    write_oracle(path, oracle)  # 不重算 oracle_sha256，自校验必须失败

    with pytest.raises(Exception, match="invalid reference oracle"):
        load_oracle(path)
    assert "oracle:invalid" in inventory_blockers(run)
    with pytest.raises(SystemExit, match="oracle:invalid"):
        freeze_inventory(run)


def _fabricate_consistent_source_gate(run: common.Run) -> None:
    meta = run.load_meta()
    receipt = read_json(run.root / RECEIPT_PATH)
    candidate_sha = "c" * 64
    scene = read_json(run.scene_path)
    scene["canonical_svg"] = {"sha256": candidate_sha}
    write_json(run.scene_path, scene)
    write_json(
        run.source_gate_report_path,
        {
            "schema_version": "4.0.0",
            "kind": "source_gate_report",
            "decision": "accept",
            "route_gate": {"input_route": meta["input_route"]},
            "reference": {
                "expected_sha256": meta["source_sha256"],
                "actual_sha256": meta["source_sha256"],
            },
            "reference_inventory_sha256": receipt["inventory_sha256"],
            "candidate": {"sha256": candidate_sha},
            "blockers": [],
        },
    )


def test_check_detects_oracle_receipt_mismatch(tmp_path: Path):
    run = _run(tmp_path, "oracle-receipt", "reference-only")
    _configure_text_inventory(run)
    receipt = freeze_inventory(run)
    _fabricate_consistent_source_gate(run)
    assert _source_gate_blockers(run) == []

    tampered = _tamper_oracle_counts(run)

    assert tampered["oracle_sha256"] != receipt["oracle_sha256"]
    assert "oracle:receipt-mismatch" in _source_gate_blockers(run)


def test_compare_reports_inventory_and_oracle_hashes(tmp_path: Path, monkeypatch):
    direct = _run(tmp_path, "compare-direct", "reference-only")
    _configure_text_inventory(direct)
    direct_receipt = freeze_inventory(direct)
    seeded = _run(tmp_path, "compare-seeded", "svg-seeded")
    _configure_text_inventory(seeded)
    seeded_receipt = freeze_inventory(seeded)
    for run in (direct, seeded):
        provenance = read_json(run.provenance_path)
        provenance["comparison_group"] = "oracle-ab"
        write_json(run.provenance_path, provenance)

    monkeypatch.setattr(common, "CASES_ROOT", tmp_path / "examples")
    report = build_comparison(seeded, direct)

    direct_summary = report["cases"]["reference-only"]["reference_inventory"]
    seeded_summary = report["cases"]["svg-seeded"]["reference_inventory"]
    assert direct_summary["inventory_sha256"] == direct_receipt["inventory_sha256"]
    assert seeded_summary["inventory_sha256"] == seeded_receipt["inventory_sha256"]
    assert direct_summary["oracle_sha256"] == direct_receipt["oracle_sha256"]
    assert seeded_summary["oracle_sha256"] == seeded_receipt["oracle_sha256"]
    assert direct_summary["oracle_sha256"] == seeded_summary["oracle_sha256"]

    markdown = render_markdown(report)
    assert "reference oracle" in markdown
    assert direct_receipt["oracle_sha256"][:12] in markdown
