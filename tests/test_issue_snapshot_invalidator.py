"""Tests for trusted Issue-mutation invalidation of exact PR heads."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / ".github" / "scripts"

GOVERNANCE_SPEC = importlib.util.spec_from_file_location(
    "check_pr_governance", SCRIPT_DIR / "check_pr_governance.py"
)
assert GOVERNANCE_SPEC is not None and GOVERNANCE_SPEC.loader is not None
governance = importlib.util.module_from_spec(GOVERNANCE_SPEC)
sys.modules[GOVERNANCE_SPEC.name] = governance
GOVERNANCE_SPEC.loader.exec_module(governance)

INVALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "autofigure_issue_snapshot_invalidator",
    SCRIPT_DIR / "invalidate_issue_snapshots.py",
)
assert INVALIDATOR_SPEC is not None and INVALIDATOR_SPEC.loader is not None
invalidator = importlib.util.module_from_spec(INVALIDATOR_SPEC)
sys.modules[INVALIDATOR_SPEC.name] = invalidator
INVALIDATOR_SPEC.loader.exec_module(invalidator)

POLICY = json.loads(
    (ROOT / ".github" / "governance-policy.json").read_text(encoding="utf-8")
)


def _event(number: int = 42, *, action: str = "edited", sender: dict | None = None) -> dict:
    return {
        "action": action,
        "repository": {"id": 778750, "full_name": POLICY["repository"]},
        "issue": {"number": number},
        "sender": sender or {"login": "let778750-cpu", "type": "User"},
    }


def test_discovery_finds_referenced_issue_without_executing_head(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    pull = payload["pull_request"]
    evidence = governance.fixture_contract_evidence(payload)
    monkeypatch.setattr(
        invalidator, "list_governed_pulls_awaiting_receipt", lambda _policy: [pull]
    )
    monkeypatch.setattr(
        invalidator,
        "pull_authority_contracts",
        lambda _pull, _policy: [evidence],
    )

    discovered = invalidator.discover_invalidations(_event(42), POLICY)
    group = f"governance-head-778750-{governance.FIXTURE_HEAD}"
    assert discovered[group]["head"] == governance.FIXTURE_HEAD
    assert discovered[group]["pr_number"] == 99
    assert "references Issue #42 as included" in discovered[group]["reason"]
    assert invalidator.discover_invalidations(_event(999), POLICY) == {}


def test_unreadable_contract_poisons_known_exact_head(monkeypatch) -> None:
    pull = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )["pull_request"]
    monkeypatch.setattr(
        invalidator, "list_governed_pulls_awaiting_receipt", lambda _policy: [pull]
    )

    def unreadable(_pull, _policy):
        raise ValueError("duplicate JSON key")

    monkeypatch.setattr(invalidator, "pull_authority_contracts", unreadable)
    discovered = invalidator.discover_invalidations(_event(42), POLICY)
    group = f"governance-head-778750-{governance.FIXTURE_HEAD}"
    assert list(discovered) == [group]
    assert "contract unreadable" in discovered[group]["reason"]


def test_discovery_rejects_wrong_repository_and_malformed_issue(monkeypatch) -> None:
    monkeypatch.setattr(
        invalidator, "list_governed_pulls_awaiting_receipt", lambda _policy: []
    )
    with pytest.raises(ValueError, match="repository"):
        invalidator.discover_invalidations(
            {
                "repository": {"id": 778750, "full_name": "other/repo"},
                "issue": {"number": 42},
            },
            POLICY,
        )
    with pytest.raises(ValueError, match="issue.number"):
        invalidator.discover_invalidations(
            {
                "repository": {"id": 778750, "full_name": POLICY["repository"]},
                "issue": {},
            },
            POLICY,
        )


def test_contract_reference_requires_valid_snapshot_set() -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    evidence = governance.fixture_contract_evidence(
        payload, overrides={"issue_snapshots": []}
    )
    with pytest.raises(ValueError, match="issue_snapshots"):
        invalidator.contract_references_issue(evidence, 42)


def test_scan_includes_merged_develop_pr_until_exact_receipt(monkeypatch) -> None:
    open_pull = governance.fixture_payload(
        "develop", "codex/dna-v1", "feature"
    )["pull_request"]
    merged = json.loads(json.dumps(open_pull))
    merged.update(
        number=100,
        merged_at="2026-08-25T02:00:00Z",
        merge_commit_sha="f" * 40,
    )
    closed_unmerged = json.loads(json.dumps(open_pull))
    closed_unmerged.update(number=101, merged_at=None, merge_commit_sha=None)
    merged_release = governance.fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v2",
        sunset="not-applicable",
        rollback="release-merge",
        scope=governance.FIXTURE_HEAD,
    )["pull_request"]
    merged_release.update(
        number=102,
        merged_at="2026-08-25T03:00:00Z",
        merge_commit_sha="e" * 40,
    )

    def pulls(state: str, _policy: dict):
        return [open_pull] if state == "open" else [merged, closed_unmerged, merged_release]

    monkeypatch.setattr(invalidator, "_list_pulls", pulls)
    monkeypatch.setattr(invalidator, "finalization_receipt_exists", lambda *_args: False)
    records = invalidator.list_governed_pulls_awaiting_receipt(POLICY)
    assert [(item["number"], item["_autofigure_invalidation_phase"]) for item in records] == [
        (99, "open"),
        (100, "merged-unreceipted"),
    ]

    monkeypatch.setattr(invalidator, "finalization_receipt_exists", lambda *_args: True)
    assert [
        item["number"]
        for item in invalidator.list_governed_pulls_awaiting_receipt(POLICY)
    ] == [99]


def test_merged_unreceipted_target_uses_finalizer_coordination_group(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    pull = payload["pull_request"]
    pull.update(
        merged_at="2026-08-25T02:00:00Z",
        merge_commit_sha="f" * 40,
        _autofigure_invalidation_phase="merged-unreceipted",
    )
    evidence = governance.fixture_contract_evidence(payload)
    monkeypatch.setattr(
        invalidator, "list_governed_pulls_awaiting_receipt", lambda _policy: [pull]
    )
    monkeypatch.setattr(
        invalidator, "pull_authority_contracts", lambda *_args: [evidence]
    )
    discovered = invalidator.discover_invalidations(_event(42), POLICY)
    group = "branch-lifecycle-778750-99"
    assert list(discovered) == [group]
    assert discovered[group]["phase"] == "merged-unreceipted"


def test_trusted_finalizer_close_is_exempt_only_for_included_role(monkeypatch) -> None:
    payload = governance.fixture_payload("develop", "codex/dna-v1", "feature")
    pull = payload["pull_request"]
    evidence = governance.fixture_contract_evidence(payload)
    monkeypatch.setattr(
        invalidator, "list_governed_pulls_awaiting_receipt", lambda _policy: [pull]
    )
    monkeypatch.setattr(
        invalidator, "pull_authority_contracts", lambda *_args: [evidence]
    )
    trusted = {"login": "github-actions[bot]", "type": "Bot"}
    assert invalidator.discover_invalidations(
        _event(42, action="closed", sender=trusted), POLICY
    ) == {}
    assert invalidator.discover_invalidations(
        _event(40, action="closed", sender=trusted), POLICY
    )
