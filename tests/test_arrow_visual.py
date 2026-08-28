"""Physical arrow visual gates are independent of PowerPoint/OOXML readback."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from tools.arrows.arrow_visual import (
    _contract_sha256,
    _foreground_mask,
    evaluate_arrow_visual_contracts,
)

REFERENCE_HASH = "a" * 64
GRAY = np.array([118, 113, 113], dtype=np.uint8)


def _image(*, end_right: int = 90) -> np.ndarray:
    image = np.full((40, 110, 3), 255, dtype=np.uint8)
    image[12:28, 10:20] = GRAY
    image[17:23, 20:80] = GRAY
    image[12:28, 80:end_right] = GRAY
    return image


def _contract(*, evidence_kind: str = "reference_pixels") -> dict:
    evidence = {"kind": evidence_kind, "reference_sha256": REFERENCE_HASH}
    if evidence_kind == "explicit":
        evidence["basis"] = "measured from the hash-bound reference arrow glyph"
    heads: dict = {
        "start": {"search_bbox": [10, 12, 10, 16]},
        "end": {"search_bbox": [80, 12, 10, 16]},
    }
    if evidence_kind == "explicit":
        heads["start"]["expected"] = {
            "bbox": [10, 12, 10, 16],
            "width_px": 16,
            "length_px": 10,
        }
        heads["end"]["expected"] = {
            "bbox": [80, 12, 10, 16],
            "width_px": 16,
            "length_px": 10,
        }
    return {
        "id": "exchange-glyph",
        "element_id": "exchange",
        "axis": "horizontal",
        "tight_bbox": [10, 12, 80, 16],
        "shaft_seed_point": [50, 20],
        "shaft_seed_radius_px": 1,
        "shaft_width_px": 6,
        "mask": {"mode": "rgb", "foreground_rgb": GRAY.tolist(), "tolerance": 0},
        "evidence": evidence,
        "heads": heads,
        "obstacles": [
            {
                "id": "environment",
                "bbox": [94, 12, 4, 16],
                "max_intersection_pixels": 0,
                "clearance_tolerance_px": 0,
            }
        ],
    }


def _svg(head_length: str = "10") -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<line id="exchange" data-head-length-end="{head_length}" '
        'marker-end="url(#tip)"/></svg>'
    )


def test_hsv_mask_keeps_gold_antialiasing_without_absorbing_navy_border():
    image = np.full((4, 4, 3), 255, dtype=np.uint8)
    image[1, 1] = [141, 106, 0]
    image[1, 2] = [210, 193, 145]
    image[2, 1] = [29, 56, 93]
    mask = _foreground_mask(
        image,
        {
            "mode": "hsv",
            "foreground_rgb": [141, 106, 0],
            "hue_tolerance_deg": 18,
            "saturation_min": 0.05,
        },
        resolved_background=None,
    )
    assert mask[1, 1]
    assert mask[1, 2]
    assert not mask[2, 1]
    assert not mask[0, 0]


def test_reference_pixels_measure_head_bbox_size_without_svg_self_report():
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[_contract()],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )
    assert report["pass"] is True
    assert report["trusted_head_lengths"] == {
        "exchange:start": 10.0,
        "exchange:end": 10.0,
    }
    heads = {item["side"]: item for item in report["records"][0]["heads"]}
    assert heads["end"]["expected"]["bbox"] == [80, 12, 10, 16]
    obstacle = report["records"][0]["obstacles"][0]
    assert obstacle["render"]["intersection_pixels"] == 0
    assert obstacle["render"]["clearance_px"] == 4.0
    assert report["declared_head_lengths"] == []


def test_short_wide_block_head_is_distinct_by_cross_axis_width():
    image = np.full((40, 100, 3), 255, dtype=np.uint8)
    image[17:23, 20:80] = GRAY
    image[12:28, 80:86] = GRAY
    contract = _contract()
    contract["heads"] = {"end": {"search_bbox": [80, 12, 6, 16]}}
    contract["tight_bbox"] = [20, 12, 66, 16]
    report = evaluate_arrow_visual_contracts(
        reference=image,
        render=image,
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )
    assert report["pass"] is True
    head = report["records"][0]["heads"][0]
    assert head["expected_head_to_shaft_ratio"] == 2.6667


def test_explicit_self_reported_geometry_is_rejected_even_when_hash_bound():
    contract = _contract(evidence_kind="explicit")
    blank_reference = np.full_like(_image(), 255)
    report = evaluate_arrow_visual_contracts(
        reference=blank_reference,
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text=_svg(),
    )
    assert report["pass"] is False
    assert report["blockers"] == [
        "arrow-visual:exchange-glyph:evidence:unsupported-kind",
        "arrow-visual:exchange:end:deprecated-data-head-length",
    ]

    contract["evidence"]["reference_sha256"] = "b" * 64
    stale = evaluate_arrow_visual_contracts(
        reference=blank_reference,
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text=_svg(),
    )
    assert "arrow-visual:exchange-glyph:evidence:unsupported-kind" in stale["blockers"]
    assert stale["trusted_head_lengths"] == {}
    assert any("deprecated-data-head-length" in item for item in stale["blockers"])


def test_render_head_growth_and_obstacle_collision_are_physical_blockers():
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(end_right=96),
        reference_sha256=REFERENCE_HASH,
        contracts=[_contract()],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )
    assert report["pass"] is False
    assert "arrow-visual:exchange-glyph:silhouette-bbox" in report["blockers"]
    assert (
        "arrow-visual:exchange-glyph:obstacle:environment:pixel-intersection"
        in report["blockers"]
    )
    obstacle = report["records"][0]["obstacles"][0]
    assert obstacle["render"]["intersection_pixels"] == 32
    assert obstacle["render"]["clearance_px"] == 0.0


def test_self_reported_head_length_is_never_a_pixel_calibration_source():
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[],
        svg_text=_svg(),
    )
    assert report["pass"] is False
    assert report["trusted_head_lengths"] == {}
    assert report["blockers"] == [
        "arrow-visual:exchange:end:deprecated-data-head-length"
    ]


def test_declared_visual_contract_inventory_blocks_silent_contract_removal():
    contract = _contract()
    expectation = {
        "count": 1,
        "contracts": [
            {
                "element_id": "exchange",
                "head_sides": ["start", "end"],
                "contract_sha256": _contract_sha256(contract),
            }
        ],
    }
    missing = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
        expectation=expectation,
    )
    assert missing["pass"] is False
    assert missing["expectation"]["missing_element_ids"] == ["exchange"]
    assert missing["blockers"] == [
        "arrow-visual:expectation:count-mismatch",
        "arrow-visual:expectation:missing-elements",
    ]

    complete = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
        expectation=expectation,
    )
    assert complete["pass"] is True
    assert complete["expectation"]["pass"] is True


def test_frozen_embedded_plot_axis_exemption_closes_scene_inventory():
    contract = _contract()
    expectation = {
        "count": 1,
        "contracts": [
            {
                "element_id": "exchange",
                "head_sides": ["start", "end"],
                "contract_sha256": _contract_sha256(contract),
            }
        ],
        "exemptions": [
            {
                "element_id": "plot-axis-x",
                "head_sides": ["end"],
                "reason": "embedded_plot_axis",
                "parent_object_id": "plot-1",
            }
        ],
    }
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
        expectation=expectation,
        required_heads={
            "exchange": {"start", "end"},
            "plot-axis-x": {"end"},
        },
    )
    assert report["pass"] is True
    assert report["expectation"]["exempt_element_ids"] == ["plot-axis-x"]


def test_unsupported_head_key_cannot_create_a_zero_measurement_pass():
    contract = _contract()
    contract["heads"] = {"bogus": {}}
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )
    assert report["pass"] is False
    assert "arrow-visual:exchange-glyph:contract:unsupported-head-side" in report[
        "blockers"
    ]


def test_whole_arrow_mask_rejects_a_thinned_shaft_even_with_the_same_head():
    render = _image()
    render[17:23, 20:80] = 255
    render[19:21, 20:80] = GRAY
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=render,
        reference_sha256=REFERENCE_HASH,
        contracts=[_contract()],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )
    assert report["pass"] is False
    assert "arrow-visual:exchange-glyph:silhouette-mask" in report["blockers"]
    assert "arrow-visual:exchange-glyph:silhouette-area" in report["blockers"]


def test_head_silhouette_gate_rejects_a_reversed_triangle_with_same_bbox():
    reference_canvas = Image.new("RGB", (50, 30), "white")
    render_canvas = Image.new("RGB", (50, 30), "white")
    reference_draw = ImageDraw.Draw(reference_canvas)
    render_draw = ImageDraw.Draw(render_canvas)
    reference_draw.line((5, 15, 35, 15), fill=tuple(GRAY), width=3)
    render_draw.line((5, 15, 35, 15), fill=tuple(GRAY), width=3)
    reference_draw.polygon([(35, 10), (45, 15), (35, 20)], fill=tuple(GRAY))
    render_draw.polygon([(45, 10), (35, 15), (45, 20)], fill=tuple(GRAY))
    contract = {
        "id": "direction",
        "element_id": "direction",
        "axis": "horizontal",
        "tight_bbox": [5, 10, 41, 11],
        "shaft_seed_point": [20, 15],
        "shaft_width_px": 3,
        "mask": {"mode": "rgb", "foreground_rgb": GRAY.tolist(), "tolerance": 0},
        "evidence": {"kind": "reference_pixels", "reference_sha256": REFERENCE_HASH},
        "heads": {
            "end": {
                "search_bbox": [34, 9, 12, 13],
                "bbox_tolerance_px": 1,
                "size_tolerance_px": 1,
            }
        },
    }
    report = evaluate_arrow_visual_contracts(
        reference=np.asarray(reference_canvas, dtype=np.uint8),
        render=np.asarray(render_canvas, dtype=np.uint8),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )
    assert report["pass"] is False
    assert "arrow-visual:direction:end:head-silhouette" in report["blockers"]


def test_diagonal_head_is_measured_in_its_declared_tangent_basis():
    canvas = Image.new("RGB", (50, 50), "white")
    draw = ImageDraw.Draw(canvas)
    draw.line((10, 40, 35, 15), fill=tuple(GRAY), width=3)
    draw.polygon([(35, 15), (28, 17), (33, 22)], fill=tuple(GRAY))
    image = np.asarray(canvas, dtype=np.uint8)
    contract = {
        "id": "diagonal",
        "element_id": "diagonal",
        "axis": "angle",
        "tight_bbox": [8, 12, 30, 31],
        "shaft_seed_point": [20, 30],
        "shaft_width_px": 3,
        "mask": {"mode": "rgb", "foreground_rgb": GRAY.tolist(), "tolerance": 0},
        "evidence": {"kind": "reference_pixels", "reference_sha256": REFERENCE_HASH},
        "heads": {
            "end": {
                "search_bbox": [27, 12, 12, 12],
                "axis_angle_deg": -45,
            }
        },
    }

    report = evaluate_arrow_visual_contracts(
        reference=image,
        render=image,
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
    )

    assert report["pass"] is True
    head = report["records"][0]["heads"][0]
    assert head["expected"]["length_px"] > 0
    assert head["expected"]["width_px"] > 0


def _tiny_direction_case(*, reverse_reference: bool, reverse_render: bool):
    def image_for(reverse: bool) -> np.ndarray:
        image = np.full((15, 20, 3), 255, dtype=np.uint8)
        image[7, 2:12] = GRAY
        if reverse:
            image[6:9, 13] = GRAY
        else:
            image[6:9, 11] = GRAY
        image[7, 11:14] = GRAY
        return image

    contract = {
        "id": "tiny-direction",
        "element_id": "tiny-direction",
        "axis": "horizontal",
        "tight_bbox": [2, 6, 12, 3],
        "shaft_seed_point": [5, 7],
        "shaft_width_px": 1,
        "mask": {"mode": "rgb", "foreground_rgb": GRAY.tolist(), "tolerance": 0},
        "evidence": {"kind": "reference_pixels", "reference_sha256": REFERENCE_HASH},
        "heads": {"end": {"search_bbox": [11, 6, 3, 3]}},
    }
    return evaluate_arrow_visual_contracts(
        reference=image_for(reverse_reference),
        render=image_for(reverse_render),
        reference_sha256=REFERENCE_HASH,
        contracts=[contract],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
        scene_head_directions={
            "tiny-direction": {
                "end": {"head_type": "triangle", "outward_angle_deg": 0.0}
            }
        },
    )


def test_three_pixel_head_reversal_cannot_hide_behind_fuzzy_iou():
    report = _tiny_direction_case(reverse_reference=False, reverse_render=True)
    assert report["pass"] is False
    assert "arrow-visual:tiny-direction:end:head-direction" in report["blockers"]
    head = report["records"][0]["heads"][0]
    assert head["mask_iou"] == 1.0
    assert head["canonical_orientation"]["direct_iou"] < head[
        "canonical_orientation"
    ]["opposite_iou"]


def test_matching_but_scene_reversed_heads_fail_the_directed_taper_gate():
    report = _tiny_direction_case(reverse_reference=True, reverse_render=True)
    assert report["pass"] is False
    assert "arrow-visual:tiny-direction:end:head-direction" in report["blockers"]
    head = report["records"][0]["heads"][0]
    assert head["canonical_orientation"]["direct_iou"] == 1.0
    assert head["scene_taper"]["reference_taper"] < 0


def test_null_arrow_expectation_is_not_an_explicit_zero_contract():
    report = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
        expectation=None,
    )
    assert report["pass"] is False
    assert report["blockers"] == ["arrow-visual:expectation:invalid"]

    explicit_zero = evaluate_arrow_visual_contracts(
        reference=_image(),
        render=_image(),
        reference_sha256=REFERENCE_HASH,
        contracts=[],
        svg_text="<svg xmlns='http://www.w3.org/2000/svg'/>",
        expectation={"count": 0, "contracts": []},
    )
    assert explicit_zero["pass"] is True
