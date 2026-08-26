"""Canonical, backend-neutral contracts for logical scientific microassets.

An AssetSpec is derived only when three frozen identities agree exactly:

* a ``scene.json`` logical group;
* one ``assets.json.microasset_opportunity_map`` record; and
* one reference-inventory object with a valid topology contract.

The module deliberately does not infer assets from names, visual kinds, or
member geometry.  Missing or conflicting frozen evidence therefore produces
no guessed contract.  Invalid evidence fails closed before ``scene`` is
mutated.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any

from tools import common
from tools.contracts import (
    SCHEMA_VERSION as PROJECT_SCHEMA_VERSION,
    TASK_MODE,
    TRACE_ELIGIBILITY_VALUES,
    ContractError,
    read_json,
    utc_now,
    write_json,
)
from tools.reference_inventory import (
    RECEIPT_KIND as INVENTORY_RECEIPT_KIND,
    canonical_sha256 as inventory_sha256,
    normalize_topology_contract,
)
from tools.svggeom import parse_transform


ASSET_SPEC_VERSION = "1.0.0"
ASSET_SPEC_KIND = "asset_spec"
ASSET_CONTRACT_VERSION = "1.0.0"
ASSET_CONTRACT_KIND = "reference_bound_asset_contract"
ASSET_CONTRACT_RECEIPT_KIND = "asset_contract_freeze_receipt"
ASSET_CONTRACT_RECEIPT_PATH = "qa/asset-contract-receipt.json"

_IMPLEMENTATION_EDITABILITY = {
    "native_editable_vector": True,
    "native_editable_shapes": True,
    "native_editable_freeform": True,
}
_NATIVE_IMPLEMENTATIONS = {
    implementation
    for implementation, editable in _IMPLEMENTATION_EDITABILITY.items()
    if editable
}
_ASSET_POLICY_KEYS = {
    "formal_content_native",
    "authorized_atomic_raster_only",
    "whole_reference_forbidden",
}
_OPPORTUNITY_KEYS = {
    "slot_id",
    "object_kind",
    "implementation",
    "reference_bbox",
    "reference_sha256",
    "expected_group_role",
    "member_ids",
    "authorization",
    "output_bbox",
    "implementation_bbox",
    "editable",
    "single_logical_asset",
    "allow_nonuniform_scale",
    "allow_non_uniform_scale",
    "nonuniform_scale",
    "non_uniform_scale",
    "data-allow-nonuniform-scale",
    "allow_stretch",
    "data-allow-stretch",
    "preserve_aspect_ratio",
    "preserveAspectRatio",
    "data-preserve-aspect-ratio",
    "aspect_ratio_policy",
    "scale_x",
    "scale_y",
    "scaleX",
    "scaleY",
    "transform",
    # freeze 时由 annotate_trace_eligibility 实测写入的可选冻结字段(成对出现);
    # 旧案例机会图项无此字段,schema 保持只读兼容。
    "trace_eligibility",
    "trace_eligibility_statistics",
}
_SPEC_KEYS = {
    "schema_version",
    "kind",
    "asset_id",
    "semantic_kind",
    "reference_bbox",
    "member_ids",
    "member_role_counts",
    "member_roles",
    "internal_topology_relations",
    "component_count",
    "topology_scope_element_id",
    "implementation",
    "editable",
    "authorization",
    "reference_sha256",
    "aspect_ratio_policy",
    "single_logical_asset",
}
_TRANSFORM_RE = re.compile(
    r"(?:matrix|translate|scale|rotate|skewX|skewY)\s*\([^)]*\)",
)

# Atomic-vector asset entry contract for the assets.json ``assets`` list.  An
# atomic-vector entry is the authorized traced vector form of one microasset;
# the original atomic-raster entry stays in the same document as its explicit
# fallback layer.  Field set per docs/ILLUSTRATOR_VECTOR_AUTHORING_PLAN.md
# Phase 2.
ATOMIC_VECTOR_ID_PREFIX = "atomic:"
ATOMIC_VECTOR_SOURCE = "vtracer-trace"
ATOMIC_VECTOR_FALLBACK_SOURCE = "reference_crop"
_ATOMIC_VECTOR_ASSET_KEYS = {
    "id",
    "editable",
    "source",
    "vector_source_svg",
    "trace_method",
    "trace_engine_version",
    "authorization_basis",
    "rights_status",
    "fallback_atomic_raster",
    "ink_contract_region_id",
    "trace_eligibility",
}
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:")


class AssetSpecError(ValueError):
    """Raised when frozen asset evidence cannot produce one safe contract."""

    def __init__(self, errors: list[str] | tuple[str, ...] | str):
        values = [errors] if isinstance(errors, str) else list(errors)
        self.errors = tuple(dict.fromkeys(str(value) for value in values))
        super().__init__("; ".join(self.errors))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def asset_spec_sha256(spec: dict[str, Any]) -> str:
    """Return the stable content identity of one normalized AssetSpec."""

    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def _stable_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_eligibility_statistics(value: Any) -> bool:
    """Statistics dicts stay open-ended so future classifier signals remain
    readable on already-frozen cases; only shape and numeric values are enforced."""

    return (
        isinstance(value, dict)
        and bool(value)
        and all(_stable_string(key) for key in value)
        and all(_finite(item) for item in value.values())
    )


def _normalize_bbox(value: Any) -> list[float] | None:
    if not (
        isinstance(value, list)
        and len(value) == 4
        and all(_finite(number) for number in value)
        and value[2] > 0
        and value[3] > 0
    ):
        return None
    return [float(number) for number in value]


def _valid_contract_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_valid_contract_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _valid_contract_value(item)
            for key, item in value.items()
        )
    return False


def _asset_contract_payload(
    assets: Any,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(assets, dict):
        return None, ["asset-contract:assets-invalid"]
    if assets.get("kind") != "assets":
        errors.append("asset-contract:assets-kind")
    reference_sha256 = assets.get("reference_sha256")
    if not _valid_sha256(reference_sha256):
        errors.append("asset-contract:reference-sha256")
        reference_sha256 = ""
    else:
        reference_sha256 = reference_sha256.lower()

    policy = assets.get("policy")
    if not isinstance(policy, dict) or not _valid_contract_value(policy):
        errors.append("asset-contract:policy")
        policy = {}
    else:
        if set(policy) != _ASSET_POLICY_KEYS:
            errors.append("asset-contract:policy-schema")
        for field in sorted(_ASSET_POLICY_KEYS):
            if policy.get(field) is not True:
                errors.append(f"asset-contract:policy:{field}")

    if "microasset_opportunity_map" not in assets:
        errors.append("asset-contract:opportunity-map-missing")
    raw_opportunities = assets.get("microasset_opportunity_map")
    if not isinstance(raw_opportunities, list):
        errors.append("asset-contract:opportunity-map")
        raw_opportunities = []
    opportunities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_opportunities):
        label = str(index + 1)
        if not isinstance(raw, dict) or not _valid_contract_value(raw):
            errors.append(f"asset-contract:opportunity:{label}:invalid")
            continue
        unknown_keys = set(raw) - _OPPORTUNITY_KEYS
        if unknown_keys:
            errors.append(f"asset-contract:opportunity:{label}:schema")
        slot_id = raw.get("slot_id")
        if not _stable_string(slot_id):
            errors.append(f"asset-contract:opportunity:{label}:slot-id")
            continue
        label = slot_id
        if slot_id in seen_ids:
            errors.append(f"asset-contract:opportunity:{slot_id}:duplicate")
            continue
        seen_ids.add(slot_id)
        if not _stable_string(raw.get("object_kind")):
            errors.append(f"asset-contract:opportunity:{slot_id}:object_kind")
        implementation = raw.get("implementation")
        if implementation not in _IMPLEMENTATION_EDITABILITY:
            errors.append(f"asset-contract:opportunity:{slot_id}:implementation")
        expected_group_role = raw.get("expected_group_role")
        if expected_group_role is not None and not _stable_string(expected_group_role):
            errors.append(f"asset-contract:opportunity:{slot_id}:expected_group_role")
        member_ids = raw.get("member_ids")
        if member_ids is not None and (
            not isinstance(member_ids, list)
            or not all(_stable_string(member_id) for member_id in member_ids)
            or len(member_ids) != len(set(member_ids))
        ):
            errors.append(f"asset-contract:opportunity:{slot_id}:member_ids")
        authorization = raw.get("authorization")
        if authorization is not None and (
            not isinstance(authorization, dict)
            or set(authorization) != {"authorized", "basis"}
            or authorization.get("authorized") is not True
            or not _stable_string(authorization.get("basis"))
        ):
            errors.append(f"asset-contract:opportunity:{slot_id}:authorization")
        if "editable" in raw and raw.get("editable") is not True:
            errors.append(f"asset-contract:opportunity:{slot_id}:editable")
        if "single_logical_asset" in raw and raw.get("single_logical_asset") is not True:
            errors.append(f"asset-contract:opportunity:{slot_id}:single_logical_asset")
        if _record_declares_nonuniform_scale(raw):
            errors.append(f"asset-contract:opportunity:{slot_id}:nonuniform-scale")
        eligibility = raw.get("trace_eligibility")
        eligibility_statistics = raw.get("trace_eligibility_statistics")
        if (eligibility is None) != (eligibility_statistics is None):
            errors.append(f"asset-contract:opportunity:{slot_id}:trace-eligibility-pairing")
        if eligibility is not None and eligibility not in TRACE_ELIGIBILITY_VALUES:
            errors.append(f"asset-contract:opportunity:{slot_id}:trace-eligibility")
        if eligibility_statistics is not None and not _valid_eligibility_statistics(
            eligibility_statistics
        ):
            errors.append(
                f"asset-contract:opportunity:{slot_id}:trace-eligibility-statistics"
            )
        opportunity_reference = raw.get("reference_sha256")
        if (
            not _valid_sha256(opportunity_reference)
            or opportunity_reference.lower() != reference_sha256
        ):
            errors.append(f"asset-contract:opportunity:{slot_id}:reference-sha256")
        bbox = _normalize_bbox(raw.get("reference_bbox"))
        if bbox is None or bbox[0] < 0 or bbox[1] < 0:
            errors.append(f"asset-contract:opportunity:{slot_id}:reference-bbox")
            continue
        for field in ("output_bbox", "implementation_bbox"):
            if field in raw:
                declared_bbox = _normalize_bbox(raw.get(field))
                if declared_bbox is None or not _same_aspect_ratio(bbox, declared_bbox):
                    errors.append(f"asset-contract:opportunity:{slot_id}:{field}")
        normalized = deepcopy(raw)
        normalized["reference_sha256"] = reference_sha256
        normalized["reference_bbox"] = bbox
        opportunities.append(normalized)
    if errors:
        return None, list(dict.fromkeys(errors))
    return {
        "schema_version": ASSET_CONTRACT_VERSION,
        "kind": ASSET_CONTRACT_KIND,
        "reference_sha256": reference_sha256,
        "policy": deepcopy(policy),
        "microasset_opportunity_map": sorted(
            opportunities,
            key=lambda item: (item["slot_id"], canonical_json(item)),
        ),
    }, []


def canonical_asset_contract_payload(assets: dict[str, Any]) -> dict[str, Any]:
    """Return the reference-bound input oracle, excluding generated assets."""

    payload, errors = _asset_contract_payload(assets)
    if payload is None or errors:
        raise AssetSpecError(errors or "asset-contract:invalid")
    return payload


def asset_contract_sha256(assets: dict[str, Any]) -> str:
    """Hash only policy and the reference-derived microasset opportunity map."""

    payload = canonical_asset_contract_payload(assets)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _asset_contract_preflight_blockers(
    run: common.Run,
    inventory: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    try:
        meta = run.load_meta()
        assets = read_json(run.assets_path)
    except (ContractError, OSError, ValueError, KeyError, TypeError, SystemExit):
        return None, ["asset-contract:preflight-input-invalid"]
    payload, payload_errors = _asset_contract_payload(assets)
    blockers.extend(payload_errors)
    case = meta.get("case")
    reference_sha256 = meta.get("source_sha256")
    if assets.get("schema_version") != PROJECT_SCHEMA_VERSION:
        blockers.append("asset-contract:assets-schema-version")
    if assets.get("task_mode") != TASK_MODE:
        blockers.append("asset-contract:assets-task-mode")
    if assets.get("case") != case:
        blockers.append("asset-contract:assets-case")
    if not _valid_sha256(reference_sha256):
        blockers.append("asset-contract:reference-sha256:run")
        reference_sha256 = ""
    else:
        reference_sha256 = reference_sha256.lower()
    if (
        not _valid_sha256(assets.get("reference_sha256"))
        or assets["reference_sha256"].lower() != reference_sha256
    ):
        blockers.append("asset-contract:assets-reference-sha256")
    inventory = _inventory_payload(inventory)
    if (
        not _valid_sha256(inventory.get("reference_sha256"))
        or inventory["reference_sha256"].lower() != reference_sha256
    ):
        blockers.append("asset-contract:inventory-reference-sha256")
    objects = inventory.get("objects")
    if not isinstance(objects, list):
        blockers.append("asset-contract:inventory-objects")
        objects = []
    inventory_objects: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(objects, start=1):
        if not isinstance(item, dict) or not _stable_string(item.get("id")):
            blockers.append(f"asset-contract:inventory-object:{index}:identity")
            continue
        object_id = item["id"]
        if object_id in inventory_objects:
            blockers.append(f"asset-contract:inventory-object:{object_id}:duplicate")
            continue
        inventory_objects[object_id] = item
    width = meta.get("width")
    height = meta.get("height")
    canvas_valid = _finite(width) and _finite(height) and width > 0 and height > 0
    if not canvas_valid:
        blockers.append("asset-contract:run-canvas")
    if payload is not None:
        for opportunity in payload["microasset_opportunity_map"]:
            asset_id = opportunity["slot_id"]
            object_contract = inventory_objects.get(asset_id)
            if object_contract is None:
                blockers.append(f"asset-contract:opportunity:{asset_id}:inventory-object")
                continue
            if object_contract.get("kind") != opportunity.get("object_kind"):
                blockers.append(f"asset-contract:opportunity:{asset_id}:object-kind")
            inventory_members = object_contract.get("element_ids")
            declared_members = opportunity.get("member_ids")
            if declared_members is not None and (
                not isinstance(inventory_members, list)
                or set(declared_members) != set(inventory_members)
            ):
                blockers.append(f"asset-contract:opportunity:{asset_id}:member-ids")
            inventory_bbox = _normalize_bbox(object_contract.get("bbox"))
            if inventory_bbox != opportunity.get("reference_bbox"):
                blockers.append(f"asset-contract:opportunity:{asset_id}:reference-bbox")
            elif canvas_valid:
                x, y, box_width, box_height = inventory_bbox
                if x + box_width > float(width) + 1e-6 or y + box_height > float(height) + 1e-6:
                    blockers.append(f"asset-contract:opportunity:{asset_id}:canvas-bounds")
            contract, topology_errors = normalize_topology_contract(object_contract)
            if contract is None or topology_errors:
                blockers.append(f"asset-contract:opportunity:{asset_id}:topology-contract")
            _, authorization_errors = _authorization(
                assets,
                opportunity,
                opportunity["implementation"],
                asset_id,
            )
            if authorization_errors:
                blockers.append(f"asset-contract:opportunity:{asset_id}:authorization")
    return payload, list(dict.fromkeys(blockers))


def preflight_asset_contract(
    run: common.Run,
    inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the asset oracle before the inventory freeze mutates case state."""

    if inventory is None:
        try:
            inventory = read_json(run.regions_path)
        except ContractError as exc:
            raise common.fail(f"asset contract preflight cannot read inventory: {exc}") from exc
    payload, blockers = _asset_contract_preflight_blockers(run, inventory)
    if payload is None or blockers:
        raise common.fail("asset contract preflight failed: " + ", ".join(blockers))
    return payload


def _asset_contract_expectation(
    run: common.Run,
    *,
    inventory_receipt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    try:
        meta = run.load_meta()
    except (OSError, ValueError, KeyError, TypeError, SystemExit):
        return None, ["asset-contract:run-meta-invalid"]
    case = meta.get("case")
    reference_sha256 = meta.get("source_sha256")
    if not _stable_string(case):
        blockers.append("asset-contract:case")
    if not _valid_sha256(reference_sha256):
        blockers.append("asset-contract:reference-sha256:run")
        reference_sha256 = ""
    else:
        reference_sha256 = reference_sha256.lower()
    if not run.source_png.is_file():
        blockers.append("asset-contract:reference-missing")
    elif reference_sha256:
        try:
            actual_reference_sha256 = common.sha256_file(run.source_png).lower()
        except OSError:
            blockers.append("asset-contract:reference-unreadable")
        else:
            if actual_reference_sha256 != reference_sha256:
                blockers.append("asset-contract:reference-file-sha256")

    try:
        assets = read_json(run.assets_path)
    except ContractError:
        return None, [*blockers, "asset-contract:assets-invalid"]
    payload, payload_errors = _asset_contract_payload(assets)
    blockers.extend(payload_errors)
    if assets.get("schema_version") != PROJECT_SCHEMA_VERSION:
        blockers.append("asset-contract:assets-schema-version")
    if assets.get("task_mode") != TASK_MODE:
        blockers.append("asset-contract:assets-task-mode")
    if assets.get("case") != case:
        blockers.append("asset-contract:assets-case")
    if (
        not _valid_sha256(assets.get("reference_sha256"))
        or assets["reference_sha256"].lower() != reference_sha256
    ):
        blockers.append("asset-contract:assets-reference-sha256")

    try:
        regions = read_json(run.regions_path)
    except ContractError:
        return None, [*blockers, "asset-contract:inventory-invalid"]
    inventory = regions.get("reference_inventory")
    if not isinstance(inventory, dict):
        return None, [*blockers, "asset-contract:inventory-missing"]
    if inventory.get("status") != "frozen":
        blockers.append("asset-contract:inventory-not-frozen")
    if (
        not _valid_sha256(inventory.get("reference_sha256"))
        or inventory["reference_sha256"].lower() != reference_sha256
    ):
        blockers.append("asset-contract:inventory-reference-sha256")
    try:
        from tools.reference_inventory import inventory_blockers

        reference_inventory_blockers = inventory_blockers(run)
    except (Exception, SystemExit):
        blockers.append("asset-contract:inventory-validation-error")
    else:
        blockers.extend(
            f"asset-contract:inventory-contract:{blocker}"
            for blocker in reference_inventory_blockers
        )
    try:
        current_inventory_sha256 = inventory_sha256(inventory)
    except (TypeError, ValueError):
        return None, [*blockers, "asset-contract:inventory-hash-invalid"]
    if inventory_receipt is None:
        inventory_receipt_path = run.qa_dir / "reference-inventory-receipt.json"
        if not inventory_receipt_path.is_file():
            return None, [*blockers, "asset-contract:inventory-receipt-missing"]
        try:
            inventory_receipt = read_json(inventory_receipt_path)
        except ContractError:
            return None, [*blockers, "asset-contract:inventory-receipt-invalid"]
    if (
        inventory_receipt.get("kind") != INVENTORY_RECEIPT_KIND
        or inventory_receipt.get("status") != "PASS"
        or inventory_receipt.get("case") != case
        or inventory_receipt.get("reference_sha256") != reference_sha256
    ):
        blockers.append("asset-contract:inventory-receipt-identity")
    if inventory_receipt.get("inventory_sha256") != current_inventory_sha256:
        blockers.append("asset-contract:inventory-receipt-stale")

    if payload is not None:
        objects = inventory.get("objects")
        if not isinstance(objects, list):
            blockers.append("asset-contract:inventory-objects")
            objects = []
        inventory_objects: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(objects, start=1):
            if not isinstance(item, dict) or not _stable_string(item.get("id")):
                blockers.append(f"asset-contract:inventory-object:{index}:identity")
                continue
            object_id = item["id"]
            if object_id in inventory_objects:
                blockers.append(f"asset-contract:inventory-object:{object_id}:duplicate")
                continue
            inventory_objects[object_id] = item
        width = meta.get("width")
        height = meta.get("height")
        canvas_valid = _finite(width) and _finite(height) and width > 0 and height > 0
        if not canvas_valid:
            blockers.append("asset-contract:run-canvas")
        for opportunity in payload["microasset_opportunity_map"]:
            asset_id = opportunity["slot_id"]
            object_contract = inventory_objects.get(asset_id)
            if object_contract is None:
                blockers.append(f"asset-contract:opportunity:{asset_id}:inventory-object")
                continue
            if object_contract.get("kind") != opportunity.get("object_kind"):
                blockers.append(f"asset-contract:opportunity:{asset_id}:object-kind")
            declared_members = opportunity.get("member_ids")
            if declared_members is not None and (
                not isinstance(object_contract.get("element_ids"), list)
                or set(declared_members) != set(object_contract["element_ids"])
            ):
                blockers.append(f"asset-contract:opportunity:{asset_id}:member-ids")
            inventory_bbox = _normalize_bbox(object_contract.get("bbox"))
            if inventory_bbox != opportunity.get("reference_bbox"):
                blockers.append(f"asset-contract:opportunity:{asset_id}:reference-bbox")
            elif canvas_valid:
                x, y, box_width, box_height = inventory_bbox
                if x + box_width > float(width) + 1e-6 or y + box_height > float(height) + 1e-6:
                    blockers.append(f"asset-contract:opportunity:{asset_id}:canvas-bounds")
            contract, topology_errors = normalize_topology_contract(object_contract)
            if contract is None or topology_errors:
                blockers.append(f"asset-contract:opportunity:{asset_id}:topology-contract")
            _, authorization_errors = _authorization(
                assets,
                opportunity,
                opportunity["implementation"],
                asset_id,
            )
            if authorization_errors:
                blockers.append(f"asset-contract:opportunity:{asset_id}:authorization")

    blockers = list(dict.fromkeys(blockers))
    if blockers or payload is None:
        return None, blockers or ["asset-contract:invalid"]
    contract_sha256 = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    opportunity_map = payload["microasset_opportunity_map"]
    return {
        "schema_version": ASSET_CONTRACT_VERSION,
        "kind": ASSET_CONTRACT_RECEIPT_KIND,
        "case": case,
        "reference_sha256": reference_sha256,
        "inventory_sha256": current_inventory_sha256,
        "asset_contract_sha256": contract_sha256,
        "policy_sha256": inventory_sha256(payload["policy"]),
        "microasset_opportunity_map_sha256": inventory_sha256(opportunity_map),
        "opportunity_count": len(opportunity_map),
        "status": "PASS",
    }, []


def annotate_trace_eligibility(run: common.Run) -> None:
    """Stamp measured trace eligibility onto every opportunity map record.

    The stamp is a pure function of the hash-bound reference crop and the
    record's ``reference_bbox`` (same tight-crop rounding as the converter),
    so repeated freezes rewrite identical values and leave assets.json
    untouched.  Records carry both fields or neither; the receipt binds the
    stamped values, so the frozen map always reflects measurement rather than
    authored claims.
    """

    assets = read_json(run.assets_path)
    opportunities = assets.get("microasset_opportunity_map")
    if not isinstance(opportunities, list):
        return
    targets: list[tuple[dict[str, Any], list[float]]] = []
    for item in opportunities:
        if not isinstance(item, dict):
            continue
        bbox = _normalize_bbox(item.get("reference_bbox"))
        if bbox is None or bbox[0] < 0 or bbox[1] < 0:
            continue
        targets.append((item, bbox))
    if not targets:
        return
    if not run.source_png.is_file():
        raise common.fail(
            "asset contract freeze requires reference.png for trace eligibility stamping"
        )
    from PIL import Image

    from tools.asset_trace import compute_trace_eligibility

    changed = False
    with Image.open(run.source_png) as reference:
        rgb = reference.convert("RGB")
        for item, bbox in targets:
            x, y, width, height = bbox
            crop = rgb.crop((round(x), round(y), round(x + width), round(y + height)))
            result = compute_trace_eligibility(crop)
            if (
                item.get("trace_eligibility") != result["classification"]
                or item.get("trace_eligibility_statistics") != result["statistics"]
            ):
                item["trace_eligibility"] = result["classification"]
                item["trace_eligibility_statistics"] = result["statistics"]
                changed = True
    if changed:
        assets["updated_at"] = utc_now()
        write_json(run.assets_path, assets)


def freeze_asset_contract(run: common.Run) -> dict[str, Any]:
    """Freeze the reference/inventory-bound asset evaluation oracle."""

    annotate_trace_eligibility(run)
    expected, blockers = _asset_contract_expectation(run)
    if expected is None or blockers:
        raise common.fail("asset contract cannot be frozen: " + ", ".join(blockers))
    receipt = {**expected, "frozen_at": utc_now()}
    write_json(run.root / ASSET_CONTRACT_RECEIPT_PATH, receipt)
    return receipt


def asset_contract_blockers(run: common.Run) -> list[str]:
    """Return stale/missing Asset Contract receipt blockers for check wiring."""

    expected, blockers = _asset_contract_expectation(run)
    if expected is None or blockers:
        return list(dict.fromkeys(blockers or ["asset-contract:invalid"]))
    receipt_path = run.root / ASSET_CONTRACT_RECEIPT_PATH
    if not receipt_path.is_file():
        return ["asset-contract:receipt-missing"]
    try:
        receipt = read_json(receipt_path)
    except ContractError:
        return ["asset-contract:receipt-invalid"]
    findings: list[str] = []
    if set(receipt) != {*expected, "frozen_at"}:
        findings.append("asset-contract:receipt-schema")
    for field, value in expected.items():
        if receipt.get(field) != value:
            findings.append(f"asset-contract:receipt-stale:{field}")
    if not _stable_string(receipt.get("frozen_at")):
        findings.append("asset-contract:receipt-stale:frozen_at")
    return list(dict.fromkeys(findings))


def _bbox_within_canvas(bbox: list[float], scene: dict[str, Any]) -> bool:
    canvas = scene.get("canvas")
    if not isinstance(canvas, dict):
        return True
    width = canvas.get("width")
    height = canvas.get("height")
    if not (_finite(width) and _finite(height) and width > 0 and height > 0):
        return False
    x, y, box_width, box_height = bbox
    tolerance = 1e-6
    return (
        x >= -tolerance
        and y >= -tolerance
        and x + box_width <= float(width) + tolerance
        and y + box_height <= float(height) + tolerance
    )


def _boolean_declaration(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "allow", "preserve"}:
        return True
    if normalized in {"0", "false", "none", "no", "deny"}:
        return False
    return None


def _transform_is_uniform(value: Any) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    matches = list(_TRANSFORM_RE.finditer(value))
    if not matches:
        return False
    remainder = _TRANSFORM_RE.sub("", value)
    if remainder.strip(" \t\r\n,"):
        return False
    expected_arity = {
        "matrix": {6},
        "translate": {1, 2},
        "scale": {1, 2},
        "rotate": {1, 3},
        "skewX": {1},
        "skewY": {1},
    }
    for match in matches:
        text = match.group(0)
        name, arguments_text = text.split("(", 1)
        arguments_text = arguments_text[:-1].strip()
        if (
            not arguments_text
            or re.search(r",\s*,", arguments_text)
            or arguments_text.startswith(",")
            or arguments_text.endswith(",")
        ):
            return False
        try:
            arguments = [
                float(argument)
                for argument in re.split(r"[\s,]+", arguments_text)
                if argument
            ]
        except ValueError:
            return False
        if len(arguments) not in expected_arity[name.strip()] or not all(
            math.isfinite(argument) for argument in arguments
        ):
            return False
    try:
        matrix = parse_transform(value)
    except (IndexError, TypeError, ValueError):
        return False
    first_scale = math.hypot(matrix.a, matrix.b)
    second_scale = math.hypot(matrix.c, matrix.d)
    if first_scale <= 1e-12 or second_scale <= 1e-12:
        return False
    tolerance = 1e-8 * max(first_scale, second_scale, 1.0)
    orthogonality_tolerance = 1e-8 * max(first_scale * second_scale, 1.0)
    return (
        abs(first_scale - second_scale) <= tolerance
        and abs(matrix.a * matrix.c + matrix.b * matrix.d)
        <= orthogonality_tolerance
    )


def _record_declares_nonuniform_scale(record: dict[str, Any]) -> bool:
    for key in (
        "allow_nonuniform_scale",
        "allow_non_uniform_scale",
        "nonuniform_scale",
        "non_uniform_scale",
        "data-allow-nonuniform-scale",
        "allow_stretch",
        "data-allow-stretch",
    ):
        if key in record:
            declaration = _boolean_declaration(record.get(key))
            if declaration is None or declaration is True:
                return True
    for key in (
        "preserve_aspect_ratio",
        "preserveAspectRatio",
        "data-preserve-aspect-ratio",
    ):
        if key in record:
            declaration = _boolean_declaration(record.get(key))
            if declaration is None or declaration is False:
                return True
    aspect_ratio_policy = record.get("aspect_ratio_policy")
    if aspect_ratio_policy is not None and aspect_ratio_policy != "preserve":
        return True
    scale_x_values = [record[key] for key in ("scale_x", "scaleX") if key in record]
    scale_y_values = [record[key] for key in ("scale_y", "scaleY") if key in record]
    if scale_x_values or scale_y_values:
        if not scale_x_values or not scale_y_values:
            return True
        if not all(_finite(value) for value in (*scale_x_values, *scale_y_values)):
            return True
        first_x = float(scale_x_values[0])
        first_y = float(scale_y_values[0])
        if any(
            not math.isclose(first_x, float(value), rel_tol=1e-8, abs_tol=1e-8)
            for value in scale_x_values[1:]
        ) or any(
            not math.isclose(first_y, float(value), rel_tol=1e-8, abs_tol=1e-8)
            for value in scale_y_values[1:]
        ):
            return True
        first = abs(first_x)
        second = abs(first_y)
        if (
            first <= 1e-12
            or second <= 1e-12
            or not math.isclose(first, second, rel_tol=1e-8, abs_tol=1e-8)
        ):
            return True
    if "transform" in record and not _transform_is_uniform(record.get("transform")):
        return True
    return False


def _group_declares_nonuniform_scale(group: dict[str, Any]) -> bool:
    records = [group]
    for key in ("geometry", "source_attributes", "layout"):
        value = group.get(key)
        if isinstance(value, dict):
            records.append(value)
    return any(_record_declares_nonuniform_scale(record) for record in records)


def _same_aspect_ratio(first: list[float], second: list[float]) -> bool:
    first_ratio = first[2] / first[3]
    second_ratio = second[2] / second[3]
    return math.isclose(first_ratio, second_ratio, rel_tol=1e-8, abs_tol=1e-8)


def _inventory_payload(inventory: dict[str, Any]) -> dict[str, Any]:
    nested = inventory.get("reference_inventory")
    return nested if isinstance(nested, dict) else inventory


def _scene_element_index(
    scene: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    collections = [
        value
        for value in (scene.get("elements"), scene.get("nodes"))
        if isinstance(value, list)
    ]
    if not collections:
        return {}, ["asset-spec-scene-elements"]
    result: dict[str, dict[str, Any]] = {}
    for index, element in enumerate(item for values in collections for item in values):
        if not isinstance(element, dict) or not _stable_string(element.get("id")):
            errors.append(f"asset-spec-scene-element:{index}")
            continue
        element_id = element["id"]
        if element_id in result:
            errors.append(f"asset-spec-scene-element-duplicate:{element_id}")
            continue
        result[element_id] = element
    return result, errors


def _index_records(
    records: Any,
    *,
    id_field: str,
    error_prefix: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not isinstance(records, list):
        return {}, [error_prefix]
    result: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not _stable_string(record.get(id_field)):
            errors.append(f"{error_prefix}:{index}")
            continue
        record_id = record[id_field]
        if record_id in result:
            errors.append(f"{error_prefix}-duplicate:{record_id}")
            continue
        result[record_id] = record
    return result, errors


def _authorization(
    assets: dict[str, Any],
    opportunity: dict[str, Any],
    implementation: str,
    asset_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    policy = assets.get("policy")
    if not isinstance(policy, dict):
        return None, [f"asset-spec-authorization-policy:{asset_id}"]
    if not isinstance(policy.get("whole_reference_forbidden"), bool):
        return None, [f"asset-spec-authorization-policy:{asset_id}:whole_reference_forbidden"]
    if policy["whole_reference_forbidden"] is not True:
        return None, [f"asset-spec-whole-reference-policy:{asset_id}"]
    explicit = opportunity.get("authorization")
    if explicit is not None and (
        not isinstance(explicit, dict)
        or explicit.get("authorized") is not True
        or not _stable_string(explicit.get("basis"))
    ):
        return None, [f"asset-spec-explicit-authorization:{asset_id}"]
    basis = (
        explicit["basis"]
        if isinstance(explicit, dict)
        else "frozen_microasset_opportunity_map"
    )
    if implementation in _NATIVE_IMPLEMENTATIONS:
        if not isinstance(policy.get("formal_content_native"), bool):
            return None, [f"asset-spec-authorization-policy:{asset_id}:formal_content_native"]
        if policy.get("formal_content_native") is not True:
            return None, [f"asset-spec-native-authorization:{asset_id}"]
        return {
            "authorized": True,
            "basis": basis,
            "policy": "formal_content_native",
        }, []
    return None, [f"asset-spec-implementation:{asset_id}"]


def _implicit_topology_id(kind: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{kind}-{digest[:16]}"


def _topology_records(
    contract: dict[str, Any],
    raw_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    relation_field = "relations" if "relations" in raw_contract else "required_relations"
    raw_relations = raw_contract.get(relation_field, [])
    for index, relation in enumerate(contract["relations"]):
        raw_relation = raw_relations[index] if index < len(raw_relations) else None
        explicit_id = raw_relation.get("id") if isinstance(raw_relation, dict) else None
        explicit_element_id = (
            raw_relation.get("element_id") if isinstance(raw_relation, dict) else None
        )
        payload = {
            "kind": "relation",
            "element_id": (
                explicit_element_id
                if explicit_element_id is not None
                else relation.get("element_id")
                if _stable_string(explicit_id)
                else None
            ),
            "source_id": relation["source_id"],
            "target_id": relation["target_id"],
            "relation": relation["relation"],
        }
        records.append(
            {
                "id": (
                    explicit_id
                    if _stable_string(explicit_id)
                    else _implicit_topology_id("relation", payload)
                ),
                **payload,
            }
        )
    raw_pairs = raw_contract.get("required_pairs", [])
    for index, pair in enumerate(contract["required_pairs"]):
        payload = {
            "kind": "required_pair",
            "member_ids": sorted((pair["a"], pair["b"])),
        }
        raw_pair = raw_pairs[index] if index < len(raw_pairs) else None
        explicit_id = raw_pair.get("id") if isinstance(raw_pair, dict) else None
        records.append(
            {
                "id": (
                    explicit_id
                    if _stable_string(explicit_id)
                    else _implicit_topology_id("pair", payload)
                ),
                **payload,
            }
        )
    return sorted(records, key=lambda record: (record["kind"], record["id"], canonical_json(record)))


def _build_expected_specs(
    scene: dict[str, Any],
    assets: dict[str, Any],
    inventory: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    element_index, errors = _scene_element_index(scene)
    opportunities = assets.get("microasset_opportunity_map", [])
    opportunity_index, opportunity_errors = _index_records(
        opportunities,
        id_field="slot_id",
        error_prefix="asset-spec-opportunity-map",
    )
    errors.extend(opportunity_errors)
    if not opportunity_index:
        return {}, element_index, list(dict.fromkeys(errors))
    inventory_payload = _inventory_payload(inventory)
    inventory_index, inventory_errors = _index_records(
        inventory_payload.get("objects", []),
        id_field="id",
        error_prefix="asset-spec-inventory-objects",
    )
    errors.extend(inventory_errors)

    if inventory_payload.get("status") != "frozen":
        errors.append("asset-spec-inventory-not-frozen")
    reference_values = {
        "scene": scene.get("reference_sha256"),
        "assets": assets.get("reference_sha256"),
        "inventory": inventory_payload.get("reference_sha256"),
    }
    for owner, value in reference_values.items():
        if not _valid_sha256(value):
            errors.append(f"asset-spec-reference-sha256:{owner}")
    valid_reference_values = {
        str(value).lower() for value in reference_values.values() if _valid_sha256(value)
    }
    if len(valid_reference_values) != 1:
        errors.append("asset-spec-reference-sha256-mismatch")
    reference_sha256 = next(iter(valid_reference_values), "")

    expected: dict[str, dict[str, Any]] = {}
    member_owners: dict[str, str] = {}
    for asset_id, opportunity in sorted(opportunity_index.items()):
        group = element_index.get(asset_id)
        object_contract = inventory_index.get(asset_id)
        if group is None or group.get("kind") != "logical_group":
            errors.append(f"asset-spec-logical-group-missing:{asset_id}")
            continue
        if object_contract is None:
            errors.append(f"asset-spec-inventory-object-missing:{asset_id}")
            continue
        opportunity_reference = opportunity.get("reference_sha256")
        if not _valid_sha256(opportunity_reference) or opportunity_reference.lower() != reference_sha256:
            errors.append(f"asset-spec-reference-sha256:opportunity:{asset_id}")

        semantic_kind = opportunity.get("object_kind")
        inventory_kind = object_contract.get("kind")
        semantic_kind_valid = (
            _stable_string(semantic_kind)
            and _stable_string(inventory_kind)
            and semantic_kind == inventory_kind
        )
        if not semantic_kind_valid:
            errors.append(f"asset-spec-semantic-kind:{asset_id}")
        expected_group_role = opportunity.get("expected_group_role")
        if expected_group_role is not None and (
            not _stable_string(expected_group_role)
            or group.get("role") != expected_group_role
        ):
            errors.append(f"asset-spec-group-role:{asset_id}")

        member_ids = group.get("member_ids")
        inventory_members = object_contract.get("element_ids")
        if not (
            isinstance(member_ids, list)
            and member_ids
            and all(_stable_string(member_id) for member_id in member_ids)
            and len(member_ids) == len(set(member_ids))
        ):
            errors.append(f"asset-spec-members:{asset_id}")
            continue
        if not (
            isinstance(inventory_members, list)
            and inventory_members
            and all(_stable_string(member_id) for member_id in inventory_members)
            and len(inventory_members) == len(set(inventory_members))
        ):
            errors.append(f"asset-spec-inventory-members:{asset_id}")
            continue
        if set(member_ids) != set(inventory_members):
            errors.append(f"asset-spec-member-set:{asset_id}")
            continue
        if any(member_id not in element_index for member_id in member_ids):
            errors.append(f"asset-spec-member-unresolved:{asset_id}")
            continue
        if any(element_index[member_id].get("kind") == "logical_group" for member_id in member_ids):
            errors.append(f"asset-spec-member-nonleaf:{asset_id}")
        declared_members = opportunity.get("member_ids")
        if declared_members is not None and (
            not isinstance(declared_members, list)
            or not all(_stable_string(member_id) for member_id in declared_members)
            or len(declared_members) != len(set(declared_members))
            or set(declared_members) != set(member_ids)
        ):
            errors.append(f"asset-spec-opportunity-members:{asset_id}")
        for member_id in member_ids:
            owner = member_owners.get(member_id)
            if owner is not None and owner != asset_id:
                errors.append(
                    f"asset-spec-member-multiple-assets:{member_id}:{owner}:{asset_id}"
                )
            else:
                member_owners[member_id] = asset_id

        contract, topology_errors = normalize_topology_contract(object_contract)
        if contract is None or topology_errors:
            if not topology_errors:
                topology_errors = ["topology-contract-missing"]
            errors.extend(
                f"asset-spec-topology:{asset_id}:{topology_error}"
                for topology_error in topology_errors
            )
            continue
        if contract["scope_element_id"] not in {None, asset_id}:
            errors.append(f"asset-spec-topology-scope:{asset_id}")

        opportunity_bbox = _normalize_bbox(opportunity.get("reference_bbox"))
        inventory_bbox = _normalize_bbox(object_contract.get("bbox"))
        if opportunity_bbox is None:
            errors.append(f"asset-spec-reference-bbox:{asset_id}")
            continue
        if inventory_bbox is None:
            errors.append(f"asset-spec-inventory-bbox:{asset_id}")
            continue
        bbox = opportunity_bbox
        if inventory_bbox != bbox:
            errors.append(f"asset-spec-reference-bbox-conflict:{asset_id}")
        if not _bbox_within_canvas(bbox, scene):
            errors.append(f"asset-spec-reference-bbox-canvas:{asset_id}")
        declared_group_bboxes: list[list[float]] = []
        raw_group_bbox = group.get("bbox")
        if raw_group_bbox is not None:
            group_bbox = _normalize_bbox(raw_group_bbox)
            if group_bbox is None:
                errors.append(f"asset-spec-group-bbox:{asset_id}")
            else:
                declared_group_bboxes.append(group_bbox)
        geometry = group.get("geometry")
        if isinstance(geometry, dict):
            raw_geometry_bbox = geometry.get("bbox")
            if raw_geometry_bbox is not None:
                geometry_bbox = _normalize_bbox(raw_geometry_bbox)
                if geometry_bbox is None:
                    errors.append(f"asset-spec-group-bbox:{asset_id}")
                else:
                    declared_group_bboxes.append(geometry_bbox)
        if any(
            not _same_aspect_ratio(bbox, declared_bbox)
            for declared_bbox in declared_group_bboxes
        ):
            errors.append(f"asset-spec-nonuniform-bbox:{asset_id}")
        for key in ("output_bbox", "implementation_bbox"):
            if key in opportunity:
                implementation_bbox = _normalize_bbox(opportunity.get(key))
                if implementation_bbox is None or not _same_aspect_ratio(bbox, implementation_bbox):
                    errors.append(f"asset-spec-nonuniform-bbox:{asset_id}")
        if _record_declares_nonuniform_scale(opportunity) or _group_declares_nonuniform_scale(group):
            errors.append(f"asset-spec-nonuniform-scale:{asset_id}")

        implementation = opportunity.get("implementation")
        if not _stable_string(implementation) or implementation not in _IMPLEMENTATION_EDITABILITY:
            errors.append(f"asset-spec-implementation:{asset_id}")
            continue
        if "editable" in opportunity and (
            not isinstance(opportunity.get("editable"), bool)
            or opportunity["editable"] is not _IMPLEMENTATION_EDITABILITY[implementation]
        ):
            errors.append(f"asset-spec-editable-declaration:{asset_id}")
        if "single_logical_asset" in opportunity and (
            opportunity.get("single_logical_asset") is not True
        ):
            errors.append(f"asset-spec-single-logical-asset:{asset_id}")
        authorization, authorization_errors = _authorization(
            assets,
            opportunity,
            implementation,
            asset_id,
        )
        errors.extend(authorization_errors)
        if authorization is None:
            continue

        spec = {
            "schema_version": ASSET_SPEC_VERSION,
            "kind": ASSET_SPEC_KIND,
            "asset_id": asset_id,
            "semantic_kind": semantic_kind,
            "reference_bbox": bbox,
            "member_ids": sorted(member_ids),
            "member_role_counts": {
                role: contract["role_counts"][role]
                for role in sorted(contract["role_counts"])
            },
            "member_roles": {
                member_id: role
                for role, role_members in sorted(contract["expected_roles"].items())
                for member_id in sorted(role_members)
            },
            "internal_topology_relations": _topology_records(
                contract,
                object_contract["topology_contract"],
            ),
            "component_count": contract["component_count"],
            "topology_scope_element_id": contract["scope_element_id"],
            "implementation": implementation,
            "editable": _IMPLEMENTATION_EDITABILITY[implementation],
            "authorization": authorization,
            "reference_sha256": reference_sha256,
            "aspect_ratio_policy": "preserve",
            "single_logical_asset": True,
        }
        spec_errors = validate_asset_spec(spec)
        errors.extend(f"asset-spec-generated:{asset_id}:{error}" for error in spec_errors)
        expected[asset_id] = spec
    return expected, element_index, list(dict.fromkeys(errors))


def validate_asset_spec(spec: dict[str, Any]) -> list[str]:
    """Validate one self-contained AssetSpec without consulting a case."""

    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["asset-spec"]
    if set(spec) != _SPEC_KEYS:
        errors.append("fields")
    if spec.get("schema_version") != ASSET_SPEC_VERSION:
        errors.append("schema-version")
    if spec.get("kind") != ASSET_SPEC_KIND:
        errors.append("kind")
    if not _stable_string(spec.get("asset_id")):
        errors.append("asset-id")
    if not _stable_string(spec.get("semantic_kind")):
        errors.append("semantic-kind")
    if _normalize_bbox(spec.get("reference_bbox")) != spec.get("reference_bbox"):
        errors.append("reference-bbox")
    if not _valid_sha256(spec.get("reference_sha256")):
        errors.append("reference-sha256")
    elif spec["reference_sha256"] != spec["reference_sha256"].lower():
        errors.append("reference-sha256-canonical")

    members = spec.get("member_ids")
    if not (
        isinstance(members, list)
        and members
        and all(_stable_string(member_id) for member_id in members)
        and members == sorted(set(members))
    ):
        errors.append("member-ids")
        member_set: set[str] = set()
    else:
        member_set = set(members)

    role_counts = spec.get("member_role_counts")
    if not (
        isinstance(role_counts, dict)
        and role_counts
        and all(
            _stable_string(role)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
            for role, count in role_counts.items()
        )
    ):
        errors.append("member-role-counts")
    elif sum(role_counts.values()) != len(member_set):
        errors.append("member-role-count-total")
    member_roles = spec.get("member_roles")
    if not (
        isinstance(member_roles, dict)
        and set(member_roles) == member_set
        and all(_stable_string(role) for role in member_roles.values())
    ):
        errors.append("member-roles")
    elif isinstance(role_counts, dict):
        actual_role_counts = {
            role: sum(value == role for value in member_roles.values())
            for role in sorted(set(member_roles.values()))
        }
        if actual_role_counts != role_counts:
            errors.append("member-role-assignment-counts")

    topology = spec.get("internal_topology_relations")
    relation_ids: list[str] = []
    if not isinstance(topology, list):
        errors.append("internal-topology-relations")
    else:
        orderable = all(
            isinstance(record, dict)
            and _stable_string(record.get("kind"))
            and _stable_string(record.get("id"))
            for record in topology
        )
        if orderable:
            try:
                canonical = sorted(
                    topology,
                    key=lambda record: (
                        record["kind"],
                        record["id"],
                        canonical_json(record),
                    ),
                )
            except (TypeError, ValueError):
                errors.append("internal-topology-json")
            else:
                if topology != canonical:
                    errors.append("internal-topology-order")
        for index, record in enumerate(topology):
            if not isinstance(record, dict) or not _stable_string(record.get("id")):
                errors.append(f"internal-topology:{index}")
                continue
            relation_ids.append(record["id"])
            if record.get("kind") == "relation":
                if set(record) != {
                    "id",
                    "kind",
                    "element_id",
                    "source_id",
                    "target_id",
                    "relation",
                }:
                    errors.append(f"internal-topology-fields:{record['id']}")
                if (
                    record.get("source_id") not in member_set
                    or record.get("target_id") not in member_set
                    or record.get("source_id") == record.get("target_id")
                    or not _stable_string(record.get("relation"))
                    or (
                        record.get("element_id") is not None
                        and record.get("element_id") not in member_set
                    )
                ):
                    errors.append(f"internal-topology-scope:{record['id']}")
            elif record.get("kind") == "required_pair":
                pair = record.get("member_ids")
                if set(record) != {"id", "kind", "member_ids"} or not (
                    isinstance(pair, list)
                    and len(pair) == 2
                    and all(_stable_string(member_id) for member_id in pair)
                    and pair == sorted(set(pair))
                    and set(pair).issubset(member_set)
                ):
                    errors.append(f"internal-topology-pair:{record['id']}")
            else:
                errors.append(f"internal-topology-kind:{record['id']}")
    if len(relation_ids) != len(set(relation_ids)):
        errors.append("internal-topology-id-closure")

    component_count = spec.get("component_count")
    if (
        not isinstance(component_count, int)
        or isinstance(component_count, bool)
        or component_count < 1
    ):
        errors.append("component-count")
    asset_id = spec.get("asset_id")
    scope_element_id = spec.get("topology_scope_element_id")
    if scope_element_id is not None and not _stable_string(scope_element_id):
        errors.append("topology-scope-element-id")
    elif scope_element_id is not None and (
        not _stable_string(asset_id) or scope_element_id != asset_id
    ):
        errors.append("topology-scope-identity")
    implementation = spec.get("implementation")
    if not _stable_string(implementation) or implementation not in _IMPLEMENTATION_EDITABILITY:
        errors.append("implementation")
    elif spec.get("editable") is not _IMPLEMENTATION_EDITABILITY[implementation]:
        errors.append("editable")
    authorization = spec.get("authorization")
    if not (
        isinstance(authorization, dict)
        and set(authorization) == {"authorized", "basis", "policy"}
        and authorization.get("authorized") is True
        and _stable_string(authorization.get("basis"))
        and authorization.get("policy") == "formal_content_native"
    ):
        errors.append("authorization")
    elif _stable_string(implementation) and implementation in _NATIVE_IMPLEMENTATIONS and (
        authorization.get("policy") != "formal_content_native"
    ):
        errors.append("authorization-implementation")
    if spec.get("aspect_ratio_policy") != "preserve":
        errors.append("aspect-ratio-policy")
    if spec.get("single_logical_asset") is not True:
        errors.append("single-logical-asset")
    return list(dict.fromkeys(errors))


def _valid_atomic_asset_id(value: Any) -> bool:
    return (
        _stable_string(value)
        and value.startswith(ATOMIC_VECTOR_ID_PREFIX)
        and _stable_string(value[len(ATOMIC_VECTOR_ID_PREFIX):])
    )


def _valid_case_relative_svg_path(value: Any) -> bool:
    if (
        not _stable_string(value)
        or "\\" in value
        or value.startswith("/")
        or _WINDOWS_ABSOLUTE_PATH_RE.match(value)
    ):
        return False
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return parts[-1].endswith(".svg")


def validate_atomic_vector_asset(entry: Any) -> list[str]:
    """Validate one self-contained atomic-vector assets.json entry."""

    errors: list[str] = []
    if not isinstance(entry, dict):
        return ["atomic-vector-asset"]
    if set(entry) != _ATOMIC_VECTOR_ASSET_KEYS:
        errors.append("fields")
    if not _valid_atomic_asset_id(entry.get("id")):
        errors.append("id")
    if entry.get("editable") is not True:
        errors.append("editable")
    if entry.get("source") != ATOMIC_VECTOR_SOURCE:
        errors.append("source")
    vector_source = entry.get("vector_source_svg")
    if not (
        isinstance(vector_source, dict)
        and set(vector_source) == {"path", "sha256"}
        and _valid_case_relative_svg_path(vector_source.get("path"))
        and _valid_sha256(vector_source.get("sha256"))
        and vector_source["sha256"] == vector_source["sha256"].lower()
    ):
        errors.append("vector-source-svg")
    if not _stable_string(entry.get("trace_method")):
        errors.append("trace-method")
    if not _stable_string(entry.get("trace_engine_version")):
        errors.append("trace-engine-version")
    if not _stable_string(entry.get("authorization_basis")):
        errors.append("authorization-basis")
    if not _stable_string(entry.get("rights_status")):
        errors.append("rights-status")
    if not _valid_atomic_asset_id(entry.get("fallback_atomic_raster")):
        errors.append("fallback-atomic-raster")
    if not _stable_string(entry.get("ink_contract_region_id")):
        errors.append("ink-contract-region-id")
    if entry.get("trace_eligibility") not in TRACE_ELIGIBILITY_VALUES:
        errors.append("trace-eligibility")
    return list(dict.fromkeys(errors))


def audit_atomic_vector_assets(assets: Any) -> list[str]:
    """Audit every atomic-vector entry in one assets.json document.

    Entries with other ``source`` values keep their own contracts; the
    atomic-vector checks additionally verify that each declared
    ``fallback_atomic_raster`` resolves to an atomic-raster entry in the same
    document.
    """

    if not isinstance(assets, dict) or not isinstance(assets.get("assets"), list):
        return ["atomic-vector-asset:assets"]
    entries = [item for item in assets["assets"] if isinstance(item, dict)]
    raster_ids = {
        item.get("id")
        for item in entries
        if item.get("source") == ATOMIC_VECTOR_FALLBACK_SOURCE
    }
    errors: list[str] = []
    for item in entries:
        if item.get("source") != ATOMIC_VECTOR_SOURCE:
            continue
        label = item.get("id") if _stable_string(item.get("id")) else "[missing-id]"
        errors.extend(
            f"atomic-vector-asset:{label}:{error}"
            for error in validate_atomic_vector_asset(item)
        )
        fallback = item.get("fallback_atomic_raster")
        if _valid_atomic_asset_id(fallback) and fallback not in raster_ids:
            errors.append(f"atomic-vector-asset:{label}:fallback-unresolved")
    return list(dict.fromkeys(errors))


def attach_asset_specs(
    scene: dict[str, Any],
    assets: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Attach deterministic specs to matched logical groups transactionally.

    Unmatched logical groups remain untouched.  An existing spec without a
    current three-way frozen contract is rejected instead of silently retained.
    """

    expected, element_index, errors = _build_expected_specs(scene, assets, inventory)
    edges = scene.get("edges", [])
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict) and (
                "asset_spec" in edge or "asset_spec_sha256" in edge
            ):
                errors.append(f"asset-spec-invalid-carrier:{edge.get('id', '[missing]')}")
    for element_id, element in element_index.items():
        if "asset_spec" in element and element_id not in expected:
            errors.append(f"asset-spec-contract-missing:{element_id}")
        if "asset_spec_sha256" in element and element_id not in expected:
            errors.append(f"asset-spec-hash-contract-missing:{element_id}")
    if errors:
        raise AssetSpecError(errors)
    for asset_id, spec in expected.items():
        group = element_index[asset_id]
        group["asset_spec"] = spec
        group["asset_spec_sha256"] = asset_spec_sha256(spec)
    return scene


def audit_asset_specs(
    scene: dict[str, Any],
    assets: dict[str, Any],
    inventory: dict[str, Any],
) -> list[str]:
    """Audit attached AssetSpecs against fresh frozen source contracts."""

    expected, element_index, errors = _build_expected_specs(scene, assets, inventory)
    edges = scene.get("edges", [])
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict) and (
                "asset_spec" in edge or "asset_spec_sha256" in edge
            ):
                errors.append(f"asset-spec-invalid-carrier:{edge.get('id', '[missing]')}")
    actual_ids: set[str] = set()
    for element_id, element in element_index.items():
        has_spec = "asset_spec" in element
        has_hash = "asset_spec_sha256" in element
        if not has_spec and not has_hash:
            continue
        actual_ids.add(element_id)
        if element.get("kind") != "logical_group":
            errors.append(f"asset-spec-non-logical-group:{element_id}")
        spec = element.get("asset_spec")
        if not isinstance(spec, dict):
            errors.append(f"asset-spec-invalid:{element_id}")
            continue
        errors.extend(
            f"asset-spec-invalid:{element_id}:{error}" for error in validate_asset_spec(spec)
        )
        if spec.get("asset_id") != element_id:
            errors.append(f"asset-spec-identity:{element_id}")
        try:
            digest = asset_spec_sha256(spec)
        except (TypeError, ValueError):
            errors.append(f"asset-spec-hash-unserializable:{element_id}")
            continue
        if element.get("asset_spec_sha256") != digest:
            errors.append(f"asset-spec-hash:{element_id}")
        expected_spec = expected.get(element_id)
        if expected_spec is None:
            errors.append(f"asset-spec-contract-missing:{element_id}")
        elif digest != asset_spec_sha256(expected_spec):
            errors.append(f"asset-spec-contract-drift:{element_id}")
    for asset_id in sorted(set(expected) - actual_ids):
        errors.append(f"asset-spec-missing:{asset_id}")
    return list(dict.fromkeys(errors))
