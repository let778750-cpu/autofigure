from __future__ import annotations

import copy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tools.core import common
from tools.assets.asset_spec import (
    ASSET_CONTRACT_KIND,
    ASSET_CONTRACT_RECEIPT_KIND,
    ASSET_CONTRACT_RECEIPT_PATH,
    AssetSpecError,
    asset_contract_blockers,
    asset_contract_sha256,
    canonical_asset_contract_payload,
    freeze_asset_contract,
)
from tools.assets.asset_trace import compute_trace_eligibility
from tools.assets.reference_inventory import (
    OBJECT_KINDS,
    canonical_sha256,
    freeze_inventory,
)
from tools.core.contracts import read_json, write_json
from tools.pipeline.prepare import main as prepare_main


def _run(tmp_path: Path, case: str, image: Image.Image | None = None) -> common.Run:
    reference = tmp_path / f"{case}.png"
    (image if image is not None else Image.new("RGB", (160, 100), "white")).save(
        reference
    )
    cases_root = tmp_path / "examples"
    assert (
        prepare_main(
            [
                str(reference),
                "--case",
                case,
                "--cases-root",
                str(cases_root),
                "--input-route",
                "reference-only",
            ]
        )
        == 0
    )
    return common.open_run(cases_root / "reference-only" / case)


def _zero_authorizations(run: common.Run, *, excluded_kind: str) -> list[dict]:
    return [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "asset-contract-test",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("text", "arrow", "icon", "brace")
        if kind != excluded_kind
    ]


def _configure_text_inventory(run: common.Run) -> None:
    payload = read_json(run.regions_path)
    payload["regions"] = [
        {
            "id": "title-region",
            "label": "Title",
            "bbox": [0, 0, 160, 50],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": ["title"],
        }
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {kind: 0 for kind in OBJECT_KINDS}
    inventory["expected_counts"]["text"] = 1
    inventory["zero_count_authorizations"] = _zero_authorizations(
        run, excluded_kind="text"
    )
    inventory["objects"] = [
        {
            "id": "title",
            "kind": "text",
            "bbox": [18, 10, 124, 28],
            "element_ids": ["title"],
            "critical_region_ids": ["title-region"],
            "typography": {
                "exact_text": "Frozen title",
                "font_family": "Arial",
                "font_size_px": 18,
                "font_weight": "normal",
                "font_style": "normal",
                "line_count": 1,
                "alignment": "left",
                "bbox_tolerance_px": 1,
                "font_size_tolerance_px": 0.5,
            },
        }
    ]
    write_json(run.regions_path, payload)


def _configure_asset_inventory(run: common.Run) -> None:
    payload = read_json(run.regions_path)
    element_ids = ["bond", "atom-a", "atom-b"]
    payload["regions"] = [
        {
            "id": "molecule-region",
            "label": "Molecule",
            "bbox": [18, 18, 44, 34],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": element_ids,
            "ink_contract": {
                "background_rgb": [255, 255, 255],
                "background_tolerance": 24,
                "bbox_tolerance_px": 2,
                "center_tolerance_px": 2,
                "area_relative_tolerance": 0.2,
            },
        }
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {kind: 0 for kind in OBJECT_KINDS}
    inventory["expected_counts"]["icon"] = 1
    inventory["zero_count_authorizations"] = _zero_authorizations(
        run, excluded_kind="icon"
    )
    inventory["objects"] = [
        {
            "id": "molecule",
            "kind": "icon",
            "bbox": [20, 20, 40, 30],
            "element_ids": element_ids,
            "critical_region_ids": ["molecule-region"],
            "visual": {"aspect_ratio_tolerance": 0.03, "bbox_tolerance_px": 2},
            "contract_refs": {"ink_contract": {"region_id": "molecule-region"}},
            "topology_contract": {
                "role_counts": {"atom": 2, "bond": 1},
                "role_mapping": {
                    "atom-a": "atom",
                    "atom-b": "atom",
                    "bond": "bond",
                },
                "required_pairs": [],
                "relations": [
                    {
                        "id": "bond",
                        "source_id": "atom-a",
                        "target_id": "atom-b",
                        "relation": "bond",
                    }
                ],
                "component_count": 1,
            },
        }
    ]
    write_json(run.regions_path, payload)
    assets = read_json(run.assets_path)
    assets["microasset_opportunity_map"] = [
        {
            "slot_id": "molecule",
            "object_kind": "icon",
            "implementation": "native_editable_vector",
            "reference_bbox": [20, 20, 40, 30],
            "reference_sha256": run.load_meta()["source_sha256"],
        }
    ]
    write_json(run.assets_path, assets)


def test_canonical_asset_contract_excludes_generated_assets_and_sorts_map():
    assets = {
        "kind": "assets",
        "reference_sha256": "a" * 64,
        "policy": {
            "formal_content_native": True,
            "authorized_atomic_raster_only": True,
            "whole_reference_forbidden": True,
        },
        "microasset_opportunity_map": [
            {
                "slot_id": "z-slot",
                "object_kind": "icon",
                "implementation": "native_editable_vector",
                "reference_bbox": [1, 1, 2, 2],
                "reference_sha256": "a" * 64,
            },
            {
                "slot_id": "a-slot",
                "object_kind": "plot",
                "implementation": "native_editable_shapes",
                "reference_bbox": [3, 3, 4, 4],
                "reference_sha256": "a" * 64,
            },
        ],
        "assets": [{"id": "generated-one"}],
        "raster_count": 9,
        "updated_at": "first",
    }
    first = canonical_asset_contract_payload(assets)
    first_hash = asset_contract_sha256(assets)
    mutated = copy.deepcopy(assets)
    mutated["assets"] = [{"id": "generated-two"}]
    mutated["raster_count"] = 0
    mutated["updated_at"] = "second"
    mutated["microasset_opportunity_map"].reverse()
    assert asset_contract_sha256(mutated) == first_hash
    assert first["kind"] == ASSET_CONTRACT_KIND
    assert [item["slot_id"] for item in first["microasset_opportunity_map"]] == [
        "a-slot",
        "z-slot",
    ]
    assert "assets" not in first


def test_inventory_freeze_writes_explicit_empty_asset_contract_receipt(tmp_path: Path):
    run = _run(tmp_path, "empty-map")
    _configure_text_inventory(run)
    freeze_inventory(run)
    receipt = read_json(run.root / ASSET_CONTRACT_RECEIPT_PATH)
    assert receipt["kind"] == ASSET_CONTRACT_RECEIPT_KIND
    assert receipt["case"] == "empty-map"
    assert receipt["reference_sha256"] == run.load_meta()["source_sha256"]
    assert receipt["opportunity_count"] == 0
    assert asset_contract_blockers(run) == []


def test_receipt_binds_inventory_and_asset_oracle_but_ignores_generated_assets(
    tmp_path: Path,
):
    run = _run(tmp_path, "mapped")
    _configure_asset_inventory(run)
    inventory_receipt = freeze_inventory(run)
    receipt = read_json(run.root / ASSET_CONTRACT_RECEIPT_PATH)
    assets = read_json(run.assets_path)
    assert receipt["inventory_sha256"] == inventory_receipt["inventory_sha256"]
    assert receipt["asset_contract_sha256"] == asset_contract_sha256(assets)
    assert receipt["opportunity_count"] == 1

    assets["assets"] = [{"id": "converter-generated"}]
    assets["raster_count"] = 1
    write_json(run.assets_path, assets)
    assert asset_contract_blockers(run) == []

    assets["microasset_opportunity_map"][0][
        "implementation"
    ] = "native_editable_shapes"
    write_json(run.assets_path, assets)
    assert "asset-contract:receipt-stale:asset_contract_sha256" in asset_contract_blockers(
        run
    )
    refreshed = freeze_asset_contract(run)
    assert refreshed["asset_contract_sha256"] == asset_contract_sha256(assets)
    assert asset_contract_blockers(run) == []


def test_asset_contract_receipt_fails_closed_on_inventory_or_receipt_drift(tmp_path: Path):
    run = _run(tmp_path, "drift")
    _configure_asset_inventory(run)
    freeze_inventory(run)
    regions = read_json(run.regions_path)
    regions["reference_inventory"]["objects"][0]["bbox"] = [21, 20, 40, 30]
    write_json(run.regions_path, regions)
    assert "asset-contract:inventory-receipt-stale" in asset_contract_blockers(run)

    _configure_asset_inventory(run)
    freeze_inventory(run)
    receipt_path = run.root / ASSET_CONTRACT_RECEIPT_PATH
    receipt = read_json(receipt_path)
    receipt["asset_contract_sha256"] = "0" * 64
    write_json(receipt_path, receipt)
    assert "asset-contract:receipt-stale:asset_contract_sha256" in asset_contract_blockers(
        run
    )


def test_invalid_or_unmatched_opportunity_map_cannot_be_frozen(tmp_path: Path):
    run = _run(tmp_path, "invalid-map")
    _configure_text_inventory(run)
    freeze_inventory(run)
    assets = read_json(run.assets_path)
    assets["microasset_opportunity_map"] = [
        {
            "slot_id": "unknown-asset",
            "object_kind": "icon",
            "implementation": "native_editable_vector",
            "reference_bbox": [1, 1, 10, 10],
            "reference_sha256": run.load_meta()["source_sha256"],
        }
    ]
    write_json(run.assets_path, assets)
    with pytest.raises(SystemExit, match="inventory-object"):
        freeze_asset_contract(run)

    assets["microasset_opportunity_map"] = {"slot_id": "not-a-list"}
    with pytest.raises(AssetSpecError, match="asset-contract:opportunity-map"):
        canonical_asset_contract_payload(assets)


def test_missing_map_and_unsafe_policy_fail_before_inventory_freeze_mutates_state(
    tmp_path: Path,
):
    run = _run(tmp_path, "transactional-preflight")
    _configure_text_inventory(run)
    regions_before = run.regions_path.read_bytes()
    meta_before = run.meta_path.read_bytes()
    assets = read_json(run.assets_path)
    assets.pop("microasset_opportunity_map")
    write_json(run.assets_path, assets)
    with pytest.raises(SystemExit, match="opportunity-map-missing"):
        freeze_inventory(run)
    assert run.regions_path.read_bytes() == regions_before
    assert run.meta_path.read_bytes() == meta_before
    assert not (run.qa_dir / "reference-inventory-receipt.json").exists()
    assert not (run.root / ASSET_CONTRACT_RECEIPT_PATH).exists()

    assets["microasset_opportunity_map"] = []
    assets["policy"]["whole_reference_forbidden"] = False
    write_json(run.assets_path, assets)
    with pytest.raises(SystemExit, match="whole_reference_forbidden"):
        freeze_inventory(run)
    assert run.regions_path.read_bytes() == regions_before
    assert run.meta_path.read_bytes() == meta_before


def test_full_inventory_receipt_and_task_drift_block_asset_contract(tmp_path: Path):
    run = _run(tmp_path, "inventory-dependency")
    _configure_text_inventory(run)
    freeze_inventory(run)
    tasks = read_json(run.region_tasks_path)
    tasks["tasks"][0]["label"] = "tampered after freeze"
    write_json(run.region_tasks_path, tasks)
    assert any(
        finding.startswith("asset-contract:inventory-contract:reference-inventory:")
        for finding in asset_contract_blockers(run)
    )


def test_receipt_schema_and_opportunity_schema_fail_closed(tmp_path: Path):
    run = _run(tmp_path, "schema-closure")
    _configure_asset_inventory(run)
    freeze_inventory(run)
    receipt_path = run.root / ASSET_CONTRACT_RECEIPT_PATH
    receipt = read_json(receipt_path)
    receipt["unbound_extra"] = True
    write_json(receipt_path, receipt)
    assert "asset-contract:receipt-schema" in asset_contract_blockers(run)

    assets = read_json(run.assets_path)
    assets["microasset_opportunity_map"][0]["unreviewed_hint"] = "not contracted"
    with pytest.raises(AssetSpecError, match="schema"):
        canonical_asset_contract_payload(assets)


def _flat_icon_reference() -> Image.Image:
    image = Image.new("RGB", (160, 100), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, 39, 39), fill=(200, 60, 50))
    draw.rectangle((40, 24, 55, 39), fill=(60, 130, 200))
    draw.rectangle((24, 40, 39, 47), fill=(240, 190, 60))
    draw.rectangle((40, 40, 55, 47), fill=(90, 170, 90))
    return image


def test_freeze_stamps_trace_eligibility_into_opportunity_map(tmp_path: Path):
    run = _run(tmp_path, "eligibility-stamp")
    _configure_asset_inventory(run)
    freeze_inventory(run)
    assets = read_json(run.assets_path)
    opportunity = assets["microasset_opportunity_map"][0]
    with Image.open(run.source_png) as reference:
        expected = compute_trace_eligibility(reference.convert("RGB").crop((20, 20, 60, 50)))
    assert opportunity["trace_eligibility"] == expected["classification"]
    assert opportunity["trace_eligibility_statistics"] == expected["statistics"]
    receipt = read_json(run.root / ASSET_CONTRACT_RECEIPT_PATH)
    assert receipt["asset_contract_sha256"] == asset_contract_sha256(assets)
    assert asset_contract_blockers(run) == []


def test_freeze_eligibility_classifies_flat_illustration_crop(tmp_path: Path):
    run = _run(tmp_path, "eligibility-flat", image=_flat_icon_reference())
    _configure_asset_inventory(run)
    freeze_inventory(run)
    opportunity = read_json(run.assets_path)["microasset_opportunity_map"][0]
    assert opportunity["trace_eligibility"] == "flat-illustration"
    statistics = opportunity["trace_eligibility_statistics"]
    assert statistics["width"] == 40
    assert statistics["height"] == 30
    assert asset_contract_blockers(run) == []


def test_eligibility_stamp_is_stable_across_refreezes(tmp_path: Path):
    run = _run(tmp_path, "eligibility-refreeze")
    _configure_asset_inventory(run)
    freeze_inventory(run)
    stamped_assets = run.assets_path.read_bytes()
    first_sha256 = read_json(run.root / ASSET_CONTRACT_RECEIPT_PATH)[
        "asset_contract_sha256"
    ]
    freeze_inventory(run)
    assert run.assets_path.read_bytes() == stamped_assets
    assert (
        read_json(run.root / ASSET_CONTRACT_RECEIPT_PATH)["asset_contract_sha256"]
        == first_sha256
    )
    assert asset_contract_blockers(run) == []


def test_legacy_frozen_map_without_eligibility_fields_still_verifies(tmp_path: Path):
    run = _run(tmp_path, "eligibility-legacy")
    _configure_asset_inventory(run)
    freeze_inventory(run)
    assets = read_json(run.assets_path)
    for item in assets["microasset_opportunity_map"]:
        item.pop("trace_eligibility", None)
        item.pop("trace_eligibility_statistics", None)
    write_json(run.assets_path, assets)
    receipt_path = run.root / ASSET_CONTRACT_RECEIPT_PATH
    receipt = read_json(receipt_path)
    payload = canonical_asset_contract_payload(assets)
    receipt["asset_contract_sha256"] = asset_contract_sha256(assets)
    receipt["microasset_opportunity_map_sha256"] = canonical_sha256(
        payload["microasset_opportunity_map"]
    )
    write_json(receipt_path, receipt)
    assert asset_contract_blockers(run) == []


def test_trace_eligibility_fields_are_optional_but_validated(tmp_path: Path):
    run = _run(tmp_path, "eligibility-schema")
    _configure_asset_inventory(run)
    assets = read_json(run.assets_path)
    opportunity = assets["microasset_opportunity_map"][0]

    opportunity["trace_eligibility"] = "not-a-class"
    with pytest.raises(AssetSpecError, match="trace-eligibility"):
        canonical_asset_contract_payload(assets)

    opportunity["trace_eligibility"] = "flat-illustration"
    with pytest.raises(AssetSpecError, match="trace-eligibility-pairing"):
        canonical_asset_contract_payload(assets)

    opportunity["trace_eligibility_statistics"] = {"width": "40"}
    with pytest.raises(AssetSpecError, match="trace-eligibility-statistics"):
        canonical_asset_contract_payload(assets)

    opportunity["trace_eligibility_statistics"] = {"width": 40, "height": 30}
    canonical_asset_contract_payload(assets)
