from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from tools.core import common
from tools.core.contracts import read_json, write_json
from tools.pipeline.normalize_source import (
    _frozen_text_visual_bboxes,
    normalize_source,
)
from tools.assets.reference_inventory import OBJECT_KINDS, freeze_inventory


SVG_NS = "{http://www.w3.org/2000/svg}"


def _run_with_frozen_text_inventory(tmp_path: Path, *, kind: str = "text") -> common.Run:
    reference = tmp_path / f"reference-{kind}.png"
    Image.new("RGB", (160, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case=f"normalize-{kind}",
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )
    payload = read_json(run.regions_path)
    payload["regions"] = [
        {
            "id": "label-region",
            "label": "Frozen label",
            "bbox": [5, 5, 70, 35],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": ["scene-text-001"],
        }
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {item: 0 for item in OBJECT_KINDS}
    inventory["expected_counts"][kind] = 1
    zero_kinds = ["arrow", "icon", "brace"]
    if kind != "text":
        zero_kinds.append("text")
    inventory["zero_count_authorizations"] = [
        {
            "kind": zero_kind,
            "basis": "full-reference-review",
            "reviewer": "normalize-source-test",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for zero_kind in zero_kinds
    ]
    inventory["objects"] = [
        {
            "id": "frozen-label",
            "kind": kind,
            "bbox": [10, 14, 45, 20],
            "element_ids": ["scene-text-001"],
            "critical_region_ids": ["label-region"],
            "typography": {
                "exact_text": "Frozen label",
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
    freeze_inventory(run)
    return run


def _run_with_frozen_topology_inventory(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-topology.png"
    Image.new("RGB", (160, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="normalize-topology",
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )
    payload = read_json(run.regions_path)
    element_ids = ["node-a", "node-b", "edge-ab", "pair-a", "pair-b"]
    payload["regions"] = [
        {
            "id": "topology-region",
            "label": "Frozen relation and pair topology",
            "bbox": [5, 5, 150, 90],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": element_ids,
        }
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {item: 0 for item in OBJECT_KINDS}
    inventory["expected_counts"]["shape"] = 1
    inventory["zero_count_authorizations"] = [
        {
            "kind": zero_kind,
            "basis": "full-reference-review",
            "reviewer": "normalize-source-test",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for zero_kind in ("text", "arrow", "icon", "brace")
    ]
    inventory["objects"] = [
        {
            "id": "logical-network",
            "kind": "shape",
            "bbox": [10, 15, 140, 65],
            "element_ids": element_ids,
            "critical_region_ids": ["topology-region"],
            "topology_contract": {
                "role_counts": {"node": 2, "edge": 1, "pair-member": 2},
                "role_mapping": {
                    "node-a": "node",
                    "node-b": "node",
                    "edge-ab": "edge",
                    "pair-a": "pair-member",
                    "pair-b": "pair-member",
                },
                "required_pairs": [
                    {"id": "paired-labels", "a": "pair-a", "b": "pair-b"}
                ],
                "relations": [
                    {
                        "id": "logical-edge",
                        "element_id": "edge-ab",
                        "source_id": "node-a",
                        "target_id": "node-b",
                        "relation": "connection",
                    }
                ],
                "component_count": 2,
            },
        }
    ]
    write_json(run.regions_path, payload)
    freeze_inventory(run)
    return run


def _topology_source(
    path: Path,
    *,
    relation_attributes: str = "",
    pair_a_attributes: str = "",
    pair_b_attributes: str = "",
    omit: str | None = None,
    duplicate_relation_target: bool = False,
) -> Path:
    records = {
        "node-a": '<rect id="node-a" x="10" y="20" width="20" height="15"/>',
        "node-b": '<rect id="node-b" x="120" y="20" width="20" height="15"/>',
        "edge-ab": (
            '<line id="edge-ab" x1="30" y1="27" x2="120" y2="27" '
            f'{relation_attributes}/>'
        ),
        "pair-a": (
            '<rect id="pair-a" x="45" y="65" width="20" height="10" '
            f'{pair_a_attributes}/>'
        ),
        "pair-b": (
            '<rect id="pair-b" x="85" y="65" width="20" height="10" '
            f'{pair_b_attributes}/>'
        ),
    }
    if omit is not None:
        records[omit] = ""
    duplicate = records["edge-ab"] if duplicate_relation_target else ""
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        'viewBox="0 0 160 100">'
        + "".join(records.values())
        + duplicate
        + "</svg>",
        encoding="utf-8",
    )
    return path


def _source(path: Path, *, visual_bbox: str | None = None) -> Path:
    bbox_attribute = (
        "" if visual_bbox is None else f' data-visual-bbox="{visual_bbox}"'
    )
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        'viewBox="0 0 160 100">'
        f'<text x="10" y="30" font-family="Arial" font-size="18"'
        f'{bbox_attribute}>Frozen label</text>'
        "</svg>",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("kind", ["text", "formula"])
def test_normalize_injects_hash_bound_bbox_after_stable_id_assignment(
    tmp_path: Path,
    kind: str,
):
    run = _run_with_frozen_text_inventory(tmp_path, kind=kind)
    output = tmp_path / f"normalized-{kind}.svg"

    report = normalize_source(run, _source(tmp_path / f"source-{kind}.svg"), output)

    receipt = read_json(run.qa_dir / "reference-inventory-receipt.json")
    root = ET.parse(output).getroot()
    text = root.find(f"{SVG_NS}text")
    assert text is not None
    assert root.get("data-object-inventory-sha256") == receipt["inventory_sha256"]
    assert report["object_inventory_sha256"] == receipt["inventory_sha256"]
    assert text.get("id") == "scene-text-001"
    assert text.get("data-visual-bbox") == "10 14 45 20"
    assert text.get("x") == "10"
    assert text.get("y") == "30"
    assert text.get("font-family") == "Arial"
    assert text.get("font-size") == "18"
    assert text.text == "Frozen label"
    assert report["assigned_ids"] == ["scene-text-001"]
    assert report["visual_bbox_injected_ids"] == ["scene-text-001"]
    assert report["visual_repairs_applied"] is False


def test_normalize_rejects_conflicting_existing_visual_bbox(tmp_path: Path):
    run = _run_with_frozen_text_inventory(tmp_path)
    output = tmp_path / "conflict-output.svg"

    with pytest.raises(SystemExit, match="conflicting data-visual-bbox"):
        normalize_source(
            run,
            _source(tmp_path / "conflict-source.svg", visual_bbox="1 2 3 4"),
            output,
        )

    assert not output.exists()


def test_normalize_visual_bbox_injection_is_byte_idempotent(tmp_path: Path):
    run = _run_with_frozen_text_inventory(tmp_path)
    first = tmp_path / "first.svg"
    second = tmp_path / "second.svg"

    first_report = normalize_source(run, _source(tmp_path / "source.svg"), first)
    second_report = normalize_source(run, first, second)

    assert first.read_bytes() == second.read_bytes()
    assert first_report["visual_bbox_injected_count"] == 1
    assert second_report["visual_bbox_injected_count"] == 0
    assert second_report["visual_bbox_existing_ids"] == ["scene-text-001"]


def test_normalize_does_not_guess_multi_element_or_non_text_bboxes():
    expected, skipped = _frozen_text_visual_bboxes(
        {
            "objects": [
                {
                    "id": "multi-text",
                    "kind": "text",
                    "bbox": [1, 2, 3, 4],
                    "element_ids": ["line-a", "line-b"],
                },
                {
                    "id": "ordinary-shape",
                    "kind": "shape",
                    "bbox": [5, 6, 7, 8],
                    "element_ids": ["shape-a"],
                },
            ]
        }
    )

    assert expected == {}
    assert skipped == ["multi-text"]


def test_normalize_injects_frozen_relation_and_pair_metadata(tmp_path: Path):
    run = _run_with_frozen_topology_inventory(tmp_path)
    output = tmp_path / "normalized-topology.svg"

    report = normalize_source(
        run,
        _topology_source(tmp_path / "topology-source.svg"),
        output,
    )

    root = ET.parse(output).getroot()
    by_id = {element.get("id"): element for element in root.iter() if element.get("id")}
    edge = by_id["edge-ab"]
    assert edge.get("data-source-id") == "node-a"
    assert edge.get("data-target-id") == "node-b"
    assert edge.get("data-topology-relation") == "connection"
    assert by_id["pair-a"].get("data-pair-with") == "pair-b"
    assert by_id["pair-b"].get("data-pair-with") == "pair-a"
    assert report["topology_relation_contract_count"] == 1
    assert report["topology_relation_metadata_injected_ids"] == ["edge-ab"]
    assert report["topology_pair_contract_count"] == 1
    assert report["topology_pair_metadata_injected_ids"] == ["pair-a", "pair-b"]


def test_normalize_topology_metadata_is_byte_idempotent(tmp_path: Path):
    run = _run_with_frozen_topology_inventory(tmp_path)
    first = tmp_path / "normalized-topology-first.svg"
    second = tmp_path / "normalized-topology-second.svg"

    normalize_source(run, _topology_source(tmp_path / "topology-source.svg"), first)
    report = normalize_source(run, first, second)

    assert first.read_bytes() == second.read_bytes()
    assert report["topology_relation_metadata_injected_count"] == 0
    assert report["topology_relation_metadata_existing_ids"] == ["edge-ab"]
    assert report["topology_pair_metadata_injected_count"] == 0
    assert report["topology_pair_metadata_existing_ids"] == ["pair-a", "pair-b"]


@pytest.mark.parametrize(
    ("source_kwargs", "message"),
    [
        (
            {
                "relation_attributes": (
                    'data-source-id="node-b" data-target-id="node-a" '
                    'data-topology-relation="connection"'
                )
            },
            "conflicting data-source-id",
        ),
        (
            {
                "relation_attributes": (
                    'data-source-id="node-a" data-target-id="node-b" '
                    'data-topology-relation="wrong-kind"'
                )
            },
            "conflicting data-topology-relation",
        ),
        ({"pair_a_attributes": 'data-pair-with="node-a"'}, "conflicting data-pair-with"),
    ],
)
def test_normalize_rejects_conflicting_frozen_topology_metadata(
    tmp_path: Path,
    source_kwargs: dict[str, str],
    message: str,
):
    run = _run_with_frozen_topology_inventory(tmp_path)
    output = tmp_path / "topology-conflict.svg"

    with pytest.raises(SystemExit, match=message):
        normalize_source(
            run,
            _topology_source(tmp_path / "topology-conflict-source.svg", **source_kwargs),
            output,
        )

    assert not output.exists()


@pytest.mark.parametrize(
    ("source_kwargs", "message"),
    [
        ({"omit": "edge-ab"}, "relation element edge-ab is missing"),
        ({"omit": "pair-b"}, "pair element pair-b is missing"),
        (
            {"duplicate_relation_target": True},
            "relation target edge-ab resolved 2 times",
        ),
    ],
)
def test_normalize_rejects_missing_or_duplicate_topology_targets(
    tmp_path: Path,
    source_kwargs: dict[str, object],
    message: str,
):
    run = _run_with_frozen_topology_inventory(tmp_path)
    output = tmp_path / "topology-target-error.svg"

    with pytest.raises(SystemExit, match=message):
        normalize_source(
            run,
            _topology_source(tmp_path / "topology-target-source.svg", **source_kwargs),
            output,
        )

    assert not output.exists()
