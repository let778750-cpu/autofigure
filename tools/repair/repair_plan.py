"""Deterministic, fail-closed repair planning for strict QA blockers.

The planner deliberately does not execute repairs.  It converts the blocker
strings emitted by Autofigure's QA gates into a small set of ownership classes,
binds that decision to the reference/current artifact and blocker inventory,
and proves that every blocker is covered by exactly one action.  An unfamiliar
blocker is left unclassified and makes the plan fail instead of being silently
assigned to a generic repair loop.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from tools.core.contracts import write_json

REPAIR_PLAN_SCHEMA_VERSION = "1.0.0"
CATEGORIES = (
    "contract",
    "source_model",
    "compiler",
    "host_compat",
    "evidence_only",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _is_region_contract_definition_error(blocker: str) -> bool:
    if not blocker.startswith("region-contract:"):
        return False
    definition_tokens = (
        ":element-ids-invalid",
        ":element-id-duplicate",
        ":required-relations-invalid",
        ":required-relation-duplicate",
        ":relations-exhaustive-invalid",
    )
    return any(token in blocker for token in definition_tokens)


def _is_region_contract_backend_error(blocker: str) -> bool:
    if not blocker.startswith("region-contract:"):
        return False
    backend_tokens = (
        ":binding-",
        ":visible-object-count",
        ":arrow-readback-",
        ":readback-",
    )
    return any(token in blocker for token in backend_tokens)


def _qa_code(blocker: str, namespace: str) -> str | None:
    prefix = f"{namespace}:"
    if not blocker.startswith(prefix):
        return None
    code = blocker[len(prefix) :].split(":", 1)[0]
    return code or None


def _asset_spec_contract_error(blocker: str) -> bool:
    return blocker.startswith(
        (
            "asset-spec-opportunity-map",
            "asset-spec-opportunity-members",
            "asset-spec-inventory-",
            "asset-spec-reference-sha256",
            "asset-spec-reference-bbox",
            "asset-spec-semantic-kind",
            "asset-spec-topology",
            "asset-spec-authorization",
            "asset-spec-explicit-authorization",
            "asset-spec-whole-reference-policy",
            "asset-spec-native-authorization",
            "asset-spec-implementation",
            "asset-spec-editable-declaration",
            "asset-spec-single-logical-asset",
        )
    )


def _asset_spec_source_error(blocker: str) -> bool:
    return blocker.startswith(
        (
            "asset-spec-logical-group-missing",
            "asset-spec-group-role",
            "asset-spec-members",
            "asset-spec-member-set",
            "asset-spec-member-unresolved",
            "asset-spec-member-nonleaf",
            "asset-spec-member-multiple-assets",
            "asset-spec-group-bbox",
            "asset-spec-nonuniform-bbox",
            "asset-spec-nonuniform-scale",
        )
    )


def classify_blocker(blocker: str) -> str | None:
    """Return the repair owner for one canonical blocker string.

    Rules are intentionally based on the stable QA namespace and, where the
    namespace contains one, its explicit ``source``/``backend`` stage.  Unknown
    namespaces return ``None`` so adding a new QA gate also requires an explicit
    planner decision.
    """

    if not isinstance(blocker, str) or not blocker or blocker != blocker.strip():
        return None

    # Frozen inventories and gate declarations must be repaired from the
    # designated reference, never by moving candidate artwork until a weak
    # contract happens to pass.
    if (
        blocker.startswith("reference-inventory:")
        or blocker.startswith("source-gate:source:")
        or blocker.startswith("source-gate:route:")
        or blocker.startswith("source-gate:seed:")
        or blocker.startswith("source-gate:isolation:")
        or blocker.startswith("source-gate:hash:")
        or blocker.startswith("source-gate:canvas:")
        or blocker.startswith("source-gate:semantic-metadata:")
        or blocker in {
            "source-gate:missing",
            "source-gate:invalid",
            "source-gate:reference-mismatch",
            "source-gate:route-mismatch",
            "source-gate:inventory-mismatch",
            "source-gate:candidate-mismatch",
        }
        or blocker.startswith("source-gate:decision:")
        or blocker.startswith("regions:expectation:")
        or blocker == "regions:no-critical-regions"
        or blocker.startswith("arrow-visual:expectation:")
        or blocker.startswith("contract:")
        or blocker.startswith("schema:")
        or blocker.startswith("provenance:")
        or blocker.startswith("input-route:")
        or _is_region_contract_definition_error(blocker)
        or _qa_code(blocker, "primitive") in {"P1", "P2", "P12_EXPECTATION"}
        or _qa_code(blocker, "visual-contract") in {"V1", "V2", "V3", "V10", "V30"}
        or (blocker.startswith("asset:") and blocker.endswith(":authorization-unverified"))
        or blocker.startswith("asset-contract:")
        or _asset_spec_contract_error(blocker)
    ):
        return "contract"

    # Missing/stale/hash-mismatched reports are regenerated from the unchanged
    # current artifact.  They are not permission to modify visual content.
    if (
        blocker in {
            "live-render-finalizer-unverified",
            "live-evidence-missing",
            "live-evidence-reference-mismatch",
            "live-evidence-operation-receipt-mismatch",
            "live-operation-receipt-path-mismatch",
            "live-operation-receipt-fields-missing",
            "live-operation-receipt-binding-mismatch",
            "live-operation-receipt-order-invalid",
            "live-operation-receipt-event-digest-mismatch",
            "live-operation-receipt-log-digest-invalid",
            "live-operation-receipt-log-drift",
            "bindings:artifact-hash-mismatch",
            "arrow:A20_ARTIFACT_IDENTITY",
        }
        or blocker in {
            "live-candidate-hash-mismatch",
            "live-reopened-hash-mismatch",
            "live-binding-evidence-hash-mismatch",
            "live-binding-artifact-hash-mismatch",
        }
        or blocker in {
            "arrow-compile:missing",
            "arrow-readback:missing",
            "arrow-composition:missing",
        }
        or blocker.startswith("live-evidence-")
        and (
            blocker == "live-evidence-inventory-content-missing"
            or (
                blocker.endswith("-mismatch")
                and blocker
                not in {
                    "live-evidence-reopened-inventory-content-mismatch",
                    "live-evidence-reopened-inventory-summary-mismatch",
                    "live-evidence-inventory-candidate-mismatch",
                    "live-evidence-math-summary-mismatch",
                }
            )
        )
        or blocker.startswith("math-summary:")
        and any(token in blocker for token in ("hash-mismatch", "plan-hash", "pptx-hash"))
        or blocker.startswith("repair-plan:")
    ):
        return "evidence_only"

    # These blockers say that PowerPoint did not preserve, reopen, or expose
    # the compiled object inventory.  The source contract remains protected.
    if (
        blocker in {
            "bindings:save-reopen-not-verified",
            "live-save-reopen-missing",
            "live-root-save-reopen-missing",
            "live-bindings-incomplete",
            "live-root-bindings-incomplete",
            "live-unverified-arrow-authoring",
            "live-evidence-reopened-inventory-content-mismatch",
            "live-evidence-reopened-inventory-summary-mismatch",
            "live-evidence-inventory-candidate-mismatch",
            "live-evidence-math-summary-mismatch",
        }
        or blocker.startswith("powerpoint:")
        or blocker.startswith("host:")
        or blocker.startswith("provider:")
        or blocker.startswith("math-summary:")
        and blocker.endswith(("readback-unverified", "save-reopen-unverified"))
    ):
        return "host_compat"

    # Explicit source-stage failures and reference/render visual mismatches go
    # back to reconstruction.  A live-region failure is still a failed visual
    # region; the host evidence namespace alone must not turn it into paperwork.
    if (
        blocker.startswith("region:")
        or blocker.startswith("live-region:")
        or blocker.startswith("ocr:")
        or blocker.startswith("arrow-visual:")
        or _qa_code(blocker, "visual-contract")
        in {"V4", "V5", "V11", "V12", "V20", "V21", "V31", "V34", "V36", "V38"}
        or _qa_code(blocker, "primitive")
        in {"P3", "P11_SOURCE_PATH", "P13_TRANSFORM", "P14_SYMMETRY"}
        or blocker.startswith("layout:")
        and ":source:" in blocker
        or blocker.startswith("region-contract:")
        and not _is_region_contract_backend_error(blocker)
        or blocker.startswith("source-gate:image:")
        or blocker.startswith("source-gate:unsupported-feature:")
        or _asset_spec_source_error(blocker)
    ):
        return "source_model"

    # Backend layout, semantic compilation, bindings, native math, arrows and
    # deterministic primitives are owned by the offline compiler/readback path.
    if (
        blocker.startswith("layout:")
        and ":backend:" in blocker
        or blocker.startswith("arrow:")
        or blocker.startswith("arrow-compile:")
        or blocker.startswith("arrow-readback:")
        or blocker.startswith("arrow-composition:")
        or _qa_code(blocker, "primitive")
        in {
            "P4",
            "P5",
            "P6",
            "P7",
            "P8",
            "P9",
            "P10",
            "P15_BRACE_SPEC_MIGRATION",
            "P16_BRACE_SPEC_ALIAS",
        }
        or _qa_code(blocker, "visual-contract")
        in {"V6", "V7", "V13", "V32", "V33", "V35", "V37"}
        or blocker == "bindings:incomplete"
        or blocker.startswith("math-summary:")
        or _is_region_contract_backend_error(blocker)
        or blocker.startswith("lineage:")
        or blocker.startswith("asset-spec-")
    ):
        return "compiler"

    return None


_ACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "contract": {
        "strategy": "rederive-and-refreeze-reference-contract",
        "artifact_mutation_allowed": False,
        "protected": ["reference.png", "input_route", "current candidate"],
        "required_verification": [
            "reference inventory closure",
            "contract receipt hashes",
            "critical-region expectation closure",
        ],
    },
    "source_model": {
        "strategy": "repair-or-regenerate-failed-source-regions",
        "artifact_mutation_allowed": True,
        "protected": ["passing regions", "frozen reference contracts"],
        "required_verification": [
            "region pixel and ink gates",
            "OCR and visual contracts",
            "scene topology closure",
        ],
    },
    "compiler": {
        "strategy": "fix-deterministic-compiler-and-recompile",
        "artifact_mutation_allowed": True,
        "protected": ["source scene semantics", "frozen reference contracts"],
        "required_verification": [
            "compiler report",
            "OOXML readback",
            "single-visible-object and binding closure",
        ],
    },
    "host_compat": {
        "strategy": "reproduce-in-powerpoint-and-fix-host-compatibility",
        "artifact_mutation_allowed": True,
        "protected": ["source scene semantics", "unsupported live-authoring capabilities"],
        "required_verification": [
            "PowerPoint open-save-close-reopen",
            "reopened inventory equality",
            "reopened bindings and native-object readback",
        ],
    },
    "evidence_only": {
        "strategy": "regenerate-hash-bound-evidence-from-unchanged-artifact",
        "artifact_mutation_allowed": False,
        "protected": ["redraw.svg", "redraw.pptx", "scene.json", "bindings.json"],
        "required_verification": [
            "evidence artifact identity",
            "QA report digests",
            "finalizer-bound render receipt",
        ],
    },
}


def _normalize_blockers(blockers: Iterable[str]) -> tuple[list[str], list[dict[str, Any]]]:
    valid: list[str] = []
    invalid: list[dict[str, Any]] = []
    for index, value in enumerate(blockers):
        if not isinstance(value, str):
            invalid.append(
                {"index": index, "reason": "blocker-not-string", "type": type(value).__name__}
            )
            continue
        normalized = value.strip()
        if not normalized:
            invalid.append({"index": index, "reason": "blocker-empty"})
            continue
        valid.append(normalized)
    return sorted(set(valid)), invalid


def _normalized_report_hashes(values: Mapping[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, digest in (values or {}).items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("QA report hash names must be non-empty canonical strings")
        normalized[name] = _require_sha256(digest, f"qa_report_sha256[{name!r}]")
    return dict(sorted(normalized.items()))


def _plan_digest(plan: Mapping[str, Any]) -> str:
    return _canonical_sha256({key: value for key, value in plan.items() if key != "plan_sha256"})


def build_repair_plan(
    blockers: Iterable[str],
    *,
    case: str,
    reference_sha256: str,
    artifact_sha256: str,
    qa_report_sha256: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic plan whose coverage closes over every blocker."""

    if not isinstance(case, str) or not case.strip() or case != case.strip():
        raise ValueError("case must be a non-empty canonical string")
    reference_sha256 = _require_sha256(reference_sha256, "reference_sha256")
    artifact_sha256 = _require_sha256(artifact_sha256, "artifact_sha256")
    report_hashes = _normalized_report_hashes(qa_report_sha256)
    canonical_blockers, invalid_inputs = _normalize_blockers(blockers)

    blocker_records: list[dict[str, Any]] = []
    by_category: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    unclassified: list[str] = []
    for index, blocker in enumerate(canonical_blockers, start=1):
        blocker_id = f"B{index:04d}"
        category = classify_blocker(blocker)
        blocker_records.append(
            {"id": blocker_id, "blocker": blocker, "category": category}
        )
        if category is None:
            unclassified.append(blocker_id)
        else:
            by_category[category].append(blocker_id)

    actions: list[dict[str, Any]] = []
    for category in CATEGORIES:
        covered = by_category[category]
        if not covered:
            continue
        actions.append(
            {
                "id": f"A-{category.replace('_', '-')}",
                "category": category,
                "covers": covered,
                **_ACTION_TEMPLATES[category],
            }
        )

    covered_ids = [blocker_id for action in actions for blocker_id in action["covers"]]
    coverage_counts = Counter(covered_ids)
    expected_ids = [record["id"] for record in blocker_records]
    uncovered = sorted(
        blocker_id for blocker_id in expected_ids if coverage_counts[blocker_id] == 0
    )
    multiply_covered = sorted(
        blocker_id for blocker_id, count in coverage_counts.items() if count != 1
    )
    errors = [f"invalid-input:{item['index']}:{item['reason']}" for item in invalid_inputs]
    errors.extend(f"unclassified-blocker:{blocker_id}" for blocker_id in unclassified)
    errors.extend(f"uncovered-blocker:{blocker_id}" for blocker_id in uncovered)
    errors.extend(f"multiply-covered-blocker:{blocker_id}" for blocker_id in multiply_covered)

    plan: dict[str, Any] = {
        "schema_version": REPAIR_PLAN_SCHEMA_VERSION,
        "kind": "repair-plan",
        "case": case,
        "reference_sha256": reference_sha256,
        "artifact_sha256": artifact_sha256,
        "qa_report_sha256": report_hashes,
        "input_blockers_sha256": _canonical_sha256(
            {"blockers": canonical_blockers, "invalid_inputs": invalid_inputs}
        ),
        "blockers": blocker_records,
        "actions": actions,
        "coverage": {
            "expected_blocker_ids": expected_ids,
            "covered_blocker_ids": sorted(set(covered_ids)),
            "uncovered_blocker_ids": uncovered,
            "multiply_covered_blocker_ids": multiply_covered,
        },
        "invalid_inputs": invalid_inputs,
        "classification_counts": {
            category: len(by_category[category]) for category in CATEGORIES
        },
        "errors": errors,
        "pass": not errors,
    }
    plan["plan_sha256"] = _plan_digest(plan)
    return plan


def validate_repair_plan(
    plan: Mapping[str, Any],
    *,
    expected_reference_sha256: str | None = None,
    expected_artifact_sha256: str | None = None,
    expected_qa_report_sha256: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate identity, classification and exact action coverage of a plan."""

    errors: list[str] = []
    if plan.get("schema_version") != REPAIR_PLAN_SCHEMA_VERSION:
        errors.append("schema-version")
    if plan.get("kind") != "repair-plan":
        errors.append("kind")
    if _SHA256_RE.fullmatch(str(plan.get("reference_sha256", ""))) is None:
        errors.append("reference-sha256-invalid")
    if _SHA256_RE.fullmatch(str(plan.get("artifact_sha256", ""))) is None:
        errors.append("artifact-sha256-invalid")
    if expected_reference_sha256 is not None and plan.get("reference_sha256") != expected_reference_sha256:
        errors.append("reference-sha256-mismatch")
    if expected_artifact_sha256 is not None and plan.get("artifact_sha256") != expected_artifact_sha256:
        errors.append("artifact-sha256-mismatch")
    if expected_qa_report_sha256 is not None:
        try:
            expected_reports = _normalized_report_hashes(expected_qa_report_sha256)
        except ValueError:
            errors.append("expected-qa-report-sha256-invalid")
        else:
            if plan.get("qa_report_sha256") != expected_reports:
                errors.append("qa-report-sha256-mismatch")
    try:
        normalized_reports = _normalized_report_hashes(plan.get("qa_report_sha256"))
    except (AttributeError, TypeError, ValueError):
        errors.append("qa-report-sha256-invalid")
    else:
        if plan.get("qa_report_sha256") != normalized_reports:
            errors.append("qa-report-sha256-not-canonical")

    records = plan.get("blockers")
    actions = plan.get("actions")
    if not isinstance(records, list):
        records = []
        errors.append("blockers-invalid")
    if not isinstance(actions, list):
        actions = []
        errors.append("actions-invalid")

    blocker_ids: list[str] = []
    categories_by_id: dict[str, str | None] = {}
    canonical_values: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            errors.append("blocker-record-invalid")
            continue
        blocker_id = record.get("id")
        blocker = record.get("blocker")
        category = record.get("category")
        if not isinstance(blocker_id, str) or not blocker_id:
            errors.append("blocker-id-invalid")
            continue
        blocker_ids.append(blocker_id)
        if not isinstance(blocker, str) or not blocker:
            errors.append(f"blocker-value-invalid:{blocker_id}")
            continue
        canonical_values.append(blocker)
        actual_category = classify_blocker(blocker)
        if category != actual_category or category not in CATEGORIES:
            errors.append(f"blocker-category-invalid:{blocker_id}")
        categories_by_id[blocker_id] = category if isinstance(category, str) else None
    if len(blocker_ids) != len(set(blocker_ids)):
        errors.append("blocker-ids-not-unique")
    if canonical_values != sorted(set(canonical_values)):
        errors.append("blockers-not-canonical")

    invalid_inputs = plan.get("invalid_inputs")
    if not isinstance(invalid_inputs, list):
        invalid_inputs = []
        errors.append("invalid-inputs-invalid")
    expected_input_digest = _canonical_sha256(
        {"blockers": canonical_values, "invalid_inputs": invalid_inputs}
    )
    if plan.get("input_blockers_sha256") != expected_input_digest:
        errors.append("input-blockers-sha256-mismatch")

    coverage_counts: Counter[str] = Counter()
    action_ids: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            errors.append("action-invalid")
            continue
        action_id = action.get("id")
        category = action.get("category")
        covers = action.get("covers")
        if not isinstance(action_id, str) or not action_id:
            errors.append("action-id-invalid")
            continue
        action_ids.append(action_id)
        if category not in CATEGORIES:
            errors.append(f"action-category-invalid:{action_id}")
        if not isinstance(covers, list) or not covers:
            errors.append(f"action-coverage-invalid:{action_id}")
            continue
        for blocker_id in covers:
            if not isinstance(blocker_id, str):
                errors.append(f"action-blocker-id-invalid:{action_id}")
                continue
            coverage_counts[blocker_id] += 1
            if blocker_id not in categories_by_id:
                errors.append(f"action-covers-unknown:{action_id}:{blocker_id}")
            elif categories_by_id[blocker_id] != category:
                errors.append(f"action-category-mismatch:{action_id}:{blocker_id}")
    if len(action_ids) != len(set(action_ids)):
        errors.append("action-ids-not-unique")

    uncovered = sorted(blocker_id for blocker_id in blocker_ids if coverage_counts[blocker_id] == 0)
    multiply_covered = sorted(
        blocker_id for blocker_id in blocker_ids if coverage_counts[blocker_id] > 1
    )
    errors.extend(f"uncovered-blocker:{blocker_id}" for blocker_id in uncovered)
    errors.extend(f"multiply-covered-blocker:{blocker_id}" for blocker_id in multiply_covered)

    expected_by_category = {
        category: [
            blocker_id
            for blocker_id in blocker_ids
            if categories_by_id.get(blocker_id) == category
        ]
        for category in CATEGORIES
    }
    expected_actions = [
        {
            "id": f"A-{category.replace('_', '-')}",
            "category": category,
            "covers": expected_by_category[category],
            **_ACTION_TEMPLATES[category],
        }
        for category in CATEGORIES
        if expected_by_category[category]
    ]
    if actions != expected_actions:
        errors.append("actions-not-canonical")
    expected_coverage = {
        "expected_blocker_ids": blocker_ids,
        "covered_blocker_ids": sorted(
            blocker_id for blocker_id in blocker_ids if coverage_counts[blocker_id] > 0
        ),
        "uncovered_blocker_ids": uncovered,
        "multiply_covered_blocker_ids": multiply_covered,
    }
    if plan.get("coverage") != expected_coverage:
        errors.append("coverage-summary-mismatch")
    expected_counts = {
        category: len(expected_by_category[category]) for category in CATEGORIES
    }
    if plan.get("classification_counts") != expected_counts:
        errors.append("classification-counts-mismatch")
    if plan.get("plan_sha256") != _plan_digest(plan):
        errors.append("plan-sha256-mismatch")
    if plan.get("pass") is not True or plan.get("errors"):
        errors.append("plan-self-failed")

    errors = list(dict.fromkeys(errors))
    return {
        "pass": not errors,
        "errors": errors,
        "uncovered_blocker_ids": uncovered,
        "multiply_covered_blocker_ids": multiply_covered,
    }


def write_repair_plan(
    path: Path,
    blockers: Iterable[str],
    *,
    case: str,
    reference_sha256: str,
    artifact_sha256: str,
    qa_report_sha256: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build and atomically write a repair plan JSON document."""

    plan = build_repair_plan(
        blockers,
        case=case,
        reference_sha256=reference_sha256,
        artifact_sha256=artifact_sha256,
        qa_report_sha256=qa_report_sha256,
    )
    write_json(path, plan)
    return plan
