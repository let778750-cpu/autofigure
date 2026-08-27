from __future__ import annotations

from tools.core import common
from tools.core.contracts import read_json


def test_real_modular_agent_route_ab_is_truthfully_qa_failed() -> None:
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
    # A fresh offline compile invalidates an older PowerPoint Live receipt.
    # The root bindings must therefore stay truthful until the new artifact is
    # opened, saved and reopened again; stale evidence is not completion.
    assert bindings["saved_reopened"] is False
    assert bindings["bindings_complete"] is True
    # The reference-only case preserves the three independently observed
    # rollout-state -> reward-circle arrows and compiles the interaction glyph
    # as one native bidirectional block arrow instead of two overlapping lines.
    assert len(bindings["bindings"]) > 0
    assert sum(item["object_kind"] == "native-math" for item in bindings["bindings"]) == 22

    regions = read_json(direct.qa_dir / "regions-report.json")
    critical = [item for item in regions["regions"] if item["critical"]]
    assert len(critical) == 10
    assert sum(item["pass"] for item in critical) == 2
    # environment-globe-creative-asset is compiled from the atomic-vector
    # (vtracer) representation and no longer meets its frozen raster-grade
    # 0.95 SSIM threshold; adopting the vector-grade floor for that region is
    # a separate per-asset re-freeze decision, so the region honestly fails.
    assert {
        item["id"]
        for item in critical
        if item["pass"]
    } == {
        "observation-creative-asset",
        "interaction-exchange-block-arrow",
    }

    live = read_json(direct.qa_dir / "live-save-reopen-summary.json")
    assert live["live_attempt_saved_reopened"] is True
    assert live["reopened_artifact_sha256"] == live["live_candidate_sha256"]
    assert live["current_root_candidate_sha256"] != common.sha256_file(direct.pptx_path)

    blockers = read_json(direct.blockers_path)["blockers"]
    assert "bindings:save-reopen-not-verified" in blockers

    comparison = read_json(common.CASES_ROOT / "route-comparison-modular-agent-route-ab.json")
    assert comparison["conclusion"]["reference_only_pipeline_completed"] is True
    assert comparison["conclusion"]["reference_only_strict_passed"] is False
    assert comparison["conclusion"]["reference_only_capability_mature"] is False
