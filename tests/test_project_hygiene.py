from __future__ import annotations

from pathlib import Path

from tools.check_project_hygiene import inspect_project


def _project(tmp_path: Path) -> Path:
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "target_figure.png").write_bytes(b"fixture")
    (examples / "target_figure.fixture.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_clean_layout_passes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".git").mkdir()
    (root / "examples" / "generated" / "runs").mkdir(parents=True)

    report = inspect_project(root)

    assert report["status"] == "PASS"
    assert report["findings"] == []


def test_named_benchmark_source_images_are_stable_inputs(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source_name = (
        "01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png"
    )
    (root / "examples" / source_name).write_bytes(b"benchmark source")

    report = inspect_project(root)

    assert report["status"] == "PASS"
    assert report["findings"] == []


def test_named_source_authority_is_a_stable_contract(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "examples" / "modularagent.source-authority.json").write_text(
        "{}", encoding="utf-8"
    )

    report = inspect_project(root)

    assert report["status"] == "PASS"
    assert report["findings"] == []


def test_transient_root_directories_and_bytecode_fail(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "work").mkdir()
    (root / ".pytest_cache").mkdir()
    (root / "output").mkdir()
    (root / "tools" / "__pycache__").mkdir(parents=True)

    report = inspect_project(root)
    codes = {finding["code"] for finding in report["findings"]}

    assert report["status"] == "FAIL"
    assert "FORBIDDEN_ROOT_ARTIFACT_DIRECTORY" in codes
    assert "BYTECODE_CACHE_IN_PROJECT" in codes


def test_python_environments_are_forbidden_inside_project_root(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / ".venv").mkdir()
    (root / "env").mkdir()
    (root / "site-packages").mkdir()

    report = inspect_project(root)

    assert report["status"] == "FAIL"
    forbidden = {
        finding["path"]
        for finding in report["findings"]
        if finding["code"] == "FORBIDDEN_ROOT_ARTIFACT_DIRECTORY"
    }
    assert {".venv", "env", "site-packages"}.issubset(forbidden)


def test_unclassified_root_artifact_fails(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "final.pptx").write_bytes(b"generated")

    report = inspect_project(root)

    assert report["status"] == "FAIL"
    assert any(
        finding["code"] == "UNCLASSIFIED_PROJECT_ROOT_ENTRY"
        for finding in report["findings"]
    )


def test_unclassified_examples_entries_fail(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "examples" / "_analysis").mkdir()
    (root / "examples" / "result.png").write_bytes(b"generated")

    report = inspect_project(root)

    assert report["status"] == "FAIL"
    assert any(
        finding["code"] == "UNCLASSIFIED_EXAMPLES_ENTRY"
        for finding in report["findings"]
    )
