from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Callable

import pytest
from PIL import Image

from tools.core import common
from tools.arrows import pptx_arrows
from tools.core.contracts import read_json, write_json
from tools.pipeline.convert import convert
from tools.assets.primitives import (
    _command_signature,
    _segments_close,
    audit_primitives,
    canonical_brace_segments,
    materialize_brace,
    segments_to_d,
    strict_blockers,
)
from tools.core.svggeom import parse_path_d

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PPTX_NS = {"p": P_NS, "a": A_NS}


@pytest.mark.parametrize("orientation", ["over", "under", "left", "right"])
def test_brace_v1_all_orientations_share_two_lobe_signature(orientation: str):
    segments = canonical_brace_segments(
        20,
        30,
        107,
        16,
        orientation,
        cusp_offset=53.5,
    )
    assert _command_signature(segments) == "MLCLCLMLCLCL"
    assert sum(1 for segment in segments if segment[0] == "M") == 2
    assert segments[5][1:] == segments[6][1:]


def test_over_and_under_are_exact_vertical_mirrors():
    under = canonical_brace_segments(0, 40, 107, 16, "under", cusp_offset=53.5)
    over = canonical_brace_segments(0, 40, 107, 16, "over", cusp_offset=53.5)
    mirrored = []
    for segment in under:
        values = list(segment[1:])
        for index in range(1, len(values), 2):
            values[index] = 80 - values[index]
        mirrored.append((segment[0], *values))
    assert _segments_close(mirrored, over, tolerance=1e-9)


def _run(tmp_path: Path, path: str) -> common.Run:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (180, 100), "white").save(reference)
    run = common.create_run(
        reference,
        case="brace",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    run.redraw_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="100" viewBox="0 0 180 100">'
        f"{path}</svg>",
        encoding="utf-8",
    )
    convert(run)
    bindings = read_json(run.bindings_path)
    primitives = [
        {
            "element_id": row["element_id"],
            "brace_spec_sha256": row["brace_spec_sha256"],
            "primitive_spec_sha256": row["primitive_spec_sha256"],
        }
        for row in bindings.get("bindings", [])
        if isinstance(row.get("brace_spec_sha256"), str)
    ]
    if primitives:
        regions = read_json(run.regions_path)
        regions["primitive_expectations"] = [
            {"kind": "brace", "count": len(primitives), "primitives": primitives}
        ]
        write_json(run.regions_path, regions)
    return run


def _canonical_run(tmp_path: Path) -> common.Run:
    d = segments_to_d(
        canonical_brace_segments(20, 40, 107, 16, "under", cusp_offset=53.5)
    )
    return _run(
        tmp_path,
        f'<path id="answer-underbrace" d="{d}" fill="none" stroke="#000000" '
        'stroke-width="1" stroke-linecap="round" data-primitive-kind="brace" '
        'data-brace-orientation="under" data-brace-x="20" data-brace-y="40" '
        'data-brace-length="107" data-brace-depth="16" data-brace-cusp-offset="53.5"/>',
    )


def _rewrite_brace_ooxml(
    path: Path, mutate: Callable[[ET.Element, ET.Element], None]
) -> None:
    replacement = path.with_name(f"{path.stem}-mutated{path.suffix}")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename == "ppt/slides/slide1.xml":
                root = ET.fromstring(payload)
                for shape in root.findall(".//p:sp", PPTX_NS):
                    identity = shape.find("./p:nvSpPr/p:cNvPr", PPTX_NS)
                    properties = shape.find("./p:spPr", PPTX_NS)
                    if (
                        identity is not None
                        and properties is not None
                        and identity.get("name", "").startswith("af-answer-underbrace-freeform-")
                    ):
                        mutate(identity, properties)
                        break
                else:
                    raise AssertionError("brace shape not found in generated PPTX")
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(entry, payload)
    replacement.replace(path)


def test_canonical_brace_roundtrips_as_one_powerpoint_freeform(tmp_path: Path):
    run = _canonical_run(tmp_path)
    report = audit_primitives(run)
    assert report["pass"] is True
    assert report["primitive_count"] == 1
    record = report["records"][0]
    assert record["source_path_pass"] is True
    assert record["scene_geometry_signature_pass"] is True
    assert record["scene_geometry_pass"] is True
    assert record["native_identity_pass"] is True
    assert record["powerpoint_path_pass"] is True
    assert record["powerpoint_fill_pass"] is True
    assert record["powerpoint_stroke_pass"] is True
    assert record["binding_hash_pass"] is True
    assert record["binding_brace_spec_hash_pass"] is True
    assert record["description_identity_pass"] is True
    assert record["embedded_hash_pass"] is True
    assert record["embedded_brace_spec_hash_pass"] is True
    assert record["scene_brace_spec_hash_pass"] is True
    assert record["brace_spec_migration_locations"] == []
    assert record["brace_spec_alias_mismatch_locations"] == []
    assert record["symmetry_contract"]["central_cusp_pass"] is True
    assert record["symmetry_contract"]["double_lobe_mirror_pass"] is True
    scene_element = next(
        item
        for item in read_json(run.scene_path)["elements"]
        if item["id"] == "answer-underbrace"
    )
    assert scene_element["brace_spec"] == scene_element["primitive_spec"]
    rows = [
        row
        for row in read_json(run.bindings_path)["bindings"]
        if row["element_id"] == "answer-underbrace"
    ]
    assert len(rows) == 1
    assert rows[0]["object_kind"] == "freeform"
    assert rows[0]["brace_spec_sha256"] == rows[0]["primitive_spec_sha256"]
    assert rows[0]["brace_spec_readback_found"] is True
    assert rows[0]["primitive_spec_readback_found"] is True
    native = next(
        item
        for item in pptx_arrows.read_pptx_inventory(run.pptx_path)
        if item["shape_id"] == rows[0]["shape_id"]
        and item["shape_name"] == rows[0]["shape_name"]
    )
    description = json.loads(native["description"])
    assert description["brace_spec_sha256"] == rows[0]["brace_spec_sha256"]
    assert description["primitive_spec_sha256"] == rows[0]["brace_spec_sha256"]


@pytest.mark.parametrize(
    ("target", "record_field"),
    [
        ("svg-coordinate", "source_path_pass"),
        ("scene-coordinate", "scene_geometry_pass"),
        ("scene-signature", "scene_geometry_signature_pass"),
    ],
)
def test_source_geometry_drift_from_canonical_path_is_a_blocker(
    tmp_path: Path, target: str, record_field: str
):
    run = _canonical_run(tmp_path)
    if target == "svg-coordinate":
        tree = ET.parse(run.redraw_svg)
        brace = next(
            element for element in tree.getroot().iter() if element.get("id") == "answer-underbrace"
        )
        segments = parse_path_d(brace.get("d", ""))
        segments[0] = (segments[0][0], segments[0][1] + 0.02, segments[0][2])
        brace.set("d", segments_to_d(segments))
        tree.write(run.redraw_svg, encoding="utf-8", xml_declaration=True)
    else:
        scene = read_json(run.scene_path)
        element = next(item for item in scene["elements"] if item["id"] == "answer-underbrace")
        segments = parse_path_d(element["geometry"]["d"])
        if target == "scene-coordinate":
            segments[0] = (segments[0][0], segments[0][1] + 0.02, segments[0][2])
        else:
            assert target == "scene-signature"
            segments[1] = ("M", *segments[1][1:])
        element["geometry"]["d"] = segments_to_d(segments)
        write_json(run.scene_path, scene)

    report = audit_primitives(run)

    assert report["pass"] is False
    assert report["records"][0][record_field] is False
    assert any(item["code"] == "P11_SOURCE_PATH" for item in report["findings"])


def test_source_geometry_allows_at_most_point_zero_one_pixel_drift(tmp_path: Path):
    run = _canonical_run(tmp_path)
    tree = ET.parse(run.redraw_svg)
    brace = next(
        element for element in tree.getroot().iter() if element.get("id") == "answer-underbrace"
    )
    segments = parse_path_d(brace.get("d", ""))
    segments[0] = (segments[0][0], segments[0][1] + 0.009, segments[0][2])
    brace.set("d", segments_to_d(segments))
    tree.write(run.redraw_svg, encoding="utf-8", xml_declaration=True)

    report = audit_primitives(run)

    assert report["pass"] is True
    assert report["records"][0]["source_path_pass"] is True


def test_pptx_lookup_requires_exact_shape_id_and_name_pair(tmp_path: Path):
    run = _canonical_run(tmp_path)
    bindings = read_json(run.bindings_path)
    row = next(item for item in bindings["bindings"] if item["element_id"] == "answer-underbrace")
    row["shape_id"] += 1
    write_json(run.bindings_path, bindings)

    report = audit_primitives(run)

    assert report["pass"] is False
    assert report["records"][0]["native_identity_pass"] is False
    assert any(item["code"] == "P7" for item in report["findings"])


@pytest.mark.parametrize(
    ("mutation", "finding", "record_field"),
    [
        ("fill", "P8", "powerpoint_fill_pass"),
        ("stroke_color", "P9", "powerpoint_stroke_pass"),
        ("stroke_width", "P9", "powerpoint_stroke_pass"),
        ("missing_cap", "P9", "powerpoint_stroke_pass"),
    ],
)
def test_pptx_brace_style_mismatch_is_a_blocker(
    tmp_path: Path, mutation: str, finding: str, record_field: str
):
    run = _canonical_run(tmp_path)

    def mutate(_identity: ET.Element, properties: ET.Element) -> None:
        line = properties.find("a:ln", PPTX_NS)
        assert line is not None
        if mutation == "fill":
            no_fill = properties.find("a:noFill", PPTX_NS)
            assert no_fill is not None
            properties.remove(no_fill)
            solid = ET.Element(f"{{{A_NS}}}solidFill")
            ET.SubElement(solid, f"{{{A_NS}}}srgbClr", {"val": "FFFFFF"})
            properties.insert(0, solid)
        elif mutation == "stroke_color":
            color = line.find("./a:solidFill/a:srgbClr", PPTX_NS)
            assert color is not None
            color.set("val", "FF0000")
        elif mutation == "stroke_width":
            line.set("w", "19050")
        else:
            assert mutation == "missing_cap"
            line.attrib.pop("cap", None)

    _rewrite_brace_ooxml(run.pptx_path, mutate)
    report = audit_primitives(run)

    assert report["pass"] is False
    assert report["records"][0][record_field] is False
    assert any(item["code"] == finding for item in report["findings"])


@pytest.mark.parametrize("hash_location", ["binding", "description"])
def test_primitive_spec_hash_mismatch_is_a_blocker(tmp_path: Path, hash_location: str):
    run = _canonical_run(tmp_path)
    if hash_location == "binding":
        bindings = read_json(run.bindings_path)
        row = next(
            item for item in bindings["bindings"] if item["element_id"] == "answer-underbrace"
        )
        row["primitive_spec_sha256"] = "0" * 64
        write_json(run.bindings_path, bindings)
    else:

        def mutate(identity: ET.Element, _properties: ET.Element) -> None:
            payload = json.loads(identity.get("descr", "{}"))
            payload["primitive_spec_sha256"] = "0" * 64
            identity.set("descr", json.dumps(payload, sort_keys=True, separators=(",", ":")))

        _rewrite_brace_ooxml(run.pptx_path, mutate)

    report = audit_primitives(run)

    assert report["pass"] is False
    record = report["records"][0]
    assert record[f"{'binding' if hash_location == 'binding' else 'embedded'}_hash_pass"] is False
    assert any(item["code"] == "P10" for item in report["findings"])
    assert any(item["code"] == "P16_BRACE_SPEC_ALIAS" for item in report["findings"])


@pytest.mark.parametrize("hash_location", ["binding", "description"])
def test_brace_spec_hash_mismatch_is_a_blocker(tmp_path: Path, hash_location: str):
    run = _canonical_run(tmp_path)
    if hash_location == "binding":
        bindings = read_json(run.bindings_path)
        row = next(
            item for item in bindings["bindings"] if item["element_id"] == "answer-underbrace"
        )
        row["brace_spec_sha256"] = "0" * 64
        write_json(run.bindings_path, bindings)
    else:

        def mutate(identity: ET.Element, _properties: ET.Element) -> None:
            payload = json.loads(identity.get("descr", "{}"))
            payload["brace_spec_sha256"] = "0" * 64
            identity.set("descr", json.dumps(payload, sort_keys=True, separators=(",", ":")))

        _rewrite_brace_ooxml(run.pptx_path, mutate)

    report = audit_primitives(run)

    assert report["pass"] is False
    record = report["records"][0]
    assert record[
        f"{'binding' if hash_location == 'binding' else 'embedded'}_brace_spec_hash_pass"
    ] is False
    assert any(item["code"] == "P10" for item in report["findings"])
    assert any(item["code"] == "P16_BRACE_SPEC_ALIAS" for item in report["findings"])


def test_legacy_scene_primitive_spec_alias_is_a_fail_closed_migration(tmp_path: Path):
    run = _canonical_run(tmp_path)
    scene = read_json(run.scene_path)
    element = next(item for item in scene["elements"] if item["id"] == "answer-underbrace")
    element.pop("brace_spec")
    write_json(run.scene_path, scene)

    report = audit_primitives(run)

    assert report["pass"] is False
    record = report["records"][0]
    assert record["scene_pass"] is True
    assert record["scene_brace_spec_hash_pass"] is False
    assert record["source_path_pass"] is True
    assert record["powerpoint_path_pass"] is True
    assert record["brace_spec_migration_locations"] == ["scene"]
    assert any(item["code"] == "P15_BRACE_SPEC_MIGRATION" for item in report["findings"])
    assert not any(item["code"] == "P4" for item in report["findings"])
    assert "primitive:P15_BRACE_SPEC_MIGRATION:answer-underbrace" in strict_blockers(
        report
    )


@pytest.mark.parametrize("location", ["binding", "description"])
def test_legacy_hash_alias_is_a_fail_closed_migration(tmp_path: Path, location: str):
    run = _canonical_run(tmp_path)
    if location == "binding":
        bindings = read_json(run.bindings_path)
        row = next(
            item for item in bindings["bindings"] if item["element_id"] == "answer-underbrace"
        )
        row.pop("brace_spec_sha256")
        write_json(run.bindings_path, bindings)
    else:

        def mutate(identity: ET.Element, _properties: ET.Element) -> None:
            payload = json.loads(identity.get("descr", "{}"))
            payload.pop("brace_spec_sha256")
            identity.set("descr", json.dumps(payload, sort_keys=True, separators=(",", ":")))

        _rewrite_brace_ooxml(run.pptx_path, mutate)

    report = audit_primitives(run)

    assert report["pass"] is False
    record = report["records"][0]
    record_location = "bindings" if location == "binding" else "pptx-description"
    assert record["brace_spec_migration_locations"] == [record_location]
    assert record[f"{'binding' if location == 'binding' else 'embedded'}_hash_pass"] is True
    assert any(item["code"] == "P15_BRACE_SPEC_MIGRATION" for item in report["findings"])
    assert not any(item["code"] == "P10" for item in report["findings"])


def test_scene_brace_spec_and_legacy_alias_hashes_must_match(tmp_path: Path):
    run = _canonical_run(tmp_path)
    scene = read_json(run.scene_path)
    element = next(item for item in scene["elements"] if item["id"] == "answer-underbrace")
    element["primitive_spec"] = {"stale": True}
    write_json(run.scene_path, scene)

    report = audit_primitives(run)

    assert report["pass"] is False
    record = report["records"][0]
    assert record["scene_brace_spec_hash_pass"] is True
    assert record["scene_alias_hash_match"] is False
    assert record["scene_pass"] is False
    assert record["brace_spec_alias_mismatch_locations"] == ["scene"]
    assert any(item["code"] == "P16_BRACE_SPEC_ALIAS" for item in report["findings"])


def test_canonical_brace_spec_does_not_require_legacy_aliases(tmp_path: Path):
    run = _canonical_run(tmp_path)
    scene = read_json(run.scene_path)
    element = next(item for item in scene["elements"] if item["id"] == "answer-underbrace")
    element.pop("primitive_spec")
    write_json(run.scene_path, scene)
    bindings = read_json(run.bindings_path)
    row = next(
        item for item in bindings["bindings"] if item["element_id"] == "answer-underbrace"
    )
    row.pop("primitive_spec_sha256")
    write_json(run.bindings_path, bindings)

    def mutate(identity: ET.Element, _properties: ET.Element) -> None:
        payload = json.loads(identity.get("descr", "{}"))
        payload.pop("primitive_spec_sha256")
        identity.set("descr", json.dumps(payload, sort_keys=True, separators=(",", ":")))

    _rewrite_brace_ooxml(run.pptx_path, mutate)

    report = audit_primitives(run)

    assert report["pass"] is True
    record = report["records"][0]
    assert record["scene_brace_spec_hash_pass"] is True
    assert record["binding_brace_spec_hash_pass"] is True
    assert record["embedded_brace_spec_hash_pass"] is True
    assert record["brace_spec_migration_locations"] == []
    assert record["brace_spec_alias_mismatch_locations"] == []


def test_unavailable_native_readback_is_a_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run = _canonical_run(tmp_path)

    def unavailable(_path: Path):
        raise ValueError("readback unavailable")

    monkeypatch.setattr(pptx_arrows, "read_pptx_inventory", unavailable)
    report = audit_primitives(run)

    assert report["pass"] is False
    assert any(item["code"] == "P0" for item in report["findings"])


def test_single_lobe_u_shape_is_rejected_before_white_background_can_hide_it(tmp_path: Path):
    run = _run(
        tmp_path,
        '<path id="bad-underbrace" d="M20 40 V44 Q20 49 26 49 H121 Q127 49 127 44 V40" '
        'fill="none" stroke="#000000"/>',
    )
    report = audit_primitives(run)
    assert report["pass"] is False
    assert report["findings"][0]["code"] == "P1"


def test_declared_brace_inventory_fails_when_a_required_primitive_is_missing(tmp_path: Path):
    run = _canonical_run(tmp_path)
    regions = read_json(run.regions_path)
    regions["primitive_expectations"] = [
        {
            "kind": "brace",
            "count": 2,
            "primitives": [
                {
                    "element_id": "answer-underbrace",
                    "primitive_spec_sha256": read_json(run.bindings_path)["bindings"][0][
                        "primitive_spec_sha256"
                    ],
                },
                {
                    "element_id": "missing-side-brace",
                    "primitive_spec_sha256": "0" * 64,
                },
            ],
        }
    ]
    write_json(run.regions_path, regions)

    report = audit_primitives(run)

    assert report["pass"] is False
    assert report["expectations"][0]["actual_count"] == 1
    assert report["expectations"][0]["missing_element_ids"] == ["missing-side-brace"]
    assert any(item["code"] == "P12_EXPECTATION" for item in report["findings"])


def test_declared_brace_inventory_requires_exact_ids_even_when_count_matches(tmp_path: Path):
    run = _canonical_run(tmp_path)
    regions = read_json(run.regions_path)
    regions["primitive_expectations"] = [
        {
            "kind": "brace",
            "count": 1,
            "primitives": [
                {
                    "element_id": "different-brace",
                    "primitive_spec_sha256": "0" * 64,
                }
            ],
        }
    ]
    write_json(run.regions_path, regions)

    report = audit_primitives(run)

    expectation = report["expectations"][0]
    assert expectation["actual_count"] == 1
    assert expectation["missing_element_ids"] == ["different-brace"]
    assert expectation["unexpected_element_ids"] == ["answer-underbrace"]
    assert report["pass"] is False


def test_brace_inventory_freezes_each_canonical_primitive_spec_hash(tmp_path: Path):
    run = _canonical_run(tmp_path)
    tree = ET.parse(run.redraw_svg)
    brace = next(
        element for element in tree.getroot().iter() if element.get("id") == "answer-underbrace"
    )
    brace.set("data-brace-terminal", "3")
    brace.set(
        "d",
        segments_to_d(
            canonical_brace_segments(
                20, 40, 107, 16, "under", cusp_offset=53.5, terminal=3
            )
        ),
    )
    tree.write(run.redraw_svg, encoding="utf-8", xml_declaration=True)

    report = audit_primitives(run)

    assert report["pass"] is False
    expectation = report["expectations"][0]
    assert expectation["hash_mismatches"] == ["answer-underbrace"]
    assert any(item["code"] == "P12_EXPECTATION" for item in report["findings"])


def test_brace_without_exact_case_owned_expectation_fails_closed(tmp_path: Path):
    run = _canonical_run(tmp_path)
    regions = read_json(run.regions_path)
    regions["primitive_expectations"] = []
    write_json(run.regions_path, regions)

    report = audit_primitives(run)

    assert report["pass"] is False
    assert any(
        item["code"] == "P12_EXPECTATION" and item["element"] == "[brace]"
        for item in report["findings"]
    )


def test_visually_two_lobed_but_off_center_cusp_fails_symmetry_gate(tmp_path: Path):
    run = _canonical_run(tmp_path)
    tree = ET.parse(run.redraw_svg)
    brace = next(
        element for element in tree.getroot().iter() if element.get("id") == "answer-underbrace"
    )
    segments = parse_path_d(brace.get("d", ""))
    segments[5] = (segments[5][0], segments[5][1] + 0.5, segments[5][2])
    segments[6] = (segments[6][0], segments[6][1] + 0.5, segments[6][2])
    brace.set("d", segments_to_d(segments))
    tree.write(run.redraw_svg, encoding="utf-8", xml_declaration=True)

    report = audit_primitives(run)

    assert report["pass"] is False
    record = report["records"][0]
    assert record["symmetry_contract"]["central_cusp_pass"] is False
    assert record["symmetry_contract"]["double_lobe_mirror_pass"] is False
    assert any(item["code"] == "P14_SYMMETRY" for item in report["findings"])


def test_cusp_center_tolerance_is_both_absolute_and_normalized():
    canonical_brace_segments(
        0,
        0,
        107,
        16,
        "under",
        cusp_offset=53.505,
    )
    with pytest.raises(ValueError, match="brace cusp must be centered"):
        canonical_brace_segments(
            0,
            0,
            107,
            16,
            "under",
            cusp_offset=53.511,
        )
    with pytest.raises(ValueError, match="normalized error"):
        canonical_brace_segments(
            0,
            0,
            20,
            10,
            "under",
            cusp_offset=10.005,
            outer_radius=1,
            cusp_radius=1,
        )


def test_invalid_or_unsupported_primitive_expectation_fails_closed(tmp_path: Path):
    run = _canonical_run(tmp_path)
    regions = read_json(run.regions_path)
    regions["primitive_expectations"] = [
        {
            "kind": "icon",
            "count": 1,
            "primitives": [
                {"element_id": "reward-icon", "primitive_spec_sha256": "0" * 64}
            ],
        }
    ]
    write_json(run.regions_path, regions)

    report = audit_primitives(run)

    assert report["pass"] is False
    assert report["expectations"][0]["pass"] is False
    assert any(
        "unsupported primitive expectation kind" in item["message"]
        for item in report["findings"]
    )


def test_materializer_replaces_handwritten_path_with_canonical_contract():
    element = ET.fromstring(
        '<path id="brace" d="M0 0 L1 1" stroke="#000000" stroke-width="1" '
        'data-primitive-kind="brace" data-brace-orientation="right" '
        'data-brace-x="10" data-brace-y="20" data-brace-length="77" '
        'data-brace-depth="16" data-brace-cusp-offset="38.5"/>'
    )
    spec = materialize_brace(element)
    assert spec is not None
    assert _command_signature(parse_path_d(element.get("d", ""))) == "MLCLCLMLCLCL"


@pytest.mark.parametrize(
    "transform",
    [
        "matrix(1 0.25 0 1 0 0)",
        "scale(1 -1)",
        "scale(2 1)",
    ],
)
def test_brace_transform_cannot_skew_flip_or_nonuniformly_scale_declared_orientation(
    tmp_path: Path, transform: str
):
    d = segments_to_d(
        canonical_brace_segments(20, 40, 107, 16, "under", cusp_offset=53.5)
    )
    run = _run(
        tmp_path,
        f'<path id="transformed-underbrace" transform="{transform}" d="{d}" '
        'fill="none" stroke="#000000" stroke-width="1" stroke-linecap="round" '
        'data-primitive-kind="brace" data-brace-orientation="under" '
        'data-brace-x="20" data-brace-y="40" data-brace-length="107" '
        'data-brace-depth="16" data-brace-cusp-offset="53.5"/>',
    )

    report = audit_primitives(run)

    assert report["pass"] is False
    assert any(item["code"] == "P13_TRANSFORM" for item in report["findings"])
