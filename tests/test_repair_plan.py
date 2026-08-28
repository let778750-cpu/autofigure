from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.repair.repair_plan import (
    CATEGORIES,
    build_repair_plan,
    classify_blocker,
    validate_repair_plan,
    write_repair_plan,
)

REFERENCE_SHA256 = "a" * 64
ARTIFACT_SHA256 = "b" * 64
REPORT_SHA256 = {"qa/regions-report.json": "c" * 64}


def _plan(blockers: list[str]):
    return build_repair_plan(
        blockers,
        case="case-04",
        reference_sha256=REFERENCE_SHA256,
        artifact_sha256=ARTIFACT_SHA256,
        qa_report_sha256=REPORT_SHA256,
    )


def _refresh_self_digest(plan: dict) -> None:
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    plan["plan_sha256"] = hashlib.sha256(encoded).hexdigest()


def test_representative_blockers_are_classified_and_covered_exactly_once():
    blockers = [
        "reference-inventory:not-frozen",
        "region:dna-offline",
        "arrow:A13_COMPILE_FIDELITY_LOSS:edge-01:arrowhead-fallback",
        "live-bindings-incomplete",
        "live-render-finalizer-unverified",
    ]

    plan = _plan(blockers)

    assert plan["pass"] is True
    assert plan["classification_counts"] == dict.fromkeys(CATEGORIES, 1)
    assert plan["coverage"]["uncovered_blocker_ids"] == []
    assert plan["coverage"]["multiply_covered_blocker_ids"] == []
    assert validate_repair_plan(
        plan,
        expected_reference_sha256=REFERENCE_SHA256,
        expected_artifact_sha256=ARTIFACT_SHA256,
        expected_qa_report_sha256=REPORT_SHA256,
    )["pass"] is True
    assert all(len(action["covers"]) == 1 for action in plan["actions"])


def test_classification_uses_stage_and_evidence_semantics():
    assert classify_blocker("regions:expectation:count-mismatch") == "contract"
    assert classify_blocker("layout:L2:source:panel-offline") == "source_model"
    assert classify_blocker("layout:L2:backend:panel-offline") == "compiler"
    assert classify_blocker("arrow-compile:missing") == "evidence_only"
    assert classify_blocker("bindings:save-reopen-not-verified") == "host_compat"
    assert classify_blocker("live-evidence-missing") == "evidence_only"
    assert classify_blocker("live-evidence-layout-audit-mismatch") == "evidence_only"
    assert classify_blocker("live-evidence-arrow-readback-mismatch") == "evidence_only"
    assert classify_blocker("live-candidate-hash-mismatch") == "evidence_only"
    assert classify_blocker("region-contract:r1:binding-missing:edge") == "compiler"
    assert classify_blocker("visual-contract:V38:dna") == "source_model"
    assert classify_blocker("visual-contract:V7:dna") == "compiler"
    assert classify_blocker("primitive:P14_SYMMETRY:brace") == "source_model"
    assert classify_blocker("primitive:P9:brace") == "compiler"
    assert (
        classify_blocker("primitive:P15_BRACE_SPEC_MIGRATION:brace") == "compiler"
    )
    assert classify_blocker("primitive:P16_BRACE_SPEC_ALIAS:brace") == "compiler"
    assert classify_blocker("asset-spec-opportunity-map:0") == "contract"
    assert classify_blocker("asset-contract:receipt-missing") == "contract"
    assert (
        classify_blocker("asset-spec-logical-group-missing:asset-a")
        == "source_model"
    )
    assert classify_blocker("asset-spec-readback-hash:node-a") == "compiler"
    assert classify_blocker("asset-spec-future-compiler-gate:asset-a") == "compiler"


def test_plan_is_deterministic_across_order_and_duplicate_inputs():
    blockers = ["region:z", "ocr:reference-text-unmatched", "region:a"]
    first = _plan(blockers)
    second = _plan(list(reversed(blockers)) + ["region:a"])

    assert first["blockers"] == second["blockers"]
    assert first["actions"] == second["actions"]
    assert first["input_blockers_sha256"] == second["input_blockers_sha256"]
    assert first["plan_sha256"] == second["plan_sha256"]


def test_unknown_blocker_fails_closed_and_remains_uncovered():
    plan = _plan(["future-gate:G99:new-semantics"])

    assert plan["pass"] is False
    assert plan["blockers"][0]["category"] is None
    assert plan["coverage"]["uncovered_blocker_ids"] == ["B0001"]
    assert "unclassified-blocker:B0001" in plan["errors"]
    assert "uncovered-blocker:B0001" in plan["errors"]
    validation = validate_repair_plan(plan)
    assert validation["pass"] is False
    assert "uncovered-blocker:B0001" in validation["errors"]
    assert "plan-self-failed" in validation["errors"]


def test_missing_or_duplicate_action_coverage_is_a_validation_failure():
    plan = _plan(["region:a", "ocr:reference-text-unmatched"])
    missing = copy.deepcopy(plan)
    missing["actions"][0]["covers"].remove("B0001")
    result = validate_repair_plan(missing)
    assert result["pass"] is False
    assert "uncovered-blocker:B0001" in result["errors"]
    assert "plan-sha256-mismatch" in result["errors"]

    duplicate = copy.deepcopy(plan)
    duplicate["actions"][0]["covers"].append("B0001")
    result = validate_repair_plan(duplicate)
    assert result["pass"] is False
    assert "multiply-covered-blocker:B0001" in result["errors"]


def test_plan_hash_binds_reference_artifact_reports_and_action_content():
    baseline = _plan(["region:dna"])
    other_artifact = build_repair_plan(
        ["region:dna"],
        case="case-04",
        reference_sha256=REFERENCE_SHA256,
        artifact_sha256="d" * 64,
        qa_report_sha256=REPORT_SHA256,
    )
    other_report = build_repair_plan(
        ["region:dna"],
        case="case-04",
        reference_sha256=REFERENCE_SHA256,
        artifact_sha256=ARTIFACT_SHA256,
        qa_report_sha256={"qa/regions-report.json": "e" * 64},
    )
    assert len({baseline["plan_sha256"], other_artifact["plan_sha256"], other_report["plan_sha256"]}) == 3

    tampered = copy.deepcopy(baseline)
    tampered["actions"][0]["strategy"] = "silently-ignore"
    _refresh_self_digest(tampered)
    validation = validate_repair_plan(tampered)
    assert validation["pass"] is False
    assert "actions-not-canonical" in validation["errors"]


def test_input_digest_and_coverage_summary_are_independently_recomputed():
    baseline = _plan(["region:dna"])
    tampered_digest = copy.deepcopy(baseline)
    tampered_digest["input_blockers_sha256"] = "0" * 64
    _refresh_self_digest(tampered_digest)
    assert "input-blockers-sha256-mismatch" in validate_repair_plan(tampered_digest)[
        "errors"
    ]

    tampered_coverage = copy.deepcopy(baseline)
    tampered_coverage["coverage"]["covered_blocker_ids"] = []
    _refresh_self_digest(tampered_coverage)
    assert "coverage-summary-mismatch" in validate_repair_plan(tampered_coverage)[
        "errors"
    ]


def test_evidence_only_action_forbids_artifact_mutation():
    plan = _plan(
        [
            "arrow-readback:missing",
            "bindings:artifact-hash-mismatch",
            "live-render-finalizer-unverified",
        ]
    )
    action = plan["actions"][0]
    assert action["category"] == "evidence_only"
    assert action["artifact_mutation_allowed"] is False
    assert action["covers"] == ["B0001", "B0002", "B0003"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reference_sha256", "A" * 64),
        ("artifact_sha256", "not-a-digest"),
    ],
)
def test_invalid_identity_hashes_are_rejected(field: str, value: str):
    kwargs = {
        "case": "case-04",
        "reference_sha256": REFERENCE_SHA256,
        "artifact_sha256": ARTIFACT_SHA256,
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        build_repair_plan(["region:dna"], **kwargs)


def test_invalid_blocker_input_makes_plan_self_fail_without_losing_valid_work():
    plan = build_repair_plan(
        ["region:dna", "  ", 7],  # type: ignore[list-item]
        case="case-04",
        reference_sha256=REFERENCE_SHA256,
        artifact_sha256=ARTIFACT_SHA256,
    )
    assert plan["classification_counts"]["source_model"] == 1
    assert len(plan["invalid_inputs"]) == 2
    assert plan["pass"] is False


def test_write_repair_plan_uses_atomic_project_json_writer(tmp_path: Path):
    path = tmp_path / "repair-plan.json"
    plan = write_repair_plan(
        path,
        ["primitive:P14_SYMMETRY:brace-01"],
        case="case-02",
        reference_sha256=REFERENCE_SHA256,
        artifact_sha256=ARTIFACT_SHA256,
        qa_report_sha256={"qa/primitive-audit.json": "f" * 64},
    )
    assert json.loads(path.read_text(encoding="utf-8")) == plan
    assert validate_repair_plan(plan)["pass"] is True
