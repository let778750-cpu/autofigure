"""qa-status 六维度读出单元测试（合成案例，不触 OCR/PowerPoint）。"""

from __future__ import annotations

from pathlib import Path

from tools import common
from tools.contracts import read_json, write_json
from tools.qa_lineage import validate_qa_lineage_manifest, write_qa_lineage_manifest
from tools.qa_status import (
    QA_DIMENSIONS,
    QA_STATUS_NAME,
    compute_qa_dimensions,
    write_qa_status,
)
from tests.qa_fixtures import SVG, make_approved_case, make_case


def _write_artifacts_only(run: common.Run) -> None:
    from tools.revisions import bind_canonical_svg, materialize_svg, stamp_active_revision

    scene = read_json(run.scene_path)
    bind_canonical_svg(scene, SVG, source_role="reference-reconstruction")
    write_json(run.scene_path, scene)
    materialize_svg(run, scene)
    run.pptx_path.write_bytes(b"pptx projection")
    bindings = read_json(run.bindings_path)
    bindings["artifact_sha256"] = common.sha256_file(run.pptx_path)
    bindings["saved_reopened"] = True
    bindings["bindings_complete"] = True
    write_json(run.bindings_path, bindings)
    stamp_active_revision(run)


def test_dimension_order_is_fixed():
    assert QA_DIMENSIONS == (
        "offline_package_consistency",
        "saved_reopened_consistency",
        "reference_fidelity",
        "repair_plan_coverage",
        "repair_execution",
        "release_eligibility",
    )


def test_fresh_case_dimensions_are_not_pass(tmp_path: Path):
    run = make_case(tmp_path)

    dimensions = compute_qa_dimensions(run)

    assert tuple(dimensions) == QA_DIMENSIONS
    assert dimensions["offline_package_consistency"]["status"] == "fail"
    assert dimensions["saved_reopened_consistency"]["status"] == "fail"
    assert dimensions["reference_fidelity"]["status"] == "not_evaluated"
    assert dimensions["repair_plan_coverage"]["status"] == "not_evaluated"
    assert dimensions["repair_execution"]["status"] == "not_evaluated"
    eligibility = dimensions["release_eligibility"]
    assert eligibility["status"] == "fail"
    assert "release-eligibility:not-approved" in eligibility["blockers"]


def test_approved_case_reaches_all_pass_and_document_is_deterministic(tmp_path: Path):
    run = make_approved_case(tmp_path)

    document = write_qa_status(run, ocr_unmatched=(0, 0))

    assert document["kind"] == "qa_status"
    assert document["schema_version"] == "4.0.0"
    assert document["reference_sha256"] == run.load_meta()["source_sha256"]
    assert document["artifact_sha256"] == common.sha256_file(run.pptx_path)
    statuses = {
        name: dimension["status"] for name, dimension in document["dimensions"].items()
    }
    assert statuses == {name: "pass" for name in QA_DIMENSIONS}
    for dimension in document["dimensions"].values():
        assert dimension["blockers"] == []
        assert all(
            set(entry) == {"path", "sha256"} for entry in dimension["evidence"]
        )
    on_disk = (run.qa_dir / QA_STATUS_NAME).read_bytes()
    write_qa_status(run, ocr_unmatched=(0, 0))
    assert (run.qa_dir / QA_STATUS_NAME).read_bytes() == on_disk


def test_reference_fidelity_surfaces_report_blockers_and_ocr_counts(tmp_path: Path):
    run = make_approved_case(tmp_path)
    regions = read_json(run.qa_dir / "regions-report.json")
    regions["blockers"] = ["region:panel:ssim-below-threshold"]
    write_json(run.qa_dir / "regions-report.json", regions)

    dimensions = compute_qa_dimensions(run, ocr_unmatched=(2, 1))

    fidelity = dimensions["reference_fidelity"]
    assert fidelity["status"] == "fail"
    assert "region:panel:ssim-below-threshold" in fidelity["blockers"]
    assert "ocr:svg-text-unmatched" in fidelity["blockers"]
    assert "ocr:reference-text-unmatched" in fidelity["blockers"]


def test_reference_fidelity_ignores_ocr_when_not_run(tmp_path: Path):
    run = make_approved_case(tmp_path)

    dimensions = compute_qa_dimensions(run, ocr_unmatched=None)

    fidelity = dimensions["reference_fidelity"]
    assert fidelity["status"] == "pass"
    assert not any(item.startswith("ocr:") for item in fidelity["blockers"])


def test_saved_reopened_not_evaluated_without_live_evidence(tmp_path: Path):
    run = make_case(tmp_path)
    _write_artifacts_only(run)

    dimensions = compute_qa_dimensions(run)

    consistency = dimensions["saved_reopened_consistency"]
    assert consistency["status"] == "not_evaluated"
    assert consistency["blockers"] == []

    bindings = read_json(run.bindings_path)
    bindings["saved_reopened"] = False
    write_json(run.bindings_path, bindings)
    dimensions = compute_qa_dimensions(run)
    consistency = dimensions["saved_reopened_consistency"]
    assert consistency["status"] == "fail"
    assert "bindings:save-reopen-not-verified" in consistency["blockers"]


def test_saved_reopened_fails_on_stale_live_evidence(tmp_path: Path):
    run = make_approved_case(tmp_path)
    summary_path = run.qa_dir / "live-save-reopen-summary.json"
    summary = read_json(summary_path)
    summary["current_root_candidate_sha256"] = "0" * 64
    write_json(summary_path, summary)

    dimensions = compute_qa_dimensions(run)

    consistency = dimensions["saved_reopened_consistency"]
    assert consistency["status"] == "fail"
    assert "live-save-reopen:not-current-artifact" in consistency["blockers"]


def test_repair_execution_is_open_while_blockers_remain(tmp_path: Path):
    run = make_approved_case(tmp_path)
    inventory = read_json(run.blockers_path)
    inventory["blockers"] = ["region:panel:ssim-below-threshold"]
    write_json(run.blockers_path, inventory)

    dimensions = compute_qa_dimensions(run)

    execution = dimensions["repair_execution"]
    assert execution["status"] == "fail"
    assert execution["execution"] == "open"
    assert "region:panel:ssim-below-threshold" in execution["blockers"]
    # 修复计划自身的覆盖校验不受影响：闭环是重算判定，不是动作回执。
    assert dimensions["repair_plan_coverage"]["status"] == "pass"


def test_repair_execution_fails_when_inventory_is_stale(tmp_path: Path):
    run = make_approved_case(tmp_path)
    run.pptx_path.write_bytes(b"pptx projection rebuilt")

    dimensions = compute_qa_dimensions(run)

    execution = dimensions["repair_execution"]
    assert execution["status"] == "fail"
    assert "repair-execution:inventory-not-current" in execution["blockers"]


def test_qa_status_is_excluded_from_lineage_report_set(tmp_path: Path):
    run = make_approved_case(tmp_path)
    write_qa_status(run, ocr_unmatched=(0, 0))

    manifest = write_qa_lineage_manifest(run)

    paths = [item["path"] for item in manifest["reports"]]
    assert f"qa/{QA_STATUS_NAME}" not in paths
    assert validate_qa_lineage_manifest(run) == []
    # qa-status.json 重写后 lineage 清单保持有效（派生读出不进哈希集）。
    write_qa_status(run, ocr_unmatched=(1, 0))
    assert validate_qa_lineage_manifest(run) == []
