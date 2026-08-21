from __future__ import annotations

from pathlib import Path

from PIL import Image

from tools.v2 import common
from tools.v2.contracts import read_json, write_json
from tools.v2.regions import evaluate_regions


def _run(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference-source.png"
    Image.new("RGB", (30, 20), (240, 120, 80)).save(reference)
    run = common.create_run(reference, case="case", cases_root=tmp_path / "examples")
    run.qa_dir.mkdir(exist_ok=True)
    Image.open(run.source_png).save(run.render_png)
    regions = read_json(run.regions_path)
    regions["regions"] = [
        {
            "id": "critical",
            "bbox": [5, 5, 10, 10],
            "critical": True,
            "color_probes": [
                {"id": "center", "point": [10, 10], "radius": 1, "max_delta_e": 5}
            ],
        },
        {"id": "whole", "bbox": [0, 0, 30, 20], "critical": False},
    ]
    write_json(run.regions_path, regions)
    return run


def test_identical_critical_region_passes(tmp_path: Path):
    run = _run(tmp_path)
    report = evaluate_regions(run)
    assert report["strict_pass"] is True
    assert report["blockers"] == []
    assert report["regions"][0]["color_probes"][0]["delta_e00"] == 0.0


def test_local_failure_blocks_even_when_whole_canvas_is_diagnostic(tmp_path: Path):
    run = _run(tmp_path)
    render = Image.open(run.render_png)
    for y in range(5, 15):
        for x in range(5, 15):
            render.putpixel((x, y), (0, 0, 0))
    render.save(run.render_png)
    report = evaluate_regions(run)
    assert report["strict_pass"] is False
    assert report["blockers"] == ["region:critical"]
