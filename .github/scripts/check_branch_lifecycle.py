"""Supervise topic-branch sunset and finalize merged develop Stages.

This script is executed only from a trusted default revision. It reads the
head-bound JSON contract as inert data through the GitHub API and never fetches
or runs PR-head content. Open expired PRs receive a failing status; the merged
finalizer reads the immutable merge tree and never trusts editable PR prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from check_pr_governance import (
    ContractEvidence,
    DEFAULT_POLICY,
    HISTORICAL_OPEN_KEY,
    ISSUE_INVALIDATION_DESCRIPTION_PREFIX,
    ISSUE_LAST_EDITED_KEY,
    ISSUE_MANAGED_EVENTS_KEY,
    PreparedRelease,
    ValidationResult,
    _issue_snapshot_map,
    _labels_sha256,
    _branch_match,
    _date_value,
    _text_sha256,
    _timestamp,
    _validate_contract_schema,
    collect_changes,
    contract_metadata,
    decode_contract_evidence,
    derive_contract_path,
    issue_snapshot_errors,
    load_json,
    managed_events_after_cursor,
    managed_issue_events,
    prior_branch_prs,
    release_contract_paths,
    scope_freeze_is_ancestor,
    validate,
    validate_prepared_release,
)


@dataclass
class LifecycleResult:
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class FinalizationValidation:
    errors: list[str] = field(default_factory=list)
    contract: ContractEvidence | None = None


ISSUE_TRANSITIONS = {
    "closed": ("closed", "open"),
    "reopened": ("open", "closed"),
}
DRAFT_TRANSITIONS = {
    "convert_to_draft": (True, False),
    # Kept for defensive compatibility with older timeline payload fixtures.
    "converted_to_draft": (True, False),
    "ready_for_review": (False, True),
}


def _state_at_timestamp(
    current_state: Any,
    events: list[dict[str, Any]],
    timestamp: datetime,
    transitions: dict[str, tuple[Any, Any]],
    *,
    label: str,
) -> Any:
    """Rewind a complete ordered event stream to one exact timestamp.

    GitHub timestamps have one-second resolution.  A state transition stamped
    in the same second as the boundary is therefore ambiguous and fails closed.
    Event ids provide a stable order between transitions outside the boundary.
    """

    if timestamp.utcoffset() is None:
        raise RuntimeError(f"{label} merge boundary has no timezone")
    parsed: list[tuple[datetime, int, str]] = []
    seen_ids: set[int] = set()
    for event in events:
        name = event.get("event")
        if name not in transitions:
            continue
        event_id = event.get("id")
        created_at = _timestamp(event.get("created_at"))
        if (
            type(event_id) is not int
            or event_id < 1
            or created_at is None
            or created_at.utcoffset() is None
        ):
            raise RuntimeError(f"{label} transition history is incomplete or invalid")
        if event_id in seen_ids:
            raise RuntimeError(f"{label} transition history contains duplicate event ids")
        seen_ids.add(event_id)
        if created_at == timestamp:
            raise RuntimeError(
                f"{label} transition is timestamp-ambiguous at the merge boundary"
            )
        parsed.append((created_at, event_id, str(name)))

    state = current_state
    for created_at, _event_id, name in sorted(parsed, reverse=True):
        after, before = transitions[name]
        if state != after:
            raise RuntimeError(f"{label} transition history is not internally coherent")
        if created_at < timestamp:
            return state
        state = before
    return state


def _trusted_actor(event: dict[str, Any], policy: dict[str, Any]) -> bool:
    actor = event.get("actor") or {}
    config = policy["branch_lifecycle"]["trusted_finalizer_actor"]
    login = str(actor.get("login") or "").casefold()
    allowed = {str(item).casefold() for item in config["logins"]}
    return actor.get("type") == config["account_type"] and login in allowed


def _ruleset_bypass_attestation(
    ruleset_id: int, updated_at: str, actors: list[dict[str, Any]]
) -> str:
    value = {
        "ruleset_id": ruleset_id,
        "updated_at": updated_at,
        "trusted_bypass_actors": actors,
    }
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class GitHubApi:
    def __init__(self, repository: str, token: str, endpoint: str) -> None:
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required for lifecycle supervision")
        self.repository = repository
        self.endpoint = endpoint.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autofigure-branch-lifecycle",
        }

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        allow_404: bool = False,
        allow_422: bool = False,
    ) -> Any:
        encoded = None if data is None else json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/repos/{self.repository}{path}",
            data=encoded,
            headers=self.headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                return None
            if allow_422 and exc.code == 422:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def graphql(
        self, query: str, variables: dict[str, Any], policy: dict[str, Any]
    ) -> dict[str, Any]:
        endpoint = policy["api_evidence"].get("github_graphql")
        if not isinstance(endpoint, str) or not endpoint:
            raise RuntimeError("GitHub GraphQL endpoint is missing from trusted policy")
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={**self.headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub GraphQL request failed: {exc.code} {detail}"
            ) from exc
        if not isinstance(value, dict) or value.get("errors"):
            raise RuntimeError("GitHub GraphQL response is invalid or contains errors")
        return value

    def get_issue_edit_identity(
        self, number: int, policy: dict[str, Any]
    ) -> dict[str, Any]:
        owner, name = self.repository.split("/", 1)
        value = self.graphql(
            (
                "query($owner:String!,$name:String!,$number:Int!){"
                "repository(owner:$owner,name:$name){issue(number:$number){"
                "id number lastEditedAt}}}"
            ),
            {"owner": owner, "name": name, "number": number},
            policy,
        )
        issue = ((value.get("data") or {}).get("repository") or {}).get("issue")
        if (
            not isinstance(issue, dict)
            or issue.get("number") != number
            or not isinstance(issue.get("id"), str)
            or not issue["id"]
            or (
                issue.get("lastEditedAt") is not None
                and not isinstance(issue.get("lastEditedAt"), str)
            )
        ):
            raise RuntimeError(f"GitHub Issue #{number} GraphQL identity is invalid")
        return issue

    def paginate(self, path: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 101):
            query = urllib.parse.urlencode(
                {**parameters, "per_page": 100, "page": page}
            )
            value = self.request("GET", f"{path}?{query}")
            if not isinstance(value, list) or not all(
                isinstance(item, dict) for item in value
            ):
                raise RuntimeError(f"GitHub API pagination response is invalid: {path}")
            result.extend(value)
            if len(value) < 100:
                return result
        raise RuntimeError(f"GitHub API pagination limit reached: {path}")

    def list_pulls(self, *, state: str, base: str | None = None) -> list[dict[str, Any]]:
        parameters: dict[str, Any] = {
            "state": state,
            "sort": "updated",
            "direction": "desc",
        }
        if base is not None:
            parameters["base"] = base
        return self.paginate("/pulls", parameters)

    def get_pull(self, number: int) -> dict[str, Any]:
        value = self.request("GET", f"/pulls/{number}")
        if not isinstance(value, dict) or value.get("number") != number:
            raise RuntimeError(f"PR #{number} response is invalid")
        return value

    def get_commit(self, sha: str) -> dict[str, Any]:
        value = self.request("GET", f"/commits/{sha}")
        if not isinstance(value, dict) or value.get("sha") != sha:
            raise RuntimeError(f"commit {sha} response is invalid")
        return value

    def get_contract_at_ref(
        self, path: str, source_ref: str, policy: dict[str, Any]
    ) -> ContractEvidence:
        encoded_path = urllib.parse.quote(path, safe="/")
        query = urllib.parse.urlencode({"ref": source_ref})
        contents = self.request("GET", f"/contents/{encoded_path}?{query}")
        if not isinstance(contents, dict) or not isinstance(contents.get("sha"), str):
            raise RuntimeError("Contents API contract response is invalid")
        blob = self.request("GET", f"/git/blobs/{contents['sha']}")
        return decode_contract_evidence(
            path=path,
            source_ref=source_ref,
            contents_value=contents,
            blob_value=blob,
            policy=policy,
        )

    def get_open_contract(
        self, pr: dict[str, Any], policy: dict[str, Any]
    ) -> FinalizationValidation:
        payload = {"pull_request": pr}
        changes = collect_changes(payload, policy)
        path = derive_contract_path(pr, changes.files, policy)
        head_sha = ((pr.get("head") or {}).get("sha") or "")
        contract = self.get_contract_at_ref(path, head_sha, policy)
        validation = ValidationResult()
        _validate_contract_schema(
            validation,
            contract,
            pr,
            changes,
            policy,
            expected_contract_ref=head_sha,
        )
        return FinalizationValidation(errors=validation.errors, contract=contract)

    def set_status(self, sha: str, *, state: str, context: str, description: str) -> None:
        value = self.request(
            "POST",
            f"/statuses/{sha}",
            {"state": state, "context": context, "description": description[:140]},
        )
        if not isinstance(value, dict) or value.get("state") != state:
            raise RuntimeError(f"commit status verification failed for {sha}")

    def issue_snapshot_invalidations(
        self, head_sha: str, policy: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
            raise RuntimeError("original PR head SHA is invalid for status history")
        context = policy["release_approval"]["policy_status_context"]
        return [
            item
            for item in self.paginate(f"/commits/{head_sha}/statuses", {})
            if item.get("context") == context
            and item.get("state") == "failure"
            and str(item.get("description") or "").startswith(
                ISSUE_INVALIDATION_DESCRIPTION_PREFIX
            )
        ]

    def get_issue(self, number: int) -> dict[str, Any]:
        value = self.request("GET", f"/issues/{number}")
        if not isinstance(value, dict):
            raise RuntimeError(f"Issue #{number} response is invalid")
        return value

    def list_issue_events(self, number: int) -> list[dict[str, Any]]:
        return self.paginate(f"/issues/{number}/events", {})

    def close_issue(self, number: int) -> None:
        self.request(
            "PATCH",
            f"/issues/{number}",
            {"state": "closed", "state_reason": "completed"},
        )
        issue = self.get_issue(number)
        if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
            raise RuntimeError(
                f"Issue #{number} did not remain closed with state_reason=completed"
            )

    def reopen_issue(self, number: int) -> None:
        self.request("PATCH", f"/issues/{number}", {"state": "open"})
        if self.get_issue(number).get("state") != "open":
            raise RuntimeError(f"Issue #{number} did not remain open after rollback restore")

    def get_ref(self, branch: str) -> dict[str, Any] | None:
        ref = urllib.parse.quote(f"heads/{branch}", safe="")
        value = self.request("GET", f"/git/ref/{ref}", allow_404=True)
        if value is not None and not isinstance(value, dict):
            raise RuntimeError(f"branch ref response is invalid: {branch}")
        return value

    def delete_ref(self, branch: str) -> None:
        ref = urllib.parse.quote(f"heads/{branch}", safe="")
        # A concurrent retry may win between get_ref and DELETE.  A 404 is
        # idempotent success only after an exact absence readback.
        self.request("DELETE", f"/git/refs/{ref}", allow_404=True)
        if self.get_ref(branch) is not None:
            raise RuntimeError(f"merged topic branch still exists after deletion: {branch}")

    def _receipt_ref(self, number: int, policy: dict[str, Any]) -> str:
        prefix = policy["branch_lifecycle"]["receipt"]["tag_prefix"]
        return f"tags/{prefix}{number}"

    def get_finalization_receipt(
        self, number: int, merge_sha: str, policy: dict[str, Any]
    ) -> bool:
        ref_name = self._receipt_ref(number, policy)
        encoded = urllib.parse.quote(ref_name, safe="")
        value = self.request("GET", f"/git/ref/{encoded}", allow_404=True)
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

    def create_finalization_receipt(
        self, number: int, merge_sha: str, policy: dict[str, Any]
    ) -> None:
        ref_name = self._receipt_ref(number, policy)
        # POST may race with an event retry.  GitHub returns 422 when the exact
        # ref already exists; only an exact target readback converts that into
        # idempotent success.
        self.request(
            "POST",
            "/git/refs",
            {"ref": f"refs/{ref_name}", "sha": merge_sha},
            allow_422=True,
        )
        if not self.get_finalization_receipt(number, merge_sha, policy):
            raise RuntimeError(f"PR #{number} finalization receipt was not persisted")

    def verify_receipt_ruleset(self, policy: dict[str, Any]) -> None:
        config = policy["branch_lifecycle"]["receipt"]["ruleset"]
        if config.get("state") != "active":
            raise RuntimeError(
                "finalization receipt tag ruleset is not activated and verified"
            )
        expected_id = config.get("ruleset_id")
        expected_updated_at = config.get("verified_updated_at")
        expected_actors = config.get("trusted_bypass_actors")
        attestation = config.get("bypass_attestation_sha256")
        updated_timestamp = (
            _timestamp(expected_updated_at)
            if isinstance(expected_updated_at, str)
            else None
        )
        if (
            type(expected_id) is not int
            or expected_id < 1
            or updated_timestamp is None
            or updated_timestamp.utcoffset() is None
            or not isinstance(expected_actors, list)
            or not isinstance(attestation, str)
            or re.fullmatch(r"[0-9a-f]{64}", attestation) is None
        ):
            raise RuntimeError("finalization receipt ruleset attestation is incomplete")
        if attestation != _ruleset_bypass_attestation(
            expected_id, expected_updated_at, expected_actors
        ):
            raise RuntimeError("finalization receipt ruleset attestation digest is wrong")
        summaries = self.paginate("/rulesets", {"targets": "tag"})
        candidates = [
            item
            for item in summaries
            if item.get("id") == expected_id
            and item.get("name") == config["name"]
            and item.get("source_type") == config["source_type"]
            and str(item.get("source") or "").casefold()
            == str(config["source"]).casefold()
            and item.get("updated_at") == expected_updated_at
        ]
        if len(candidates) != 1 or type(candidates[0].get("id")) is not int:
            raise RuntimeError("exactly one active finalization receipt ruleset is required")
        detail = self.request("GET", f"/rulesets/{candidates[0]['id']}")
        if not isinstance(detail, dict):
            raise RuntimeError("finalization receipt ruleset detail is invalid")
        if (
            detail.get("id") != expected_id
            or detail.get("name") != config["name"]
            or detail.get("source_type") != config["source_type"]
            or str(detail.get("source") or "").casefold()
            != str(config["source"]).casefold()
            or detail.get("target") != "tag"
            or detail.get("enforcement") != "active"
            or detail.get("updated_at") != expected_updated_at
        ):
            raise RuntimeError("finalization receipt ruleset detail identity is wrong")
        ref_names = ((detail.get("conditions") or {}).get("ref_name") or {})
        if ref_names.get("include") != [config["include_ref"]] or ref_names.get(
            "exclude"
        ) not in ([], None):
            raise RuntimeError("finalization receipt ruleset ref condition is not exact")
        rule_types = {
            item.get("type")
            for item in detail.get("rules") or []
            if isinstance(item, dict)
        }
        if not set(config["required_rules"]).issubset(rule_types):
            raise RuntimeError("finalization receipt ruleset lacks required protections")

        def actor_key(item: dict[str, Any]) -> tuple[Any, Any, Any]:
            return (item.get("actor_type"), item.get("actor_id"), item.get("bypass_mode"))

        actual_value = detail.get("bypass_actors")
        actual = (
            {
                actor_key(item)
                for item in actual_value
                if isinstance(item, dict)
            }
            if isinstance(actual_value, list)
            else None
        )
        expected = {
            actor_key(item)
            for item in expected_actors
            if isinstance(item, dict)
        }
        expected_actor_keys = [
            actor_key(item) for item in expected_actors if isinstance(item, dict)
        ]
        trusted_shape = all(
            isinstance(item, dict)
            and set(item) == {"actor_type", "actor_id", "bypass_mode"}
            and item.get("actor_type") == "Integration"
            and type(item.get("actor_id")) is int
            and item["actor_id"] > 0
            and item.get("bypass_mode") == "always"
            for item in expected_actors
        )
        if (
            not expected
            or not trusted_shape
            or expected_actor_keys != sorted(expected)
            or (
                actual is not None
                and (
                    actual != expected
                    or not isinstance(actual_value, list)
                    or len(actual_value) != len(actual)
                )
            )
        ):
            raise RuntimeError(
                "finalization receipt ruleset bypass actors are not the exact trusted set"
            )

    def _issue_record_with_ledger(
        self, number: int, merged_at: datetime, policy: dict[str, Any]
    ) -> dict[str, Any]:
        issue = dict(self.get_issue(number))
        if issue.get("number") != number or "pull_request" in issue:
            raise RuntimeError(f"Issue #{number} REST identity is invalid")
        created_at = _timestamp(issue.get("created_at"))
        if created_at is None or created_at.utcoffset() is None:
            raise RuntimeError(f"Issue #{number} created_at evidence is invalid")
        if created_at > merged_at:
            raise RuntimeError(f"Issue #{number} did not exist when the Stage merged")
        graph_issue = self.get_issue_edit_identity(number, policy)
        if issue.get("node_id") != graph_issue.get("id"):
            raise RuntimeError(
                f"Issue #{number} REST/GraphQL Node ID identity disagrees"
            )
        events = self.list_issue_events(number)
        state_at_merge = _state_at_timestamp(
            issue.get("state"),
            events,
            merged_at,
            ISSUE_TRANSITIONS,
            label=f"Issue #{number}",
        )
        issue[HISTORICAL_OPEN_KEY] = state_at_merge == "open"
        issue[ISSUE_LAST_EDITED_KEY] = graph_issue.get("lastEditedAt")
        issue[ISSUE_MANAGED_EVENTS_KEY] = events
        return issue

    @staticmethod
    def _events_through_snapshot(
        issue: dict[str, Any], cursor: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        events = managed_issue_events(issue)
        if cursor is None:
            if events:
                raise RuntimeError(
                    "managed Issue event cursor is absent from a non-empty ledger"
                )
            return []
        for index, event in enumerate(events):
            if (
                event.get("id") == cursor.get("id")
                and event.get("event") == cursor.get("event")
                and event.get("created_at") == cursor.get("created_at")
            ):
                return events[: index + 1]
        raise RuntimeError("managed Issue event cursor is absent from the complete ledger")

    @staticmethod
    def _state_from_snapshot_events(
        number: int, events: list[dict[str, Any]]
    ) -> str:
        state = "open"
        for event in events:
            name = event.get("event")
            if name == "closed":
                if state != "open":
                    raise RuntimeError(
                        f"Issue #{number} snapshot transition ledger is incoherent"
                    )
                state = "closed"
            elif name == "reopened":
                if state != "closed":
                    raise RuntimeError(
                        f"Issue #{number} snapshot transition ledger is incoherent"
                    )
                state = "open"
        return state

    def _verify_issue_snapshot_ledger(
        self,
        *,
        issue: dict[str, Any],
        snapshot: dict[str, Any],
        merged_at: datetime,
        policy: dict[str, Any],
        allow_trusted_included_close: bool,
    ) -> list[dict[str, Any]]:
        number = int(snapshot["number"])
        title = issue.get("title")
        body = issue.get("body")
        if not isinstance(title, str) or (body is not None and not isinstance(body, str)):
            raise RuntimeError(f"Issue #{number} title/body evidence is invalid")
        if _text_sha256(title) != snapshot["title_sha256"]:
            raise RuntimeError(f"Issue #{number} title changed after scope-freeze")
        if _text_sha256(body or "") != snapshot["body_sha256"]:
            raise RuntimeError(f"Issue #{number} body/Parent Epic changed after scope-freeze")
        if issue.get("node_id") != snapshot["node_id"]:
            raise RuntimeError(
                f"Issue #{number} Node ID changed or the Issue was transferred"
            )
        if issue.get(ISSUE_LAST_EDITED_KEY) != snapshot["last_edited_at"]:
            raise RuntimeError(
                f"Issue #{number} was edited after scope-freeze, even if restored"
            )
        try:
            labels_sha = _labels_sha256(issue)
            snapshot_events = self._events_through_snapshot(
                issue, snapshot["managed_event_cursor"]
            )
            delta = managed_events_after_cursor(
                issue, snapshot["managed_event_cursor"]
            )
        except ValueError as exc:
            raise RuntimeError(f"Issue #{number} managed evidence is invalid: {exc}") from exc
        if labels_sha != snapshot["labels_sha256"]:
            raise RuntimeError(f"Issue #{number} labels changed after scope-freeze")
        snapshot_state = self._state_from_snapshot_events(number, snapshot_events)
        if not delta:
            if issue.get("state") != snapshot_state:
                raise RuntimeError(
                    f"Issue #{number} state changed without complete event provenance"
                )
            return snapshot_events
        allowed = (
            allow_trusted_included_close
            and snapshot.get("role") == "included"
            and snapshot_state == "open"
            and len(delta) == 1
            and delta[0].get("event") == "closed"
            and _trusted_actor(delta[0], policy)
        )
        closed_at = _timestamp(delta[0].get("created_at")) if allowed else None
        if (
            not allowed
            or closed_at is None
            or closed_at.utcoffset() is None
            or closed_at <= merged_at
            or issue.get("state") != "closed"
            or issue.get("state_reason")
            != policy["branch_lifecycle"]["completed_issue_state_reason"]
        ):
            names = ",".join(str(event.get("event")) for event in delta)
            raise RuntimeError(
                f"Issue #{number} managed ledger advanced after scope-freeze "
                f"with non-finalizer mutation(s): {names or '[invalid]'}"
            )
        return snapshot_events

    def issue_records_for_contract(
        self,
        contract: ContractEvidence,
        merged_at: datetime,
        policy: dict[str, Any],
        *,
        sanitize_expected_closes: bool = True,
    ) -> dict[int, dict[str, Any]]:
        snapshot_errors = issue_snapshot_errors(contract.contract)
        if snapshot_errors:
            raise RuntimeError("; ".join(snapshot_errors))
        snapshots = _issue_snapshot_map(contract.contract)
        records: dict[int, dict[str, Any]] = {}
        for number, snapshot in sorted(snapshots.items()):
            issue = self._issue_record_with_ledger(number, merged_at, policy)
            snapshot_events = self._verify_issue_snapshot_ledger(
                issue=issue,
                snapshot=snapshot,
                merged_at=merged_at,
                policy=policy,
                allow_trusted_included_close=True,
            )
            if sanitize_expected_closes:
                # Generic governance validation requires the exact snapshot
                # cursor.  A trusted Included close from an earlier partial
                # finalizer run is audited above, then hidden only from that
                # generic comparison so the idempotent retry can continue.
                issue[ISSUE_MANAGED_EVENTS_KEY] = snapshot_events
            records[number] = issue
        return records

    def verify_issue_scope_ledger(
        self,
        contract: ContractEvidence,
        merged_at: datetime,
        policy: dict[str, Any],
    ) -> None:
        self.issue_records_for_contract(
            contract,
            merged_at,
            policy,
            sanitize_expected_closes=False,
        )

    def validate_finalization(
        self,
        pr: dict[str, Any],
        policy: dict[str, Any],
    ) -> FinalizationValidation:
        payload = {"pull_request": pr}
        merged_at = _timestamp(pr.get("merged_at"))
        if merged_at is None:
            return FinalizationValidation(
                errors=[f"PR #{pr.get('number')}: merged_at is missing or invalid"]
            )
        merge_commit_sha = pr.get("merge_commit_sha")
        if not isinstance(merge_commit_sha, str) or re.fullmatch(
            r"[0-9a-f]{40}", merge_commit_sha
        ) is None:
            return FinalizationValidation(
                errors=[f"PR #{pr.get('number')}: merge_commit_sha is missing or invalid"]
            )
        head_sha = str((pr.get("head") or {}).get("sha") or "")
        snapshot_invalidations = self.issue_snapshot_invalidations(head_sha, policy)
        merge_commit = self.get_commit(merge_commit_sha)
        parents = merge_commit.get("parents")
        if not isinstance(parents, list) or len(parents) != 1:
            return FinalizationValidation(
                errors=[
                    f"PR #{pr.get('number')}: develop Stage merge must have one parent "
                    "(squash/rebase-safe shape)"
                ]
            )
        changes = collect_changes(payload, policy)
        try:
            path = derive_contract_path(pr, changes.files, policy)
            contract = self.get_contract_at_ref(path, merge_commit_sha, policy)
            head_contract = self.get_contract_at_ref(path, head_sha, policy)
        except (RuntimeError, ValueError) as exc:
            return FinalizationValidation(errors=[str(exc)])
        if (
            contract.blob_sha != head_contract.blob_sha
            or contract.content_sha256 != head_contract.content_sha256
        ):
            return FinalizationValidation(
                errors=[
                    f"PR #{pr.get('number')}: merge tree contract differs from the "
                    "policy-validated PR head contract"
                ]
            )
        parent_sha = parents[0].get("sha") if isinstance(parents[0], dict) else None
        contract_base_sha = contract.contract.get("base_sha")
        if parent_sha != contract_base_sha:
            return FinalizationValidation(
                errors=[
                    f"PR #{pr.get('number')}: develop Stage merge parent does not "
                    "equal the authority contract base_sha; the Stage is not one "
                    "atomic base-to-merge commit"
                ]
            )
        metadata = contract_metadata(contract, pr)
        freeze_valid = scope_freeze_is_ancestor(payload, metadata, policy)
        try:
            issue_records = self.issue_records_for_contract(
                contract, merged_at, policy
            )
        except RuntimeError as exc:
            return FinalizationValidation(errors=[str(exc)], contract=contract)
        previous = prior_branch_prs(payload, policy)
        same_head = [
            item
            for item in self.list_pulls(state="open")
            if item.get("number") != pr.get("number")
            and ((item.get("head") or {}).get("sha") or "")
            == ((pr.get("head") or {}).get("sha") or "")
        ]
        validation = validate(
            payload,
            changes,
            policy,
            prior_branch_prs=previous,
            scope_freeze_valid=freeze_valid,
            issue_records=issue_records,
            finalization_merged_at=merged_at,
            contract_evidence=contract,
            expected_contract_ref=merge_commit_sha,
            same_head_open_prs=same_head,
            issue_snapshot_invalidation_records=snapshot_invalidations,
            require_body_mirror=False,
            today=merged_at.date(),
        )
        release_paths = release_contract_paths(changes, policy)
        if release_paths:
            if len(release_paths) != 1:
                validation.errors.append(
                    "release preparation finalizer requires exactly one release contract"
                )
            else:
                release_path = release_paths[0]
                try:
                    release_contract = self.get_contract_at_ref(
                        release_path, merge_commit_sha, policy
                    )
                    release_head_contract = self.get_contract_at_ref(
                        release_path, ((pr.get("head") or {}).get("sha") or ""), policy
                    )
                except (RuntimeError, ValueError) as exc:
                    validation.errors.append(str(exc))
                else:
                    if (
                        release_contract.blob_sha != release_head_contract.blob_sha
                        or release_contract.content_sha256
                        != release_head_contract.content_sha256
                    ):
                        validation.errors.append(
                            "merge tree release contract differs from preparation head"
                        )
                    target_number = release_contract.contract.get("pr_number")
                    if type(target_number) is not int or target_number < 1:
                        validation.errors.append(
                            "prepared release contract target pr_number is invalid"
                        )
                    else:
                        target_pull = self.get_pull(target_number)
                        prepared = PreparedRelease(
                            preparation_pull=pr,
                            target_pull=target_pull,
                            contract=release_contract,
                        )
                        target_head = ((target_pull.get("head") or {}).get("sha") or "")
                        duplicates = [
                            item
                            for item in self.list_pulls(state="open")
                            if item.get("number") != target_number
                            and ((item.get("head") or {}).get("sha") or "")
                            == target_head
                        ]
                        release_config = policy["governance_contract"][
                            "release_preparation"
                        ]
                        if (
                            release_config["require_target_open"]
                            and target_pull.get("state") != "open"
                        ):
                            validation.errors.append(
                                "release target PR must remain open until the "
                                "preparation receipt is complete"
                            )
                        if (
                            release_config["require_target_draft"]
                            and target_pull.get("draft") is not True
                        ):
                            validation.errors.append(
                                "release target PR must remain draft until the "
                                "preparation receipt is complete"
                            )
                        try:
                            target_events = self.list_issue_events(target_number)
                            target_state_at_merge = _state_at_timestamp(
                                target_pull.get("state"),
                                target_events,
                                merged_at,
                                ISSUE_TRANSITIONS,
                                label=f"release target PR #{target_number} state",
                            )
                            target_draft_at_merge = _state_at_timestamp(
                                target_pull.get("draft"),
                                target_events,
                                merged_at,
                                DRAFT_TRANSITIONS,
                                label=f"release target PR #{target_number} draft state",
                            )
                        except RuntimeError as exc:
                            validation.errors.append(str(exc))
                        else:
                            if target_state_at_merge != "open":
                                validation.errors.append(
                                    "release target PR was not open when preparation merged"
                                )
                            if (
                                release_config["require_target_draft"]
                                and target_draft_at_merge is not True
                            ):
                                validation.errors.append(
                                    "release target PR was not draft when preparation merged"
                                )
                        try:
                            release_issue_records = self.issue_records_for_contract(
                                release_contract, merged_at, policy
                            )
                        except RuntimeError as exc:
                            validation.errors.append(str(exc))
                            release_issue_records = {}
                        release_validation = validate_prepared_release(
                            prepared,
                            changes,
                            policy,
                            issue_records=release_issue_records,
                            same_head_open_prs=duplicates,
                            expected_contract_ref=merge_commit_sha,
                            # Draft authority is reconstructed above from the
                            # complete event stream for every invocation.  A
                            # mutable current snapshot is never a substitute.
                            enforce_current_target_draft=False,
                            today=merged_at.date(),
                        )
                        validation.errors.extend(release_validation.errors)
                        validation.warnings.extend(release_validation.warnings)
        return FinalizationValidation(errors=validation.errors, contract=contract)


def _same_repository(pr: dict[str, Any], repository: str) -> bool:
    return (
        ((pr.get("head") or {}).get("repo") or {}).get("full_name", "").casefold()
        == repository.casefold()
    )


def _sunset_state(
    pr: dict[str, Any],
    contract: ContractEvidence,
    policy: dict[str, Any],
    today: date,
) -> tuple[str, str]:
    sunset = _date_value(str(contract.contract.get("branch_sunset", "")))
    if sunset is None:
        return "failure", "Branch-Sunset missing or invalid"
    if sunset < today:
        return "failure", f"Branch-Sunset expired on {sunset.isoformat()}"
    kind, _ = _branch_match((pr.get("head") or {}).get("ref", ""), policy)
    created = _date_value((pr.get("created_at") or "")[:10])
    if kind == "integration" and created:
        limit = created + timedelta(days=policy["branch_flow"]["integration_sunset_days"])
        if sunset > limit:
            return "failure", f"integration sunset exceeds {limit.isoformat()}"
    return "success", f"Branch-Sunset valid through {sunset.isoformat()}"


def supervise_open_pr(
    pr: dict[str, Any], api: GitHubApi, policy: dict[str, Any], today: date
) -> LifecycleResult:
    result = LifecycleResult()
    head = pr.get("head") or {}
    kind, _ = _branch_match(head.get("ref", ""), policy)
    if kind not in {"feature", "integration"}:
        return result
    if not _same_repository(pr, policy["repository"]):
        result.errors.append(
            f"PR #{pr.get('number')}: lifecycle refuses a non-base-repository head"
        )
        return result
    authority = api.get_open_contract(pr, policy)
    if authority.errors or authority.contract is None:
        state = "failure"
        description = "head-bound authority contract is missing or invalid"
        result.errors.extend(
            f"PR #{pr.get('number')}: contract validation: {error}"
            for error in authority.errors
        )
        if authority.contract is None and not authority.errors:
            result.errors.append(f"PR #{pr.get('number')}: contract evidence is missing")
    else:
        state, description = _sunset_state(pr, authority.contract, policy, today)
    api.set_status(
        head.get("sha", ""),
        state=state,
        context=policy["branch_lifecycle"]["sunset_status_context"],
        description=description,
    )
    result.actions.append(f"PR #{pr.get('number')}: sunset status={state}")
    if state != "success":
        result.errors.append(f"PR #{pr.get('number')}: {description}")
    return result


def _close_included_issues(
    pr: dict[str, Any],
    contract: ContractEvidence,
    api: GitHubApi,
    policy: dict[str, Any],
) -> LifecycleResult:
    result = LifecycleResult()
    included = contract.contract.get("included_issues")
    if (
        not isinstance(included, list)
        or not included
        or any(type(number) is not int or number < 1 for number in included)
        or included != sorted(set(included))
    ):
        result.errors.append(
            f"PR #{pr.get('number')}: immutable contract included_issues is invalid; none closed"
        )
        return result
    merged_at = _timestamp(pr.get("merged_at"))
    if merged_at is None:
        result.errors.append(f"PR #{pr.get('number')}: merged_at is invalid; none closed")
        return result

    def verified_completed_close(number: int) -> str | None:
        issue = api.get_issue(number)
        if issue.get("state") != "closed":
            return f"Issue #{number} is not closed after finalizer mutation"
        required_reason = policy["branch_lifecycle"]["completed_issue_state_reason"]
        if issue.get("state_reason") != required_reason:
            return (
                f"Issue #{number} closed state_reason is not {required_reason}; "
                "closure provenance is rejected"
            )
        transitions: list[tuple[datetime, int, dict[str, Any]]] = []
        seen_event_ids: set[int] = set()
        for event in api.list_issue_events(number):
            if event.get("event") not in ISSUE_TRANSITIONS:
                continue
            created_at = _timestamp(event.get("created_at"))
            event_id = event.get("id")
            if (
                created_at is None
                or created_at.utcoffset() is None
                or type(event_id) is not int
                or event_id < 1
                or event_id in seen_event_ids
            ):
                return f"Issue #{number} transition provenance is incomplete"
            seen_event_ids.add(event_id)
            transitions.append((created_at, event_id, event))
        if not transitions:
            return f"Issue #{number} has no close-event provenance"
        if any(
            event.get("event") == "reopened" and created_at >= merged_at
            for created_at, _event_id, event in transitions
        ):
            # Close/reopen is the explicit rollback signal.  If it raced with
            # this finalizer's PATCH, restore the open state before failing.
            api.reopen_issue(number)
            return (
                f"Issue #{number} was reopened after merge; rollback-open state restored"
            )
        closed_at, _event_id, latest = max(transitions)
        if latest.get("event") != "closed" or closed_at < merged_at:
            return (
                f"Issue #{number} latest close is not attributable to this merged Stage"
            )
        if not _trusted_actor(latest, policy):
            return f"Issue #{number} latest close actor is not the trusted finalizer"
        return None

    for number in included:
        issue = api.get_issue(number)
        if "pull_request" in issue:
            result.errors.append(f"PR #{pr.get('number')}: Included #{number} is a PR")
            continue
        if issue.get("state") == "open":
            # A post-merge reopen is an explicit rollback/supersession signal.
            # An old or partially failed finalizer must never close it again.
            post_merge_reopens = [
                event
                for event in api.list_issue_events(number)
                if event.get("event") == "reopened"
                and (_timestamp(event.get("created_at")) or merged_at) >= merged_at
            ]
            if post_merge_reopens:
                result.errors.append(
                    f"Issue #{number} was reopened after merge; old finalizer will not re-close it"
                )
                continue
            api.close_issue(number)
            disposition = "closed by develop Stage finalizer"
        elif issue.get("state") == "closed":
            disposition = "already closed with trusted completed provenance"
        else:
            result.errors.append(f"Issue #{number} has an invalid state")
            continue
        provenance_error = verified_completed_close(number)
        if provenance_error:
            result.errors.append(provenance_error)
        else:
            result.actions.append(f"Issue #{number}: {disposition}")
    return result


def _delete_merged_topic_branch(
    pr: dict[str, Any],
    api: GitHubApi,
    policy: dict[str, Any],
    open_heads: set[str],
) -> LifecycleResult:
    result = LifecycleResult()
    head = pr.get("head") or {}
    branch = head.get("ref", "")
    kind, _ = _branch_match(branch, policy)
    if kind not in {"feature", "integration"}:
        return result
    if not _same_repository(pr, policy["repository"]):
        result.errors.append(f"PR #{pr.get('number')}: merged topic head is not same-repository")
        return result
    if branch in open_heads:
        result.errors.append(
            f"PR #{pr.get('number')}: branch {branch} is reused by an open PR; not deleted"
        )
        return result
    ref = api.get_ref(branch)
    if ref is None:
        result.actions.append(f"branch {branch}: already absent")
        return result
    ref_sha = ((ref.get("object") or {}).get("sha"))
    if ref_sha != head.get("sha"):
        result.errors.append(
            f"PR #{pr.get('number')}: branch {branch} advanced after merge; not deleted"
        )
        return result
    api.delete_ref(branch)
    result.actions.append(f"branch {branch}: deleted after verified merge")
    return result


def _issue_snapshot_poison_error(
    pr: dict[str, Any], api: GitHubApi, policy: dict[str, Any]
) -> str | None:
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    records = api.issue_snapshot_invalidations(head_sha, policy)
    if not records:
        return None
    descriptions = sorted(
        {
            str(item.get("description") or ISSUE_INVALIDATION_DESCRIPTION_PREFIX)
            for item in records
        }
    )
    return (
        f"PR #{pr.get('number')}: original head {head_sha} has an irreversible "
        "Issue-snapshot poison status: " + "; ".join(descriptions)
    )


def _persist_finalization_conflict(
    pr: dict[str, Any],
    contract: ContractEvidence,
    api: GitHubApi,
    policy: dict[str, Any],
    detail: str,
) -> LifecycleResult:
    """Make a finalization race durable and restore Included Issues open."""

    result = LifecycleResult()
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    fingerprint = hashlib.sha256(detail.encode("utf-8")).hexdigest()[:12]
    description = (
        f"{ISSUE_INVALIDATION_DESCRIPTION_PREFIX} finalizer-race {fingerprint}; "
        "receipt nonterminal"
    )
    try:
        api.set_status(
            head_sha,
            state="failure",
            context=policy["release_approval"]["policy_status_context"],
            description=description,
        )
        result.actions.append(
            f"PR #{pr.get('number')}: durable finalization-conflict poison recorded"
        )
    except RuntimeError as exc:
        result.errors.append(
            f"PR #{pr.get('number')}: could not persist post-receipt poison: {exc}"
        )

    included = contract.contract.get("included_issues")
    if not isinstance(included, list):
        result.errors.append(
            f"PR #{pr.get('number')}: cannot restore Included Issues from invalid contract"
        )
        return result
    for number in included:
        if type(number) is not int or number < 1:
            result.errors.append(
                f"PR #{pr.get('number')}: cannot restore invalid Included Issue identity"
            )
            continue
        try:
            issue = api.get_issue(number)
            if issue.get("state") == "closed":
                api.reopen_issue(number)
                result.actions.append(
                    f"Issue #{number}: reopened after governance finalization conflict"
                )
        except RuntimeError as exc:
            result.errors.append(f"Issue #{number}: conflict restore failed: {exc}")
    return result


def finalize_merged_pr(
    pr: dict[str, Any],
    api: GitHubApi,
    policy: dict[str, Any],
    open_heads: set[str],
) -> LifecycleResult:
    result = LifecycleResult()
    branch = (pr.get("head") or {}).get("ref", "")
    kind, _ = _branch_match(branch, policy)
    if kind not in {"feature", "integration"}:
        return result
    if not pr.get("merged_at"):
        result.actions.append(
            f"PR #{pr.get('number')}: closed unmerged; branch retained and frozen"
        )
        return result
    base = (pr.get("base") or {}).get("ref")
    if base == policy["branch_flow"]["develop_branch"]:
        number = pr.get("number")
        merge_sha = pr.get("merge_commit_sha")
        if type(number) is not int or not isinstance(merge_sha, str):
            result.errors.append("merged governed PR identity is incomplete")
            return result
        try:
            api.verify_receipt_ruleset(policy)
            poison_error = _issue_snapshot_poison_error(pr, api, policy)
            if poison_error is not None:
                result.errors.append(poison_error)
                result.actions.append(f"PR #{number}: no Issue/ref mutation performed")
                return result
            if api.get_finalization_receipt(number, merge_sha, policy):
                result.actions.append(
                    f"PR #{number}: immutable finalization receipt already complete; skipped"
                )
                return result
        except RuntimeError as exc:
            result.errors.append(f"PR #{number}: receipt validation: {exc}")
            result.actions.append(f"PR #{number}: no Issue/ref mutation performed")
            return result
        finalization = api.validate_finalization(
            pr,
            policy,
        )
        if finalization.errors or finalization.contract is None:
            result.errors.extend(
                f"PR #{pr.get('number')}: finalizer validation: {error}"
                for error in finalization.errors
            )
            if finalization.contract is None and not finalization.errors:
                result.errors.append(
                    f"PR #{pr.get('number')}: finalizer validation returned no contract"
                )
            result.actions.append(
                f"PR #{pr.get('number')}: no Issue/ref mutation performed"
            )
            return result
        merged_at = _timestamp(pr.get("merged_at"))
        if merged_at is None:
            result.errors.append(f"PR #{number}: merged_at is invalid")
            return result
        try:
            # Close the validate->mutate gap with a fresh full-ledger read.
            api.verify_issue_scope_ledger(
                finalization.contract, merged_at, policy
            )
        except RuntimeError as exc:
            result.errors.append(f"PR #{number}: pre-mutation Issue ledger: {exc}")
            result.actions.append(f"PR #{number}: no Issue/ref mutation performed")
            return result
        issue_result = _close_included_issues(
            pr, finalization.contract, api, policy
        )
        result.actions.extend(issue_result.actions)
        result.errors.extend(issue_result.errors)
        if issue_result.errors:
            result.actions.append(
                f"PR #{pr.get('number')}: branch retained until Issue finalization succeeds"
            )
            return result
        branch_result = _delete_merged_topic_branch(pr, api, policy, open_heads)
        result.actions.extend(branch_result.actions)
        result.errors.extend(branch_result.errors)
        if branch_result.errors:
            result.actions.append(
                f"PR #{number}: receipt withheld until branch finalization succeeds"
            )
            return result
        try:
            # Re-read the authoritative Issue objects and complete ledgers.
            # Only this finalizer's exact Included close may have advanced.
            api.verify_issue_scope_ledger(
                finalization.contract, merged_at, policy
            )
            poison_error = _issue_snapshot_poison_error(pr, api, policy)
        except RuntimeError as exc:
            result.errors.append(
                f"PR #{number}: final Issue/status readback: {exc}"
            )
            conflict = _persist_finalization_conflict(
                pr, finalization.contract, api, policy, str(exc)
            )
            result.actions.extend(conflict.actions)
            result.errors.extend(conflict.errors)
            result.actions.append(
                f"PR #{number}: receipt withheld after final evidence read failed"
            )
            return result
        if poison_error is not None:
            result.errors.append(poison_error)
            conflict = _persist_finalization_conflict(
                pr, finalization.contract, api, policy, poison_error
            )
            result.actions.extend(conflict.actions)
            result.errors.extend(conflict.errors)
            result.actions.append(
                f"PR #{number}: receipt withheld after final Issue-snapshot poison read"
            )
            return result
        try:
            api.create_finalization_receipt(number, merge_sha, policy)
        except RuntimeError as exc:
            result.errors.append(f"PR #{number}: receipt creation: {exc}")
            return result
        try:
            # Receipt creation is not assumed atomic with Issue APIs.  A second
            # full-ledger/status read conflict-marks any race instead of
            # treating the receipt as terminal.
            api.verify_issue_scope_ledger(
                finalization.contract, merged_at, policy
            )
            poison_error = _issue_snapshot_poison_error(pr, api, policy)
        except RuntimeError as exc:
            detail = f"post-receipt Issue/status readback: {exc}"
            result.errors.append(f"PR #{number}: {detail}")
            conflict = _persist_finalization_conflict(
                pr, finalization.contract, api, policy, detail
            )
            result.actions.extend(conflict.actions)
            result.errors.extend(conflict.errors)
            return result
        if poison_error is not None:
            result.errors.append(
                poison_error
                + "; the newly created receipt is conflict-marked and cannot be terminal"
            )
            conflict = _persist_finalization_conflict(
                pr, finalization.contract, api, policy, poison_error
            )
            result.actions.extend(conflict.actions)
            result.errors.extend(conflict.errors)
            return result
        result.actions.append(
            f"PR #{number}: immutable finalization receipt created at {merge_sha}"
        )
    return result


def supervise(
    mode: str,
    event_name: str,
    payload: dict[str, Any],
    api: GitHubApi,
    policy: dict[str, Any],
    *,
    today: date | None = None,
) -> LifecycleResult:
    result = LifecycleResult()
    current_date = today or datetime.now(timezone.utc).date()
    develop = policy["branch_flow"]["develop_branch"]
    if mode not in {"audit", "finalize"}:
        result.errors.append(f"unsupported lifecycle mode: {mode}")
        return result
    if event_name == "pull_request_target":
        pr = payload.get("pull_request")
        if not isinstance(pr, dict):
            result.errors.append("pull_request_target payload has no pull_request object")
            return result
        number = pr.get("number")
        if not isinstance(number, int):
            result.errors.append("pull_request_target payload has no PR number")
            return result
        pr = api.get_pull(number)
        if mode == "finalize":
            if payload.get("action") != "closed":
                return result
            open_pulls = api.list_pulls(state="open", base=develop)
            open_heads = {
                (item.get("head") or {}).get("ref", "") for item in open_pulls
            }
            try:
                return finalize_merged_pr(
                    pr,
                    api,
                    policy,
                    open_heads,
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                result.errors.append(f"PR #{number}: isolated finalizer failure: {exc}")
                return result
        if payload.get("action") == "closed":
            return result
        return supervise_open_pr(pr, api, policy, current_date)

    if event_name not in {"schedule", "workflow_dispatch"}:
        result.errors.append(f"unsupported lifecycle event: {event_name}")
        return result

    open_pulls = api.list_pulls(state="open", base=develop)
    open_heads = {(item.get("head") or {}).get("ref", "") for item in open_pulls}
    if mode == "audit":
        for pr in open_pulls:
            number = pr.get("number", "?")
            try:
                item_result = supervise_open_pr(pr, api, policy, current_date)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                result.errors.append(f"PR #{number}: isolated audit failure: {exc}")
                continue
            result.actions.extend(item_result.actions)
            result.errors.extend(item_result.errors)
        return result

    if event_name == "schedule":
        result.errors.append(
            "scheduled finalization is forbidden; schedule is sunset-audit only"
        )
        return result

    inputs = payload.get("inputs")
    raw_number = inputs.get("pr_number") if isinstance(inputs, dict) else None
    try:
        number = int(raw_number)
    except (TypeError, ValueError):
        number = 0
    if number < 1 or str(number) != str(raw_number).strip():
        result.errors.append(
            "workflow_dispatch finalization requires one canonical positive pr_number"
        )
        return result
    try:
        pr = api.get_pull(number)
        return finalize_merged_pr(pr, api, policy, open_heads)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        result.errors.append(f"PR #{number}: isolated targeted retry failure: {exc}")
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--event", type=Path)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--mode", choices=("audit", "finalize"), required=True)
    args = parser.parse_args(argv)
    event_value = os.environ.get("GITHUB_EVENT_PATH", "")
    if args.event is None and not event_value:
        raise SystemExit("GITHUB_EVENT_PATH or --event is required")
    policy = load_json(args.policy.resolve())
    payload = load_json((args.event or Path(event_value)).resolve())
    try:
        api = GitHubApi(
            policy["repository"],
            os.environ.get("GITHUB_TOKEN", ""),
            policy["api_evidence"]["github_api"],
        )
        result = supervise(args.mode, args.event_name, payload, api, policy)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        result = LifecycleResult(errors=[f"lifecycle evidence/action failed: {exc}"])
    for action in result.actions:
        print(f"::notice::{action}")
    for error in result.errors:
        print(f"::error::{error}")
    if result.errors:
        return 1
    print("Branch lifecycle: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
