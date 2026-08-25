from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import read_json, write_json
from tools.convert import convert
from tools.live_bridge import build_powerpoint_live_bridge
from tools.math import upgrade


XSL_PATH = Path(r"C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL")


def _math_engine_ready() -> bool:
    try:
        import latex2mathml  # noqa: F401
    except ImportError:
        return False
    return XSL_PATH.is_file()


def test_live_bridge_routes_every_v3_element_and_preserves_reference_policy(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (120, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="bridge",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="100" viewBox="0 0 120 100">'
        '<rect id="source" x="2" y="20" width="20" height="20"/>'
        '<rect id="target" x="98" y="20" width="20" height="20"/>'
        '<line id="edge" data-source-id="source" data-target-id="target" '
        'x1="22" y1="30" x2="98" y2="30" stroke="#111111"/>'
        '</svg>',
        encoding="utf-8",
    )
    convert(run)

    manifest = build_powerpoint_live_bridge(run)
    live_root = run.root / manifest["case_root"]
    scene = read_json(live_root / "design" / "scene_graph.json")
    plan = read_json(live_root / "design" / "render_plan.json")
    source = read_json(live_root / "input" / "source_manifest.json")
    state = read_json(live_root / "project_state.json")

    v3_ids = {item["id"] for item in read_json(run.scene_path)["elements"]}
    bridge_ids = {item["id"] for item in scene["nodes"] + scene["edges"]}
    routed_ids = {item["elementId"] for item in plan["elements"]}
    assert bridge_ids == routed_ids == v3_ids
    assert scene["schemaVersion"] == "2.1.0"
    assert scene["canvas"]["profileId"] == "journal-double-column"
    assert state["profileId"] == scene["canvas"]["profileId"]
    assert state["state"] == "PLANNED"
    assert state["gates"]["scientificApproval"] == "PENDING"
    assert state["gates"]["humanApproval"] == "PENDING"
    assert source["referenceEmbedded"] is False
    assert source["designatedReferenceSha256"] == run.load_meta()["source_sha256"]
    assert manifest["release_authority"] == "NONE"


def test_live_bridge_preserves_native_text_rotation(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (120, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="rotated-bridge",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="100" '
        'viewBox="0 0 120 100"><rect id="slot" x="40" y="10" width="40" '
        'height="80"/><text id="rotated" x="60" y="50" text-anchor="middle" '
        'transform="rotate(90 60 50)" data-text-flow="rotated-word" '
        'data-text-container="slot">Sem</text></svg>',
        encoding="utf-8",
    )
    convert(run)
    manifest = build_powerpoint_live_bridge(run)
    scene = read_json(run.root / manifest["case_root"] / "design" / "scene_graph.json")
    rotated = next(item for item in scene["nodes"] if item["id"] == "rotated")
    assert rotated["rotation"] == 90.0


def test_live_bridge_routes_one_bidirectional_block_arrow_without_inventing_a_line(
    tmp_path: Path,
):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (200, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="block-arrow-bridge",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" '
        'viewBox="0 0 200 100"><rect id="source" x="0" y="30" width="20" '
        'height="40"/><rect id="target" x="180" y="30" width="20" height="40"/>'
        '<polygon id="exchange" data-arrow-representation="block_arrow" '
        'data-arrow-body-width="9" data-start-head-type="triangle" '
        'data-end-head-type="triangle" data-source-id="source" data-target-id="target" '
        'data-arrow-centerline="M30 50 L170 50" '
        'points="30,50 38,41.5 38,45.5 162,45.5 162,41.5 170,50 '
        '162,58.5 162,54.5 38,54.5 38,58.5" fill="#767171" '
        'stroke="#767171" stroke-width="1"/></svg>',
        encoding="utf-8",
    )
    convert(run)

    manifest = build_powerpoint_live_bridge(run)
    scene = read_json(run.root / manifest["case_root"] / "design" / "scene_graph.json")
    exchange = next(item for item in scene["edges"] if item["id"] == "exchange")

    assert exchange["direction"] == "bidirectional"
    assert exchange["route"] == "straight"
    assert "pathSpec" not in exchange
    assert sum(item["id"] == "exchange" for item in scene["edges"]) == 1


def test_live_bridge_rejects_split_shape_id_and_name_identity(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (120, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="split-binding-identity",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="100" '
        'viewBox="0 0 120 100"><rect id="left" x="5" y="10" width="20" '
        'height="20"/><rect id="right" x="85" y="60" width="30" height="30"/>'
        "</svg>",
        encoding="utf-8",
    )
    convert(run)

    bindings = read_json(run.bindings_path)
    left = next(item for item in bindings["bindings"] if item["element_id"] == "left")
    right = next(item for item in bindings["bindings"] if item["element_id"] == "right")
    assert (left["shape_id"], left["shape_name"]) != (
        right["shape_id"],
        right["shape_name"],
    )
    left["shape_name"] = right["shape_name"]
    write_json(run.bindings_path, bindings)

    with pytest.raises(SystemExit, match="cannot read exact bound shape identity"):
        build_powerpoint_live_bridge(run)
    assert not (run.live_case_dir / "design" / "scene_graph.json").exists()


@pytest.mark.skipif(not _math_engine_ready(), reason="requires native Office Math engine")
def test_live_bridge_reads_native_math_hidden_in_alternate_content(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (160, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="math-bridge",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        'viewBox="0 0 160 100"><text id="formula" x="20" y="55" '
        'font-size="18" font-style="italic"><tspan>z</tspan>'
        '<tspan baseline-shift="super" font-size="11">τ</tspan></text></svg>',
        encoding="utf-8",
    )
    convert(run)
    assert upgrade(run)["injected"] == 1

    manifest = build_powerpoint_live_bridge(run)
    scene = read_json(run.root / manifest["case_root"] / "design" / "scene_graph.json")
    formula = next(item for item in scene["nodes"] if item["id"] == "formula")
    assert formula["kind"] == "text"
    assert formula["textSpec"]["text"] == "zτ"
