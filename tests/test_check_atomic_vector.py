"""atomic-vector(vtracer-trace)资产 QA 门禁单元测试(合成文档,不触 OCR/PowerPoint)。"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from tools.pipeline.check import (
    ATOMIC_VECTOR_SSIM_FLOOR,
    _pptx_shape_metadata,
    audit_atomic_vector_qa,
)

VECTOR_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30" '
    'viewBox="0 0 40 30"><path d="M 0 0 L 40 0 L 40 30 L 0 30 Z" fill="#3366CC"/></svg>'
)
VECTOR_SVG_REL_PATH = "assets/atomic-globe-vector.svg"
VECTOR_ID = "atomic:globe-vector"
RASTER_ID = "atomic:globe"
REGION_ID = "globe-region"
SHAPE_NAME = "af-atomic-globe-vector-atomic-vector-01"


def _write_vector(case_root: Path, svg: str = VECTOR_SVG) -> dict[str, str]:
    path = case_root.joinpath(*VECTOR_SVG_REL_PATH.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return {
        "path": VECTOR_SVG_REL_PATH,
        "sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
    }


def _base_docs(tmp_path: Path) -> dict:
    case_root = tmp_path / "case"
    case_root.mkdir(exist_ok=True)
    vector_source = _write_vector(case_root)
    sha256 = vector_source["sha256"]
    raster = {
        "id": RASTER_ID,
        "authorized": True,
        "authorization_basis": "user supplied reference crop",
        "rights_status": "unknown",
        "editable": False,
        "source": "reference_crop",
        "source_sha256": "a" * 64,
        "bbox": [10, 10, 40, 30],
    }
    vector = {
        "id": VECTOR_ID,
        "editable": True,
        "source": "vtracer-trace",
        "vector_source_svg": vector_source,
        "trace_method": "vtracer-color-stacked-spline",
        "trace_engine_version": "0.6.15",
        "authorization_basis": "user supplied reference crop",
        "rights_status": "unknown",
        "fallback_atomic_raster": RASTER_ID,
        "ink_contract_region_id": REGION_ID,
        "trace_eligibility": "flat-illustration",
    }
    assets = {
        "kind": "assets",
        "reference_sha256": "b" * 64,
        "policy": {},
        "assets": [raster, vector],
        "microasset_opportunity_map": [],
    }
    bindings = {
        "bindings": [
            {
                "element_id": VECTOR_ID,
                "shape_id": 7,
                "shape_name": SHAPE_NAME,
                "object_kind": "atomic-vector",
                "editable": True,
            }
        ],
        "logical_group_bindings": [],
    }
    regions_payload = {
        "regions": [
            {
                "id": REGION_ID,
                "bbox": [10, 10, 40, 30],
                "ink_contract": {"background_rgb": [255, 255, 255]},
                "color_probes": [{"id": "p1", "point": [12, 12], "max_delta_e": 5}],
            }
        ]
    }
    regions_report = {
        "regions": [
            {
                "id": REGION_ID,
                "ssim": 0.82,
                "edge_iou": 0.8,
                "ink_contract": {"pass": True},
                "color_probes": [{"id": "p1", "pass": True}],
            }
        ]
    }
    provenance = {
        "kind": "provenance",
        "candidate_history": [
            {
                "kind": "svg",
                "role": "reconstruction-candidate",
                "origin": "vtracer-provider",
                "sha256": sha256,
                "trace_engine_version": "0.6.15",
                "parameters": {"mode": "spline", "colormode": "color"},
                "ingested_at": "20260826T000000Z",
            }
        ],
    }
    shape_metadata = {
        (7, SHAPE_NAME): {
            "shape_kind": "grpSp",
            "tags": {
                "AISCIENTIFICILLUSTRATORASSETID": VECTOR_ID,
                "AISCIENTIFICILLUSTRATORSOURCESHA256": sha256,
                "AISCIENTIFICILLUSTRATOREDITABLE": "True",
                "AISCIENTIFICILLUSTRATORORIGIN": "vtracer-provider",
            },
        }
    }
    return {
        "case_root": case_root,
        "assets": assets,
        "bindings": bindings,
        "regions_payload": regions_payload,
        "regions_report": regions_report,
        "provenance": provenance,
        "shape_metadata": shape_metadata,
        "vector_sha256": sha256,
    }


def _audit(docs: dict) -> dict:
    return audit_atomic_vector_qa(
        docs["case_root"],
        assets=docs["assets"],
        bindings=docs["bindings"],
        regions_payload=docs["regions_payload"],
        regions_report=docs["regions_report"],
        provenance=docs["provenance"],
        shape_metadata=docs["shape_metadata"],
    )


def _vector_entry(docs: dict) -> dict:
    return docs["assets"]["assets"][1]


def test_pass_all_gates(tmp_path: Path):
    docs = _base_docs(tmp_path)
    report = _audit(docs)
    assert report["blockers"] == []
    assert report["pass"] is True
    assert report["asset_count"] == 1
    assert report["assets"][0]["pass"] is True
    assert report["assets"][0]["gates"]["fallback"] == []


def test_no_vector_entries_is_zero_regression(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["assets"]["assets"] = docs["assets"]["assets"][:1]
    report = _audit(docs)
    assert report["asset_count"] == 0
    assert report["blockers"] == []
    assert report["pass"] is True


def test_contract_field_violation_fails_closed(tmp_path: Path):
    docs = _base_docs(tmp_path)
    del _vector_entry(docs)["trace_method"]
    report = _audit(docs)
    assert f"atomic-vector-asset:{VECTOR_ID}:fields" in report["blockers"]
    assert f"atomic-vector-asset:{VECTOR_ID}:trace-method" in report["blockers"]
    assert f"atomic-vector:{VECTOR_ID}:fallback-required:{RASTER_ID}" in report["blockers"]
    # 合同失败时下游门禁不求值,避免噪声
    assert report["assets"][0]["gates"]["nativeness"] == []


def test_fallback_unresolved(tmp_path: Path):
    docs = _base_docs(tmp_path)
    _vector_entry(docs)["fallback_atomic_raster"] = "atomic:missing"
    report = _audit(docs)
    assert f"atomic-vector-asset:{VECTOR_ID}:fallback-unresolved" in report["blockers"]
    assert f"atomic-vector:{VECTOR_ID}:fallback-required:atomic:missing" in report["blockers"]


def test_binding_missing(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["bindings"]["bindings"] = []
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:binding-missing" in report["blockers"]
    assert f"atomic-vector:{VECTOR_ID}:fallback-required:{RASTER_ID}" in report["blockers"]


def test_raster_binding_kind_and_picture_rejected(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["bindings"]["bindings"][0]["object_kind"] = "atomic-raster"
    docs["bindings"]["bindings"][0]["editable"] = False
    docs["shape_metadata"][(7, SHAPE_NAME)] = {"shape_kind": "pic", "tags": {}}
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:binding-kind:atomic-raster" in report["blockers"]
    assert f"atomic-vector:{VECTOR_ID}:binding-not-editable" in report["blockers"]
    assert f"atomic-vector:{VECTOR_ID}:picture-binding:{SHAPE_NAME}" in report["blockers"]
    assert f"atomic-vector:{VECTOR_ID}:tags-incomplete:{SHAPE_NAME}" in report["blockers"]


def test_tags_incomplete(tmp_path: Path):
    docs = _base_docs(tmp_path)
    del docs["shape_metadata"][(7, SHAPE_NAME)]["tags"]["AISCIENTIFICILLUSTRATORORIGIN"]
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:tags-incomplete:{SHAPE_NAME}" in report["blockers"]


def test_shape_unresolved(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["shape_metadata"] = {}
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:shape-unresolved:{SHAPE_NAME}" in report["blockers"]


def test_logical_group_binding_path(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["bindings"] = {
        "bindings": [],
        "logical_group_bindings": [
            {
                "element_id": VECTOR_ID,
                "object_kind": "atomic-vector",
                "editable": True,
                "backend_object_identities": [{"shape_id": 9, "shape_name": "member-1"}],
                "attachment_shape_id": 9,
                "attachment_shape_name": "member-1",
            }
        ],
    }
    sha256 = docs["vector_sha256"]
    docs["shape_metadata"] = {
        (9, "member-1"): {
            "shape_kind": "sp",
            "tags": {
                "AISCIENTIFICILLUSTRATORASSETID": VECTOR_ID,
                "AISCIENTIFICILLUSTRATORSOURCESHA256": sha256,
                "AISCIENTIFICILLUSTRATOREDITABLE": "True",
                "AISCIENTIFICILLUSTRATORORIGIN": "vtracer-provider",
            },
        }
    }
    report = _audit(docs)
    assert report["blockers"] == []


def test_nativeness_linked_via_fallback_raster_id(tmp_path: Path):
    """链接场景:convert 以场景元素 id(位图 id)登记 atomic-vector 绑定行。"""
    docs = _base_docs(tmp_path)
    docs["bindings"]["bindings"][0]["element_id"] = RASTER_ID
    report = _audit(docs)
    assert report["blockers"] == []
    assert report["pass"] is True
    assert report["assets"][0]["gates"]["nativeness"] == []


def test_region_missing(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["regions_payload"]["regions"] = []
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:region-missing:{REGION_ID}" in report["blockers"]


def test_ink_contract_must_be_declared_and_pass(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["regions_payload"]["regions"][0]["ink_contract"] = None
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:ink-contract-missing:{REGION_ID}" in report["blockers"]

    docs = _base_docs(tmp_path)
    docs["regions_report"]["regions"][0]["ink_contract"] = {"pass": False}
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:ink-contract-failed:{REGION_ID}" in report["blockers"]


def test_ssim_floor_is_vector_tier(tmp_path: Path):
    assert ATOMIC_VECTOR_SSIM_FLOOR == 0.80
    docs = _base_docs(tmp_path)
    docs["regions_report"]["regions"][0]["ssim"] = 0.79
    report = _audit(docs)
    assert any(
        blocker.startswith(f"atomic-vector:{VECTOR_ID}:ssim:")
        for blocker in report["blockers"]
    )
    docs = _base_docs(tmp_path)
    docs["regions_report"]["regions"][0]["ssim"] = 0.80
    assert _audit(docs)["blockers"] == []


def test_edge_iou_floor(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["regions_report"]["regions"][0]["edge_iou"] = 0.74
    report = _audit(docs)
    assert any(
        blocker.startswith(f"atomic-vector:{VECTOR_ID}:edge-iou:")
        for blocker in report["blockers"]
    )


def test_color_probe_failure(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["regions_report"]["regions"][0]["color_probes"] = [{"id": "p1", "pass": False}]
    report = _audit(docs)
    assert (
        f"atomic-vector:{VECTOR_ID}:color-probe:{REGION_ID}:p1" in report["blockers"]
    )


def test_provenance_record_required(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["provenance"]["candidate_history"] = []
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:provenance-missing" in report["blockers"]


def test_provenance_engine_version_mismatch(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["provenance"]["candidate_history"][0]["trace_engine_version"] = "0.6.14"
    report = _audit(docs)
    assert (
        f"atomic-vector:{VECTOR_ID}:provenance-engine-version" in report["blockers"]
    )


def test_provenance_parameters_required(tmp_path: Path):
    docs = _base_docs(tmp_path)
    del docs["provenance"]["candidate_history"][0]["parameters"]
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:provenance-parameters" in report["blockers"]


def test_vector_source_file_missing(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["case_root"].joinpath(*VECTOR_SVG_REL_PATH.split("/")).unlink()
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:vector-source-missing" in report["blockers"]


def test_vector_source_hash_drift_rejected(tmp_path: Path):
    docs = _base_docs(tmp_path)
    path = docs["case_root"].joinpath(*VECTOR_SVG_REL_PATH.split("/"))
    path.write_text(VECTOR_SVG.replace("#3366CC", "#CC3366"), encoding="utf-8")
    report = _audit(docs)
    assert (
        f"atomic-vector:{VECTOR_ID}:vector-source-hash-mismatch" in report["blockers"]
    )


def test_contract_subset_violation_rejected(tmp_path: Path):
    docs = _base_docs(tmp_path)
    bad_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30" '
        'viewBox="0 0 40 30"><image href="x.png"/></svg>'
    )
    vector_source = _write_vector(docs["case_root"], bad_svg)
    _vector_entry(docs)["vector_source_svg"] = vector_source
    docs["provenance"]["candidate_history"][0]["sha256"] = vector_source["sha256"]
    docs["shape_metadata"][(7, SHAPE_NAME)]["tags"][
        "AISCIENTIFICILLUSTRATORSOURCESHA256"
    ] = vector_source["sha256"]
    report = _audit(docs)
    assert (
        f"atomic-vector:{VECTOR_ID}:contract-subset:forbidden-element:image"
        in report["blockers"]
    )


def test_fallback_active_is_deviation_when_vector_passes(tmp_path: Path):
    docs = _base_docs(tmp_path)
    docs["bindings"]["bindings"].append(
        {
            "element_id": RASTER_ID,
            "shape_id": 8,
            "shape_name": "af-atomic-globe-atomic-raster-01",
            "object_kind": "atomic-raster",
            "editable": False,
        }
    )
    report = _audit(docs)
    assert f"atomic-vector:{VECTOR_ID}:fallback-active:{RASTER_ID}" in report["blockers"]


def test_fallback_active_with_linked_vector_binding(tmp_path: Path):
    """链接场景:矢量绑定行与仍活跃的位图行同登记在位图 id 下。"""
    docs = _base_docs(tmp_path)
    docs["bindings"]["bindings"][0]["element_id"] = RASTER_ID
    docs["bindings"]["bindings"].append(
        {
            "element_id": RASTER_ID,
            "shape_id": 8,
            "shape_name": "af-atomic-globe-atomic-raster-01",
            "object_kind": "atomic-raster",
            "editable": False,
        }
    )
    report = _audit(docs)
    # 位图行不污染矢量原生性判定,由 fallback 门禁单独报表示分歧。
    assert report["assets"][0]["gates"]["nativeness"] == []
    assert report["assets"][0]["gates"]["fallback"] == [
        f"atomic-vector:{VECTOR_ID}:fallback-active:{RASTER_ID}"
    ]
    assert f"atomic-vector:{VECTOR_ID}:fallback-active:{RASTER_ID}" in report["blockers"]


def test_pptx_shape_metadata_reads_kinds_and_tags(tmp_path: Path):
    pptx = tmp_path / "deck.pptx"
    slide = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<p:cSld><p:spTree>"
        '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="7" name="vec-group"/><p:cNvGrpSpPr/>'
        '<p:nvPr><p:custDataLst><p:tags r:id="rId5"/></p:custDataLst></p:nvPr>'
        "</p:nvGrpSpPr><p:spPr/></p:grpSp>"
        '<p:pic><p:nvPicPr><p:cNvPr id="8" name="raster-pic"/><p:cNvPicPr/><p:nvPr/>'
        "</p:nvPicPr><p:blipFill/><p:spPr/></p:pic>"
        "</p:spTree></p:cSld></p:sld>"
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId5" Target="../tags/tag1.xml" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/tags"/>'
        "</Relationships>"
    )
    tags = (
        '<p:tagLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:tag name="AISCIENTIFICILLUSTRATORASSETID" val="atomic:globe-vector"/>'
        '<p:tag name="AISCIENTIFICILLUSTRATORORIGIN" val="vtracer-provider"/>'
        "</p:tagLst>"
    )
    with zipfile.ZipFile(pptx, "w") as package:
        package.writestr("ppt/slides/slide1.xml", slide)
        package.writestr("ppt/slides/_rels/slide1.xml.rels", rels)
        package.writestr("ppt/tags/tag1.xml", tags)

    metadata = _pptx_shape_metadata(pptx)

    assert metadata[(7, "vec-group")]["shape_kind"] == "grpSp"
    assert metadata[(7, "vec-group")]["tags"] == {
        "AISCIENTIFICILLUSTRATORASSETID": "atomic:globe-vector",
        "AISCIENTIFICILLUSTRATORORIGIN": "vtracer-provider",
    }
    assert metadata[(8, "raster-pic")]["shape_kind"] == "pic"
    assert metadata[(8, "raster-pic")]["tags"] == {}
