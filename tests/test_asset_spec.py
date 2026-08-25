from __future__ import annotations

import copy

import pytest

from tools.asset_spec import (
    ASSET_SPEC_KIND,
    ASSET_SPEC_VERSION,
    AssetSpecError,
    asset_spec_sha256,
    attach_asset_specs,
    audit_asset_specs,
    validate_asset_spec,
)


REFERENCE_SHA256 = "a" * 64


def _contracts():
    scene = {
        "schema_version": "4.0.0",
        "kind": "scene",
        "reference_sha256": REFERENCE_SHA256,
        "canvas": {"width": 200, "height": 100},
        "elements": [
            {
                "id": "asset-a",
                "kind": "logical_group",
                "role": "icon",
                "member_ids": ["node-b", "edge-ab", "node-a"],
                "geometry": {"transform": "translate(2 3) rotate(10) scale(1.5)"},
            },
            {"id": "node-a", "kind": "ellipse"},
            {"id": "node-b", "kind": "ellipse"},
            {"id": "edge-ab", "kind": "line"},
            {
                "id": "uncontracted-group",
                "kind": "logical_group",
                "role": "icon",
                "member_ids": ["uncontracted-leaf"],
            },
            {"id": "uncontracted-leaf", "kind": "path"},
        ],
    }
    assets = {
        "schema_version": "4.0.0",
        "kind": "assets",
        "reference_sha256": REFERENCE_SHA256,
        "policy": {
            "formal_content_native": True,
            "authorized_atomic_raster_only": True,
            "whole_reference_forbidden": True,
        },
        "microasset_opportunity_map": [
            {
                "slot_id": "asset-a",
                "object_kind": "icon",
                "implementation": "native_editable_vector",
                "reference_bbox": [10, 12, 40, 20],
                "reference_sha256": REFERENCE_SHA256,
            }
        ],
    }
    inventory = {
        "schema_version": "1.0.0",
        "status": "frozen",
        "reference_sha256": REFERENCE_SHA256,
        "objects": [
            {
                "id": "asset-a",
                "kind": "icon",
                "bbox": [10, 12, 40, 20],
                "element_ids": ["edge-ab", "node-a", "node-b"],
                "topology_contract": {
                    "role_counts": {
                        "edge": {"count": 1, "element_id_pattern": "edge-ab"},
                        "source": {"count": 1, "element_id_pattern": "node-a"},
                        "target": {"count": 1, "element_id_pattern": "node-b"},
                    },
                    "required_pairs": [
                        ["node-a", "node-b"],
                        ["edge-ab", "node-a"],
                    ],
                    "required_relations": [
                        {
                            "id": "edge-ab",
                            "element_id": "edge-ab",
                            "source_id": "node-a",
                            "target_id": "node-b",
                            "relation": "graph_edge",
                        }
                    ],
                    "component_count": 1,
                    "scope_element_id": "asset-a",
                },
            }
        ],
    }
    return scene, assets, inventory


def _group(scene, element_id="asset-a"):
    return next(element for element in scene["elements"] if element["id"] == element_id)


def test_attach_is_deterministic_idempotent_and_uses_only_frozen_contracts():
    scene, assets, inventory = _contracts()
    returned = attach_asset_specs(scene, assets, inventory)
    assert returned is scene
    group = _group(scene)
    spec = group["asset_spec"]

    assert spec["schema_version"] == ASSET_SPEC_VERSION
    assert spec["kind"] == ASSET_SPEC_KIND
    assert spec["asset_id"] == "asset-a"
    assert spec["semantic_kind"] == "icon"
    assert spec["reference_bbox"] == [10.0, 12.0, 40.0, 20.0]
    assert spec["member_ids"] == ["edge-ab", "node-a", "node-b"]
    assert spec["member_role_counts"] == {"edge": 1, "source": 1, "target": 1}
    assert spec["member_roles"] == {
        "edge-ab": "edge",
        "node-a": "source",
        "node-b": "target",
    }
    assert spec["topology_scope_element_id"] == "asset-a"
    assert spec["implementation"] == "native_editable_vector"
    assert spec["editable"] is True
    assert spec["authorization"] == {
        "authorized": True,
        "basis": "frozen_microasset_opportunity_map",
        "policy": "formal_content_native",
    }
    assert spec["single_logical_asset"] is True
    assert validate_asset_spec(spec) == []
    assert group["asset_spec_sha256"] == asset_spec_sha256(spec)
    assert "asset_spec" not in _group(scene, "uncontracted-group")
    assert audit_asset_specs(scene, assets, inventory) == []

    first = copy.deepcopy(scene)
    attach_asset_specs(scene, assets, inventory)
    assert scene == first


def test_hash_is_stable_when_nonsemantic_input_order_changes():
    scene, assets, inventory = _contracts()
    attach_asset_specs(scene, assets, inventory)
    first_hash = _group(scene)["asset_spec_sha256"]

    reordered_scene, reordered_assets, reordered_inventory = _contracts()
    _group(reordered_scene)["member_ids"].reverse()
    reordered_inventory["objects"][0]["element_ids"].reverse()
    reordered_inventory["objects"][0]["topology_contract"]["role_counts"] = {
        "edge": {"count": 1, "element_id_pattern": "edge-ab"},
        "target": {"count": 1, "element_id_pattern": "node-b"},
        "source": {"count": 1, "element_id_pattern": "node-a"},
    }
    reordered_inventory["objects"][0]["topology_contract"]["required_pairs"].reverse()
    attach_asset_specs(reordered_scene, reordered_assets, reordered_inventory)
    assert _group(reordered_scene)["asset_spec_sha256"] == first_hash


def test_no_microasset_map_means_no_asset_is_guessed():
    scene, _, _ = _contracts()
    original = copy.deepcopy(scene)
    assert attach_asset_specs(scene, {"microasset_opportunity_map": []}, {}) == original
    assert audit_asset_specs(scene, {"microasset_opportunity_map": []}, {}) == []


def test_map_entry_without_matching_inventory_contract_fails_before_mutation():
    scene, assets, inventory = _contracts()
    inventory["objects"] = []
    before = copy.deepcopy(scene)
    with pytest.raises(AssetSpecError, match="asset-spec-inventory-object-missing:asset-a"):
        attach_asset_specs(scene, assets, inventory)
    assert scene == before


@pytest.mark.parametrize(
    ("mutator", "error"),
    [
        (
            lambda scene, assets, inventory: _group(scene)["member_ids"].append("ghost"),
            "asset-spec-member-set:asset-a",
        ),
        (
            lambda scene, assets, inventory: inventory["objects"][0][
                "topology_contract"
            ]["required_relations"][0].update(target_id="ghost"),
            "asset-spec-topology:asset-a:",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                reference_bbox=[10, 10, 0, 20]
            ),
            "asset-spec-reference-bbox:asset-a",
        ),
        (
            lambda scene, assets, inventory: inventory["objects"][0].update(
                bbox=[10, 10, -1, 20]
            ),
            "asset-spec-inventory-bbox:asset-a",
        ),
        (
            lambda scene, assets, inventory: _group(scene).update(bbox=[1, 1, 0, 2]),
            "asset-spec-group-bbox:asset-a",
        ),
        (
            lambda scene, assets, inventory: _group(scene)["geometry"].update(
                transform="scale(2 1)"
            ),
            "asset-spec-nonuniform-scale:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                preserve_aspect_ratio=False
            ),
            "asset-spec-nonuniform-scale:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                editable=False
            ),
            "asset-spec-editable-declaration:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                authorization={"authorized": False, "basis": "explicit-denial"}
            ),
            "asset-spec-explicit-authorization:asset-a",
        ),
        (
            lambda scene, assets, inventory: inventory["objects"][0][
                "topology_contract"
            ].update(scope_element_id="another-asset"),
            "asset-spec-topology-scope:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                object_kind=[]
            ),
            "asset-spec-semantic-kind:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                implementation=[]
            ),
            "asset-spec-implementation:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                allow_nonuniform_scale={"enabled": True}
            ),
            "asset-spec-nonuniform-scale:asset-a",
        ),
        (
            lambda scene, assets, inventory: _group(scene)["geometry"].update(
                transform="scale(2 2 999)"
            ),
            "asset-spec-nonuniform-scale:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                scale_x=0, scale_y=0
            ),
            "asset-spec-nonuniform-scale:asset-a",
        ),
        (
            lambda scene, assets, inventory: assets["microasset_opportunity_map"][0].update(
                scale_x=1, scale_y=1, scaleX=2, scaleY=1
            ),
            "asset-spec-nonuniform-scale:asset-a",
        ),
    ],
)
def test_invalid_member_topology_bbox_and_nonuniform_declarations_fail_closed(
    mutator,
    error,
):
    scene, assets, inventory = _contracts()
    mutator(scene, assets, inventory)
    before = copy.deepcopy(scene)
    with pytest.raises(AssetSpecError, match=error):
        attach_asset_specs(scene, assets, inventory)
    assert scene == before


def test_audit_detects_mutation_hash_drift_and_uncontracted_specs():
    scene, assets, inventory = _contracts()
    attach_asset_specs(scene, assets, inventory)
    group = _group(scene)
    group["asset_spec"]["reference_bbox"][2] = 41.0
    findings = audit_asset_specs(scene, assets, inventory)
    assert "asset-spec-hash:asset-a" in findings
    assert "asset-spec-contract-drift:asset-a" in findings

    group.pop("asset_spec")
    group.pop("asset_spec_sha256")
    uncontracted = _group(scene, "uncontracted-group")
    uncontracted["asset_spec"] = {"asset_id": "uncontracted-group"}
    uncontracted["asset_spec_sha256"] = "0" * 64
    findings = audit_asset_specs(scene, assets, inventory)
    assert "asset-spec-missing:asset-a" in findings
    assert "asset-spec-contract-missing:uncontracted-group" in findings


def test_member_role_assignment_is_part_of_the_stable_contract_hash():
    scene, assets, inventory = _contracts()
    attach_asset_specs(scene, assets, inventory)
    first_hash = _group(scene)["asset_spec_sha256"]

    changed_scene, changed_assets, changed_inventory = _contracts()
    role_counts = changed_inventory["objects"][0]["topology_contract"]["role_counts"]
    role_counts["source"]["element_id_pattern"] = "node-b"
    role_counts["target"]["element_id_pattern"] = "node-a"
    attach_asset_specs(changed_scene, changed_assets, changed_inventory)
    assert _group(changed_scene)["asset_spec_sha256"] != first_hash


def test_one_physical_member_cannot_belong_to_two_logical_assets():
    scene, assets, inventory = _contracts()
    scene["elements"].insert(
        1,
        {
            "id": "asset-b",
            "kind": "logical_group",
            "role": "icon",
            "member_ids": ["node-a", "node-b", "edge-ab"],
        },
    )
    second_opportunity = copy.deepcopy(assets["microasset_opportunity_map"][0])
    second_opportunity.update(slot_id="asset-b", reference_bbox=[60, 12, 40, 20])
    assets["microasset_opportunity_map"].append(second_opportunity)
    second_object = copy.deepcopy(inventory["objects"][0])
    second_object["id"] = "asset-b"
    inventory["objects"].append(second_object)

    with pytest.raises(AssetSpecError, match="asset-spec-member-multiple-assets"):
        attach_asset_specs(scene, assets, inventory)


def test_live_nodes_carrier_is_supported_but_edges_cannot_carry_asset_specs():
    scene, assets, inventory = _contracts()
    scene["nodes"] = scene.pop("elements")
    attach_asset_specs(scene, assets, inventory)
    assert audit_asset_specs(scene, assets, inventory) == []

    scene["edges"] = [
        {
            "id": "not-an-asset",
            "asset_spec": _group({"elements": scene["nodes"]})["asset_spec"],
            "asset_spec_sha256": _group({"elements": scene["nodes"]})[
                "asset_spec_sha256"
            ],
        }
    ]
    findings = audit_asset_specs(scene, assets, inventory)
    assert "asset-spec-invalid-carrier:not-an-asset" in findings
    with pytest.raises(AssetSpecError, match="asset-spec-invalid-carrier:not-an-asset"):
        attach_asset_specs(scene, assets, inventory)


def test_each_declared_group_bbox_is_independently_validated():
    scene, assets, inventory = _contracts()
    group = _group(scene)
    group["bbox"] = [0, 0, 40, 20]
    group["geometry"]["bbox"] = [0, 0, 0, 20]
    with pytest.raises(AssetSpecError, match="asset-spec-group-bbox:asset-a"):
        attach_asset_specs(scene, assets, inventory)


def test_malformed_attached_topology_returns_blockers_instead_of_raising():
    scene, assets, inventory = _contracts()
    attach_asset_specs(scene, assets, inventory)
    group = _group(scene)
    group["asset_spec"]["internal_topology_relations"] = [
        {"id": "x", "kind": 1},
        {"id": "y", "kind": "required_pair", "member_ids": [[], "node-a"]},
    ]
    findings = audit_asset_specs(scene, assets, inventory)
    assert "asset-spec-invalid:asset-a:internal-topology-kind:x" in findings
    assert "asset-spec-invalid:asset-a:internal-topology-pair:y" in findings
    assert "asset-spec-hash:asset-a" in findings


def test_group_role_is_checked_only_when_the_frozen_map_declares_it():
    scene, assets, inventory = _contracts()
    _group(scene)["role"] = "scientific-node"
    assets["microasset_opportunity_map"][0]["object_kind"] = "shape"
    inventory["objects"][0]["kind"] = "shape"
    attach_asset_specs(scene, assets, inventory)
    assert _group(scene)["asset_spec"]["semantic_kind"] == "shape"

    other_scene, other_assets, other_inventory = _contracts()
    _group(other_scene)["role"] = "scientific-node"
    other_assets["microasset_opportunity_map"][0][
        "expected_group_role"
    ] = "icon"
    with pytest.raises(AssetSpecError, match="asset-spec-group-role:asset-a"):
        attach_asset_specs(other_scene, other_assets, other_inventory)


def test_nested_asset_member_overlap_is_rejected_for_single_physical_ownership():
    scene, assets, inventory = _contracts()
    outer = _group(scene)
    outer["logical_descendant_group_ids"] = ["asset-b"]
    scene["elements"].insert(
        1,
        {
            "id": "asset-b",
            "kind": "logical_group",
            "role": "icon",
            "member_ids": ["node-a"],
            "logical_descendant_group_ids": [],
        },
    )
    assets["microasset_opportunity_map"].append(
        {
            "slot_id": "asset-b",
            "object_kind": "icon",
            "implementation": "native_editable_vector",
            "reference_bbox": [60, 12, 10, 10],
            "reference_sha256": REFERENCE_SHA256,
        }
    )
    inventory["objects"].append(
        {
            "id": "asset-b",
            "kind": "icon",
            "bbox": [60, 12, 10, 10],
            "element_ids": ["node-a"],
            "topology_contract": {
                "role_counts": {
                    "node": {"count": 1, "element_id_pattern": "node-a"}
                },
                "required_pairs": [],
                "required_relations": [],
                "component_count": 1,
                "scope_element_id": "asset-b",
            },
        }
    )
    with pytest.raises(AssetSpecError, match="asset-spec-member-multiple-assets"):
        attach_asset_specs(scene, assets, inventory)


def test_malformed_attached_asset_identity_returns_blockers_instead_of_raising():
    scene, assets, inventory = _contracts()
    attach_asset_specs(scene, assets, inventory)
    group = _group(scene)
    group["asset_spec"]["asset_id"] = []
    findings = audit_asset_specs(scene, assets, inventory)
    assert "asset-spec-invalid:asset-a:asset-id" in findings
    assert "asset-spec-invalid:asset-a:topology-scope-identity" in findings
    assert "asset-spec-hash:asset-a" in findings
