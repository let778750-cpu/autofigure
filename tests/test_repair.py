from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import read_json, transition, write_json
from tools.convert import convert
from tools.repair import build_live_request, ingest_live_evidence, live_evidence_passes


def _run(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-source.png"
    Image.new("RGB", (120, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="case",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="100" viewBox="0 0 120 100">'
        '<rect id="box" x="10" y="10" width="100" height="80" fill="#ffffff" stroke="#111111"/>'
        '</svg>',
        encoding="utf-8",
    )
    convert(run)
    regions = read_json(run.regions_path)
    regions["regions"] = [
        {
            "id": "failed",
            "bbox": [0, 0, 10, 10],
            "critical": True,
            "element_ids": ["box"],
        }
    ]
    write_json(run.regions_path, regions)
    return run


def test_live_request_and_evidence_are_hash_bound(tmp_path: Path):
    run = _run(tmp_path)
    transition(run, "repairing", "live-request")
    request = build_live_request(run)
    assert request["visible"] is True
    assert request["failed_regions"] == ["failed"]
    assert request["failed_region_tasks"][0]["allowed_element_ids"] == ["box"]
    assert request["failed_region_tasks"][0]["all_other_elements_protected"] is True
    assert request["failed_region_tasks"][0]["manual_scope_required"] is False
    assert "save-reopen" in request["required_capabilities"]
    assert request["scene_compatibility"]["source_schema_version"] == "3.1.0"
    assert request["scene_compatibility"]["adapter_schema_version"] == "2.1.0"
    assert (run.root / request["case_root"] / "project_state.json").is_file()
    assert (run.root / request["template_path"]).is_file()

    evidence_path = tmp_path / "evidence.json"
    write_json(
        evidence_path,
        {
            "provider": "powerpoint-live",
            "reference_sha256": run.load_meta()["source_sha256"],
            "target_id": "autofigure-pptx",
            "saved_reopened": True,
            "bindings_complete": True,
            "regions": {"failed": "REGION_PASS"},
        },
    )
    ingest_live_evidence(run, evidence_path)
    assert live_evidence_passes(run, ["failed"]) == (True, [])


def test_live_evidence_mismatch_is_rejected(tmp_path: Path):
    run = _run(tmp_path)
    evidence_path = tmp_path / "bad-evidence.json"
    write_json(
        evidence_path,
        {
            "provider": "powerpoint-live",
            "reference_sha256": "wrong",
            "target_id": "autofigure-pptx",
            "saved_reopened": True,
            "bindings_complete": True,
            "regions": {},
        },
    )
    with pytest.raises(SystemExit, match="contract mismatch"):
        ingest_live_evidence(run, evidence_path)
