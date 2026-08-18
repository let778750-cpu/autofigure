from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from tools.calibrate_geometry_promotion import calibrate as calibrate_geometry
from tools.calibrate_ocr_consensus import calibrate as calibrate_ocr
from tools.materialize_reference_atomic_asset import (
    ReferenceAtomicAssetError,
    materialize_reference_atomic_asset,
)
from tools.migrate_figure_spec_v3_to_v4 import upgrade_edges, upgrade_elements
from tools.perception_policy import (
    consensus_eligible,
    deterministic_sample_ids,
    region_for_candidate,
    sampled_error_regions,
    semantic_role_for_candidate,
)
from tools.render_strategy import classify_edge, classify_element
from tools.run_state import (
    RunStateError,
    advance_run_state,
    initialize_run_state,
    load_run_state,
    release_ceiling_for_elements,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_v3_migration_removes_overloaded_micro_asset_and_preserves_via() -> None:
    elements = upgrade_elements(
        [
            {
                "id": "asset.compound",
                "type": "micro_asset",
                "semantic_role": "compound_structure",
                "strategy": "native_editable",
                "source_evidence": ["target_visual"],
            }
        ]
    )
    edges = upgrade_edges(
        [
            {
                "id": "edge.1",
                "from": "a",
                "to": "b",
                "via": [{"x": 4, "y": 5}],
                "style": {"arrowhead": "triangle", "width_px": 2},
            }
        ]
    )
    assert elements[0]["type"] == "group"
    assert elements[0]["render_strategy"] == "native_preferred"
    assert edges[0]["representation"] == "native_line_chain"
    assert edges[0]["via"] == [{"x": 4, "y": 5}]
    assert edges[0]["style"]["end_arrowhead"] == "triangle"


def test_classification_is_selected_before_drawing() -> None:
    assert classify_element({"type": "text", "semantic_role": "ordinary"}) == "native_required"
    assert classify_element({"type": "reference_atomic_asset", "semantic_role": "complex_icon"}) == "reference_atomic_asset"
    assert classify_edge({"via": [{"x": 1, "y": 2}], "style": {}}) == ("native_line_chain", "thin_connector")
    assert classify_edge({"visual_asset_id": "asset.arrow", "style": {}}) == ("reference_atomic_asset", "style_asset")


def test_atomic_asset_is_exact_and_text_overlap_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (40, 30), (22, 99, 188)).save(source)
    asset = tmp_path / "asset.png"
    receipt = tmp_path / "asset.json"
    result = materialize_reference_atomic_asset(
        source,
        _sha(source),
        [5, 4, 12, 10],
        asset,
        receipt,
        role="photo",
        semantic_object_count=1,
        rights_basis="User-supplied designated reference.",
        source_user_confirmed=True,
    )
    assert result["classification"]["result"] == "ISOLATED"
    assert result["qa"]["opaque_pixel_rgb_mae"] == 0
    assert Image.open(asset).size == (12, 10)

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source": {"sha256": _sha(source)},
                "text_candidates": [{"candidate_id": "T0001", "bbox_source": {"x": 7, "y": 5, "w": 4, "h": 4}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReferenceAtomicAssetError, match="overlaps reconstructable OCR"):
        materialize_reference_atomic_asset(
            source,
            _sha(source),
            [5, 4, 12, 10],
            tmp_path / "blocked.png",
            tmp_path / "blocked.json",
            role="photo",
            semantic_object_count=1,
            rights_basis="User-supplied designated reference.",
            perception_manifest_path=manifest,
            source_user_confirmed=True,
        )


def test_atomic_asset_accepts_only_reviewed_not_text_overlap(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source_image = Image.new("RGB", (40, 30), (250, 250, 250))
    source_image.paste((30, 90, 170), (7, 5, 11, 9))
    source_image.save(source)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source": {"sha256": _sha(source)},
                "text_candidates": [
                    {"candidate_id": "T0001", "bbox_source": {"x": 7, "y": 5, "w": 4, "h": 4}}
                ],
            }
        ),
        encoding="utf-8",
    )
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "document_type": "PERCEPTION_REVIEW_RECEIPT",
                "status": "PERCEPTION_REVIEW_PASS",
                "raw_manifest": {
                    "manifest_sha256": _sha(manifest),
                    "source_sha256": _sha(source),
                },
                "decisions": [{"candidate_id": "T0001", "status": "NOT_TEXT"}],
            }
        ),
        encoding="utf-8",
    )
    result = materialize_reference_atomic_asset(
        source,
        _sha(source),
        [5, 4, 12, 10],
        tmp_path / "asset.png",
        tmp_path / "asset.json",
        role="complex_icon",
        semantic_object_count=1,
        rights_basis="User-supplied designated reference.",
        perception_manifest_path=manifest,
        perception_review_receipt_path=review,
        source_user_confirmed=True,
    )
    assert result["classification"]["ocr_overlap_candidate_ids"] == ["T0001"]
    assert result["classification"]["reviewed_not_text_candidate_ids"] == ["T0001"]


def test_run_state_is_append_only_and_generator_cannot_approve(tmp_path: Path) -> None:
    run = tmp_path / "perception-20260817T000000Z-aaaaaaaa-000001"
    run.mkdir()
    source = run / "source.png"
    Image.new("RGB", (2, 2), "white").save(source)
    evidence = run / "perception.json"
    evidence.write_text("{}\n", encoding="utf-8")
    initialize_run_state(run, source, _sha(source), policy_profile="standard")
    advance_run_state(
        run,
        "PERCEPTION_COMPLETE",
        actor="runner",
        stage="perception",
        evidence_paths=[evidence],
        note="test",
    )
    state = load_run_state(run)
    assert state["current_state"] == "PERCEPTION_COMPLETE"
    assert state["event_log"]["event_count"] == 2
    with pytest.raises(RunStateError, match="illegal run-state transition"):
        advance_run_state(
            run,
            "APPROVED",
            actor="runner",
            stage="release",
            evidence_paths=[evidence],
            note="forbidden",
        )


def test_manual_slots_lower_ceiling_but_atomic_assets_do_not() -> None:
    assert release_ceiling_for_elements([{"type": "reference_atomic_asset"}]) == "CANDIDATE"
    assert release_ceiling_for_elements(
        [{"type": "manual_asset_slot", "slot_contract": {"mode": "backfilled_verified"}}]
    ) == "CANDIDATE_WITH_SLOTS"


def test_ocr_threshold_is_fixture_calibrated_and_sampling_is_source_stable(tmp_path: Path) -> None:
    fixture = tmp_path / "ocr-fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "version": "fixture-v1",
                "samples": [
                    {"eligible": True, "ocr_confidence": 0.9 + index / 1000, "correct": True}
                    for index in range(30)
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = calibrate_ocr(fixture, "standard", minimum_selected=30)
    assert receipt["status"] == "PASS"
    assert receipt["selected_min_confidence"] == pytest.approx(0.9)
    ids = [f"T{index:04d}" for index in range(1, 21)]
    assert deterministic_sample_ids("A" * 64, ids, fraction=0.1, minimum=3) == deterministic_sample_ids(
        "A" * 64, list(reversed(ids)), fraction=0.1, minimum=3
    )
    assert len(deterministic_sample_ids("A" * 64, ids, fraction=0.1, minimum=3)) == 3


def test_consensus_requires_agent_primary_and_noncritical_structural_context() -> None:
    candidate = {
        "candidate_id": "T0001",
        "text": "Encoder",
        "ocr_confidence": 0.99,
        "bbox_source": {"x": 10, "y": 10, "w": 20, "h": 10},
        "alternatives": [],
        "review_flags": [],
    }
    fusion = {
        "facts": [
            {
                "fact_kind": "TEXT_CANDIDATE",
                "subject_id": "T0001",
                "consistency_tier": "PAIR",
                "conflict_reasons": [],
                "detail": {"ocr_alternative_count": 0, "vlm_opinion": "CONFIRMS_TEXT"},
            },
            {
                "fact_kind": "REGION_STRUCTURE",
                "subject_id": "P001",
                "detail": {
                    "vlm_kind": "PROCESS_PANEL",
                    "contained_candidate_ids": ["T0001"],
                    "vlm_bbox_source": {"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                    "anchored_bbox_source": None,
                },
            },
        ]
    }
    fact = fusion["facts"][0]
    allowed, reason = consensus_eligible(
        candidate,
        fact,
        {"selected_min_confidence": 0.95},
        semantic_role=semantic_role_for_candidate("T0001", fusion),
    )
    assert (allowed, reason) == (True, "eligible")
    assert region_for_candidate(candidate, fusion) == "P001"
    candidate["text"] = "Encoder 2"
    assert consensus_eligible(candidate, fact, {"selected_min_confidence": 0.95})[0] is False


def test_corrected_consensus_audit_sample_escalates_its_structural_region() -> None:
    candidates = {
        "T0001": {
            "candidate_id": "T0001",
            "bbox_source": {"x": 10, "y": 10, "w": 20, "h": 10},
        },
        "T0002": {
            "candidate_id": "T0002",
            "bbox_source": {"x": 40, "y": 10, "w": 20, "h": 10},
        },
    }
    fusion = {
        "facts": [
            {
                "fact_kind": "REGION_STRUCTURE",
                "subject_id": "P001",
                "detail": {
                    "contained_candidate_ids": ["T0001", "T0002"],
                    "vlm_bbox_source": {"x0": 0, "y0": 0, "x1": 100, "y1": 100},
                    "anchored_bbox_source": None,
                },
            }
        ]
    }
    decisions = {
        "T0001": {"candidate_id": "T0001", "status": "CORRECTED"},
        "T0002": {"candidate_id": "T0002", "status": "CONFIRMED"},
    }
    assert sampled_error_regions(decisions, candidates, fusion, {"T0001"}) == {"P001"}
    decisions["T0001"]["status"] = "CONFIRMED"
    assert sampled_error_regions(decisions, candidates, fusion, {"T0001"}) == set()


def test_geometry_calibration_requires_four_legends_and_thirty_per_promoted_class(tmp_path: Path) -> None:
    fixture = tmp_path / "geometry-fixture.json"
    classes = ["horizontal_single_line_ink_bbox", "clear_frame", "same_row_gap"]
    samples = [
        {"class_id": class_id, "error_px": 0.5, "high_risk": False, "promoted": True}
        for class_id in classes
        for _ in range(30)
    ]
    fixture.write_text(
        json.dumps(
            {
                "version": "geometry-v1",
                "legend_classes": ["single_line", "frame", "gap", "excluded_high_risk"],
                "samples": samples,
            }
        ),
        encoding="utf-8",
    )
    receipt = calibrate_geometry(fixture, "standard")
    assert receipt["status"] == "PASS"
    assert {record["status"] for record in receipt["classes"]} == {"PROMOTABLE"}
    strict = calibrate_geometry(fixture, "strict")
    assert strict["status"] == "FAIL"
