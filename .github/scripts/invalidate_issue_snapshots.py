"""Invalidate exact-head PR governance when a referenced GitHub Issue mutates.

This trusted default-branch helper treats every PR revision as inert API data. It
never checks out or executes a pull-request head. A failure status with the
reserved description prefix is an irreversible poison pill for that commit;
the PR checker requires a new contract commit/head before review can pass again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import check_pr_governance as governance


def _is_governed_pull(pull: dict[str, Any], policy: dict[str, Any]) -> bool:
    base = str((pull.get("base") or {}).get("ref") or "")
    head = str((pull.get("head") or {}).get("ref") or "")
    flow = policy["branch_flow"]
    if base == flow["develop_branch"]:
        kind, _ = governance._branch_match(head, policy)
        return kind in {"feature", "integration"}
    return base == flow["release_branch"] and head == flow["main_head"]


def _list_pulls(state: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    repository = policy["repository"]
    history = policy["branch_history"]
    result: list[dict[str, Any]] = []
    for page in range(1, history["max_pages"] + 1):
        query = urllib.parse.urlencode(
            {
                "state": state,
                "per_page": history["page_size"],
                "page": page,
            }
        )
        value = governance._github_get_json(
            f"{history['github_api']}/repos/{repository}/pulls?{query}"
        )
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise RuntimeError("GitHub open-PR response was not a list of objects")
        result.extend(item for item in value if _is_governed_pull(item, policy))
        if len(value) < history["page_size"]:
            return result
    raise RuntimeError("GitHub open-PR pagination limit was reached")


def _get_json_allow_404(url: str) -> Any:
    request = urllib.request.Request(url, headers=governance._github_headers())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API GET failed: {exc.code} {detail}") from exc


def finalization_receipt_exists(
    pull: dict[str, Any], policy: dict[str, Any]
) -> bool:
    number = pull.get("number")
    merge_sha = pull.get("merge_commit_sha")
    if type(number) is not int or number < 1 or not isinstance(merge_sha, str):
        raise ValueError("merged governed PR identity is incomplete")
    if re.fullmatch(r"[0-9a-f]{40}", merge_sha) is None:
        raise ValueError("merged governed PR merge_commit_sha is invalid")
    prefix = policy["branch_lifecycle"]["receipt"]["tag_prefix"]
    ref_name = urllib.parse.quote(f"tags/{prefix}{number}", safe="")
    endpoint = policy["api_evidence"]["github_api"]
    value = _get_json_allow_404(
        f"{endpoint}/repos/{policy['repository']}/git/ref/{ref_name}"
    )
    if value is None:
        return False
    if not isinstance(value, dict):
        raise RuntimeError(f"PR #{number} finalization receipt ref is invalid")
    target = value.get("object") or {}
    if target.get("type") != "commit" or target.get("sha") != merge_sha:
        raise RuntimeError(
            f"PR #{number} finalization receipt does not bind exact merge SHA"
        )
    return True


def list_governed_pulls_awaiting_receipt(
    policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return open governed PRs plus merged develop PRs lacking a receipt."""

    result: list[dict[str, Any]] = []
    for pull in _list_pulls("open", policy):
        record = dict(pull)
        record["_autofigure_invalidation_phase"] = "open"
        result.append(record)
    develop = policy["branch_flow"]["develop_branch"]
    for pull in _list_pulls("closed", policy):
        if (pull.get("base") or {}).get("ref") != develop or not pull.get("merged_at"):
            continue
        if finalization_receipt_exists(pull, policy):
            continue
        record = dict(pull)
        record["_autofigure_invalidation_phase"] = "merged-unreceipted"
        result.append(record)
    return result


def _validated_primary_contract(
    pull: dict[str, Any],
    changes: governance.ChangeSet,
    policy: dict[str, Any],
) -> governance.ContractEvidence:
    evidence = governance.fetch_contract_evidence(pull, changes, policy)
    if pull.get("_autofigure_invalidation_phase") == "merged-unreceipted":
        contract = evidence.contract
        expected_kind = "stage"
        identity_errors = governance.issue_snapshot_errors(contract)
        if contract.get("contract_kind") != expected_kind:
            identity_errors.append("merged develop authority contract kind is not stage")
        if contract.get("repository") != policy["repository"]:
            identity_errors.append("merged authority contract repository binding is wrong")
        if contract.get("pr_number") != pull.get("number"):
            identity_errors.append("merged authority contract pr_number binding is wrong")
        if contract.get("base_ref") != (pull.get("base") or {}).get("ref"):
            identity_errors.append("merged authority contract base_ref binding is wrong")
        if contract.get("head_ref") != (pull.get("head") or {}).get("ref"):
            identity_errors.append("merged authority contract head_ref binding is wrong")
        if identity_errors:
            raise ValueError("; ".join(identity_errors))
        return evidence
    result = governance.ValidationResult()
    governance._validate_contract_schema(
        result,
        evidence,
        pull,
        changes,
        policy,
        expected_contract_ref=str((pull.get("head") or {}).get("sha") or ""),
    )
    if result.errors:
        raise ValueError("; ".join(result.errors))
    return evidence


def pull_authority_contracts(
    pull: dict[str, Any], policy: dict[str, Any]
) -> list[governance.ContractEvidence]:
    """Read every relevant contract twice from the exact PR head as inert bytes."""

    changes = governance.collect_changes({"pull_request": pull}, policy)
    primary = _validated_primary_contract(pull, changes, policy)
    result = [primary]
    if governance.is_release_preparation_pull(pull, policy):
        release_paths = governance.release_contract_paths(changes, policy)
        if len(release_paths) != 1:
            raise ValueError(
                "release-preparation PR must carry exactly one release contract"
            )
        source_ref = str((pull.get("head") or {}).get("sha") or "")
        prepared = governance.fetch_contract_path_evidence(
            release_paths[0], source_ref, policy
        )
        if prepared.contract.get("contract_kind") != "release":
            raise ValueError("release-preparation contract kind is not release")
        if prepared.contract.get("repository") != policy["repository"]:
            raise ValueError("release-preparation contract repository binding is wrong")
        snapshot_errors = governance.issue_snapshot_errors(prepared.contract)
        if snapshot_errors:
            raise ValueError("; ".join(snapshot_errors))
        result.append(prepared)
    return result


def contract_issue_role(
    evidence: governance.ContractEvidence, issue_number: int
) -> str | None:
    errors = governance.issue_snapshot_errors(evidence.contract)
    if errors:
        raise ValueError("; ".join(errors))
    for snapshot in evidence.contract["issue_snapshots"]:
        if snapshot.get("number") == issue_number:
            return str(snapshot["role"])
    return None


def contract_references_issue(
    evidence: governance.ContractEvidence, issue_number: int
) -> bool:
    return contract_issue_role(evidence, issue_number) is not None


def _trusted_finalizer_close(payload: dict[str, Any], policy: dict[str, Any]) -> bool:
    if payload.get("action") != "closed":
        return False
    sender = payload.get("sender") or {}
    config = policy["branch_lifecycle"]["trusted_finalizer_actor"]
    return (
        sender.get("type") == config["account_type"]
        and str(sender.get("login") or "").casefold()
        in {str(login).casefold() for login in config["logins"]}
    )


def post_failure_status(
    sha: str, issue_number: int, policy: dict[str, Any]
) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("refusing to write status for an invalid head SHA")
    description = (
        f"{governance.ISSUE_INVALIDATION_DESCRIPTION_PREFIX} "
        f"#{issue_number} mutated; commit a new contract/head"
    )
    body = json.dumps(
        {
            "state": "failure",
            "context": policy["release_approval"]["policy_status_context"],
            "description": description,
        }
    ).encode("utf-8")
    endpoint = policy["api_evidence"]["github_api"]
    url = f"{endpoint}/repos/{policy['repository']}/statuses/{sha}"
    request = urllib.request.Request(
        url,
        data=body,
        headers={**governance._github_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in {200, 201}:
            raise RuntimeError(f"GitHub status write returned HTTP {response.status}")


def discover_invalidations(
    payload: dict[str, Any], policy: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    repository = payload.get("repository") or {}
    if repository.get("full_name") != policy["repository"]:
        raise ValueError("Issue event repository does not match governance policy")
    repository_id = repository.get("id")
    if type(repository_id) is not int or repository_id < 1:
        raise ValueError("Issue event repository.id is missing or invalid")
    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    if type(issue_number) is not int or issue_number < 1:
        raise ValueError("Issue event has no positive issue.number")

    failures: dict[str, dict[str, Any]] = {}
    trusted_close = _trusted_finalizer_close(payload, policy)
    for pull in list_governed_pulls_awaiting_receipt(policy):
        head_sha = str((pull.get("head") or {}).get("sha") or "")
        number = pull.get("number")
        if re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
            raise ValueError(f"PR #{number} head SHA is invalid")
        if type(number) is not int or number < 1:
            raise ValueError("governed PR number is missing or invalid")
        phase = pull.get("_autofigure_invalidation_phase")
        coordination_group = (
            f"branch-lifecycle-{repository_id}-{number}"
            if phase == "merged-unreceipted"
            else f"governance-head-{repository_id}-{head_sha}"
        )
        try:
            contracts = pull_authority_contracts(pull, policy)
            roles = [
                role
                for contract in contracts
                if (role := contract_issue_role(contract, issue_number)) is not None
            ]
            if not roles:
                continue
            # Closing an Included Issue is the finalizer's intended mutation.
            # Only that exact trusted actor/action/role tuple is exempt; the
            # same actor changing an Epic or Deferred Issue still poisons.
            if trusted_close and set(roles) == {"included"}:
                continue
            reason = f"PR #{number} references Issue #{issue_number} as {','.join(roles)}"
        except (OSError, RuntimeError, ValueError) as exc:
            # A malformed/unreadable authority contract cannot prove non-reference.
            # Poison this known exact head instead of preserving a prior success.
            reason = f"PR #{number} contract unreadable: {exc}"
        failures[coordination_group] = {
            "head": head_sha,
            "pr_number": number,
            "phase": phase,
            "coordination_group": coordination_group,
            "reason": reason,
        }

    return failures


def invalidate(payload: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    failures = discover_invalidations(payload, policy)
    issue_number = int((payload.get("issue") or {})["number"])
    for target in failures.values():
        post_failure_status(str(target["head"]), issue_number, policy)
    return [str(target["reason"]) for target in failures.values()]


def _write_discovery_output(targets: list[dict[str, Any]]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    if not output_path:
        raise ValueError("GITHUB_OUTPUT is required in discovery mode")
    with Path(output_path).open("a", encoding="utf-8", newline="\n") as output:
        output.write("targets=" + json.dumps(targets, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=governance.DEFAULT_POLICY)
    parser.add_argument("--event", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--discover", action="store_true")
    mode.add_argument("--invalidate-head")
    parser.add_argument("--issue-number", type=int)
    args = parser.parse_args(argv)
    event_value = os.environ.get("GITHUB_EVENT_PATH", "")
    if args.event is None and not event_value:
        raise SystemExit("GITHUB_EVENT_PATH or --event is required")
    payload = governance.load_json((args.event or Path(event_value)).resolve())
    policy = governance.load_json(args.policy.resolve())
    try:
        if args.discover:
            discovered = discover_invalidations(payload, policy)
            for target in discovered.values():
                print(f"::warning::{target['reason']}")
            matrix_targets = [
                {
                    "head": target["head"],
                    "pr_number": target["pr_number"],
                    "coordination_group": target["coordination_group"],
                }
                for target in discovered.values()
            ]
            _write_discovery_output(matrix_targets)
            print(
                "Issue snapshot discovery: "
                f"{len(discovered)} exact head(s) require invalidation"
            )
            return 0
        if args.invalidate_head is not None:
            event_number = (payload.get("issue") or {}).get("number")
            if (
                type(args.issue_number) is not int
                or args.issue_number < 1
                or args.issue_number != event_number
            ):
                raise ValueError("matrix issue number does not match the trusted event")
            post_failure_status(args.invalidate_head, args.issue_number, policy)
            print(f"Issue snapshot invalidated exact head {args.invalidate_head}")
            return 0
        failures = invalidate(payload, policy)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"::error::Issue snapshot invalidation failed closed: {exc}")
        return 1
    for failure in failures:
        print(f"::warning::{failure}")
    print(f"Issue snapshot invalidation: {len(failures)} exact head(s) failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
