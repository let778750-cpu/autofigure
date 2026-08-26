from __future__ import annotations

from pathlib import Path

from PIL import Image

from tools.core import common
from tools.arrows.arrow_spec import spec_sha256
from tools.core.contracts import read_json, write_json
from tools.pipeline.ingest import build_region_tasks
from tools.regions.regions import build_critical_region_expectation, evaluate_regions


def _head(kind: str) -> dict:
    if kind == "none":
        return {"type": "none", "width": None, "length": None, "color": None}
    return {"type": kind, "width": "med", "length": "med", "color": "#9A7600"}


def _arrow_spec(run: common.Run, *, start: str = "none", end: str = "triangle") -> dict:
    return {
        "schema_version": "1.1.0",
        "representation": "line_arrow",
        "path": {
            "kind": "straight",
            "coordinate_space": "canvas",
            "points": [{"x": 30.0, "y": 70.0}, {"x": 30.0, "y": 40.0}],
        },
        "routing": "host",
        "topology": {
            "mode": "attached",
            "source_id": "lower-box",
            "target_id": "reward-circle",
            "source_site": 0,
            "target_site": 0,
        },
        "body": {
            "color": "#9A7600",
            "width_px": 2.0,
            "dash": "solid",
            "line_cap": "butt",
            "line_join": "miter",
        },
        "start_head": _head(start),
        "end_head": _head(end),
        "silhouette_path": None,
        "fallback_policy": "strict_fail",
        "single_visible_object": True,
        "source_evidence": {
            "input_route": "reference-only",
            "reference_sha256": run.load_meta()["source_sha256"],
            "reference_bbox": [24.0, 36.0, 12.0, 38.0],
            "confidence": 0.99,
        },
    }


def _run(tmp_path: Path) -> common.Run:
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(source)
    run = common.create_run(
        source,
        case="relation-contract",
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )
    Image.open(run.source_png).save(run.render_png)
    spec = _arrow_spec(run)
    elements = [
        {"id": "lower-box", "kind": "shape"},
        {"id": "reward-circle", "kind": "shape"},
        {"id": "lower-to-reward", "kind": "edge", "arrow_spec": spec},
    ]
    edges = [
        {
            "id": "lower-to-reward",
            "source": "lower-box",
            "target": "reward-circle",
            "arrow_spec": spec,
        }
    ]
    write_json(
        run.scene_path,
        {
            "schema_version": "3.1.0",
            "kind": "scene",
            "case": run.root.name,
            "reference_sha256": run.load_meta()["source_sha256"],
            "elements": elements,
            "edges": edges,
        },
    )
    bindings = []
    for shape_id, element_id in enumerate(("lower-box", "reward-circle"), start=1):
        bindings.append(
            {
                "element_id": element_id,
                "shape_id": shape_id,
                "shape_name": f"af-{element_id}",
                "readback_found": True,
            }
        )
    bindings.append(
        {
            "element_id": "lower-to-reward",
            "shape_id": 3,
            "shape_name": "af-lower-to-reward-connector-01",
            "readback_found": True,
            "single_visible_object": True,
            "arrow_spec_sha256": spec_sha256(spec),
        }
    )
    write_json(
        run.bindings_path,
        {
            "schema_version": "3.1.0",
            "kind": "bindings",
            "case": run.root.name,
            "reference_sha256": run.load_meta()["source_sha256"],
            "bindings": bindings,
        },
    )
    write_json(
        run.powerpoint_arrow_readback_path,
        {
            "schema_version": "1.0.0",
            "kind": "powerpoint_arrow_readback",
            "records": [
                {
                    "element_id": "lower-to-reward",
                    "arrow_spec_sha256": spec_sha256(spec),
                    "shape_id": 3,
                    "shape_name": "af-lower-to-reward-connector-01",
                    "status": "PASS",
                }
            ],
        },
    )
    regions = read_json(run.regions_path)
    regions["regions"] = [
        {
            "id": "reward-flow",
            "bbox": [10, 10, 80, 80],
            "critical": True,
            "relations_exhaustive": True,
            "element_ids": ["lower-box", "reward-circle", "lower-to-reward"],
            "required_relations": [
                {
                    "id": "lower-to-reward",
                    "source_id": "lower-box",
                    "target_id": "reward-circle",
                    "direction": "forward",
                    "start_head_type": "none",
                    "end_head_type": "triangle",
                    "representation": "line_arrow",
                    "visible_object_count": 1,
                }
            ],
        }
    ]
    regions["critical_region_expectation"] = build_critical_region_expectation(
        regions
    )
    write_json(run.regions_path, regions)
    return run


def test_declared_relation_closes_regions_scene_arrow_spec_and_binding(tmp_path: Path):
    run = _run(tmp_path)
    report = evaluate_regions(run)
    assert report["strict_pass"] is True
    assert report["blockers"] == []
    assert report["contract_audit"]["pass"] is True


def test_declared_but_omitted_arrow_is_a_fail_closed_region_blocker(tmp_path: Path):
    run = _run(tmp_path)
    scene = read_json(run.scene_path)
    scene["elements"] = [item for item in scene["elements"] if item["id"] != "lower-to-reward"]
    scene["edges"] = []
    write_json(run.scene_path, scene)
    bindings = read_json(run.bindings_path)
    bindings["bindings"] = [
        item for item in bindings["bindings"] if item["element_id"] != "lower-to-reward"
    ]
    write_json(run.bindings_path, bindings)

    report = evaluate_regions(run)
    assert report["strict_pass"] is False
    assert "region-contract:reward-flow:scene-element-missing:lower-to-reward" in report["blockers"]
    assert "region-contract:reward-flow:scene-edge-missing:lower-to-reward" in report["blockers"]
    assert "region-contract:reward-flow:binding-missing:lower-to-reward" in report["blockers"]


def test_relation_contract_rejects_wrong_arrow_direction_and_head(tmp_path: Path):
    run = _run(tmp_path)
    scene = read_json(run.scene_path)
    wrong = _arrow_spec(run, start="triangle", end="none")
    scene["elements"][-1]["arrow_spec"] = wrong
    scene["edges"][0]["arrow_spec"] = wrong
    write_json(run.scene_path, scene)
    bindings = read_json(run.bindings_path)
    arrow_binding = bindings["bindings"][-1]
    arrow_binding["arrow_spec_sha256"] = spec_sha256(wrong)
    write_json(run.bindings_path, bindings)

    blockers = evaluate_regions(run)["blockers"]
    assert "region-contract:reward-flow:arrow-direction-mismatch:lower-to-reward" in blockers
    assert "region-contract:reward-flow:arrow-start-head-mismatch:lower-to-reward" in blockers
    assert "region-contract:reward-flow:arrow-end-head-mismatch:lower-to-reward" in blockers


def test_relation_contract_requires_one_bound_visible_arrow_object(tmp_path: Path):
    run = _run(tmp_path)
    bindings = read_json(run.bindings_path)
    arrow_binding = bindings["bindings"][-1]
    arrow_binding["backend_object_ids"] = ["shape-3", "shape-4"]
    arrow_binding["single_visible_object"] = False
    write_json(run.bindings_path, bindings)

    blockers = evaluate_regions(run)["blockers"]
    assert "region-contract:reward-flow:visible-object-count:lower-to-reward" in blockers
    assert "region-contract:reward-flow:arrow-binding-not-single-object:lower-to-reward" in blockers
    assert (
        "region-contract:reward-flow:arrow-visible-object-count-mismatch:lower-to-reward"
        in blockers
    )


def test_required_relation_endpoints_must_have_unique_scene_and_binding_readback(
    tmp_path: Path,
):
    run = _run(tmp_path)
    scene = read_json(run.scene_path)
    scene["elements"].append({"id": "reward-circle", "kind": "shape"})
    write_json(run.scene_path, scene)
    bindings = read_json(run.bindings_path)
    bindings["bindings"] = [
        item for item in bindings["bindings"] if item["element_id"] != "lower-box"
    ]
    write_json(run.bindings_path, bindings)

    blockers = evaluate_regions(run)["blockers"]
    assert (
        "region-contract:reward-flow:required-relation-source-binding-missing:lower-box"
        in blockers
    )
    assert (
        "region-contract:reward-flow:required-relation-target-scene-duplicate:reward-circle"
        in blockers
    )


def test_readback_found_must_be_explicitly_true(tmp_path: Path):
    run = _run(tmp_path)
    bindings = read_json(run.bindings_path)
    bindings["bindings"][0].pop("readback_found")
    write_json(run.bindings_path, bindings)

    blockers = evaluate_regions(run)["blockers"]
    assert "region-contract:reward-flow:binding-readback-missing:lower-box" in blockers
    assert (
        "region-contract:reward-flow:required-relation-source-readback-missing:lower-box"
        in blockers
    )


def test_composite_non_arrow_binding_is_not_forced_to_one_object(tmp_path: Path):
    run = _run(tmp_path)
    bindings = read_json(run.bindings_path)
    bindings["bindings"][0]["backend_object_ids"] = ["shape-1a", "shape-1b"]
    write_json(run.bindings_path, bindings)

    report = evaluate_regions(run)
    assert report["contract_audit"]["pass"] is True
    assert not any(
        blocker.endswith("visible-object-count:lower-box")
        for blocker in report["blockers"]
    )


def test_explicit_single_object_contract_still_rejects_composite_binding(
    tmp_path: Path,
):
    run = _run(tmp_path)
    bindings = read_json(run.bindings_path)
    bindings["bindings"][0].update(
        {
            "backend_object_ids": ["shape-1a", "shape-1b"],
            "single_visible_object": True,
        }
    )
    write_json(run.bindings_path, bindings)

    blockers = evaluate_regions(run)["blockers"]
    assert "region-contract:reward-flow:visible-object-count:lower-box" in blockers


def test_required_relation_closes_against_powerpoint_arrow_readback(tmp_path: Path):
    run = _run(tmp_path)
    readback = read_json(run.powerpoint_arrow_readback_path)
    record = readback["records"][0]
    record["status"] = "FAIL"
    record["arrow_spec_sha256"] = "0" * 64
    record["shape_name"] = "wrong-shape"
    write_json(run.powerpoint_arrow_readback_path, readback)

    blockers = evaluate_regions(run)["blockers"]
    assert "region-contract:reward-flow:arrow-readback-status:lower-to-reward" in blockers
    assert (
        "region-contract:reward-flow:arrow-readback-spec-hash-mismatch:lower-to-reward"
        in blockers
    )
    assert (
        "region-contract:reward-flow:arrow-readback-shape-identity-mismatch:lower-to-reward"
        in blockers
    )


def test_required_relation_requires_powerpoint_arrow_readback_report(tmp_path: Path):
    run = _run(tmp_path)
    run.powerpoint_arrow_readback_path.unlink()

    blockers = evaluate_regions(run)["blockers"]
    assert (
        "region-contract:reward-flow:arrow-readback-report-missing:lower-to-reward"
        in blockers
    )


def test_exhaustive_relation_inventory_rejects_undeclared_scoped_edge(
    tmp_path: Path,
):
    run = _run(tmp_path)
    scene = read_json(run.scene_path)
    spec = _arrow_spec(run)
    scene["elements"].append({"id": "second-edge", "kind": "edge", "arrow_spec": spec})
    scene["edges"].append(
        {
            "id": "second-edge",
            "source": "lower-box",
            "target": "reward-circle",
            "arrow_spec": spec,
        }
    )
    write_json(run.scene_path, scene)
    bindings = read_json(run.bindings_path)
    bindings["bindings"].append(
        {
            "element_id": "second-edge",
            "shape_id": 4,
            "shape_name": "af-second-edge-connector-01",
            "readback_found": True,
            "single_visible_object": True,
            "arrow_spec_sha256": spec_sha256(spec),
        }
    )
    write_json(run.bindings_path, bindings)
    regions = read_json(run.regions_path)
    regions["regions"][0]["element_ids"].append("second-edge")
    write_json(run.regions_path, regions)

    blockers = evaluate_regions(run)["blockers"]
    assert "region-contract:reward-flow:exhaustive-relation-missing:second-edge" in blockers


def test_region_tasks_preserve_frozen_element_and_relation_scope(tmp_path: Path):
    run = _run(tmp_path)
    tasks = build_region_tasks(run)
    task = tasks["tasks"][0]
    assert task["element_ids"] == ["lower-box", "reward-circle", "lower-to-reward"]
    assert task["relations_exhaustive"] is True
    assert task["required_relations"] == [
        {
            "id": "lower-to-reward",
            "source_id": "lower-box",
            "target_id": "reward-circle",
            "direction": "forward",
            "start_head_type": "none",
            "end_head_type": "triangle",
            "representation": "line_arrow",
            "visible_object_count": 1,
        }
    ]
