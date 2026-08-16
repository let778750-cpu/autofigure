from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tests.test_canvas_and_preflight import base_spec, make_png
from tools.materialize_reference_preview import (
    ReferencePreviewError,
    materialize_reference_preview,
)
from tools.preflight_scene import preflight_scene


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def make_pattern_png(path: Path, width: int = 120, height: int = 90) -> Path:
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 12, 39, 31), fill=(18, 77, 133, 255))
    draw.ellipse((20, 16, 34, 29), fill=(240, 151, 92, 255))
    image.save(path, format="PNG")
    return path


def slot_element(
    receipt: Path,
    receipt_sha256: str,
    *,
    bbox: dict[str, int] | None = None,
    forbidden_content: list[str] | None = None,
) -> dict:
    slot_bbox = bbox or {"x": 20, "y": 20, "w": 100, "h": 80}
    return {
        "id": "asset.observation-preview",
        "type": "manual_asset_slot",
        "bbox": slot_bbox,
        "z_index": 0,
        "strategy": "manual_asset_slot",
        "slot_contract": {
            "slot_id": "asset.observation-preview",
            "mode": "reference_preview",
            "classification": "creative_raster",
            "required_content": "A photographic observation montage without baked labels",
            "forbidden_content": forbidden_content
            or [
                "text",
                "formula",
                "connector",
                "axis_or_legend",
                "panel_border",
                "quantitative_evidence",
            ],
            "bbox_source": slot_bbox,
            "aspect_ratio": slot_bbox["w"] / slot_bbox["h"],
            "fit_mode": "contain",
            "rotation_deg": 0,
            "replacement_object_name": "asset.observation-preview_REPLACE_ME",
            "native_capability_audit": {
                "backend": "powerpoint",
                "outcome": "SLOT_REQUIRED",
                "tested_families": [
                    "primitive_shapes",
                    "freeform_paths",
                    "builtin_icons",
                ],
                "reason_codes": ["PHOTOGRAPHIC_CONTINUOUS_TONE"],
                "estimated_native_shape_count": 80,
                "assessed_at_utc": "2026-08-16T00:00:00Z",
            },
            "preview": {
                "manifest_path": str(receipt),
                "manifest_sha256": receipt_sha256,
                "decomposition_mode": "ATOMIC",
                "decomposition_note": "One minimal photographic field; labels and arrows stay native.",
                "contains_reconstructable_text": False,
                "contains_formula": False,
                "contains_connector": False,
                "contains_axis_or_legend": False,
                "contains_panel_border": False,
                "contains_quantitative_evidence": False,
                "visible_disclosure_required": True,
                "disclosure_text": "REFERENCE PREVIEW — REPLACE ME",
                "qa_similarity_masked": True,
                "native_coverage_credit": False,
                "replace_before_approval": True,
            },
        },
    }


def test_materializer_preserves_exact_source_pixels_and_refuses_overwrite(tmp_path: Path) -> None:
    source = make_pattern_png(tmp_path / "source.png")
    asset = tmp_path / "slot.png"
    receipt = tmp_path / "slot.reference-preview.json"
    document = materialize_reference_preview(
        source,
        digest(source),
        (10, 12, 30, 20),
        asset,
        receipt,
        source_user_confirmed=True,
    )

    with Image.open(source) as original, Image.open(asset) as preview:
        assert preview.mode == original.mode
        assert preview.size == (30, 20)
        assert preview.tobytes() == original.crop((10, 12, 40, 32)).tobytes()
    assert document["status"] == "PREVIEW_ONLY_REPLACE_BEFORE_APPROVAL"
    assert document["policy"]["native_coverage_credit"] is False
    assert document["policy"]["qa_similarity_masked"] is True
    assert document["asset"]["sha256"] == digest(asset)

    with pytest.raises(ReferencePreviewError, match="overwrite"):
        materialize_reference_preview(
            source,
            digest(source),
            (10, 12, 30, 20),
            asset,
            receipt,
            source_user_confirmed=True,
        )


def test_materializer_rejects_unconfirmed_source_hash_mismatch_and_escape(tmp_path: Path) -> None:
    source = make_pattern_png(tmp_path / "source.png")
    with pytest.raises(ReferencePreviewError, match="user-confirmed"):
        materialize_reference_preview(
            source,
            digest(source),
            (10, 12, 30, 20),
            tmp_path / "a.png",
            tmp_path / "a.json",
            source_user_confirmed=False,
        )
    with pytest.raises(ReferencePreviewError, match="mismatch"):
        materialize_reference_preview(
            source,
            "0" * 64,
            (10, 12, 30, 20),
            tmp_path / "b.png",
            tmp_path / "b.json",
            source_user_confirmed=True,
        )
    with pytest.raises(ReferencePreviewError, match="escapes source canvas"):
        materialize_reference_preview(
            source,
            digest(source),
            (100, 70, 30, 30),
            tmp_path / "c.png",
            tmp_path / "c.json",
            source_user_confirmed=True,
        )


def test_preflight_accepts_bound_reference_preview_but_denies_native_credit(tmp_path: Path) -> None:
    source = make_png(tmp_path / "reference.png")
    asset = tmp_path / "observation.png"
    receipt = tmp_path / "observation.reference-preview.json"
    materialize_reference_preview(
        source,
        digest(source),
        (20, 20, 100, 80),
        asset,
        receipt,
        source_user_confirmed=True,
    )
    spec = base_spec(
        source,
        elements=[slot_element(receipt, digest(receipt))],
    )

    report = preflight_scene(spec, source_path=source, base_dir=tmp_path)

    assert report["status"] == "PASS"
    assert report["summary"]["reference_preview_slot_count"] == 1
    assert report["manual_asset_slots"] == [
        {
            "element_id": "asset.observation-preview",
            "slot_id": "asset.observation-preview",
            "mode": "reference_preview",
            "replacement_object_name": "asset.observation-preview_REPLACE_ME",
            "native_coverage_credit": False,
            "approval_blocking": True,
            "manifest_path": str(receipt.resolve()),
            "manifest_sha256": digest(receipt),
            "asset_path": str(asset.resolve()),
            "asset_sha256": digest(asset),
            "qa_similarity_masked": True,
            "visible_disclosure_required": True,
        }
    ]


def test_preflight_rejects_whole_reference_wrapper_and_incomplete_slot_policy(tmp_path: Path) -> None:
    source = make_png(tmp_path / "reference.png")
    with Image.open(source) as image:
        width, height = image.size
    asset = tmp_path / "whole.png"
    receipt = tmp_path / "whole.reference-preview.json"
    materialize_reference_preview(
        source,
        digest(source),
        (0, 0, width, height),
        asset,
        receipt,
        source_user_confirmed=True,
    )
    whole_bbox = {"x": 0, "y": 0, "w": width, "h": height}
    spec = base_spec(
        source,
        elements=[
            slot_element(
                receipt,
                digest(receipt),
                bbox=whole_bbox,
                forbidden_content=["text"],
            )
        ],
    )

    report = preflight_scene(spec, source_path=source, base_dir=tmp_path)
    codes = {finding["code"] for finding in report["findings"]}

    assert report["status"] == "SPEC_INVALID"
    assert "WHOLE_REFERENCE_PREVIEW_FORBIDDEN" in codes
    assert "SLOT_FORBIDDEN_CONTENT_POLICY_INCOMPLETE" in codes


def test_preflight_rejects_tampered_reference_preview_asset(tmp_path: Path) -> None:
    source = make_png(tmp_path / "reference.png")
    asset = tmp_path / "observation.png"
    receipt = tmp_path / "observation.reference-preview.json"
    materialize_reference_preview(
        source,
        digest(source),
        (20, 20, 100, 80),
        asset,
        receipt,
        source_user_confirmed=True,
    )
    spec = base_spec(source, elements=[slot_element(receipt, digest(receipt))])
    asset.write_bytes(asset.read_bytes() + b"tamper")

    report = preflight_scene(spec, source_path=source, base_dir=tmp_path)

    assert report["status"] == "SPEC_INVALID"
    assert "REFERENCE_PREVIEW_ASSET_HASH_MISMATCH" in {
        finding["code"] for finding in report["findings"]
    }


def test_preflight_rejects_reference_preview_outside_reconstruct_mode(tmp_path: Path) -> None:
    source = make_png(tmp_path / "reference.png")
    asset = tmp_path / "observation.png"
    receipt = tmp_path / "observation.reference-preview.json"
    materialize_reference_preview(
        source,
        digest(source),
        (20, 20, 100, 80),
        asset,
        receipt,
        source_user_confirmed=True,
    )
    spec = base_spec(source, elements=[slot_element(receipt, digest(receipt))])
    spec["mode"] = "publication_normalize"

    report = preflight_scene(spec, source_path=source, base_dir=tmp_path)

    assert report["status"] == "SPEC_INVALID"
    assert "REFERENCE_PREVIEW_MODE_FORBIDDEN" in {
        finding["code"] for finding in report["findings"]
    }
