"""Validate Autofigure pull-request governance from a GitHub event.

The checker is standard-library only. It validates Epic/Stage identity, branch
topology and lifetime, scope/evidence bindings, issue closure, risk, independent
Bot authorship, rollback units, and explainable scope warnings.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fnmatch
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / ".github" / "governance-policy.json"
FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z-]+):\s*(.*?)\s*$", re.MULTILINE)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
ISSUE_RE = re.compile(r"#([1-9][0-9]*)")
HISTORICAL_OPEN_KEY = "_autofigure_open_at_merge"
ISSUE_LAST_EDITED_KEY = "_autofigure_last_edited_at"
ISSUE_MANAGED_EVENTS_KEY = "_autofigure_managed_events"
MANAGED_ISSUE_EVENTS = {
    "closed",
    "labeled",
    "renamed",
    "reopened",
    "transferred",
    "unlabeled",
}
METADATA_RE = re.compile(
    r"<!-- GOV-METADATA-START -->(.*?)<!-- GOV-METADATA-END -->", re.DOTALL
)
LOW_INFORMATION_RE = re.compile(
    r"\b(?:filler|placeholder|lorem|todo|tbd|none|n/?a)\b|"
    r"(?:待补|占位|废话|无意义|填充文字)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChangedPath:
    path: str
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class ChangeSet:
    paths: tuple[ChangedPath, ...]

    @property
    def files(self) -> list[str]:
        return [item.path for item in self.paths]


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContractEvidence:
    """A JSON governance contract read as inert data from one exact commit."""

    path: str
    source_ref: str
    blob_sha: str
    content_sha256: str
    contract: dict[str, Any]


@dataclass(frozen=True)
class PreparedRelease:
    """A release contract authored by one version-matched preparation Stage."""

    preparation_pull: dict[str, Any]
    target_pull: dict[str, Any]
    contract: ContractEvidence


CONTRACT_FIELDS = {
    "schema_version",
    "contract_kind",
    "repository",
    "pr_number",
    "base_ref",
    "base_sha",
    "head_ref",
    "pr_type",
    "risk_level",
    "epic",
    "stage",
    "included_issues",
    "deferred_issues",
    "issue_snapshots",
    "scope_freeze",
    "branch_sunset",
    "evidence_invalidation",
    "rollback_unit",
    "accountable_owner",
    "implementation_agent",
    "independent_pr_author",
    "workstream",
    "scientific_mode",
    "closure_state",
    "scope_threshold_justification",
    "release_preparation",
}
ISSUE_SNAPSHOT_FIELDS = {
    "number",
    "role",
    "node_id",
    "last_edited_at",
    "title_sha256",
    "body_sha256",
    "labels_sha256",
    "managed_event_cursor",
}
ISSUE_SNAPSHOT_ROLES = {"epic", "included", "deferred"}
ISSUE_INVALIDATION_DESCRIPTION_PREFIX = "Issue snapshot invalidated:"
CONTRACT_JUSTIFICATION_FIELDS = {
    "atomic_outcome",
    "shared_failure_mechanism",
    "shared_validation",
    "rollback_reason",
    "included_issues",
    "source_test_files",
    "non_generated_loc",
}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_strict_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number is forbidden: {value}")
        ),
    )


def load_json(path: Path) -> dict[str, Any]:
    value = strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def parse_metadata(body: str) -> dict[str, str]:
    matches = METADATA_RE.findall(body)
    if len(matches) != 1:
        return {}
    pairs = FIELD_RE.findall(matches[0])
    counts = Counter(key for key, _ in pairs)
    if any(count > 1 for count in counts.values()):
        return {}
    return {key: value.strip() for key, value in pairs}


def metadata_ambiguities(body: str) -> list[str]:
    blocks = METADATA_RE.findall(body)
    errors: list[str] = []
    if len(blocks) != 1:
        errors.append(f"expected exactly one GOV metadata block, found {len(blocks)}")
        return errors
    counts = Counter(key for key, _ in FIELD_RE.findall(blocks[0]))
    for key, count in sorted(counts.items()):
        if count > 1:
            errors.append(f"duplicate GOV metadata field: {key}")
    return errors


def sections(body: str) -> dict[str, str]:
    matches = list(HEADING_RE.finditer(body))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        heading = match.group(1).strip()
        if heading not in result:
            result[heading] = body[match.end() : end].strip()
    return result


def section_counts(body: str) -> Counter[str]:
    return Counter(match.group(1).strip() for match in HEADING_RE.finditer(body))


def substantive(text: str, minimum: int) -> bool:
    clean = COMMENT_RE.sub("", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return len(clean) >= minimum and "<" not in clean and ">" not in clean


def substantive_scope_field(text: str, minimum: int) -> bool:
    if not substantive(text, minimum):
        return False
    clean = COMMENT_RE.sub("", text).strip()
    if LOW_INFORMATION_RE.search(clean):
        return False
    units = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]", clean.casefold())
    return len(set(units)) >= 5


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def risk_floor(files: list[str], policy: dict[str, Any]) -> str:
    order = policy["risk_order"]
    required = order[0]
    for rule in policy["risk_rules"]:
        if any(_matches(path, rule["patterns"]) for path in files):
            if order.index(rule["level"]) > order.index(required):
                required = rule["level"]
    return required


def _parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        additions = int(parts[0]) if parts[0].isdigit() else 0
        deletions = int(parts[1]) if parts[1].isdigit() else 0
        result[parts[2].strip().replace("\\", "/")] = (additions, deletions)
    return result


def _github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise RuntimeError("GITHUB_TOKEN is required for GitHub API evidence")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "autofigure-pr-governance",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _github_post_json(url: str, data: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(data, ensure_ascii=True).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def derive_contract_path(
    pull: dict[str, Any], changed_files: list[str], policy: dict[str, Any]
) -> str:
    """Derive the sole authority path without trusting editable PR prose."""

    config = policy["governance_contract"]
    base = ((pull.get("base") or {}).get("ref") or "")
    head = ((pull.get("head") or {}).get("ref") or "")
    kind, match = _branch_match(head, policy)
    if base == policy["branch_flow"]["develop_branch"] and kind in {
        "feature",
        "integration",
    }:
        assert match is not None
        return config["stage_path_template"].format(
            stage=match.group("stage"), version=match.group("version")
        )
    if (
        base == policy["branch_flow"]["release_branch"]
        and head == policy["branch_flow"]["main_head"]
    ):
        matches = sorted(
            {
                path
                for path in changed_files
                if re.fullmatch(config["release_path_regex"], path)
            }
        )
        if len(matches) != 1:
            raise ValueError(
                "release PR must change exactly one versioned release contract path"
            )
        return matches[0]
    raise ValueError("cannot derive governance contract path for unsupported PR topology")


def _decode_base64(value: Any, *, label: str, maximum: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} content is not base64 text")
    try:
        raw = base64.b64decode("".join(value.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{label} content is invalid base64") from exc
    if len(raw) > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes")
    return raw


def decode_contract_evidence(
    *,
    path: str,
    source_ref: str,
    contents_value: Any,
    blob_value: Any,
    policy: dict[str, Any],
) -> ContractEvidence:
    """Decode and cross-check Contents and Git Blobs API responses as inert JSON."""

    maximum = int(policy["governance_contract"]["max_bytes"])
    if not isinstance(contents_value, dict) or contents_value.get("type") != "file":
        raise ValueError(f"authority contract is not a file at {source_ref}:{path}")
    if contents_value.get("path") != path or contents_value.get("encoding") != "base64":
        raise ValueError("Contents API contract path or encoding is invalid")
    blob_sha = contents_value.get("sha")
    if not isinstance(blob_sha, str) or re.fullmatch(r"[0-9a-f]{40}", blob_sha) is None:
        raise ValueError("Contents API contract blob SHA is invalid")
    contents_raw = _decode_base64(
        contents_value.get("content"), label="Contents API contract", maximum=maximum
    )
    if (
        not isinstance(blob_value, dict)
        or blob_value.get("sha") != blob_sha
        or blob_value.get("encoding") != "base64"
    ):
        raise ValueError("Git Blobs API contract identity or encoding is invalid")
    blob_raw = _decode_base64(
        blob_value.get("content"), label="Git Blobs API contract", maximum=maximum
    )
    if contents_raw != blob_raw:
        raise ValueError("Contents and Git Blobs API contract bytes disagree")
    git_blob_sha = hashlib.sha1(
        f"blob {len(contents_raw)}\0".encode("ascii") + contents_raw,
        usedforsecurity=False,
    ).hexdigest()
    if git_blob_sha != blob_sha:
        raise ValueError("authority contract bytes do not match the declared Git blob SHA")
    try:
        text_value = contents_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("authority contract is not UTF-8") from exc
    value = strict_json_loads(text_value)
    if not isinstance(value, dict):
        raise ValueError("authority contract root must be a JSON object")
    return ContractEvidence(
        path=path,
        source_ref=source_ref,
        blob_sha=blob_sha,
        content_sha256=hashlib.sha256(contents_raw).hexdigest(),
        contract=value,
    )


def fetch_contract_path_evidence(
    path: str, source_ref: str, policy: dict[str, Any]
) -> ContractEvidence:
    if re.fullmatch(r"[0-9a-f]{40}", source_ref) is None:
        raise ValueError("contract source ref must be an exact 40-character SHA")
    repository = policy["repository"]
    endpoint = policy["api_evidence"]["github_api"]
    encoded_path = urllib.parse.quote(path, safe="/")
    query = urllib.parse.urlencode({"ref": source_ref})
    contents = _github_get_json(
        f"{endpoint}/repos/{repository}/contents/{encoded_path}?{query}"
    )
    if not isinstance(contents, dict) or not isinstance(contents.get("sha"), str):
        raise ValueError("Contents API contract response is invalid")
    blob_sha = contents["sha"]
    blob = _github_get_json(f"{endpoint}/repos/{repository}/git/blobs/{blob_sha}")
    return decode_contract_evidence(
        path=path,
        source_ref=source_ref,
        contents_value=contents,
        blob_value=blob,
        policy=policy,
    )


def fetch_contract_evidence(
    pull: dict[str, Any], changes: ChangeSet, policy: dict[str, Any]
) -> ContractEvidence:
    path = derive_contract_path(pull, changes.files, policy)
    source_ref = ((pull.get("head") or {}).get("sha") or "")
    return fetch_contract_path_evidence(path, source_ref, policy)


def release_contract_paths(changes: ChangeSet, policy: dict[str, Any]) -> list[str]:
    prefix = policy["governance_contract"]["release_directory"] + "/"
    return sorted(path for path in changes.files if path.startswith(prefix))


def is_release_preparation_pull(
    pull: dict[str, Any], policy: dict[str, Any]
) -> bool:
    config = policy["governance_contract"]["release_preparation"]
    kind, match = _branch_match(str((pull.get("head") or {}).get("ref", "")), policy)
    return (
        config["state"] == "active"
        and kind == "feature"
        and match is not None
        and match.group("stage") == config["stage_slug"]
    )


def fetch_prepared_release(
    preparation_pull: dict[str, Any],
    changes: ChangeSet,
    policy: dict[str, Any],
) -> PreparedRelease | None:
    paths = release_contract_paths(changes, policy)
    if not paths or not is_release_preparation_pull(preparation_pull, policy):
        return None
    if len(paths) != 1:
        raise ValueError("release preparation Stage must change exactly one release contract")
    source_ref = ((preparation_pull.get("head") or {}).get("sha") or "")
    contract = fetch_contract_path_evidence(paths[0], source_ref, policy)
    target_number = contract.contract.get("pr_number")
    if type(target_number) is not int or target_number < 1:
        raise ValueError("prepared release contract has no valid target pr_number")
    repository = policy["repository"]
    endpoint = policy["api_evidence"]["github_api"]
    target_pull = _github_get_json(
        f"{endpoint}/repos/{repository}/pulls/{target_number}"
    )
    if not isinstance(target_pull, dict) or target_pull.get("number") != target_number:
        raise ValueError("prepared release target PR response is invalid")
    return PreparedRelease(
        preparation_pull=preparation_pull,
        target_pull=target_pull,
        contract=contract,
    )


def _pull_files(payload: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    pull = payload["pull_request"]
    number = pull.get("number")
    if not isinstance(number, int) or number < 1:
        raise RuntimeError("pull_request.number is required for API diff collection")
    repository = policy["repository"]
    endpoint = policy["api_evidence"]["github_api"]
    page_size = policy["api_evidence"]["page_size"]
    maximum = policy["api_evidence"]["pull_files_max"]
    result: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": page_size, "page": page})
        url = f"{endpoint}/repos/{repository}/pulls/{number}/files?{query}"
        value = _github_get_json(url)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise RuntimeError("GitHub pull-files response was not a list of objects")
        result.extend(value)
        if len(result) >= maximum:
            raise RuntimeError(
                f"pull-files evidence reached GitHub's {maximum}-file limit; split the Stage"
            )
        if len(value) < page_size:
            return result
        page += 1


def refresh_pull_payload(
    payload: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    event_pull = payload.get("pull_request")
    if not isinstance(event_pull, dict) or not isinstance(event_pull.get("number"), int):
        raise RuntimeError("event pull_request.number is required")
    number = event_pull["number"]
    endpoint = policy["api_evidence"]["github_api"]
    repository = policy["repository"]
    current = _github_get_json(f"{endpoint}/repos/{repository}/pulls/{number}")
    if not isinstance(current, dict) or current.get("number") != number:
        raise RuntimeError("current GitHub pull-request response is invalid")
    refreshed = dict(payload)
    refreshed["pull_request"] = current
    return refreshed


def collect_changes(payload: dict[str, Any], policy: dict[str, Any]) -> ChangeSet:
    files_override = os.environ.get("GOVERNANCE_CHANGED_FILES")
    numstat_override = os.environ.get("GOVERNANCE_CHANGED_NUMSTAT")
    if (files_override is None) != (numstat_override is None):
        raise ValueError(
            "GOVERNANCE_CHANGED_FILES and GOVERNANCE_CHANGED_NUMSTAT are test-only "
            "overrides and must be supplied together"
        )
    if files_override is not None and numstat_override is not None:
        files = [line.strip().replace("\\", "/") for line in files_override.splitlines()]
        files = [path for path in files if path]
        stats = _parse_numstat(numstat_override)
        ordered = list(dict.fromkeys([*files, *stats.keys()]))
        return ChangeSet(
            tuple(
                ChangedPath(path, stats.get(path, (0, 0))[0], stats.get(path, (0, 0))[1])
                for path in ordered
            )
        )

    api_files = _pull_files(payload, policy)
    paths: list[ChangedPath] = []
    seen: set[str] = set()
    for item in api_files:
        filename = str(item.get("filename", "")).strip().replace("\\", "/")
        if not filename:
            raise RuntimeError("GitHub pull-files item has no filename")
        if filename not in seen:
            paths.append(
                ChangedPath(
                    filename,
                    int(item.get("additions") or 0),
                    int(item.get("deletions") or 0),
                )
            )
            seen.add(filename)
        previous = str(item.get("previous_filename", "")).strip().replace("\\", "/")
        if previous and previous not in seen:
            paths.append(ChangedPath(previous))
            seen.add(previous)
    return ChangeSet(
        tuple(paths)
    )


def _normalized_login(value: str) -> str:
    return value.strip().lstrip("@").casefold()


def _issue_numbers(value: str) -> set[int]:
    return {int(number) for number in ISSUE_RE.findall(value)}


def _label_names(issue: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for label in issue.get("labels") or []:
        if isinstance(label, str):
            names.add(label.casefold())
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"].casefold())
    return names


def _parent_epic_number(body: str, heading: str) -> int | None:
    pattern = re.compile(
        rf"(?m)^###\s+{re.escape(heading)}\s*$\r?\n+\s*#([1-9][0-9]*)\s*$"
    )
    matches = pattern.findall(body)
    return int(matches[0]) if len(matches) == 1 else None


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _labels_sha256(issue: dict[str, Any]) -> str:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise ValueError("Issue labels API evidence is not an array")
    names: list[str] = []
    for label in labels:
        name = label if isinstance(label, str) else (
            label.get("name") if isinstance(label, dict) else None
        )
        if not isinstance(name, str) or not name:
            raise ValueError("Issue label name is missing or invalid")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("Issue labels API evidence contains duplicate names")
    canonical = json.dumps(
        sorted(names, key=lambda item: (item.casefold(), item)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _text_sha256(canonical)


def managed_issue_events(issue: dict[str, Any]) -> list[dict[str, Any]]:
    value = issue.get(ISSUE_MANAGED_EVENTS_KEY)
    if not isinstance(value, list):
        raise ValueError("complete managed Issue event ledger is missing")
    parsed: list[tuple[datetime, int, dict[str, Any]]] = []
    seen_ids: set[int] = set()
    for event in value:
        if not isinstance(event, dict) or event.get("event") not in MANAGED_ISSUE_EVENTS:
            continue
        event_id = event.get("id")
        created_at = _timestamp(event.get("created_at"))
        if (
            type(event_id) is not int
            or event_id < 1
            or event_id in seen_ids
            or created_at is None
            or created_at.utcoffset() is None
        ):
            raise ValueError("managed Issue event ledger is incomplete or invalid")
        seen_ids.add(event_id)
        parsed.append((created_at, event_id, event))
    return [event for _created_at, _event_id, event in sorted(parsed)]


def managed_event_cursor(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    event = events[-1]
    return {
        "id": event["id"],
        "event": event["event"],
        "created_at": event["created_at"],
    }


def managed_events_after_cursor(
    issue: dict[str, Any], cursor: dict[str, Any] | None
) -> list[dict[str, Any]]:
    events = managed_issue_events(issue)
    if cursor is None:
        return events
    for index, event in enumerate(events):
        if (
            event.get("id") == cursor.get("id")
            and event.get("event") == cursor.get("event")
            and event.get("created_at") == cursor.get("created_at")
        ):
            return events[index + 1 :]
    raise ValueError("managed Issue event cursor is absent from the complete ledger")


def _expected_issue_snapshot_roles(contract: dict[str, Any]) -> dict[int, str] | None:
    """Return the exact Issue-number/role set implied by authority fields."""

    included = contract.get("included_issues")
    deferred = contract.get("deferred_issues")
    if (
        not isinstance(included, list)
        or not isinstance(deferred, list)
        or any(type(number) is not int or number < 1 for number in [*included, *deferred])
    ):
        return None
    pairs: list[tuple[int, str]] = [
        *((number, "included") for number in included),
        *((number, "deferred") for number in deferred),
    ]
    if contract.get("contract_kind") == "stage":
        epic = contract.get("epic")
        if not isinstance(epic, str) or re.fullmatch(r"#[1-9][0-9]*", epic) is None:
            return None
        pairs.append((int(epic[1:]), "epic"))
    elif contract.get("contract_kind") != "release":
        return None
    roles: dict[int, str] = {}
    for number, role in pairs:
        if number in roles:
            return None
        roles[number] = role
    return roles


def issue_snapshot_errors(contract: dict[str, Any]) -> list[str]:
    """Validate immutable Issue title/body bindings without API state."""

    snapshots = contract.get("issue_snapshots")
    if not isinstance(snapshots, list):
        return ["authority contract issue_snapshots must be an array"]
    errors: list[str] = []
    actual_roles: dict[int, str] = {}
    order: list[int] = []
    for index, snapshot in enumerate(snapshots):
        label = f"authority contract issue_snapshots[{index}]"
        if not isinstance(snapshot, dict) or set(snapshot) != ISSUE_SNAPSHOT_FIELDS:
            errors.append(f"{label} does not contain the exact Issue snapshot fields")
            continue
        number = snapshot.get("number")
        role = snapshot.get("role")
        node_id = snapshot.get("node_id")
        last_edited_at = snapshot.get("last_edited_at")
        title_sha = snapshot.get("title_sha256")
        body_sha = snapshot.get("body_sha256")
        labels_sha = snapshot.get("labels_sha256")
        cursor = snapshot.get("managed_event_cursor")
        if type(number) is not int or number < 1:
            errors.append(f"{label}.number must be a positive integer")
            continue
        order.append(number)
        if number in actual_roles:
            errors.append(f"authority contract issue_snapshots repeats Issue #{number}")
        elif role not in ISSUE_SNAPSHOT_ROLES:
            errors.append(f"{label}.role is invalid")
        else:
            actual_roles[number] = str(role)
        if not isinstance(node_id, str) or not node_id or node_id.strip() != node_id:
            errors.append(f"{label}.node_id must be one non-empty exact Node ID")
        if last_edited_at is not None and (
            not isinstance(last_edited_at, str)
            or (parsed_edit := _timestamp(last_edited_at)) is None
            or parsed_edit.utcoffset() is None
        ):
            errors.append(f"{label}.last_edited_at must be null or a timezone-aware timestamp")
        for hash_field, value in (
            ("title_sha256", title_sha),
            ("body_sha256", body_sha),
            ("labels_sha256", labels_sha),
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                errors.append(f"{label}.{hash_field} must be a lowercase SHA-256")
        if cursor is not None:
            cursor_valid = (
                isinstance(cursor, dict)
                and set(cursor) == {"id", "event", "created_at"}
                and type(cursor.get("id")) is int
                and cursor.get("id", 0) > 0
                and cursor.get("event") in MANAGED_ISSUE_EVENTS
                and isinstance(cursor.get("created_at"), str)
            )
            parsed_cursor = (
                _timestamp(cursor.get("created_at")) if cursor_valid else None
            )
            if (
                not cursor_valid
                or parsed_cursor is None
                or parsed_cursor.utcoffset() is None
            ):
                errors.append(f"{label}.managed_event_cursor is invalid")
    if order != sorted(order):
        errors.append("authority contract issue_snapshots must be sorted by Issue number")
    expected_roles = _expected_issue_snapshot_roles(contract)
    if expected_roles is None:
        errors.append(
            "authority contract Issue roles are ambiguous or overlap across Epic/Included/Deferred"
        )
    elif actual_roles != expected_roles:
        errors.append(
            "authority contract issue_snapshots must exactly bind every Epic/Included/Deferred Issue role"
        )
    return errors


def _issue_snapshot_map(contract: dict[str, Any]) -> dict[int, dict[str, Any]]:
    if issue_snapshot_errors(contract):
        return {}
    return {
        int(snapshot["number"]): snapshot
        for snapshot in contract["issue_snapshots"]
    }


def build_issue_snapshots(
    contract: dict[str, Any], issue_records: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build deterministic exact-content bindings for a new contract revision."""

    roles = _expected_issue_snapshot_roles(contract)
    if roles is None:
        raise ValueError("cannot build snapshots from ambiguous Issue roles")
    snapshots: list[dict[str, Any]] = []
    for number, role in sorted(roles.items()):
        issue = issue_records.get(number)
        if not isinstance(issue, dict) or not isinstance(issue.get("title"), str):
            raise ValueError(f"cannot build snapshot without Issue #{number} title")
        body = issue.get("body")
        if body is not None and not isinstance(body, str):
            raise ValueError(f"cannot build snapshot from malformed Issue #{number} body")
        node_id = issue.get("node_id")
        last_edited_at = issue.get(ISSUE_LAST_EDITED_KEY)
        if not isinstance(node_id, str) or not node_id:
            raise ValueError(f"cannot build snapshot without Issue #{number} node_id")
        if last_edited_at is not None and not isinstance(last_edited_at, str):
            raise ValueError(f"cannot build snapshot from invalid Issue #{number} edit time")
        events = managed_issue_events(issue)
        snapshots.append(
            {
                "number": number,
                "role": role,
                "node_id": node_id,
                "last_edited_at": last_edited_at,
                "title_sha256": _text_sha256(issue["title"]),
                "body_sha256": _text_sha256(body or ""),
                "labels_sha256": _labels_sha256(issue),
                "managed_event_cursor": managed_event_cursor(events),
            }
        )
    return snapshots


def _validate_issue_snapshot_record(
    result: ValidationResult,
    number: int,
    role: str,
    issue: dict[str, Any],
    snapshots: dict[int, dict[str, Any]],
) -> None:
    snapshot = snapshots.get(number)
    if snapshot is None or snapshot.get("role") != role:
        result.errors.append(f"Issue #{number} has no exact {role} snapshot binding")
        return
    title = issue.get("title")
    body = issue.get("body")
    if not isinstance(title, str) or (body is not None and not isinstance(body, str)):
        result.errors.append(f"Issue #{number} title/body API evidence is malformed")
        return
    if snapshot["title_sha256"] != _text_sha256(title):
        result.errors.append(f"Issue #{number} title changed after the authority snapshot")
    if snapshot["body_sha256"] != _text_sha256(body or ""):
        result.errors.append(f"Issue #{number} body changed after the authority snapshot")
    if issue.get("node_id") != snapshot["node_id"]:
        result.errors.append(f"Issue #{number} Node ID/repository identity changed")
    if ISSUE_LAST_EDITED_KEY not in issue:
        result.errors.append(f"Issue #{number} GraphQL lastEditedAt evidence is missing")
    elif issue.get(ISSUE_LAST_EDITED_KEY) != snapshot["last_edited_at"]:
        result.errors.append(
            f"Issue #{number} was edited after the authority snapshot, even if text was restored"
        )
    try:
        labels_sha = _labels_sha256(issue)
    except ValueError as exc:
        result.errors.append(f"Issue #{number} label evidence is invalid: {exc}")
    else:
        if labels_sha != snapshot["labels_sha256"]:
            result.errors.append(f"Issue #{number} labels changed after the authority snapshot")
    try:
        cursor = managed_event_cursor(managed_issue_events(issue))
    except ValueError as exc:
        result.errors.append(f"Issue #{number} managed event evidence is invalid: {exc}")
    else:
        if cursor != snapshot["managed_event_cursor"]:
            result.errors.append(
                f"Issue #{number} managed event ledger advanced after the authority snapshot"
            )


def collect_issue_records(
    metadata: dict[str, str], policy: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    issue_numbers = _issue_numbers(metadata.get("Included-Issues", ""))
    deferred_value = metadata.get("Deferred-Issues", "")
    if deferred_value != "none":
        issue_numbers.update(_issue_numbers(deferred_value))
    epic_value = metadata.get("Epic", "")
    if re.fullmatch(policy["metadata"]["epic_regex"], epic_value):
        issue_numbers.add(int(epic_value[1:]))
    repository = policy["repository"]
    endpoint = policy["api_evidence"]["github_api"]
    graphql_endpoint = policy["api_evidence"].get("github_graphql")
    if not isinstance(graphql_endpoint, str) or not graphql_endpoint:
        raise RuntimeError("GitHub GraphQL endpoint is missing from trusted policy")
    owner, name = repository.split("/", 1)
    page_size = int(policy["api_evidence"]["page_size"])
    max_pages = int(policy["branch_history"]["max_pages"])
    result: dict[int, dict[str, Any]] = {}
    for number in sorted(issue_numbers):
        value = _github_get_json(f"{endpoint}/repos/{repository}/issues/{number}")
        if not isinstance(value, dict):
            raise RuntimeError(f"GitHub Issue #{number} response was not an object")
        graph = _github_post_json(
            graphql_endpoint,
            {
                "query": (
                    "query($owner:String!,$name:String!,$number:Int!){"
                    "repository(owner:$owner,name:$name){issue(number:$number){"
                    "id number lastEditedAt}}}"
                ),
                "variables": {"owner": owner, "name": name, "number": number},
            },
        )
        graph_issue = (
            ((graph.get("data") or {}).get("repository") or {}).get("issue")
            if isinstance(graph, dict) and not graph.get("errors")
            else None
        )
        if (
            not isinstance(graph_issue, dict)
            or graph_issue.get("number") != number
            or graph_issue.get("id") != value.get("node_id")
            or (
                graph_issue.get("lastEditedAt") is not None
                and not isinstance(graph_issue.get("lastEditedAt"), str)
            )
        ):
            raise RuntimeError(f"GitHub Issue #{number} GraphQL identity is invalid")
        events: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            query = urllib.parse.urlencode(
                {"per_page": page_size, "page": page}
            )
            page_value = _github_get_json(
                f"{endpoint}/repos/{repository}/issues/{number}/events?{query}"
            )
            if not isinstance(page_value, list) or not all(
                isinstance(item, dict) for item in page_value
            ):
                raise RuntimeError(
                    f"GitHub Issue #{number} event ledger response is invalid"
                )
            events.extend(page_value)
            if len(page_value) < page_size:
                break
        else:
            raise RuntimeError(
                f"GitHub Issue #{number} event ledger pagination limit was reached"
            )
        value[ISSUE_LAST_EDITED_KEY] = graph_issue.get("lastEditedAt")
        value[ISSUE_MANAGED_EVENTS_KEY] = events
        result[number] = value
    return result


def collect_release_preparation_record(
    evidence: ContractEvidence, policy: dict[str, Any]
) -> dict[str, Any] | None:
    if evidence.contract.get("contract_kind") != "release":
        return None
    binding = evidence.contract.get("release_preparation")
    if not isinstance(binding, dict) or type(binding.get("pr_number")) is not int:
        return None
    number = binding["pr_number"]
    repository = policy["repository"]
    endpoint = policy["api_evidence"]["github_api"]
    value = _github_get_json(f"{endpoint}/repos/{repository}/pulls/{number}")
    if not isinstance(value, dict) or value.get("number") != number:
        raise RuntimeError(f"release preparation PR #{number} response is invalid")
    return value


def _validate_issue_contracts(
    result: ValidationResult,
    metadata: dict[str, str],
    included_issues: set[int],
    deferred_issues: set[int],
    branch_kind: str | None,
    policy: dict[str, Any],
    issue_records: dict[int, dict[str, Any]] | None,
    finalization_merged_at: datetime | None,
    authority_contract: dict[str, Any] | None,
) -> None:
    if issue_records is None:
        result.errors.append("GitHub Issue API evidence is missing")
        return
    snapshots = _issue_snapshot_map(authority_contract or {})
    contract = policy["issue_contract"]
    epic_value = metadata.get("Epic", "")
    epic_number = int(epic_value[1:]) if re.fullmatch(
        policy["metadata"]["epic_regex"], epic_value
    ) else None
    if branch_kind in {"feature", "integration"} and epic_number is not None:
        epic = issue_records.get(epic_number)
        if epic is None:
            result.errors.append(f"Epic Issue #{epic_number} API evidence is missing")
        else:
            _validate_issue_snapshot_record(
                result, epic_number, "epic", epic, snapshots
            )
            if "pull_request" in epic:
                result.errors.append(f"Epic #{epic_number} resolves to a pull request")
            if contract["epic_label"].casefold() not in _label_names(epic):
                result.errors.append(
                    f"Epic #{epic_number} must have label {contract['epic_label']}"
                )
            epic_open_at_merge = _issue_was_open_at_merge(epic, finalization_merged_at)
            if contract["require_open_epic_for_stage"] and not epic_open_at_merge:
                result.errors.append(f"Epic #{epic_number} must be open for a Stage PR")

    for number in sorted(included_issues):
        issue = issue_records.get(number)
        if issue is None:
            result.errors.append(f"Included Issue #{number} API evidence is missing")
            continue
        if "pull_request" in issue:
            result.errors.append(f"Included #{number} resolves to a pull request")
            continue
        _validate_issue_snapshot_record(
            result, number, "included", issue, snapshots
        )
        if branch_kind in {"feature", "integration"}:
            issue_open_at_merge = _issue_was_open_at_merge(issue, finalization_merged_at)
            if contract["require_open_included_issue_for_stage"] and not issue_open_at_merge:
                result.errors.append(f"Included Issue #{number} must be open before merge")
            parent = _parent_epic_number(
                issue.get("body") or "", contract["parent_epic_heading"]
            )
            if epic_number is not None and parent != epic_number:
                result.errors.append(
                    f"Included Issue #{number} must declare Parent Epic #{epic_number}"
                )

    for number in sorted(deferred_issues):
        issue = issue_records.get(number)
        if issue is None:
            result.errors.append(f"Deferred Issue #{number} API evidence is missing")
            continue
        if "pull_request" in issue:
            result.errors.append(f"Deferred #{number} resolves to a pull request")
            continue
        _validate_issue_snapshot_record(
            result, number, "deferred", issue, snapshots
        )
        if branch_kind in {"feature", "integration"}:
            issue_open_at_merge = _issue_was_open_at_merge(issue, finalization_merged_at)
            if contract["require_open_deferred_issue_for_stage"] and not issue_open_at_merge:
                result.errors.append(f"Deferred Issue #{number} must be open for a Stage PR")
            parent = _parent_epic_number(
                issue.get("body") or "", contract["parent_epic_heading"]
            )
            if epic_number is not None and parent != epic_number:
                result.errors.append(
                    f"Deferred Issue #{number} must declare Parent Epic #{epic_number}"
                )


def _metadata_sha(value: str) -> str | None:
    if "@" not in value:
        return None
    candidate = value.rsplit("@", 1)[1]
    return candidate if re.fullmatch(r"[0-9a-f]{40}", candidate) else None


def _date_value(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _issue_was_open_at_merge(
    issue: dict[str, Any], finalization_merged_at: datetime | None
) -> bool:
    if finalization_merged_at is None:
        return issue.get("state") == "open"
    # A current state/closed_at snapshot cannot reconstruct state at merge after
    # later close/reopen cycles.  The trusted lifecycle finalizer must inject
    # this value from the complete, paginated Issue event history.  Missing or
    # malformed historical evidence therefore fails closed.
    return issue.get(HISTORICAL_OPEN_KEY) is True


def _branch_match(
    head: str, policy: dict[str, Any]
) -> tuple[str | None, re.Match[str] | None]:
    flow = policy["branch_flow"]
    integration = re.fullmatch(flow["integration_head_regex"], head)
    if integration:
        return "integration", integration
    feature = re.fullmatch(flow["feature_head_regex"], head)
    if feature:
        return "feature", feature
    return None, None


def scope_freeze_is_ancestor(
    payload: dict[str, Any], metadata: dict[str, str], policy: dict[str, Any]
) -> bool | None:
    scope_sha = _metadata_sha(metadata.get("Scope-Freeze", ""))
    if scope_sha is None:
        return None
    pull = payload["pull_request"]
    head_sha = pull["head"]["sha"]
    override = os.environ.get("GOVERNANCE_SCOPE_FREEZE_VALID")
    if override is not None:
        if override not in {"true", "false"}:
            raise ValueError("GOVERNANCE_SCOPE_FREEZE_VALID must be true or false")
        return override == "true"
    if pull["base"]["ref"] == policy["branch_flow"]["release_branch"]:
        return scope_sha == head_sha
    repository = policy["repository"]
    endpoint = policy["api_evidence"]["github_api"]
    base_sha = pull["base"]["sha"]
    comparison = urllib.parse.quote(f"{base_sha}...{head_sha}", safe=".")
    value = _github_get_json(f"{endpoint}/repos/{repository}/compare/{comparison}")
    if not isinstance(value, dict):
        raise RuntimeError("GitHub compare response was not an object")
    merge_base = value.get("merge_base_commit")
    if not isinstance(merge_base, dict) or not isinstance(merge_base.get("sha"), str):
        raise RuntimeError("GitHub compare response has no merge_base_commit.sha")
    return scope_sha == merge_base["sha"]


def prior_branch_prs(
    payload: dict[str, Any], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    pull = payload["pull_request"]
    branch_kind, _ = _branch_match(pull["head"]["ref"], policy)
    if branch_kind is None:
        return []

    override = os.environ.get("GOVERNANCE_PRIOR_BRANCH_PRS")
    if override is not None:
        value = json.loads(override)
        if not isinstance(value, list):
            raise ValueError("GOVERNANCE_PRIOR_BRANCH_PRS must be a JSON list")
        return [item for item in value if isinstance(item, dict)]

    repository = policy["repository"]
    owner = pull["head"]["repo"]["full_name"].split("/", 1)[0]
    history = policy["branch_history"]
    value: list[dict[str, Any]] = []
    for page in range(1, history["max_pages"] + 1):
        query = urllib.parse.urlencode(
            {
                "state": "all",
                "head": f"{owner}:{pull['head']['ref']}",
                "per_page": history["page_size"],
                "page": page,
            }
        )
        url = f"{history['github_api']}/repos/{repository}/pulls?{query}"
        page_value = _github_get_json(url)
        if not isinstance(page_value, list) or not all(
            isinstance(item, dict) for item in page_value
        ):
            raise RuntimeError("GitHub branch-history response was not a list of objects")
        value.extend(page_value)
        if len(page_value) < history["page_size"]:
            break
    else:
        raise RuntimeError("GitHub branch-history pagination limit was reached")
    current = pull.get("number")
    return [item for item in value if item.get("number") != current]


def open_prs_sharing_head(
    payload: dict[str, Any], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return every other open PR sharing this commit-status identity."""

    override = os.environ.get("GOVERNANCE_OPEN_PRS_SAME_HEAD")
    if override is not None:
        value = strict_json_loads(override)
        if not isinstance(value, list):
            raise ValueError("GOVERNANCE_OPEN_PRS_SAME_HEAD must be a JSON list")
        return [item for item in value if isinstance(item, dict)]

    pull = payload["pull_request"]
    current = pull.get("number")
    head_sha = ((pull.get("head") or {}).get("sha") or "")
    repository = policy["repository"]
    history = policy["branch_history"]
    result: list[dict[str, Any]] = []
    for page in range(1, history["max_pages"] + 1):
        query = urllib.parse.urlencode(
            {
                "state": "open",
                "per_page": history["page_size"],
                "page": page,
            }
        )
        page_value = _github_get_json(
            f"{history['github_api']}/repos/{repository}/pulls?{query}"
        )
        if not isinstance(page_value, list) or not all(
            isinstance(item, dict) for item in page_value
        ):
            raise RuntimeError("GitHub open-PR response was not a list of objects")
        result.extend(
            item
            for item in page_value
            if item.get("number") != current
            and ((item.get("head") or {}).get("sha") or "") == head_sha
        )
        if len(page_value) < history["page_size"]:
            break
    else:
        raise RuntimeError("GitHub open-PR pagination limit was reached")
    return result


def issue_snapshot_invalidations(
    pull: dict[str, Any], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Find trusted Issue-event poison pills in this exact head's status history."""

    override = os.environ.get("GOVERNANCE_ISSUE_SNAPSHOT_INVALIDATIONS")
    if override is not None:
        value = strict_json_loads(override)
        if not isinstance(value, list):
            raise ValueError("GOVERNANCE_ISSUE_SNAPSHOT_INVALIDATIONS must be a JSON list")
        return [item for item in value if isinstance(item, dict)]
    head_sha = str((pull.get("head") or {}).get("sha") or "")
    if re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
        raise RuntimeError("pull_request.head.sha is invalid for status-history evidence")
    repository = policy["repository"]
    api = policy["api_evidence"]
    page_size = int(api["page_size"])
    context = policy["release_approval"]["policy_status_context"]
    result: list[dict[str, Any]] = []
    for page in range(1, policy["branch_history"]["max_pages"] + 1):
        query = urllib.parse.urlencode({"per_page": page_size, "page": page})
        value = _github_get_json(
            f"{api['github_api']}/repos/{repository}/commits/{head_sha}/statuses?{query}"
        )
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise RuntimeError("GitHub commit-status history was not a list of objects")
        result.extend(
            item
            for item in value
            if item.get("context") == context
            and item.get("state") == "failure"
            and str(item.get("description") or "").startswith(
                ISSUE_INVALIDATION_DESCRIPTION_PREFIX
            )
        )
        if len(value) < page_size:
            return result
    raise RuntimeError("GitHub commit-status pagination limit was reached")


def active_branch_rules(
    pull: dict[str, Any], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    """Read every active remote rule GitHub says applies to the PR base branch.

    This is deliberately live API evidence. A checked-in description of the
    intended ruleset cannot prevent a stale successful commit status from being
    merged after the base branch advances.
    """

    base_ref = str((pull.get("base") or {}).get("ref") or "")
    if not base_ref:
        raise RuntimeError("pull_request.base.ref is required for remote rule evidence")
    repository = policy["repository"]
    api = policy["api_evidence"]
    page_size = int(api["page_size"])
    encoded_ref = urllib.parse.quote(base_ref, safe="")
    result: list[dict[str, Any]] = []
    for page in range(1, policy["branch_history"]["max_pages"] + 1):
        query = urllib.parse.urlencode({"per_page": page_size, "page": page})
        value = _github_get_json(
            f"{api['github_api']}/repos/{repository}/rules/branches/"
            f"{encoded_ref}?{query}"
        )
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise RuntimeError("GitHub active-branch-rules response was not a list")
        result.extend(value)
        if len(value) < page_size:
            return result
    raise RuntimeError("GitHub active-branch-rules pagination limit was reached")


def remote_merge_gate_errors(
    pull: dict[str, Any], rules: list[dict[str, Any]], policy: dict[str, Any]
) -> list[str]:
    """Validate the remote, PR-specific merge gate for one base branch.

    Commit statuses are shared by repository+SHA. Therefore duplicate-head
    detection and workflow serialization are defense in depth, while an active
    PR review rule remains the PR-specific barrier. Strict required checks are
    the merge-time barrier that invalidates a success when the base advances.
    """

    base_ref = str((pull.get("base") or {}).get("ref") or "")
    flow = policy["branch_flow"]
    remote_policy = policy["remote_merge_gate"]
    if remote_policy.get("require_app_binding") is not True:
        return [
            "remote rollout blocker: trusted policy must require GitHub App binding"
        ]
    configured_contexts = remote_policy["required_contexts_by_base"].get(base_ref)
    if not isinstance(configured_contexts, list) or not configured_contexts or not all(
        isinstance(item, str) and item for item in configured_contexts
    ):
        return [
            f"remote rollout blocker: trusted policy has no required contexts for {base_ref}"
        ]
    expected_contexts = set(configured_contexts)
    expected_methods: set[str]
    if base_ref == flow["develop_branch"]:
        configured_method = policy["merge_policy"]["into_develop"]
    elif base_ref == flow["release_branch"]:
        configured_method = policy["merge_policy"]["into_main"]
    else:
        return [f"remote merge gate does not support base branch {base_ref!r}"]
    api_method = {"merge-commit": "merge"}.get(configured_method, configured_method)
    expected_methods = {api_method}

    strict_bound_contexts: set[str] = set()
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict) or (
            parameters.get("strict_required_status_checks_policy") is not True
        ):
            continue
        checks = parameters.get("required_status_checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict):
                continue
            context = check.get("context")
            integration_id = check.get("integration_id")
            if (
                isinstance(context, str)
                and type(integration_id) is int
                and integration_id > 0
            ):
                strict_bound_contexts.add(context)

    errors: list[str] = []
    missing_contexts = sorted(expected_contexts - strict_bound_contexts)
    if missing_contexts:
        errors.append(
            "remote rollout blocker: active rules for "
            f"{base_ref} must require strict, GitHub-App-bound status contexts: "
            + ", ".join(missing_contexts)
        )

    pr_gate_found = False
    for rule in rules:
        if rule.get("type") != "pull_request":
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, dict):
            continue
        methods = parameters.get("allowed_merge_methods")
        method_set = (
            {str(item) for item in methods}
            if isinstance(methods, list)
            else set()
        )
        count = parameters.get("required_approving_review_count")
        required_flags = remote_policy["required_pr_rule_parameters"]
        flags_pass = isinstance(required_flags, list) and all(
            isinstance(flag, str) and parameters.get(flag) is True
            for flag in required_flags
        )
        if (
            method_set == expected_methods
            and type(count) is int
            and count >= 1
            and flags_pass
        ):
            pr_gate_found = True
            break
    if not pr_gate_found:
        errors.append(
            "remote rollout blocker: active rules for "
            f"{base_ref} must require a PR-specific approval, stale-review dismissal, "
            "CODEOWNER review, last-push approval, resolved review threads, and only "
            "the governed merge method "
            f"{sorted(expected_methods)}"
        )
    active_rule_types = {
        str(rule.get("type")) for rule in rules if isinstance(rule.get("type"), str)
    }
    configured_ref_guards = remote_policy["required_ref_guard_rules"]
    expected_ref_guards = (
        {str(item) for item in configured_ref_guards}
        if isinstance(configured_ref_guards, list)
        else set()
    )
    missing_ref_guards = sorted(expected_ref_guards - active_rule_types)
    if missing_ref_guards:
        errors.append(
            "remote rollout blocker: active rules for "
            f"{base_ref} must prevent resident-branch deletion and force push; missing: "
            + ", ".join(missing_ref_guards)
        )
    return errors


def _validate_branch_and_stage(
    result: ValidationResult,
    pull: dict[str, Any],
    metadata: dict[str, str],
    policy: dict[str, Any],
    today: date,
) -> tuple[str | None, str]:
    flow = policy["branch_flow"]
    meta_policy = policy["metadata"]
    base = pull["base"]["ref"]
    head = pull["head"]["ref"]
    pr_type = metadata.get("PR-Type", "")
    declared_risk = metadata.get("Risk-Level", "")
    branch_kind, branch_match = _branch_match(head, policy)

    if base == flow["develop_branch"]:
        if branch_kind == "feature":
            if pr_type not in flow["feature_pr_types"]:
                result.errors.append(f"PR-Type {pr_type or '[missing]'} cannot target {base}")
        elif branch_kind == "integration":
            if pr_type != flow["integration_pr_type"]:
                result.errors.append(
                    "codex/integration-<stage-slug>-vN must use PR-Type integration"
                )
            if declared_risk != flow["integration_risk"]:
                result.errors.append("codex/integration-* is an R2-only exception")
        else:
            result.errors.append(
                "develop only accepts codex/<stage-slug>-vN or R2 "
                "codex/integration-<stage-slug>-vN heads"
            )
    elif base == flow["release_branch"]:
        branch_kind = "release"
        if head != flow["main_head"]:
            result.errors.append(f"only {flow['main_head']} may target {base}")
        if pr_type != flow["main_pr_type"]:
            result.errors.append(f"PR-Type must be {flow['main_pr_type']} when targeting {base}")
        required_owner = policy["release_approval"]["required_login"]
        if _normalized_login(metadata.get("Accountable-Owner", "")) != required_owner.casefold():
            result.errors.append(f"release Accountable-Owner must be @{required_owner}")
    else:
        branch_kind = None
        result.errors.append(f"unsupported base branch: {base}")

    epic_value = metadata.get("Epic", "")
    stage_value = metadata.get("Stage", "")
    stage_match = re.fullmatch(meta_policy["stage_regex"], stage_value)
    if branch_kind in {"feature", "integration"}:
        if not re.fullmatch(meta_policy["epic_regex"], epic_value):
            result.errors.append("Epic must be a module Epic Issue in #<number> form")
        if stage_match is None:
            result.errors.append("Stage must use <stage-slug>@v<positive-version>")
        if branch_kind == "feature" and branch_match and stage_match:
            if branch_match.group("stage") != stage_match.group("stage"):
                result.errors.append("branch stage slug does not match Stage metadata")
            if branch_match.group("version") != stage_match.group("version"):
                result.errors.append("branch stage version does not match Stage metadata")
        elif branch_kind == "integration" and branch_match and stage_match:
            if branch_match.group("stage") != stage_match.group("stage"):
                result.errors.append("branch stage slug does not match Stage metadata")
            if branch_match.group("version") != stage_match.group("version"):
                result.errors.append("branch stage version does not match Stage metadata")
    elif branch_kind == "release":
        if epic_value != meta_policy["release_epic"]:
            result.errors.append("release PR must use Epic: release")
        if stage_match is None or stage_match.group("stage") != "release":
            result.errors.append("release PR must use Stage: release@v<positive-version>")

    sunset_value = metadata.get("Branch-Sunset", "")
    created = _date_value((pull.get("created_at") or "")[:10])
    if branch_kind == "release":
        if sunset_value != "not-applicable":
            result.errors.append("release PR must use Branch-Sunset: not-applicable")
    elif branch_kind in {"feature", "integration"}:
        sunset = _date_value(sunset_value)
        if sunset is None:
            result.errors.append("topic branches require an ISO Branch-Sunset date")
        else:
            if sunset < today:
                result.errors.append("topic branch Branch-Sunset has expired")
            if created and sunset < created:
                result.errors.append("Branch-Sunset cannot predate PR creation")
            if branch_kind == "integration" and created:
                limit = created + timedelta(days=flow["integration_sunset_days"])
                if sunset > limit:
                    result.errors.append(
                        f"integration Branch-Sunset exceeds {flow['integration_sunset_days']} days"
                    )

    expected_rollback = meta_policy["rollback_units"].get(base)
    if expected_rollback and metadata.get("Rollback-Unit") != expected_rollback:
        result.errors.append(f"Rollback-Unit must be {expected_rollback} when targeting {base}")
    return branch_kind, base


def _format_issue_list(numbers: Any, *, allow_none: bool = False) -> str:
    if not isinstance(numbers, list) or any(type(item) is not int for item in numbers):
        return ""
    if allow_none and not numbers:
        return "none"
    return ", ".join(f"#{number}" for number in numbers)


def contract_metadata(
    evidence: ContractEvidence, pull: dict[str, Any]
) -> dict[str, str]:
    """Build the exact human-readable metadata mirror from authority data."""

    contract = evidence.contract
    scope_value = str(contract.get("scope_freeze", ""))
    if contract.get("contract_kind") == "release" and scope_value == "current-head":
        scope_value = f"scope@{((pull.get('head') or {}).get('sha') or '')}"
    return {
        "Authority-Contract": evidence.path,
        "PR-Type": str(contract.get("pr_type", "")),
        "Risk-Level": str(contract.get("risk_level", "")),
        "Epic": str(contract.get("epic", "")),
        "Stage": str(contract.get("stage", "")),
        "Included-Issues": _format_issue_list(contract.get("included_issues", [])),
        "Deferred-Issues": _format_issue_list(
            contract.get("deferred_issues", []), allow_none=True
        ),
        "Scope-Freeze": scope_value,
        "Branch-Sunset": str(contract.get("branch_sunset", "")),
        "Evidence-Baseline": f"head@{((pull.get('head') or {}).get('sha') or '')}",
        "Evidence-Invalidation": (
            ",".join(str(item) for item in contract.get("evidence_invalidation", []))
            if isinstance(contract.get("evidence_invalidation"), list)
            else ""
        ),
        "Rollback-Unit": str(contract.get("rollback_unit", "")),
        "Accountable-Owner": str(contract.get("accountable_owner", "")),
        "Implementation-Agent": str(contract.get("implementation_agent", "")),
        "Independent-PR-Author": str(contract.get("independent_pr_author", "")),
        "Workstream": str(contract.get("workstream", "")),
        "Scientific-Mode": str(contract.get("scientific_mode", "")),
        "Closure-State": str(contract.get("closure_state", "")),
    }


def _validate_contract_schema(
    result: ValidationResult,
    evidence: ContractEvidence | None,
    pull: dict[str, Any],
    changes: ChangeSet,
    policy: dict[str, Any],
    *,
    expected_contract_ref: str,
) -> dict[str, str]:
    if evidence is None:
        result.errors.append("head-bound governance contract evidence is missing")
        return {}
    config = policy["governance_contract"]
    try:
        expected_path = derive_contract_path(pull, changes.files, policy)
    except ValueError as exc:
        result.errors.append(str(exc))
        return {}
    if evidence.path != expected_path:
        result.errors.append(
            f"authority contract path must be {expected_path}, got {evidence.path}"
        )
    if expected_path not in changes.files:
        result.errors.append("authority contract must be present in PR changed files")
    expected_kind = (
        "release"
        if (pull.get("base") or {}).get("ref")
        == policy["branch_flow"]["release_branch"]
        else "stage"
    )
    contract_prefixes = (
        [config["release_directory"] + "/"]
        if expected_kind == "release"
        else [config["stage_directory"] + "/"]
    )
    changed_contracts = sorted(
        path
        for path in changes.files
        if any(path.startswith(prefix) for prefix in contract_prefixes)
    )
    if changed_contracts != [expected_path]:
        result.errors.append(
            "PR must change exactly its one derived authority contract path"
        )
    changed_release_contracts = [
        path
        for path in changes.files
        if path.startswith(config["release_directory"] + "/")
    ]
    release_preparation = config["release_preparation"]
    if expected_kind == "stage" and changed_release_contracts:
        branch_kind, branch_match = _branch_match(
            str((pull.get("head") or {}).get("ref", "")), policy
        )
        valid_preparation = (
            release_preparation["state"] == "active"
            and branch_kind == "feature"
            and branch_match is not None
            and branch_match.group("stage") == release_preparation["stage_slug"]
            and len(changed_release_contracts) == 1
        )
        if valid_preparation:
            release_path_match = re.fullmatch(
                config["release_path_regex"], changed_release_contracts[0]
            )
            valid_preparation = release_path_match is not None
        if not valid_preparation:
            result.errors.append(
                "only codex/release-preparation-vK may author exactly one valid "
                "release-vR contract"
            )
    if expected_kind == "release" and release_preparation["state"] != "active":
        result.errors.append(
            "release contract preparation is rollout-blocked; no release may merge"
        )
    if evidence.source_ref != expected_contract_ref:
        result.errors.append(
            "authority contract was not read from the exact required commit ref"
        )
    if re.fullmatch(r"[0-9a-f]{40}", evidence.blob_sha) is None:
        result.errors.append("authority contract blob SHA is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", evidence.content_sha256) is None:
        result.errors.append("authority contract content SHA-256 is invalid")

    contract = evidence.contract
    keys = set(contract)
    for key in sorted(CONTRACT_FIELDS - keys):
        result.errors.append(f"authority contract missing field: {key}")
    for key in sorted(keys - CONTRACT_FIELDS):
        result.errors.append(f"authority contract has unknown field: {key}")
    if keys != CONTRACT_FIELDS:
        return {}

    if type(contract["schema_version"]) is not int or (
        contract["schema_version"] != config["schema_version"]
    ):
        result.errors.append(
            f"authority contract schema_version must be {config['schema_version']}"
        )
    if contract["contract_kind"] != expected_kind:
        result.errors.append(f"authority contract kind must be {expected_kind}")
    if contract["repository"] != policy["repository"]:
        result.errors.append("authority contract repository binding is wrong")
    if type(contract["pr_number"]) is not int or contract["pr_number"] < 1:
        result.errors.append("authority contract pr_number must be a positive integer")
    elif contract["pr_number"] != pull.get("number"):
        result.errors.append("authority contract pr_number does not match this PR")
    if contract["base_ref"] != (pull.get("base") or {}).get("ref"):
        result.errors.append("authority contract base_ref does not match this PR")
    if not isinstance(contract["base_sha"], str) or re.fullmatch(
        r"[0-9a-f]{40}", contract["base_sha"]
    ) is None:
        result.errors.append("authority contract base_sha must be a 40-character SHA")
    elif expected_contract_ref == ((pull.get("head") or {}).get("sha") or "") and (
        contract["base_sha"] != ((pull.get("base") or {}).get("sha") or "")
    ):
        result.errors.append(
            "authority contract base_sha does not match the current PR base head"
        )
    if contract["head_ref"] != (pull.get("head") or {}).get("ref"):
        result.errors.append("authority contract head_ref does not match this PR")
    if expected_kind == "release":
        if contract["scope_freeze"] != release_preparation["release_scope_mode"]:
            result.errors.append(
                "release authority contract scope_freeze must be current-head"
            )
        preparation_binding = contract["release_preparation"]
        if (
            not isinstance(preparation_binding, dict)
            or set(preparation_binding) != {"pr_number", "base_sha"}
            or type(preparation_binding.get("pr_number")) is not int
            or preparation_binding.get("pr_number", 0) < 1
            or not isinstance(preparation_binding.get("base_sha"), str)
            or re.fullmatch(r"[0-9a-f]{40}", preparation_binding.get("base_sha", ""))
            is None
        ):
            result.errors.append(
                "release authority contract release_preparation binding is invalid"
            )
    elif not isinstance(contract["scope_freeze"], str) or re.fullmatch(
        policy["metadata"]["scope_freeze_regex"], contract["scope_freeze"]
    ) is None:
        result.errors.append(
            "Stage authority contract scope_freeze must be scope@<40-character-sha>"
        )
    elif contract["release_preparation"] is not None:
        result.errors.append("Stage authority contract release_preparation must be null")

    scalar_fields = CONTRACT_FIELDS - {
        "schema_version",
        "pr_number",
        "included_issues",
        "deferred_issues",
        "issue_snapshots",
        "evidence_invalidation",
        "scope_threshold_justification",
        "release_preparation",
    }
    for key in sorted(scalar_fields):
        value = contract[key]
        if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
            result.errors.append(f"authority contract field must be one non-empty line: {key}")
        elif "<" in value or ">" in value:
            result.errors.append(f"authority contract field contains a placeholder: {key}")

    for key in ("included_issues", "deferred_issues"):
        values = contract[key]
        if (
            not isinstance(values, list)
            or any(type(item) is not int or item < 1 for item in values)
            or values != sorted(set(values))
        ):
            result.errors.append(
                f"authority contract {key} must be a sorted unique positive-integer array"
            )
    if isinstance(contract["included_issues"], list) and not contract["included_issues"]:
        result.errors.append("authority contract included_issues cannot be empty")
    if isinstance(contract["included_issues"], list) and isinstance(
        contract["deferred_issues"], list
    ):
        if set(contract["included_issues"]) & set(contract["deferred_issues"]):
            result.errors.append(
                "authority contract cannot include and defer the same Issue"
            )

    result.errors.extend(issue_snapshot_errors(contract))

    invalidation = contract["evidence_invalidation"]
    if (
        not isinstance(invalidation, list)
        or any(not isinstance(item, str) or not item for item in invalidation)
        or len(invalidation) != len(set(invalidation))
    ):
        result.errors.append(
            "authority contract evidence_invalidation must be a unique string array"
        )
    else:
        required = policy["metadata"]["required_evidence_invalidation_tokens"]
        if invalidation[: len(required)] != required:
            result.errors.append(
                "authority contract evidence_invalidation must begin with policy tokens"
            )

    justification = contract["scope_threshold_justification"]
    if not isinstance(justification, dict):
        result.errors.append("authority contract scope_threshold_justification must be an object")
    else:
        justification_keys = set(justification)
        if justification_keys != CONTRACT_JUSTIFICATION_FIELDS:
            result.errors.append(
                "authority contract scope_threshold_justification has wrong fields"
            )
        for key, value in justification.items():
            if not isinstance(value, str) or "\r" in value or "\n" in value:
                result.errors.append(
                    "authority contract scope_threshold_justification values must be strings"
                )
                break

    stage_match = re.fullmatch(policy["metadata"]["stage_regex"], str(contract["stage"]))
    if stage_match:
        if expected_kind == "stage":
            expected_from_stage = config["stage_path_template"].format(
                stage=stage_match.group("stage"), version=stage_match.group("version")
            )
        else:
            expected_from_stage = config["release_path_template"].format(
                version=stage_match.group("version")
            )
        if evidence.path != expected_from_stage:
            result.errors.append("authority contract Stage does not bind its derived path")
    return contract_metadata(evidence, pull)


def _scope_warnings(
    changes: ChangeSet,
    included_issues: set[int],
    section_map: dict[str, str],
    policy: dict[str, Any],
    authority_justification: dict[str, str] | None,
    *,
    require_body_explanation: bool,
) -> ValidationResult:
    result = ValidationResult()
    config = policy["scope_thresholds"]
    case_source_patterns = config["case_source_patterns"]
    source_test_count = sum(
        1
        for item in changes.paths
        if _matches(item.path, config["source_test_patterns"])
        or _matches(item.path, case_source_patterns)
    )
    derived_evidence_patterns = config["derived_evidence_patterns"]
    non_generated_loc = sum(
        item.additions + item.deletions
        for item in changes.paths
        if _matches(item.path, case_source_patterns)
        or not _matches(item.path, derived_evidence_patterns)
    )
    breaches: list[tuple[str, str]] = []
    if len(included_issues) > config["max_included_issues"]:
        breaches.append(
            (
                "included-issues",
                f"included-issues:{len(included_issues)}>{config['max_included_issues']}",
            )
        )
    if source_test_count > config["max_source_test_files"]:
        breaches.append(
            (
                "source-test-files",
                f"source-test-files:{source_test_count}>{config['max_source_test_files']}",
            )
        )
    if non_generated_loc > config["max_non_generated_loc"]:
        breaches.append(
            (
                "non-generated-loc",
                f"non-generated-loc:{non_generated_loc}>{config['max_non_generated_loc']}",
            )
        )
    for _, breach in breaches:
        result.warnings.append(f"scope-threshold:{breach}")
    if breaches:
        minimum = policy["closure_gate"]["minimum_section_characters"]
        heading = config["explanation_section"]
        explanation = section_map.get(heading, "")
        if require_body_explanation and not substantive(explanation, minimum):
            result.errors.append(
                f"scope threshold warning requires substantive section: {heading}"
            )
        explanation_pairs = FIELD_RE.findall(explanation)
        explanation_counts = Counter(key for key, _ in explanation_pairs)
        explanation_fields = {key: value.strip() for key, value in explanation_pairs}
        for key, count in sorted(explanation_counts.items()):
            if count > 1:
                result.errors.append(f"duplicate scope explanation field: {key}")
        contract_values = authority_justification or {}
        token_keys = {
            "included-issues": "included_issues",
            "source-test-files": "source_test_files",
            "non-generated-loc": "non_generated_loc",
        }
        required_keys = {
            "Atomic-Outcome": "atomic_outcome",
            "Shared-Failure-Mechanism": "shared_failure_mechanism",
            "Shared-Validation": "shared_validation",
            "Rollback-Reason": "rollback_reason",
        }
        for token, _ in breaches:
            contract_key = token_keys[token]
            if not substantive_scope_field(
                contract_values.get(contract_key, ""), minimum
            ):
                result.errors.append(
                    "authority contract scope threshold explanation needs substantive "
                    f"field: {contract_key}"
                )
            if require_body_explanation and explanation_fields.get(token, "") != (
                contract_values.get(contract_key, "")
            ):
                result.errors.append(
                    f"body scope threshold field does not mirror contract: {token}"
                )
        for required_field in config["explanation_required_fields"]:
            contract_key = required_keys[required_field]
            if not substantive_scope_field(
                contract_values.get(contract_key, ""), minimum
            ):
                result.errors.append(
                    "authority contract scope threshold explanation needs substantive "
                    f"field: {contract_key}"
                )
            if require_body_explanation and explanation_fields.get(required_field, "") != (
                contract_values.get(contract_key, "")
            ):
                result.errors.append(
                    "body scope threshold field does not mirror contract: "
                    f"{required_field}"
                )
    return result


def _validate_release_preparation_binding(
    result: ValidationResult,
    pull: dict[str, Any],
    evidence: ContractEvidence | None,
    record: dict[str, Any] | None,
    policy: dict[str, Any],
    *,
    pending: bool,
) -> None:
    if evidence is None:
        return
    binding = evidence.contract.get("release_preparation")
    if not isinstance(binding, dict):
        return
    if record is None:
        result.errors.append("release preparation PR API evidence is missing")
        return
    number = binding.get("pr_number")
    base_sha = binding.get("base_sha")
    if record.get("number") != number:
        result.errors.append("release preparation PR number binding is wrong")
    config = policy["governance_contract"]["release_preparation"]
    record_base = record.get("base") or {}
    record_head = record.get("head") or {}
    if record_base.get("ref") != policy["branch_flow"]["develop_branch"]:
        result.errors.append("release preparation PR must target develop")
    record_kind, record_match = _branch_match(str(record_head.get("ref", "")), policy)
    if (
        record_kind != "feature"
        or record_match is None
        or record_match.group("stage") != config["stage_slug"]
    ):
        result.errors.append("release preparation PR branch/version binding is wrong")
    if record_base.get("sha") != base_sha:
        result.errors.append("release preparation base SHA binding is wrong")
    repository = policy["repository"].casefold()
    for label, side in (("base", record_base), ("head", record_head)):
        full_name = ((side.get("repo") or {}).get("full_name") or "").casefold()
        if full_name != repository:
            result.errors.append(
                f"release preparation PR {label} repository binding is wrong"
            )
    target_created = _timestamp(pull.get("created_at"))
    preparation_created = _timestamp(record.get("created_at"))
    if (
        target_created is None
        or preparation_created is None
        or target_created > preparation_created
    ):
        result.errors.append("release PR must exist before its preparation PR")
    target_head_sha = ((pull.get("head") or {}).get("sha") or "")
    if pending:
        if record.get("state") != "open" or record.get("merged_at"):
            result.errors.append("pending release preparation PR must be open and unmerged")
        if target_head_sha != base_sha:
            result.errors.append(
                "draft release head must equal preparation base before preparation merge"
            )
    else:
        if not record.get("merged_at"):
            result.errors.append("release preparation PR must be merged")
        if record.get("merge_commit_sha") != target_head_sha:
            result.errors.append(
                "release head must equal the exact preparation merge commit; "
                "a newer develop head requires a new preparation version"
            )


def validate(
    payload: dict[str, Any],
    changes: ChangeSet,
    policy: dict[str, Any],
    *,
    prior_branch_prs: list[dict[str, Any]] | None = None,
    scope_freeze_valid: bool | None = None,
    issue_records: dict[int, dict[str, Any]] | None = None,
    finalization_merged_at: datetime | None = None,
    contract_evidence: ContractEvidence | None = None,
    expected_contract_ref: str | None = None,
    same_head_open_prs: list[dict[str, Any]] | None = None,
    issue_snapshot_invalidation_records: list[dict[str, Any]] | None = None,
    require_body_mirror: bool = True,
    release_preparation_record: dict[str, Any] | None = None,
    release_preparation_pending: bool = False,
    today: date | None = None,
) -> ValidationResult:
    result = ValidationResult()
    pull = payload.get("pull_request")
    if not isinstance(pull, dict):
        result.errors.append("event does not contain a pull_request object")
        return result

    body = pull.get("body") or ""
    body_metadata = parse_metadata(body)
    section_map = sections(body)
    heading_counts = section_counts(body)
    branch = policy["branch_flow"]
    base_repo = pull["base"]["repo"]["full_name"]
    head_repo = pull["head"]["repo"]["full_name"]

    if base_repo.casefold() != policy["repository"].casefold():
        result.errors.append(f"unexpected base repository: {base_repo}")
    if branch["same_repository_only"] and head_repo.casefold() != base_repo.casefold():
        result.errors.append("pull requests must use a branch in the same repository")

    authority_ref = expected_contract_ref or ((pull.get("head") or {}).get("sha") or "")
    metadata = _validate_contract_schema(
        result,
        contract_evidence,
        pull,
        changes,
        policy,
        expected_contract_ref=authority_ref,
    )

    if require_body_mirror:
        result.errors.extend(metadata_ambiguities(body))
        for required_field in policy["metadata"]["required_fields"]:
            value = body_metadata.get(required_field, "")
            if not value or "<" in value or ">" in value:
                result.errors.append(
                    f"missing or placeholder governance metadata: {required_field}"
                )
            if metadata and value != metadata.get(required_field, ""):
                result.errors.append(
                    f"PR body metadata does not mirror authority contract: {required_field}"
                )

    declared_risk = metadata.get("Risk-Level", "")
    if declared_risk not in policy["risk_order"]:
        result.errors.append(f"invalid Risk-Level: {declared_risk or '[missing]'}")
    if metadata.get("Scientific-Mode") not in policy["metadata"]["scientific_modes"]:
        result.errors.append("Scientific-Mode is invalid")
    if metadata.get("Closure-State") != policy["metadata"]["ready_value"]:
        result.errors.append("Closure-State must be ready before review")

    branch_kind, base = _validate_branch_and_stage(
        result,
        pull,
        metadata,
        policy,
        today or datetime.now(timezone.utc).date(),
    )
    if branch_kind == "release":
        _validate_release_preparation_binding(
            result,
            pull,
            contract_evidence,
            release_preparation_record,
            policy,
            pending=release_preparation_pending,
        )

    meta_policy = policy["metadata"]
    included_value = metadata.get("Included-Issues", "")
    if not re.fullmatch(meta_policy["issue_list_regex"], included_value):
        result.errors.append("Included-Issues must be a strict comma-separated #<number> list")
    included_issues = _issue_numbers(included_value)
    deferred_value = metadata.get("Deferred-Issues", "")
    if not re.fullmatch(meta_policy["deferred_issue_list_regex"], deferred_value):
        result.errors.append(
            "Deferred-Issues must be none or a strict comma-separated #<number> list"
        )
    deferred_issues = set() if deferred_value == "none" else _issue_numbers(deferred_value)
    if policy["closure_gate"]["require_included_issue"] and not included_issues:
        result.errors.append("Included-Issues must contain at least one #<number>")
    if included_issues & deferred_issues:
        result.errors.append("an Issue cannot be both Included and Deferred")
    _validate_issue_contracts(
        result,
        metadata,
        included_issues,
        deferred_issues,
        branch_kind,
        policy,
        issue_records,
        finalization_merged_at,
        contract_evidence.contract if contract_evidence is not None else None,
    )

    scope_value = metadata.get("Scope-Freeze", "")
    if not re.fullmatch(meta_policy["scope_freeze_regex"], scope_value):
        result.errors.append("Scope-Freeze must be scope@<40-character-commit-sha>")
    elif scope_freeze_valid is False:
        result.errors.append(
            "Stage Scope-Freeze must equal the GitHub compare merge-base/branch-point"
        )
    if branch_kind == "release" and _metadata_sha(scope_value) != pull["head"]["sha"]:
        result.errors.append("release Scope-Freeze must equal the exact develop/head SHA")

    evidence_value = metadata.get("Evidence-Baseline", "")
    if not re.fullmatch(meta_policy["evidence_baseline_regex"], evidence_value):
        result.errors.append("Evidence-Baseline must be head@<40-character-current-head-sha>")
    else:
        evidence_sha = _metadata_sha(evidence_value)
        if evidence_sha != pull["head"]["sha"]:
            result.errors.append("Evidence-Baseline is stale for the current head")

    invalidation = {
        item.strip()
        for item in metadata.get("Evidence-Invalidation", "").split(",")
        if item.strip()
    }
    missing_invalidation = set(meta_policy["required_evidence_invalidation_tokens"])
    missing_invalidation -= invalidation
    if missing_invalidation:
        result.errors.append(
            "Evidence-Invalidation is missing: " + ", ".join(sorted(missing_invalidation))
        )

    author = pull.get("user") or {}
    expected_type = policy["pr_author"]["required_account_type"]
    allowed_bots = {
        _normalized_login(item) for item in policy["pr_author"]["allowed_bot_logins"]
    }
    author_login = _normalized_login(author.get("login", ""))
    if author.get("type") != expected_type:
        result.errors.append(f"PR author must have GitHub account type {expected_type}")
    if not allowed_bots:
        result.errors.append(
            "PR bot allowlist is empty; rollout is blocked until a real Bot user.login is verified"
        )
    elif author_login not in allowed_bots:
        result.errors.append(f"PR bot author is not allowlisted: {author_login}")
    independent = _normalized_login(metadata.get("Independent-PR-Author", ""))
    implementer = _normalized_login(metadata.get("Implementation-Agent", ""))
    if independent != author_login:
        result.errors.append("Independent-PR-Author must match the GitHub PR author")
    if policy["pr_author"]["require_distinct_implementation_agent"]:
        if implementer == independent:
            result.errors.append("Implementation-Agent and Independent-PR-Author must differ")

    if require_body_mirror:
        minimum = policy["closure_gate"]["minimum_section_characters"]
        required_sections = list(policy["required_sections"])
        if declared_risk == "R2":
            required_sections.extend(policy["r2_required_sections"])
        if branch_kind == "integration":
            required_sections.extend(policy["integration_required_sections"])
        if branch_kind == "release":
            required_sections.extend(policy["release_required_sections"])
        governance_headings = set(required_sections)
        governance_headings.add(policy["scope_thresholds"]["explanation_section"])
        for heading in sorted(governance_headings):
            if heading_counts[heading] > 1:
                result.errors.append(f"duplicate governance section heading: {heading}")
        for heading in required_sections:
            if not substantive(section_map.get(heading, ""), minimum):
                result.errors.append(f"missing substantive section: {heading}")

    required_risk = risk_floor(changes.files, policy)
    if declared_risk in policy["risk_order"]:
        if policy["risk_order"].index(declared_risk) < policy["risk_order"].index(required_risk):
            result.errors.append(
                f"Risk-Level {declared_risk} is below the changed-path floor {required_risk}"
            )
    if not changes.files:
        result.errors.append("no changed files were found for the pull request")

    if branch_kind in {"feature", "integration"}:
        previous = prior_branch_prs or []
        if branch["forbid_topic_branch_reuse_after_any_pr"] and previous:
            numbers = ", ".join(f"#{item.get('number', '?')}" for item in previous)
            result.errors.append(
                f"topic branch was already used by {numbers}; create a new Stage version branch"
            )

    duplicates = same_head_open_prs or []
    if duplicates:
        numbers = ", ".join(f"#{item.get('number', '?')}" for item in duplicates)
        result.errors.append(
            "current head SHA is shared by another open PR "
            f"({numbers}); all PRs on this SHA must fail the shared status context"
        )

    invalidation_records = issue_snapshot_invalidation_records or []
    if invalidation_records:
        descriptions = sorted(
            {
                str(item.get("description") or ISSUE_INVALIDATION_DESCRIPTION_PREFIX)
                for item in invalidation_records
            }
        )
        result.errors.append(
            "this exact head was permanently invalidated by an Issue mutation; "
            "commit updated Issue snapshots and rerun review on a new head: "
            + "; ".join(descriptions)
        )

    justification = None
    if contract_evidence is not None:
        value = contract_evidence.contract.get("scope_threshold_justification")
        if isinstance(value, dict):
            justification = {
                key: item for key, item in value.items() if isinstance(item, str)
            }
    threshold = _scope_warnings(
        changes,
        included_issues,
        section_map,
        policy,
        justification,
        require_body_explanation=require_body_mirror,
    )
    result.errors.extend(threshold.errors)
    result.warnings.extend(threshold.warnings)
    return result


def validate_prepared_release(
    prepared: PreparedRelease,
    changes: ChangeSet,
    policy: dict[str, Any],
    *,
    issue_records: dict[int, dict[str, Any]] | None,
    same_head_open_prs: list[dict[str, Any]] | None,
    issue_snapshot_invalidation_records: list[dict[str, Any]] | None = None,
    expected_contract_ref: str | None = None,
    enforce_current_target_draft: bool = False,
    today: date | None = None,
) -> ValidationResult:
    """Validate the release payload before its preparation Stage may land."""

    result = ValidationResult()
    config = policy["governance_contract"]
    release_config = config["release_preparation"]
    preparation_pull = prepared.preparation_pull
    target_pull = prepared.target_pull
    paths = release_contract_paths(changes, policy)
    kind, branch_match = _branch_match(
        str((preparation_pull.get("head") or {}).get("ref", "")), policy
    )
    release_path_match = re.fullmatch(config["release_path_regex"], prepared.contract.path)
    if (
        release_config["state"] != "active"
        or kind != "feature"
        or branch_match is None
        or branch_match.group("stage") != release_config["stage_slug"]
        or len(paths) != 1
        or paths[0] != prepared.contract.path
        or release_path_match is None
    ):
        result.errors.append(
            "prepared release contract is not carried by one unique "
            "release-preparation-vK Stage"
        )
    preparation_merged_at = _timestamp(preparation_pull.get("merged_at"))
    if preparation_merged_at is None:
        if release_config["require_target_open"] and target_pull.get("state") != "open":
            result.errors.append("release preparation target PR must remain open")
        if release_config["require_target_draft"] and target_pull.get("draft") is not True:
            result.errors.append("release preparation target PR must remain a draft")
    else:
        target_closed_at = _timestamp(target_pull.get("closed_at"))
        if target_pull.get("state") == "closed" and (
            target_closed_at is None or target_closed_at <= preparation_merged_at
        ):
            result.errors.append(
                "release target PR was not open when preparation merged"
            )
        if (
            enforce_current_target_draft
            and release_config["require_target_draft"]
            and target_pull.get("draft") is not True
        ):
            result.errors.append(
                "release target PR must still be draft during first merge-event finalization"
            )
    target_created = _timestamp(target_pull.get("created_at"))
    preparation_created = _timestamp(preparation_pull.get("created_at"))
    if (
        target_created is None
        or preparation_created is None
        or target_created > preparation_created
    ):
        result.errors.append(
            "release target PR must exist before its preparation Stage PR"
        )

    validation = validate(
        {"pull_request": target_pull},
        changes,
        policy,
        prior_branch_prs=[],
        scope_freeze_valid=True,
        issue_records=issue_records,
        contract_evidence=prepared.contract,
        expected_contract_ref=(
            expected_contract_ref
            or ((preparation_pull.get("head") or {}).get("sha")
            or "")
        ),
        same_head_open_prs=same_head_open_prs,
        issue_snapshot_invalidation_records=issue_snapshot_invalidation_records,
        require_body_mirror=False,
        release_preparation_record=preparation_pull,
        release_preparation_pending=not bool(preparation_pull.get("merged_at")),
        today=today,
    )
    result.errors.extend(validation.errors)
    result.warnings.extend(validation.warnings)
    return result


FIXTURE_HEAD = "b" * 40
FIXTURE_SCOPE = "a" * 40
FIXTURE_EPIC = 40
FIXTURE_BOT = "chatgpt-codex-connector[bot]"


def fixture_body(
    pr_type: str,
    *,
    epic: str = f"#{FIXTURE_EPIC}",
    stage: str = "dna@v1",
    sunset: str = "2026-09-01",
    rollback: str = "stage-squash",
    scope: str = FIXTURE_SCOPE,
    threshold_explanation: str = "",
) -> str:
    return f"""<!-- GOV-METADATA-START -->
Authority-Contract: .github/stage-contracts/{stage.replace('@', '-')}.json
PR-Type: {pr_type}
Risk-Level: R2
Epic: {epic}
Stage: {stage}
Included-Issues: #42
Deferred-Issues: #43
Scope-Freeze: scope@{scope}
Branch-Sunset: {sunset}
Evidence-Baseline: head@{FIXTURE_HEAD}
Evidence-Invalidation: head-change,scope-change,oracle-change
Rollback-Unit: {rollback}
Accountable-Owner: @let778750-cpu
Implementation-Agent: codex/worker-42
Independent-PR-Author: @{FIXTURE_BOT}
Workstream: dna-v1
Scientific-Mode: NOT_APPLICABLE
Closure-State: ready
<!-- GOV-METADATA-END -->

## 范围与非目标
The fixture has a bounded scope and explicit non-goals for validation.
## Epic、Stage 与 Scope Freeze
Epic 40 and dna version one are frozen at the declared scope commit.
## 变更说明
The fixture changes one governed behavior with an inspectable contract.
## 契约与权威影响
The fixture records authority and scientific-contract impact explicitly.
## 验证证据
The fixture records exact commands, revisions, platforms, and results.
## 证据失效条件
Head, scope, or evaluation-oracle changes invalidate this fixture evidence.
## 回滚单元与恢复
The complete Stage squash is the rollback unit and reopens included Issues.
## 回归与反例证据
The fixture includes negative topology and branch-reuse cases that must fail.
## 剩余风险与后续
Issue 43 remains deferred and is not claimed by this bounded Stage.
## 独立复核
The governance bot is distinct from the implementation agent in this fixture.
## Scope Threshold Explanation
{threshold_explanation}
## Integration 例外与 14 天退出
The integration exception expires within fourteen days and is then deleted.
## 科学保真门禁（R2 必填）
The fixture proves that all required R2 evidence headings are enforced.
## 对抗性证据（R2 必填）
Changing the head, scope, branch history, or oracle fails deterministically.
## 发布审批（release 必填）
Release evidence covers the exact develop head and all included Stage records.
"""


def fixture_payload(base: str, head: str, pr_type: str, **body_kwargs: str) -> dict[str, Any]:
    repo = {"full_name": "let778750-cpu/autofigure"}
    body = fixture_body(pr_type, **body_kwargs)
    if base == "main":
        stage_value = body_kwargs.get("stage", "dna@v1")
        stage_match = re.fullmatch(r"[a-z][a-z0-9-]*@v([1-9][0-9]*)", stage_value)
        if stage_match:
            body = re.sub(
                r"(?m)^Authority-Contract: .*?$",
                "Authority-Contract: .github/release-contracts/release-v"
                f"{stage_match.group(1)}.json",
                body,
            )
    return {
        "pull_request": {
            "number": 99,
            "body": body,
            "created_at": "2026-08-25T00:00:00Z",
            "base": {"ref": base, "sha": "c" * 40, "repo": repo},
            "head": {"ref": head, "sha": FIXTURE_HEAD, "repo": repo},
            "user": {"login": FIXTURE_BOT, "type": "Bot"},
        }
    }


def _fixture_changes(count: int = 1, loc_each: int = 10) -> ChangeSet:
    return ChangeSet(
        tuple(ChangedPath(f"tools/file_{index}.py", loc_each, 0) for index in range(count))
    )


def fixture_contract_evidence(
    payload: dict[str, Any],
    *,
    path: str | None = None,
    source_ref: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> ContractEvidence:
    pull = payload["pull_request"]
    metadata = parse_metadata(pull.get("body") or "")
    stage_match = re.fullmatch(r"([a-z][a-z0-9-]*)@v([1-9][0-9]*)", metadata.get("Stage", ""))
    if path is None:
        if (pull.get("base") or {}).get("ref") == "main" and stage_match:
            path = f".github/release-contracts/release-v{stage_match.group(2)}.json"
        elif stage_match:
            path = (
                f".github/stage-contracts/{stage_match.group(1)}-v"
                f"{stage_match.group(2)}.json"
            )
        else:
            path = ".github/stage-contracts/invalid-v1.json"
    explanation_fields = {
        key: value.strip()
        for key, value in FIELD_RE.findall(
            sections(pull.get("body") or "").get("Scope Threshold Explanation", "")
        )
    }
    included = sorted(_issue_numbers(metadata.get("Included-Issues", "")))
    deferred = (
        []
        if metadata.get("Deferred-Issues") == "none"
        else sorted(_issue_numbers(metadata.get("Deferred-Issues", "")))
    )
    contract: dict[str, Any] = {
        "schema_version": 1,
        "contract_kind": (
            "release" if (pull.get("base") or {}).get("ref") == "main" else "stage"
        ),
        "repository": ((pull.get("base") or {}).get("repo") or {}).get("full_name", ""),
        "pr_number": pull.get("number"),
        "base_ref": (pull.get("base") or {}).get("ref", ""),
        "base_sha": (pull.get("base") or {}).get("sha", ""),
        "head_ref": (pull.get("head") or {}).get("ref", ""),
        "pr_type": metadata.get("PR-Type", ""),
        "risk_level": metadata.get("Risk-Level", ""),
        "epic": metadata.get("Epic", ""),
        "stage": metadata.get("Stage", ""),
        "included_issues": included,
        "deferred_issues": deferred,
        "issue_snapshots": [],
        "scope_freeze": (
            "current-head"
            if (pull.get("base") or {}).get("ref") == "main"
            else metadata.get("Scope-Freeze", "")
        ),
        "branch_sunset": metadata.get("Branch-Sunset", ""),
        "evidence_invalidation": [
            item.strip()
            for item in metadata.get("Evidence-Invalidation", "").split(",")
            if item.strip()
        ],
        "rollback_unit": metadata.get("Rollback-Unit", ""),
        "accountable_owner": metadata.get("Accountable-Owner", ""),
        "implementation_agent": metadata.get("Implementation-Agent", ""),
        "independent_pr_author": metadata.get("Independent-PR-Author", ""),
        "workstream": metadata.get("Workstream", ""),
        "scientific_mode": metadata.get("Scientific-Mode", ""),
        "closure_state": metadata.get("Closure-State", ""),
        "release_preparation": (
            {
                "pr_number": 98,
                "base_sha": ((pull.get("head") or {}).get("sha") or ""),
            }
            if (pull.get("base") or {}).get("ref") == "main"
            else None
        ),
        "scope_threshold_justification": {
            "atomic_outcome": explanation_fields.get("Atomic-Outcome", ""),
            "shared_failure_mechanism": explanation_fields.get(
                "Shared-Failure-Mechanism", ""
            ),
            "shared_validation": explanation_fields.get("Shared-Validation", ""),
            "rollback_reason": explanation_fields.get("Rollback-Reason", ""),
            "included_issues": explanation_fields.get("included-issues", ""),
            "source_test_files": explanation_fields.get("source-test-files", ""),
            "non_generated_loc": explanation_fields.get("non-generated-loc", ""),
        },
    }
    try:
        contract["issue_snapshots"] = build_issue_snapshots(
            contract, fixture_issue_records(payload)
        )
    except ValueError:
        # Negative fixtures still need serializable evidence so validation can
        # report the deliberately malformed authority fields themselves.
        contract["issue_snapshots"] = []
    if overrides:
        contract.update(overrides)
    raw = (json.dumps(contract, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    return ContractEvidence(
        path=path,
        source_ref=source_ref or ((pull.get("head") or {}).get("sha") or ""),
        blob_sha="e" * 40,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        contract=contract,
    )


def with_contract_change(changes: ChangeSet, evidence: ContractEvidence) -> ChangeSet:
    if evidence.path in changes.files:
        return changes
    return ChangeSet((ChangedPath(evidence.path, 40, 0), *changes.paths))


def fixture_release_preparation_record(
    payload: dict[str, Any], evidence: ContractEvidence | None = None
) -> dict[str, Any] | None:
    pull = payload["pull_request"]
    if (pull.get("base") or {}).get("ref") != "main":
        return None
    authority = evidence or fixture_contract_evidence(payload)
    binding = authority.contract.get("release_preparation")
    stage_match = re.fullmatch(r"release@v([1-9][0-9]*)", authority.contract.get("stage", ""))
    if not isinstance(binding, dict) or stage_match is None:
        return None
    repo = {"full_name": "let778750-cpu/autofigure"}
    return {
        "number": binding["pr_number"],
        "state": "closed",
        "draft": False,
        "created_at": "2026-08-25T01:00:00Z",
        "merged_at": "2026-08-25T02:00:00Z",
        "merge_commit_sha": (pull.get("head") or {}).get("sha"),
        "base": {
            "ref": "develop",
            "sha": binding["base_sha"],
            "repo": repo,
        },
        "head": {
            "ref": f"codex/release-preparation-v{stage_match.group(1)}",
            "sha": "9" * 40,
            "repo": repo,
        },
        "user": {"login": FIXTURE_BOT, "type": "Bot"},
    }


def fixture_issue_records(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    metadata = parse_metadata(payload["pull_request"]["body"])
    epic_value = metadata.get("Epic", "")
    epic_number = int(epic_value[1:]) if re.fullmatch(r"#[1-9][0-9]*", epic_value) else None
    # Release fixtures reuse the same module Issues as their Stage fixtures;
    # `Epic: release` is not itself an Issue, while child bodies keep their
    # real module Parent Epic declaration.
    parent_epic_number = epic_number if epic_number is not None else 40
    records: dict[int, dict[str, Any]] = {}
    if epic_number is not None:
        records[epic_number] = {
            "number": epic_number,
            "node_id": f"ISSUE_NODE_{epic_number}",
            "title": f"Module Epic {epic_number}",
            "state": "open",
            "created_at": "2026-08-20T00:00:00Z",
            "body": "",
            "labels": [{"name": "type:epic"}],
            ISSUE_LAST_EDITED_KEY: None,
            ISSUE_MANAGED_EVENTS_KEY: [],
        }
    related_numbers = _issue_numbers(metadata.get("Included-Issues", ""))
    deferred_value = metadata.get("Deferred-Issues", "")
    if deferred_value != "none":
        related_numbers.update(_issue_numbers(deferred_value))
    for number in related_numbers:
        body = f"### Parent Epic\n\n#{parent_epic_number}\n"
        records[number] = {
            "number": number,
            "node_id": f"ISSUE_NODE_{number}",
            "title": f"Issue {number}",
            "state": "open",
            "created_at": "2026-08-20T00:00:00Z",
            "body": body,
            "labels": [{"name": "type:bug"}],
            ISSUE_LAST_EDITED_KEY: None,
            ISSUE_MANAGED_EVENTS_KEY: [],
        }
    return records


def self_test(policy: dict[str, Any]) -> list[str]:
    fixture_today = date(2026, 8, 25)
    test_policy = json.loads(json.dumps(policy))
    test_policy["pr_author"]["allowed_bot_logins"] = [FIXTURE_BOT]
    test_policy["governance_contract"]["release_preparation"]["state"] = "active"

    def run_fixture(
        payload: dict[str, Any],
        changes: ChangeSet,
        **kwargs: Any,
    ) -> ValidationResult:
        evidence = kwargs.pop("contract_evidence", fixture_contract_evidence(payload))
        preparation_record = kwargs.pop(
            "release_preparation_record",
            fixture_release_preparation_record(payload, evidence),
        )
        governed_changes = with_contract_change(changes, evidence)
        return validate(
            payload,
            governed_changes,
            test_policy,
            issue_records=fixture_issue_records(payload),
            scope_freeze_valid=True,
            contract_evidence=evidence,
            release_preparation_record=preparation_record,
            today=fixture_today,
            **kwargs,
        )

    feature_payload = fixture_payload("develop", "codex/dna-v1", "feature")
    feature = run_fixture(feature_payload, _fixture_changes())
    release_payload = fixture_payload(
        "main",
        "develop",
        "release",
        epic="release",
        stage="release@v1",
        sunset="not-applicable",
        rollback="release-merge",
        scope=FIXTURE_HEAD,
    )
    release = run_fixture(release_payload, _fixture_changes())
    integration_payload = fixture_payload(
        "develop", "codex/integration-dna-v1", "integration"
    )
    integration = run_fixture(integration_payload, _fixture_changes())

    errors = [f"valid feature fixture: {item}" for item in feature.errors]
    errors.extend(f"valid release fixture: {item}" for item in release.errors)
    errors.extend(f"valid integration fixture: {item}" for item in integration.errors)

    reused = run_fixture(
        feature_payload,
        _fixture_changes(),
        prior_branch_prs=[{"number": 12, "merged_at": "2026-08-24T00:00:00Z"}],
    )
    if not any("already used" in item for item in reused.errors):
        errors.append("branch-reuse fixture did not reject a previously used topic branch")

    oversized = fixture_payload("develop", "codex/dna-v1", "feature")
    oversized["pull_request"]["body"] = oversized["pull_request"]["body"].replace(
        "Included-Issues: #42",
        "Included-Issues: #42, #44, #45, #46",
    )
    threshold = run_fixture(
        oversized,
        _fixture_changes(31, 50),
    )
    if len(threshold.warnings) != 3:
        errors.append("scope-threshold fixture did not emit all three explainable warnings")
    if not any("Scope Threshold Explanation" in item for item in threshold.errors):
        errors.append("scope-threshold fixture did not require a substantive explanation")

    stale = fixture_payload("develop", "codex/dna-v1", "feature")
    stale["pull_request"]["head"]["sha"] = "d" * 40
    stale_result = run_fixture(
        stale,
        _fixture_changes(),
    )
    if not any("Evidence-Baseline" in item for item in stale_result.errors):
        errors.append("stale-evidence fixture did not bind evidence to the current head")

    long_lived = fixture_payload(
        "develop",
        "codex/integration-dna-v1",
        "integration",
        sunset="2026-09-20",
    )
    long_lived_result = run_fixture(
        long_lived,
        _fixture_changes(),
    )
    if not any("exceeds 14 days" in item for item in long_lived_result.errors):
        errors.append("integration fixture did not enforce the fourteen-day sunset")

    rollout_block = validate(
        feature_payload,
        with_contract_change(
            _fixture_changes(), fixture_contract_evidence(feature_payload)
        ),
        policy,
        issue_records=fixture_issue_records(feature_payload),
        scope_freeze_valid=True,
        contract_evidence=fixture_contract_evidence(feature_payload),
        today=fixture_today,
    )
    if not any("allowlist is empty" in item for item in rollout_block.errors):
        errors.append("empty production Bot allowlist did not fail closed")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--event", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    policy = load_json(args.policy.resolve())
    if args.self_test:
        errors = self_test(policy)
        warnings: list[str] = []
    else:
        event_value = os.environ.get("GITHUB_EVENT_PATH", "")
        if args.event is None and not event_value:
            raise SystemExit("GITHUB_EVENT_PATH or --event is required")
        event_path = args.event or Path(event_value)
        payload = load_json(event_path.resolve())
        try:
            payload = refresh_pull_payload(payload, policy)
            changes = collect_changes(payload, policy)
            contract = fetch_contract_evidence(payload["pull_request"], changes, policy)
            release_preparation_record = collect_release_preparation_record(
                contract, policy
            )
            prepared_release = fetch_prepared_release(
                payload["pull_request"], changes, policy
            )
            metadata = contract_metadata(contract, payload["pull_request"])
            prior = prior_branch_prs(payload, policy)
            same_head = open_prs_sharing_head(payload, policy)
            snapshot_invalidations = issue_snapshot_invalidations(
                payload["pull_request"], policy
            )
            remote_rules = active_branch_rules(payload["pull_request"], policy)
            freeze_valid = scope_freeze_is_ancestor(payload, metadata, policy)
            issues = collect_issue_records(metadata, policy)
            validation = validate(
                payload,
                changes,
                policy,
                prior_branch_prs=prior,
                scope_freeze_valid=freeze_valid,
                issue_records=issues,
                contract_evidence=contract,
                same_head_open_prs=same_head,
                issue_snapshot_invalidation_records=snapshot_invalidations,
                release_preparation_record=release_preparation_record,
            )
            validation.errors.extend(
                remote_merge_gate_errors(
                    payload["pull_request"], remote_rules, policy
                )
            )
            if prepared_release is not None:
                release_metadata = contract_metadata(
                    prepared_release.contract, prepared_release.target_pull
                )
                release_issues = collect_issue_records(release_metadata, policy)
                release_duplicates = open_prs_sharing_head(
                    {"pull_request": prepared_release.target_pull}, policy
                )
                release_snapshot_invalidations = issue_snapshot_invalidations(
                    prepared_release.target_pull, policy
                )
                release_validation = validate_prepared_release(
                    prepared_release,
                    changes,
                    policy,
                    issue_records=release_issues,
                    same_head_open_prs=release_duplicates,
                    issue_snapshot_invalidation_records=(
                        release_snapshot_invalidations
                    ),
                )
                release_rules = active_branch_rules(
                    prepared_release.target_pull, policy
                )
                release_validation.errors.extend(
                    remote_merge_gate_errors(
                        prepared_release.target_pull, release_rules, policy
                    )
                )
                validation.errors.extend(release_validation.errors)
                validation.warnings.extend(release_validation.warnings)
            errors = validation.errors
            warnings = validation.warnings
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            errors = [f"governance evidence collection failed: {exc}"]
            warnings = []

    for warning in warnings:
        print(f"::warning::{warning}")
    for error in errors:
        print(f"::error::{error}")
    if errors:
        return 1
    print("PR governance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
