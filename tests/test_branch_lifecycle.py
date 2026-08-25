"""Tests for trusted API-only branch lifecycle supervision."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SCRIPT = SCRIPT_DIR / "check_branch_lifecycle.py"
SPEC = importlib.util.spec_from_file_location("autofigure_branch_lifecycle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
lifecycle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lifecycle
SPEC.loader.exec_module(lifecycle)

import check_pr_governance as governance  # noqa: E402


POLICY = json.loads(
    (ROOT / ".github" / "governance-policy.json").read_text(encoding="utf-8")
)
TEST_POLICY = json.loads(json.dumps(POLICY))
TEST_POLICY["pr_author"]["allowed_bot_logins"] = [governance.FIXTURE_BOT]
TEST_POLICY["governance_contract"]["release_preparation"]["state"] = "active"
TEST_POLICY["branch_lifecycle"]["receipt"]["ruleset"]["state"] = "active"
TEST_POLICY["branch_lifecycle"]["receipt"]["ruleset"][
    "trusted_bypass_actors"
] = [{"actor_type": "Integration", "actor_id": 15368, "bypass_mode": "always"}]
TEST_RECEIPT_RULESET = TEST_POLICY["branch_lifecycle"]["receipt"]["ruleset"]
TEST_RECEIPT_RULESET["ruleset_id"] = 7
TEST_RECEIPT_RULESET["verified_updated_at"] = "2026-08-25T00:00:00Z"
TEST_RECEIPT_RULESET["bypass_attestation_sha256"] = (
    lifecycle._ruleset_bypass_attestation(
        TEST_RECEIPT_RULESET["ruleset_id"],
        TEST_RECEIPT_RULESET["verified_updated_at"],
        TEST_RECEIPT_RULESET["trusted_bypass_actors"],
    )
)
TODAY = date(2026, 8, 25)


class FakeApi:
    def __init__(self) -> None:
        self.open_pulls: list[dict] = []
        self.closed_pulls: list[dict] = []
        self.statuses: list[tuple[str, str, str, str]] = []
        self.issues: dict[int, dict] = {
            42: {
                "number": 42,
                "state": "open",
                "state_reason": None,
                "created_at": "2026-08-20T00:00:00Z",
            },
            44: {
                "number": 44,
                "state": "open",
                "state_reason": None,
                "created_at": "2026-08-20T00:00:00Z",
            },
        }
        self.issue_events: dict[int, list[dict]] = {42: [], 44: []}
        self.refs: dict[str, str] = {}
        self.receipts: dict[int, str] = {}
        self.snapshot_invalidations: list[dict] = []
        self.snapshot_invalidation_reads: list[list[dict]] = []
        self.issue_ledger_errors: list[str | None] = []
        self.validation_errors: list[str] = []
        self.contract_override = None

    def list_pulls(self, *, state: str, base: str) -> list[dict]:
        assert base == "develop"
        return self.open_pulls if state == "open" else self.closed_pulls

    def get_pull(self, number: int) -> dict:
        for pr in [*self.open_pulls, *self.closed_pulls]:
            if pr.get("number") == number:
                return pr
        raise AssertionError(number)

    def set_status(self, sha: str, *, state: str, context: str, description: str) -> None:
        self.statuses.append((sha, state, context, description))

    def issue_snapshot_invalidations(self, head_sha: str, policy: dict) -> list[dict]:
        assert head_sha == governance.FIXTURE_HEAD
        if self.snapshot_invalidation_reads:
            return list(self.snapshot_invalidation_reads.pop(0))
        return list(self.snapshot_invalidations)

    def verify_issue_scope_ledger(self, contract, merged_at, policy) -> None:
        if self.issue_ledger_errors:
            error = self.issue_ledger_errors.pop(0)
            if error is not None:
                raise RuntimeError(error)

    def get_issue(self, number: int) -> dict:
        return self.issues[number]

    def list_issue_events(self, number: int) -> list[dict]:
        return list(self.issue_events[number])

    def close_issue(self, number: int) -> None:
        self.issues[number]["state"] = "closed"
        self.issues[number]["state_reason"] = "completed"
        self.issue_events[number].append(
            {
                "id": 1000 + len(self.issue_events[number]),
                "event": "closed",
                "created_at": "2026-08-25T02:01:00Z",
                "actor": {"login": "github-actions[bot]", "type": "Bot"},
            }
        )

    def reopen_issue(self, number: int) -> None:
        self.issues[number]["state"] = "open"
        self.issues[number]["state_reason"] = "reopened"
        self.issue_events[number].append(
            {
                "id": 2000 + len(self.issue_events[number]),
                "event": "reopened",
                "created_at": "2026-08-25T02:02:00Z",
                "actor": {"login": "github-actions[bot]", "type": "Bot"},
            }
        )

    def get_ref(self, branch: str) -> dict | None:
        sha = self.refs.get(branch)
        return None if sha is None else {"object": {"sha": sha}}

    def delete_ref(self, branch: str) -> None:
        self.refs.pop(branch, None)

    def verify_receipt_ruleset(self, policy: dict) -> None:
        return None

    def get_finalization_receipt(
        self, number: int, merge_sha: str, policy: dict
    ) -> bool:
        value = self.receipts.get(number)
        if value is not None and value != merge_sha:
            raise RuntimeError("receipt target mismatch")
        return value == merge_sha

    def create_finalization_receipt(
        self, number: int, merge_sha: str, policy: dict
    ) -> None:
        existing = self.receipts.setdefault(number, merge_sha)
        if existing != merge_sha:
            raise RuntimeError("receipt target mismatch")

    def validate_finalization(self, pr: dict, policy: dict):
        contract = self.contract_override or governance.fixture_contract_evidence(pr_payload(pr))
        return lifecycle.FinalizationValidation(
            errors=list(self.validation_errors), contract=contract
        )

    def get_open_contract(self, pr: dict, policy: dict):
        contract = self.contract_override or governance.fixture_contract_evidence(pr_payload(pr))
        return lifecycle.FinalizationValidation(
            errors=list(self.validation_errors), contract=contract
        )


def pr_payload(pr: dict) -> dict:
    return {"pull_request": pr}


def _pr(*, sunset: str = "2026-09-01", merged: bool = False) -> dict:
    payload = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature", sunset=sunset
    )
    pr = payload["pull_request"]
    pr["merged_at"] = "2026-08-25T02:00:00Z" if merged else None
    pr["merge_commit_sha"] = "f" * 40 if merged else None
    return pr


def test_schedule_payload_needs_no_pull_request_and_refreshes_status() -> None:
    api = FakeApi()
    api.open_pulls = [_pr()]
    result = lifecycle.supervise("audit", "schedule", {}, api, POLICY, today=TODAY)
    assert result.errors == []
    assert api.statuses[0][1:3] == ("success", "branch-lifecycle/sunset")


def test_expired_open_pr_gets_failure_status_but_is_not_closed() -> None:
    api = FakeApi()
    pr = _pr(sunset="2026-08-24")
    api.open_pulls = [pr]
    result = lifecycle.supervise("audit", "schedule", {}, api, POLICY, today=TODAY)
    assert any("expired" in item for item in result.errors)
    assert api.statuses[0][1] == "failure"
    assert pr.get("state") != "closed"


def test_merged_develop_finalizer_closes_full_issue_set_and_deletes_exact_ref() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    authority_body = pr["body"].replace(
        "Included-Issues: #42", "Included-Issues: #42, #44"
    )
    authority_pr = dict(pr)
    authority_pr["body"] = authority_body
    api.contract_override = governance.fixture_contract_evidence(pr_payload(authority_pr))
    # Editable body is deliberately stale; it must not influence finalization.
    pr["body"] = pr["body"].replace("Included-Issues: #42", "Included-Issues: #999")
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    payload = {"action": "closed", "pull_request": pr}
    api.closed_pulls = [pr]
    result = lifecycle.supervise(
        "finalize", "pull_request_target", payload, api, POLICY, today=TODAY
    )
    assert result.errors == []
    assert api.issues[42]["state"] == "closed"
    assert api.issues[44]["state"] == "closed"
    assert "codex/dna-v1" not in api.refs


def test_advanced_merged_branch_is_not_deleted() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.refs["codex/dna-v1"] = "d" * 40
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("advanced after merge" in item for item in result.errors)
    assert api.refs["codex/dna-v1"] == "d" * 40


def test_invalid_contract_issue_list_closes_nothing_and_retains_merged_branch() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.contract_override = governance.fixture_contract_evidence(
        pr_payload(pr), overrides={"included_issues": "Closes #42"}
    )
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("immutable contract included_issues is invalid" in item for item in result.errors)
    assert api.issues[42]["state"] == "open"
    assert "codex/dna-v1" in api.refs


def test_invalid_full_governance_contract_performs_no_finalizer_writes() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    api.validation_errors = ["Epic #40 must have label type:epic"]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("finalizer validation" in item for item in result.errors)
    assert api.issues[42]["state"] == "open"
    assert "codex/dna-v1" in api.refs


def test_schedule_never_replays_merged_finalizers() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.closed_pulls = [pr]
    result = lifecycle.supervise(
        "finalize", "schedule", {}, api, POLICY, today=TODAY
    )
    assert any("scheduled finalization is forbidden" in item for item in result.errors)
    assert api.issues[42]["state"] == "open"
    assert api.receipts == {}


def test_receipt_makes_targeted_retry_terminal_and_preserves_reopened_issue() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.closed_pulls = [pr]
    api.receipts[pr["number"]] = pr["merge_commit_sha"]
    api.issues[42]["state"] = "open"
    api.issue_events[42] = [
        {
            "id": 1001,
            "event": "closed",
            "created_at": "2026-08-25T02:01:00Z",
            "actor": {"login": "github-actions[bot]", "type": "Bot"},
        },
        {
            "id": 1002,
            "event": "reopened",
            "created_at": "2026-08-26T00:00:00Z",
            "actor": {"login": "let778750-cpu", "type": "User"},
        },
    ]
    result = lifecycle.supervise(
        "finalize",
        "workflow_dispatch",
        {"inputs": {"pr_number": str(pr["number"])}},
        api,
        POLICY,
        today=TODAY,
    )
    assert result.errors == []
    assert any("receipt already complete" in item for item in result.actions)
    assert api.issues[42]["state"] == "open"


def test_poison_status_precedes_receipt_and_blocks_all_finalizer_mutations() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    api.receipts[pr["number"]] = pr["merge_commit_sha"]
    api.snapshot_invalidations = [
        {
            "state": "failure",
            "context": "pr-governance/policy",
            "description": "Issue snapshot invalidated: #42 mutated",
        }
    ]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("Issue-snapshot poison" in item for item in result.errors)
    assert api.issues[42]["state"] == "open"
    assert "codex/dna-v1" in api.refs


def test_final_poison_read_withholds_receipt_if_invalidation_arrives_mid_run() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    poison = [
        {
            "state": "failure",
            "context": "pr-governance/policy",
            "description": "Issue snapshot invalidated: #42 mutated",
        }
    ]
    api.snapshot_invalidation_reads = [[], poison]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("Issue-snapshot poison" in item for item in result.errors)
    assert any("receipt withheld" in item for item in result.actions)
    assert api.receipts == {}


def test_post_receipt_poison_read_conflict_marks_a_last_instant_race() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    poison = [
        {
            "state": "failure",
            "context": "pr-governance/policy",
            "description": "Issue snapshot invalidated: #42 mutated",
        }
    ]
    api.snapshot_invalidation_reads = [[], [], poison]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("conflict-marked" in item for item in result.errors)
    assert api.receipts[pr["number"]] == pr["merge_commit_sha"]
    assert api.issues[42]["state"] == "open"
    assert any(
        state == "failure"
        and context == "pr-governance/policy"
        and description.startswith(governance.ISSUE_INVALIDATION_DESCRIPTION_PREFIX)
        for _sha, state, context, description in api.statuses
    )


def test_issue_ledger_race_before_receipt_reopens_nothing_and_withholds_receipt() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    api.issue_ledger_errors = [None, "label add/remove after scope-freeze"]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("final Issue/status readback" in item for item in result.errors)
    assert api.receipts == {}
    assert api.issues[42]["state"] == "open"
    assert any(state == "failure" for _sha, state, _context, _description in api.statuses)


def test_issue_ledger_race_after_receipt_is_poisoned_and_restored_open() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.issue_ledger_errors = [None, None, "edit-revert after scope-freeze"]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("post-receipt Issue/status readback" in item for item in result.errors)
    assert api.receipts[pr["number"]] == pr["merge_commit_sha"]
    assert api.issues[42]["state"] == "open"
    assert any(
        state == "failure"
        and context == "pr-governance/policy"
        and "receipt nonterminal" in description
        for _sha, state, context, description in api.statuses
    )


def test_issue_ledger_rejects_edit_then_restore_and_label_add_then_remove() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    authority = governance.fixture_contract_evidence(payload)
    snapshot = next(
        item for item in authority.contract["issue_snapshots"] if item["number"] == 42
    )
    base_issue = governance.fixture_issue_records(payload)[42]
    merged_at = lifecycle._timestamp("2026-08-25T02:00:00Z")
    assert merged_at is not None
    api = lifecycle.GitHubApi.__new__(lifecycle.GitHubApi)

    edit_restore = dict(base_issue)
    edit_restore[governance.ISSUE_LAST_EDITED_KEY] = "2026-08-25T01:00:00Z"
    with pytest.raises(RuntimeError, match="edited after scope-freeze, even if restored"):
        api._verify_issue_snapshot_ledger(
            issue=edit_restore,
            snapshot=snapshot,
            merged_at=merged_at,
            policy=TEST_POLICY,
            allow_trusted_included_close=True,
        )

    label_restore = dict(base_issue)
    label_restore[governance.ISSUE_MANAGED_EVENTS_KEY] = [
        {
            "id": 99,
            "event": "labeled",
            "created_at": "2026-08-24T23:00:00Z",
            "actor": {"login": "maintainer", "type": "User"},
        },
        {
            "id": 100,
            "event": "labeled",
            "created_at": "2026-08-25T00:30:00Z",
            "actor": {"login": "maintainer", "type": "User"},
        },
        {
            "id": 101,
            "event": "unlabeled",
            "created_at": "2026-08-25T00:31:00Z",
            "actor": {"login": "maintainer", "type": "User"},
        },
    ]
    label_snapshot = dict(snapshot)
    label_snapshot["managed_event_cursor"] = {
        "id": 99,
        "event": "labeled",
        "created_at": "2026-08-24T23:00:00Z",
    }
    with pytest.raises(RuntimeError, match="non-finalizer mutation"):
        api._verify_issue_snapshot_ledger(
            issue=label_restore,
            snapshot=label_snapshot,
            merged_at=merged_at,
            policy=TEST_POLICY,
            allow_trusted_included_close=True,
        )


def test_audit_mode_never_finalizes_closed_pulls() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.closed_pulls = [pr]
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    result = lifecycle.supervise("audit", "schedule", {}, api, POLICY, today=TODAY)
    assert result.errors == []
    assert api.issues[42]["state"] == "open"
    assert "codex/dna-v1" in api.refs


def test_workflow_dispatch_requires_one_canonical_pr_number() -> None:
    api = FakeApi()
    for payload in ({}, {"inputs": {}}, {"inputs": {"pr_number": "0042"}}):
        result = lifecycle.supervise(
            "finalize", "workflow_dispatch", payload, api, POLICY, today=TODAY
        )
        assert any("canonical positive pr_number" in item for item in result.errors)


def test_audit_isolates_one_pr_failure_and_continues_other_prs() -> None:
    api = FakeApi()
    bad = _pr()
    good = _pr()
    good["number"] = 43

    def contract(pr: dict, _policy: dict):
        if pr["number"] == bad["number"]:
            raise RuntimeError("synthetic API failure")
        return lifecycle.FinalizationValidation(
            contract=governance.fixture_contract_evidence(pr_payload(pr))
        )

    api.get_open_contract = contract  # type: ignore[method-assign]
    api.open_pulls = [bad, good]
    result = lifecycle.supervise("audit", "schedule", {}, api, POLICY, today=TODAY)
    assert any("isolated audit failure" in item for item in result.errors)
    assert any(item[0] == good["head"]["sha"] for item in api.statuses)


def test_event_history_reconstructs_state_and_rejects_boundary_ambiguity() -> None:
    events = [
        {"id": 1, "event": "closed", "created_at": "2026-08-25T01:00:00Z"},
        {"id": 2, "event": "reopened", "created_at": "2026-08-25T03:00:00Z"},
    ]
    boundary = governance._timestamp("2026-08-25T02:00:00Z")
    assert boundary is not None
    assert lifecycle._state_at_timestamp(
        "open", events, boundary, lifecycle.ISSUE_TRANSITIONS, label="Issue #42"
    ) == "closed"
    assert lifecycle._state_at_timestamp(
        False,
        [
            {
                "id": 3,
                "event": "ready_for_review",
                "created_at": "2026-08-25T03:00:00Z",
            }
        ],
        boundary,
        lifecycle.DRAFT_TRANSITIONS,
        label="release draft",
    ) is True

    ambiguous = [
        {"id": 4, "event": "closed", "created_at": "2026-08-25T02:00:00Z"}
    ]
    try:
        lifecycle._state_at_timestamp(
            "closed",
            ambiguous,
            boundary,
            lifecycle.ISSUE_TRANSITIONS,
            label="Issue #42",
        )
    except RuntimeError as exc:
        assert "timestamp-ambiguous" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("boundary transition must fail closed")


def test_reopened_issue_without_receipt_is_not_closed_by_old_retry() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.issue_events[42] = [
        {
            "id": 1,
            "event": "closed",
            "created_at": "2026-08-25T02:01:00Z",
            "actor": {"login": "github-actions[bot]", "type": "Bot"},
        },
        {
            "id": 2,
            "event": "reopened",
            "created_at": "2026-08-26T00:00:00Z",
            "actor": {"login": "let778750-cpu", "type": "User"},
        },
    ]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("old finalizer will not re-close" in item for item in result.errors)
    assert api.issues[42]["state"] == "open"
    assert api.receipts == {}


def test_closed_issue_requires_completed_reason_and_trusted_close_actor() -> None:
    api = FakeApi()
    pr = _pr(merged=True)
    api.issues[42].update(state="closed", state_reason="not_planned")
    api.issue_events[42] = [
        {
            "id": 1,
            "event": "closed",
            "created_at": "2026-08-25T02:01:00Z",
            "actor": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("state_reason is not completed" in item for item in result.errors)

    api.issues[42]["state_reason"] = "completed"
    api.issue_events[42][0]["actor"] = {"login": "someone", "type": "User"}
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert any("close actor is not the trusted finalizer" in item for item in result.errors)


def test_closed_unmerged_branch_is_retained_and_frozen() -> None:
    api = FakeApi()
    pr = _pr(merged=False)
    api.refs["codex/dna-v1"] = governance.FIXTURE_HEAD
    result = lifecycle.finalize_merged_pr(pr, api, POLICY, set())
    assert result.errors == []
    assert any("retained and frozen" in item for item in result.actions)
    assert "codex/dna-v1" in api.refs


def test_api_ref_deletion_and_receipt_creation_are_race_idempotent(monkeypatch) -> None:
    api = lifecycle.GitHubApi.__new__(lifecycle.GitHubApi)
    calls: list[tuple[str, str, bool, bool]] = []

    def request(
        method: str,
        path: str,
        data=None,
        *,
        allow_404: bool = False,
        allow_422: bool = False,
    ):
        calls.append((method, path, allow_404, allow_422))
        return None

    monkeypatch.setattr(api, "request", request)
    monkeypatch.setattr(api, "get_ref", lambda _branch: None)
    api.delete_ref("codex/dna-v1")
    assert calls[-1][0] == "DELETE" and calls[-1][2] is True

    monkeypatch.setattr(api, "get_finalization_receipt", lambda *_args: True)
    api.create_finalization_receipt(42, "f" * 40, TEST_POLICY)
    assert calls[-1][0] == "POST" and calls[-1][3] is True
    assert calls[-1][1] == "/git/refs"


def test_finalizer_reads_complete_original_head_poison_status_history(monkeypatch) -> None:
    api = lifecycle.GitHubApi.__new__(lifecycle.GitHubApi)
    calls: list[tuple[str, dict]] = []
    poison = {
        "state": "failure",
        "context": "pr-governance/policy",
        "description": "Issue snapshot invalidated: #42 mutated",
    }

    def paginate(path: str, parameters: dict):
        calls.append((path, parameters))
        return [
            {"state": "success", "context": "pr-governance/policy"},
            poison,
            {
                "state": "failure",
                "context": "another/check",
                "description": poison["description"],
            },
        ]

    monkeypatch.setattr(api, "paginate", paginate)
    records = api.issue_snapshot_invalidations(governance.FIXTURE_HEAD, POLICY)
    assert records == [poison]
    assert calls == [
        (f"/commits/{governance.FIXTURE_HEAD}/statuses", {})
    ]


def test_receipt_ruleset_requires_exact_namespace_controls_and_actor(monkeypatch) -> None:
    config = TEST_POLICY["branch_lifecycle"]["receipt"]["ruleset"]
    summary = {
        "id": config["ruleset_id"],
        "name": config["name"],
        "source_type": config["source_type"],
        "source": config["source"],
        "target": "tag",
        "enforcement": "active",
        "updated_at": config["verified_updated_at"],
    }
    detail = {
        "id": config["ruleset_id"],
        "name": config["name"],
        "source_type": config["source_type"],
        "source": config["source"],
        "target": "tag",
        "enforcement": "active",
        "updated_at": config["verified_updated_at"],
        "conditions": {
            "ref_name": {"include": [config["include_ref"]], "exclude": []}
        },
        "rules": [{"type": item} for item in config["required_rules"]],
        "bypass_actors": list(config["trusted_bypass_actors"]),
    }
    api = lifecycle.GitHubApi.__new__(lifecycle.GitHubApi)
    monkeypatch.setattr(api, "paginate", lambda *_args: [summary])
    monkeypatch.setattr(api, "request", lambda *_args, **_kwargs: detail)
    api.verify_receipt_ruleset(TEST_POLICY)

    # GitHub omits bypass_actors unless the caller can write the ruleset.
    # The immutable policy attestation plus exact id/updated_at remains usable
    # with the workflow token's Metadata:read permission.
    detail.pop("bypass_actors")
    api.verify_receipt_ruleset(TEST_POLICY)

    detail["rules"] = [{"type": "creation"}, {"type": "deletion"}]
    try:
        api.verify_receipt_ruleset(TEST_POLICY)
    except RuntimeError as exc:
        assert "lacks required protections" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("incomplete receipt ruleset must fail closed")


def test_real_finalizer_reads_immutable_tree_and_enforces_atomic_merge_shape(
    monkeypatch,
) -> None:
    pr = _pr(merged=True)
    merge_sha = pr["merge_commit_sha"]
    authority_payload = pr_payload(pr)
    authority = governance.fixture_contract_evidence(
        authority_payload, source_ref=merge_sha
    )
    changes = governance.with_contract_change(governance._fixture_changes(), authority)
    issue_records = governance.fixture_issue_records(authority_payload)
    # This edit happens after merge. It must not influence validation or closure authority.
    pr["body"] = pr["body"].replace(
        "Included-Issues: #42", "Included-Issues: #999"
    )

    api = lifecycle.GitHubApi.__new__(lifecycle.GitHubApi)
    seen_refs: list[str] = []
    monkeypatch.setattr(api, "issue_snapshot_invalidations", lambda *_args: [])

    monkeypatch.setattr(
        api,
        "get_commit",
        lambda sha: {
            "sha": sha,
            "parents": [{"sha": authority.contract["base_sha"]}],
        },
    )

    def get_contract(path: str, source_ref: str, policy: dict):
        assert path == authority.path
        seen_refs.append(source_ref)
        if source_ref == merge_sha:
            return authority
        return governance.ContractEvidence(
            path=authority.path,
            source_ref=source_ref,
            blob_sha=authority.blob_sha,
            content_sha256=authority.content_sha256,
            contract=authority.contract,
        )

    monkeypatch.setattr(api, "get_contract_at_ref", get_contract)
    monkeypatch.setattr(api, "get_issue", lambda number: issue_records[number])
    monkeypatch.setattr(
        api,
        "get_issue_edit_identity",
        lambda number, _policy: {
            "id": issue_records[number]["node_id"],
            "number": number,
            "lastEditedAt": issue_records[number][governance.ISSUE_LAST_EDITED_KEY],
        },
    )
    monkeypatch.setattr(api, "list_issue_events", lambda _number: [])
    monkeypatch.setattr(api, "list_pulls", lambda **_kwargs: [])
    monkeypatch.setattr(lifecycle, "collect_changes", lambda *_args: changes)
    monkeypatch.setattr(lifecycle, "prior_branch_prs", lambda *_args: [])
    monkeypatch.setattr(lifecycle, "scope_freeze_is_ancestor", lambda *_args: True)

    result = api.validate_finalization(pr, TEST_POLICY)
    assert result.errors == []
    assert result.contract == authority
    assert seen_refs == [merge_sha, governance.FIXTURE_HEAD]
    assert result.contract.contract["included_issues"] == [42]

    poison = {
        "state": "failure",
        "context": "pr-governance/policy",
        "description": "Issue snapshot invalidated: #42 mutated",
    }
    monkeypatch.setattr(
        api, "issue_snapshot_invalidations", lambda *_args: [poison]
    )
    poisoned = api.validate_finalization(pr, TEST_POLICY)
    assert any("permanently invalidated" in item for item in poisoned.errors)
    monkeypatch.setattr(api, "issue_snapshot_invalidations", lambda *_args: [])

    monkeypatch.setattr(
        api,
        "get_commit",
        lambda sha: {"sha": sha, "parents": [{"sha": "0" * 40}]},
    )
    wrong_parent = api.validate_finalization(pr, TEST_POLICY)
    assert any("not one atomic base-to-merge commit" in item for item in wrong_parent.errors)

    monkeypatch.setattr(
        api,
        "get_commit",
        lambda sha: {
            "sha": sha,
            "parents": [
                {"sha": authority.contract["base_sha"]},
                {"sha": "1" * 40},
            ],
        },
    )
    multi_parent = api.validate_finalization(pr, TEST_POLICY)
    assert any("must have one parent" in item for item in multi_parent.errors)


def test_release_preparation_finalizer_validates_stage_and_release_contracts(
    monkeypatch,
) -> None:
    target = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope=governance.FIXTURE_HEAD,
    )["pull_request"]
    target.update(
        number=77,
        draft=True,
        state="open",
        created_at="2026-08-24T00:00:00Z",
    )
    preparation = governance.fixture_payload(
        "develop",
        "codex/release-preparation-v2",
        "feature",
        stage="release-preparation@v2",
    )["pull_request"]
    preparation.update(
        merged_at="2026-08-25T02:00:00Z",
        merge_commit_sha="f" * 40,
    )
    preparation["head"]["sha"] = "a" * 40
    preparation["base"]["sha"] = governance.FIXTURE_HEAD
    target["head"]["sha"] = "f" * 40
    stage_contract = governance.fixture_contract_evidence(
        pr_payload(preparation), source_ref="f" * 40
    )
    release_contract = governance.fixture_contract_evidence(
        pr_payload(target),
        source_ref="f" * 40,
        overrides={
            "release_preparation": {
                "pr_number": preparation["number"],
                "base_sha": governance.FIXTURE_HEAD,
            }
        },
    )
    changes = governance.ChangeSet(
        (
            governance.ChangedPath(stage_contract.path, 40, 0),
            governance.ChangedPath(release_contract.path, 40, 0),
            governance.ChangedPath("docs/release-notes.md", 5, 0),
        )
    )
    records = governance.fixture_issue_records(pr_payload(preparation))

    api = lifecycle.GitHubApi.__new__(lifecycle.GitHubApi)
    monkeypatch.setattr(api, "issue_snapshot_invalidations", lambda *_args: [])
    monkeypatch.setattr(
        api,
        "get_commit",
        lambda sha: {
            "sha": sha,
            "parents": [{"sha": stage_contract.contract["base_sha"]}],
        },
    )

    authorities = {
        stage_contract.path: stage_contract,
        release_contract.path: release_contract,
    }

    def get_contract(path: str, source_ref: str, policy: dict):
        authority = authorities[path]
        return governance.ContractEvidence(
            path=authority.path,
            source_ref=source_ref,
            blob_sha=authority.blob_sha,
            content_sha256=authority.content_sha256,
            contract=authority.contract,
        )

    monkeypatch.setattr(api, "get_contract_at_ref", get_contract)
    monkeypatch.setattr(api, "get_issue", lambda number: records[number])
    monkeypatch.setattr(
        api,
        "get_issue_edit_identity",
        lambda number, _policy: {
            "id": records[number]["node_id"],
            "number": number,
            "lastEditedAt": records[number][governance.ISSUE_LAST_EDITED_KEY],
        },
    )
    monkeypatch.setattr(api, "list_issue_events", lambda _number: [])
    monkeypatch.setattr(api, "get_pull", lambda number: target if number == 77 else None)
    monkeypatch.setattr(
        api,
        "list_pulls",
        lambda **_kwargs: [target],
    )
    monkeypatch.setattr(lifecycle, "collect_changes", lambda *_args: changes)
    monkeypatch.setattr(lifecycle, "prior_branch_prs", lambda *_args: [])
    monkeypatch.setattr(lifecycle, "scope_freeze_is_ancestor", lambda *_args: True)

    result = api.validate_finalization(preparation, TEST_POLICY)
    assert result.errors == []
    assert result.contract.contract["stage"] == "release-preparation@v2"

    target["draft"] = False
    rejected = api.validate_finalization(preparation, TEST_POLICY)
    assert any("was not draft when preparation merged" in item for item in rejected.errors)

    target.update(
        state="closed",
        draft=False,
        closed_at="2026-08-26T00:00:00Z",
        merged_at="2026-08-26T00:00:00Z",
    )
    scheduled_retry = api.validate_finalization(preparation, TEST_POLICY)
    assert any("was not open when preparation merged" in item for item in scheduled_retry.errors)
    assert any("was not draft when preparation merged" in item for item in scheduled_retry.errors)
