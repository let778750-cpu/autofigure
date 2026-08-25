from __future__ import annotations

import itertools

from tools.arrow_spec import (
    ARROW_SPEC_VERSION,
    DASH_TO_OOXML,
    arrow_direction,
    compiler_strategy,
    head,
    path_from_segments,
    semantic_dash_from_ooxml,
    silhouette_from_segments,
    spec_sha256,
    validate_arrow_spec,
    validate_scene_arrow_specs,
)


def _spec(**overrides):
    value = {
        "schema_version": ARROW_SPEC_VERSION,
        "representation": "line_arrow",
        "path": path_from_segments([("M", 10, 20), ("L", 80, 20)]),
        "routing": "fixed",
        "topology": {
            "mode": "none",
            "source_id": None,
            "target_id": None,
            "source_site": None,
            "target_site": None,
        },
        "body": {
            "color": "#000000",
            "width_px": 1.0,
            "dash": "solid",
            "line_cap": "butt",
            "line_join": "miter",
        },
        "start_head": head(),
        "end_head": head("triangle", width="med", length="med", color="#000000"),
        "silhouette_path": None,
        "fallback_policy": "strict_fail",
        "single_visible_object": True,
        "source_evidence": {
            "input_route": "svg-seeded",
            "reference_sha256": "a" * 64,
            "reference_bbox": None,
            "confidence": None,
        },
    }
    value.update(overrides)
    return value


def test_all_native_endpoint_size_combinations_are_valid_and_deterministic():
    hashes = set()
    for head_type, width, length, side in itertools.product(
        ("open", "triangle", "stealth", "diamond", "oval"),
        ("sm", "med", "lg"),
        ("sm", "med", "lg"),
        ("start", "end"),
    ):
        start = head()
        end = head()
        selected = head(head_type, width=width, length=length, color="#000000")
        if side == "start":
            start = selected
        else:
            end = selected
        spec = _spec(start_head=start, end_head=end)
        assert validate_arrow_spec(spec) == []
        assert spec_sha256(spec) == spec_sha256(spec)
        hashes.add(spec_sha256(spec))
    assert len(hashes) == 90


def test_direction_handles_start_only_and_bidirectional():
    assert arrow_direction(_spec(start_head=head("open"), end_head=head())) == "backward"
    assert arrow_direction(
        _spec(start_head=head("open"), end_head=head("triangle"))
    ) == "bidirectional"


def test_path_normalization_is_deterministic_for_line_polyline_and_cubic():
    assert path_from_segments([("M", 0, 0), ("L", 1, 1)])["kind"] == "straight"
    assert path_from_segments([("M", 0, 0), ("L", 1, 1), ("L", 2, 1)])["kind"] == "polyline"
    cubic = path_from_segments(
        [("M", 0, 0), ("L", 1, 0), ("C", 2, 0, 3, 1, 4, 1)]
    )
    assert cubic["kind"] == "cubic"
    assert len(cubic["segments"]) == 2


def test_reference_only_requires_bbox_and_confidence():
    spec = _spec()
    spec["source_evidence"] = {
        "input_route": "reference-only",
        "reference_sha256": "b" * 64,
        "reference_bbox": None,
        "confidence": None,
    }
    assert {"reference-bbox", "inference-confidence"}.issubset(validate_arrow_spec(spec))
    spec["source_evidence"].update(
        {"reference_bbox": [8, 9, 75, 22], "confidence": 0.75}
    )
    assert validate_arrow_spec(spec) == []


def test_source_evidence_is_bound_to_the_current_run():
    spec = _spec()
    assert validate_arrow_spec(
        spec,
        expected_input_route="svg-seeded",
        expected_reference_sha256="a" * 64,
    ) == []
    assert "source-input-route-mismatch" in validate_arrow_spec(
        spec,
        expected_input_route="reference-only",
        expected_reference_sha256="a" * 64,
    )
    assert "source-reference-sha256-mismatch" in validate_arrow_spec(
        spec,
        expected_input_route="svg-seeded",
        expected_reference_sha256="b" * 64,
    )


def test_routing_and_topology_must_form_a_supported_pair():
    attached = {
        "mode": "attached",
        "source_id": "source",
        "target_id": "target",
        "source_site": 0,
        "target_site": 1,
    }
    assert "routing-topology" in validate_arrow_spec(
        _spec(routing="fixed", topology=attached)
    )
    assert "routing-topology" in validate_arrow_spec(_spec(routing="host"))
    assert validate_arrow_spec(_spec(routing="host", topology=attached)) == []


def test_vetted_block_autoshape_has_a_deterministic_native_strategy():
    silhouette = silhouette_from_segments(
        [
            ("M", 0, 8.5),
            ("L", 8, 0),
            ("L", 8, 4),
            ("L", 152, 4),
            ("L", 152, 0),
            ("L", 160, 8.5),
            ("L", 152, 17),
            ("L", 152, 13),
            ("L", 8, 13),
            ("L", 8, 17),
            ("Z",),
        ]
    )
    spec = _spec(
        representation="block_arrow",
        path=path_from_segments([("M", 0, 8.5), ("L", 160, 8.5)]),
        autoshape={
            "subtype": "leftRightArrow",
            "adjustments": [9 / 17, 8 / 17],
            "bbox": [0, 0, 160, 17],
        },
        silhouette_path=silhouette,
        start_head=head("triangle", width="lg", length="sm", color="#000000"),
        end_head=head("triangle", width="lg", length="sm", color="#000000"),
    )
    assert validate_arrow_spec(spec) == []
    assert compiler_strategy(spec) == "native-block-autoshape"


def test_unvetted_block_autoshape_still_fails_closed():
    spec = _spec(
        representation="block_arrow",
        path=path_from_segments([("M", 0, 0.5), ("L", 1, 0.5)]),
        autoshape={
            "subtype": "rightArrow",
            "adjustments": [0.5],
            "bbox": [0, 0, 160, 17],
        },
        silhouette_path=silhouette_from_segments(
            [("M", 0, 0), ("L", 1, 0), ("L", 1, 1), ("Z",)]
        ),
        start_head=head(),
        end_head=head(),
    )
    errors = validate_arrow_spec(spec)
    assert "block-autoshape-subtype" in errors
    assert "block-autoshape-adjustments" in errors
    assert compiler_strategy(spec) == "unsupported"


def test_scene_edge_and_element_specs_cannot_drift():
    spec = _spec()
    scene = {
        "elements": [{"id": "a", "arrow_spec": spec}],
        "edges": [{"id": "a", "arrow_spec": spec}],
    }
    assert validate_scene_arrow_specs(scene) == []
    scene["edges"][0]["arrow_spec"] = _spec(routing="host")
    assert validate_scene_arrow_specs(scene) == ["arrow-scene-edge-drift:a"]


def test_declared_topology_endpoints_must_resolve_to_scene_elements():
    spec = _spec(
        topology={
            "mode": "declared",
            "source_id": "source",
            "target_id": "nonexistent-target",
            "source_site": 0,
            "target_site": 0,
        }
    )
    scene = {
        "elements": [
            {"id": "source", "kind": "shape"},
            {"id": "arrow", "kind": "edge", "arrow_spec": spec},
        ],
        "edges": [{"id": "arrow", "arrow_spec": spec}],
    }

    assert validate_scene_arrow_specs(scene) == [
        "arrow-scene-topology-target-unresolved:arrow:nonexistent-target"
    ]


def test_all_twelve_dash_semantics_have_an_ooxml_encoding():
    assert len(DASH_TO_OOXML) == 12
    assert semantic_dash_from_ooxml("dot", "butt") == "square_dot"
    assert semantic_dash_from_ooxml("dot", "round") == "round_dot"
    assert semantic_dash_from_ooxml("sysDot", "butt") == "sys_dot"


def test_block_arrow_uses_one_closed_silhouette():
    silhouette = silhouette_from_segments(
        [("M", 0, 0), ("L", 8, 0), ("L", 8, -3), ("L", 14, 4), ("L", 8, 11), ("L", 8, 8), ("L", 0, 8), ("Z",)]
    )
    spec = _spec(
        representation="block_arrow",
        path=path_from_segments([("M", 0, 4), ("L", 14, 4)]),
        silhouette_path=silhouette,
        start_head=head(),
        end_head=head(),
    )
    assert validate_arrow_spec(spec) == []
    assert compiler_strategy(spec) == "single-closed-freeform"


def test_block_arrow_requires_a_valid_semantic_centerline():
    silhouette = silhouette_from_segments(
        [("M", 0, 0), ("L", 8, 0), ("L", 14, 4), ("L", 8, 8), ("Z",)]
    )
    spec = _spec(
        representation="block_arrow",
        path=None,
        silhouette_path=silhouette,
        start_head=head(),
        end_head=head("triangle", width="lg", length="sm", color="#000000"),
    )

    assert "path-kind" in validate_arrow_spec(spec)
    spec["path"] = path_from_segments([("M", 4, 4), ("L", 4, 4)])
    assert {
        "path-endpoints-degenerate",
        "path-start-tangent",
        "path-end-tangent",
    }.issubset(validate_arrow_spec(spec))


def test_arrow_body_and_head_style_fields_are_fail_closed():
    spec = _spec()
    for field, value, error in (
        ("color", "black", "body-color"),
        ("dash", "mystery", "body-dash"),
        ("line_cap", "flat", "body-line-cap"),
        ("line_join", "sharp", "body-line-join"),
    ):
        mutated = _spec(body={**spec["body"], field: value})
        assert error in validate_arrow_spec(mutated)

    bad_head = head("triangle", width="med", length="med", color="black")
    assert "end_head:color" in validate_arrow_spec(_spec(end_head=bad_head))
    none_with_style = head()
    none_with_style["color"] = "#000000"
    assert "end_head:none-fields" in validate_arrow_spec(
        _spec(end_head=none_with_style)
    )


def test_previous_arrow_spec_version_requires_regeneration():
    assert "schema-version" in validate_arrow_spec(_spec(schema_version="1.0.0"))
