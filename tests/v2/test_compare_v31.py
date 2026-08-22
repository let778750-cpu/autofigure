from __future__ import annotations

from pathlib import Path

from PIL import Image

from tools.v2 import common
from tools.v2.compare import build_comparison, main as compare_main
from tools.v2.contracts import read_json, write_json


def _case(tmp_path: Path, route: str, case: str) -> common.Run:
    reference = tmp_path / "reference.png"
    if not reference.is_file():
        Image.new("RGB", (80, 50), "white").save(reference)
    run = common.create_run(
        reference,
        case=case,
        cases_root=tmp_path / "examples",
        input_route=route,
    )
    provenance = read_json(run.provenance_path)
    provenance["comparison_group"] = "controlled-ab"
    write_json(run.provenance_path, provenance)
    bindings = read_json(run.bindings_path)
    bindings.update({"saved_reopened": True, "bindings_complete": True})
    bindings["bindings"] = [
        {"object_kind": "text"},
        {"object_kind": "native-math"},
        {"object_kind": "connector"},
    ]
    write_json(run.bindings_path, bindings)
    write_json(
        run.qa_dir / "regions-report.json",
        {
            "strict_pass": False,
            "regions": [
                {
                    "id": "critical",
                    "critical": True,
                    "ssim": 0.7,
                    "edge_iou": 0.8,
                    "mean_abs_rgb_delta": 10,
                    "pass": False,
                }
            ],
        },
    )
    write_json(run.qa_dir / "arrows-audit.json", {"arrows": 1, "findings": []})
    write_json(run.layout_audit_path, {"pass": True, "findings": []})
    return run


def test_comparison_is_hash_and_group_bound(tmp_path: Path, monkeypatch) -> None:
    seeded = _case(tmp_path, "svg-seeded", "seeded")
    direct = _case(tmp_path, "reference-only", "direct")
    monkeypatch.setattr(common, "CASES_ROOT", tmp_path / "examples")
    report = build_comparison(seeded, direct)
    assert report["comparison_group"] == "controlled-ab"
    assert report["conclusion"]["reference_only_pipeline_completed"] is True
    assert report["conclusion"]["reference_only_capability_mature"] is False


def test_compare_cli_writes_portable_json_and_markdown(tmp_path: Path, monkeypatch) -> None:
    seeded = _case(tmp_path, "svg-seeded", "seeded")
    direct = _case(tmp_path, "reference-only", "direct")
    monkeypatch.setattr(common, "CASES_ROOT", tmp_path / "examples")
    output = tmp_path / "reports"
    assert compare_main([str(seeded.root), str(direct.root), "--output-root", str(output)]) == 0
    payload = read_json(output / "route-comparison-controlled-ab.json")
    assert payload["cases"]["reference-only"]["path"] == "reference-only/direct"
    assert (output / "route-comparison-controlled-ab.md").is_file()
