from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest
from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "finalize_perception_review.py"
RAW_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
REVIEW_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "perception-review.schema.json"


def load_adapter():
    module_name = "ai_autofigure_finalize_perception_review_test"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load finalize_perception_review.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_adapter()


def _resolve_ref(root: Mapping[str, Any], reference: str) -> Mapping[str, Any]:
    value: Any = root
    for part in reference.removeprefix("#/").split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _schema_string(schema: Mapping[str, Any]) -> str:
    if schema.get("format") == "date-time":
        return "2026-08-14T00:00:00Z"
    pattern = schema.get("pattern")
    minimum = int(schema.get("minLength", 0))
    candidates = [
        "A" * 64,
        "review-run-001",
        "T0001",
        "O00001",
        "cpu",
        "x" * max(minimum, 1),
        "",
    ]
    for candidate in candidates:
        if len(candidate) < minimum:
            continue
        if pattern is None or re.search(pattern, candidate):
            return candidate
    raise AssertionError(f"Test factory cannot satisfy string schema {schema}")


def minimal_instance(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Any:
    if "$ref" in schema:
        return minimal_instance(_resolve_ref(root, schema["$ref"]), root)
    if "const" in schema:
        return copy.deepcopy(schema["const"])
    if "enum" in schema:
        return copy.deepcopy(schema["enum"][0])
    if "oneOf" in schema:
        return minimal_instance(schema["oneOf"][0], root)
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")
    if schema_type == "object":
        properties = schema.get("properties", {})
        return {
            key: minimal_instance(properties[key], root)
            for key in schema.get("required", [])
        }
    if schema_type == "array":
        count = int(schema.get("minItems", 0))
        return [minimal_instance(schema["items"], root) for _ in range(count)]
    if schema_type == "string":
        return _schema_string(schema)
    if schema_type == "integer":
        return int(schema.get("minimum", 0))
    if schema_type == "number":
        return float(schema.get("minimum", 0))
    if schema_type == "boolean":
        return False
    if schema_type == "null":
        return None
    raise AssertionError(f"Test factory cannot construct schema {schema}")


def build_raw_manifest() -> dict[str, Any]:
    schema = json.loads(RAW_SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = minimal_instance(schema, schema)
    manifest["run_id"] = "review-run-001"
    manifest["created_at_utc"] = "2026-08-14T00:00:00Z"
    manifest["status"] = "OCR_HYPOTHESES_REVIEW_REQUIRED"
    manifest["acceptance_checks"]["passed"] = True
    manifest["source"]["path"] = "D:/fixtures/source.png"
    manifest["source"]["sha256"] = "B" * 64
    manifest["configuration"]["manifest_schema_sha256"] = adapter.sha256_file(
        RAW_SCHEMA_PATH
    )

    candidate_schema = _resolve_ref(schema, "#/$defs/candidate")
    first = minimal_instance(candidate_schema, schema)
    first.update(
        {
            "candidate_id": "T0001",
            "text": "Mamba",
            "normalized_text": "mamba",
            "ocr_confidence": 0.995,
            "review_flags": [],
        }
    )
    second = copy.deepcopy(first)
    second.update(
        {
            "candidate_id": "T0002",
            "text": "x2+y2",
            "normalized_text": "x2+y2",
            "ocr_confidence": 0.91,
            "review_flags": ["FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"],
        }
    )
    manifest["text_candidates"] = [first, second]
    manifest["summary"]["candidate_count"] = 2
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    return manifest


def write_raw(tmp_path: Path, manifest: dict[str, Any] | None = None) -> Path:
    path = tmp_path / "perception-manifest.json"
    path.write_text(
        json.dumps(manifest or build_raw_manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def initialize(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    raw_path = write_raw(tmp_path)
    decisions_path = tmp_path / "perception-review-decisions.json"
    decisions = adapter.initialize_review(raw_path, decisions_path)
    return raw_path, decisions_path, decisions


def set_evidence(decision: dict[str, Any], kind: str = "user_confirmed") -> None:
    decision["evidence"] = {
        "kind": kind,
        "detail": "Reviewed against the frozen source image at 200% zoom.",
    }


def write_decisions(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def test_init_creates_schema_valid_pending_template_with_exact_hash_binding(tmp_path: Path):
    raw_path, decisions_path, document = initialize(tmp_path)
    schema = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)

    assert decisions_path.is_file()
    assert document["raw_manifest"]["manifest_sha256"] == adapter.sha256_file(raw_path)
    assert document["raw_manifest"]["source_sha256"] == "B" * 64
    assert document["raw_manifest"]["run_id"] == "review-run-001"
    assert [item["candidate_id"] for item in document["decisions"]] == ["T0001", "T0002"]
    assert all(item["status"] == "PENDING" for item in document["decisions"])
    assert all(item["evidence"]["kind"] is None for item in document["decisions"])


def test_finalize_passes_only_after_normal_text_and_formula_authority(tmp_path: Path):
    raw_path, decisions_path, document = initialize(tmp_path)
    ordinary, formula = document["decisions"]
    ordinary["status"] = "CONFIRMED"
    ordinary["confirmed_text"] = ordinary["ocr_text"]
    set_evidence(ordinary)
    formula["status"] = "FORMULA_CONFIRMED"
    formula["authoritative_latex"] = r"x^2+y^2"
    set_evidence(formula, "source_text")
    write_decisions(decisions_path, document)

    receipt_path = tmp_path / "perception-review-receipt.json"
    receipt, exit_code = adapter.finalize_review(raw_path, decisions_path, receipt_path)

    assert exit_code == 0
    assert receipt["status"] == "PERCEPTION_REVIEW_PASS"
    assert receipt["counts"] == {
        "total_candidates": 2,
        "input_decisions": 2,
        "terminal_count": 2,
        "confirmed_count": 1,
        "corrected_count": 0,
        "not_text_count": 0,
        "formula_confirmed_count": 1,
        "pending_count": 0,
        "inconclusive_count": 0,
        "missing_count": 0,
        "unresolved_count": 0,
        "gate_blocker_count": 0,
    }
    assert receipt["raw_manifest"]["manifest_sha256"] == adapter.sha256_file(raw_path)
    assert receipt["review_input"]["sha256"] == adapter.sha256_file(decisions_path)
    assert receipt_path.is_file()


def test_pending_and_missing_decisions_write_inconclusive_receipt_and_exit_3(tmp_path: Path):
    raw_path, decisions_path, document = initialize(tmp_path)
    document["decisions"].pop()
    write_decisions(decisions_path, document)
    receipt_path = tmp_path / "receipt.json"

    exit_code = adapter.main(
        [
            "--manifest",
            str(raw_path),
            "--decisions",
            str(decisions_path),
            "--output",
            str(receipt_path),
        ]
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert exit_code == 3
    assert receipt["status"] == "INCONCLUSIVE"
    assert receipt["counts"]["missing_count"] == 1
    assert receipt["counts"]["pending_count"] == 1
    assert receipt["counts"]["inconclusive_count"] == 1
    assert receipt["counts"]["unresolved_count"] == 2
    assert receipt["missing_candidate_ids"] == ["T0002"]
    assert receipt["unresolved_candidate_ids"] == ["T0001", "T0002"]
    assert [item["candidate_id"] for item in receipt["decisions"]] == ["T0001", "T0002"]


def test_raw_manifest_must_validate_against_checked_in_schema(tmp_path: Path):
    manifest = build_raw_manifest()
    manifest["policy"]["ocr_is_ground_truth"] = True
    raw_path = write_raw(tmp_path, manifest)

    exit_code = adapter.main(
        [
            "--init",
            "--manifest",
            str(raw_path),
            "--decisions",
            str(tmp_path / "decisions.json"),
        ]
    )
    assert exit_code == 2
    assert not (tmp_path / "decisions.json").exists()


@pytest.mark.parametrize("case", ["duplicate", "extra"])
def test_candidate_ids_must_be_an_exact_unique_set(tmp_path: Path, case: str):
    raw_path, decisions_path, document = initialize(tmp_path)
    if case == "duplicate":
        duplicate = copy.deepcopy(document["decisions"][0])
        duplicate["review_note"] = "same ID, different object"
        document["decisions"].append(duplicate)
    else:
        extra = copy.deepcopy(document["decisions"][0])
        extra["candidate_id"] = "T9999"
        document["decisions"].append(extra)
    write_decisions(decisions_path, document)

    with pytest.raises(adapter.ReviewError):
        adapter.finalize_review(raw_path, decisions_path, tmp_path / "receipt.json")
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("formula_as_confirmed", "schema-valid"),
        ("formula_without_latex", "schema-valid"),
        ("empty_confirmed_text", "schema-valid"),
        ("ocr_as_evidence", "schema-valid"),
        ("snapshot_tamper", "snapshot differs"),
        ("binding_tamper", "binding mismatch"),
    ],
)
def test_invalid_or_self_confirming_reviews_fail_closed(
    tmp_path: Path, mutation: str, expected_fragment: str
):
    raw_path, decisions_path, document = initialize(tmp_path)
    ordinary, formula = document["decisions"]
    if mutation == "formula_as_confirmed":
        formula["status"] = "CONFIRMED"
        formula["confirmed_text"] = formula["ocr_text"]
        set_evidence(formula)
    elif mutation == "formula_without_latex":
        formula["status"] = "FORMULA_CONFIRMED"
        formula["authoritative_latex"] = ""
        set_evidence(formula, "source_text")
    elif mutation == "empty_confirmed_text":
        ordinary["status"] = "CORRECTED"
        ordinary["confirmed_text"] = ""
        set_evidence(ordinary)
    elif mutation == "ocr_as_evidence":
        ordinary["status"] = "CONFIRMED"
        ordinary["confirmed_text"] = ordinary["ocr_text"]
        ordinary["evidence"] = {"kind": "OCR_HYPOTHESIS", "detail": "score=0.995"}
    elif mutation == "snapshot_tamper":
        ordinary["review_flags"] = ["FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"]
        ordinary["formula_like"] = True
    elif mutation == "binding_tamper":
        document["raw_manifest"]["source_sha256"] = "C" * 64
    write_decisions(decisions_path, document)

    with pytest.raises(adapter.ReviewError, match=expected_fragment):
        adapter.finalize_review(raw_path, decisions_path, tmp_path / "receipt.json")
    assert not (tmp_path / "receipt.json").exists()


def test_confirmed_text_change_requires_corrected_status(tmp_path: Path):
    raw_path, decisions_path, document = initialize(tmp_path)
    ordinary = document["decisions"][0]
    ordinary["status"] = "CONFIRMED"
    ordinary["confirmed_text"] = "Marnba"
    set_evidence(ordinary)
    write_decisions(decisions_path, document)

    with pytest.raises(adapter.ReviewError, match="use CORRECTED"):
        adapter.finalize_review(raw_path, decisions_path, tmp_path / "receipt.json")


def test_raw_inconclusive_gate_cannot_be_overridden_by_resolved_candidate_reviews(
    tmp_path: Path,
):
    manifest = build_raw_manifest()
    manifest["status"] = "OCR_HYPOTHESES_INCONCLUSIVE"
    manifest["acceptance_checks"]["passed"] = False
    raw_path = write_raw(tmp_path, manifest)
    decisions_path = tmp_path / "decisions.json"
    document = adapter.initialize_review(raw_path, decisions_path)
    for decision in document["decisions"]:
        if decision["formula_like"]:
            decision["status"] = "FORMULA_CONFIRMED"
            decision["authoritative_latex"] = r"x^2+y^2"
        else:
            decision["status"] = "CONFIRMED"
            decision["confirmed_text"] = decision["ocr_text"]
        set_evidence(decision)
    write_decisions(decisions_path, document)

    receipt, exit_code = adapter.finalize_review(
        raw_path, decisions_path, tmp_path / "receipt.json"
    )
    assert exit_code == 3
    assert receipt["counts"]["unresolved_count"] == 0
    assert receipt["counts"]["gate_blocker_count"] == 2
    assert receipt["status"] == "INCONCLUSIVE"


def test_atomic_writer_leaves_no_partial_sibling(tmp_path: Path):
    destination = tmp_path / "review.json"
    adapter.atomic_write_json(destination, {"value": "Δ", "ok": True})
    assert json.loads(destination.read_text(encoding="utf-8"))["value"] == "Δ"
    assert list(tmp_path.glob(".*.tmp")) == []
