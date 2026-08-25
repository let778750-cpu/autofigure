"""Tests for trusted, convergent Issue taxonomy synchronization."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "normalize_issue_taxonomy.py"
SPEC = importlib.util.spec_from_file_location("autofigure_issue_taxonomy", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
taxonomy_script = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = taxonomy_script
SPEC.loader.exec_module(taxonomy_script)

POLICY = json.loads(
    (ROOT / ".github" / "governance-policy.json").read_text(encoding="utf-8")
)
REPOSITORY_URL = f"{POLICY['api_evidence']['github_api']}/repos/{POLICY['repository']}"


def _body(area: str = "area:microasset-fidelity") -> str:
    return f"""### Primary Area

{area}

### Parent Epic

#40
"""


def _event(*, action: str = "edited", node_id: str = "I_kwDO_example") -> dict:
    return {
        "action": action,
        "repository": {"id": 778750, "full_name": POLICY["repository"]},
        "issue": {"number": 42, "node_id": node_id},
    }


class FakeIssueApi:
    def __init__(self, *, body: str, labels: list[str]) -> None:
        self.issue = {
            "number": 42,
            "node_id": "I_kwDO_example",
            "repository_url": REPOSITORY_URL,
            "body": body,
            "labels": [{"name": name} for name in labels],
        }
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, payload: dict | None):
        self.calls.append((method, url, copy.deepcopy(payload)))
        if method == "GET":
            return copy.deepcopy(self.issue)
        if method == "POST" and url.endswith("/labels"):
            for name in payload["labels"]:
                if name not in self.label_names:
                    self.issue["labels"].append({"name": name})
            return copy.deepcopy(self.issue["labels"])
        if method == "DELETE" and "/labels/" in url:
            name = urllib.parse.unquote(url.rsplit("/", 1)[1])
            self.issue["labels"] = [
                label for label in self.issue["labels"] if label["name"] != name
            ]
            return copy.deepcopy(self.issue["labels"])
        raise AssertionError(f"unexpected API request: {method} {url} {payload}")

    @property
    def label_names(self) -> list[str]:
        return [label["name"] for label in self.issue["labels"]]


def test_policy_declares_one_complete_canonical_taxonomy() -> None:
    taxonomy = taxonomy_script.issue_taxonomy(POLICY)
    assert taxonomy.canonical_areas == (
        "area:visual-grammar",
        "area:typography",
        "area:member-geometry",
        "area:microasset-fidelity",
        "area:asset-representation",
        "area:qa-repair",
        "area:route-parity",
    )
    assert taxonomy.primary_area_heading == "Primary Area"
    assert taxonomy.sync_mode == "trusted-issues-workflow"
    assert "area:" not in taxonomy.auxiliary_label_prefixes
    assert {"type:", "topic:", "component:", "case:", "status:", "target:"}.issubset(
        taxonomy.auxiliary_label_prefixes
    )


def test_parser_accepts_exact_h3_and_ignores_heading_text_inside_fence() -> None:
    taxonomy = taxonomy_script.issue_taxonomy(POLICY)
    body = """```text
### Primary Area
area:typography
```

### Primary Area

area:route-parity

### Evidence

Bound evidence.
"""
    assert taxonomy_script.parse_primary_area(body, taxonomy) == "area:route-parity"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("### Evidence\n\nnone\n", "exactly one level-3"),
        (
            "### Primary Area\n\narea:qa-repair\n\n### Primary Area\n\narea:typography\n",
            "exactly one level-3",
        ),
        ("## Primary Area\n\narea:qa-repair\n", "exactly one level-3"),
        ("### Primary Area\n\narea:not-canonical\n", "exactly one canonical"),
        (
            "### Primary Area\n\narea:qa-repair\narea:typography\n",
            "exactly one canonical",
        ),
    ],
)
def test_parser_fails_closed_on_ambiguous_or_noncanonical_body(
    body: str, message: str
) -> None:
    taxonomy = taxonomy_script.issue_taxonomy(POLICY)
    with pytest.raises(taxonomy_script.TaxonomyError, match=message):
        taxonomy_script.parse_primary_area(body, taxonomy)


def test_normalizer_preserves_other_labels_and_replaces_only_canonical_areas() -> None:
    api = FakeIssueApi(
        body=_body(),
        labels=[
            "type:defect",
            "topic:reference-fidelity",
            "custom-review-label",
            "area:typography",
        ],
    )

    result = taxonomy_script.normalize_issue(_event(), POLICY, request_json=api)

    assert result.changed is True
    assert result.added is True
    assert result.removed == ("area:typography",)
    assert api.label_names == [
        "type:defect",
        "topic:reference-fidelity",
        "custom-review-label",
        "area:microasset-fidelity",
    ]
    assert [method for method, _url, _payload in api.calls] == [
        "GET",
        "POST",
        "DELETE",
        "GET",
    ]
    assert api.calls[1][2] == {"labels": ["area:microasset-fidelity"]}
    assert api.calls[2][1].endswith("/labels/area%3Atypography")


@pytest.mark.parametrize("action", ["labeled", "unlabeled", "edited", "opened"])
def test_canonical_issue_is_idempotent_for_own_label_event(action: str) -> None:
    api = FakeIssueApi(
        body=_body("area:qa-repair"),
        labels=["status:triage", "area:qa-repair"],
    )

    result = taxonomy_script.normalize_issue(
        _event(action=action), POLICY, request_json=api
    )

    assert result.changed is False
    assert result.added is False
    assert result.removed == ()
    assert [method for method, _url, _payload in api.calls] == ["GET"]


def test_invalid_body_never_mutates_labels() -> None:
    api = FakeIssueApi(
        body="### Primary Area\n\narea:unknown\n",
        labels=["type:defect", "area:typography"],
    )
    with pytest.raises(taxonomy_script.TaxonomyError, match="exactly one canonical"):
        taxonomy_script.normalize_issue(_event(), POLICY, request_json=api)
    assert [method for method, _url, _payload in api.calls] == ["GET"]
    assert api.label_names == ["type:defect", "area:typography"]


def test_unknown_area_label_fails_closed_without_overwriting_it() -> None:
    api = FakeIssueApi(
        body=_body("area:qa-repair"),
        labels=["type:defect", "area:unregistered"],
    )
    with pytest.raises(taxonomy_script.TaxonomyError, match="non-canonical area"):
        taxonomy_script.normalize_issue(_event(), POLICY, request_json=api)
    assert [method for method, _url, _payload in api.calls] == ["GET"]
    assert api.label_names == ["type:defect", "area:unregistered"]


def test_policy_and_webhook_identity_fail_closed_before_api_mutation() -> None:
    bad_policy = copy.deepcopy(POLICY)
    bad_policy["issue_taxonomy"]["canonical_areas"].pop()
    api = FakeIssueApi(body=_body(), labels=[])
    with pytest.raises(taxonomy_script.TaxonomyError, match="seven unique"):
        taxonomy_script.normalize_issue(_event(), bad_policy, request_json=api)
    assert api.calls == []

    wrong_repo = _event()
    wrong_repo["repository"]["full_name"] = "attacker/fork"
    with pytest.raises(taxonomy_script.TaxonomyError, match="repository"):
        taxonomy_script.normalize_issue(wrong_repo, POLICY, request_json=api)
    assert api.calls == []


def test_live_issue_node_identity_must_match_webhook() -> None:
    api = FakeIssueApi(body=_body(), labels=[])
    with pytest.raises(taxonomy_script.TaxonomyError, match="node_id"):
        taxonomy_script.normalize_issue(
            _event(node_id="I_kwDO_different"), POLICY, request_json=api
        )
    assert [method for method, _url, _payload in api.calls] == ["GET"]
