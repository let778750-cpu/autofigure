"""Normalize untrusted SVG syntax and stable IDs without repairing its visuals."""

from __future__ import annotations

import argparse
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.core import common
from tools.core.contracts import read_json


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
TEXTUAL_KINDS = frozenset({"text", "formula"})


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.").lower()
    return result or "object"


def _format_bbox_number(value: int | float) -> str:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise common.fail("frozen visual bbox contains a non-finite number")
    if numeric == 0:
        numeric = 0.0
    return format(numeric, ".12g")


def _format_visual_bbox(value: object) -> str:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
    ):
        raise common.fail("frozen text/formula visual bbox must contain four numbers")
    numeric = [float(item) for item in value]
    if not all(math.isfinite(item) for item in numeric):
        raise common.fail("frozen visual bbox contains a non-finite number")
    if numeric[2] <= 0 or numeric[3] <= 0:
        raise common.fail("frozen text/formula visual bbox must have positive dimensions")
    return " ".join(_format_bbox_number(item) for item in numeric)


def _parse_visual_bbox(value: str) -> tuple[float, float, float, float] | None:
    try:
        fields = [float(item) for item in re.split(r"[\s,]+", value.strip()) if item]
    except ValueError:
        return None
    if len(fields) != 4 or not all(math.isfinite(item) for item in fields):
        return None
    return tuple(fields)  # type: ignore[return-value]


def _frozen_text_visual_bboxes(
    inventory: dict,
) -> tuple[dict[str, str], list[str]]:
    """Return only unambiguous, single-element textual bbox contracts."""

    expected: dict[str, str] = {}
    skipped: list[str] = []
    objects = inventory.get("objects", [])
    if not isinstance(objects, list):
        raise common.fail("frozen reference inventory objects must be a list")
    for index, item in enumerate(objects, start=1):
        if not isinstance(item, dict) or item.get("kind") not in TEXTUAL_KINDS:
            continue
        object_id = str(item.get("id") or f"inventory-object-{index}")
        element_ids = item.get("element_ids")
        if (
            not isinstance(element_ids, list)
            or len(element_ids) != 1
            or not isinstance(element_ids[0], str)
            or not element_ids[0]
        ):
            skipped.append(object_id)
            continue
        element_id = element_ids[0]
        formatted = _format_visual_bbox(item.get("bbox"))
        previous = expected.get(element_id)
        if previous is not None and previous != formatted:
            raise common.fail(
                f"frozen inventory assigns conflicting visual bboxes to {element_id}"
            )
        expected[element_id] = formatted
    return expected, skipped


def _inject_frozen_text_visual_bboxes(
    root: ET.Element,
    expected: dict[str, str],
) -> dict:
    by_id: defaultdict[str, list[ET.Element]] = defaultdict(list)
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        element_id = element.get("id")
        if element_id:
            by_id[element_id].append(element)

    injected: list[str] = []
    existing: list[str] = []
    for element_id, bbox_value in expected.items():
        matches = by_id.get(element_id, [])
        if len(matches) != 1:
            raise common.fail(
                f"frozen text/formula element {element_id} resolved {len(matches)} times"
            )
        element = matches[0]
        if _local(element.tag) != "text":
            raise common.fail(
                f"frozen text/formula element {element_id} is not an SVG text element"
            )
        current = element.get("data-visual-bbox")
        if current is None:
            element.set("data-visual-bbox", bbox_value)
            injected.append(element_id)
            continue
        parsed_current = _parse_visual_bbox(current)
        parsed_expected = _parse_visual_bbox(bbox_value)
        if parsed_current != parsed_expected:
            raise common.fail(
                f"conflicting data-visual-bbox for {element_id}: "
                f"source={current!r}, frozen={bbox_value!r}"
            )
        existing.append(element_id)
    return {
        "injected_ids": injected,
        "existing_ids": existing,
    }


def _frozen_topology_metadata(inventory: dict) -> dict[str, Any]:
    """Compile frozen topology contracts into unambiguous SVG metadata targets."""

    from tools.assets.reference_inventory import normalize_topology_contract

    objects = inventory.get("objects", [])
    if not isinstance(objects, list):
        raise common.fail("frozen reference inventory objects must be a list")
    relations: dict[str, dict[str, str]] = {}
    pair_partners: defaultdict[str, set[str]] = defaultdict(set)
    seen_pairs: dict[tuple[str, str], str] = {}
    pair_count = 0
    for index, item in enumerate(objects, start=1):
        if not isinstance(item, dict):
            raise common.fail("frozen reference inventory objects must be mappings")
        contract, errors = normalize_topology_contract(item)
        if contract is None:
            if errors:
                object_id = str(item.get("id") or f"inventory-object-{index}")
                raise common.fail(
                    f"frozen topology contract for {object_id} is invalid: "
                    + ", ".join(errors)
                )
            continue
        object_id = str(item.get("id") or f"inventory-object-{index}")
        for relation in contract["relations"]:
            element_id = relation.get("element_id")
            if element_id is None:
                continue
            element_id = str(element_id)
            if element_id in relations:
                previous = relations[element_id]["object_id"]
                raise common.fail(
                    "duplicate frozen topology relation target element "
                    f"{element_id}: {previous}, {object_id}"
                )
            relations[element_id] = {
                "object_id": object_id,
                "relation_id": str(relation["id"]),
                "source_id": str(relation["source_id"]),
                "target_id": str(relation["target_id"]),
                "relation": str(relation["relation"]),
            }
        for pair in contract["required_pairs"]:
            left = str(pair["a"])
            right = str(pair["b"])
            key = tuple(sorted((left, right)))
            if key in seen_pairs:
                raise common.fail(
                    "duplicate frozen topology pair "
                    f"{left}:{right}: {seen_pairs[key]}, {object_id}"
                )
            seen_pairs[key] = object_id
            pair_partners[left].add(right)
            pair_partners[right].add(left)
            pair_count += 1
    return {
        "relations": relations,
        "pair_partners": {
            element_id: sorted(partners)
            for element_id, partners in sorted(pair_partners.items())
        },
        "pair_count": pair_count,
    }


def _inject_frozen_topology_metadata(
    root: ET.Element,
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Bind frozen relation/pair semantics to unique physical SVG leaves."""

    by_id: defaultdict[str, list[ET.Element]] = defaultdict(list)
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        element_id = element.get("id")
        if element_id:
            by_id[element_id].append(element)

    container_tags = {
        "svg",
        "g",
        "defs",
        "symbol",
        "marker",
        "clippath",
        "mask",
        "pattern",
    }

    def resolve_leaf(element_id: str, contract_kind: str) -> ET.Element:
        matches = by_id.get(element_id, [])
        if not matches:
            raise common.fail(
                f"frozen topology {contract_kind} element {element_id} is missing from SVG"
            )
        if len(matches) != 1:
            raise common.fail(
                f"frozen topology {contract_kind} target {element_id} "
                f"resolved {len(matches)} times"
            )
        element = matches[0]
        if _local(element.tag).lower() in container_tags:
            raise common.fail(
                f"frozen topology {contract_kind} element {element_id} is not an SVG leaf"
            )
        return element

    relation_injected: list[str] = []
    relation_existing: list[str] = []
    for element_id, relation in expected["relations"].items():
        element = resolve_leaf(element_id, "relation")
        attributes = {
            "data-source-id": relation["source_id"],
            "data-target-id": relation["target_id"],
            "data-topology-relation": relation["relation"],
        }
        injected = False
        for name, value in attributes.items():
            current = element.get(name)
            if current is None:
                element.set(name, value)
                injected = True
            elif current != value:
                raise common.fail(
                    f"conflicting {name} for topology relation element {element_id}: "
                    f"source={current!r}, frozen={value!r}"
                )
        legacy_relation = element.get("data-relation")
        if legacy_relation is not None and legacy_relation != relation["relation"]:
            raise common.fail(
                f"conflicting data-relation for topology relation element {element_id}: "
                f"source={legacy_relation!r}, frozen={relation['relation']!r}"
            )
        (relation_injected if injected else relation_existing).append(element_id)

    pair_injected: list[str] = []
    pair_existing: list[str] = []
    pair_canonicalized: list[str] = []
    for element_id, partners in expected["pair_partners"].items():
        element = resolve_leaf(element_id, "pair")
        canonical = " ".join(partners)
        current = element.get("data-pair-with")
        if current is None:
            element.set("data-pair-with", canonical)
            pair_injected.append(element_id)
            continue
        current_tokens = [
            token for token in re.split(r"[\s,]+", current.strip()) if token
        ]
        if (
            len(current_tokens) != len(set(current_tokens))
            or element_id in current_tokens
            or set(current_tokens) != set(partners)
        ):
            raise common.fail(
                f"conflicting data-pair-with for topology pair element {element_id}: "
                f"source={current!r}, frozen={canonical!r}"
            )
        if current != canonical:
            element.set("data-pair-with", canonical)
            pair_canonicalized.append(element_id)
        else:
            pair_existing.append(element_id)

    return {
        "relation_contract_count": len(expected["relations"]),
        "relation_injected_ids": relation_injected,
        "relation_existing_ids": relation_existing,
        "pair_contract_count": expected["pair_count"],
        "pair_target_count": len(expected["pair_partners"]),
        "pair_injected_ids": pair_injected,
        "pair_existing_ids": pair_existing,
        "pair_canonicalized_ids": pair_canonicalized,
    }


def normalize_source(run: common.Run, source: Path, output: Path) -> dict:
    from tools.assets.reference_inventory import require_frozen_inventory

    require_frozen_inventory(run)
    receipt_path = run.qa_dir / "reference-inventory-receipt.json"
    receipt = read_json(receipt_path)
    regions = read_json(run.regions_path)
    inventory = regions.get("reference_inventory")
    if not isinstance(inventory, dict):
        raise common.fail("source normalization requires a frozen reference inventory")
    visual_bboxes, skipped_textual_objects = _frozen_text_visual_bboxes(inventory)
    topology_metadata = _frozen_topology_metadata(inventory)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.fromstring(source.read_bytes(), parser=parser)
    except ET.ParseError as exc:
        raise common.fail(f"invalid SVG source: {exc}") from exc
    if _local(root.tag) != "svg":
        raise common.fail("source normalization requires an SVG root")
    meta = run.load_meta()
    inventory_sha256 = receipt["inventory_sha256"]
    existing_inventory_sha256 = root.get("data-object-inventory-sha256")
    if (
        existing_inventory_sha256 is not None
        and existing_inventory_sha256 != inventory_sha256
    ):
        raise common.fail(
            "source data-object-inventory-sha256 conflicts with the frozen receipt"
        )
    root.set("data-source-schema-version", "4.0.0")
    root.set("data-case", meta["case"])
    root.set("data-reference-sha256", meta["source_sha256"])
    root.set("data-object-inventory-sha256", inventory_sha256)
    root.set("data-stable-element-ids", "true")
    root.set("data-relations-exhaustive", "true")

    used = {
        element.get("id")
        for element in root.iter()
        if isinstance(element.tag, str) and element.get("id")
    }
    counters: defaultdict[tuple[str, str], int] = defaultdict(int)
    assigned: list[str] = []

    def walk(parent: ET.Element, parent_key: str) -> None:
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            tag = _local(child.tag)
            child_id = child.get("id")
            key = _slug(child_id or parent_key)
            if child_id is None and tag not in {"defs", "metadata", "title", "desc"}:
                counter_key = (_slug(parent_key), tag)
                counters[counter_key] += 1
                stem = f"{counter_key[0]}-{_slug(tag)}-{counters[counter_key]:03d}"
                candidate = stem
                suffix = 1
                while candidate in used:
                    suffix += 1
                    candidate = f"{stem}-{suffix}"
                child.set("id", candidate)
                child_id = candidate
                key = candidate
                used.add(candidate)
                assigned.append(candidate)
            walk(child, key)

    walk(root, "scene")
    bbox_report = _inject_frozen_text_visual_bboxes(root, visual_bboxes)
    topology_report = _inject_frozen_topology_metadata(root, topology_metadata)
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(payload + b"\n")
    temporary.replace(output)
    return {
        "schema_version": "4.0.0",
        "kind": "normalized_svg_source",
        "source_sha256": common.sha256_file(source),
        "output_sha256": common.sha256_file(output),
        "assigned_id_count": len(assigned),
        "assigned_ids": assigned,
        "object_inventory_sha256": inventory_sha256,
        "visual_bbox_contract_count": len(visual_bboxes),
        "visual_bbox_injected_count": len(bbox_report["injected_ids"]),
        "visual_bbox_injected_ids": bbox_report["injected_ids"],
        "visual_bbox_existing_count": len(bbox_report["existing_ids"]),
        "visual_bbox_existing_ids": bbox_report["existing_ids"],
        "visual_bbox_skipped_textual_object_ids": skipped_textual_objects,
        "topology_relation_contract_count": topology_report[
            "relation_contract_count"
        ],
        "topology_relation_metadata_injected_count": len(
            topology_report["relation_injected_ids"]
        ),
        "topology_relation_metadata_injected_ids": topology_report[
            "relation_injected_ids"
        ],
        "topology_relation_metadata_existing_count": len(
            topology_report["relation_existing_ids"]
        ),
        "topology_relation_metadata_existing_ids": topology_report[
            "relation_existing_ids"
        ],
        "topology_pair_contract_count": topology_report["pair_contract_count"],
        "topology_pair_metadata_target_count": topology_report["pair_target_count"],
        "topology_pair_metadata_injected_count": len(
            topology_report["pair_injected_ids"]
        ),
        "topology_pair_metadata_injected_ids": topology_report["pair_injected_ids"],
        "topology_pair_metadata_existing_count": len(
            topology_report["pair_existing_ids"]
        ),
        "topology_pair_metadata_existing_ids": topology_report["pair_existing_ids"],
        "topology_pair_metadata_canonicalized_ids": topology_report[
            "pair_canonicalized_ids"
        ],
        "visual_repairs_applied": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure normalize-source", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    run = common.open_run(args.run_dir)
    report = normalize_source(run, args.source.resolve(), args.output.resolve())
    sys.stdout.write(
        f"normalized source: {args.output} ({report['assigned_id_count']} ids assigned)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
