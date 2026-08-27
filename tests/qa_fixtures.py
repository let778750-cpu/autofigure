"""测试夹具：构造六个 QA 维度全 pass 的合成案例。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from tools.core import common
from tools.pipeline.check import _qa_report_hashes
from tools.core.contracts import read_json, record_validation, transition, write_json
from tools.qa.qa_lineage import write_qa_lineage_manifest
from tools.repair.repair_plan import write_repair_plan
from tools.core.revisions import bind_canonical_svg, materialize_svg, stamp_active_revision

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60" '
    'viewBox="0 0 80 60"><rect id="panel" x="5" y="5" width="70" height="50"/></svg>'
)


def make_case(tmp_path: Path, case: str = "status-case") -> common.Run:
    reference = tmp_path / f"{case}-reference.png"
    Image.new("RGB", (80, 60), "white").save(reference)
    return common.create_run(
        reference,
        case=case,
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )


def _write_artifacts(run: common.Run) -> None:
    scene = read_json(run.scene_path)
    bind_canonical_svg(scene, SVG, source_role="reference-reconstruction")
    write_json(run.scene_path, scene)
    materialize_svg(run, scene)
    run.pptx_path.write_bytes(b"pptx projection")
    Image.new("RGB", (80, 60), "white").save(run.render_png)
    Image.new("RGB", (80, 130), "white").save(run.preview_png)
    run.report_md.write_text("# check 报告\n", encoding="utf-8")
    bindings = read_json(run.bindings_path)
    bindings["artifact_sha256"] = common.sha256_file(run.pptx_path)
    bindings["saved_reopened"] = True
    bindings["bindings_complete"] = True
    write_json(run.bindings_path, bindings)
    stamp_active_revision(run)


def _write_qa_evidence(run: common.Run) -> None:
    meta = run.load_meta()
    reference_sha256 = meta["source_sha256"]
    artifact_sha256 = common.sha256_file(run.pptx_path)
    write_json(
        run.qa_dir / "regions-report.json",
        {
            "schema_version": "4.0.0",
            "kind": "regions_report",
            "reference_sha256": reference_sha256,
            "strict_pass": True,
            "critical_regions": 1,
            "blockers": [],
            "regions": [{"id": "whole-canvas", "critical": True, "pass": True}],
        },
    )
    write_json(
        run.layout_audit_path,
        {"schema_version": "4.0.0", "pass": True, "findings": []},
    )
    write_json(
        run.qa_dir / "arrow-visual-report.json",
        {
            "schema_version": "4.0.0",
            "kind": "arrow_visual_report",
            "reference_sha256": reference_sha256,
            "pass": True,
            "blockers": [],
        },
    )
    write_json(
        run.qa_dir / "visual-contracts-report.json",
        {"schema_version": "4.0.0", "pass": True, "blockers": []},
    )
    write_json(
        run.qa_dir / "live-save-reopen-summary.json",
        {
            "schema_version": "1.2.0",
            "kind": "powerpoint_live_save_reopen_summary",
            "reference_sha256": reference_sha256,
            "saved_reopened": True,
            "bindings_complete": True,
            "live_candidate_sha256": artifact_sha256,
            "reopened_artifact_sha256": artifact_sha256,
            "current_root_candidate_sha256": artifact_sha256,
        },
    )
    write_json(
        run.live_evidence_path,
        {
            "schema_version": "1.1.0",
            "reference_sha256": reference_sha256,
            "saved_reopened": True,
            "bindings_complete": True,
            "candidate_sha256": artifact_sha256,
            "reopened_artifact_sha256": artifact_sha256,
        },
    )
    # repair plan 绑定当前 QA 报告哈希，必须在全部报告落定之后写入。
    write_repair_plan(
        run.repair_plan_path,
        [],
        case=meta["case"],
        reference_sha256=reference_sha256,
        artifact_sha256=artifact_sha256,
        qa_report_sha256=_qa_report_hashes(run),
    )
    write_json(
        run.blockers_path,
        {
            "schema_version": "4.0.0",
            "kind": "strict_blocker_inventory",
            "case": meta["case"],
            "reference_sha256": reference_sha256,
            "artifact_sha256": artifact_sha256,
            "blockers": [],
        },
    )
    write_qa_lineage_manifest(run)


def make_approved_case(tmp_path: Path, case: str = "status-case") -> common.Run:
    run = make_case(tmp_path, case)
    _write_artifacts(run)
    _write_qa_evidence(run)
    record_validation(run, "strict", [])
    transition(run, "candidate", "candidate-built")
    transition(run, "approved", "strict-check-passed")
    return run
