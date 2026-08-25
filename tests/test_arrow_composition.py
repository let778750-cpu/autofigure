from __future__ import annotations

from copy import deepcopy

from tools.arrow_composition import (
    find_reciprocal_arrow_overlaps,
    reciprocal_overlap_finding,
)
from tools.arrow_spec import ARROW_SPEC_VERSION, head, path_from_segments


def _spec(
    segments: list[tuple],
    *,
    start: str = "none",
    end: str = "triangle",
) -> dict:
    return {
        "schema_version": ARROW_SPEC_VERSION,
        "representation": "line_arrow",
        "path": path_from_segments(segments),
        "routing": "fixed",
        "topology": {
            "mode": "none",
            "source_id": None,
            "target_id": None,
            "source_site": None,
            "target_site": None,
        },
        "body": {
            "color": "#7F7F7F",
            "width_px": 7.0,
            "dash": "solid",
            "line_cap": "butt",
            "line_join": "miter",
        },
        "start_head": head(start, width="sm", length="sm", color="#7F7F7F"),
        "end_head": head(end, width="sm", length="sm", color="#7F7F7F"),
        "silhouette_path": None,
        "fallback_policy": "strict_fail",
        "single_visible_object": True,
        "source_evidence": {
            "input_route": "reference-only",
            "reference_sha256": "a" * 64,
            "reference_bbox": [0, 0, 200, 20],
            "confidence": 1.0,
        },
    }


def test_exact_reverse_single_headed_lines_are_a_stable_blocker():
    forward = _spec([("M", 0, 0), ("L", 100, 0)])
    reverse = _spec([("M", 100, 0), ("L", 0, 0)])

    assert find_reciprocal_arrow_overlaps(
        [("right", forward), ("left", reverse)]
    ) == [
        {
            "code": "reciprocal-arrow-overlap",
            "severity": "blocker",
            "arrow_ids": ["left", "right"],
            "path_kinds": ["straight", "straight"],
            "overlap_ratio": 1.0,
            "overlap_threshold": 0.8,
            "endpoint_error_px": 0.0,
            "centerline_error_px": 0.0,
            "opposition_cosine": -1.0,
            "required_representation": "one-bidirectional-visible-object-matching-reference",
        }
    ]


def test_current_153_162_pixel_interaction_fixture_is_detected():
    interaction_left = _spec([("M", 1168, 573), ("L", 1015, 573)])
    interaction_right = _spec([("M", 1015, 573), ("L", 1177, 573)])

    findings = find_reciprocal_arrow_overlaps(
        {
            "interaction-left": interaction_left,
            "interaction-right": interaction_right,
        }
    )

    assert len(findings) == 1
    assert findings[0]["arrow_ids"] == ["interaction-left", "interaction-right"]
    assert findings[0]["overlap_ratio"] >= 153 / 162 - 0.01
    assert findings[0]["endpoint_error_px"] == 9.0


def test_one_double_ended_arrow_is_not_a_reciprocal_pair():
    double_ended = _spec(
        [("M", 0, 0), ("L", 100, 0)],
        start="triangle",
        end="triangle",
    )

    assert find_reciprocal_arrow_overlaps(
        [{"id": "interaction", "arrow_spec": double_ended}]
    ) == []


def test_parallel_separate_lanes_are_not_treated_as_one_relation():
    forward = _spec([("M", 0, 0), ("L", 100, 0)])
    reverse = _spec([("M", 100, 10), ("L", 0, 10)])

    assert reciprocal_overlap_finding("forward", forward, "reverse", reverse) is None


def test_crossing_shared_endpoint_and_short_overlap_do_not_trigger():
    horizontal = _spec([("M", 0, 0), ("L", 100, 0)])
    crossing = _spec([("M", 50, 50), ("L", 50, -50)])
    shared_endpoint = _spec([("M", 200, 0), ("L", 100, 0)])
    short_overlap = _spec([("M", 20, 0), ("L", 0, 0)])

    assert reciprocal_overlap_finding("horizontal", horizontal, "crossing", crossing) is None
    assert (
        reciprocal_overlap_finding(
            "horizontal", horizontal, "shared-endpoint", shared_endpoint
        )
        is None
    )
    assert (
        reciprocal_overlap_finding(
            "horizontal", horizontal, "short-overlap", short_overlap
        )
        is None
    )


def test_reversed_polyline_and_cubic_centerlines_are_supported():
    polyline = _spec(
        [("M", 0, 0), ("L", 50, 1), ("L", 100, 0)]
    )
    reverse_polyline = _spec(
        [("M", 100, 0), ("L", 50, 1), ("L", 0, 0)]
    )
    cubic = _spec(
        [("M", 0, 0), ("C", 30, 12, 70, 12, 100, 0)]
    )
    reverse_cubic = _spec(
        [("M", 100, 0), ("C", 70, 12, 30, 12, 0, 0)]
    )

    assert reciprocal_overlap_finding("polyline", polyline, "reverse", reverse_polyline)
    assert reciprocal_overlap_finding("cubic", cubic, "reverse", reverse_cubic)


def test_invalid_or_non_line_records_are_ignored_without_masking_valid_pairs():
    forward = _spec([("M", 0, 0), ("L", 100, 0)])
    reverse = deepcopy(forward)
    reverse["path"] = path_from_segments([("M", 100, 0), ("L", 0, 0)])

    records = [
        {"id": "missing-spec"},
        {"id": "block", "arrow_spec": {"representation": "block_arrow"}},
        {"id": "forward", "arrow_spec": forward},
        {"id": "reverse", "arrow_spec": reverse},
    ]

    assert [item["arrow_ids"] for item in find_reciprocal_arrow_overlaps(records)] == [
        ["forward", "reverse"]
    ]
