from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.cases import discover_cases, main as cases_main
from tools.contracts import read_json, write_json


def _reference(tmp_path: Path) -> Path:
    path = tmp_path / "reference.png"
    Image.new("RGB", (80, 50), "white").save(path)
    return path


def test_cases_check_validates_physical_routes_and_generated_index(tmp_path: Path):
    cases_root = tmp_path / "examples"
    reference = _reference(tmp_path)
    seeded = common.create_run(
        reference,
        case="seeded",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    direct = common.create_run(
        reference,
        case="direct",
        cases_root=cases_root,
        input_route="reference-only",
    )
    for run in (seeded, direct):
        provenance = read_json(run.provenance_path)
        provenance["comparison_group"] = "same-reference-ab"
        write_json(run.provenance_path, provenance)

    assert cases_main(["--cases-root", str(cases_root), "--write-index", "--check"]) == 0
    records, findings = discover_cases(cases_root)
    assert len(records) == 2
    assert findings == []


def test_new_case_can_declare_read_only_legacy_comparison_peer(tmp_path: Path):
    cases_root = tmp_path / "examples"
    reference = _reference(tmp_path)
    seeded = common.create_run(
        reference,
        case="legacy-seeded",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    direct = common.create_run(
        reference,
        case="new-direct",
        cases_root=cases_root,
        input_route="reference-only",
    )
    provenance = read_json(direct.provenance_path)
    provenance["comparison_group"] = "same-reference-read-only-peer"
    provenance["comparison_peers"] = ["svg-seeded/legacy-seeded"]
    write_json(direct.provenance_path, provenance)

    assert read_json(seeded.provenance_path)["comparison_group"] is None
    assert cases_main(["--cases-root", str(cases_root), "--write-index", "--check"]) == 0
    _, findings = discover_cases(cases_root)
    assert findings == []


def test_case_id_is_globally_unique_across_routes(tmp_path: Path):
    cases_root = tmp_path / "examples"
    reference = _reference(tmp_path)
    common.create_run(
        reference,
        case="duplicate",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    with pytest.raises(SystemExit, match="案例 ID 已存在"):
        common.create_run(
            reference,
            case="duplicate",
            cases_root=cases_root,
            input_route="reference-only",
        )


def test_case_name_resolver_finds_nested_case(monkeypatch, tmp_path: Path):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="nested",
        cases_root=cases_root,
        input_route="reference-only",
    )
    monkeypatch.setattr(common, "CASES_ROOT", cases_root)
    assert common.open_run(Path("nested")).root == run.root


def test_cases_check_rejects_route_metadata_mismatch(tmp_path: Path):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="wrong-route",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    meta = run.load_meta()
    meta["input_route"] = "reference-only"
    write_json(run.meta_path, meta)
    _, findings = discover_cases(cases_root)
    assert any(item.startswith("route-directory-mismatch:") for item in findings)


def test_cases_check_rejects_powerpoint_live_session_build(tmp_path: Path):
    cases_root = tmp_path / "examples"
    (cases_root / "svg-seeded").mkdir(parents=True)
    run = common.create_run(
        _reference(tmp_path),
        case="live-build-residue",
        cases_root=cases_root,
        input_route="reference-only",
    )
    live_build = run.qa_dir / "powerpoint-live-case" / "build" / "candidates"
    live_build.mkdir(parents=True)

    _, findings = discover_cases(cases_root)

    assert f"transient-case-directory:{live_build.parent}" in findings
    assert cases_main(["--cases-root", str(cases_root), "--write-index"]) == 0
    assert cases_main(["--cases-root", str(cases_root), "--check"]) == 2


@pytest.mark.parametrize(
    "relative",
    (
        Path("__pycache__"),
        Path(".pytest_cache"),
        Path("qa/session-review"),
        Path("qa/candidates"),
    ),
)
def test_cases_check_rejects_other_transient_case_directories(
    tmp_path: Path,
    relative: Path,
):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="transient-residue",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    transient = run.root / relative
    transient.mkdir(parents=True)

    _, findings = discover_cases(cases_root)

    assert f"transient-case-directory:{transient}" in findings


def test_cases_check_rejects_invalid_approved_validation_semantics(tmp_path: Path):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="invalid-approved",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    meta = run.load_meta()
    meta["workflow"]["state"] = "approved"
    meta["validation"] = {
        "profile": "standard",
        "status": "diagnostic",
        "checked_at": None,
        "blockers": ["region:failed"],
    }
    write_json(run.meta_path, meta)

    _, findings = discover_cases(cases_root)

    assert f"approved-without-strict-validation:{run.meta_path}" in findings
    assert f"approved-validation-not-passed:{run.meta_path}" in findings
    assert f"approved-with-blockers:{run.meta_path}" in findings


def test_cases_check_requires_live_evidence_for_hybrid_approved_case(tmp_path: Path):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="hybrid-approved",
        cases_root=cases_root,
        input_route="reference-only",
    )
    meta = run.load_meta()
    meta["backend_mode"] = "hybrid"
    meta["workflow"]["state"] = "approved"
    meta["validation"] = {
        "profile": "strict",
        "status": "passed",
        "checked_at": "20260823T000000Z",
        "blockers": [],
    }
    write_json(run.meta_path, meta)

    _, findings = discover_cases(cases_root)
    expected = f"approved-hybrid-live-evidence-missing:{run.qa_dir / 'live-evidence.json'}"
    assert expected in findings

    write_json(run.qa_dir / "live-evidence.json", {"schema_version": "1.1.0"})
    _, findings = discover_cases(cases_root)
    assert expected not in findings
    assert f"approved-without-release-manifest:{run.root / 'release-manifest.json'}" in findings

    write_json(
        run.root / "release-manifest.json",
        {"schema_version": "4.0.0", "kind": "release_manifest"},
    )
    _, findings = discover_cases(cases_root)
    assert not any(item.startswith("approved-") for item in findings)


def test_cases_check_rejects_release_manifest_without_approval(tmp_path: Path):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="orphan-manifest",
        cases_root=cases_root,
        input_route="reference-only",
    )
    manifest = run.root / "release-manifest.json"
    write_json(manifest, {"schema_version": "4.0.0", "kind": "release_manifest"})

    _, findings = discover_cases(cases_root)

    assert f"release-manifest-without-approval:{manifest}" in findings


def test_cases_check_requires_release_manifest_for_approved_case(tmp_path: Path):
    cases_root = tmp_path / "examples"
    run = common.create_run(
        _reference(tmp_path),
        case="approved-no-manifest",
        cases_root=cases_root,
        input_route="svg-seeded",
    )
    meta = run.load_meta()
    meta["workflow"]["state"] = "approved"
    meta["validation"] = {
        "profile": "strict",
        "status": "passed",
        "checked_at": "20260823T000000Z",
        "blockers": [],
    }
    write_json(run.meta_path, meta)
    manifest = run.root / "release-manifest.json"

    _, findings = discover_cases(cases_root)
    assert f"approved-without-release-manifest:{manifest}" in findings

    write_json(manifest, {"schema_version": "4.0.0", "kind": "release_manifest"})
    _, findings = discover_cases(cases_root)
    assert f"approved-without-release-manifest:{manifest}" not in findings
