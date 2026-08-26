from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import read_json
from tools.prepare import SVG_AUTHORING_CONTRACT, main as prepare_main


def _reference(tmp_path: Path) -> Path:
    path = tmp_path / "reference.png"
    Image.new("RGB", (120, 80), "white").save(path)
    return path


def _seed(tmp_path: Path, name: str = "seed.svg") -> Path:
    path = tmp_path / name
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" '
        'viewBox="0 0 120 80"><rect id="panel" width="120" height="80"/></svg>',
        encoding="utf-8",
    )
    return path


def test_reference_only_prepare_forbids_seed_and_creates_png_contract(tmp_path: Path):
    reference = _reference(tmp_path)
    cases_root = tmp_path / "examples"
    seed = _seed(tmp_path)

    with pytest.raises(SystemExit, match="reference-only.*--seed"):
        prepare_main(
            [
                str(reference),
                "--case",
                "forbidden-seed",
                "--cases-root",
                str(cases_root),
                "--input-route",
                "reference-only",
                "--seed",
                str(seed),
            ]
        )
    assert not (cases_root / "reference-only" / "forbidden-seed").exists()

    assert (
        prepare_main(
            [
                str(reference),
                "--case",
                "png-only",
                "--cases-root",
                str(cases_root),
                "--input-route",
                "reference-only",
            ]
        )
        == 0
    )
    run = common.Run(cases_root / "reference-only" / "png-only")
    meta = run.load_meta()
    assert meta["schema_version"] == "4.0.0"
    assert meta["input_route"] == "reference-only"
    assert meta["processing_mode"] == "png_reconstruct"
    assert not run.external_seed_svg.exists()
    provenance = read_json(run.provenance_path)
    assert provenance["external_svg_seed"] is None
    assert provenance["candidate_history"] == []
    assert read_json(run.scene_path)["canonical_source"] == "scene"
    tasks = read_json(run.region_tasks_path)
    assert tasks["schema_version"] == "4.0.0"
    assert tasks["input_route"] == "reference-only"


def test_svg_seeded_prepare_requires_one_valid_seed_and_freezes_it(tmp_path: Path):
    reference = _reference(tmp_path)
    cases_root = tmp_path / "examples"

    with pytest.raises(SystemExit, match="svg-seeded.*--seed"):
        prepare_main(
            [
                str(reference),
                "--case",
                "missing-seed",
                "--cases-root",
                str(cases_root),
                "--input-route",
                "svg-seeded",
            ]
        )
    assert not (cases_root / "svg-seeded" / "missing-seed").exists()

    seed = _seed(tmp_path)
    assert (
        prepare_main(
            [
                str(reference),
                "--case",
                "seeded",
                "--cases-root",
                str(cases_root),
                "--input-route",
                "svg-seeded",
                "--seed",
                str(seed),
            ]
        )
        == 0
    )
    run = common.Run(cases_root / "svg-seeded" / "seeded")
    meta = run.load_meta()
    assert meta["schema_version"] == "4.0.0"
    assert meta["input_route"] == "svg-seeded"
    assert meta["processing_mode"] == "svg_import"
    assert run.external_seed_svg.read_bytes() == seed.read_bytes()
    seed_sha256 = common.sha256_file(seed)
    provenance = read_json(run.provenance_path)
    assert provenance["external_svg_seed"]["sha256"] == seed_sha256
    assert provenance["external_svg_seed"]["canonical_path"] == "external-seed.svg"
    assert [item["role"] for item in provenance["candidate_history"]] == [
        "external-seed"
    ]
    gate = read_json(run.source_gate_report_path)
    assert gate["status"] == "pending"
    assert gate["decision"] is None
    assert gate["seed_sha256"] == seed_sha256
    assert gate["reason_codes"] == ["reference-inventory-not-frozen"]
    scene = read_json(run.scene_path)
    assert scene["canonical_source"] == "scene"
    assert "canonical_svg" not in scene


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("not xml", "外部 SVG 种子无效"),
        ("<html/>", "--seed 必须是 SVG 根文档"),
    ],
)
def test_svg_seeded_prepare_rejects_invalid_seed_before_case_creation(
    tmp_path: Path,
    contents: str,
    message: str,
):
    reference = _reference(tmp_path)
    cases_root = tmp_path / "examples"
    seed = tmp_path / "bad.svg"
    seed.write_text(contents, encoding="utf-8")
    with pytest.raises(SystemExit, match=message):
        prepare_main(
            [
                str(reference),
                "--case",
                "invalid-seed",
                "--cases-root",
                str(cases_root),
                "--input-route",
                "svg-seeded",
                "--seed",
                str(seed),
            ]
        )
    assert not (cases_root / "svg-seeded" / "invalid-seed").exists()


def test_svg_authoring_contract_includes_authorized_inline_atomic_vector_group():
    assert '<g id="atomic:...">' in SVG_AUTHORING_CONTRACT
    # Both prompt templates and the route smoke tests interpolate the contract
    # with str.format(width=..., height=...), so no other braces may appear.
    SVG_AUTHORING_CONTRACT.format(width=120, height=80)
