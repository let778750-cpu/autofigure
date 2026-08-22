from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import read_json
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
    assert source["referenceEmbedded"] is False
    assert source["designatedReferenceSha256"] == run.load_meta()["source_sha256"]
    assert manifest["release_authority"] == "NONE"


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
