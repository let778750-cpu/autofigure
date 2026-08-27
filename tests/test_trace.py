"""autofigure trace 子命令的 case-neutral 测试(合成参考图与授权条目)。"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools import common
from tools.assets.asset_spec import (
    asset_contract_sha256,
    audit_atomic_vector_assets,
    validate_atomic_vector_asset,
)
from tools.assets.asset_trace import check_svg_contract_subset
from tools.core.contracts import read_json, write_json
from tools.assets.trace import main as trace_main

BBOX_BY_ID = {
    "atomic:flat-icon": (0, 0, 32, 64),
    "atomic:photo": (32, 0, 32, 64),
    "atomic:smooth": (64, 0, 32, 64),
}


def _gradient_stack(width: int, height: int = 64) -> np.ndarray:
    ramp = np.linspace(0, 255, width)
    return np.stack(
        [
            np.broadcast_to(ramp, (height, width)),
            np.broadcast_to(np.linspace(120, 220, height)[:, None], (height, width)),
            np.broadcast_to(255 - ramp, (height, width)),
        ],
        axis=-1,
    )


def _reference_image() -> np.ndarray:
    image = np.full((64, 96, 3), 245, np.uint8)
    # 平面插画条带:低色数且有硬边色块。
    image[6:16, 4:14] = (200, 60, 50)
    image[6:16, 18:28] = (60, 130, 200)
    image[40:50, 4:14] = (240, 190, 60)
    image[40:50, 18:28] = (90, 170, 90)
    # 照片条带:连续调渐变加噪声。
    rng = np.random.default_rng(0)
    noisy = _gradient_stack(32).astype(np.float32) + rng.normal(0, 18, (64, 32, 3))
    image[:, 32:64] = np.clip(noisy, 0, 255).astype(np.uint8)
    # 平滑渐变条带:低色数但没有硬边。
    image[:, 64:96] = np.clip(_gradient_stack(32), 0, 255).astype(np.uint8)
    return image


def _crop_sha256(reference: Path, bbox: tuple[int, int, int, int]) -> str:
    x, y, width, height = bbox
    with Image.open(reference) as image:
        crop = image.crop((x, y, x + width, y + height))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _raster_entry(atomic_id: str, bbox: tuple[int, int, int, int], reference: Path) -> dict:
    return {
        "id": atomic_id,
        "authorized": True,
        "authorization_basis": (
            "User explicitly authorized tight crops from this case's own reference PNG."
        ),
        "rights_status": (
            "unknown; authorization records workflow permission, not copyright clearance"
        ),
        "editable": False,
        "raster_reason": "synthetic case-neutral test asset",
        "decomposition_note": "synthetic case-neutral test asset",
        "source": "reference_crop",
        "source_sha256": _crop_sha256(reference, bbox),
        "bbox": list(bbox),
        "source_tightly_cropped": True,
        "atomic_raster_unit": True,
        "contains_reconstructable_content": False,
    }


def _make_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> common.Run:
    monkeypatch.setattr(common, "PROJECT_ROOT", tmp_path / "project-root")
    reference = tmp_path / "trace-reference.png"
    Image.fromarray(_reference_image(), "RGB").save(reference)
    run = common.create_run(
        reference,
        case="trace-case",
        cases_root=tmp_path / "examples",
        input_route="reference-only",
    )
    assets = read_json(run.assets_path)
    assets["assets"] = [
        _raster_entry(atomic_id, bbox, run.source_png)
        for atomic_id, bbox in BBOX_BY_ID.items()
    ]
    write_json(run.assets_path, assets)
    regions = read_json(run.regions_path)
    regions["regions"].extend(
        {
            "id": f"region-{atomic_id.split(':', 1)[1]}",
            "label": f"synthetic region for {atomic_id}",
            "bbox": list(bbox),
            "critical": True,
            "asset_id": atomic_id,
            "element_ids": [atomic_id],
        }
        for atomic_id, bbox in BBOX_BY_ID.items()
    )
    write_json(run.regions_path, regions)
    return run


def _trace(run: common.Run, *extra: str) -> int:
    return trace_main([str(run.root), *extra])


def _vector_entries(assets: dict) -> list[dict]:
    return [
        entry
        for entry in assets.get("assets", [])
        if isinstance(entry, dict) and entry.get("source") == "vtracer-trace"
    ]


def test_trace_flat_illustration_appends_atomic_vector_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("vtracer")
    run = _make_case(tmp_path, monkeypatch)
    before = read_json(run.assets_path)

    assert _trace(run, "--asset", "flat-icon") == 0

    after = read_json(run.assets_path)
    assert after["policy"] == before["policy"]
    assert after["microasset_opportunity_map"] == before["microasset_opportunity_map"]
    assert asset_contract_sha256(after) == asset_contract_sha256(before)
    raster_ids = {
        entry.get("id")
        for entry in after["assets"]
        if isinstance(entry, dict) and entry.get("source") == "reference_crop"
    }
    assert raster_ids == set(BBOX_BY_ID)

    vectors = _vector_entries(after)
    assert [entry["id"] for entry in vectors] == ["atomic:flat-icon-vector"]
    entry = vectors[0]
    assert validate_atomic_vector_asset(entry) == []
    assert audit_atomic_vector_assets(after) == []
    assert entry["editable"] is True
    assert entry["fallback_atomic_raster"] == "atomic:flat-icon"
    assert entry["ink_contract_region_id"] == "region-flat-icon"
    assert entry["trace_eligibility"] == "flat-illustration"
    assert entry["authorization_basis"] == before["assets"][0]["authorization_basis"]
    assert entry["rights_status"] == before["assets"][0]["rights_status"]
    assert entry["trace_method"] == "vtracer-color-stacked-spline"
    assert entry["trace_engine_version"]

    assert entry["vector_source_svg"]["path"] == "assets/flat-icon-vector.svg"
    svg_path = run.root / "assets" / "flat-icon-vector.svg"
    assert svg_path.is_file()
    assert (
        hashlib.sha256(svg_path.read_bytes()).hexdigest()
        == entry["vector_source_svg"]["sha256"]
    )
    assert check_svg_contract_subset(svg_path) == []
    assert (run.root / "assets" / "flat-icon.png").is_file()

    provenance = read_json(run.provenance_path)
    history = provenance["asset_trace_history"]
    assert len(history) == 1
    record = history[0]
    assert record["asset_id"] == "atomic:flat-icon-vector"
    assert record["fallback_atomic_raster"] == "atomic:flat-icon"
    assert record["origin"] == "vtracer-provider"
    assert record["sha256"] == entry["vector_source_svg"]["sha256"]
    assert record["trace_engine_version"] == entry["trace_engine_version"]
    assert record["trace_method"] == entry["trace_method"]
    assert record["parameters"]["colormode"] == "color"
    assert record["parameters"]["hierarchical"] == "stacked"
    assert record["parameters"]["color_precision"] == 6
    assert record["parameters"]["path_precision"] == 3
    assert record["trace_eligibility"] == "flat-illustration"
    assert record["trace_eligibility_statistics"]["unique_colors_4bit"] > 0
    assert record["traced_at"]
    assert record["input_crop"]["path"] == "assets/flat-icon.png"
    assert record["input_crop"]["bbox"] == [0, 0, 32, 64]
    assert record["input_crop"]["sha256"] == hashlib.sha256(
        (run.root / "assets" / "flat-icon.png").read_bytes()
    ).hexdigest()
    assert any(
        event.get("event") == "asset-traced"
        and event.get("candidate_sha256") == record["sha256"]
        and event.get("origin") == "vtracer-provider"
        for event in provenance["events"]
    )


def test_trace_accepts_full_atomic_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("vtracer")
    run = _make_case(tmp_path, monkeypatch)
    assert _trace(run, "--asset", "atomic:flat-icon") == 0
    vectors = _vector_entries(read_json(run.assets_path))
    assert [entry["id"] for entry in vectors] == ["atomic:flat-icon-vector"]


def test_trace_photographic_stays_on_raster_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = _make_case(tmp_path, monkeypatch)
    before = run.assets_path.read_bytes()
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "photo")
    message = str(excinfo.value)
    assert "photographic" in message
    assert "atomic-raster" in message
    assert run.assets_path.read_bytes() == before
    assert not (run.root / "assets").exists()


def test_trace_ambiguous_requires_explicit_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("vtracer")
    run = _make_case(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "smooth")
    assert "ambiguous" in str(excinfo.value)
    assert "--allow-ambiguous" in str(excinfo.value)
    assert not (run.root / "assets").exists()

    assert _trace(run, "--asset", "smooth", "--allow-ambiguous") == 0
    vectors = _vector_entries(read_json(run.assets_path))
    assert [entry["trace_eligibility"] for entry in vectors] == ["ambiguous"]


def test_trace_missing_asset_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = _make_case(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "nonexistent")
    assert "不存在" in str(excinfo.value)


def test_trace_unauthorized_entry_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = _make_case(tmp_path, monkeypatch)
    assets = read_json(run.assets_path)
    for entry in assets["assets"]:
        if entry["id"] == "atomic:flat-icon":
            entry["authorized"] = False
    write_json(run.assets_path, assets)
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "flat-icon")
    assert "授权" in str(excinfo.value)


def test_trace_crop_hash_drift_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = _make_case(tmp_path, monkeypatch)
    assets = read_json(run.assets_path)
    for entry in assets["assets"]:
        if entry["id"] == "atomic:flat-icon":
            entry["source_sha256"] = "0" * 64
    write_json(run.assets_path, assets)
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "flat-icon")
    assert "不一致" in str(excinfo.value)


def test_trace_requires_ink_contract_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = _make_case(tmp_path, monkeypatch)
    regions = read_json(run.regions_path)
    regions["regions"] = [
        region
        for region in regions["regions"]
        if region.get("asset_id") != "atomic:flat-icon"
    ]
    write_json(run.regions_path, regions)
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "flat-icon")
    assert "ink_contract_region_id" in str(excinfo.value)


def test_trace_rejects_vector_entry_as_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("vtracer")
    run = _make_case(tmp_path, monkeypatch)
    assert _trace(run, "--asset", "flat-icon") == 0
    with pytest.raises(SystemExit) as excinfo:
        _trace(run, "--asset", "flat-icon-vector")
    assert "reference_crop" in str(excinfo.value)


def test_trace_is_byte_stable_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("vtracer")
    run = _make_case(tmp_path, monkeypatch)
    assert _trace(run, "--asset", "flat-icon") == 0
    svg_path = run.root / "assets" / "flat-icon-vector.svg"
    first_bytes = svg_path.read_bytes()
    assert _trace(run, "--asset", "flat-icon") == 0
    assert svg_path.read_bytes() == first_bytes
    vectors = _vector_entries(read_json(run.assets_path))
    assert len(vectors) == 1
    provenance = read_json(run.provenance_path)
    assert len(provenance["asset_trace_history"]) == 1
    assert (
        sum(event.get("event") == "asset-traced" for event in provenance["events"]) == 1
    )
