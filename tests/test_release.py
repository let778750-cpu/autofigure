"""release manifest 生成与 --check 漂移检测（合成案例，不触 OCR/PowerPoint）。"""

from __future__ import annotations

from pathlib import Path

from tools import common
from tools.contracts import read_json
from tools.qa_status import QA_STATUS_NAME, write_qa_status
from tools.release import (
    RELEASE_ARTIFACTS,
    main as release_main,
    release_manifest_path,
    validate_release_manifest,
)
from tests.qa_fixtures import make_approved_case, make_case


def test_release_refuses_case_without_passing_dimensions(tmp_path: Path, capsys):
    run = make_case(tmp_path, case="release-refused")

    assert release_main([str(run.root)]) == 2

    out = capsys.readouterr().out
    assert "release 拒绝" in out
    assert "offline_package_consistency" in out
    assert "release_eligibility" in out
    assert not release_manifest_path(run).is_file()


def test_release_generates_manifest_and_recheck_passes(tmp_path: Path, capsys):
    run = make_approved_case(tmp_path, case="release-ok")
    write_qa_status(run, ocr_unmatched=(0, 0))

    assert release_main([str(run.root)]) == 0

    manifest_path = release_manifest_path(run)
    assert manifest_path.is_file()
    manifest = read_json(manifest_path)
    meta = run.load_meta()
    assert manifest["schema_version"] == "4.0.0"
    assert manifest["kind"] == "release_manifest"
    assert manifest["case"] == meta["case"]
    assert manifest["reference_sha256"] == meta["source_sha256"]
    assert tuple(manifest["artifacts"]) == RELEASE_ARTIFACTS
    for name, digest in manifest["artifacts"].items():
        assert digest == common.sha256_file(run.root / name)
    assert manifest["qa_status_sha256"] == common.sha256_file(
        run.qa_dir / QA_STATUS_NAME
    )
    assert validate_release_manifest(run) == []
    assert release_main([str(run.root), "--check"]) == 0
    assert "校验通过" in capsys.readouterr().out


def test_release_check_fails_on_artifact_drift(tmp_path: Path, capsys):
    run = make_approved_case(tmp_path, case="release-drift")
    write_qa_status(run, ocr_unmatched=(0, 0))
    assert release_main([str(run.root)]) == 0

    run.pptx_path.write_bytes(b"pptx projection tampered")

    assert release_main([str(run.root), "--check"]) == 2
    out = capsys.readouterr().out
    assert "release-manifest:artifact-drift:redraw.pptx" in out
    assert "offline_package_consistency" in out


def test_release_check_fails_on_qa_status_drift(tmp_path: Path, capsys):
    run = make_approved_case(tmp_path, case="release-status-drift")
    write_qa_status(run, ocr_unmatched=(0, 0))
    assert release_main([str(run.root)]) == 0

    write_qa_status(run, ocr_unmatched=(1, 0))

    assert release_main([str(run.root), "--check"]) == 2
    assert "release-manifest:qa-status-drift" in capsys.readouterr().out


def test_release_regenerates_over_stale_manifest(tmp_path: Path):
    run = make_approved_case(tmp_path, case="release-refresh")
    write_qa_status(run, ocr_unmatched=(0, 0))
    assert release_main([str(run.root)]) == 0

    run.report_md.write_text("# check 报告（更新）\n", encoding="utf-8")
    assert "release-manifest:artifact-drift:check-report.md" in validate_release_manifest(run)

    assert release_main([str(run.root)]) == 0
    assert validate_release_manifest(run) == []
    assert release_main([str(run.root), "--check"]) == 0


def test_release_check_fails_when_manifest_missing(tmp_path: Path, capsys):
    run = make_approved_case(tmp_path, case="release-missing")
    write_qa_status(run, ocr_unmatched=(0, 0))

    assert release_main([str(run.root), "--check"]) == 2
    assert "release-manifest:missing" in capsys.readouterr().out
