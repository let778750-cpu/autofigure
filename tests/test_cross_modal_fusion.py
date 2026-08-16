from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import cross_modal_fusion as fusion  # noqa: E402
import validate_agent_vision as validator  # noqa: E402
from tests.test_agent_vision_task import make_agent_vision_case  # noqa: E402
from tests.test_agent_vision_validation import make_filled_response  # noqa: E402
from tests.test_geometry_refinement import file_hash  # noqa: E402


def make_fusion_case(tmp_path: Path, *, reject_all: bool = False) -> dict[str, Path]:
    case = make_agent_vision_case(tmp_path)
    package_path, response_path, _ctx = make_filled_response(tmp_path, case)
    if reject_all:
        response = json.loads(response_path.read_text("utf-8"))
        conflict = next(
            q for q in response["queries"] if q["task_type"] == "CONFLICT_ARBITRATION"
        )
        conflict["conflict"] = {
            "decision": "REJECT_ALL",
            "selected_index": None,
            "confidence_self_rating": "LOW",
            "reason_code": "ILLEGIBLE",
        }
        response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

    document_path = response_path.parent / "agent-vision-document.json"
    validator.validate_response(
        package_path=package_path,
        response_path=response_path,
        output_path=document_path,
    )
    return {
        **case,
        "package": package_path,
        "vision": document_path,
    }


def _run_fusion(case: dict[str, Path], output_name: str = "fusion"):
    return fusion.build_fusion(
        ocr_path=Path(case["ocr"]),
        geometry_path=Path(case["geometry"]),
        task_package_path=Path(case["package"]),
        vision_path=case.get("vision"),
        segment_dir=case.get("segment_dir"),
        config_path=PROJECT_ROOT / "agent-vision-config.json",
        output_dir=Path(case["ocr"]).parent / output_name,
    )


def _fact_by_subject(manifest: dict, subject: str, kind: str | None = None) -> dict:
    return next(
        f
        for f in manifest["facts"]
        if f["subject_id"] == subject and (kind is None or f["fact_kind"] == kind)
    )


def test_full_fusion_produces_tiers_and_queue(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path)
    manifest_path, manifest = _run_fusion(case)

    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "fusion-manifest.schema.json").read_text("utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(manifest)
    assert manifest["status"] == "FUSION_REVIEW_QUEUE_READY"
    assert manifest["policy"]["triple_agreement_does_not_waive_review"] is True
    assert manifest["policy"]["vlm_is_ground_truth"] is False

    # T0001: OCR + measured CV, no VLM opinion -> PAIR
    assert _fact_by_subject(manifest, "T0001")["consistency_tier"] == "PAIR"
    # T0007: arbitration selects the primary text -> PAIR (its geometry is INCONCLUSIVE)
    t7 = _fact_by_subject(manifest, "T0007")
    assert t7["detail"]["vlm_opinion"] == "ARBITRATION_SELECT_PRIMARY"
    assert t7["consistency_tier"] == "PAIR"
    # Formula proposal is recorded but never authoritative
    formula = _fact_by_subject(manifest, "T0003", kind="FORMULA_TRANSCRIPTION")
    assert formula["detail"]["proposal_latex"] == "x = y"
    assert formula["detail"]["proposal_status"] == "PROPOSAL_ONLY_NOT_AUTHORITATIVE"
    assert formula["consistency_tier"] == "PAIR"
    # Region anchored to the CV frame containing OCR candidates -> TRIPLE
    region = _fact_by_subject(manifest, "P001")
    assert region["consistency_tier"] == "TRIPLE"
    assert region["detail"]["anchored"] is True
    assert region["detail"]["anchor_source"] == "CV_FRAME_CANDIDATE"
    assert "T0001" in region["detail"]["contained_candidate_ids"]

    # Every OCR candidate has exactly one fact (detail capture completeness)
    ocr = json.loads(Path(case["ocr"]).read_text("utf-8"))
    text_subjects = {
        f["subject_id"]
        for f in manifest["facts"]
        if f["fact_kind"] == "TEXT_CANDIDATE" and f["detail"]["candidate_id"] is not None
    }
    assert text_subjects == {c["candidate_id"] for c in ocr["text_candidates"]}
    assert manifest["summary"]["formula_fact_count"] == 1

    # Queue ordering is priority-descending with stable fact_id tiebreak
    priorities = [item["priority"] for item in manifest["review_queue"]]
    assert priorities == sorted(priorities, reverse=True)
    ranks = [item["rank"] for item in manifest["review_queue"]]
    assert ranks == list(range(1, len(ranks) + 1))

    # Artifacts exist and hash-bind
    base = manifest_path.parent
    assert file_hash(base / "fusion-review-queue.md") == manifest["artifacts"][
        "review_queue_markdown"
    ]["sha256"]
    assert file_hash(base / "fusion-overlay.png") == manifest["artifacts"]["overlay"]["sha256"]


def test_reject_all_arbitration_becomes_conflict(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path, reject_all=True)
    _path, manifest = _run_fusion(case)
    t7 = _fact_by_subject(manifest, "T0007")
    assert t7["consistency_tier"] == "CONFLICT"
    assert t7["detail"]["vlm_opinion"] == "ARBITRATION_REJECT_ALL"
    top = manifest["review_queue"][0]
    assert top["band"] == "FOCUS_CONFLICT"
    assert top["fact_id"] == t7["fact_id"]


def test_missing_vision_document_degrades_to_ocr_cv(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path)
    case.pop("vision")
    _path, manifest = _run_fusion(case, "fusion-degraded")

    assert "AGENT_VISION_ABSENT" in manifest["degradations"]
    assert all(
        fact["fact_kind"] == "TEXT_CANDIDATE" for fact in manifest["facts"]
    )
    assert all(fact["detail"]["vlm_opinion"] is None for fact in manifest["facts"])
    assert manifest["summary"]["vlm_query_count"] == 0
    assert manifest["summary"]["region_fact_count"] == 0
    # TRIPLE is impossible without the VLM channel
    assert manifest["summary"]["tier_counts"]["TRIPLE"] == 0
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "fusion-manifest.schema.json").read_text("utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(manifest)


def test_new_text_hypothesis_without_ocr_is_focus_single(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path)
    response_path = Path(case["package"]).parent / "agent-vision-response.json"
    response = json.loads(response_path.read_text("utf-8"))
    miss = next(q for q in response["queries"] if q["task_type"] == "MISS_SCAN")
    miss["observation_status"] = "OBSERVED"
    miss["miss_scan"] = {
        "contains_text": True,
        "text_hypothesis": "完全新的文字",
        "reason_code": None,
    }
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    document_path = Path(case["package"]).parent / "agent-vision-document.json"
    validator.validate_response(
        package_path=Path(case["package"]),
        response_path=response_path,
        output_path=document_path,
    )
    case["vision"] = document_path

    _path, manifest = _run_fusion(case, "fusion-newtext")
    standalone = [
        f
        for f in manifest["facts"]
        if f["fact_kind"] == "TEXT_CANDIDATE" and f["detail"]["candidate_id"] is None
    ]
    assert len(standalone) == 1
    assert standalone[0]["consistency_tier"] == "SINGLE"
    assert standalone[0]["detail"]["vlm_opinion"] == "NEW_TEXT_HYPOTHESIS"
    assert "VLM_REPORTS_TEXT_WITHOUT_OCR_CANDIDATE" in standalone[0]["conflict_reasons"]
    queue_item = next(
        item for item in manifest["review_queue"] if item["fact_id"] == standalone[0]["fact_id"]
    )
    assert queue_item["band"] == "FOCUS_SINGLE"


def test_unsupported_vlm_panel_is_flagged_not_anchored(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path)
    response_path = Path(case["package"]).parent / "agent-vision-response.json"
    response = json.loads(response_path.read_text("utf-8"))
    structure = next(q for q in response["queries"] if q["task_type"] == "STRUCTURE_GLOBAL")
    structure["structure"]["panels"].append(
        {
            "panel_id": "P003",
            "bbox_source": {"x0": 60, "y0": 48, "x1": 66, "y1": 54},
            "kind": "AXIS",
            "reading_order_rank": 3,
            "reading_flow_hint": None,
            "label_text_guess": None,
        }
    )
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    document_path = Path(case["package"]).parent / "agent-vision-document.json"
    validator.validate_response(
        package_path=Path(case["package"]),
        response_path=response_path,
        output_path=document_path,
    )
    case["vision"] = document_path

    _path, manifest = _run_fusion(case, "fusion-unsupported")
    p3 = _fact_by_subject(manifest, "P003")
    assert p3["consistency_tier"] == "UNSUPPORTED_VLM_CLAIM"
    assert p3["detail"]["anchored"] is False
    assert p3["detail"]["anchored_bbox_source"] is None
    queue_item = next(
        item for item in manifest["review_queue"] if item["fact_id"] == p3["fact_id"]
    )
    assert queue_item["band"] == "FOCUS_UNSUPPORTED"


def test_foreign_vision_document_fails_closed(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_case = make_fusion_case(other_root)
    case["vision"] = other_case["vision"]
    with pytest.raises(fusion.FusionError, match="different task package"):
        _run_fusion(case, "fusion-rejected")
    assert not (Path(case["ocr"]).parent / "fusion-rejected" / "fusion-manifest.json").exists()


def test_fusion_is_deterministic_for_identical_inputs(tmp_path: Path) -> None:
    case = make_fusion_case(tmp_path)
    first_path, first = _run_fusion(case, "fusion-a")
    second_path, second = _run_fusion(case, "fusion-b")
    assert first_path.read_bytes() == second_path.read_bytes()
    assert file_hash(first_path.parent / "fusion-review-queue.md") == file_hash(
        second_path.parent / "fusion-review-queue.md"
    )


def test_review_init_consumes_fusion_ranking_and_notes(tmp_path: Path) -> None:
    import finalize_perception_review as review

    case = make_fusion_case(tmp_path)
    fusion_path, manifest = _run_fusion(case)
    decisions_path = tmp_path / "decisions.json"
    document = review.initialize_review(
        case["ocr"], decisions_path, fusion_manifest_path=fusion_path
    )

    ids = [d["candidate_id"] for d in document["decisions"]]
    ocr = json.loads(Path(case["ocr"]).read_text("utf-8"))
    candidate_ids = {c["candidate_id"] for c in ocr["text_candidates"]}
    assert set(ids) == candidate_ids
    # The highest-ranked OCR candidate in the fusion queue leads the review order.
    top_candidate = next(
        item["subject_id"]
        for item in manifest["review_queue"]
        if item["subject_id"] in candidate_ids
    )
    assert ids[0] == top_candidate

    t3 = next(d for d in document["decisions"] if d["candidate_id"] == "T0003")
    assert "latex_proposal=x = y" in t3["review_note"]
    assert "PROPOSAL_ONLY_NOT_AUTHORITATIVE" in t3["review_note"]
    # Fusion only reorders and annotates: confirmation policy is untouched.
    assert all(d["status"] == "PENDING" for d in document["decisions"])
    assert all(d["evidence"]["kind"] is None for d in document["decisions"])

    stale = json.loads(fusion_path.read_text("utf-8"))
    stale["inputs"]["ocr_manifest"]["sha256"] = "C" * 64
    stale_path = tmp_path / "stale-fusion.json"
    stale_path.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(review.ReviewError, match="not bound"):
        review.initialize_review(
            case["ocr"],
            tmp_path / "decisions-rejected.json",
            fusion_manifest_path=stale_path,
        )
