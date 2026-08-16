from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import cv2
import jsonschema
import numpy as np
import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import geometry_refinement as geometry  # noqa: E402
from tests.test_paddle_ocr_manifest import make_host_runtime_receipt  # noqa: E402
from tests.test_perception_review import build_raw_manifest  # noqa: E402


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _observation(template: dict, *, observation_id: str, text: str, box: dict) -> dict:
    value = copy.deepcopy(template)
    value.update(
        {
            "observation_id": observation_id,
            "view_id": "full",
            "text": text,
            "ocr_confidence": 0.99,
            "bbox_source": dict(box),
            "polygon_source": [
                [box["x"], box["y"]],
                [box["x"] + box["w"], box["y"]],
                [box["x"] + box["w"], box["y"] + box["h"]],
                [box["x"], box["y"] + box["h"]],
            ],
            "textline_orientation_degrees": 0,
            "input_rotation_degrees": 0,
        }
    )
    return value


def _candidate(
    template: dict,
    *,
    candidate_id: str,
    observation_id: str,
    text: str,
    box: tuple[int, int, int, int],
    flags: list[str] | None = None,
    conflict: bool = False,
) -> dict:
    x0, y0, x1, y1 = box
    ocr_box = {"x": float(x0), "y": float(y0), "w": float(x1 - x0), "h": float(y1 - y0)}
    value = copy.deepcopy(template)
    first_template = value["observations"][0]
    first = _observation(
        first_template,
        observation_id=observation_id,
        text=text,
        box=ocr_box,
    )
    second_box = {
        "x": float(x0) + 0.5,
        "y": float(y0),
        "w": float(x1 - x0),
        "h": float(y1 - y0) + 0.5,
    }
    second = _observation(
        first_template,
        observation_id=f"{observation_id}-repeat",
        text=text,
        box=second_box,
    )
    polygon = [
        [float(x0), float(y0)],
        [float(x1), float(y0)],
        [float(x1), float(y1)],
        [float(x0), float(y1)],
    ]
    value.update(
        {
            "candidate_id": candidate_id,
            "text": text,
            "normalized_text": geometry._normalize_ocr_text(text),
            "ocr_confidence": 0.99,
            "confidence_band": "OCR_CONFLICT" if conflict else "OCR_HIGH",
            "bbox_source": ocr_box,
            # Deliberately huge: Phase 1 must never use the multi-view envelope
            # as its extraction ROI.
            "bbox_envelope_source": {"x": 0.0, "y": 0.0, "w": 180.0, "h": 100.0},
            "polygon_source": polygon,
            "primary_observation_id": observation_id,
            "source_views": ["full", "tile_r0_c0"],
            "agreement_count": 2,
            "observations": [first, second],
            "alternatives": [second] if conflict else [],
            "review_flags": [*(flags or []), *(["OCR_CONFLICT"] if conflict else [])],
            "verification": {
                "status": "CONFLICT" if conflict else "UNVERIFIED",
                "user_confirmed_text": None,
            },
        }
    )
    return value


def make_gold_case(root: Path) -> tuple[Path, Path, Path]:
    source = root / "gold.png"
    rgb = np.full((100, 180, 3), 254, dtype=np.uint8)
    cv2.rectangle(rgb, (5, 5), (174, 94), (30, 30, 30), 2)

    # Two horizontal, three-component text runs with the same ink-bottom line.
    for x in (24, 32, 40):
        rgb[25:43, x : x + 4] = 20
    for x in (84, 92, 100):
        rgb[25:43, x : x + 4] = 20
    # Formula-like diagnostic ink.
    rgb[58:72, 24:29] = 20
    rgb[58:72, 34:39] = 20
    # Vertical diagnostic ink.
    rgb[48:54, 152:162] = 20
    rgb[60:66, 152:162] = 20
    rgb[72:78, 152:162] = 20
    # Conflict and multiline candidates retain diagnostic masks only.
    for x in (115, 123, 131):
        rgb[25:40, x : x + 4] = 20
        rgb[54:62, x : x + 4] = 20
        rgb[68:76, x : x + 4] = 20
    # A contaminating line-only OCR ROI.
    rgb[84:86, 62:108] = 20
    Image.fromarray(rgb).save(source)

    manifest = build_raw_manifest()
    source_hash = file_hash(source)
    manifest["run_id"] = "geometry-gold-001"
    manifest["created_at_utc"] = "2026-08-15T00:00:00Z"
    manifest["source"].update(
        {
            "path": str(source.resolve()),
            "sha256": source_hash,
            "size_bytes": source.stat().st_size,
            "width_px": 180,
            "height_px": 100,
            "pixel_mode": "RGB",
            "format": "PNG",
        }
    )
    manifest["configuration"]["manifest_schema_sha256"] = file_hash(
        PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
    )
    template = manifest["text_candidates"][0]
    manifest["text_candidates"] = [
        _candidate(
            template,
            candidate_id="T0001",
            observation_id="O00001",
            text="ABC",
            box=(20, 20, 50, 47),
        ),
        _candidate(
            template,
            candidate_id="T0002",
            observation_id="O00002",
            text="DEF",
            box=(80, 20, 110, 47),
        ),
        _candidate(
            template,
            candidate_id="T0003",
            observation_id="O00003",
            text="x=y",
            box=(20, 54, 44, 76),
            flags=["FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"],
        ),
        _candidate(
            template,
            candidate_id="T0004",
            observation_id="O00004",
            text="VERT",
            box=(148, 44, 167, 82),
        ),
        _candidate(
            template,
            candidate_id="T0005",
            observation_id="O00005",
            text="LINE",
            box=(60, 81, 110, 89),
        ),
        _candidate(
            template,
            candidate_id="T0006",
            observation_id="O00006",
            text="BLANK",
            box=(112, 84, 150, 91),
        ),
        _candidate(
            template,
            candidate_id="T0007",
            observation_id="O00007",
            text="CONFLICT",
            box=(112, 20, 148, 44),
            conflict=True,
        ),
        _candidate(
            template,
            candidate_id="T0008",
            observation_id="O00008",
            text="TOP\nBOT",
            box=(112, 50, 148, 80),
        ),
    ]
    manifest["summary"]["candidate_count"] = len(manifest["text_candidates"])
    ocr_path = root / "perception-manifest.json"
    ocr_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    receipt = make_host_runtime_receipt(manifest["run_id"], source_hash)
    receipt_path = root / "host-runtime-receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return source, ocr_path, receipt_path


def _run_gold(root: Path, output_name: str = "geometry") -> tuple[dict, Path]:
    source, ocr_path, receipt_path = make_gold_case(root)
    output = root / output_name
    manifest_path, manifest = geometry.run_geometry_refinement(
        source_path=source,
        ocr_manifest_path=ocr_path,
        host_runtime_receipt_path=receipt_path,
        output_dir=output,
        project_root=PROJECT_ROOT,
        require_isolated_runtime=True,
    )
    return manifest, manifest_path


def _append_candidate(
    ocr_path: Path,
    *,
    candidate_id: str,
    observation_id: str,
    text: str,
    box: tuple[int, int, int, int],
) -> None:
    manifest = json.loads(ocr_path.read_text("utf-8"))
    manifest["text_candidates"].append(
        _candidate(
            manifest["text_candidates"][0],
            candidate_id=candidate_id,
            observation_id=observation_id,
            text=text,
            box=box,
        )
    )
    manifest["summary"]["candidate_count"] = len(manifest["text_candidates"])
    ocr_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _assert_overlap_is_diagnostic_only(manifest: dict, manifest_path: Path) -> None:
    by_id = {item["candidate_id"]: item for item in manifest["text_geometry"]}
    for candidate_id in ("T0001", "T0009"):
        item = by_id[candidate_id]
        assert item["status"] == "INCONCLUSIVE"
        assert item["ink_bbox"] is None
        assert item["ink_area_px"] == 0
        assert item["mask_label"] is None
        assert item["edge_uncertainty_px"] is None
        assert "OVERLAPPING_CANDIDATE_MASKS" in item["quality_flags"]
        assert "CANDIDATE_MASK_OWNERSHIP_AMBIGUOUS" in item["reasons"]
        assert item["baseline"]["status"] == "INCONCLUSIVE"
        assert item["baseline"]["y_source_px"] is None
        assert item["baseline"]["endpoints_source"] is None
    assert not any(
        pair["candidate_a_id"] in {"T0001", "T0009"} or pair["candidate_b_id"] in {"T0001", "T0009"}
        for pair in manifest["neighbor_pairs"]
    )
    atlas = np.asarray(Image.open(manifest_path.parent / "geometry-label-atlas.png"))
    ambiguity = np.asarray(Image.open(manifest_path.parent / "geometry-ambiguity-mask.png"))
    assert 1 not in np.unique(atlas)
    assert 9 not in np.unique(atlas)
    assert np.any(ambiguity[20:47, 20:68] == 255)


def test_gold_ink_baseline_gap_frame_and_atlases(tmp_path: Path) -> None:
    manifest, manifest_path = _run_gold(tmp_path)
    assert manifest["status"] == "GEOMETRY_OBSERVATIONS_READY"
    by_id = {item["candidate_id"]: item for item in manifest["text_geometry"]}
    assert by_id["T0001"]["detector_bbox"] == {"x0": 20, "y0": 20, "x1": 50, "y1": 47}
    assert by_id["T0001"]["ink_bbox"] == {"x0": 24, "y0": 25, "x1": 44, "y1": 43}
    assert by_id["T0001"]["baseline"]["status"] == "MEASURED"
    assert by_id["T0001"]["baseline"]["meaning"] == "INK_BOTTOM_ALIGNMENT_ONLY"
    assert abs(by_id["T0001"]["baseline"]["y_source_px"] - 43.0) <= 1.0
    assert by_id["T0001"]["baseline"]["support_component_count"] == 3
    assert by_id["T0001"]["baseline"]["inlier_fraction"] == 1.0
    assert by_id["T0001"]["detector_repeatability_px"]["supporting_observation_count"] == 2
    assert by_id["T0001"]["detector_repeatability_px"]["left"] == 0.5

    pairs = [
        item
        for item in manifest["neighbor_pairs"]
        if {item["candidate_a_id"], item["candidate_b_id"]} == {"T0001", "T0002"}
    ]
    assert len(pairs) == 1
    assert pairs[0]["relationship"] == "LOCAL_PAIR_UNVERIFIED_CONTAINER"
    assert pairs[0]["signed_horizontal_gap_px"] == 40.0
    assert pairs[0]["minimum_ink_distance_px"] == 41.0
    assert pairs[0]["uncertainty_px"] >= 2.0

    assert any(
        item["status"] == "MEASURED"
        and item["semantic_role"] == "UNVERIFIED"
        and item["closed_contour_evidence"]
        and item["bbox_source"]["x0"] <= 6
        and item["bbox_source"]["x1"] >= 174
        for item in manifest["frame_candidates"]
    )
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "geometry-manifest.schema.json").read_text("utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(manifest)

    atlas = np.asarray(Image.open(manifest_path.parent / "geometry-label-atlas.png"))
    ambiguity = np.asarray(Image.open(manifest_path.parent / "geometry-ambiguity-mask.png"))
    assert atlas.shape == (100, 180)
    assert ambiguity.shape == (100, 180)
    assert set(np.unique(atlas)).issuperset({0, 1, 2})
    for candidate_id in ("T0001", "T0002"):
        item = by_id[candidate_id]
        label_mask = atlas == item["mask_label"]
        ys, xs = np.nonzero(label_mask)
        assert int(label_mask.sum()) == item["ink_area_px"]
        assert {
            "x0": int(xs.min()),
            "y0": int(ys.min()),
            "x1": int(xs.max()) + 1,
            "y1": int(ys.max()) + 1,
        } == item["ink_bbox"]
    for artifact in manifest["artifacts"].values():
        path = manifest_path.parent / artifact["relative_path"]
        assert artifact["sha256"] == file_hash(path)
        assert artifact["size_bytes"] == path.stat().st_size


def test_formula_vertical_pollution_and_blank_are_fail_closed(tmp_path: Path) -> None:
    manifest, _manifest_path = _run_gold(tmp_path)
    by_id = {item["candidate_id"]: item for item in manifest["text_geometry"]}
    for candidate_id, expected_flag in (
        ("T0003", "FORMULA_LIKE"),
        ("T0004", "VERTICAL_ORIENTATION"),
        ("T0005", "FRAME_OR_LINE_CONTAMINATION_REMOVED"),
        ("T0007", "OCR_CONFLICT"),
        ("T0008", "MULTILINE_INK_LAYOUT"),
    ):
        item = by_id[candidate_id]
        assert item["status"] == "INCONCLUSIVE"
        assert item["ink_bbox"] is None
        assert item["mask_label"] is None
        assert item["baseline"]["status"] == "INCONCLUSIVE"
        assert expected_flag in item["quality_flags"]
        if candidate_id != "T0005":
            assert any(evidence["status"] == "MEASURED" for evidence in item["method_evidence"])
    assert by_id["T0006"]["status"] == "INCONCLUSIVE"
    assert "NO_STABLE_FOREGROUND_CONSENSUS" in by_id["T0006"]["reasons"]
    atlas = np.asarray(Image.open(_manifest_path.parent / "geometry-label-atlas.png"))
    ambiguity = np.asarray(Image.open(_manifest_path.parent / "geometry-ambiguity-mask.png"))
    for candidate_id in ("T0007", "T0008"):
        detector = by_id[candidate_id]["detector_bbox"]
        region = np.s_[detector["y0"] : detector["y1"], detector["x0"] : detector["x1"]]
        assert not np.any(atlas[region] != 0)
        assert np.any(ambiguity[region] == 255)


def test_frame_suppression_uses_ink_not_whole_ocr_detector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, ocr_path, receipt_path = make_gold_case(tmp_path)
    captured: dict[str, np.ndarray] = {}
    original = geometry._detect_frames

    def capture(
        rgb: np.ndarray,
        text_mask: np.ndarray,
        uncertain_text_zones: np.ndarray | None = None,
    ) -> list[dict]:
        captured["text_mask"] = text_mask.copy()
        assert uncertain_text_zones is not None
        captured["uncertain_text_zones"] = uncertain_text_zones.copy()
        return original(rgb, text_mask, uncertain_text_zones)

    monkeypatch.setattr(geometry, "_detect_frames", capture)
    geometry.build_geometry_manifest(
        source_path=source,
        ocr_path=ocr_path,
        receipt_path=receipt_path,
        require_isolated_runtime=True,
    )
    text_mask = captured["text_mask"]
    assert text_mask[30, 25] == 1  # measured glyph ink
    assert text_mask[22, 22] == 0  # blank detector interior remains available
    assert text_mask[20, 49] == 0  # detector edge is not erased as a rectangle
    uncertain = captured["uncertain_text_zones"]
    assert uncertain[86, 120] == 1  # blank candidate is retained as risk evidence
    assert uncertain[22, 22] == 0  # reliable candidate does not poison its full box


def test_unresolved_text_zone_downgrades_but_does_not_erase_frame() -> None:
    rgb = np.full((100, 140, 3), 254, dtype=np.uint8)
    cv2.rectangle(rgb, (20, 20), (120, 80), (20, 20, 20), 2)
    unresolved = np.zeros((100, 140), dtype=np.uint8)
    unresolved[10:45, 10:75] = 1
    frames = geometry._detect_frames(
        rgb,
        np.zeros((100, 140), dtype=np.uint8),
        unresolved,
    )
    crossing = [
        item
        for item in frames
        if item["bbox_source"]["x0"] <= 21
        and item["bbox_source"]["y0"] <= 21
        and item["bbox_source"]["x1"] >= 120
    ]
    assert crossing  # structure survives; the unresolved box was never inpainted
    assert not any(item["status"] == "MEASURED" for item in crossing)
    assert all("TEXT_DETECTOR_ZONE_OVERLAP" in item["quality_flags"] for item in crossing)


def test_exactly_overlapping_measured_masks_are_all_diagnostic_only(tmp_path: Path) -> None:
    source, ocr_path, receipt_path = make_gold_case(tmp_path)
    _append_candidate(
        ocr_path,
        candidate_id="T0009",
        observation_id="O00009",
        text="ABC",
        box=(20, 20, 50, 47),
    )
    manifest_path, manifest = geometry.run_geometry_refinement(
        source_path=source,
        ocr_manifest_path=ocr_path,
        host_runtime_receipt_path=receipt_path,
        output_dir=tmp_path / "geometry-overlap-exact",
        project_root=PROJECT_ROOT,
        require_isolated_runtime=True,
    )
    _assert_overlap_is_diagnostic_only(manifest, manifest_path)


def test_partially_overlapping_measured_masks_are_all_diagnostic_only(tmp_path: Path) -> None:
    source, ocr_path, receipt_path = make_gold_case(tmp_path)
    rgb = np.asarray(Image.open(source).convert("RGB")).copy()
    for x in (52, 60):
        rgb[25:43, x : x + 4] = 20
    Image.fromarray(rgb).save(source)
    source_hash = file_hash(source)
    ocr = json.loads(ocr_path.read_text("utf-8"))
    ocr["source"].update(sha256=source_hash, size_bytes=source.stat().st_size)
    ocr_path.write_text(json.dumps(ocr, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt_path.write_text(
        json.dumps(make_host_runtime_receipt(ocr["run_id"], source_hash)), encoding="utf-8"
    )
    _append_candidate(
        ocr_path,
        candidate_id="T0009",
        observation_id="O00009",
        text="GHI",
        box=(38, 20, 68, 47),
    )
    manifest_path, manifest = geometry.run_geometry_refinement(
        source_path=source,
        ocr_manifest_path=ocr_path,
        host_runtime_receipt_path=receipt_path,
        output_dir=tmp_path / "geometry-overlap-partial",
        project_root=PROJECT_ROOT,
        require_isolated_runtime=True,
    )
    _assert_overlap_is_diagnostic_only(manifest, manifest_path)


def test_repeat_run_is_byte_deterministic_across_fresh_directories(tmp_path: Path) -> None:
    first, first_path = _run_gold(tmp_path, "geometry-a")
    second, second_path = _run_gold(tmp_path, "geometry-b")
    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    for name in (
        "geometry-overlay.png",
        "geometry-label-atlas.png",
        "geometry-ambiguity-mask.png",
    ):
        assert file_hash(first_path.parent / name) == file_hash(second_path.parent / name)


def test_output_directory_is_fresh_only(tmp_path: Path) -> None:
    _manifest, manifest_path = _run_gold(tmp_path)
    before = file_hash(manifest_path)
    source = tmp_path / "gold.png"
    with pytest.raises(geometry.GeometryContractError, match="fresh run"):
        geometry.run_geometry_refinement(
            source_path=source,
            ocr_manifest_path=tmp_path / "perception-manifest.json",
            host_runtime_receipt_path=tmp_path / "host-runtime-receipt.json",
            output_dir=manifest_path.parent,
            project_root=PROJECT_ROOT,
            require_isolated_runtime=True,
        )
    assert file_hash(manifest_path) == before


def test_atomic_fresh_writer_cannot_replace_raced_destination(tmp_path: Path) -> None:
    destination = tmp_path / "geometry-overlay.png"
    destination.write_bytes(b"sentinel")
    with pytest.raises(FileExistsError):
        geometry._atomic_write_fresh(destination, b"replacement")
    assert destination.read_bytes() == b"sentinel"
    assert not list(tmp_path.glob(".*.tmp"))


def test_input_mutation_during_build_fails_without_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, ocr_path, receipt_path = make_gold_case(tmp_path)
    original_extract = geometry._extract_candidate
    mutated = False

    def mutating_extract(*args, **kwargs):
        nonlocal mutated
        if not mutated:
            mutated = True
            ocr_path.write_bytes(ocr_path.read_bytes() + b"\n")
        return original_extract(*args, **kwargs)

    monkeypatch.setattr(geometry, "_extract_candidate", mutating_extract)
    with pytest.raises(geometry.GeometryContractError, match="changed during"):
        geometry.run_geometry_refinement(
            source_path=source,
            ocr_manifest_path=ocr_path,
            host_runtime_receipt_path=receipt_path,
            output_dir=tmp_path / "geometry-mutated",
            project_root=PROJECT_ROOT,
            require_isolated_runtime=True,
        )
    assert not (tmp_path / "geometry-mutated" / "geometry-manifest.json").exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest, receipt: manifest["source"].update(sha256="A" * 64), "source SHA-256"),
        (lambda manifest, receipt: receipt.update(status="FAIL"), "all-checks PASS"),
        (
            lambda manifest, receipt: receipt["context"].update(source_sha256="A" * 64),
            "context does not match",
        ),
        (
            lambda manifest, receipt: receipt["runtime"].update(python_version="0.0.0"),
            "Python version",
        ),
    ],
)
def test_wrong_source_or_runtime_binding_fails_closed(
    tmp_path: Path, mutation, message: str
) -> None:
    source, ocr_path, receipt_path = make_gold_case(tmp_path)
    manifest = json.loads(ocr_path.read_text("utf-8"))
    receipt = json.loads(receipt_path.read_text("utf-8"))
    mutation(manifest, receipt)
    ocr_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(geometry.GeometryContractError, match=message):
        geometry.run_geometry_refinement(
            source_path=source,
            ocr_manifest_path=ocr_path,
            host_runtime_receipt_path=receipt_path,
            output_dir=tmp_path / "geometry",
            project_root=PROJECT_ROOT,
            require_isolated_runtime=True,
        )
    assert not (tmp_path / "geometry" / "geometry-manifest.json").exists()


def test_c_shaped_frame_is_never_promoted() -> None:
    rgb = np.full((80, 120, 3), 254, dtype=np.uint8)
    cv2.line(rgb, (10, 10), (100, 10), (20, 20, 20), 2)
    cv2.line(rgb, (10, 10), (10, 65), (20, 20, 20), 2)
    cv2.line(rgb, (10, 65), (100, 65), (20, 20, 20), 2)
    frames = geometry._detect_frames(rgb, np.zeros((80, 120), dtype=np.uint8))
    assert not any(item["status"] == "MEASURED" for item in frames)


def test_filled_rectangle_is_diagnostic_only() -> None:
    rgb = np.full((100, 140, 3), 254, dtype=np.uint8)
    cv2.rectangle(rgb, (25, 20), (110, 75), (20, 20, 20), -1)
    frames = geometry._detect_frames(rgb, np.zeros((100, 140), dtype=np.uint8))
    assert frames
    assert not any(item["status"] == "MEASURED" for item in frames)
    assert any("HIGH_INTERIOR_FOREGROUND_OCCUPANCY" in item["quality_flags"] for item in frames)


def test_regular_grid_cells_and_enclosure_are_not_promoted() -> None:
    rgb = np.full((110, 150, 3), 254, dtype=np.uint8)
    for x in range(10, 131, 20):
        cv2.line(rgb, (x, 10), (x, 90), (20, 20, 20), 1)
    for y in range(10, 91, 20):
        cv2.line(rgb, (10, y), (130, y), (20, 20, 20), 1)
    frames = geometry._detect_frames(rgb, np.zeros((110, 150), dtype=np.uint8))
    assert frames
    assert not any(item["status"] == "MEASURED" for item in frames)
    assert sum("GRID_LIKE_REPEATED_CELLS" in item["quality_flags"] for item in frames) >= 4


def test_extreme_aspect_ratio_outline_is_not_promoted() -> None:
    rgb = np.full((80, 220, 3), 254, dtype=np.uint8)
    cv2.rectangle(rgb, (20, 30), (200, 43), (20, 20, 20), 1)
    frames = geometry._detect_frames(rgb, np.zeros((80, 220), dtype=np.uint8))
    assert frames
    assert not any(item["status"] == "MEASURED" for item in frames)
    assert any("EXTREME_ASPECT_RATIO" in item["quality_flags"] for item in frames)


def test_single_thin_rectangle_does_not_emit_nested_duplicate_measurements() -> None:
    rgb = np.full((70, 90, 3), 254, dtype=np.uint8)
    cv2.rectangle(rgb, (20, 20), (56, 44), (20, 20, 20), 1)
    frames = geometry._detect_frames(rgb, np.zeros((70, 90), dtype=np.uint8))
    assert frames
    assert sum(item["status"] == "MEASURED" for item in frames) <= 1
    assert sum(item["contour_pair_count"] >= 2 for item in frames) == 1
    assert any(item["stroke"]["method"] == "PAIRED_NESTED_CONTOURS" for item in frames)


def test_half_open_polygon_excludes_touching_outside_ink() -> None:
    rgb = np.full((70, 80, 3), 254, dtype=np.uint8)
    for x in (24, 32, 40):
        rgb[25:43, x : x + 4] = 20
    rgb[25:43, 50:54] = 20
    template = build_raw_manifest()["text_candidates"][0]
    candidate = _candidate(
        template,
        candidate_id="T0001",
        observation_id="O00001",
        text="ABC",
        box=(20, 20, 50, 47),
    )
    record, _state, _ambiguity = geometry._extract_candidate(rgb, candidate, mask_label=1)
    assert record["status"] == "MEASURED"
    assert record["ink_bbox"]["x1"] <= record["detector_bbox"]["x1"] == 50


def test_ink_touching_primary_boundary_is_inconclusive() -> None:
    rgb = np.full((70, 80, 3), 254, dtype=np.uint8)
    for x in (20, 30, 40):
        rgb[25:43, x : x + 4] = 20
    template = build_raw_manifest()["text_candidates"][0]
    candidate = _candidate(
        template,
        candidate_id="T0001",
        observation_id="O00001",
        text="ABC",
        box=(20, 20, 50, 47),
    )
    record, state, ambiguity = geometry._extract_candidate(rgb, candidate, mask_label=1)
    assert record["status"] == "INCONCLUSIVE"
    assert record["ink_bbox"] is None
    assert record["mask_label"] is None
    assert state.mask is None
    assert state.diagnostic_mask is not None
    assert ambiguity is not None and np.any(ambiguity)
    assert "INK_TOUCHES_PRIMARY_BOUNDARY" in record["quality_flags"]
    assert "POSSIBLE_OCR_DETECTOR_TRUNCATION" in record["reasons"]


def test_inconclusive_run_always_has_explicit_degradation(tmp_path: Path) -> None:
    source, ocr_path, receipt_path = make_gold_case(tmp_path)
    rgb = np.full((100, 180, 3), 254, dtype=np.uint8)
    cv2.rectangle(rgb, (25, 20), (150, 75), (20, 20, 20), -1)
    Image.fromarray(rgb).save(source)
    source_hash = file_hash(source)
    manifest = json.loads(ocr_path.read_text("utf-8"))
    manifest["text_candidates"] = []
    manifest["summary"]["candidate_count"] = 0
    manifest["source"].update(
        sha256=source_hash,
        size_bytes=source.stat().st_size,
        width_px=180,
        height_px=100,
    )
    ocr_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path.write_text(
        json.dumps(make_host_runtime_receipt(manifest["run_id"], source_hash)),
        encoding="utf-8",
    )
    geometry_manifest, _payloads = geometry.build_geometry_manifest(
        source_path=source,
        ocr_path=ocr_path,
        receipt_path=receipt_path,
        require_isolated_runtime=True,
    )
    assert geometry_manifest["status"] == "GEOMETRY_INCONCLUSIVE"
    assert "NO_MEASURED_GEOMETRY_OBSERVATIONS" in geometry_manifest["degradations"]


def test_schema_objects_are_closed_world() -> None:
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "geometry-manifest.schema.json").read_text("utf-8")
    )

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)


def test_schema_rejects_fake_measured_records(tmp_path: Path) -> None:
    manifest, _path = _run_gold(tmp_path)
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "geometry-manifest.schema.json").read_text("utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)

    fake_text = copy.deepcopy(manifest)
    measured_text = next(
        item for item in fake_text["text_geometry"] if item["status"] == "MEASURED"
    )
    measured_text["ink_bbox"] = None
    assert list(validator.iter_errors(fake_text))

    fake_baseline = copy.deepcopy(manifest)
    measured_baseline = next(
        item["baseline"]
        for item in fake_baseline["text_geometry"]
        if item["baseline"]["status"] == "MEASURED"
    )
    measured_baseline["endpoints_source"] = None
    assert list(validator.iter_errors(fake_baseline))

    fake_frame = copy.deepcopy(manifest)
    measured_frame = next(
        item for item in fake_frame["frame_candidates"] if item["status"] == "MEASURED"
    )
    measured_frame["closed_contour_evidence"] = False
    assert list(validator.iter_errors(fake_frame))


def test_read_only_manifest_verifier_is_strict(tmp_path: Path) -> None:
    _manifest, manifest_path = _run_gold(tmp_path)
    assert geometry.verify_geometry_manifest_file(manifest_path)["schema_version"] == "1.0.0"
    assert geometry.main(["--verify-manifest", str(manifest_path)]) == geometry.EXIT_OK

    duplicate = tmp_path / "duplicate-key.json"
    payload = manifest_path.read_text("utf-8")
    payload = payload.replace(
        '"schema_version": "1.0.0",',
        '"schema_version": "1.0.0",\n  "schema_version": "1.0.0",',
        1,
    )
    duplicate.write_text(payload, encoding="utf-8")
    assert geometry.main(["--verify-manifest", str(duplicate)]) == geometry.EXIT_CONTRACT_REJECTED

    for index, token in enumerate(("NaN", "Infinity", "1e999"), start=1):
        nonfinite = tmp_path / f"nonfinite-{index}.json"
        nonfinite.write_text(
            manifest_path.read_text("utf-8").replace(
                '"ocr_confidence": 0.99', f'"ocr_confidence": {token}', 1
            ),
            encoding="utf-8",
        )
        assert (
            geometry.main(["--verify-manifest", str(nonfinite)]) == geometry.EXIT_CONTRACT_REJECTED
        )
