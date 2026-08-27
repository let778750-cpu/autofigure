from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools.core import common
from tools.pipeline.check import _strict_live_blockers, main as check_main
from tools.core.contracts import read_json, write_json
from tools.pipeline.ingest import main as ingest_main
from tools.pipeline.prepare import main as prepare_main
from tools.assets.reference_inventory import (
    OBJECT_KINDS,
    RECEIPT_PATH,
    freeze_inventory,
    inventory_blockers,
    svg_text_blockers,
    validate_inventory,
)
from tools.__main__ import main as cli_main


def _reference(tmp_path: Path) -> Path:
    path = tmp_path / "reference.png"
    Image.new("RGB", (160, 100), "white").save(path)
    return path


def _run(tmp_path: Path, case: str = "inventory") -> common.Run:
    cases_root = tmp_path / "examples"
    assert (
        prepare_main(
            [
                str(_reference(tmp_path)),
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


def _candidate(tmp_path: Path, text: str = "Frozen title") -> Path:
    path = tmp_path / f"candidate-{len(list(tmp_path.glob('candidate-*.svg')))}.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        'viewBox="0 0 160 100">'
        f'<text id="title" x="20" y="30" font-size="18">{text}</text>'
        "</svg>",
        encoding="utf-8",
    )
    return path


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
        },
        {
            "id": "whole-canvas",
            "label": "Whole canvas (diagnostic only)",
            "bbox": [0, 0, 160, 100],
            "critical": False,
        },
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {kind: 0 for kind in OBJECT_KINDS}
    inventory["expected_counts"]["text"] = 1
    inventory["zero_count_authorizations"] = [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "test-reviewer",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("arrow", "icon", "brace")
    ]
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


def _configure_topology_inventory(run: common.Run) -> None:
    payload = read_json(run.regions_path)
    element_ids = ["molecule-bond-01", "molecule-atom-01", "molecule-atom-02"]
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
    inventory["zero_count_authorizations"] = [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "test-reviewer",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("text", "arrow", "brace")
    ]
    inventory["objects"] = [
        {
            "id": "molecule",
            "kind": "icon",
            "bbox": [20, 20, 40, 30],
            "element_ids": element_ids,
            "critical_region_ids": ["molecule-region"],
            "visual": {
                "aspect_ratio_tolerance": 0.03,
                "bbox_tolerance_px": 2,
            },
            "contract_refs": {
                "ink_contract": {"region_id": "molecule-region"},
            },
            "topology_contract": {
                "role_counts": {"atom": 2, "bond": 1},
                "role_mapping": {
                    "molecule-atom-01": "atom",
                    "molecule-atom-02": "atom",
                    "molecule-bond-01": "bond",
                },
                "required_pairs": [],
                "relations": [
                    {
                        "id": "molecule-bond-01",
                        "source_id": "molecule-atom-01",
                        "target_id": "molecule-atom-02",
                        "relation": "bond",
                    }
                ],
                "component_count": 1,
            },
        }
    ]
    payload["arrow_visual_expectation"] = {"count": 0, "contracts": []}
    write_json(run.regions_path, payload)


def _configure_arrow_inventory(run: common.Run) -> None:
    payload = read_json(run.regions_path)
    payload["regions"] = [
        {
            "id": "flow-region",
            "label": "Directed flow",
            "bbox": [10, 10, 140, 80],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": ["source", "flow", "target"],
            "required_relations": [
                {
                    "id": "flow",
                    "source_id": "source",
                    "target_id": "target",
                    "relation": "data-flow",
                    "direction": "forward",
                    "start_head_type": "none",
                    "end_head_type": "triangle",
                    "representation": "line_arrow",
                    "visible_object_count": 1,
                }
            ],
        }
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {kind: 0 for kind in OBJECT_KINDS}
    inventory["expected_counts"]["arrow"] = 1
    inventory["expected_counts"]["shape"] = 2
    inventory["zero_count_authorizations"] = [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "test-reviewer",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("text", "icon", "brace")
    ]
    inventory["objects"] = [
        {
            "id": "source-object",
            "kind": "shape",
            "bbox": [20, 35, 30, 30],
            "element_ids": ["source"],
            "critical_region_ids": ["flow-region"],
        },
        {
            "id": "flow-object",
            "kind": "arrow",
            "bbox": [65, 45, 30, 10],
            "element_ids": ["flow"],
            "critical_region_ids": ["flow-region"],
            "contract_refs": {
                "required_relation": {
                    "region_id": "flow-region",
                    "relation_id": "flow",
                },
                "arrow_visual": {"contract_id": "arrow-visual-flow"},
            },
        },
        {
            "id": "target-object",
            "kind": "shape",
            "bbox": [110, 35, 30, 30],
            "element_ids": ["target"],
            "critical_region_ids": ["flow-region"],
        },
    ]
    payload["arrow_visual_contracts"] = [
        {
            "id": "arrow-visual-flow",
            "element_id": "flow",
        }
    ]
    payload["arrow_visual_expectation"] = {
        "count": 1,
        "contracts": [
            {
                "element_id": "flow",
                "head_sides": ["end"],
                "contract_sha256": "0" * 64,
            }
        ],
        "exemptions": [],
    }
    write_json(run.regions_path, payload)


def _configure_logical_group_scope_inventory(run: common.Run) -> None:
    payload = read_json(run.regions_path)
    payload["regions"] = [
        {
            "id": "logical-flow-region",
            "label": "Logical group endpoints and native leaves",
            "bbox": [5, 10, 150, 80],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": [
                "source-node",
                "source-body",
                "source-port",
                "flow",
                "target-node",
                "target-body",
                "target-port",
            ],
            "required_relations": [
                {
                    "id": "flow",
                    "source_id": "source-node",
                    "target_id": "target-node",
                    "relation": "logical-node-flow",
                    "direction": "forward",
                    "start_head_type": "none",
                    "end_head_type": "triangle",
                    "representation": "line_arrow",
                    "visible_object_count": 1,
                }
            ],
        }
    ]
    inventory = payload["reference_inventory"]
    inventory["expected_counts"] = {kind: 0 for kind in OBJECT_KINDS}
    inventory["expected_counts"]["arrow"] = 1
    inventory["expected_counts"]["shape"] = 2
    inventory["zero_count_authorizations"] = [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "test-reviewer",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("text", "icon", "brace")
    ]
    inventory["objects"] = [
        {
            "id": "source-node",
            "kind": "shape",
            "bbox": [15, 30, 35, 40],
            "element_ids": ["source-body", "source-port"],
            "critical_region_ids": ["logical-flow-region"],
        },
        {
            "id": "flow-object",
            "kind": "arrow",
            "bbox": [55, 45, 50, 10],
            "element_ids": ["flow"],
            "critical_region_ids": ["logical-flow-region"],
            "contract_refs": {
                "required_relation": {
                    "region_id": "logical-flow-region",
                    "relation_id": "flow",
                },
                "arrow_visual": {"contract_id": "logical-flow-visual"},
            },
        },
        {
            "id": "target-node",
            "kind": "shape",
            "bbox": [110, 30, 35, 40],
            "element_ids": ["target-body", "target-port"],
            "critical_region_ids": ["logical-flow-region"],
        },
    ]
    payload["arrow_visual_contracts"] = [
        {"id": "logical-flow-visual", "element_id": "flow"}
    ]
    payload["arrow_visual_expectation"] = {
        "count": 1,
        "contracts": [
            {
                "element_id": "flow",
                "head_sides": ["end"],
                "contract_sha256": "0" * 64,
            }
        ],
        "exemptions": [],
    }
    write_json(run.regions_path, payload)


def test_new_prepare_requires_inventory_freeze_before_ingest(tmp_path: Path):
    run = _run(tmp_path, "draft")
    inventory = read_json(run.regions_path)["reference_inventory"]
    assert inventory["required"] is True
    assert inventory["status"] == "draft"
    assert inventory["reference_sha256"] == run.load_meta()["source_sha256"]

    with pytest.raises(SystemExit, match="must be frozen"):
        ingest_main([str(run.root), str(_candidate(tmp_path)), "--kind", "svg"])
    assert not run.redraw_svg.exists()


def test_freeze_writes_hash_bound_receipt_refreshes_tasks_and_allows_ingest(
    tmp_path: Path,
):
    run = _run(tmp_path, "frozen")
    _configure_text_inventory(run)

    receipt = freeze_inventory(run)

    assert receipt["status"] == "PASS"
    assert receipt["object_count"] == 1
    assert (run.root / RECEIPT_PATH).is_file()
    tasks = read_json(run.region_tasks_path)
    assert tasks["reference_inventory_status"] == "frozen"
    assert tasks["reference_inventory_sha256"] == receipt["inventory_sha256"]
    assert inventory_blockers(run) == []
    assert ingest_main([str(run.root), str(_candidate(tmp_path)), "--kind", "svg"]) == 0


def test_freeze_accepts_complete_arrow_relation_with_optional_semantics(
    tmp_path: Path,
):
    run = _run(tmp_path, "complete-arrow-relation")
    _configure_arrow_inventory(run)

    receipt = freeze_inventory(run)

    assert receipt["status"] == "PASS"
    assert receipt["counts"]["arrow"] == 1

    run_without_semantics = _run(tmp_path, "arrow-without-semantics")
    _configure_arrow_inventory(run_without_semantics)
    payload = read_json(run_without_semantics.regions_path)
    payload["regions"][0]["required_relations"][0].pop("relation")
    write_json(run_without_semantics.regions_path, payload)
    assert freeze_inventory(run_without_semantics)["status"] == "PASS"


def test_freeze_accepts_logical_group_scope_ids_alongside_owned_leaf_ids(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "logical-group-scope")
    _configure_logical_group_scope_inventory(run)

    draft_report = validate_inventory(run, require_frozen=False)
    assert draft_report["pass"] is True, draft_report["blockers"]
    assert draft_report["semantic_scope_identity_count"] == 3
    assert not any(
        "critical-element-uninventoried" in blocker
        for blocker in draft_report["blockers"]
    )

    inventory = read_json(run.regions_path)["reference_inventory"]
    source = next(item for item in inventory["objects"] if item["id"] == "source-node")
    target = next(item for item in inventory["objects"] if item["id"] == "target-node")
    assert "source-node" not in source["element_ids"]
    assert "target-node" not in target["element_ids"]
    assert source["element_ids"] == ["source-body", "source-port"]
    assert target["element_ids"] == ["target-body", "target-port"]

    receipt = freeze_inventory(run)
    assert receipt["status"] == "PASS"
    assert inventory_blockers(run) == []


def test_freeze_rejects_semantic_group_id_owned_as_another_objects_leaf(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "semantic-physical-collision")
    _configure_logical_group_scope_inventory(run)
    payload = read_json(run.regions_path)
    target = next(
        item
        for item in payload["reference_inventory"]["objects"]
        if item["id"] == "target-node"
    )
    target["element_ids"].append("source-node")
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="semantic-physical-id-collision:source-node"):
        freeze_inventory(run)


def test_logical_scope_support_keeps_leaf_ownership_unique_and_fail_closed(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, "physical-leaf-owner-collision")
    _configure_logical_group_scope_inventory(run)
    payload = read_json(run.regions_path)
    target = next(
        item
        for item in payload["reference_inventory"]["objects"]
        if item["id"] == "target-node"
    )
    target["element_ids"].append("source-body")
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="element-owner-count:source-body"):
        freeze_inventory(run)


@pytest.mark.parametrize(
    "missing_field",
    [
        "id",
        "source_id",
        "target_id",
        "direction",
        "start_head_type",
        "end_head_type",
        "representation",
        "visible_object_count",
    ],
)
def test_freeze_rejects_incomplete_arrow_relation(tmp_path: Path, missing_field: str):
    run = _run(tmp_path, f"arrow-missing-{missing_field}")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["regions"][0]["required_relations"][0].pop(missing_field)
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="required-relation:1:fields"):
        freeze_inventory(run)


@pytest.mark.parametrize(
    ("field", "value", "blocker"),
    [
        ("direction", "sideways", "direction"),
        ("start_head_type", "wedge", "start-head-type"),
        ("end_head_type", "wedge", "end-head-type"),
        ("representation", "arrow-group", "representation"),
        ("visible_object_count", 2, "visible-object-count"),
        ("visible_object_count", True, "visible-object-count"),
    ],
)
def test_freeze_rejects_invalid_arrow_relation_values(
    tmp_path: Path, field: str, value: object, blocker: str
):
    run = _run(tmp_path, f"arrow-invalid-{field}-{value}")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["regions"][0]["required_relations"][0][field] = value
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match=rf"required-relation:1:{blocker}"):
        freeze_inventory(run)


def test_freeze_rejects_direction_that_disagrees_with_head_presence(
    tmp_path: Path,
):
    run = _run(tmp_path, "arrow-direction-heads")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["regions"][0]["required_relations"][0]["direction"] = "backward"
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="required-relation:1:direction-heads"):
        freeze_inventory(run)


def test_freeze_allows_only_the_declared_optional_relation_semantics(
    tmp_path: Path,
):
    run = _run(tmp_path, "arrow-unknown-relation-field")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["regions"][0]["required_relations"][0]["confidence"] = 0.99
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="required-relation:1:fields"):
        freeze_inventory(run)


def test_freeze_binds_relation_object_and_visual_contract_one_to_one(
    tmp_path: Path,
):
    run = _run(tmp_path, "arrow-visual-mismatch")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["arrow_visual_contracts"][0]["element_id"] = "target"
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="arrow-visual-element-mismatch"):
        freeze_inventory(run)


def test_freeze_rejects_unidentified_arrow_visual_contract(tmp_path: Path):
    run = _run(tmp_path, "arrow-visual-unidentified")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["arrow_visual_contracts"][0].pop("element_id")
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="arrow-visual-contract:1:identity"):
        freeze_inventory(run)


def test_freeze_rejects_arrow_object_with_multiple_logical_element_ids(
    tmp_path: Path,
):
    run = _run(tmp_path, "arrow-multiple-elements")
    _configure_arrow_inventory(run)
    payload = read_json(run.regions_path)
    payload["reference_inventory"]["objects"][1]["element_ids"] = [
        "flow",
        "source",
    ]
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="arrow-element-count"):
        freeze_inventory(run)


def test_visual_microasset_requires_a_tight_object_level_ink_region(tmp_path: Path):
    run = _run(tmp_path, "broad-icon-region")
    payload = read_json(run.regions_path)
    payload["regions"] = [
        {
            "id": "icon-region",
            "label": "Incorrectly broad icon region",
            "bbox": [0, 0, 160, 100],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": ["icon"],
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
    inventory["zero_count_authorizations"] = [
        {
            "kind": kind,
            "basis": "full-reference-review",
            "reviewer": "test-reviewer",
            "reference_sha256": run.load_meta()["source_sha256"],
        }
        for kind in ("text", "arrow", "brace")
    ]
    inventory["objects"] = [
        {
            "id": "icon-object",
            "kind": "icon",
            "bbox": [50, 40, 20, 20],
            "element_ids": ["icon"],
            "critical_region_ids": ["icon-region"],
            "visual": {
                "aspect_ratio_tolerance": 0.03,
                "bbox_tolerance_px": 2,
            },
            "contract_refs": {"ink_contract": {"region_id": "icon-region"}},
        }
    ]
    payload["arrow_visual_expectation"] = {"count": 0, "contracts": []}
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="ink-contract-not-tight"):
        freeze_inventory(run)


def test_public_freeze_cli_dispatches_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run = _run(tmp_path, "freeze-cli")
    _configure_text_inventory(run)
    monkeypatch.setattr("sys.argv", ["autofigure", "freeze", str(run.root)])

    assert cli_main() == 0
    assert (run.root / RECEIPT_PATH).is_file()


@pytest.mark.parametrize("mutated_file", ["regions", "tasks"])
def test_stale_inventory_receipt_refuses_ingest(tmp_path: Path, mutated_file: str):
    run = _run(tmp_path, f"stale-{mutated_file}")
    _configure_text_inventory(run)
    freeze_inventory(run)
    if mutated_file == "regions":
        payload = read_json(run.regions_path)
        payload["regions"][0]["label"] = "Changed after freeze"
        write_json(run.regions_path, payload)
    else:
        tasks = read_json(run.region_tasks_path)
        tasks["tasks"][0]["label"] = "Changed after freeze"
        write_json(run.region_tasks_path, tasks)

    with pytest.raises(SystemExit, match="receipt-stale"):
        ingest_main([str(run.root), str(_candidate(tmp_path)), "--kind", "svg"])


@pytest.mark.parametrize("missing_kind", ["text", "arrow", "icon"])
def test_inventory_count_closure_rejects_missing_declared_objects(
    tmp_path: Path, missing_kind: str
):
    run = _run(tmp_path, f"missing-{missing_kind}")
    _configure_text_inventory(run)
    payload = read_json(run.regions_path)
    inventory = payload["reference_inventory"]
    if missing_kind == "text":
        inventory["expected_counts"]["text"] = 2
    else:
        inventory["expected_counts"][missing_kind] = 1
        inventory["zero_count_authorizations"] = [
            item for item in inventory["zero_count_authorizations"] if item["kind"] != missing_kind
        ]
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match=f"count-mismatch:{missing_kind}"):
        freeze_inventory(run)


def test_zero_core_inventory_requires_explicit_full_reference_authorization(
    tmp_path: Path,
):
    run = _run(tmp_path, "zero-unverified")

    with pytest.raises(SystemExit, match="zero-unverified:text"):
        freeze_inventory(run)


def test_present_inventory_cannot_disable_the_required_freeze_gate(tmp_path: Path):
    run = _run(tmp_path, "required-flag")
    _configure_text_inventory(run)
    payload = read_json(run.regions_path)
    payload["reference_inventory"]["required"] = False
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="required-flag"):
        freeze_inventory(run)


def test_frozen_exact_text_inventory_blocks_svg_text_drift(tmp_path: Path):
    run = _run(tmp_path, "text-drift")
    _configure_text_inventory(run)
    freeze_inventory(run)
    run.redraw_svg.write_text(
        _candidate(tmp_path, "Wrong title").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert svg_text_blockers(run) == ["reference-inventory:text-exact-mismatch:title"]


def test_freeze_receipt_hash_binds_object_topology_contract(tmp_path: Path):
    run = _run(tmp_path, "topology-receipt")
    _configure_topology_inventory(run)

    receipt = freeze_inventory(run)

    assert receipt["topology_contract_count"] == 1
    assert len(receipt["topology_contracts_sha256"]) == 64
    payload = read_json(run.regions_path)
    payload["reference_inventory"]["objects"][0]["topology_contract"]["relations"][0][
        "relation"
    ] = "link"
    write_json(run.regions_path, payload)
    assert "reference-inventory:receipt-stale:topology_contracts_sha256" in inventory_blockers(run)


def test_freeze_rejects_topology_role_count_that_does_not_close(tmp_path: Path):
    run = _run(tmp_path, "topology-count")
    _configure_topology_inventory(run)
    payload = read_json(run.regions_path)
    payload["reference_inventory"]["objects"][0]["topology_contract"]["role_counts"]["bond"] = 2
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="topology-role-count:bond"):
        freeze_inventory(run)


def test_anisotropic_source_transform_allowance_requires_a_frozen_basis(
    tmp_path: Path,
):
    run = _run(tmp_path, "anisotropy-allowance")
    _configure_topology_inventory(run)
    payload = read_json(run.regions_path)
    visual = payload["reference_inventory"]["objects"][0]["visual"]
    visual["allow_source_anisotropic_scale"] = True
    write_json(run.regions_path, payload)

    with pytest.raises(SystemExit, match="object:molecule:visual"):
        freeze_inventory(run)

    visual["source_anisotropy_basis"] = "reference-measured-affine-distortion"
    write_json(run.regions_path, payload)
    receipt = freeze_inventory(run)

    assert receipt["status"] == "PASS"


def test_legacy_case_without_inventory_must_migrate_before_ingest(tmp_path: Path):
    run = _run(tmp_path, "legacy")
    payload = read_json(run.regions_path)
    payload.pop("reference_inventory")
    write_json(run.regions_path, payload)

    assert inventory_blockers(run) == []
    with pytest.raises(
        ValueError,
        match="reference inventory must be frozen before source-gate evaluation",
    ):
        ingest_main([str(run.root), str(_candidate(tmp_path)), "--kind", "svg"])


def test_strict_profile_cannot_skip_ocr():
    with pytest.raises(SystemExit, match="does not allow --skip-ocr"):
        check_main(["missing-case", "--profile", "strict", "--skip-ocr"])


def test_strict_profile_requires_live_finalizer_evidence(tmp_path: Path):
    run = _run(tmp_path, "live-required")

    assert _strict_live_blockers(run, {"regions": []}, "standard") == []
    assert _strict_live_blockers(run, {"regions": []}, "strict") == ["live-evidence-missing"]
