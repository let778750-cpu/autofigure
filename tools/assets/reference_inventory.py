"""Freeze and verify a reference-derived closed-world object inventory.

The inventory is authored from the hash-bound reference before a candidate is
accepted.  It closes the otherwise circular situation where the candidate
itself decides which small arrows, labels, icons, or braces deserve QA.
Legacy cases without ``reference_inventory`` remain readable; every newly
prepared case declares the inventory as required and therefore must freeze it
before ingest.  Freezing also binds the inventory to the route-neutral
reference oracle (``tools/reference_oracle.py``) so that all input routes
share one frozen truth per reference hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.core import common
from tools.arrows.arrow_spec import HEAD_TYPES, REPRESENTATIONS
from tools.core.contracts import read_json, utc_now, write_json

SCHEMA_VERSION = "1.0.0"
RECEIPT_KIND = "reference_inventory_freeze_receipt"
RECEIPT_PATH = "qa/reference-inventory-receipt.json"
OBJECT_KINDS = (
    "text",
    "formula",
    "arrow",
    "icon",
    "brace",
    "plot",
    "shape",
)
ZERO_AUTH_REQUIRED_KINDS = ("text", "arrow", "icon", "brace")
TEXT_KINDS = {"text", "formula"}
VISUAL_KINDS = {"icon", "plot"}
DEFAULT_SOURCE_ANISOTROPY_TOLERANCE = 0.01
_STABLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_SVG_NS = "{http://www.w3.org/2000/svg}"

_ARROW_RELATION_DIRECTIONS = {
    "forward",
    "backward",
    "bidirectional",
    "undirected",
}
_REQUIRED_ARROW_RELATION_FIELDS = {
    "id",
    "source_id",
    "target_id",
    "direction",
    "start_head_type",
    "end_head_type",
    "representation",
    "visible_object_count",
}
# ``relation`` is descriptive scientific semantics (for example
# ``conditioning`` or ``sampling-output``), not part of ArrowSpec geometry.
# It is the only optional field accepted by the frozen relation contract.
_OPTIONAL_ARROW_RELATION_FIELDS = {"relation"}

_TOPOLOGY_KEYS = {
    "role_counts",
    "role_patterns",
    "role_mapping",
    "element_roles",
    "required_pairs",
    "relations",
    "required_relations",
    "component_count",
    "scope_element_id",
}


def canonical_sha256(value: object) -> str:
    """Return the stable digest used by receipts and downstream evaluators."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def default_inventory(reference_sha256: str) -> dict[str, Any]:
    """Return a deliberately unfreezable skeleton for a newly prepared case."""

    return {
        "schema_version": SCHEMA_VERSION,
        "required": True,
        "status": "draft",
        "reference_sha256": reference_sha256,
        "receipt_path": RECEIPT_PATH,
        "expected_counts": {kind: 0 for kind in OBJECT_KINDS},
        "zero_count_authorizations": [],
        "objects": [],
    }


def _block(blockers: list[str], suffix: str) -> None:
    blockers.append(f"reference-inventory:{suffix}")


def _valid_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (numeric > 0 if positive else numeric >= 0)


def _valid_bbox(value: Any, width: int, height: int) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(_valid_number(item) for item in value):
        return False
    x, y, box_width, box_height = (float(item) for item in value)
    return (
        box_width > 0
        and box_height > 0
        and x + box_width <= width + 1e-6
        and y + box_height <= height + 1e-6
    )


def _contains(outer: Any, inner: list[float]) -> bool:
    if not isinstance(outer, list) or len(outer) != 4:
        return False
    try:
        x, y, width, height = (float(value) for value in outer)
        ix, iy, iwidth, iheight = (float(value) for value in inner)
    except (TypeError, ValueError):
        return False
    return (
        ix >= x - 1e-6
        and iy >= y - 1e-6
        and ix + iwidth <= x + width + 1e-6
        and iy + iheight <= y + height + 1e-6
    )


def _valid_typography(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "exact_text",
        "font_family",
        "font_size_px",
        "font_weight",
        "font_style",
        "line_count",
        "alignment",
        "bbox_tolerance_px",
        "font_size_tolerance_px",
    }
    optional = {"font_weights", "underline"}
    if not required.issubset(value) or not set(value).issubset(required | optional):
        return False

    def valid_weight(weight: Any) -> bool:
        return (
            isinstance(weight, str) and weight in {"normal", "medium", "semibold", "bold", "light"}
        ) or (
            isinstance(weight, int)
            and not isinstance(weight, bool)
            and 100 <= weight <= 900
            and weight % 100 == 0
        )

    weight_valid = valid_weight(value["font_weight"])
    line_weights = value.get("font_weights")
    line_weights_valid = line_weights is None or (
        isinstance(line_weights, list)
        and len(line_weights) == value.get("line_count")
        and all(valid_weight(weight) for weight in line_weights)
    )
    underline_valid = "underline" not in value or isinstance(value["underline"], bool)
    return bool(
        isinstance(value["exact_text"], str)
        and value["exact_text"].strip()
        and isinstance(value["font_family"], str)
        and value["font_family"].strip()
        and _valid_number(value["font_size_px"], positive=True)
        and weight_valid
        and value["font_style"] in {"normal", "italic", "oblique"}
        and isinstance(value["line_count"], int)
        and not isinstance(value["line_count"], bool)
        and value["line_count"] > 0
        and value["alignment"] in {"left", "center", "right", "start", "end"}
        and _valid_number(value["bbox_tolerance_px"])
        and _valid_number(value["font_size_tolerance_px"])
        and line_weights_valid
        and underline_valid
    )


def _valid_visual(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"aspect_ratio_tolerance", "bbox_tolerance_px"}
    optional = {
        "allow_source_anisotropic_scale",
        "source_anisotropy_basis",
    }
    if not required.issubset(value) or not set(value).issubset(required | optional):
        return False
    allow_anisotropy = value.get("allow_source_anisotropic_scale", False)
    basis = value.get("source_anisotropy_basis")
    explicit_allowance_valid = isinstance(allow_anisotropy, bool) and (
        (allow_anisotropy and isinstance(basis, str) and bool(basis.strip()))
        or (not allow_anisotropy and basis is None)
    )
    return bool(
        _valid_number(value["aspect_ratio_tolerance"])
        and float(value["aspect_ratio_tolerance"]) <= 1.0
        and _valid_number(value["bbox_tolerance_px"])
        and explicit_allowance_valid
    )


def _stable_string(value: Any) -> bool:
    return isinstance(value, str) and _STABLE_ID.fullmatch(value) is not None


def _topology_component_count(
    element_ids: set[str],
    pairs: list[dict[str, str]],
    relations: list[dict[str, str | None]],
) -> int:
    """Count connected components in a normalized topology graph."""

    if not element_ids:
        return 0
    adjacency = {element_id: set() for element_id in element_ids}

    def connect(left: str | None, right: str | None) -> None:
        if left in adjacency and right in adjacency and left != right:
            adjacency[left].add(right)
            adjacency[right].add(left)

    for pair in pairs:
        connect(pair["a"], pair["b"])
    for relation in relations:
        source_id = relation["source_id"]
        target_id = relation["target_id"]
        element_id = relation.get("element_id")
        if element_id in adjacency:
            connect(element_id, source_id)
            connect(element_id, target_id)
        else:
            connect(source_id, target_id)

    remaining = set(element_ids)
    count = 0
    while remaining:
        count += 1
        pending = [remaining.pop()]
        while pending:
            current = pending.pop()
            neighbors = adjacency[current] & remaining
            remaining.difference_update(neighbors)
            pending.extend(neighbors)
    return count


def _topology_role_mapping(
    raw: Any,
) -> tuple[dict[str, str], list[str]]:
    """Normalize either element->role or role->[element] explicit mappings."""

    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["topology-role-mapping"]
    mapping: dict[str, str] = {}
    errors: list[str] = []
    if all(isinstance(value, str) for value in raw.values()):
        records = [(element_id, role) for element_id, role in raw.items()]
    elif all(isinstance(value, list) for value in raw.values()):
        records = [
            (element_id, role) for role, element_ids in raw.items() for element_id in element_ids
        ]
    else:
        return {}, ["topology-role-mapping"]
    for element_id, role in records:
        if not _stable_string(element_id) or not _stable_string(role):
            errors.append("topology-role-mapping")
            continue
        existing = mapping.get(element_id)
        if existing is not None and existing != role:
            errors.append("topology-role-mapping-conflict")
            continue
        mapping[element_id] = role
    return mapping, errors


def _normalize_topology_pair(raw: Any, index: int) -> dict[str, str] | None:
    if isinstance(raw, list) and len(raw) == 2:
        left, right = raw
        pair_id = f"pair-{index + 1}"
    elif isinstance(raw, dict):
        members = raw.get("members")
        left = raw.get("a", raw.get("left_id", raw.get("source_id")))
        right = raw.get("b", raw.get("right_id", raw.get("target_id")))
        if isinstance(members, list) and len(members) == 2:
            left, right = members
        pair_id = raw.get("id", f"pair-{index + 1}")
    else:
        return None
    if (
        not _stable_string(pair_id)
        or not _stable_string(left)
        or not _stable_string(right)
        or left == right
    ):
        return None
    return {"id": pair_id, "a": left, "b": right}


def _normalize_topology_relation(
    raw: Any,
    index: int,
    object_element_ids: set[str],
) -> dict[str, str | None] | None:
    if not isinstance(raw, dict):
        return None
    source_id = raw.get("source_id", raw.get("source"))
    target_id = raw.get("target_id", raw.get("target"))
    record_id = raw.get("id", f"relation-{index + 1}")
    element_id = raw.get("element_id")
    if element_id is None and record_id in object_element_ids:
        element_id = record_id
    relation = raw.get("relation", raw.get("kind", raw.get("type", "relation")))
    if (
        not _stable_string(record_id)
        or not _stable_string(source_id)
        or not _stable_string(target_id)
        or source_id == target_id
        or not _stable_string(relation)
        or (element_id is not None and not _stable_string(element_id))
    ):
        return None
    return {
        "id": record_id,
        "element_id": element_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
    }


def normalize_topology_contract(
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize and validate one optional closed-world object topology contract.

    Canonical contracts use integer ``role_counts`` plus either regex
    ``role_patterns`` or an explicit ``role_mapping``.  For authoring
    convenience, a role-count value may instead be an object containing
    ``count`` and either ``element_id_pattern`` or ``element_ids``.
    """

    raw = item.get("topology_contract")
    if raw is None:
        return None, []
    if not isinstance(raw, dict) or not set(raw).issubset(_TOPOLOGY_KEYS):
        return None, ["topology-contract"]
    raw_counts = raw.get("role_counts")
    if not isinstance(raw_counts, dict) or not raw_counts:
        return None, ["topology-role-counts"]

    counts: dict[str, int] = {}
    embedded_patterns: dict[str, str] = {}
    embedded_mapping: dict[str, str] = {}
    errors: list[str] = []
    for role, value in raw_counts.items():
        if not _stable_string(role):
            errors.append("topology-role-counts")
            continue
        if isinstance(value, dict):
            allowed = {"count", "element_id_pattern", "element_ids"}
            if not set(value).issubset(allowed):
                errors.append("topology-role-counts")
                continue
            count = value.get("count")
            pattern = value.get("element_id_pattern")
            element_ids = value.get("element_ids")
            if pattern is not None and element_ids is not None:
                errors.append("topology-role-selector")
            elif pattern is not None:
                if not isinstance(pattern, str) or not pattern:
                    errors.append("topology-role-patterns")
                else:
                    embedded_patterns[role] = pattern
            elif element_ids is not None:
                if not isinstance(element_ids, list):
                    errors.append("topology-role-mapping")
                else:
                    for element_id in element_ids:
                        if not _stable_string(element_id):
                            errors.append("topology-role-mapping")
                        elif (
                            element_id in embedded_mapping and embedded_mapping[element_id] != role
                        ):
                            errors.append("topology-role-mapping-conflict")
                        else:
                            embedded_mapping[element_id] = role
            else:
                errors.append("topology-role-selector")
        else:
            count = value
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append("topology-role-counts")
        else:
            counts[role] = count

    raw_patterns = raw.get("role_patterns", {})
    patterns = dict(embedded_patterns)
    if not isinstance(raw_patterns, dict):
        errors.append("topology-role-patterns")
    else:
        for role, pattern in raw_patterns.items():
            if (
                not _stable_string(role)
                or not isinstance(pattern, str)
                or not pattern
                or role in patterns
            ):
                errors.append("topology-role-patterns")
                continue
            try:
                re.compile(pattern)
            except re.error:
                errors.append("topology-role-patterns")
                continue
            patterns[role] = pattern
    for role, pattern in list(patterns.items()):
        try:
            re.compile(pattern)
        except re.error:
            errors.append("topology-role-patterns")

    mapping = dict(embedded_mapping)
    mapping_fields = [field for field in ("role_mapping", "element_roles") if field in raw]
    if len(mapping_fields) > 1:
        errors.append("topology-role-mapping")
    elif mapping_fields:
        explicit, mapping_errors = _topology_role_mapping(raw[mapping_fields[0]])
        errors.extend(mapping_errors)
        for element_id, role in explicit.items():
            if element_id in mapping and mapping[element_id] != role:
                errors.append("topology-role-mapping-conflict")
            else:
                mapping[element_id] = role

    declared_roles = set(counts)
    selector_roles = set(patterns) | set(mapping.values())
    if declared_roles != selector_roles:
        errors.append("topology-role-selector-closure")

    object_element_ids = {value for value in item.get("element_ids", []) if isinstance(value, str)}
    expected_roles: dict[str, list[str]] = {role: [] for role in counts}
    for element_id in sorted(object_element_ids):
        resolved = set()
        if element_id in mapping:
            resolved.add(mapping[element_id])
        for role, pattern in patterns.items():
            if re.fullmatch(pattern, element_id):
                resolved.add(role)
        if len(resolved) != 1:
            errors.append(
                "topology-element-unclassified"
                if not resolved
                else "topology-element-role-ambiguous"
            )
            continue
        role = next(iter(resolved))
        if role in expected_roles:
            expected_roles[role].append(element_id)
    if set(mapping) - object_element_ids:
        errors.append("topology-role-mapping-scope")
    for role, count in counts.items():
        if len(expected_roles.get(role, [])) != count:
            errors.append(f"topology-role-count:{role}")

    raw_pairs = raw.get("required_pairs", [])
    pairs: list[dict[str, str]] = []
    if not isinstance(raw_pairs, list):
        errors.append("topology-required-pairs")
    else:
        for index, pair in enumerate(raw_pairs):
            normalized = _normalize_topology_pair(pair, index)
            if normalized is None:
                errors.append("topology-required-pairs")
            else:
                pairs.append(normalized)
    pair_ids = [pair["id"] for pair in pairs]
    pair_members = [tuple(sorted((pair["a"], pair["b"]))) for pair in pairs]
    if len(pair_ids) != len(set(pair_ids)) or len(pair_members) != len(set(pair_members)):
        errors.append("topology-required-pair-closure")
    if any(
        pair["a"] not in object_element_ids or pair["b"] not in object_element_ids for pair in pairs
    ):
        errors.append("topology-required-pair-scope")

    relation_fields = [field for field in ("relations", "required_relations") if field in raw]
    raw_relations: Any = []
    if len(relation_fields) > 1:
        errors.append("topology-relations")
    elif relation_fields:
        raw_relations = raw[relation_fields[0]]
    relations: list[dict[str, str | None]] = []
    if not isinstance(raw_relations, list):
        errors.append("topology-relations")
    else:
        for index, relation in enumerate(raw_relations):
            normalized = _normalize_topology_relation(
                relation,
                index,
                object_element_ids,
            )
            if normalized is None:
                errors.append("topology-relations")
            else:
                relations.append(normalized)
    relation_ids = [str(relation["id"]) for relation in relations]
    relation_elements = [
        str(relation["element_id"])
        for relation in relations
        if relation.get("element_id") is not None
    ]
    if len(relation_ids) != len(set(relation_ids)) or len(relation_elements) != len(
        set(relation_elements)
    ):
        errors.append("topology-relation-closure")
    if any(
        relation["source_id"] not in object_element_ids
        or relation["target_id"] not in object_element_ids
        or (
            relation.get("element_id") is not None
            and relation["element_id"] not in object_element_ids
        )
        for relation in relations
    ):
        errors.append("topology-relation-scope")

    component_count = raw.get("component_count")
    if (
        not isinstance(component_count, int)
        or isinstance(component_count, bool)
        or component_count < 1
    ):
        errors.append("topology-component-count")
    elif _topology_component_count(object_element_ids, pairs, relations) != component_count:
        errors.append("topology-component-count-mismatch")

    scope_element_id = raw.get("scope_element_id")
    if scope_element_id is not None and not _stable_string(scope_element_id):
        errors.append("topology-scope-element-id")

    errors = list(dict.fromkeys(errors))
    if errors:
        return None, errors
    return {
        "role_counts": counts,
        "role_patterns": patterns,
        "role_mapping": mapping,
        "expected_roles": {
            role: sorted(element_ids) for role, element_ids in expected_roles.items()
        },
        "required_pairs": pairs,
        "relations": relations,
        "component_count": component_count,
        "scope_element_id": scope_element_id,
    }, []


def topology_contracts_sha256(inventory: dict[str, Any]) -> str:
    """Hash the exact authored topology contracts and their owning object ids."""

    objects = inventory.get("objects", [])
    if not isinstance(objects, list):
        objects = []
    contracts = [
        {
            "object_id": item.get("id"),
            "topology_contract": item.get("topology_contract"),
        }
        for item in objects
        if isinstance(item, dict) and "topology_contract" in item
    ]
    return canonical_sha256(contracts)


def _arrow_visual_contract_values(payload: dict[str, Any]) -> list[Any]:
    values: list[Any] = [payload.get("arrow_visual_contracts", [])]
    for region in payload.get("regions", []):
        if not isinstance(region, dict):
            continue
        if "arrow_visual_contract" in region:
            values.append(region["arrow_visual_contract"])
        if "arrow_visual_contracts" in region:
            values.append(region["arrow_visual_contracts"])
    contracts: list[Any] = []
    for value in values:
        collection = value if isinstance(value, list) else [value]
        contracts.extend(collection)
    return contracts


def _arrow_visual_contract_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _arrow_visual_contract_values(payload) if isinstance(item, dict)]


def _arrow_visual_contract_id_list(payload: dict[str, Any]) -> list[str]:
    return [
        item["id"]
        for item in _arrow_visual_contract_records(payload)
        if isinstance(item.get("id"), str)
    ]


def _required_arrow_relations(
    critical_regions: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate and return the frozen closed-world logical arrow relations."""

    relations: list[dict[str, Any]] = []
    blockers: list[str] = []
    allowed_fields = _REQUIRED_ARROW_RELATION_FIELDS | _OPTIONAL_ARROW_RELATION_FIELDS
    for region_id, region in critical_regions.items():
        if "required_relations" not in region:
            continue
        region_relations = region.get("required_relations")
        if not isinstance(region_relations, list):
            _block(blockers, f"region:{region_id}:required-relations")
            continue
        scoped_ids = {value for value in region.get("element_ids", []) if isinstance(value, str)}
        for index, relation in enumerate(region_relations, start=1):
            prefix = f"region:{region_id}:required-relation:{index}"
            if not isinstance(relation, dict):
                _block(blockers, f"{prefix}:schema")
                continue
            fields = set(relation)
            if not _REQUIRED_ARROW_RELATION_FIELDS.issubset(fields) or fields - allowed_fields:
                _block(blockers, f"{prefix}:fields")

            for field in ("id", "source_id", "target_id"):
                value = relation.get(field)
                if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
                    _block(blockers, f"{prefix}:{field.replace('_', '-')}")
                elif value not in scoped_ids:
                    _block(blockers, f"{prefix}:{field.replace('_', '-')}-not-scoped")

            direction = relation.get("direction")
            start_head_type = relation.get("start_head_type")
            end_head_type = relation.get("end_head_type")
            if direction not in _ARROW_RELATION_DIRECTIONS:
                _block(blockers, f"{prefix}:direction")
            if start_head_type not in HEAD_TYPES:
                _block(blockers, f"{prefix}:start-head-type")
            if end_head_type not in HEAD_TYPES:
                _block(blockers, f"{prefix}:end-head-type")
            if relation.get("representation") not in REPRESENTATIONS:
                _block(blockers, f"{prefix}:representation")
            visible_object_count = relation.get("visible_object_count")
            if (
                not isinstance(visible_object_count, int)
                or isinstance(visible_object_count, bool)
                or visible_object_count != 1
            ):
                _block(blockers, f"{prefix}:visible-object-count")
            if "relation" in relation and (
                not isinstance(relation["relation"], str) or not relation["relation"].strip()
            ):
                _block(blockers, f"{prefix}:relation")

            if (
                direction in _ARROW_RELATION_DIRECTIONS
                and start_head_type in HEAD_TYPES
                and end_head_type in HEAD_TYPES
            ):
                start_visible = start_head_type != "none"
                end_visible = end_head_type != "none"
                actual_direction = (
                    "bidirectional"
                    if start_visible and end_visible
                    else "backward"
                    if start_visible
                    else "forward"
                    if end_visible
                    else "undirected"
                )
                if actual_direction != direction:
                    _block(blockers, f"{prefix}:direction-heads")
            relations.append(relation)
    return relations, blockers


def _brace_expectation_id_list(payload: dict[str, Any]) -> list[str]:
    return [
        primitive["element_id"]
        for expectation in payload.get("primitive_expectations", [])
        if isinstance(expectation, dict) and expectation.get("kind") == "brace"
        for primitive in expectation.get("primitives", [])
        if isinstance(primitive, dict) and isinstance(primitive.get("element_id"), str)
    ]


def _validate_contract_refs(
    item: dict[str, Any],
    *,
    regions_by_id: dict[str, dict[str, Any]],
    arrow_visual_element_by_id: dict[str, str],
    brace_ids: set[str],
) -> list[str]:
    kind = item["kind"]
    item_id = item["id"]
    refs = item.get("contract_refs")
    blockers: list[str] = []
    if kind == "arrow":
        if not isinstance(refs, dict) or set(refs) != {
            "required_relation",
            "arrow_visual",
        }:
            _block(blockers, f"object:{item_id}:arrow-contract-refs")
            return blockers
        relation_ref = refs.get("required_relation")
        visual_ref = refs.get("arrow_visual")
        relation_id: Any = None
        if not isinstance(relation_ref, dict) or set(relation_ref) != {
            "region_id",
            "relation_id",
        }:
            _block(blockers, f"object:{item_id}:required-relation-ref")
        else:
            relation_id = relation_ref.get("relation_id")
            region = regions_by_id.get(relation_ref.get("region_id"))
            relations = [] if region is None else region.get("required_relations", [])
            if not isinstance(relations, list):
                relations = []
            relation_ids = {
                relation.get("id") for relation in relations if isinstance(relation, dict)
            }
            if (
                region is None
                or relation_ref.get("region_id") not in item["critical_region_ids"]
                or relation_id not in relation_ids
            ):
                _block(blockers, f"object:{item_id}:required-relation-missing")
            if item["element_ids"] != [relation_id]:
                _block(blockers, f"object:{item_id}:arrow-relation-object-closure")
        if (
            not isinstance(visual_ref, dict)
            or set(visual_ref) != {"contract_id"}
            or visual_ref.get("contract_id") not in arrow_visual_element_by_id
        ):
            _block(blockers, f"object:{item_id}:arrow-visual-ref")
        elif arrow_visual_element_by_id[visual_ref["contract_id"]] != relation_id:
            _block(blockers, f"object:{item_id}:arrow-visual-element-mismatch")
    elif kind == "brace":
        if (
            not isinstance(refs, dict)
            or set(refs) != {"primitive"}
            or not isinstance(refs.get("primitive"), dict)
            or set(refs["primitive"]) != {"element_id"}
            or refs["primitive"].get("element_id") not in brace_ids
            or refs["primitive"].get("element_id") not in item["element_ids"]
        ):
            _block(blockers, f"object:{item_id}:brace-primitive-ref")
    elif kind in VISUAL_KINDS:
        if (
            not isinstance(refs, dict)
            or set(refs) != {"ink_contract"}
            or not isinstance(refs.get("ink_contract"), dict)
            or set(refs["ink_contract"]) != {"region_id"}
        ):
            _block(blockers, f"object:{item_id}:ink-contract-ref")
        else:
            region_id = refs["ink_contract"].get("region_id")
            region = regions_by_id.get(region_id)
            if (
                region_id not in item["critical_region_ids"]
                or region is None
                or not isinstance(region.get("ink_contract"), dict)
            ):
                _block(blockers, f"object:{item_id}:ink-contract-missing")
            elif isinstance(item.get("bbox"), list) and isinstance(region.get("bbox"), list):
                object_x, object_y, object_width, object_height = (
                    float(value) for value in item["bbox"]
                )
                region_x, region_y, region_width, region_height = (
                    float(value) for value in region["bbox"]
                )
                padding = (
                    object_x - region_x,
                    object_y - region_y,
                    region_x + region_width - (object_x + object_width),
                    region_y + region_height - (object_y + object_height),
                )
                area_ratio = (
                    math.inf
                    if object_width <= 0 or object_height <= 0
                    else (region_width * region_height) / (object_width * object_height)
                )
                if min(padding) < 0 or max(padding) > 8 or area_ratio > 1.75:
                    _block(blockers, f"object:{item_id}:ink-contract-not-tight")
    return blockers


def validate_inventory(
    run: common.Run,
    payload: dict[str, Any] | None = None,
    *,
    require_frozen: bool,
) -> dict[str, Any]:
    """Validate schema, counts, coverage, and case-owned contract references."""

    payload = payload or read_json(run.regions_path)
    inventory = payload.get("reference_inventory")
    if inventory is None:
        return {
            "legacy": True,
            "required": False,
            "pass": True,
            "blockers": [],
        }
    blockers: list[str] = []
    if not isinstance(inventory, dict):
        return {
            "legacy": False,
            "required": True,
            "pass": False,
            "blockers": ["reference-inventory:invalid"],
        }
    required = inventory.get("required") is True
    meta = run.load_meta()
    if not required:
        _block(blockers, "required-flag")
    if inventory.get("schema_version") != SCHEMA_VERSION:
        _block(blockers, "schema-version")
    if inventory.get("reference_sha256") != meta.get("source_sha256"):
        _block(blockers, "reference-hash-mismatch")
    if inventory.get("receipt_path") != RECEIPT_PATH:
        _block(blockers, "receipt-path")
    if inventory.get("status") not in {"draft", "frozen"}:
        _block(blockers, "status")
    if required and require_frozen and inventory.get("status") != "frozen":
        _block(blockers, "not-frozen")
    expected_counts = inventory.get("expected_counts")
    if (
        not isinstance(expected_counts, dict)
        or set(expected_counts) != set(OBJECT_KINDS)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in expected_counts.values()
        )
    ):
        _block(blockers, "expected-counts")
        expected_counts = {kind: -1 for kind in OBJECT_KINDS}
    objects = inventory.get("objects")
    if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
        _block(blockers, "objects")
        objects = []
    zero_authorizations = inventory.get("zero_count_authorizations")
    if not isinstance(zero_authorizations, list):
        _block(blockers, "zero-authorizations")
        zero_authorizations = []
    authorized_zeroes: set[str] = set()
    for index, authorization in enumerate(zero_authorizations):
        if (
            not isinstance(authorization, dict)
            or set(authorization) != {"kind", "basis", "reviewer", "reference_sha256"}
            or authorization.get("kind") not in ZERO_AUTH_REQUIRED_KINDS
            or authorization.get("reference_sha256") != meta.get("source_sha256")
            or authorization.get("basis") != "full-reference-review"
            or not isinstance(authorization.get("reviewer"), str)
            or not authorization["reviewer"].strip()
        ):
            _block(blockers, f"zero-authorization:{index + 1}")
            continue
        if authorization["kind"] in authorized_zeroes:
            _block(blockers, f"zero-authorization-duplicate:{authorization['kind']}")
        authorized_zeroes.add(authorization["kind"])
    for kind in ZERO_AUTH_REQUIRED_KINDS:
        if expected_counts.get(kind) == 0 and kind not in authorized_zeroes:
            _block(blockers, f"zero-unverified:{kind}")
        if expected_counts.get(kind, -1) > 0 and kind in authorized_zeroes:
            _block(blockers, f"zero-authorization-conflict:{kind}")

    regions = [item for item in payload.get("regions", []) if isinstance(item, dict)]
    regions_by_id = {
        item["id"]: item for item in regions if isinstance(item.get("id"), str) and item["id"]
    }
    critical_regions = {
        region_id: region
        for region_id, region in regions_by_id.items()
        if region.get("critical") is True
    }
    for region_id, region in critical_regions.items():
        if region.get("relations_exhaustive") is not True:
            _block(blockers, f"region:{region_id}:relations-not-exhaustive")
    required_relations, relation_blockers = _required_arrow_relations(critical_regions)
    blockers.extend(relation_blockers)
    arrow_visual_contracts = _arrow_visual_contract_values(payload)
    arrow_visual_id_list = _arrow_visual_contract_id_list(payload)
    arrow_visual_ids = set(arrow_visual_id_list)
    if len(arrow_visual_id_list) != len(arrow_visual_ids):
        _block(blockers, "arrow-visual-contract-ids-not-unique")
    arrow_visual_element_by_id: dict[str, str] = {}
    arrow_visual_element_id_list: list[str] = []
    for index, contract in enumerate(arrow_visual_contracts, start=1):
        if not isinstance(contract, dict):
            _block(blockers, f"arrow-visual-contract:{index}:identity")
            continue
        contract_id = contract.get("id")
        element_id = contract.get("element_id")
        if (
            not isinstance(contract_id, str)
            or not _STABLE_ID.fullmatch(contract_id)
            or not isinstance(element_id, str)
            or not _STABLE_ID.fullmatch(element_id)
        ):
            _block(blockers, f"arrow-visual-contract:{index}:identity")
            continue
        arrow_visual_element_by_id[contract_id] = element_id
        arrow_visual_element_id_list.append(element_id)
    if len(arrow_visual_element_id_list) != len(set(arrow_visual_element_id_list)):
        _block(blockers, "arrow-visual-contract-elements-not-unique")
    brace_id_list = _brace_expectation_id_list(payload)
    brace_ids = set(brace_id_list)
    if len(brace_id_list) != len(brace_ids):
        _block(blockers, "brace-expectation-ids-not-unique")
    seen_object_ids: set[str] = set()
    raw_object_id_counts = Counter(
        item.get("id")
        for item in objects
        if isinstance(item.get("id"), str)
        and _STABLE_ID.fullmatch(item["id"])
    )
    semantic_scope_ids = {
        object_id for object_id, count in raw_object_id_counts.items() if count == 1
    }
    semantic_scope_regions: dict[str, set[str]] = defaultdict(set)
    element_owners: dict[str, list[str]] = defaultdict(list)
    referenced_regions: set[str] = set()
    actual_counts: Counter[str] = Counter()
    arrow_relation_id_list: list[str] = []
    arrow_visual_ref_list: list[str] = []
    inventory_brace_ids: set[str] = set()
    topology_contract_count = 0
    width, height = int(meta["width"]), int(meta["height"])
    for index, item in enumerate(objects):
        item_id = item.get("id")
        kind = item.get("kind")
        if not isinstance(item_id, str) or not _STABLE_ID.fullmatch(item_id):
            _block(blockers, f"object:{index + 1}:id")
            item_id = f"index-{index + 1}"
        elif item_id in seen_object_ids:
            _block(blockers, f"object:{item_id}:duplicate")
        seen_object_ids.add(item_id)
        if kind not in OBJECT_KINDS:
            _block(blockers, f"object:{item_id}:kind")
            continue
        actual_counts[kind] += 1
        bbox = item.get("bbox")
        if not _valid_bbox(bbox, width, height):
            _block(blockers, f"object:{item_id}:bbox")
            bbox = None
        element_ids = item.get("element_ids")
        if (
            not isinstance(element_ids, list)
            or not element_ids
            or any(
                not isinstance(value, str) or not _STABLE_ID.fullmatch(value)
                for value in element_ids
            )
            or len(element_ids) != len(set(element_ids))
        ):
            _block(blockers, f"object:{item_id}:element-ids")
            element_ids = []
        item["element_ids"] = element_ids
        if kind == "arrow" and len(element_ids) != 1:
            _block(blockers, f"object:{item_id}:arrow-element-count")
        for element_id in element_ids:
            element_owners[element_id].append(item_id)
        critical_ids = item.get("critical_region_ids")
        if (
            not isinstance(critical_ids, list)
            or not critical_ids
            or any(not isinstance(value, str) or not value for value in critical_ids)
            or len(critical_ids) != len(set(critical_ids))
        ):
            _block(blockers, f"object:{item_id}:critical-region-ids")
            critical_ids = []
        item["critical_region_ids"] = critical_ids
        if item_id in semantic_scope_ids:
            semantic_scope_regions[item_id].update(critical_ids)
        referenced_regions.update(critical_ids)
        object_regions = [critical_regions.get(region_id) for region_id in critical_ids]
        if any(region is None for region in object_regions):
            _block(blockers, f"object:{item_id}:critical-region-missing")
        valid_regions = [region for region in object_regions if region is not None]
        scoped_ids = {
            element_id
            for region in valid_regions
            for element_id in region.get("element_ids", [])
            if isinstance(element_id, str)
        }
        if set(element_ids) - scoped_ids:
            _block(blockers, f"object:{item_id}:element-not-region-scoped")
        if bbox is not None and not any(
            _contains(region.get("bbox"), bbox) for region in valid_regions
        ):
            _block(blockers, f"object:{item_id}:bbox-not-region-covered")
        if kind in TEXT_KINDS:
            if len(element_ids) != 1:
                _block(blockers, f"object:{item_id}:text-element-count")
            if not _valid_typography(item.get("typography")):
                _block(blockers, f"object:{item_id}:typography")
        elif kind in VISUAL_KINDS and not _valid_visual(item.get("visual")):
            _block(blockers, f"object:{item_id}:visual")
        _, topology_errors = normalize_topology_contract(item)
        if "topology_contract" in item:
            topology_contract_count += 1
        for error in topology_errors:
            _block(blockers, f"object:{item_id}:{error}")
        blockers.extend(
            _validate_contract_refs(
                item,
                regions_by_id=regions_by_id,
                arrow_visual_element_by_id=arrow_visual_element_by_id,
                brace_ids=brace_ids,
            )
        )
        refs = item.get("contract_refs", {})
        if kind == "arrow" and isinstance(refs, dict):
            relation_ref = refs.get("required_relation", {})
            visual_ref = refs.get("arrow_visual", {})
            if isinstance(relation_ref, dict) and isinstance(relation_ref.get("relation_id"), str):
                arrow_relation_id_list.append(relation_ref["relation_id"])
            if isinstance(visual_ref, dict) and isinstance(visual_ref.get("contract_id"), str):
                arrow_visual_ref_list.append(visual_ref["contract_id"])
        if kind == "brace" and isinstance(refs, dict):
            primitive_ref = refs.get("primitive", {})
            if isinstance(primitive_ref, dict) and isinstance(primitive_ref.get("element_id"), str):
                inventory_brace_ids.add(primitive_ref["element_id"])

    for kind in OBJECT_KINDS:
        if actual_counts[kind] != expected_counts.get(kind):
            _block(blockers, f"count-mismatch:{kind}")
    duplicate_elements = sorted(
        element_id for element_id, owners in element_owners.items() if len(owners) != 1
    )
    for element_id in duplicate_elements:
        _block(blockers, f"element-owner-count:{element_id}")
    # An inventory object ID is the stable semantic identity for a logical SVG
    # group/container.  It may therefore appear in a critical region next to
    # the object's physical leaf ``element_ids`` without being repeated as a
    # fake physical element.  Keep the two namespaces unambiguous: an object ID
    # may coincide with its *own* single physical element (legacy text/shape
    # objects), but collision with another object's physical ownership is a
    # fail-closed contract error.
    for semantic_id in sorted(semantic_scope_ids & set(element_owners)):
        owners = set(element_owners[semantic_id])
        if owners != {semantic_id}:
            _block(blockers, f"semantic-physical-id-collision:{semantic_id}")
    for region_id, region in critical_regions.items():
        for element_id in sorted(
            {
                value
                for value in region.get("element_ids", [])
                if isinstance(value, str)
            }
        ):
            physically_inventoried = element_id in element_owners
            semantically_inventoried = (
                element_id in semantic_scope_ids
                and region_id in semantic_scope_regions[element_id]
            )
            if not physically_inventoried and not semantically_inventoried:
                _block(blockers, f"critical-element-uninventoried:{element_id}")
    for region_id in sorted(set(critical_regions) - referenced_regions):
        _block(blockers, f"critical-region-uninventoried:{region_id}")
    declared_relation_ids = [
        relation["id"] for relation in required_relations if isinstance(relation.get("id"), str)
    ]
    declared_relations = set(declared_relation_ids)
    if len(declared_relation_ids) != len(declared_relations):
        _block(blockers, "required-relation-ids-not-unique")
    arrow_relation_ids = set(arrow_relation_id_list)
    if (
        len(arrow_relation_id_list) != actual_counts["arrow"]
        or len(arrow_relation_ids) != len(arrow_relation_id_list)
        or arrow_relation_ids != declared_relations
    ):
        _block(blockers, "arrow-relation-count-closure")
    arrow_visual_refs = set(arrow_visual_ref_list)
    if (
        len(arrow_visual_ref_list) != actual_counts["arrow"]
        or len(arrow_visual_refs) != len(arrow_visual_ref_list)
        or arrow_visual_refs != arrow_visual_ids
    ):
        _block(blockers, "arrow-visual-count-closure")
    if (
        len(arrow_visual_element_id_list) != actual_counts["arrow"]
        or set(arrow_visual_element_id_list) != declared_relations
    ):
        _block(blockers, "arrow-visual-element-closure")
    arrow_expectation = payload.get("arrow_visual_expectation")
    expectation_records = (
        arrow_expectation.get("contracts", []) if isinstance(arrow_expectation, dict) else []
    )
    exemption_records = (
        arrow_expectation.get("exemptions", []) if isinstance(arrow_expectation, dict) else []
    )
    expectation_element_ids = [
        record.get("element_id")
        for record in expectation_records
        if isinstance(record, dict) and isinstance(record.get("element_id"), str)
    ]
    inventory_arrow_element_ids = {
        element_id
        for item in objects
        if item.get("kind") == "arrow"
        for element_id in item.get("element_ids", [])
    }
    object_by_id = {
        item.get("id"): item
        for item in objects
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    exemption_element_ids: list[str] = []
    exemptions_valid = isinstance(exemption_records, list)
    for record in exemption_records if isinstance(exemption_records, list) else []:
        if not isinstance(record, dict):
            exemptions_valid = False
            continue
        element_id = record.get("element_id")
        parent = object_by_id.get(record.get("parent_object_id"))
        sides = record.get("head_sides")
        valid = (
            isinstance(element_id, str)
            and bool(element_id)
            and record.get("reason") == "embedded_plot_axis"
            and isinstance(sides, list)
            and bool(sides)
            and len(sides) == len(set(sides))
            and all(side in {"start", "end"} for side in sides)
            and isinstance(parent, dict)
            and parent.get("kind") == "plot"
            and element_id in parent.get("element_ids", [])
            and element_id not in inventory_arrow_element_ids
        )
        exemptions_valid = exemptions_valid and valid
        if isinstance(element_id, str):
            exemption_element_ids.append(element_id)
    exemptions_valid = (
        exemptions_valid
        and len(exemption_element_ids) == len(set(exemption_element_ids))
        and not (set(exemption_element_ids) & set(expectation_element_ids))
    )
    if (
        not isinstance(arrow_expectation, dict)
        or arrow_expectation.get("count") != actual_counts["arrow"]
        or len(expectation_records) != actual_counts["arrow"]
        or len(expectation_element_ids) != actual_counts["arrow"]
        or len(set(expectation_element_ids)) != len(expectation_element_ids)
        or set(expectation_element_ids) != inventory_arrow_element_ids
    ):
        _block(blockers, "arrow-expectation-count-closure")
    if not exemptions_valid:
        _block(blockers, "arrow-exemption-closure")
    if len(brace_id_list) != actual_counts["brace"] or inventory_brace_ids != brace_ids:
        _block(blockers, "brace-count-closure")
    blockers.extend(_oracle_blockers(run, inventory))
    blockers = list(dict.fromkeys(blockers))
    return {
        "legacy": False,
        "required": required,
        "status": inventory.get("status"),
        "reference_sha256": inventory.get("reference_sha256"),
        "object_count": len(objects),
        "semantic_scope_identity_count": len(semantic_scope_ids),
        "expected_counts": expected_counts,
        "actual_counts": {kind: actual_counts[kind] for kind in OBJECT_KINDS},
        "inventory_sha256": canonical_sha256(inventory),
        "topology_contract_count": topology_contract_count,
        "topology_contracts_sha256": topology_contracts_sha256(inventory),
        "blockers": blockers,
        "pass": not blockers,
    }


def _oracle_blockers(run: common.Run, inventory: dict[str, Any]) -> list[str]:
    """Existence-gated comparison against the route-neutral reference oracle.

    无 oracle 时不产生任何 blocker；存在即校验（真值重授权见
    tools/reference_oracle.py 模块说明）。
    """

    from tools.assets.reference_oracle import load_oracle, oracle_matches, oracle_path

    path = oracle_path(run)
    if not path.is_file():
        return []
    try:
        oracle = load_oracle(path)
    except Exception:
        return ["oracle:invalid"]
    if oracle["reference_sha256"] != run.load_meta().get("source_sha256"):
        return ["oracle:inventory-mismatch"]
    if not oracle_matches(oracle, inventory):
        return ["oracle:inventory-mismatch"]
    return []


def _receipt_blockers(run: common.Run, report: dict[str, Any]) -> list[str]:
    if report.get("legacy") or not report.get("required"):
        return []
    receipt_path = run.root / RECEIPT_PATH
    if not receipt_path.is_file():
        return ["reference-inventory:receipt-missing"]
    try:
        receipt = read_json(receipt_path)
    except Exception:
        return ["reference-inventory:receipt-invalid"]
    regions = read_json(run.regions_path)
    tasks_path = run.region_tasks_path
    blockers: list[str] = []
    expected = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "case": run.load_meta()["case"],
        "reference_sha256": run.load_meta()["source_sha256"],
        "inventory_sha256": report.get("inventory_sha256"),
        "regions_sha256": common.sha256_file(run.regions_path),
        "critical_region_expectation_sha256": canonical_sha256(
            regions.get("critical_region_expectation")
        ),
        "region_tasks_sha256": (common.sha256_file(tasks_path) if tasks_path.is_file() else None),
        "object_count": report.get("object_count"),
        "counts": report.get("actual_counts"),
        "status": "PASS",
    }
    if report.get("topology_contract_count"):
        expected["topology_contract_count"] = report["topology_contract_count"]
        expected["topology_contracts_sha256"] = report["topology_contracts_sha256"]
    for field, value in expected.items():
        if receipt.get(field) != value:
            _block(blockers, f"receipt-stale:{field}")
    frozen_at = receipt.get("frozen_at")
    if not isinstance(frozen_at, str) or not frozen_at:
        _block(blockers, "receipt-stale:frozen_at")
    return blockers


def inventory_blockers(
    run: common.Run,
    *,
    include_svg_text: bool = False,
) -> list[str]:
    """Return strict/ingest blockers, preserving legacy readability."""

    report = validate_inventory(run, require_frozen=True)
    blockers = list(report["blockers"])
    blockers.extend(_receipt_blockers(run, report))
    if include_svg_text and not report.get("legacy") and report.get("required"):
        blockers.extend(svg_text_blockers(run))
    return list(dict.fromkeys(blockers))


def require_frozen_inventory(run: common.Run) -> None:
    blockers = inventory_blockers(run)
    if blockers:
        raise common.fail(
            "reference inventory must be frozen before candidate ingest: " + ", ".join(blockers)
        )


def _visible_svg_text(element: ET.Element) -> str:
    positioned_lines = [
        re.sub(r"\s+", " ", (child.text or "").strip())
        for child in element
        if child.tag == f"{_SVG_NS}tspan"
        and any(child.get(name) is not None for name in ("x", "y", "dy"))
        and (child.text or "").strip()
    ]
    if positioned_lines:
        text = "\n".join(positioned_lines)
    else:
        text = re.sub(r"\s+", " ", "".join(element.itertext()).strip())
    return unicodedata.normalize("NFC", text)


def svg_text_blockers(run: common.Run) -> list[str]:
    """Close the frozen exact-text inventory against the current SVG carrier."""

    payload = read_json(run.regions_path)
    inventory = payload.get("reference_inventory")
    if not isinstance(inventory, dict) or not inventory.get("required"):
        return []
    try:
        root = ET.parse(run.redraw_svg).getroot()
    except (OSError, ET.ParseError):
        return ["reference-inventory:text-svg-unavailable"]
    actual: dict[str, list[str]] = defaultdict(list)
    anonymous = 0
    for element in root.iter(f"{_SVG_NS}text"):
        element_id = element.get("id")
        if not isinstance(element_id, str) or not element_id:
            anonymous += 1
            continue
        actual[element_id].append(_visible_svg_text(element))
    expected: dict[str, str] = {}
    blockers: list[str] = []
    for item in inventory.get("objects", []):
        if not isinstance(item, dict) or item.get("kind") not in TEXT_KINDS:
            continue
        element_ids = item.get("element_ids")
        typography = item.get("typography")
        if (
            not isinstance(element_ids, list)
            or len(element_ids) != 1
            or not isinstance(typography, dict)
            or not isinstance(typography.get("exact_text"), str)
        ):
            continue
        expected[element_ids[0]] = unicodedata.normalize("NFC", typography["exact_text"].strip())
    if anonymous:
        _block(blockers, "text-svg-anonymous")
    for element_id in sorted(set(expected) | set(actual)):
        rows = actual.get(element_id, [])
        if element_id not in expected:
            _block(blockers, f"text-svg-unexpected:{element_id}")
        elif len(rows) != 1:
            _block(blockers, f"text-svg-count:{element_id}")
        elif rows[0] != expected[element_id]:
            _block(blockers, f"text-exact-mismatch:{element_id}")
    return blockers


def freeze_inventory(run: common.Run) -> dict[str, Any]:
    """Validate, freeze, refresh tasks, and write the artifact-bound receipt."""

    payload = read_json(run.regions_path)
    inventory = payload.get("reference_inventory")
    if inventory is None:
        raise common.fail(
            "legacy case has no reference_inventory; add a draft inventory explicitly "
            "before using autofigure freeze"
        )
    report = validate_inventory(run, payload, require_frozen=False)
    if report["blockers"]:
        message = "reference inventory cannot be frozen: " + ", ".join(report["blockers"])
        if any(blocker.startswith("oracle:") for blocker in report["blockers"]):
            from tools.assets.reference_oracle import oracle_path

            message += (
                "; the route-neutral reference oracle is authoritative for this "
                "reference hash — align the inventory with it, or re-authorize the "
                f"truth by manually removing {oracle_path(run)} and re-running freeze"
            )
        raise common.fail(message)
    from tools.assets.asset_spec import preflight_asset_contract

    preflight_asset_contract(run, inventory)
    from tools.regions.regions import build_critical_region_expectation

    inventory["status"] = "frozen"
    inventory["frozen_at"] = utc_now()
    payload["critical_region_expectation"] = build_critical_region_expectation(payload)
    payload["updated_at"] = utc_now()
    write_json(run.regions_path, payload)
    report = validate_inventory(run, require_frozen=True)
    if report["blockers"]:
        raise common.fail("frozen reference inventory is invalid: " + ", ".join(report["blockers"]))
    from tools.assets.reference_oracle import build_oracle, load_oracle, oracle_path, write_oracle

    oracle_file = oracle_path(run)
    if oracle_file.is_file():
        # validate_inventory 已确认 inventory 与 oracle 一致；此处读取哈希绑定 receipt。
        oracle = load_oracle(oracle_file)
    else:
        # 候选生成前冻结路线无关真值；同参考图的后续 freeze 必须复现它。
        oracle = build_oracle(run.load_meta()["source_sha256"], inventory)
        write_oracle(oracle_file, oracle)
    from tools.pipeline.ingest import build_region_tasks

    build_region_tasks(run)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "case": run.load_meta()["case"],
        "reference_sha256": run.load_meta()["source_sha256"],
        "inventory_sha256": report["inventory_sha256"],
        "oracle_sha256": oracle["oracle_sha256"],
        "regions_sha256": common.sha256_file(run.regions_path),
        "critical_region_expectation_sha256": canonical_sha256(
            read_json(run.regions_path).get("critical_region_expectation")
        ),
        "region_tasks_sha256": common.sha256_file(run.region_tasks_path),
        "object_count": report["object_count"],
        "counts": report["actual_counts"],
        **(
            {
                "topology_contract_count": report["topology_contract_count"],
                "topology_contracts_sha256": report["topology_contracts_sha256"],
            }
            if report["topology_contract_count"]
            else {}
        ),
        "frozen_at": inventory["frozen_at"],
        "status": "PASS",
    }
    write_json(run.root / RECEIPT_PATH, receipt)
    from tools.assets.asset_spec import freeze_asset_contract

    freeze_asset_contract(run)
    meta = run.load_meta()
    if meta["input_route"] == "svg-seeded" and run.external_seed_svg.is_file():
        from tools.qa.source_gate import evaluate_case_source_gate

        gate = evaluate_case_source_gate(
            run,
            run.external_seed_svg,
            candidate_role="external-seed",
        )
        from tools.core.contracts import record_source_gate_provenance

        record_source_gate_provenance(
            run,
            gate,
            immutable_external_seed=True,
        )
        if gate["decision"] != "reject":
            from tools.core.revisions import (
                bind_canonical_svg,
                materialize_svg,
                read_svg_text_exact,
            )

            scene = read_json(run.scene_path)
            bind_canonical_svg(
                scene,
                read_svg_text_exact(run.external_seed_svg),
                source_role="external-seed-proposal",
                source_sha256=common.sha256_file(run.external_seed_svg),
            )
            write_json(run.scene_path, scene)
            materialize_svg(run, scene)
    from tools.core.contracts import transition

    if run.load_meta()["workflow"]["state"] == "prepared":
        transition(run, "ready", "reference-inventory-frozen")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autofigure freeze",
        description="Freeze the reference-derived closed-world object inventory.",
    )
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    run = common.open_run(args.run_dir)
    receipt = freeze_inventory(run)
    sys.stdout.write(
        f"reference inventory frozen: {receipt['object_count']} objects; "
        f"receipt={run.root / RECEIPT_PATH}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
