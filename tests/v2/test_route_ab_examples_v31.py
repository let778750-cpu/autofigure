from __future__ import annotations

from tools.v2 import common
from tools.v2.contracts import read_json


def test_real_modular_agent_route_ab_is_complete_but_not_misreported_mature() -> None:
    seeded = common.Run(common.CASES_ROOT / "svg-seeded" / "01-modular-agent")
    direct = common.Run(
        common.CASES_ROOT / "reference-only" / "01-modular-agent-reference-only"
    )
    seeded_meta = seeded.load_meta()
    direct_meta = direct.load_meta()
    assert seeded_meta["source_sha256"] == direct_meta["source_sha256"]
    assert seeded_meta["input_route"] == "svg-seeded"
    assert direct_meta["input_route"] == "reference-only"
    assert direct_meta["processing_mode"] == "png_reconstruct"
    assert direct_meta["workflow"]["state"] == "qa_failed"
    assert direct_meta["validation"]["status"] == "failed"

    provenance = read_json(direct.provenance_path)
    assert provenance["external_svg_seed"] is None
    assert provenance["comparison_group"] == "modular-agent-route-ab"
    assert all(item["role"] != "external-seed" for item in provenance["candidate_history"])
    assert provenance["construction_isolation"]["status"] == "enforced"

    bindings = read_json(direct.bindings_path)
    assert bindings["saved_reopened"] is True
    assert bindings["bindings_complete"] is True
    assert len(bindings["bindings"]) == 188
    assert sum(item["object_kind"] == "native-math" for item in bindings["bindings"]) == 22

    regions = read_json(direct.qa_dir / "regions-report.json")
    critical = [item for item in regions["regions"] if item["critical"]]
    assert len(critical) == 6
    assert sum(item["pass"] for item in critical) == 2
    assert {
        item["id"]
        for item in critical
        if item["pass"]
    } == {"observation-creative-asset", "environment-globe-creative-asset"}

    live = read_json(direct.qa_dir / "live-save-reopen-summary.json")
    assert live["saved_reopened"] is True
    assert live["reopened_artifact_sha256"] == live["candidate_sha256"]
    assert live["region_results"] == "not asserted"
    assert live["release_authority"] == "NONE"

    comparison = read_json(common.CASES_ROOT / "route-comparison-modular-agent-route-ab.json")
    assert comparison["conclusion"]["reference_only_pipeline_completed"] is True
    assert comparison["conclusion"]["reference_only_strict_passed"] is False
    assert comparison["conclusion"]["reference_only_capability_mature"] is False
