"""Fail-closed source admission gate for Autofigure schema 4.0.

The gate is intentionally independent from ``prepare``/``ingest`` so callers can
run it against a staged candidate before any case truth file is mutated.  It
classifies findings as:

``accept``
    The source is safe to normalize into the canonical scene.
``repair``
    Identity is intact, but deterministic or reviewed source repair is required.
``reject``
    The source has an identity, provenance, security, or ambiguity violation and
    must not be used as a construction source.

The returned document is JSON serializable and is the complete payload expected
at ``qa/source-gate-report.json``.  Writing the report is a separate atomic step.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


SCHEMA_VERSION = "4.0.0"
REPORT_KIND = "source_gate_report"

INPUT_ROUTES = frozenset({"reference-only", "svg-seeded"})
CANDIDATE_ROLES = frozenset(
    {"external-seed", "reconstruction-candidate", "repair-candidate"}
)
SEED_GATE_STATUSES = frozenset({"awaiting", "required", "accepted", "rejected", "forbidden"})

DECISION_ORDER = {"accept": 0, "repair": 1, "reject": 2}
CHECK_CATEGORIES = (
    "source",
    "route",
    "hash",
    "canvas",
    "image",
    "unsupported_feature",
    "semantic_metadata",
)

SUPPORTED_SVG_TAGS = frozenset(
    {
        "svg",
        "defs",
        "marker",
        "linearGradient",
        "stop",
        "g",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "path",
        "text",
        "tspan",
        "image",
        "title",
        "desc",
        "metadata",
    }
)
REPAIRABLE_SVG_TAGS = frozenset(
    {
        "style",
        "use",
        "symbol",
        "clipPath",
        "mask",
        "filter",
        "pattern",
        "radialGradient",
    }
)
REJECTED_SVG_TAGS = frozenset(
    {
        "script",
        "foreignObject",
        "animate",
        "animateMotion",
        "animateTransform",
        "set",
        "audio",
        "video",
        "canvas",
        "iframe",
    }
)
DRAWABLE_SVG_TAGS = frozenset(
    {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "image", "use"}
)
DEFINITION_CONTAINERS = frozenset(
    {"defs", "marker", "clipPath", "mask", "filter", "pattern", "symbol"}
)

DEFAULT_REQUIRED_SEMANTIC_FIELDS = (
    "semantic_schema_version",
    "reference_sha256",
    "object_inventory_sha256",
    "stable_element_ids",
    "relations_exhaustive",
)
ROOT_METADATA_ATTRIBUTES = {
    "semantic_schema_version": "data-source-schema-version",
    "reference_sha256": "data-reference-sha256",
    "object_inventory_sha256": "data-object-inventory-sha256",
    "stable_element_ids": "data-stable-element-ids",
    "relations_exhaustive": "data-relations-exhaustive",
    "case": "data-case",
}

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIMENSION = re.compile(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*(?:px)?\s*$", re.IGNORECASE)
_EXTERNAL_URL = re.compile(
    r"(?:^|url\(\s*['\"]?)(?:https?:|file:|ftp:|blob:|javascript:|//|[a-zA-Z]:[\\/])",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _namespace(value: str) -> str | None:
    if value.startswith("{") and "}" in value:
        return value[1:].split("}", 1)[0]
    return None


def _normalized_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _normalized_metadata_value(field: str, value: object) -> object:
    if field in {"stable_element_ids", "relations_exhaustive"}:
        normalized = _normalized_bool(value)
        return value if normalized is None else normalized
    if isinstance(value, str):
        return value.strip()
    return value


def _dimension(value: str | None) -> float | None:
    if value is None:
        return None
    match = _DIMENSION.fullmatch(value)
    return None if match is None else float(match.group(1))


def _is_non_embedded_href(value: str) -> bool:
    """Return true for an href that depends on bytes outside the SVG."""

    lowered = value.strip().casefold()
    return not lowered.startswith("#") and not lowered.startswith("data:image/")


def _view_box(value: str | None) -> list[float] | None:
    if value is None:
        return None
    try:
        parts = [float(item) for item in re.split(r"[\s,]+", value.strip()) if item]
    except ValueError:
        return None
    if len(parts) != 4 or not all(math.isfinite(item) for item in parts):
        return None
    return parts


def _finding(
    category: str,
    decision: str,
    code: str,
    message: str,
    *,
    evidence: Mapping[str, object] | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    if category not in CHECK_CATEGORIES:
        raise ValueError(f"unknown source-gate category: {category}")
    if decision not in DECISION_ORDER:
        raise ValueError(f"unknown source-gate decision: {decision}")
    result: dict[str, Any] = {
        "category": category,
        "decision": decision,
        "code": code,
        "message": message,
    }
    if evidence:
        result["evidence"] = dict(evidence)
    if action:
        result["action"] = action
    return result


def _max_decision(findings: Collection[Mapping[str, object]]) -> str:
    return max(
        (str(item.get("decision", "accept")) for item in findings),
        key=DECISION_ORDER.__getitem__,
        default="accept",
    )


def _normalized_seed_gate_status(input_route: str, status: str | None) -> str:
    if status == "required":
        return "awaiting"
    if status is not None:
        return status
    return "forbidden" if input_route == "reference-only" else "awaiting"


def _route_findings(
    input_route: str,
    candidate_role: str,
    seed_gate_status: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if input_route not in INPUT_ROUTES:
        return [
            _finding(
                "route",
                "reject",
                "source-gate:route:unsupported",
                "input_route must be reference-only or svg-seeded",
                evidence={"input_route": input_route},
            )
        ]
    if candidate_role not in CANDIDATE_ROLES:
        findings.append(
            _finding(
                "route",
                "reject",
                "source-gate:role:unsupported",
                "candidate role is not part of the closed source-role vocabulary",
                evidence={"candidate_role": candidate_role},
            )
        )
        return findings
    if seed_gate_status not in SEED_GATE_STATUSES:
        findings.append(
            _finding(
                "route",
                "reject",
                "source-gate:seed-gate:invalid-status",
                "seed gate status is invalid",
                evidence={"seed_gate_status": seed_gate_status},
            )
        )
        return findings

    if input_route == "reference-only":
        if seed_gate_status != "forbidden":
            findings.append(
                _finding(
                    "route",
                    "reject",
                    "source-gate:seed-gate:reference-only-not-forbidden",
                    "reference-only runs must keep the external seed gate forbidden",
                    evidence={"seed_gate_status": seed_gate_status},
                )
            )
        if candidate_role == "external-seed":
            findings.append(
                _finding(
                    "route",
                    "reject",
                    "source-gate:route:reference-only-external-seed",
                    "reference-only runs cannot admit an external SVG seed",
                )
            )
        return findings

    if seed_gate_status == "forbidden":
        findings.append(
            _finding(
                "route",
                "reject",
                "source-gate:seed-gate:svg-seeded-forbidden",
                "svg-seeded runs cannot use a forbidden seed gate",
            )
        )
    if candidate_role == "external-seed":
        if seed_gate_status == "accepted":
            findings.append(
                _finding(
                    "route",
                    "reject",
                    "source-gate:seed-gate:duplicate-seed",
                    "an svg-seeded run accepts exactly one external seed",
                )
            )
        elif seed_gate_status == "rejected":
            findings.append(
                _finding(
                    "route",
                    "reject",
                    "source-gate:seed-gate:closed-after-rejection",
                    "a rejected immutable seed cannot be replaced in the same case",
                )
            )
    elif seed_gate_status == "awaiting":
        findings.append(
            _finding(
                "route",
                "reject",
                "source-gate:seed-gate:seed-required",
                "the required external seed must be admitted before later candidates",
            )
        )
    elif seed_gate_status == "accepted" and candidate_role == "reconstruction-candidate":
        findings.append(
            _finding(
                "route",
                "reject",
                "source-gate:seed-gate:abandonment-required",
                "switching from an accepted seed to independent reconstruction requires an explicit rejection event",
            )
        )
    elif seed_gate_status == "rejected" and candidate_role != "reconstruction-candidate":
        findings.append(
            _finding(
                "route",
                "reject",
                "source-gate:seed-gate:reconstruction-required",
                "after seed rejection the next candidate must be an independent reconstruction",
            )
        )
    return findings


def _metadata_from_root(root: ET.Element) -> dict[str, object]:
    return {
        field: root.get(attribute)
        for field, attribute in ROOT_METADATA_ATTRIBUTES.items()
        if root.get(attribute) is not None
    }


def _merge_semantic_metadata(
    root: ET.Element | None,
    supplied: Mapping[str, object] | None,
    findings: list[dict[str, Any]],
) -> tuple[dict[str, object], list[str]]:
    root_metadata = {} if root is None else _metadata_from_root(root)
    supplied_metadata = {} if supplied is None else dict(supplied)
    conflicts: list[str] = []
    for field in sorted(root_metadata.keys() & supplied_metadata.keys()):
        root_value = _normalized_metadata_value(field, root_metadata[field])
        supplied_value = _normalized_metadata_value(field, supplied_metadata[field])
        if root_value != supplied_value:
            conflicts.append(field)
            findings.append(
                _finding(
                    "semantic_metadata",
                    "reject",
                    f"source-gate:semantic-metadata:conflict:{field}",
                    "SVG metadata conflicts with the staged semantic manifest",
                    evidence={"field": field},
                )
            )
    merged = {
        field: _normalized_metadata_value(field, value)
        for field, value in {**root_metadata, **supplied_metadata}.items()
    }
    return merged, conflicts


def _semantic_metadata_findings(
    metadata: Mapping[str, object],
    *,
    expected_case: str | None,
    expected_reference_sha256: str,
    expected_inventory_sha256: str | None,
    required_fields: Collection[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    findings: list[dict[str, Any]] = []
    required = list(dict.fromkeys(required_fields))
    if expected_case is not None and "case" not in required:
        required.append("case")
    missing = [field for field in required if metadata.get(field) in (None, "")]
    for field in missing:
        findings.append(
            _finding(
                "semantic_metadata",
                "repair",
                f"source-gate:semantic-metadata:missing:{field}",
                "required source semantic metadata is missing",
                evidence={"field": field},
                action=f"supply verified {field} metadata and rerun the gate",
            )
        )

    invalid: list[str] = []
    for field in ("reference_sha256", "object_inventory_sha256"):
        value = metadata.get(field)
        if value not in (None, "") and (
            not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None
        ):
            invalid.append(field)
            findings.append(
                _finding(
                    "semantic_metadata",
                    "reject",
                    f"source-gate:semantic-metadata:invalid:{field}",
                    "declared semantic identity hash is not a lowercase SHA-256 digest",
                    evidence={"field": field},
                )
            )

    declared_reference = metadata.get("reference_sha256")
    if (
        isinstance(declared_reference, str)
        and _HEX_SHA256.fullmatch(declared_reference)
        and declared_reference != expected_reference_sha256
    ):
        findings.append(
            _finding(
                "hash",
                "reject",
                "source-gate:hash:declared-reference-mismatch",
                "candidate semantic metadata names a different reference image",
                evidence={
                    "expected_reference_sha256": expected_reference_sha256,
                    "declared_reference_sha256": declared_reference,
                },
            )
        )
    declared_inventory = metadata.get("object_inventory_sha256")
    if (
        expected_inventory_sha256 is not None
        and isinstance(declared_inventory, str)
        and _HEX_SHA256.fullmatch(declared_inventory)
        and declared_inventory != expected_inventory_sha256
    ):
        findings.append(
            _finding(
                "hash",
                "reject",
                "source-gate:hash:inventory-mismatch",
                "candidate semantic metadata is bound to a different frozen inventory",
                evidence={
                    "expected_inventory_sha256": expected_inventory_sha256,
                    "declared_inventory_sha256": declared_inventory,
                },
            )
        )
    if expected_case is not None and metadata.get("case") not in (None, "", expected_case):
        findings.append(
            _finding(
                "semantic_metadata",
                "reject",
                "source-gate:semantic-metadata:case-mismatch",
                "candidate semantic metadata names a different case",
                evidence={"expected_case": expected_case, "declared_case": metadata.get("case")},
            )
        )
    schema = metadata.get("semantic_schema_version")
    if schema not in (None, "", SCHEMA_VERSION):
        findings.append(
            _finding(
                "semantic_metadata",
                "repair",
                "source-gate:semantic-metadata:schema-normalization-required",
                "source semantic metadata must be normalized to schema 4.0.0",
                evidence={"declared_schema_version": schema},
                action="normalize semantic metadata to schema 4.0.0",
            )
        )
    for field in ("stable_element_ids", "relations_exhaustive"):
        value = metadata.get(field)
        if value not in (None, "") and value is not True:
            invalid.append(field)
            findings.append(
                _finding(
                    "semantic_metadata",
                    "repair",
                    f"source-gate:semantic-metadata:not-true:{field}",
                    "source semantic closure must be explicitly true",
                    evidence={"field": field, "declared": value},
                    action=f"complete and attest {field}",
                )
            )
    return findings, missing, list(dict.fromkeys(invalid))


def _canvas_findings(
    root: ET.Element | None,
    expected_canvas: tuple[int, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_width, expected_height = expected_canvas
    observed: dict[str, Any] = {
        "width": None,
        "height": None,
        "view_box": None,
    }
    if root is None:
        return [], observed
    width = _dimension(root.get("width"))
    height = _dimension(root.get("height"))
    view_box = _view_box(root.get("viewBox"))
    observed.update({"width": width, "height": height, "view_box": view_box})
    findings: list[dict[str, Any]] = []
    for field, value in (("width", width), ("height", height), ("viewBox", view_box)):
        if value is None:
            findings.append(
                _finding(
                    "canvas",
                    "repair",
                    f"source-gate:canvas:{field.casefold()}-missing-or-invalid",
                    "SVG canvas metadata is missing or cannot be normalized without review",
                    evidence={"field": field},
                    action=f"set an explicit pixel {field} matching the reference canvas",
                )
            )
    if width is not None and not math.isclose(width, expected_width, abs_tol=1e-6):
        findings.append(
            _finding(
                "canvas",
                "reject",
                "source-gate:canvas:width-mismatch",
                "SVG width does not match the frozen reference canvas",
                evidence={"expected": expected_width, "observed": width},
            )
        )
    if height is not None and not math.isclose(height, expected_height, abs_tol=1e-6):
        findings.append(
            _finding(
                "canvas",
                "reject",
                "source-gate:canvas:height-mismatch",
                "SVG height does not match the frozen reference canvas",
                evidence={"expected": expected_height, "observed": height},
            )
        )
    if view_box is not None and any(
        not math.isclose(actual, expected, abs_tol=1e-6)
        for actual, expected in zip(view_box, (0.0, 0.0, float(expected_width), float(expected_height)))
    ):
        findings.append(
            _finding(
                "canvas",
                "reject",
                "source-gate:canvas:viewbox-mismatch",
                "SVG viewBox must preserve the exact frozen reference coordinate system",
                evidence={
                    "expected": [0, 0, expected_width, expected_height],
                    "observed": view_box,
                },
            )
        )
    return findings, observed


def _walk_svg(
    element: ET.Element,
    *,
    in_definition: bool = False,
    transformed: bool = False,
):
    tag = _local_name(element.tag)
    current_definition = in_definition or tag in DEFINITION_CONTAINERS
    current_transformed = transformed or bool(element.get("transform"))
    yield element, tag, current_definition, current_transformed
    for child in element:
        yield from _walk_svg(
            child,
            in_definition=current_definition,
            transformed=current_transformed,
        )


def _feature_and_semantic_findings(
    root: ET.Element | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if root is None:
        return [], [], {"drawable_count": 0, "missing_id_count": 0, "duplicate_ids": []}
    findings: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    ids: list[str] = []
    missing_ids: list[dict[str, str]] = []
    drawable_count = 0
    relation_records: list[tuple[str, str | None, str | None]] = []
    for element, tag, in_definition, _ in _walk_svg(root):
        namespace = _namespace(element.tag)
        element_id = element.get("id")
        if element_id:
            ids.append(element_id)
        if not in_definition and (
            tag in DRAWABLE_SVG_TAGS or element.get("data-semantic-kind") is not None
        ):
            drawable_count += 1
            if not element_id:
                missing_ids.append({"tag": tag, "semantic_kind": element.get("data-semantic-kind", "")})
        source_id = element.get("data-source-id")
        target_id = element.get("data-target-id")
        if source_id is not None or target_id is not None:
            relation_records.append((element_id or f"anonymous-{tag}", source_id, target_id))

        feature_decision: str | None = None
        if namespace not in (None, "http://www.w3.org/2000/svg"):
            feature_decision = "repair"
        elif tag in REJECTED_SVG_TAGS:
            feature_decision = "reject"
        elif tag in REPAIRABLE_SVG_TAGS:
            feature_decision = "repair"
        elif tag not in SUPPORTED_SVG_TAGS:
            feature_decision = "repair"
        if feature_decision is not None:
            record = {
                "tag": tag,
                "element_id": element_id,
                "namespace": namespace,
                "decision": feature_decision,
            }
            features.append(record)
            findings.append(
                _finding(
                    "unsupported_feature",
                    feature_decision,
                    f"source-gate:unsupported-feature:{feature_decision}:{tag}",
                    (
                        "active, foreign, or non-deterministic SVG content is forbidden"
                        if feature_decision == "reject"
                        else "SVG feature must be flattened or normalized before compilation"
                    ),
                    evidence=record,
                    action=(
                        None
                        if feature_decision == "reject"
                        else f"normalize or flatten SVG feature {tag}"
                    ),
                )
            )

        for raw_name, raw_value in element.attrib.items():
            name = _local_name(raw_name).casefold()
            value = str(raw_value)
            if name.startswith("on"):
                findings.append(
                    _finding(
                        "unsupported_feature",
                        "reject",
                        "source-gate:unsupported-feature:event-handler",
                        "SVG event-handler attributes are forbidden",
                        evidence={"tag": tag, "element_id": element_id, "attribute": name},
                    )
                )
            if _EXTERNAL_URL.search(value):
                findings.append(
                    _finding(
                        "unsupported_feature",
                        "reject",
                        "source-gate:unsupported-feature:external-reference",
                        "external URL or filesystem references are forbidden",
                        evidence={"tag": tag, "element_id": element_id, "attribute": name},
                    )
                )
            elif name == "href" and _is_non_embedded_href(value):
                findings.append(
                    _finding(
                        "unsupported_feature",
                        "reject",
                        "source-gate:unsupported-feature:non-embedded-reference",
                        "relative and non-embedded href references are forbidden",
                        evidence={"tag": tag, "element_id": element_id, "attribute": name},
                    )
                )
        if element.text and (
            _EXTERNAL_URL.search(element.text) or "@import" in element.text.casefold()
        ):
            findings.append(
                _finding(
                    "unsupported_feature",
                    "reject",
                    "source-gate:unsupported-feature:external-style-reference",
                    "embedded style or metadata text cannot import external content",
                    evidence={"tag": tag, "element_id": element_id},
                )
            )
    split_arrow_groups: list[str] = []
    for group in root.iter():
        if _local_name(group.tag) != "g":
            continue
        direct_drawables = [
            child
            for child in group
            if _local_name(child.tag) in DRAWABLE_SVG_TAGS
        ]
        direct_tags = [_local_name(child.tag) for child in direct_drawables]
        if len(direct_tags) == 2 and "line" in direct_tags and any(
            tag in direct_tags for tag in ("polyline", "polygon")
        ):
            split_arrow_groups.append(group.get("id") or "anonymous-g")
    if split_arrow_groups:
        findings.append(
            _finding(
                "semantic_metadata",
                "repair",
                "source-gate:semantic-metadata:split-arrow-composition",
                "a logical arrow cannot be represented by a separate shaft and head",
                evidence={"group_ids": split_arrow_groups},
                action="replace each split arrow with one native-compilable logical object",
            )
        )
    if drawable_count == 0:
        findings.append(
            _finding(
                "source",
                "reject",
                "source-gate:source:empty-drawing",
                "SVG has no visible drawable elements",
            )
        )
    if missing_ids:
        findings.append(
            _finding(
                "semantic_metadata",
                "repair",
                "source-gate:semantic-metadata:missing-element-ids",
                "every visible semantic object needs a stable element id",
                evidence={"count": len(missing_ids), "elements": missing_ids},
                action="assign stable case-neutral ids before normalization",
            )
        )
    duplicate_ids = sorted({value for value in ids if ids.count(value) > 1})
    if duplicate_ids:
        findings.append(
            _finding(
                "semantic_metadata",
                "reject",
                "source-gate:semantic-metadata:duplicate-element-ids",
                "duplicate SVG ids make source topology and bindings ambiguous",
                evidence={"element_ids": duplicate_ids},
            )
        )
    identity_set = set(ids)
    invalid_relations: list[dict[str, object]] = []
    for relation_id, source_id, target_id in relation_records:
        missing_endpoints = [
            endpoint
            for endpoint in (source_id, target_id)
            if endpoint is None or endpoint not in identity_set
        ]
        if missing_endpoints:
            invalid_relations.append(
                {
                    "element_id": relation_id,
                    "source_id": source_id,
                    "target_id": target_id,
                    "missing_endpoints": missing_endpoints,
                }
            )
    if invalid_relations:
        findings.append(
            _finding(
                "semantic_metadata",
                "repair",
                "source-gate:semantic-metadata:invalid-relation-endpoints",
                "declared semantic relations must bind two existing stable element ids",
                evidence={"relations": invalid_relations},
                action="repair relation endpoints against the frozen object inventory",
            )
        )
    semantic = {
        "drawable_count": drawable_count,
        "missing_id_count": len(missing_ids),
        "duplicate_ids": duplicate_ids,
        "declared_relation_count": len(relation_records),
        "invalid_relation_count": len(invalid_relations),
        "split_arrow_group_count": len(split_arrow_groups),
    }
    return findings, features, semantic


def _image_findings(
    root: ET.Element | None,
    *,
    expected_canvas: tuple[int, int],
    authorized_image_ids: Collection[str],
    image_max_area_ratio: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    total_ratio = 0.0
    authorized = set(authorized_image_ids)
    if root is not None:
        canvas_area = float(expected_canvas[0] * expected_canvas[1])
        for element, tag, _, transformed in _walk_svg(root):
            if tag != "image":
                continue
            element_id = element.get("id")
            href = element.get("href") or element.get("{http://www.w3.org/1999/xlink}href")
            width = _dimension(element.get("width"))
            height = _dimension(element.get("height"))
            ratio = None if width is None or height is None else width * height / canvas_area
            if ratio is not None:
                total_ratio += ratio
            record = {
                "element_id": element_id,
                "width": width,
                "height": height,
                "area_ratio": None if ratio is None else round(ratio, 8),
                "authorized": element_id in authorized if element_id else False,
                "transformed": transformed,
                "href_kind": (
                    "missing"
                    if not href
                    else "data-image"
                    if href.casefold().startswith("data:image/")
                    else "internal"
                    if href.startswith("#")
                    else "external"
                ),
            }
            records.append(record)
            if href and (_EXTERNAL_URL.search(href) or _is_non_embedded_href(href)):
                findings.append(
                    _finding(
                        "image",
                        "reject",
                        "source-gate:image:external-reference",
                        "image sources must be case-bound and content-addressed",
                        evidence={"element_id": element_id},
                    )
                )
            elif href and href.casefold().startswith("data:image/svg+xml"):
                findings.append(
                    _finding(
                        "image",
                        "reject",
                        "source-gate:image:nested-svg",
                        "nested SVG images can hide active or non-editable vector content",
                        evidence={"element_id": element_id},
                    )
                )
            elif not href:
                findings.append(
                    _finding(
                        "image",
                        "repair",
                        "source-gate:image:missing-source",
                        "image element has no source",
                        evidence={"element_id": element_id},
                        action="remove the image or bind an authorized case-local asset",
                    )
                )
            if ratio is None:
                findings.append(
                    _finding(
                        "image",
                        "repair",
                        "source-gate:image:unmeasurable",
                        "image width and height must be explicit pixel values",
                        evidence={"element_id": element_id},
                        action="declare a tight pixel bounding box",
                    )
                )
            elif ratio >= image_max_area_ratio:
                findings.append(
                    _finding(
                        "image",
                        "reject",
                        "source-gate:image:whole-reference-like",
                        "a raster image covers too much of the canvas to be an atomic microasset",
                        evidence={"element_id": element_id, "area_ratio": round(ratio, 8)},
                    )
                )
            if transformed:
                findings.append(
                    _finding(
                        "image",
                        "repair",
                        "source-gate:image:transformed-bbox",
                        "image coverage cannot be trusted until transforms are flattened",
                        evidence={"element_id": element_id},
                        action="flatten the image transform and remeasure its tight bbox",
                    )
                )
            if not element_id or element_id not in authorized:
                findings.append(
                    _finding(
                        "image",
                        "repair",
                        "source-gate:image:authorization-missing",
                        "raster microassets require an exact id-level authorization",
                        evidence={"element_id": element_id},
                        action="vectorize the object or add a reviewed assets.json authorization",
                    )
                )
    if len(records) > 1 and total_ratio >= image_max_area_ratio:
        findings.append(
            _finding(
                "image",
                "reject",
                "source-gate:image:aggregate-coverage",
                "multiple raster tiles collectively exceed the whole-reference guardrail",
                evidence={"image_count": len(records), "declared_area_ratio": round(total_ratio, 8)},
            )
        )
    return findings, {
        "count": len(records),
        "max_area_ratio": image_max_area_ratio,
        "whole_reference_forbidden": True,
        "declared_area_ratio": round(total_ratio, 8),
        "authorized_image_ids": sorted(authorized),
        "items": records,
    }


def evaluate_source_gate(
    candidate_path: Path,
    *,
    reference_path: Path,
    input_route: str,
    candidate_role: str,
    expected_reference_sha256: str,
    expected_canvas: tuple[int, int],
    semantic_metadata: Mapping[str, object] | None = None,
    expected_case: str | None = None,
    expected_inventory_sha256: str | None = None,
    expected_candidate_sha256: str | None = None,
    seed_gate_status: str | None = None,
    authorized_image_ids: Collection[str] = (),
    required_semantic_fields: Collection[str] = DEFAULT_REQUIRED_SEMANTIC_FIELDS,
    image_max_area_ratio: float = 0.5,
) -> dict[str, Any]:
    """Evaluate one staged SVG without mutating any case truth file.

    ``candidate_path`` and ``reference_path`` are each read once for identity.
    Callers should pass staged, case-bound paths and persist the returned report
    only after this function returns.
    """

    candidate_path = Path(candidate_path)
    reference_path = Path(reference_path)
    if (
        len(expected_canvas) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in expected_canvas)
    ):
        raise ValueError("expected_canvas must contain two positive integer pixel dimensions")
    if not 0 < image_max_area_ratio <= 1:
        raise ValueError("image_max_area_ratio must be in (0, 1]")

    normalized_seed_gate = _normalized_seed_gate_status(input_route, seed_gate_status)
    findings = _route_findings(input_route, candidate_role, normalized_seed_gate)
    candidate_bytes: bytes | None = None
    candidate_sha256: str | None = None
    if not candidate_path.is_file():
        findings.append(
            _finding(
                "source",
                "reject",
                "source-gate:source:candidate-missing",
                "staged candidate file is missing",
            )
        )
    else:
        try:
            candidate_bytes = candidate_path.read_bytes()
            candidate_sha256 = _sha256_bytes(candidate_bytes)
        except OSError as exc:
            findings.append(
                _finding(
                    "source",
                    "reject",
                    "source-gate:source:candidate-unreadable",
                    "staged candidate cannot be read",
                    evidence={"error": type(exc).__name__},
                )
            )
    if expected_candidate_sha256 is not None:
        if _HEX_SHA256.fullmatch(expected_candidate_sha256) is None:
            findings.append(
                _finding(
                    "hash",
                    "reject",
                    "source-gate:hash:invalid-expected-candidate",
                    "staged candidate expectation is not a lowercase SHA-256 digest",
                )
            )
        elif candidate_sha256 is not None and candidate_sha256 != expected_candidate_sha256:
            findings.append(
                _finding(
                    "hash",
                    "reject",
                    "source-gate:hash:candidate-drift",
                    "candidate bytes changed after staging",
                    evidence={
                        "expected_candidate_sha256": expected_candidate_sha256,
                        "actual_candidate_sha256": candidate_sha256,
                    },
                )
            )

    actual_reference_sha256: str | None = None
    reference_canvas: list[int] | None = None
    if not reference_path.is_file():
        findings.append(
            _finding(
                "hash",
                "reject",
                "source-gate:hash:reference-missing",
                "case-bound reference image is missing",
            )
        )
    else:
        try:
            reference_bytes = reference_path.read_bytes()
            actual_reference_sha256 = _sha256_bytes(reference_bytes)
            with Image.open(io.BytesIO(reference_bytes)) as image:
                reference_canvas = [int(image.width), int(image.height)]
        except (OSError, ValueError):
            findings.append(
                _finding(
                    "hash",
                    "reject",
                    "source-gate:hash:reference-image-invalid",
                    "reference bytes are not a readable image",
                )
            )
    if _HEX_SHA256.fullmatch(expected_reference_sha256) is None:
        findings.append(
            _finding(
                "hash",
                "reject",
                "source-gate:hash:invalid-expected-reference",
                "run reference identity is not a lowercase SHA-256 digest",
            )
        )
    elif actual_reference_sha256 is not None and actual_reference_sha256 != expected_reference_sha256:
        findings.append(
            _finding(
                "hash",
                "reject",
                "source-gate:hash:reference-drift",
                "reference bytes no longer match the run identity",
                evidence={
                    "expected_reference_sha256": expected_reference_sha256,
                    "actual_reference_sha256": actual_reference_sha256,
                },
            )
        )
    if reference_canvas is not None and reference_canvas != list(expected_canvas):
        findings.append(
            _finding(
                "canvas",
                "reject",
                "source-gate:canvas:reference-mismatch",
                "reference image dimensions no longer match the frozen run canvas",
                evidence={"expected": list(expected_canvas), "observed": reference_canvas},
            )
        )

    root: ET.Element | None = None
    if candidate_bytes is not None:
        lowered = candidate_bytes.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            findings.append(
                _finding(
                    "unsupported_feature",
                    "reject",
                    "source-gate:unsupported-feature:doctype-or-entity",
                    "DTD and entity declarations are forbidden in staged SVG",
                )
            )
        try:
            root = ET.fromstring(candidate_bytes)
        except ET.ParseError as exc:
            findings.append(
                _finding(
                    "source",
                    "reject",
                    "source-gate:source:invalid-xml",
                    "candidate is not well-formed XML",
                    evidence={"error": str(exc)},
                )
            )
        if root is not None and _local_name(root.tag) != "svg":
            findings.append(
                _finding(
                    "source",
                    "reject",
                    "source-gate:source:not-svg",
                    "source gate currently admits only SVG construction sources",
                    evidence={"root_tag": _local_name(root.tag)},
                )
            )

    canvas_findings, observed_canvas = _canvas_findings(root, expected_canvas)
    findings.extend(canvas_findings)
    merged_metadata, metadata_conflicts = _merge_semantic_metadata(
        root, semantic_metadata, findings
    )
    semantic_findings, missing_metadata, invalid_metadata = _semantic_metadata_findings(
        merged_metadata,
        expected_case=expected_case,
        expected_reference_sha256=expected_reference_sha256,
        expected_inventory_sha256=expected_inventory_sha256,
        required_fields=required_semantic_fields,
    )
    findings.extend(semantic_findings)
    feature_findings, unsupported_features, semantic_structure = (
        _feature_and_semantic_findings(root)
    )
    findings.extend(feature_findings)
    image_findings, image_usage = _image_findings(
        root,
        expected_canvas=expected_canvas,
        authorized_image_ids=authorized_image_ids,
        image_max_area_ratio=image_max_area_ratio,
    )
    findings.extend(image_findings)

    # A finding can be reached through both generic URL scanning and the image
    # policy.  Keep evidence deterministic without inflating blocker counts.
    unique_findings: list[dict[str, Any]] = []
    seen_finding_keys: set[tuple[str, str, str]] = set()
    for item in findings:
        key = (item["category"], item["decision"], item["code"])
        if key in seen_finding_keys:
            continue
        seen_finding_keys.add(key)
        unique_findings.append(item)
    findings = unique_findings

    decision = _max_decision(findings)
    checks = []
    for category in CHECK_CATEGORIES:
        category_findings = [item for item in findings if item["category"] == category]
        checks.append(
            {
                "id": category,
                "decision": _max_decision(category_findings),
                "finding_codes": [item["code"] for item in category_findings],
                "pass": not category_findings,
            }
        )
    reject_reasons = [item["code"] for item in findings if item["decision"] == "reject"]
    repair_reasons = [item["code"] for item in findings if item["decision"] == "repair"]
    repair_actions = list(
        dict.fromkeys(
            str(item["action"])
            for item in findings
            if item["decision"] == "repair" and item.get("action")
        )
    )
    next_action = {
        "accept": (
            "normalize-seed-to-scene"
            if candidate_role == "external-seed"
            else "normalize-candidate-to-scene"
        ),
        "repair": "repair-source-and-rerun-gate",
        "reject": "reject-source",
    }[decision]
    reported_required_fields = list(dict.fromkeys(required_semantic_fields))
    if expected_case is not None and "case" not in reported_required_fields:
        reported_required_fields.append("case")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "created_at": _utc_now(),
        "decision": decision,
        "pass": decision == "accept",
        "next_action": next_action,
        "route_gate": {
            "input_route": input_route,
            "candidate_role": candidate_role,
            "seed_gate_status": normalized_seed_gate,
        },
        "candidate": {
            "path_base": "staging-root",
            "source_name": candidate_path.name,
            "kind": "svg",
            "sha256": candidate_sha256,
            "expected_sha256": expected_candidate_sha256,
            "byte_count": None if candidate_bytes is None else len(candidate_bytes),
        },
        "reference": {
            "path_base": "case-root",
            "path": reference_path.name,
            "expected_sha256": expected_reference_sha256,
            "actual_sha256": actual_reference_sha256,
            "declared_sha256": merged_metadata.get("reference_sha256"),
            "canvas": reference_canvas,
        },
        "canvas": {
            "expected": {"width": expected_canvas[0], "height": expected_canvas[1]},
            "observed": observed_canvas,
        },
        "image_usage": image_usage,
        "unsupported_features": {
            "count": len(unsupported_features),
            "items": unsupported_features,
        },
        "semantic_metadata": {
            "required_fields": reported_required_fields,
            "observed": merged_metadata,
            "missing_fields": missing_metadata,
            "invalid_fields": invalid_metadata,
            "conflicting_fields": metadata_conflicts,
            "structure": semantic_structure,
        },
        "checks": checks,
        "findings": findings,
        "blockers": [*reject_reasons, *repair_reasons],
        "reject_reasons": reject_reasons,
        "repair_reasons": repair_reasons,
        "repair_actions": repair_actions,
    }


def write_source_gate_report(report: Mapping[str, object], output_path: Path) -> Path:
    """Atomically persist a validated source-gate report."""

    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("source-gate report schema_version must be 4.0.0")
    if report.get("kind") != REPORT_KIND:
        raise ValueError("source-gate report kind is invalid")
    if report.get("decision") not in DECISION_ORDER:
        raise ValueError("source-gate report decision is invalid")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    serialized = json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path


__all__ = [
    "DEFAULT_REQUIRED_SEMANTIC_FIELDS",
    "REPORT_KIND",
    "SCHEMA_VERSION",
    "evaluate_source_gate",
    "write_source_gate_report",
]


def evaluate_case_source_gate(
    run,
    candidate_path: Path,
    *,
    candidate_role: str,
    semantic_metadata: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Evaluate and persist a gate using only one case's frozen contracts."""

    from tools import common
    from tools.contracts import read_json, set_processing_mode

    meta = run.load_meta()
    receipt_path = run.qa_dir / "reference-inventory-receipt.json"
    if not receipt_path.is_file():
        raise ValueError("reference inventory must be frozen before source-gate evaluation")
    receipt = read_json(receipt_path)
    assets = read_json(run.assets_path)
    authorized_image_ids = [
        item["id"]
        for item in assets.get("assets", [])
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item.get("authorized") is True
    ]
    previous = None
    if run.source_gate_report_path.is_file():
        try:
            previous = read_json(run.source_gate_report_path)
        except Exception:
            previous = None
    seed_integrity_blocker: str | None = None
    provenance = read_json(run.provenance_path)
    unavailable_declared = any(
        isinstance(event, dict) and event.get("event") == "seed-unavailable"
        for event in provenance.get("events", [])
    )
    if meta["input_route"] == "reference-only":
        seed_gate_status = "forbidden"
    elif unavailable_declared:
        seed_gate_status = "rejected"
    elif candidate_role == "external-seed":
        seed_gate_status = "awaiting"
    else:
        seed_record = provenance.get("external_svg_seed")
        if isinstance(previous, dict) and previous.get("decision") == "reject":
            seed_gate_status = "rejected"
        elif not run.external_seed_svg.is_file() or not isinstance(seed_record, dict):
            seed_gate_status = "awaiting"
            seed_integrity_blocker = "source-gate:seed:unavailable"
        elif seed_record.get("sha256") != common.sha256_file(run.external_seed_svg):
            seed_gate_status = "awaiting"
            seed_integrity_blocker = "source-gate:seed:hash-mismatch"
        else:
            seed_gate_status = "accepted"
    report = evaluate_source_gate(
        candidate_path,
        reference_path=run.source_png,
        input_route=meta["input_route"],
        candidate_role=candidate_role,
        expected_reference_sha256=meta["source_sha256"],
        expected_canvas=(int(meta["width"]), int(meta["height"])),
        semantic_metadata=semantic_metadata,
        expected_case=meta["case"],
        expected_inventory_sha256=receipt["inventory_sha256"],
        expected_candidate_sha256=common.sha256_file(candidate_path),
        seed_gate_status=seed_gate_status,
        authorized_image_ids=authorized_image_ids,
    )
    report["case"] = meta["case"]
    report["reference_inventory_sha256"] = receipt["inventory_sha256"]
    if seed_integrity_blocker is not None:
        report["decision"] = "reject"
        report["pass"] = False
        report["next_action"] = "declare-seed-unavailable-and-reconstruct-from-reference"
        report["blockers"] = list(
            dict.fromkeys([*report.get("blockers", []), seed_integrity_blocker])
        )
        report["reject_reasons"] = list(
            dict.fromkeys([*report.get("reject_reasons", []), seed_integrity_blocker])
        )
        report.setdefault("findings", []).append(
            {
                "category": "route",
                "decision": "reject",
                "code": seed_integrity_blocker,
                "message": (
                    "the immutable external seed bytes are unavailable or do not "
                    "match their provenance record"
                ),
            }
        )
    write_source_gate_report(report, run.source_gate_report_path)
    if meta["input_route"] == "svg-seeded":
        mode = {
            "accept": "svg_import" if candidate_role == "external-seed" else "svg_repair",
            "repair": "svg_repair",
            "reject": "png_reconstruct",
        }[report["decision"]]
        set_processing_mode(
            run,
            processing_mode=mode,
            fidelity_profile="hybrid_fidelity" if mode == "png_reconstruct" else None,
        )
    return report


__all__.append("evaluate_case_source_gate")
