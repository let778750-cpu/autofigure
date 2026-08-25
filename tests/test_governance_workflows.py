"""Static security contracts for trusted governance workflows."""

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_pr_governance_executes_only_trusted_base_checker() -> None:
    workflow = (ROOT / ".github/workflows/pr-governance.yml").read_text(
        encoding="utf-8"
    )
    checker = (ROOT / ".github/scripts/check_pr_governance.py").read_text(
        encoding="utf-8"
    )
    assert "pull_request_target:" in workflow
    assert "pull_request_review:" not in workflow
    assert "- labeled" in workflow
    assert "- unlabeled" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha }}" not in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" not in workflow
    assert "refs/pull/" not in workflow
    assert "git fetch" not in workflow
    assert "PR-Body-SHA256:" not in workflow
    assert "issues: read" in workflow
    assert "statuses: write" in workflow
    assert workflow.count("github.rest.repos.createCommitStatus") == 4
    assert "governance-pr-${{ github.event.pull_request.number }}" not in workflow
    assert (
        "governance-head-${{ github.repository_id }}-"
        "${{ github.event.pull_request.head.sha }}" in workflow
    )
    assert workflow.count("github.event.label.name == 'governance:recheck'") == 2
    assert "sha: process.env.EXPECTED_HEAD" in workflow
    assert "context: 'pr-governance/policy'" in workflow
    assert "context: 'pr-governance/main-owner-approval'" in workflow
    assert workflow.index("state: 'pending'") < workflow.index("uses: actions/checkout@v4")
    assert "current head-bound contract" in workflow
    assert "core.setOutput('body-sha256', bodySha256)" in workflow
    assert "EXPECTED_BODY_SHA256" in workflow
    assert "stableBodyMirror" in workflow
    assert "&& stableBodyMirror" in workflow
    assert "sameHeadOpenPulls.length === 1" in workflow
    assert "sameHeadOpenPulls[0].number === currentPull.number" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "/rules/branches/" in checker
    assert "strict_required_status_checks_policy" in checker
    assert "required_pr_rule_parameters" in checker
    assert "subprocess" not in checker
    assert "/pulls/{number}/files" in checker

    policy = json.loads((ROOT / ".github/governance-policy.json").read_text(encoding="utf-8"))
    approval = policy["release_approval"]
    assert approval["policy_status_context"] == "pr-governance/policy"
    assert approval["owner_status_context"] == "pr-governance/main-owner-approval"
    assert approval["trusted_recheck_label"] == "governance:recheck"
    remote = policy["remote_merge_gate"]
    assert remote["require_app_binding"] is True
    assert remote["required_contexts_by_base"] == {
        "develop": [
            "pr-governance/policy",
            "portable-tests (ubuntu-latest, py3.12)",
            "portable-tests (windows-latest, py3.12)",
            "case-contracts",
            "branch-lifecycle/sunset",
        ],
        "main": [
            "pr-governance/policy",
            "pr-governance/main-owner-approval",
            "portable-tests (ubuntu-latest, py3.12)",
            "portable-tests (windows-latest, py3.12)",
            "case-contracts",
        ],
    }
    assert remote["required_pr_rule_parameters"] == [
        "dismiss_stale_reviews_on_push",
        "require_code_owner_review",
        "require_last_push_approval",
        "required_review_thread_resolution",
    ]
    assert remote["required_ref_guard_rules"] == [
        "deletion",
        "non_fast_forward",
    ]


def test_issue_snapshot_invalidator_is_trusted_exact_head_data_only() -> None:
    workflow = (ROOT / ".github/workflows/issue-snapshot-invalidator.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / ".github/scripts/invalidate_issue_snapshots.py").read_text(
        encoding="utf-8"
    )
    schema = json.loads(
        (ROOT / ".github/governance-contract.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert "issues:" in workflow
    for action in ("edited", "labeled", "unlabeled", "closed", "reopened", "transferred"):
        assert f"- {action}" in workflow
    assert workflow.count("ref: ${{ github.event.repository.default_branch }}") == 3
    assert "github.event.pull_request.head.sha" not in workflow
    assert "refs/pull/" not in workflow
    assert "persist-credentials: false" in workflow
    assert "statuses: write" in workflow
    assert "contents: write" not in workflow
    assert workflow.count("issues: write") == 1
    normalize_block = workflow.split("  normalize-taxonomy:", 1)[1].split(
        "  discover:", 1
    )[0]
    invalidation_blocks = workflow.split("  discover:", 1)[1]
    assert "issues: write" in normalize_block
    assert "issues: write" not in invalidation_blocks
    assert "pull-requests: write" not in workflow
    assert "--discover" in workflow
    assert "--invalidate-head" in workflow
    assert "group: ${{ matrix.target.coordination_group }}" in workflow
    assert "cancel-in-progress: true" in invalidation_blocks
    assert "fetch_contract_evidence" in script
    assert "fetch_contract_path_evidence" in script
    assert "actions/checkout" not in script
    assert "subprocess" not in script
    assert "issue_snapshots" in schema["required"]
    assert set(schema["properties"]["issue_snapshots"]["items"]["required"]) == {
        "number",
        "role",
        "node_id",
        "last_edited_at",
        "title_sha256",
        "body_sha256",
        "labels_sha256",
        "managed_event_cursor",
    }


def test_lifecycle_has_scheduled_supervision_without_pr_head_execution() -> None:
    workflow = (ROOT / ".github/workflows/branch-lifecycle.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request_target:" in workflow
    assert "schedule:" in workflow
    assert workflow.count("ref: ${{ github.event.repository.default_branch }}") == 2
    assert "github.event.pull_request.base.sha" not in workflow
    assert "github.event.pull_request.head.sha" not in workflow
    assert "python .github/scripts/check_branch_lifecycle.py --mode audit" in workflow
    assert "python .github/scripts/check_branch_lifecycle.py --mode finalize" in workflow
    assert 'pr_number:' in workflow
    assert "required: true" in workflow
    assert "github.event_name == 'schedule' ||" not in workflow.split(
        "  finalize:", 1
    )[1]
    assert "inputs.pr_number != ''" in workflow
    assert "github.event.pull_request.number || inputs.pr_number || 'audit'" in workflow
    audit_permissions = workflow.split("  audit:", 1)[1].split("  finalize:", 1)[0]
    finalizer_permissions = workflow.split("  finalize:", 1)[1]
    assert "contents: write" not in audit_permissions
    assert "issues: write" not in audit_permissions
    assert "statuses: write" in audit_permissions
    assert "contents: write" in workflow
    assert "issues: write" in workflow
    assert "statuses: write" in finalizer_permissions
    workflow_texts = [
        path.read_text(encoding="utf-8")
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
    ]
    assert sum(text.count("contents: write") for text in workflow_texts) == 1
    assert sum(text.count("issues: write") for text in workflow_texts) == 2
    issue_workflow = (
        ROOT / ".github/workflows/issue-snapshot-invalidator.yml"
    ).read_text(encoding="utf-8")
    assert "normalize-taxonomy:" in issue_workflow
    assert "issues: write" in issue_workflow
    assert "contents: write" not in issue_workflow

    policy = json.loads(
        (ROOT / ".github/governance-policy.json").read_text(encoding="utf-8")
    )
    lifecycle = policy["branch_lifecycle"]
    assert lifecycle["scheduled_finalization"] is False
    assert lifecycle["manual_retry_requires_pr_number"] is True
    receipt = lifecycle["receipt"]
    assert receipt["kind"] == "protected-lightweight-tag"
    assert receipt["target"] == "exact-merge-commit-sha"
    assert {"creation", "update", "deletion"}.issubset(
        receipt["ruleset"]["required_rules"]
    )


def test_ci_declares_merge_group_checks_requested() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "merge_group:" in workflow
    assert "checks_requested" in workflow
    assert "name: portable-tests (${{ matrix.os }}, py${{ matrix.python }})" in workflow
    assert "name: case-contracts" in workflow
    assert "name: pytest (" not in workflow


def test_workstream_contract_is_linked_and_uses_one_taxonomy() -> None:
    governance = (ROOT / "docs/WORKSTREAM_GOVERNANCE.md").read_text(
        encoding="utf-8"
    )
    normalized_governance = " ".join(governance.split())
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    pull_template = (ROOT / ".github/pull_request_template.md").read_text(
        encoding="utf-8"
    )
    migration = json.loads(
        (ROOT / "docs/dirty-baseline-migration.json").read_text(encoding="utf-8")
    )

    assert "docs/WORKSTREAM_GOVERNANCE.md" in contributing
    assert "docs/WORKSTREAM_GOVERNANCE.md" in pull_template
    assert "只有 `main` 与 `develop` 常驻" in governance
    for area in (
        "visual-grammar",
        "typography",
        "member-geometry",
        "microasset-fidelity",
        "asset-representation",
        "qa-repair",
        "route-parity",
    ):
        assert f"`area:{area}`" in governance
    assert "`Area` | single select" in governance
    assert "`Workstream` | text" in governance
    assert "超过 3 个" in normalized_governance
    assert "超过 30 个" in normalized_governance
    assert "超过 1500 LOC" in normalized_governance
    assert "最长 14 天 sunset" in normalized_governance
    assert "codex/case04-dna-fidelity-v1" in governance

    groups = {item["id"]: item for item in migration["groups"]}
    assert groups["case-evidence"]["branch_hint"] == "codex/case-<case-id>-evidence-v1"
    assert groups["comparison"]["branch_hint"] == "codex/route-comparison-v1"


def test_issue_form_default_labels_use_publication_taxonomy() -> None:
    templates = ROOT / ".github/ISSUE_TEMPLATE"
    expected = {
        "bug.yml": {'  - "type:defect"'},
        "investigation.yml": set(),
        "scientific-fidelity.yml": {
            '  - "type:defect"',
            '  - "topic:reference-fidelity"',
            '  - "area:microasset-fidelity"',
        },
        "module-epic.yml": {'  - "type:epic"'},
    }
    for name, expected_labels in expected.items():
        text = (templates / name).read_text(encoding="utf-8")
        lines = text.splitlines()
        labels: set[str] = set()
        if "labels:" in lines:
            start = lines.index("labels:") + 1
            for line in lines[start:]:
                if not line.startswith("  - "):
                    break
                labels.add(line)
        assert labels == expected_labels
        assert "id: primary_area" in text
        assert "label: Primary Area" in text
        for area in (
            "visual-grammar",
            "typography",
            "member-geometry",
            "microasset-fidelity",
            "asset-representation",
            "qa-repair",
            "route-parity",
        ):
            assert f'        - "area:{area}"' in text
