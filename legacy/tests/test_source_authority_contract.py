from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image

from tools.validate_source_authority import SourceAuthorityError, validate_authority


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "source-authority.schema.json"
MODULARAGENT_AUTHORITY_PATH = ROOT / "examples" / "modularagent.source-authority.json"


def _latex_hash(latex: str) -> str:
    return hashlib.sha256(latex.encode("utf-8")).hexdigest()


def _document(source_path: Path, *, status: str = "FROZEN") -> dict[str, object]:
    relative_path = source_path.relative_to(source_path.parent.parent).as_posix()
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    latex = r"z_t=f_{mod}(e_v,s_t,\tau)"
    evidence = [
        {
            "kind": "source_text",
            "locator": "paper Figure 2",
            "detail": "Authoritative paper figure and surrounding method text.",
        }
    ]
    return {
        "schema_version": "1.0.0",
        "document_type": "SOURCE_AUTHORITY",
        "authority_id": "fixture-authority-v1",
        "status": status,
        "source": {
            "relative_path": relative_path,
            "sha256": source_hash,
            "width_px": 80,
            "height_px": 40,
            "pixel_format": "RGB",
            "user_confirmed": True,
        },
        "provenance": {
            "authority_kind": "PAPER_SOURCE",
            "title": "Fixture paper",
            "bibliographic_citation": "Fixture et al., 2026",
            "source_url": "https://example.test/paper",
            "figure_locator": "Figure 2",
        },
        "policy": {
            "ocr_may_confirm": False,
            "vlm_may_confirm": False,
            "pixels_may_authorize_text": False,
            "unmatched_candidates": "INCONCLUSIVE",
        },
        "items": [
            {
                "authority_item_id": "AUTH-0001",
                "subject_id": "region.encoder",
                "kind": "SEMANTIC_REGION",
                "disposition": "CONFIRMED",
                "criticality": "critical",
                "bbox_source": {"x": 2, "y": 2, "w": 25, "h": 20},
                "text": None,
                "label": "Encoder",
                "canonical_latex": None,
                "latex_sha256": None,
                "formula_mode": None,
                "relation": None,
                "source_evidence": evidence,
                "notes": "",
            },
            {
                "authority_item_id": "AUTH-0002",
                "subject_id": "text.encoder",
                "kind": "TEXT",
                "disposition": "CONFIRMED",
                "criticality": "critical",
                "bbox_source": {"x": 4, "y": 4, "w": 18, "h": 6},
                "text": "Encoder",
                "label": None,
                "canonical_latex": None,
                "latex_sha256": None,
                "formula_mode": None,
                "relation": None,
                "source_evidence": evidence,
                "notes": "",
            },
            {
                "authority_item_id": "AUTH-0003",
                "subject_id": "formula.fused",
                "kind": "FORMULA",
                "disposition": "CONFIRMED",
                "criticality": "critical",
                "bbox_source": {"x": 30, "y": 4, "w": 40, "h": 8},
                "text": None,
                "label": None,
                "canonical_latex": latex,
                "latex_sha256": _latex_hash(latex),
                "formula_mode": "inline",
                "relation": None,
                "source_evidence": evidence,
                "notes": "",
            },
            {
                "authority_item_id": "AUTH-0004",
                "subject_id": "relation.encoder-to-formula",
                "kind": "RELATION",
                "disposition": "CONFIRMED",
                "criticality": "critical",
                "bbox_source": None,
                "text": None,
                "label": None,
                "canonical_latex": None,
                "latex_sha256": None,
                "formula_mode": None,
                "relation": {
                    "from_subject_id": "region.encoder",
                    "to_subject_id": "formula.fused",
                    "direction": "FORWARD",
                    "meaning": "DATA_FLOW",
                },
                "source_evidence": evidence,
                "notes": "",
            },
        ],
        "review": {
            "approved": True,
            "reviewed_by": "fixture-reviewer",
            "reviewed_at_utc": "2026-08-16T00:00:00Z",
            "method": "source and overlay review",
        }
        if status == "FROZEN"
        else None,
    }


def _case(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    project_root = tmp_path / "project"
    examples = project_root / "examples"
    examples.mkdir(parents=True)
    source_path = examples / "source.png"
    Image.new("RGB", (80, 40), "white").save(source_path)
    document = _document(source_path)
    authority_path = project_root / "authority.json"
    authority_path.write_text(json.dumps(document), encoding="utf-8")
    return project_root, authority_path, document


def test_schema_is_valid_draft_2020_12() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def test_modularagent_authority_is_frozen_after_explicit_user_review() -> None:
    document = json.loads(MODULARAGENT_AUTHORITY_PATH.read_text(encoding="utf-8"))

    result = validate_authority(MODULARAGENT_AUTHORITY_PATH)

    assert result["status"] == "PASS"
    assert result["authority_status"] == "FROZEN"
    assert result["item_count"] == 52
    assert document["review"]["approved"] is True
    assert document["review"]["reviewed_by"] == "project user"
    assert all(item["disposition"] == "CONFIRMED" for item in document["items"])
    user_confirmed_ids = {
        item["authority_item_id"]
        for item in document["items"]
        if any(evidence["kind"] == "user_confirmed" for evidence in item["source_evidence"])
    }
    assert {
        "AUTH-0016",
        "AUTH-0017",
        "AUTH-0020",
        "AUTH-0021",
        "AUTH-0033",
    }.issubset(user_confirmed_ids)
    manual_assets = [item for item in document["items"] if item["kind"] == "MANUAL_ASSET"]
    assert [item["subject_id"] for item in manual_assets] == ["asset.observation-montage"]
    assert "manual_asset_slot" in manual_assets[0]["notes"]


def test_frozen_authority_validates_source_formula_and_relations(tmp_path: Path) -> None:
    project_root, authority_path, _document_payload = _case(tmp_path)

    result = validate_authority(authority_path, project_root=project_root)

    assert result["status"] == "PASS"
    assert result["authority_status"] == "FROZEN"
    assert result["item_count"] == 4


def test_ocr_or_vlm_cannot_be_authority_evidence(tmp_path: Path) -> None:
    project_root, authority_path, document = _case(tmp_path)
    changed = copy.deepcopy(document)
    changed["items"][0]["source_evidence"][0]["kind"] = "vlm_observed"
    authority_path.write_text(json.dumps(changed), encoding="utf-8")

    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "schema rejected authority" in str(exc)
    else:
        raise AssertionError("VLM evidence unexpectedly became authoritative")


def test_source_hash_and_canvas_bounds_fail_closed(tmp_path: Path) -> None:
    project_root, authority_path, document = _case(tmp_path)
    changed = copy.deepcopy(document)
    changed["source"]["sha256"] = "0" * 64
    authority_path.write_text(json.dumps(changed), encoding="utf-8")
    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("mismatched source hash unexpectedly passed")

    changed = copy.deepcopy(document)
    changed["items"][0]["bbox_source"] = {"x": 70, "y": 2, "w": 20, "h": 10}
    authority_path.write_text(json.dumps(changed), encoding="utf-8")
    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "exceeds the source canvas" in str(exc)
    else:
        raise AssertionError("out-of-canvas authority bbox unexpectedly passed")


def test_formula_hash_duplicate_ids_and_unknown_relations_fail(tmp_path: Path) -> None:
    project_root, authority_path, document = _case(tmp_path)
    changed = copy.deepcopy(document)
    changed["items"][2]["latex_sha256"] = "0" * 64
    authority_path.write_text(json.dumps(changed), encoding="utf-8")
    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "LaTeX hash" in str(exc)
    else:
        raise AssertionError("invalid LaTeX hash unexpectedly passed")

    changed = copy.deepcopy(document)
    changed["items"][1]["authority_item_id"] = "AUTH-0001"
    authority_path.write_text(json.dumps(changed), encoding="utf-8")
    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "duplicate authority_item_id" in str(exc)
    else:
        raise AssertionError("duplicate authority IDs unexpectedly passed")

    changed = copy.deepcopy(document)
    changed["items"][3]["relation"]["to_subject_id"] = "missing.subject"
    authority_path.write_text(json.dumps(changed), encoding="utf-8")
    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "unknown subjects" in str(exc)
    else:
        raise AssertionError("unknown relation endpoint unexpectedly passed")


def test_frozen_status_requires_an_approved_review(tmp_path: Path) -> None:
    project_root, authority_path, document = _case(tmp_path)
    changed = copy.deepcopy(document)
    changed["review"]["approved"] = False
    authority_path.write_text(json.dumps(changed), encoding="utf-8")

    try:
        validate_authority(authority_path, project_root=project_root)
    except SourceAuthorityError as exc:
        assert "schema rejected authority" in str(exc)
    else:
        raise AssertionError("unapproved frozen authority unexpectedly passed")


def test_inconclusive_text_and_formula_preserve_unknowns(tmp_path: Path) -> None:
    project_root, authority_path, document = _case(tmp_path)
    changed = copy.deepcopy(document)
    changed["items"][1]["disposition"] = "INCONCLUSIVE"
    changed["items"][1]["text"] = None
    changed["items"][1]["source_evidence"] = []
    changed["items"][2]["disposition"] = "INCONCLUSIVE"
    changed["items"][2]["canonical_latex"] = None
    changed["items"][2]["latex_sha256"] = None
    changed["items"][2]["formula_mode"] = None
    changed["items"][2]["source_evidence"] = []
    authority_path.write_text(json.dumps(changed), encoding="utf-8")

    result = validate_authority(authority_path, project_root=project_root)

    assert result["status"] == "PASS"
