from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import jsonschema
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import geometry_refinement as geometry  # noqa: E402
import prepare_agent_vision_task as prepare  # noqa: E402
from tests.test_geometry_refinement import (  # noqa: E402
    file_hash,
    make_gold_case,
)


def _shrink_envelopes_to_detector_boxes(ocr_path: Path) -> None:
    """Give each candidate a truthful single-line envelope for crop building."""
    manifest = json.loads(ocr_path.read_text("utf-8"))
    for candidate in manifest["text_candidates"]:
        candidate["bbox_envelope_source"] = dict(candidate["bbox_source"])
    ocr_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_panels(root: Path, source: Path) -> Path:
    segment_dir = root / "segmentation"
    segment_dir.mkdir(exist_ok=True)
    panels = {
        "schema_version": "1.0.0",
        "source": {
            "path": str(source.resolve()),
            "sha256": file_hash(source),
            "size_bytes": source.stat().st_size,
        },
        "region_candidates": [
            {
                "candidate_id": "region-candidate-001",
                "hex": "#141414",
                "cluster": 1,
                "area": 900,
                "bbox": [10, 10, 60, 15],
                "cx": 40,
                "cy": 17,
                "aspect": 4.0,
                "status": "heuristic_region_candidate",
            },
            {
                "candidate_id": "region-candidate-002",
                "hex": "#141414",
                "cluster": 1,
                "area": 500,
                "bbox": [118, 6, 52, 12],
                "cx": 144,
                "cy": 12,
                "aspect": 4.3,
                "status": "heuristic_region_candidate",
            },
        ],
    }
    panels_path = segment_dir / "panels.json"
    panels_path.write_text(json.dumps(panels, ensure_ascii=False, indent=2), encoding="utf-8")
    return segment_dir


def make_agent_vision_case(root: Path) -> dict[str, Path]:
    source, ocr_path, receipt_path = make_gold_case(root)
    _shrink_envelopes_to_detector_boxes(ocr_path)
    geometry_dir = root / "geometry"
    geometry.run_geometry_refinement(
        source_path=source,
        ocr_manifest_path=ocr_path,
        host_runtime_receipt_path=receipt_path,
        output_dir=geometry_dir,
        project_root=PROJECT_ROOT,
        require_isolated_runtime=True,
    )
    segment_dir = _write_panels(root, source)
    ocr = json.loads(ocr_path.read_text("utf-8"))
    return {
        "source": source,
        "ocr": ocr_path,
        "geometry": geometry_dir / "geometry-manifest.json",
        "receipt": receipt_path,
        "segment_dir": segment_dir,
        "run_id": ocr["run_id"],
    }


def _build(case: dict[str, Path], output_name: str, **overrides):
    return prepare.build_task_package(
        source_path=case["source"],
        ocr_path=case["ocr"],
        geometry_path=case["geometry"],
        receipt_path=case["receipt"],
        segment_dir=case.get("segment_dir"),
        config_path=PROJECT_ROOT / "agent-vision-config.json",
        output_dir=Path(case["ocr"]).parent / output_name,
        run_id=str(case["run_id"]),
        require_isolated_runtime=True,
        **overrides,
    )


def test_task_package_structure_conflict_formula_and_miss_queries(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, package = _build(case, "agent-vision")

    assert package["status"] == "TASK_PACKAGE_READY"
    assert package["degradations"] == []
    assert package["run_id"] == case["run_id"]

    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "agent-vision-task.schema.json").read_text("utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(package)

    by_type: dict[str, list[dict]] = {}
    for query in package["queries"]:
        by_type.setdefault(query["task_type"], []).append(query)

    assert [q["query_id"] for q in by_type["STRUCTURE_GLOBAL"]] == ["V0001"]
    conflict_ids = {q["payload"]["candidate_id"] for q in by_type["CONFLICT_ARBITRATION"]}
    formula_ids = {q["payload"]["candidate_id"] for q in by_type["FORMULA_TRANSCRIPTION"]}
    miss_ids = {q["payload"]["region_candidate_id"] for q in by_type["MISS_SCAN"]}
    assert conflict_ids == {"T0007"}  # the only candidate carrying alternatives
    assert formula_ids == {"T0003"}  # the only FORMULA_LIKE candidate
    assert miss_ids == {"region-candidate-002"}  # 001 overlaps text envelopes
    assert package["summary"]["query_count"] == 1 + 1 + 1 + 1


def test_conflict_selections_are_shuffled_and_confidence_free(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    _package_path, package = _build(case, "agent-vision")
    conflict = next(
        q for q in package["queries"] if q["task_type"] == "CONFLICT_ARBITRATION"
    )
    selections = conflict["payload"]["selections"]
    ocr = json.loads(Path(case["ocr"]).read_text("utf-8"))
    candidate = next(c for c in ocr["text_candidates"] if c["candidate_id"] == "T0007")
    expected_texts = {candidate["text"], candidate["alternatives"][0]["text"]}
    assert {item["text"] for item in selections} == expected_texts
    assert [item["index"] for item in selections] == [0, 1]
    for item in selections:
        assert set(item) == {"index", "text"}  # no confidence, no primary marker


def test_task_package_is_byte_deterministic(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    first_path, first = _build(case, "agent-vision-a")
    second_path, second = _build(case, "agent-vision-b")
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first == second
    first_crop = first_path.parent / "crops" / "conflict" / "T0007.png"
    second_crop = second_path.parent / "crops" / "conflict" / "T0007.png"
    assert file_hash(first_crop) == file_hash(second_crop)


def test_missing_segmentation_degrades_but_still_builds(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    case_no_segment = dict(case)
    case_no_segment.pop("segment_dir")
    _path, package = _build(case_no_segment, "agent-vision-degraded")
    assert package["status"] == "TASK_PACKAGE_DEGRADED"
    assert "SEGMENTATION_UNAVAILABLE" in package["degradations"]
    assert package["summary"]["miss_scan_query_count"] == 0
    assert package["inputs"]["segmentation"] is None


def test_response_template_and_instructions_are_written(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, package = _build(case, "agent-vision")
    output_dir = package_path.parent
    template = json.loads((output_dir / "response-template.json").read_text("utf-8"))
    assert template["document_type"] == "AGENT_VISION_RESPONSE"
    assert template["task_package"]["sha256"] == file_hash(package_path)
    assert template["task_package"]["run_id"] == package["run_id"]
    assert template["validation"] is None
    assert [q["query_id"] for q in template["queries"]] == [
        q["query_id"] for q in package["queries"]
    ]
    assert all(q["observation_status"] == "NOT_OBSERVABLE" for q in template["queries"])
    instructions = (output_dir / "INSTRUCTIONS.md").read_text("utf-8")
    assert "STRUCTURE_GLOBAL" in instructions
    assert "agent-vision-response.json" in instructions


def test_verify_package_mode_round_trips(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, _package = _build(case, "agent-vision")
    assert prepare.verify_task_package_file(package_path)["document_type"] == (
        "AGENT_VISION_TASK_PACKAGE"
    )
    assert prepare.main(["--verify-package", str(package_path)]) == prepare.EXIT_OK

    tampered_crop = package_path.parent / "crops" / "formula" / "T0003.png"
    tampered_crop.write_bytes(tampered_crop.read_bytes() + b"x")
    with pytest.raises(prepare.TaskPackageError, match="crop hash mismatch"):
        prepare.verify_task_package_file(package_path)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda ocr, geom, receipt: ocr.update(status="OCR_HYPOTHESES_INCONCLUSIVE"),
         prepare.UpstreamInconclusive),
        (lambda ocr, geom, receipt: ocr.update(run_id="another-run-id"),
         prepare.TaskPackageError),
        (lambda ocr, geom, receipt: receipt.update(status="FAIL"),
         prepare.TaskPackageError),
        (lambda ocr, geom, receipt: ocr["source"].update(sha256="A" * 64),
         prepare.TaskPackageError),
        (lambda ocr, geom, receipt: geom["inputs"]["ocr_manifest"].update(sha256="B" * 64),
         prepare.TaskPackageError),
    ],
)
def test_fail_closed_on_stale_or_foreign_inputs(tmp_path, mutation, error) -> None:
    case = make_agent_vision_case(tmp_path)
    ocr_path = Path(case["ocr"])
    geometry_path = Path(case["geometry"])
    receipt_path = Path(case["receipt"])
    ocr = json.loads(ocr_path.read_text("utf-8"))
    geom = json.loads(geometry_path.read_text("utf-8"))
    receipt = json.loads(receipt_path.read_text("utf-8"))
    mutation(ocr, geom, receipt)
    ocr_path.write_text(json.dumps(ocr, ensure_ascii=False), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(error):
        prepare.build_task_package(
            source_path=case["source"],
            ocr_path=ocr_path,
            geometry_path=geometry_path,
            receipt_path=receipt_path,
            segment_dir=case["segment_dir"],
            config_path=PROJECT_ROOT / "agent-vision-config.json",
            output_dir=tmp_path / "agent-vision-rejected",
            run_id=str(case["run_id"]),
            require_isolated_runtime=True,
        )
    assert not (tmp_path / "agent-vision-rejected" / "task-package.json").exists()


def test_task_package_policy_is_never_authoritative(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    _path, package = _build(case, "agent-vision")
    assert package["policy"] == {
        "vlm_is_ground_truth": False,
        "coordinates_advisory_only": True,
        "may_not_invent_text": True,
        "agent_must_observe_images_directly": True,
    }
    copied = copy.deepcopy(package)
    copied["policy"]["vlm_is_ground_truth"] = True
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "agent-vision-task.schema.json").read_text("utf-8")
    )
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(copied))


def test_crop_hash_binding_is_recorded_per_query(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, package = _build(case, "agent-vision")
    base = package_path.parent
    for query in package["queries"]:
        crop = base / query["image"]["relative_path"]
        assert crop.is_file()
        assert query["image"]["sha256"] == file_hash(crop)
        assert query["image"]["size_bytes"] == crop.stat().st_size
    full_sha = hashlib.sha256(
        json.dumps(package, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest().upper()
    assert len(full_sha) == 64  # package remains hash-stable for downstream binding
