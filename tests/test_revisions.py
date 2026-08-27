from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
from PIL import Image

from tools.core import common
from tools.core.contracts import read_json, record_candidate_provenance, write_json
from tools.core.revisions import (
    bind_canonical_svg,
    canonical_scene_payload,
    canonical_svg_text,
    compiler_fingerprint,
    lineage_blockers,
    materialize_svg,
    revision_id,
    scene_sha256,
    stamp_active_revision,
)

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60" '
    'viewBox="0 0 80 60"><rect id="panel" x="5" y="5" width="70" height="50"/></svg>'
)


def _run(tmp_path: Path) -> common.Run:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (80, 60), "white").save(reference)
    return common.create_run(
        reference,
        case="revision-case",
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )


def _bind_scene(run: common.Run) -> dict:
    scene = read_json(run.scene_path)
    bind_canonical_svg(scene, SVG, source_role="reference-reconstruction")
    write_json(run.scene_path, scene)
    materialize_svg(run, scene)
    return scene


def test_revision_identity_ignores_only_derived_projection_metadata():
    scene = {
        "schema_version": "4.0.0",
        "kind": "scene",
        "case": "same-case",
        "reference_sha256": "a" * 64,
        "canonical_source": "scene",
        "elements": [{"id": "node", "kind": "rect", "x": 1}],
        "edges": [],
    }
    bind_canonical_svg(scene, SVG, source_role="normalized-candidate")
    baseline_payload = canonical_scene_payload(scene)
    baseline_sha256 = scene_sha256(scene)
    baseline_revision = revision_id(scene)

    derived = copy.deepcopy(scene)
    derived["artifact"] = {"pptx_sha256": "b" * 64}
    derived["revision"] = {"revision_id": "old"}
    derived["updated_at"] = "later"
    derived["canonical_svg"]["materialized_path"] = "elsewhere/redraw.svg"
    assert canonical_scene_payload(derived) == baseline_payload
    assert scene_sha256(derived) == baseline_sha256
    assert revision_id(derived) == baseline_revision

    semantic_change = copy.deepcopy(scene)
    semantic_change["elements"][0]["x"] = 2
    assert scene_sha256(semantic_change) != baseline_sha256
    assert revision_id(semantic_change) != baseline_revision


def test_compiler_fingerprint_includes_asset_spec_semantics(monkeypatch):
    original = Path.read_bytes
    visited: list[str] = []

    def tracked(path: Path) -> bytes:
        visited.append(path.name)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", tracked)

    digest = compiler_fingerprint()

    assert len(digest) == 64
    assert "asset_spec.py" in visited


def test_both_input_routes_converge_on_the_same_canonical_scene_revision(
    tmp_path: Path,
):
    reference = tmp_path / "shared-reference.png"
    Image.new("RGB", (80, 60), "white").save(reference)
    revisions: list[tuple[str, str]] = []
    for route in ("reference-only", "svg-seeded"):
        run = common.create_run(
            reference,
            case="shared-case",
            cases_root=tmp_path / route,
            input_route=route,
        )
        if route == "svg-seeded":
            seed = tmp_path / "shared-seed.svg"
            seed.write_text(SVG, encoding="utf-8")
            shutil.copy2(seed, run.external_seed_svg)
            record_candidate_provenance(
                run,
                run.external_seed_svg,
                kind="svg",
                origin="test",
                role="external-seed",
                canonical_path="external-seed.svg",
            )
        scene = read_json(run.scene_path)
        bind_canonical_svg(scene, SVG, source_role="normalized-candidate")
        revisions.append((scene_sha256(scene), revision_id(scene)))

    assert revisions[0] == revisions[1]


def test_scene_owned_svg_is_hash_checked_and_materialized_over_flat_drift(tmp_path: Path):
    run = _run(tmp_path)
    scene = _bind_scene(run)
    assert canonical_svg_text(scene) == SVG
    assert run.redraw_svg.read_text(encoding="utf-8") == SVG

    run.redraw_svg.write_text("stale flat projection", encoding="utf-8")
    materialize_svg(run, scene)
    assert run.redraw_svg.read_text(encoding="utf-8") == SVG

    tampered = copy.deepcopy(scene)
    tampered["canonical_svg"]["content"] = SVG.replace("width=\"70\"", "width=\"69\"")
    with pytest.raises(ValueError, match="canonical_svg hash mismatch"):
        canonical_svg_text(tampered)
    with pytest.raises(ValueError, match="SVG source hash mismatch"):
        bind_canonical_svg(
            scene,
            SVG,
            source_role="reference-reconstruction",
            source_sha256="0" * 64,
        )


def test_stamp_closes_scene_artifact_binding_and_receipt_lineage(tmp_path: Path):
    run = _run(tmp_path)
    _bind_scene(run)
    run.pptx_path.write_bytes(b"pptx projection")
    Image.new("RGB", (80, 60), "white").save(run.render_png)

    record = stamp_active_revision(run)
    meta = run.load_meta()
    scene = read_json(run.scene_path)
    bindings = read_json(run.bindings_path)
    receipt = read_json(run.revision_receipt_path)

    assert meta["active_revision"] == record
    assert scene["revision"]["revision_id"] == record["revision_id"]
    assert scene["revision"]["scene_sha256"] == record["scene_sha256"]
    assert bindings["scene_revision"]["revision_id"] == record["revision_id"]
    assert receipt["schema_version"] == "4.0.0"
    assert receipt["revision_id"] == record["revision_id"]
    assert receipt["scene_sha256"] == scene_sha256(scene)
    assert receipt["artifacts"] == record["artifacts"]
    assert set(record["artifacts"]) == {
        "redraw_svg",
        "redraw_pptx",
        "render_png",
        "bindings",
    }
    assert lineage_blockers(run) == []


def test_lineage_detects_flat_projection_and_semantic_scene_drift(tmp_path: Path):
    run = _run(tmp_path)
    scene = _bind_scene(run)
    run.pptx_path.write_bytes(b"pptx projection")
    stamp_active_revision(run)

    run.redraw_svg.write_text(SVG.replace("width=\"70\"", "width=\"69\""), encoding="utf-8")
    blockers = lineage_blockers(run)
    assert "lineage:artifact-redraw-svg-mismatch" in blockers
    assert "lineage:redraw-svg-not-derived-from-scene" in blockers

    materialize_svg(run, scene)
    stamp_active_revision(run)
    scene = read_json(run.scene_path)
    scene["elements"].append({"id": "new-node", "kind": "rect"})
    write_json(run.scene_path, scene)
    blockers = lineage_blockers(run)
    assert "lineage:revision-id-mismatch" in blockers
    assert "lineage:scene-sha256-mismatch" in blockers
