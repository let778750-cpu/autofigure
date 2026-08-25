"""Deterministic tests for Epic/Stage pull-request governance."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check_pr_governance.py"
SPEC = importlib.util.spec_from_file_location("autofigure_pr_governance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
governance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = governance
SPEC.loader.exec_module(governance)

POLICY = json.loads(
    (ROOT / ".github" / "governance-policy.json").read_text(encoding="utf-8")
)
TEST_POLICY = json.loads(json.dumps(POLICY))
TEST_POLICY["pr_author"]["allowed_bot_logins"] = [governance.FIXTURE_BOT]
TEST_POLICY["governance_contract"]["release_preparation"]["state"] = "active"
TODAY = date(2026, 8, 25)


def _validate(payload, changes=None, **kwargs):
    evidence = kwargs.pop(
        "contract_evidence", governance.fixture_contract_evidence(payload)
    )
    preparation_record = kwargs.pop(
        "release_preparation_record",
        governance.fixture_release_preparation_record(payload, evidence),
    )
    governed_changes = governance.with_contract_change(
        changes or governance._fixture_changes(), evidence
    )
    return governance.validate(
        payload,
        governed_changes,
        TEST_POLICY,
        scope_freeze_valid=kwargs.pop("scope_freeze_valid", True),
        issue_records=kwargs.pop(
            "issue_records", governance.fixture_issue_records(payload)
        ),
        contract_evidence=evidence,
        release_preparation_record=preparation_record,
        today=TODAY,
        **kwargs,
    )


def test_valid_versioned_feature_stage_passes() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    result = _validate(payload)
    assert result.errors == []
    assert result.warnings == []


def test_governance_bootstrap_branch_uses_stage_only_path() -> None:
    payload = governance.fixture_payload(
        "develop",
        "codex/governance-bootstrap-v1",
        "feature",
        stage="governance-bootstrap@v1",
    )
    assert _validate(payload).errors == []


def test_branch_stage_and_version_must_match_metadata() -> None:
    payload = governance.fixture_payload(
        "develop", "codex/other-v2", "feature"
    )
    result = _validate(payload)
    assert "branch stage slug does not match Stage metadata" in result.errors
    assert "branch stage version does not match Stage metadata" in result.errors


def test_only_versioned_codex_head_may_target_develop_normally() -> None:
    payload = governance.fixture_payload("develop", "codex/old-topic", "feature")
    result = _validate(payload)
    assert any("codex/<stage-slug>-vN" in item for item in result.errors)


def test_integration_is_r2_and_has_fourteen_day_sunset() -> None:
    payload = governance.fixture_payload(
        "develop", "codex/integration-dna-v1", "integration"
    )
    assert _validate(payload).errors == []

    low_risk = governance.fixture_payload(
        "develop", "codex/integration-dna-v1", "integration"
    )
    low_risk["pull_request"]["body"] = low_risk["pull_request"]["body"].replace(
        "Risk-Level: R2", "Risk-Level: R1"
    )
    assert "codex/integration-* is an R2-only exception" in _validate(low_risk).errors

    long_lived = governance.fixture_payload(
        "develop",
        "codex/integration-dna-v1",
        "integration",
        sunset="2026-09-20",
    )
    assert any("exceeds 14 days" in item for item in _validate(long_lived).errors)

    wrong_stage = governance.fixture_payload(
        "develop", "codex/integration-other-v1", "integration"
    )
    assert "branch stage slug does not match Stage metadata" in _validate(
        wrong_stage
    ).errors


def test_topic_branch_used_by_closed_unmerged_pr_cannot_be_reused() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    prior = [{"number": 12, "merged_at": None, "state": "closed"}]
    result = _validate(payload, prior_branch_prs=prior)
    assert any("already used by #12" in item for item in result.errors)


def test_scope_thresholds_warn_and_require_explanation() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    payload["pull_request"]["body"] = payload["pull_request"]["body"].replace(
        "Included-Issues: #42",
        "Included-Issues: #42, #44, #45, #46",
    )
    changes = governance._fixture_changes(31, 50)
    result = _validate(payload, changes)
    assert len(result.warnings) == 3
    assert any("Scope Threshold Explanation" in item for item in result.errors)

    explained = governance.fixture_payload(
        "develop",
        "codex/dna-v1",
        "feature",
        threshold_explanation=(
            "included-issues: All four Issues deliver the same indivisible schema outcome.\n"
            "source-test-files: Validators and tests share one failure boundary and revision.\n"
            "non-generated-loc: The migration and compatibility layer must land together.\n"
            "Atomic-Outcome: One schema revision is accepted by every governed consumer.\n"
            "Shared-Failure-Mechanism: Splitting makes old and new validators disagree.\n"
            "Shared-Validation: One mutation suite exercises all consumers at the same head.\n"
            "Rollback-Reason: The complete schema squash is the only coherent rollback unit."
        ),
    )
    explained["pull_request"]["body"] = explained["pull_request"]["body"].replace(
        "Included-Issues: #42",
        "Included-Issues: #42, #44, #45, #46",
    )
    explained_result = _validate(explained, changes)
    assert explained_result.errors == []
    assert len(explained_result.warnings) == 3

    partial = governance.fixture_payload(
        "develop",
        "codex/dna-v1",
        "feature",
        threshold_explanation="included-issues: atomic Issue set with enough detail.",
    )
    partial["pull_request"]["body"] = partial["pull_request"]["body"].replace(
        "Included-Issues: #42", "Included-Issues: #42, #44, #45, #46"
    )
    partial_result = _validate(partial, changes)
    assert any("field: source_test_files" in item for item in partial_result.errors)
    assert any("field: non_generated_loc" in item for item in partial_result.errors)
    assert any("field: atomic_outcome" in item for item in partial_result.errors)

    garbage = governance.fixture_payload(
        "develop",
        "codex/dna-v1",
        "feature",
        threshold_explanation=(
            "included-issues: filler filler filler filler\n"
            "source-test-files: filler filler filler filler\n"
            "non-generated-loc: filler filler filler filler\n"
            "Atomic-Outcome: filler filler filler filler\n"
            "Shared-Failure-Mechanism: filler filler filler filler\n"
            "Shared-Validation: filler filler filler filler\n"
            "Rollback-Reason: filler filler filler filler"
        ),
    )
    garbage["pull_request"]["body"] = garbage["pull_request"]["body"].replace(
        "Included-Issues: #42", "Included-Issues: #42, #44, #45, #46"
    )
    garbage_result = _validate(garbage, changes)
    assert any("field: non_generated_loc" in item for item in garbage_result.errors)
    assert any("field: shared_validation" in item for item in garbage_result.errors)


def test_derived_evidence_does_not_inflate_non_generated_loc() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    changes = governance.ChangeSet(
        (
            governance.ChangedPath(
                "examples/reference-only/01/qa/metrics.json", 2000, 0
            ),
            governance.ChangedPath("tools/compare.py", 10, 2),
        )
    )
    result = _validate(payload, changes)
    assert not any("non-generated-loc" in item for item in result.warnings)


def test_case_sources_always_count_toward_non_generated_loc() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    paths = [
        "examples/reference-only/01/reference.png",
        "examples/svg-seeded/01/external-seed.svg",
        "examples/reference-only/01/scene.json",
        "examples/reference-only/01/assets.json",
        "examples/reference-only/01/regions.json",
        "examples/reference-only/01/qa/live/input/reference.png",
    ]
    for path in paths:
        changes = governance.ChangeSet((governance.ChangedPath(path, 1501, 0),))
        result = _validate(payload, changes)
        assert any("non-generated-loc" in item for item in result.warnings), path


def test_governance_workflows_count_as_source_test_files() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    changes = governance.ChangeSet(
        tuple(
            governance.ChangedPath(f".github/workflows/gate-{index}.yml", 1, 0)
            for index in range(31)
        )
    )
    result = _validate(payload, changes)
    assert any("source-test-files:31>30" in item for item in result.warnings)


def test_case_contract_sources_count_as_source_test_files() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    changes = governance.ChangeSet(
        tuple(
            governance.ChangedPath(
                f"examples/reference-only/case-{index:02d}/scene.json", 1, 0
            )
            for index in range(31)
        )
    )
    result = _validate(payload, changes)
    assert any("source-test-files:31>30" in item for item in result.warnings)


def test_included_issues_reject_free_text_and_closing_keywords() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    payload["pull_request"]["body"] = payload["pull_request"]["body"].replace(
        "Included-Issues: #42",
        "Included-Issues: Closes #42, #44",
    )
    result = _validate(payload)
    assert any(
        "does not mirror authority contract: Included-Issues" in item
        for item in result.errors
    )


def test_deferred_issues_reject_free_text_even_when_it_contains_issue_numbers() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    payload["pull_request"]["body"] = payload["pull_request"]["body"].replace(
        "Deferred-Issues: #43",
        "Deferred-Issues: later work in #43",
    )
    result = _validate(payload)
    assert any(
        "does not mirror authority contract: Deferred-Issues" in item
        for item in result.errors
    )


def test_evidence_must_bind_current_head_and_scope_ancestor() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    payload["pull_request"]["head"]["sha"] = "d" * 40
    assert any(
        "Evidence-Baseline" in item and "does not mirror" in item
        for item in _validate(payload).errors
    )

    fresh = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    result = _validate(fresh, scope_freeze_valid=False)
    assert (
        "Stage Scope-Freeze must equal the GitHub compare merge-base/branch-point"
        in result.errors
    )


def test_included_and_deferred_issues_cannot_overlap() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    payload["pull_request"]["body"] = payload["pull_request"]["body"].replace(
        "Deferred-Issues: #43", "Deferred-Issues: #42"
    )
    assert "an Issue cannot be both Included and Deferred" in _validate(payload).errors


def test_duplicate_metadata_blocks_fields_and_required_headings_are_rejected() -> None:
    duplicate_field = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )
    duplicate_field["pull_request"]["body"] = duplicate_field["pull_request"][
        "body"
    ].replace("Risk-Level: R2", "Risk-Level: R2\nRisk-Level: R0")
    assert "duplicate GOV metadata field: Risk-Level" in _validate(
        duplicate_field
    ).errors

    duplicate_block = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )
    duplicate_block["pull_request"]["body"] += "\n" + governance.fixture_body(
        "feature"
    )
    assert "expected exactly one GOV metadata block, found 2" in _validate(
        duplicate_block
    ).errors

    duplicate_heading = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )
    duplicate_heading["pull_request"]["body"] += (
        "\n## 验证证据\nA second machine-visible evidence section must be rejected.\n"
    )
    assert "duplicate governance section heading: 验证证据" in _validate(
        duplicate_heading
    ).errors


def test_epic_and_included_issue_api_contracts_fail_closed() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    records = governance.fixture_issue_records(payload)
    records[governance.FIXTURE_EPIC]["labels"] = [{"name": "type:bug"}]
    assert any("must have label type:epic" in item for item in _validate(
        payload, issue_records=records
    ).errors)

    records = governance.fixture_issue_records(payload)
    records[42]["body"] = "### Parent Epic\n\n#999\n"
    assert any("must declare Parent Epic #40" in item for item in _validate(
        payload, issue_records=records
    ).errors)

    records = governance.fixture_issue_records(payload)
    records[42]["body"] += "\n### Parent Epic\n\n#40\n"
    assert any("must declare Parent Epic #40" in item for item in _validate(
        payload, issue_records=records
    ).errors)

    records = governance.fixture_issue_records(payload)
    records[42]["body"] += "\n### Parent Epic\n\n#999\n"
    assert any("must declare Parent Epic #40" in item for item in _validate(
        payload, issue_records=records
    ).errors)

    records = governance.fixture_issue_records(payload)
    del records[42]
    assert "Included Issue #42 API evidence is missing" in _validate(
        payload, issue_records=records
    ).errors

    records = governance.fixture_issue_records(payload)
    del records[43]
    assert "Deferred Issue #43 API evidence is missing" in _validate(
        payload, issue_records=records
    ).errors

    records = governance.fixture_issue_records(payload)
    records[43]["body"] = "### Parent Epic\n\n#999\n"
    assert any("Deferred Issue #43 must declare Parent Epic #40" in item for item in _validate(
        payload, issue_records=records
    ).errors)


def test_issue_title_body_and_roles_are_exact_head_snapshot_bindings() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(payload)
    snapshots = evidence.contract["issue_snapshots"]
    assert [(item["number"], item["role"]) for item in snapshots] == [
        (40, "epic"),
        (42, "included"),
        (43, "deferred"),
    ]

    records = governance.fixture_issue_records(payload)
    records[42]["title"] = "Scope silently changed"
    title_result = _validate(
        payload, contract_evidence=evidence, issue_records=records
    )
    assert "Issue #42 title changed after the authority snapshot" in title_result.errors

    records = governance.fixture_issue_records(payload)
    records[43]["body"] += "\nNew acceptance condition.\n"
    body_result = _validate(
        payload, contract_evidence=evidence, issue_records=records
    )
    assert "Issue #43 body changed after the authority snapshot" in body_result.errors


def test_issue_snapshot_set_is_complete_unique_sorted_and_role_exact() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(payload)

    missing = governance.fixture_contract_evidence(
        payload,
        overrides={"issue_snapshots": evidence.contract["issue_snapshots"][:-1]},
    )
    assert any(
        "exactly bind every Epic/Included/Deferred" in item
        for item in _validate(payload, contract_evidence=missing).errors
    )

    wrong_role_snapshots = json.loads(json.dumps(evidence.contract["issue_snapshots"]))
    wrong_role_snapshots[1]["role"] = "deferred"
    wrong_role = governance.fixture_contract_evidence(
        payload, overrides={"issue_snapshots": wrong_role_snapshots}
    )
    assert any(
        "exactly bind every Epic/Included/Deferred" in item
        for item in _validate(payload, contract_evidence=wrong_role).errors
    )

    reversed_snapshots = list(reversed(evidence.contract["issue_snapshots"]))
    reversed_contract = governance.fixture_contract_evidence(
        payload, overrides={"issue_snapshots": reversed_snapshots}
    )
    assert any(
        "must be sorted by Issue number" in item
        for item in _validate(payload, contract_evidence=reversed_contract).errors
    )


def test_issue_invalidation_status_permanently_poisons_same_head() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    invalidation = {
        "state": "failure",
        "context": "pr-governance/policy",
        "description": "Issue snapshot invalidated: #42 mutated; commit a new contract/head",
    }
    result = _validate(
        payload, issue_snapshot_invalidation_records=[invalidation]
    )
    assert any(
        "permanently invalidated by an Issue mutation" in item
        for item in result.errors
    )


def test_issue_invalidation_status_history_is_paginated_and_prefix_scoped(
    monkeypatch,
) -> None:
    pull = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )["pull_request"]
    policy = json.loads(json.dumps(TEST_POLICY))
    policy["api_evidence"]["page_size"] = 2
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        if "page=1" in url:
            return [
                {
                    "state": "pending",
                    "context": "pr-governance/policy",
                    "description": "Trusted governance is validating",
                },
                {
                    "state": "failure",
                    "context": "another/check",
                    "description": "Issue snapshot invalidated: unrelated context",
                },
            ]
        return [
            {
                "state": "failure",
                "context": "pr-governance/policy",
                "description": "Issue snapshot invalidated: #42 mutated",
            }
        ]

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    records = governance.issue_snapshot_invalidations(pull, policy)
    assert len(records) == 1
    assert len(calls) == 2


def test_finalizer_requires_explicit_timeline_state_at_merge() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    merged_at = governance._timestamp("2026-08-25T02:00:00Z")
    assert merged_at is not None
    records = governance.fixture_issue_records(payload)
    records[42].update(
        state="closed", closed_at="2026-08-25T02:01:00Z"
    )
    records[40][governance.HISTORICAL_OPEN_KEY] = True
    records[42][governance.HISTORICAL_OPEN_KEY] = True
    records[43][governance.HISTORICAL_OPEN_KEY] = True
    assert _validate(
        payload,
        issue_records=records,
        finalization_merged_at=merged_at,
    ).errors == []

    records[42][governance.HISTORICAL_OPEN_KEY] = False
    assert "Included Issue #42 must be open before merge" in _validate(
        payload,
        issue_records=records,
        finalization_merged_at=merged_at,
    ).errors

    drifted = governance.fixture_issue_records(payload)
    for record in drifted.values():
        record[governance.HISTORICAL_OPEN_KEY] = True
    drifted[42]["body"] += "\nChanged after review.\n"
    assert "Issue #42 body changed after the authority snapshot" in _validate(
        payload,
        issue_records=drifted,
        finalization_merged_at=merged_at,
    ).errors


def test_empty_production_bot_allowlist_blocks_rollout() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(payload)
    result = governance.validate(
        payload,
        governance.with_contract_change(governance._fixture_changes(), evidence),
        POLICY,
        issue_records=governance.fixture_issue_records(payload),
        scope_freeze_valid=True,
        contract_evidence=evidence,
        today=TODAY,
    )
    assert any("allowlist is empty" in item for item in result.errors)


def test_release_uses_exact_develop_and_release_rollback_unit() -> None:
    payload = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope=governance.FIXTURE_HEAD,
    )
    assert _validate(payload).errors == []

    wrong = governance.fixture_payload(
        "main",
        "codex/dna-v1",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope=governance.FIXTURE_HEAD,
    )
    assert "only develop may target main" in _validate(wrong).errors

    stale_scope = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
    )
    assert any(
        "does not mirror authority contract: Scope-Freeze" in item
        for item in _validate(stale_scope).errors
    )


def test_release_ignores_historical_stage_contracts_in_its_diff() -> None:
    payload = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope=governance.FIXTURE_HEAD,
    )
    changes = governance.ChangeSet(
        (
            governance.ChangedPath(
                ".github/stage-contracts/dna-v1.json", 40, 0
            ),
            governance.ChangedPath(
                ".github/stage-contracts/typography-v2.json", 40, 0
            ),
        )
    )
    assert _validate(payload, changes).errors == []


def test_stage_cannot_smuggle_a_release_contract_outside_preparation_stage() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    changes = governance.ChangeSet(
        (
            governance.ChangedPath(
                ".github/release-contracts/release-v1.json", 40, 0
            ),
        )
    )
    result = _validate(payload, changes)
    assert any("only codex/release-preparation-vK" in item for item in result.errors)


def test_two_phase_release_preparation_binds_real_draft_pr_without_self_reference() -> None:
    target = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope=governance.FIXTURE_HEAD,
    )
    target["pull_request"].update(
        number=77,
        draft=True,
        state="open",
        created_at="2026-08-24T00:00:00Z",
    )
    preparation = governance.fixture_payload(
        "develop",
        "codex/release-preparation-v3",
        "feature",
        stage="release-preparation@v3",
    )
    preparation["pull_request"].update(state="open", draft=True)
    preparation["pull_request"]["base"]["sha"] = governance.FIXTURE_HEAD
    stage_contract = governance.fixture_contract_evidence(preparation)
    release_contract = governance.fixture_contract_evidence(
        target,
        source_ref=governance.FIXTURE_HEAD,
        overrides={
            "release_preparation": {
                "pr_number": preparation["pull_request"]["number"],
                "base_sha": governance.FIXTURE_HEAD,
            }
        },
    )
    assert release_contract.contract["scope_freeze"] == "current-head"
    changes = governance.ChangeSet(
        (
            governance.ChangedPath(stage_contract.path, 40, 0),
            governance.ChangedPath(release_contract.path, 40, 0),
            governance.ChangedPath("docs/release-notes.md", 5, 0),
        )
    )
    stage_result = _validate(
        preparation,
        changes,
        contract_evidence=stage_contract,
    )
    assert stage_result.errors == []

    prepared = governance.PreparedRelease(
        preparation_pull=preparation["pull_request"],
        target_pull=target["pull_request"],
        contract=release_contract,
    )
    release_result = governance.validate_prepared_release(
        prepared,
        changes,
        TEST_POLICY,
        issue_records=governance.fixture_issue_records(target),
        same_head_open_prs=[],
        today=TODAY,
    )
    assert release_result.errors == []

    target["pull_request"]["draft"] = False
    not_draft = governance.validate_prepared_release(
        prepared,
        changes,
        TEST_POLICY,
        issue_records=governance.fixture_issue_records(target),
        same_head_open_prs=[],
        today=TODAY,
    )
    assert "release preparation target PR must remain a draft" in not_draft.errors


def test_release_head_newer_than_preparation_merge_requires_new_version() -> None:
    payload = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope="d" * 40,
    )
    payload["pull_request"]["head"]["sha"] = "d" * 40
    payload["pull_request"]["body"] = payload["pull_request"]["body"].replace(
        f"Evidence-Baseline: head@{governance.FIXTURE_HEAD}",
        f"Evidence-Baseline: head@{'d' * 40}",
    )
    evidence = governance.fixture_contract_evidence(
        payload,
        overrides={
            "release_preparation": {
                "pr_number": 98,
                "base_sha": governance.FIXTURE_HEAD,
            }
        },
    )
    record = governance.fixture_release_preparation_record(payload, evidence)
    assert record is not None
    record["merge_commit_sha"] = governance.FIXTURE_HEAD
    result = _validate(
        payload,
        contract_evidence=evidence,
        release_preparation_record=record,
    )
    assert any(
        "newer develop head requires a new preparation version" in item
        for item in result.errors
    )

    fresh_evidence = governance.fixture_contract_evidence(
        payload,
        overrides={
            "release_preparation": {
                "pr_number": 100,
                "base_sha": "c" * 40,
            }
        },
    )
    fresh_record = governance.fixture_release_preparation_record(
        payload, fresh_evidence
    )
    assert fresh_record is not None
    fresh_record["head"]["ref"] = "codex/release-preparation-v3"
    fresh_record["merge_commit_sha"] = "d" * 40
    fresh = _validate(
        payload,
        contract_evidence=fresh_evidence,
        release_preparation_record=fresh_record,
    )
    assert fresh.errors == []
    assert fresh_evidence.path == ".github/release-contracts/release-v2.json"


def test_change_collection_uses_file_and_numstat_overrides(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    monkeypatch.setenv("GOVERNANCE_CHANGED_FILES", "tools/a.py\ntests/test_a.py")
    monkeypatch.setenv(
        "GOVERNANCE_CHANGED_NUMSTAT", "10\t2\ttools/a.py\n5\t1\ttests/test_a.py"
    )
    changes = governance.collect_changes(payload, POLICY)
    assert changes.files == ["tools/a.py", "tests/test_a.py"]
    assert sum(item.additions + item.deletions for item in changes.paths) == 18


def test_change_collection_uses_pull_files_api_and_preserves_rename_risk(
    monkeypatch,
) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")

    def fake_get(url: str):
        assert "/pulls/99/files?" in url
        return [
            {
                "filename": "docs/renamed.yml",
                "previous_filename": ".github/workflows/ci.yml",
                "status": "renamed",
                "additions": 2,
                "deletions": 3,
            }
        ]

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    changes = governance.collect_changes(payload, POLICY)
    assert changes.files == ["docs/renamed.yml", ".github/workflows/ci.yml"]
    assert governance.risk_floor(changes.files, POLICY) == "R2"


def test_event_payload_is_refreshed_from_current_pull_api(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    current = json.loads(json.dumps(payload["pull_request"]))
    current["body"] = current["body"].replace("Deferred-Issues: #43", "Deferred-Issues: none")

    def fake_get(url: str):
        assert url.endswith("/pulls/99")
        return current

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    refreshed = governance.refresh_pull_payload(payload, POLICY)
    assert "Deferred-Issues: none" in refreshed["pull_request"]["body"]
    assert "Deferred-Issues: #43" in payload["pull_request"]["body"]


def test_scope_ancestry_uses_compare_api(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    metadata = governance.parse_metadata(payload["pull_request"]["body"])

    def fake_get(url: str):
        assert "/compare/" in url
        assert "c" * 40 in url
        assert governance.FIXTURE_HEAD in url
        return {"merge_base_commit": {"sha": governance.FIXTURE_SCOPE}}

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    assert governance.scope_freeze_is_ancestor(payload, metadata, POLICY) is True

    monkeypatch.setattr(
        governance,
        "_github_get_json",
        lambda _url: {"merge_base_commit": {"sha": "d" * 40}},
    )
    assert governance.scope_freeze_is_ancestor(payload, metadata, POLICY) is False


def test_branch_history_override_is_deterministic(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    monkeypatch.setenv(
        "GOVERNANCE_PRIOR_BRANCH_PRS",
        json.dumps([{"number": 7, "merged_at": "2026-08-24T00:00:00Z"}]),
    )
    assert governance.prior_branch_prs(payload, POLICY)[0]["number"] == 7


def test_branch_history_api_is_paginated_and_includes_closed_unmerged(
    monkeypatch,
) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        assert "state=all" in url
        if "&page=1" in url:
            return [{"number": number, "merged_at": None} for number in range(100, 200)]
        if "&page=2" in url:
            return [
                {"number": 7, "merged_at": None},
                {"number": 99, "merged_at": None},
            ]
        raise AssertionError(url)

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    previous = governance.prior_branch_prs(payload, POLICY)
    assert len(previous) == 101
    assert previous[-1]["number"] == 7
    assert len(calls) == 2


def test_body_metadata_is_only_a_strict_mirror_of_head_contract() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(payload)
    records = governance.fixture_issue_records(payload)
    payload["pull_request"]["body"] = payload["pull_request"]["body"].replace(
        "Included-Issues: #42", "Included-Issues: #44"
    )
    result = _validate(
        payload, contract_evidence=evidence, issue_records=records
    )
    assert any(
        "does not mirror authority contract: Included-Issues" in item
        for item in result.errors
    )


def test_duplicate_open_pr_same_head_fails_shared_status_identity() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    result = _validate(
        payload,
        same_head_open_prs=[
            {
                "number": 100,
                "state": "open",
                "head": {"sha": governance.FIXTURE_HEAD},
            }
        ],
    )
    assert any("shared by another open PR (#100)" in item for item in result.errors)


def test_contract_wrong_path_schema_and_source_head_fail_closed() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")

    wrong_path = governance.fixture_contract_evidence(
        payload, path=".github/stage-contracts/other-v1.json"
    )
    assert any(
        "authority contract path must be" in item
        for item in _validate(payload, contract_evidence=wrong_path).errors
    )

    wrong_schema = governance.fixture_contract_evidence(
        payload, overrides={"schema_version": 2}
    )
    assert any(
        "schema_version must be 1" in item
        for item in _validate(payload, contract_evidence=wrong_schema).errors
    )

    wrong_head = governance.fixture_contract_evidence(
        payload, source_ref="d" * 40
    )
    assert any(
        "not read from the exact required commit ref" in item
        for item in _validate(payload, contract_evidence=wrong_head).errors
    )


def test_contract_must_be_in_changed_files_and_be_the_only_contract() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(payload)
    changes = governance.ChangeSet((governance.ChangedPath("tools/a.py", 1, 0),))
    result = governance.validate(
        payload,
        changes,
        TEST_POLICY,
        contract_evidence=evidence,
        scope_freeze_valid=True,
        issue_records=governance.fixture_issue_records(payload),
        today=TODAY,
    )
    assert "authority contract must be present in PR changed files" in result.errors


def test_contract_fetch_reads_contents_and_blob_at_exact_current_head(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(payload)
    changes = governance.with_contract_change(governance._fixture_changes(), evidence)
    raw = (json.dumps(evidence.contract, sort_keys=True) + "\n").encode("utf-8")
    import base64
    import hashlib

    encoded = base64.b64encode(raw).decode("ascii")
    blob_sha = hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw, usedforsecurity=False
    ).hexdigest()
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        if "/contents/" in url:
            assert f"ref={governance.FIXTURE_HEAD}" in url
            return {
                "type": "file",
                "path": evidence.path,
                "encoding": "base64",
                "sha": blob_sha,
                "content": encoded,
            }
        assert url.endswith("/git/blobs/" + blob_sha)
        return {"sha": blob_sha, "encoding": "base64", "content": encoded}

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    fetched = governance.fetch_contract_evidence(
        payload["pull_request"], changes, POLICY
    )
    assert fetched.contract == evidence.contract
    assert fetched.source_ref == governance.FIXTURE_HEAD
    assert len(calls) == 2


def test_open_pr_head_identity_api_is_repo_wide_and_paginated(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")

    def fake_get(url: str):
        assert "state=open" in url
        return [
            {"number": 99, "head": {"sha": governance.FIXTURE_HEAD}},
            {"number": 100, "head": {"sha": governance.FIXTURE_HEAD}},
            {"number": 101, "head": {"sha": "d" * 40}},
        ]

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    assert [
        item["number"] for item in governance.open_prs_sharing_head(payload, POLICY)
    ] == [100]


def _active_remote_rules(base: str) -> list[dict]:
    contexts = TEST_POLICY["remote_merge_gate"]["required_contexts_by_base"][base]
    methods = ["squash"]
    if base == "main":
        methods = ["merge"]
    return [
        {
            "type": "required_status_checks",
            "ruleset_id": 11,
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [
                    {"context": context, "integration_id": 15368}
                    for context in contexts
                ],
            },
        },
        {
            "type": "pull_request",
            "ruleset_id": 11,
            "parameters": {
                "allowed_merge_methods": methods,
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": True,
                "require_last_push_approval": True,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": True,
            },
        },
        {"type": "non_fast_forward", "ruleset_id": 11},
        {"type": "deletion", "ruleset_id": 11},
    ]


def test_active_branch_rules_are_live_paginated_api_evidence(monkeypatch) -> None:
    pull = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )["pull_request"]
    policy = json.loads(json.dumps(TEST_POLICY))
    policy["api_evidence"]["page_size"] = 1
    expected = _active_remote_rules("develop")
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        assert "/rules/branches/develop?" in url
        for page, rule in enumerate(expected, 1):
            if url.endswith(f"page={page}"):
                return [rule]
        return []

    monkeypatch.setattr(governance, "_github_get_json", fake_get)
    assert governance.active_branch_rules(pull, policy) == expected
    assert len(calls) == len(expected) + 1


def test_remote_merge_gate_requires_strict_bound_status_and_pr_specific_review() -> None:
    develop = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )["pull_request"]
    main = governance.fixture_payload(
        "main", "develop", "release", epic="release"
    )["pull_request"]

    assert governance.remote_merge_gate_errors(
        develop, _active_remote_rules("develop"), TEST_POLICY
    ) == []
    assert governance.remote_merge_gate_errors(
        main, _active_remote_rules("main"), TEST_POLICY
    ) == []

    unbound = _active_remote_rules("develop")
    unbound[0]["parameters"]["required_status_checks"][0]["integration_id"] = None
    errors = governance.remote_merge_gate_errors(develop, unbound, TEST_POLICY)
    assert any("GitHub-App-bound status contexts" in item for item in errors)

    missing_ci = _active_remote_rules("develop")
    missing_ci[0]["parameters"]["required_status_checks"] = [
        check
        for check in missing_ci[0]["parameters"]["required_status_checks"]
        if check["context"] != "portable-tests (windows-latest, py3.12)"
    ]
    errors = governance.remote_merge_gate_errors(develop, missing_ci, TEST_POLICY)
    assert any("portable-tests (windows-latest, py3.12)" in item for item in errors)

    app_unbound_policy = json.loads(json.dumps(TEST_POLICY))
    app_unbound_policy["remote_merge_gate"]["require_app_binding"] = False
    errors = governance.remote_merge_gate_errors(
        develop, _active_remote_rules("develop"), app_unbound_policy
    )
    assert any("must require GitHub App binding" in item for item in errors)

    stale_allowed = _active_remote_rules("develop")
    stale_allowed[0]["parameters"]["strict_required_status_checks_policy"] = False
    errors = governance.remote_merge_gate_errors(
        develop, stale_allowed, TEST_POLICY
    )
    assert any("GitHub-App-bound status contexts" in item for item in errors)

    status_only = _active_remote_rules("develop")[:1]
    errors = governance.remote_merge_gate_errors(develop, status_only, TEST_POLICY)
    assert any("PR-specific approval" in item for item in errors)

    wrong_method = _active_remote_rules("develop")
    wrong_method[1]["parameters"]["allowed_merge_methods"] = ["merge", "squash"]
    errors = governance.remote_merge_gate_errors(develop, wrong_method, TEST_POLICY)
    assert any("governed merge method" in item for item in errors)

    unresolved_threads = _active_remote_rules("develop")
    unresolved_threads[1]["parameters"]["required_review_thread_resolution"] = False
    errors = governance.remote_merge_gate_errors(
        develop, unresolved_threads, TEST_POLICY
    )
    assert any("resolved review threads" in item for item in errors)

    missing_ref_guards = _active_remote_rules("develop")[:2]
    errors = governance.remote_merge_gate_errors(
        develop, missing_ref_guards, TEST_POLICY
    )
    assert any("deletion, non_fast_forward" in item for item in errors)
