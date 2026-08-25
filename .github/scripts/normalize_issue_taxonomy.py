"""Normalize one live Issue's canonical area from its Issue Form body.

This helper runs only from the trusted default-branch ``issues`` workflow.  It
reads the current Issue through the API, treats the webhook payload as identity
data only, and never fetches or executes pull-request contents.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "governance-policy.json"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_AREA_RE = re.compile(r"^area:[a-z][a-z0-9-]*$")
_AUXILIARY_PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]*:$")
_ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_OPEN_FENCE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(?:[^`~].*)?$")


class TaxonomyError(ValueError):
    """The policy, webhook identity, Issue body, or live labels are unsafe."""


@dataclass(frozen=True)
class IssueTaxonomy:
    canonical_areas: tuple[str, ...]
    primary_area_heading: str
    auxiliary_label_prefixes: tuple[str, ...]
    sync_mode: str


@dataclass(frozen=True)
class NormalizationResult:
    issue_number: int
    primary_area: str
    added: bool
    removed: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.added or bool(self.removed)


RequestJson = Callable[[str, str, dict[str, Any] | None], Any]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TaxonomyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )
    if not isinstance(value, dict):
        raise TaxonomyError(f"{path} must contain one JSON object")
    return value


def issue_taxonomy(policy: dict[str, Any]) -> IssueTaxonomy:
    raw = policy.get("issue_taxonomy")
    if not isinstance(raw, dict):
        raise TaxonomyError("policy issue_taxonomy must be an object")

    canonical = raw.get("canonical_areas")
    if (
        not isinstance(canonical, list)
        or len(canonical) != 7
        or not all(isinstance(item, str) and _AREA_RE.fullmatch(item) for item in canonical)
        or len(set(canonical)) != len(canonical)
    ):
        raise TaxonomyError(
            "policy issue_taxonomy.canonical_areas must contain seven unique canonical area labels"
        )

    heading = raw.get("primary_area_heading")
    if heading != "Primary Area":
        raise TaxonomyError(
            "policy issue_taxonomy.primary_area_heading must be 'Primary Area'"
        )

    prefixes = raw.get("auxiliary_label_prefixes")
    if (
        not isinstance(prefixes, list)
        or not prefixes
        or not all(
            isinstance(item, str) and _AUXILIARY_PREFIX_RE.fullmatch(item)
            for item in prefixes
        )
        or len(set(prefixes)) != len(prefixes)
        or "area:" in prefixes
    ):
        raise TaxonomyError(
            "policy issue_taxonomy.auxiliary_label_prefixes must be unique non-area label prefixes"
        )

    sync_mode = raw.get("sync_mode")
    if sync_mode != "trusted-issues-workflow":
        raise TaxonomyError(
            "policy issue_taxonomy.sync_mode must be 'trusted-issues-workflow'"
        )

    return IssueTaxonomy(
        canonical_areas=tuple(canonical),
        primary_area_heading=heading,
        auxiliary_label_prefixes=tuple(prefixes),
        sync_mode=sync_mode,
    )


def _markdown_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    headings: list[tuple[int, int, str]] = []
    fence_character: str | None = None
    fence_length = 0
    for index, line in enumerate(lines):
        if fence_character is not None:
            close = re.fullmatch(
                rf"[ ]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                line,
            )
            if close:
                fence_character = None
                fence_length = 0
            continue

        fence = _OPEN_FENCE_RE.fullmatch(line)
        if fence:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue

        heading = _ATX_HEADING_RE.fullmatch(line)
        if not heading:
            continue
        title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
        headings.append((index, len(heading.group(1)), title))
    return headings


def parse_primary_area(body: str, taxonomy: IssueTaxonomy) -> str:
    if not isinstance(body, str) or not body.strip():
        raise TaxonomyError("Issue body is empty; exactly one Primary Area section is required")

    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings = _markdown_headings(lines)
    candidates = [
        heading for heading in headings if heading[2] == taxonomy.primary_area_heading
    ]
    if len(candidates) != 1 or candidates[0][1] != 3:
        raise TaxonomyError(
            "Issue body must contain exactly one level-3 '### Primary Area' heading"
        )

    start, _level, _title = candidates[0]
    end = len(lines)
    for line_index, level, _other_title in headings:
        if line_index > start and level <= 3:
            end = line_index
            break
    values = [line.strip() for line in lines[start + 1 : end] if line.strip()]
    if len(values) != 1 or values[0] not in taxonomy.canonical_areas:
        raise TaxonomyError(
            "Primary Area section must contain exactly one canonical area label"
        )
    return values[0]


def _label_names(issue: dict[str, Any], taxonomy: IssueTaxonomy) -> list[str]:
    raw_labels = issue.get("labels")
    if not isinstance(raw_labels, list):
        raise TaxonomyError("live Issue labels must be a list")
    names: list[str] = []
    for raw in raw_labels:
        name = raw if isinstance(raw, str) else raw.get("name") if isinstance(raw, dict) else None
        if not isinstance(name, str) or not name:
            raise TaxonomyError("live Issue contains a malformed label")
        names.append(name)
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        raise TaxonomyError("live Issue contains duplicate label names")

    canonical_folded = {name.casefold(): name for name in taxonomy.canonical_areas}
    for name in names:
        if not name.casefold().startswith("area:"):
            # Auxiliary-prefix and unclassified labels are both intentionally
            # preserved. The configured prefixes document the governed
            # non-area namespace without turning this synchronizer into an
            # authority for unrelated labels.
            _ = any(name.startswith(prefix) for prefix in taxonomy.auxiliary_label_prefixes)
            continue
        canonical = canonical_folded.get(name.casefold())
        if canonical is None:
            raise TaxonomyError(f"live Issue has non-canonical area label: {name}")
        if name != canonical:
            raise TaxonomyError(f"live Issue area label has non-canonical spelling: {name}")
    return names


def _api_base(policy: dict[str, Any]) -> str:
    evidence = policy.get("api_evidence")
    value = evidence.get("github_api") if isinstance(evidence, dict) else None
    if not isinstance(value, str):
        raise TaxonomyError("policy api_evidence.github_api is missing")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise TaxonomyError("policy api_evidence.github_api is not a safe HTTPS base URL")
    return value.rstrip("/")


def _github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise TaxonomyError("GITHUB_TOKEN is required")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "autofigure-issue-taxonomy",
    }


def _github_request_json(
    method: str, url: str, payload: dict[str, Any] | None = None
) -> Any:
    data = None
    headers = _github_headers()
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} failed: {exc.code} {detail}") from exc
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)


def _live_issue(
    request_json: RequestJson,
    issue_url: str,
    issue_number: int,
    repository_url: str,
) -> dict[str, Any]:
    value = request_json("GET", issue_url, None)
    if not isinstance(value, dict):
        raise TaxonomyError("GitHub Issue response must be an object")
    if value.get("number") != issue_number:
        raise TaxonomyError("GitHub Issue response number does not match the event")
    if value.get("repository_url") != repository_url:
        raise TaxonomyError("GitHub Issue response repository identity is wrong")
    if "pull_request" in value:
        raise TaxonomyError("pull requests are not Issue taxonomy targets")
    return value


def normalize_issue(
    payload: dict[str, Any],
    policy: dict[str, Any],
    *,
    request_json: RequestJson = _github_request_json,
) -> NormalizationResult:
    taxonomy = issue_taxonomy(policy)
    repository = policy.get("repository")
    if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
        raise TaxonomyError("policy repository is invalid")
    event_repository = payload.get("repository")
    if not isinstance(event_repository, dict) or event_repository.get("full_name") != repository:
        raise TaxonomyError("Issue event repository does not match governance policy")
    event_issue = payload.get("issue")
    issue_number = event_issue.get("number") if isinstance(event_issue, dict) else None
    if type(issue_number) is not int or issue_number < 1:
        raise TaxonomyError("Issue event has no positive issue.number")

    base = _api_base(policy)
    repository_url = f"{base}/repos/{repository}"
    issue_url = f"{repository_url}/issues/{issue_number}"
    issue = _live_issue(request_json, issue_url, issue_number, repository_url)
    event_node_id = event_issue.get("node_id")
    if event_node_id is not None and issue.get("node_id") != event_node_id:
        raise TaxonomyError("live Issue identity does not match the webhook node_id")

    selected = parse_primary_area(issue.get("body"), taxonomy)
    labels = _label_names(issue, taxonomy)
    canonical_present = [name for name in labels if name in taxonomy.canonical_areas]
    added = selected not in canonical_present
    removed = tuple(name for name in canonical_present if name != selected)

    if added:
        value = request_json("POST", f"{issue_url}/labels", {"labels": [selected]})
        if not isinstance(value, list):
            raise TaxonomyError("GitHub add-label response must be a label list")
    for label in removed:
        encoded = urllib.parse.quote(label, safe="")
        value = request_json("DELETE", f"{issue_url}/labels/{encoded}", None)
        if not isinstance(value, list):
            raise TaxonomyError("GitHub remove-label response must be a label list")

    if added or removed:
        verified = _live_issue(request_json, issue_url, issue_number, repository_url)
        verified_selected = parse_primary_area(verified.get("body"), taxonomy)
        verified_labels = _label_names(verified, taxonomy)
        verified_areas = [
            name for name in verified_labels if name in taxonomy.canonical_areas
        ]
        if verified_selected != selected or verified_areas != [selected]:
            raise TaxonomyError(
                "Issue changed during taxonomy synchronization or mutation did not converge"
            )

    return NormalizationResult(
        issue_number=issue_number,
        primary_area=selected,
        added=added,
        removed=removed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--event", type=Path)
    args = parser.parse_args(argv)
    event_value = os.environ.get("GITHUB_EVENT_PATH", "")
    if args.event is None and not event_value:
        print("::error::Issue taxonomy failed closed: GITHUB_EVENT_PATH is required")
        return 1
    event_path = args.event or Path(event_value)
    try:
        policy = load_json(args.policy.resolve())
        payload = load_json(event_path.resolve())
        result = normalize_issue(payload, policy)
    except (OSError, RuntimeError, TaxonomyError, json.JSONDecodeError) as exc:
        print(f"::error::Issue taxonomy failed closed: {exc}")
        return 1

    if result.changed:
        removed = ", ".join(result.removed) if result.removed else "none"
        print(
            f"Issue #{result.issue_number} taxonomy normalized: "
            f"primary={result.primary_area}; added={result.added}; removed={removed}"
        )
    else:
        print(
            f"Issue #{result.issue_number} taxonomy already canonical: "
            f"primary={result.primary_area}; no label mutation"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
