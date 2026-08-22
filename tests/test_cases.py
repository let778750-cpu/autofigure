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
