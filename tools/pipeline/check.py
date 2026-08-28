"""autofigure check — pixel diagnostics plus structural and evidence gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from tools.core import common

SVG_NS = "{http://www.w3.org/2000/svg}"
PADDLE_PYTHON = Path(r"D:\paddle ocr\env\python.exe")
OCR_CONFIG = common.PROJECT_ROOT / "config" / "ocr-config.json"
FIGURE_LINT = common.PROJECT_ROOT / "tools" / "regions" / "figure_lint.py"


def _source_gate_blockers(run: common.Run) -> list[str]:
    """Close the admitted source to the current canonical scene carrier."""

    from tools.core.contracts import read_json

    if not run.source_gate_report_path.is_file():
        return ["source-gate:missing"]
    try:
        report = read_json(run.source_gate_report_path)
    except Exception:
        return ["source-gate:invalid"]
    meta = run.load_meta()
    blockers: list[str] = []
    if report.get("schema_version") != "4.0.0" or report.get("kind") != "source_gate_report":
        blockers.append("source-gate:invalid")
    route = report.get("route_gate", {})
    if route.get("input_route") != meta.get("input_route"):
        blockers.append("source-gate:route-mismatch")
    reference = report.get("reference", {})
    if (
        reference.get("expected_sha256") != meta.get("source_sha256")
        or reference.get("actual_sha256") != meta.get("source_sha256")
    ):
        blockers.append("source-gate:reference-mismatch")
    receipt_path = run.qa_dir / "reference-inventory-receipt.json"
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        if report.get("reference_inventory_sha256") != receipt.get("inventory_sha256"):
            blockers.append("source-gate:inventory-mismatch")
        from tools.assets.reference_oracle import oracle_path, oracle_sha256

        case_oracle = oracle_path(run)
        if case_oracle.is_file():
            try:
                actual_oracle_sha256 = oracle_sha256(read_json(case_oracle))
            except Exception:
                actual_oracle_sha256 = None
            if receipt.get("oracle_sha256") != actual_oracle_sha256:
                blockers.append("oracle:receipt-mismatch")
    else:
        blockers.append("reference-inventory:receipt-missing")
    scene = read_json(run.scene_path)
    carrier = scene.get("canonical_svg", {})
    if carrier.get("sha256") != report.get("candidate", {}).get("sha256"):
        blockers.append("source-gate:candidate-mismatch")
    # 载体-文件门禁：redraw.svg 必须逐字节等于 scene.canonical 绑定的载体。
    # 历史缺口（01/02 案例修复流程更新 redraw.svg 后未 rebind scene）由此拦截。
    if run.redraw_svg.is_file():
        if common.sha256_file(run.redraw_svg) != carrier.get("sha256"):
            blockers.append("scene:carrier-redraw-mismatch")
    # 载体-内容门禁：canonical_svg.content 内联文本必须满足自身哈希合同
    # （canonical_svg_text 的 fail-closed 前置）。#26 的 renormalize 修复曾只改
    # sha256 不重写 content，留下 file==sha 而 content 陈旧的内部不一致场景，
    # 会在下一次 compile/materialize 时才爆炸——此门禁把检出点提前到 check。
    # 仅拦"存在但不匹配"；content 缺失（极简 carrier，如候选未物化）留给
    # canonical_svg_text 在消费点 fail-closed，renormalize 可从 file 补全。
    carrier_content = carrier.get("content") if isinstance(carrier, dict) else None
    if isinstance(carrier_content, str) and carrier.get("sha256") and (
        hashlib.sha256(carrier_content.encode("utf-8")).hexdigest()
        != carrier.get("sha256")
    ):
        blockers.append("scene:carrier-content-mismatch")
    decision = report.get("decision")
    if decision != "accept":
        reported = report.get("blockers")
        if isinstance(reported, list) and reported:
            blockers.extend(item for item in reported if isinstance(item, str))
        else:
            blockers.append(f"source-gate:decision:{decision or 'missing'}")
    return list(dict.fromkeys(blockers))


def _qa_report_hashes(run: common.Run) -> dict[str, str]:
    # qa-status.json 不在名单内：它由 check 在 repair plan 封存之后重写，其内容
    # 哈希反向绑定 repair-plan.json；把它列入会使 plan 与 status 互相哈希追逐，
    # 每次 check 都漂移。qa-status.json 的内容哈希由 release-manifest.json 绑定。
    names = (
        "regions-report.json",
        "layout-audit.json",
        "arrow-visual-report.json",
        "arrow-compile-report.json",
        "powerpoint-arrow-readback.json",
        "primitive-audit.json",
        "asset-spec-audit.json",
        "asset-contract-receipt.json",
        "visual-contracts-report.json",
        "math-summary.json",
        "live-evidence.json",
        "atomic-vector-report.json",
    )
    return {
        name: common.sha256_file(run.qa_dir / name)
        for name in names
        if (run.qa_dir / name).is_file()
    }


# ---------------------------------------------------------------------------
# Atomic-vector (vtracer-trace) asset gates
# (docs/ILLUSTRATOR_VECTOR_AUTHORING_PLAN.md Phase 2 §3.4).  Each authorized
# traced asset is judged on five axes: assets.json contract, native binding,
# regional fidelity, provenance, and the fallback audit.  Any failure blocks
# and explicitly demands the documented atomic-raster fallback; check only
# judges and records, it never rewrites assets.json.

ATOMIC_VECTOR_REPORT_NAME = "atomic-vector-report.json"

# 初始值,逐案例 freeze 冻结;出处 docs/vtracer-pilot/README.md(平面插画类
# 描摹实测 SSIM 0.81–0.82)。
ATOMIC_VECTOR_SSIM_FLOOR = 0.80

_PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SLIDE_PART_RE = re.compile(r"ppt/slides/slide[0-9]+\.xml")
_NV_CONTAINER_NAMES = {
    "pic": "nvPicPr",
    "sp": "nvSpPr",
    "grpSp": "nvGrpSpPr",
    "cxnSp": "nvCxnSpPr",
    "graphicFrame": "nvGraphicFramePr",
}


def _pptx_shape_metadata(pptx_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    """Read every slide shape's OOXML container kind and PowerPoint Tags.

    The result keys shapes by the same ``(shape_id, shape_name)`` identity the
    compiler bindings use, so gates resolve one binding row to its saved
    package truth without PowerPoint.
    """

    metadata: dict[tuple[int, str], dict[str, Any]] = {}
    with zipfile.ZipFile(pptx_path) as package:
        part_names = set(package.namelist())
        for slide_name in sorted(
            name for name in part_names if _SLIDE_PART_RE.fullmatch(name)
        ):
            relationships: dict[str, str] = {}
            rels_name = f"ppt/slides/_rels/{slide_name.rsplit('/', 1)[-1]}.rels"
            if rels_name in part_names:
                rel_root = ET.fromstring(package.read(rels_name))
                for relationship in rel_root.iter(f"{{{_PACKAGE_REL_NS}}}Relationship"):
                    rel_id = relationship.get("Id")
                    target = relationship.get("Target")
                    if rel_id and target:
                        relationships[rel_id] = posixpath.normpath(
                            posixpath.join("ppt/slides", target)
                        )
            slide_root = ET.fromstring(package.read(slide_name))
            for shape in slide_root.iter():
                if not isinstance(shape.tag, str):
                    continue
                local_name = shape.tag.rsplit("}", 1)[-1]
                container_name = _NV_CONTAINER_NAMES.get(local_name)
                if container_name is None:
                    continue
                container = shape.find(f"{{{_PML_NS}}}{container_name}")
                if container is None:
                    continue
                c_nv_pr = container.find(f"{{{_PML_NS}}}cNvPr")
                if c_nv_pr is None:
                    continue
                try:
                    shape_id = int(c_nv_pr.get("id") or "")
                except ValueError:
                    continue
                shape_name = c_nv_pr.get("name") or ""
                tags: dict[str, str] = {}
                nv_pr = container.find(f"{{{_PML_NS}}}nvPr")
                tags_reference = (
                    nv_pr.find(f"{{{_PML_NS}}}custDataLst/{{{_PML_NS}}}tags")
                    if nv_pr is not None
                    else None
                )
                if tags_reference is not None:
                    rel_id = tags_reference.get(f"{{{_OFFICE_REL_NS}}}id")
                    part_name = relationships.get(rel_id or "")
                    if part_name in part_names:
                        tag_root = ET.fromstring(package.read(part_name))
                        for tag in tag_root.iter(f"{{{_PML_NS}}}tag"):
                            name = tag.get("name")
                            if name:
                                tags[name] = tag.get("val") or ""
                metadata[(shape_id, shape_name)] = {
                    "shape_kind": local_name,
                    "tags": tags,
                }
    return metadata


def _atomic_vector_expected_tags(entry: dict[str, Any]) -> dict[str, str]:
    """PowerPoint Tags contract for one atomic-vector shape.

    命名跟随 convert 的 atomic-raster Tags 分支(AISCIENTIFICILLUSTRATOR 前缀
    大写连写):资产身份、vector_source_svg 源哈希、editable=true 与 origin。
    """

    return {
        "AISCIENTIFICILLUSTRATORASSETID": entry["id"],
        "AISCIENTIFICILLUSTRATORSOURCESHA256": entry["vector_source_svg"]["sha256"],
        "AISCIENTIFICILLUSTRATOREDITABLE": "True",
        "AISCIENTIFICILLUSTRATORORIGIN": "vtracer-provider",
    }


def _binding_identity(row: dict[str, Any]) -> tuple[int, str] | None:
    shape_id = row.get("shape_id")
    shape_name = row.get("shape_name")
    if (
        isinstance(shape_id, bool)
        or not isinstance(shape_id, int)
        or not isinstance(shape_name, str)
    ):
        return None
    return (shape_id, shape_name)


def _atomic_vector_nativeness_blockers(
    entry: dict[str, Any],
    bindings: dict[str, Any],
    shape_metadata: dict[tuple[int, str], dict[str, Any]],
) -> list[str]:
    """② 原生性:原生 freeform/group 绑定、无 picture、Tags 完整且 editable。

    bindings 链接规则与 convert 一致:先按矢量条目 id 查 element_id,无命中
    再按 ``fallback_atomic_raster``(位图条目 id)查——链接场景下 convert 以
    场景元素 id(位图 id)登记 object_kind="atomic-vector" 的绑定行。位图 id
    下只认 object_kind="atomic-vector" 的行;同 element_id 的 atomic-raster
    行是位图层的独立绑定,其活跃性由 ⑤ 回退审计判定,不计入原生性。
    """

    asset_id = entry["id"]
    rows = [
        row
        for row in bindings.get("bindings", [])
        if isinstance(row, dict) and row.get("element_id") == asset_id
    ]
    group_rows = [
        row
        for row in bindings.get("logical_group_bindings", [])
        if isinstance(row, dict) and row.get("element_id") == asset_id
    ]
    if not rows and not group_rows:
        fallback_id = entry.get("fallback_atomic_raster")
        if isinstance(fallback_id, str) and fallback_id:
            rows = [
                row
                for row in bindings.get("bindings", [])
                if isinstance(row, dict)
                and row.get("element_id") == fallback_id
                and row.get("object_kind") == "atomic-vector"
            ]
            group_rows = [
                row
                for row in bindings.get("logical_group_bindings", [])
                if isinstance(row, dict)
                and row.get("element_id") == fallback_id
                and row.get("object_kind") == "atomic-vector"
            ]
    if not rows and not group_rows:
        return [f"atomic-vector:{asset_id}:binding-missing"]

    blockers: list[str] = []
    identities: list[tuple[int, str] | None] = []
    tag_carriers: list[tuple[int, str] | None] = []
    for row in [*rows, *group_rows]:
        kind = row.get("object_kind")
        if kind != "atomic-vector":
            blockers.append(f"atomic-vector:{asset_id}:binding-kind:{kind or '[missing]'}")
        if row.get("editable") is not True:
            blockers.append(f"atomic-vector:{asset_id}:binding-not-editable")
    for row in rows:
        identity = _binding_identity(row)
        identities.append(identity)
        tag_carriers.append(identity)
    for row in group_rows:
        members = [
            _binding_identity(item)
            for item in row.get("backend_object_identities", [])
            if isinstance(item, dict)
        ]
        identities.extend(members)
        attachment = _binding_identity(
            {
                "shape_id": row.get("attachment_shape_id"),
                "shape_name": row.get("attachment_shape_name"),
            }
        )
        identities.append(attachment)
        tag_carriers.append(attachment)
    identities = list(dict.fromkeys(identities))
    for identity in identities:
        if identity is None:
            blockers.append(f"atomic-vector:{asset_id}:shape-unresolved:[missing-identity]")
            continue
        info = shape_metadata.get(identity)
        if info is None:
            blockers.append(f"atomic-vector:{asset_id}:shape-unresolved:{identity[1]}")
        elif info.get("shape_kind") == "pic":
            blockers.append(f"atomic-vector:{asset_id}:picture-binding:{identity[1]}")

    expected_tags = _atomic_vector_expected_tags(entry)
    carriers = list(dict.fromkeys(item for item in tag_carriers if item is not None))
    if not carriers:
        blockers.append(f"atomic-vector:{asset_id}:tags-missing")
    for identity in carriers:
        info = shape_metadata.get(identity)
        if info is None:
            continue  # shape-unresolved 已在上面按 fail-closed 报告
        missing = {
            name: value
            for name, value in expected_tags.items()
            if info.get("tags", {}).get(name) != value
        }
        if missing:
            blockers.append(f"atomic-vector:{asset_id}:tags-incomplete:{identity[1]}")
    return blockers


def _atomic_vector_region_blockers(
    entry: dict[str, Any],
    regions_payload: dict[str, Any],
    regions_report: dict[str, Any],
    *,
    edge_iou_floor: float,
) -> list[str]:
    """③ 区域保真:紧边界 ink_contract + SSIM/Edge IoU 底线 + ΔE00 冻结探针。"""

    asset_id = entry["id"]
    region_id = entry["ink_contract_region_id"]
    definitions = {
        region.get("id"): region
        for region in regions_payload.get("regions", [])
        if isinstance(region, dict)
    }
    results = {
        region.get("id"): region
        for region in regions_report.get("regions", [])
        if isinstance(region, dict)
    }
    definition = definitions.get(region_id)
    result = results.get(region_id)
    if definition is None or result is None:
        return [f"atomic-vector:{asset_id}:region-missing:{region_id}"]
    blockers: list[str] = []
    if not isinstance(definition.get("ink_contract"), dict):
        blockers.append(f"atomic-vector:{asset_id}:ink-contract-missing:{region_id}")
    else:
        ink_contract = result.get("ink_contract")
        if not isinstance(ink_contract, dict) or ink_contract.get("pass") is not True:
            blockers.append(f"atomic-vector:{asset_id}:ink-contract-failed:{region_id}")
    ssim = result.get("ssim")
    if (
        not isinstance(ssim, (int, float))
        or isinstance(ssim, bool)
        or ssim < ATOMIC_VECTOR_SSIM_FLOOR
    ):
        blockers.append(f"atomic-vector:{asset_id}:ssim:{ssim}")
    edge_iou = result.get("edge_iou")
    if (
        not isinstance(edge_iou, (int, float))
        or isinstance(edge_iou, bool)
        or edge_iou < edge_iou_floor
    ):
        blockers.append(f"atomic-vector:{asset_id}:edge-iou:{edge_iou}")
    for probe in result.get("color_probes") or []:
        if isinstance(probe, dict) and probe.get("pass") is not True:
            blockers.append(
                f"atomic-vector:{asset_id}:color-probe:{region_id}:"
                f"{probe.get('id', '[missing-id]')}"
            )
    return blockers


def _provenance_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in document.values():
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    return records


def _atomic_vector_provenance_blockers(
    case_root: Path,
    entry: dict[str, Any],
    provenance: dict[str, Any],
) -> list[str]:
    """④ vector_source_svg 内容哈希绑定 + 引擎版本/参数/候选 SHA-256 留档。"""

    from tools.assets.asset_trace import check_svg_contract_subset

    asset_id = entry["id"]
    vector_source = entry["vector_source_svg"]
    declared_sha256 = vector_source["sha256"]
    blockers: list[str] = []
    svg_path = case_root.joinpath(*vector_source["path"].split("/"))
    if not svg_path.is_file():
        blockers.append(f"atomic-vector:{asset_id}:vector-source-missing")
    elif common.sha256_file(svg_path) != declared_sha256:
        blockers.append(f"atomic-vector:{asset_id}:vector-source-hash-mismatch")
    else:
        blockers.extend(
            f"atomic-vector:{asset_id}:contract-subset:{violation}"
            for violation in check_svg_contract_subset(svg_path)
        )

    records = [
        record
        for record in _provenance_records(provenance)
        if record.get("origin") == "vtracer-provider"
        and record.get("sha256") == declared_sha256
    ]
    if not records:
        blockers.append(f"atomic-vector:{asset_id}:provenance-missing")
        return blockers
    record = records[0]
    if record.get("trace_engine_version") != entry["trace_engine_version"]:
        blockers.append(f"atomic-vector:{asset_id}:provenance-engine-version")
    parameters = record.get("parameters")
    if not isinstance(parameters, dict) or not parameters:
        blockers.append(f"atomic-vector:{asset_id}:provenance-parameters")
    if not any(
        isinstance(record.get(key), str) and record.get(key)
        for key in ("ingested_at", "traced_at", "at")
    ):
        blockers.append(f"atomic-vector:{asset_id}:provenance-timestamp")
    return blockers


def audit_atomic_vector_qa(
    case_root: Path,
    *,
    assets: dict[str, Any],
    bindings: dict[str, Any],
    regions_payload: dict[str, Any],
    regions_report: dict[str, Any],
    provenance: dict[str, Any],
    shape_metadata: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    """Judge every atomic-vector (vtracer-trace) asset in one case.

    Returns the ``atomic-vector-report.json`` document; ``blockers`` follows
    the existing ``asset:<id>:...`` / ``region:<id>`` naming style.  The audit
    is read-only: fallback to atomic-raster is executed by the outer
    workflow, never by check.
    """

    from tools.assets.asset_spec import (
        ATOMIC_VECTOR_SOURCE,
        audit_atomic_vector_assets,
        validate_atomic_vector_asset,
    )
    from tools.regions.regions import CRITICAL_EDGE_IOU_FLOOR

    entries = [
        item
        for item in assets.get("assets", [])
        if isinstance(item, dict) and item.get("source") == ATOMIC_VECTOR_SOURCE
    ]
    raster_entry_ids = {
        item.get("id")
        for item in assets.get("assets", [])
        if isinstance(item, dict) and item.get("source") == "reference_crop"
    }
    active_raster_binding_ids = {
        row.get("element_id")
        for row in bindings.get("bindings", [])
        if isinstance(row, dict) and row.get("object_kind") == "atomic-raster"
    }
    # ① assets.json 合同层(11 字段闭集合 + fallback_atomic_raster 同文档解析)。
    blockers = audit_atomic_vector_assets(assets)

    asset_reports: list[dict[str, Any]] = []
    for entry in entries:
        raw_id = entry.get("id")
        asset_id = raw_id if isinstance(raw_id, str) and raw_id else "[missing-id]"
        gates: dict[str, list[str]] = {}
        gates["contract"] = [
            f"atomic-vector-asset:{asset_id}:{error}"
            for error in validate_atomic_vector_asset(entry)
        ]
        if gates["contract"]:
            # 合同字段不可信时下游门禁无法安全求值;合同 blocker 已 fail closed。
            gates["nativeness"] = []
            gates["region"] = []
            gates["provenance"] = []
        else:
            gates["nativeness"] = _atomic_vector_nativeness_blockers(
                entry, bindings, shape_metadata
            )
            gates["region"] = _atomic_vector_region_blockers(
                entry,
                regions_payload,
                regions_report,
                edge_iou_floor=CRITICAL_EDGE_IOU_FLOOR,
            )
            gates["provenance"] = _atomic_vector_provenance_blockers(
                case_root, entry, provenance
            )
        entry_blockers = [item for gate in gates.values() for item in gate]
        # ⑤ 回退审计:矢量为准;矢量门禁失败时显式要求回退其声明的位图层,
        # 矢量全过而位图层仍在编译产物中则为表示分歧。
        fallback = entry.get("fallback_atomic_raster")
        fallback_id = fallback if isinstance(fallback, str) and fallback else "[unresolved]"
        fallback_resolved = fallback_id in raster_entry_ids
        fallback_blockers: list[str] = []
        if entry_blockers or not fallback_resolved:
            fallback_blockers.append(
                f"atomic-vector:{asset_id}:fallback-required:{fallback_id}"
            )
        elif fallback_id in active_raster_binding_ids:
            fallback_blockers.append(f"atomic-vector:{asset_id}:fallback-active:{fallback_id}")
        gates["fallback"] = fallback_blockers
        entry_blockers.extend(fallback_blockers)
        asset_reports.append(
            {
                "id": asset_id,
                "ink_contract_region_id": entry.get("ink_contract_region_id"),
                "fallback_atomic_raster": entry.get("fallback_atomic_raster"),
                "gates": gates,
                "pass": not entry_blockers,
                "blockers": entry_blockers,
            }
        )
        blockers.extend(entry_blockers)
    blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": "4.0.0",
        "kind": "atomic_vector_audit",
        "reference_sha256": assets.get("reference_sha256"),
        "asset_count": len(entries),
        "assets": asset_reports,
        "pass": not blockers,
        "blockers": blockers,
    }


def _write_repair_evidence(run: common.Run, blockers: list[str]) -> dict:
    """Write the exact blocker inventory and its fail-closed repair coverage."""

    from tools.core.contracts import write_json
    from tools.repair.repair_plan import validate_repair_plan, write_repair_plan
    from tools.core.revisions import compiler_fingerprint, revision_id, scene_sha256

    meta = run.load_meta()
    scene = json.loads(run.scene_path.read_text(encoding="utf-8"))
    canonical_blockers = sorted(set(blockers))
    inventory = {
        "schema_version": "4.0.0",
        "kind": "strict_blocker_inventory",
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "artifact_sha256": common.sha256_file(run.pptx_path),
        "revision_id": revision_id(scene),
        "scene_sha256": scene_sha256(scene),
        "compiler_fingerprint": compiler_fingerprint(),
        "blockers": canonical_blockers,
    }
    write_json(run.blockers_path, inventory)
    report_hashes = _qa_report_hashes(run)
    plan = write_repair_plan(
        run.repair_plan_path,
        canonical_blockers,
        case=meta["case"],
        reference_sha256=meta["source_sha256"],
        artifact_sha256=inventory["artifact_sha256"],
        qa_report_sha256=report_hashes,
    )
    validation = validate_repair_plan(
        plan,
        expected_reference_sha256=meta["source_sha256"],
        expected_artifact_sha256=inventory["artifact_sha256"],
        expected_qa_report_sha256=report_hashes,
    )
    return {"inventory": inventory, "plan": plan, "validation": validation}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum() or ch in "τπƒαβγδεζηθικλμνξρσυφχψω")


def _svg_texts(svg_path: Path) -> list[str]:
    root = ET.parse(svg_path).getroot()
    texts: list[str] = []
    for element in root.iter(f"{SVG_NS}text"):
        parts = [element.text or ""]
        for tspan in element:
            parts.append(tspan.text or "")
            parts.append(tspan.tail or "")
        joined = "".join(parts).strip()
        if joined:
            texts.append(joined)
    return texts


def _match_texts(
    svg_texts: list[str], ocr_texts: list[str]
) -> tuple[list[str], list[str]]:
    """归一化精确 + 包含匹配，剩余项再做 difflib 模糊匹配（OCR l/I/破折号噪声容忍）。"""
    import difflib

    ocr_norm = [(t, _normalize(t)) for t in ocr_texts if _normalize(t)]
    svg_norm = [(t, _normalize(t)) for t in svg_texts if _normalize(t)]
    used_ocr: set[int] = set()
    unmatched_svg: list[str] = []
    for text, norm in svg_norm:
        hit = None
        for idx, (_, onorm) in enumerate(ocr_norm):
            if norm == onorm or (len(norm) >= 4 and norm in onorm) or (len(onorm) >= 4 and onorm in norm):
                hit = idx
                break
        if hit is None:
            unmatched_svg.append((text, norm))
        else:
            used_ocr.add(hit)

    # 模糊轮：SVG 剩余项与 OCR 剩余项做最佳比率匹配
    remaining_ocr = [(idx, text, norm) for idx, (text, norm) in enumerate(ocr_norm) if idx not in used_ocr]
    final_unmatched_svg: list[str] = []
    for text, norm in unmatched_svg:
        best_idx, best_ratio = None, 0.0
        for idx, _, onorm in remaining_ocr:
            if idx in used_ocr:
                continue
            ratio = difflib.SequenceMatcher(None, norm, onorm).ratio()
            if ratio > best_ratio:
                best_idx, best_ratio = idx, ratio
        if best_idx is not None and best_ratio >= 0.8:
            used_ocr.add(best_idx)
        else:
            final_unmatched_svg.append(text)
    unmatched_ocr = [text for idx, (text, _) in enumerate(ocr_norm) if idx not in used_ocr]
    return final_unmatched_svg, unmatched_ocr


def _run_ocr(run: common.Run, out_json: Path) -> list[str]:
    if not PADDLE_PYTHON.is_file():
        raise common.fail(f"Paddle 解释器不存在: {PADDLE_PYTHON}")
    helper = Path(__file__).parent.parent / "assets" / "ocr_texts.py"
    command = [
        str(PADDLE_PYTHON), "-I", "-B", "-X", "utf8",
        str(helper), str(OCR_CONFIG), str(run.source_png), str(out_json),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if result.returncode != 0 or not out_json.is_file():
        tail = (result.stderr or result.stdout or "")[-800:]
        raise common.fail(f"OCR 执行失败（这步只读 Paddle runtime，不重装模型）:\n{tail}")
    return json.loads(out_json.read_text(encoding="utf-8"))


def _run_figure_lint(run: common.Run) -> dict:
    out_png = run.qa_dir / "diff.png"
    command = [
        sys.executable, "-B", "-X", "utf8", str(FIGURE_LINT),
        str(run.source_png), str(run.render_png), "--diff-out", str(out_png), "--pretty",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    try:
        metrics = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise common.fail(f"figure_lint 输出解析失败:\n{(result.stderr or '')[-500:]}") from exc
    metrics["diff_out"] = "qa/diff.png"
    (run.qa_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return metrics


def _build_preview(run: common.Run) -> Path:
    from PIL import Image, ImageDraw

    with Image.open(run.source_png) as ref, Image.open(run.render_png) as ren:
        ref_img, ren_img = ref.convert("RGB"), ren.convert("RGB")
        width = max(ref_img.width, ren_img.width)
        height = ref_img.height + ren_img.height + 30
        canvas = Image.new("RGB", (width, height), (220, 20, 20))
        canvas.paste(ref_img, (0, 20))
        canvas.paste(ren_img, (0, ref_img.height + 30))
        draw = ImageDraw.Draw(canvas)
        draw.text((4, 4), "REFERENCE", fill=(255, 255, 255))
        draw.text((4, ref_img.height + 24), "RENDER", fill=(255, 255, 255))
        out = run.preview_png
        canvas.save(out)
        return out


def _strict_live_blockers(
    run: common.Run, regions: dict, profile: str
) -> list[str]:
    """Strict always consumes finalizer-bound Live evidence."""

    if profile != "strict":
        return []
    from tools.repair.repair import live_evidence_passes

    failed_regions = [
        item["id"]
        for item in regions.get("regions", [])
        if item.get("critical") is True and item.get("pass") is not True
    ]
    _, blockers = live_evidence_passes(run, failed_regions)
    return blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure check", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument("--skip-ocr", action="store_true", help="跳过 Paddle OCR 文本比对")
    parser.add_argument("--re-ocr", action="store_true", help="忽略缓存的 OCR 结果重新识别")
    parser.add_argument("--profile", choices=("standard", "strict"), default="standard")
    parser.add_argument("--require-live", action="store_true", help="strict 模式要求 PowerPoint-live 保存重开证据")
    args = parser.parse_args(argv)

    if args.profile == "strict" and args.skip_ocr:
        raise common.fail("strict profile does not allow --skip-ocr")

    run = common.open_run(args.run_dir)
    if not run.pptx_path.is_file() or not run.render_png.is_file():
        raise common.fail("缺少 PPTX 或 render.png，请先运行 autofigure convert")
    run.qa_dir.mkdir(exist_ok=True)

    metrics = _run_figure_lint(run)
    preview = _build_preview(run)

    from tools.regions.regions import evaluate_regions

    regions = evaluate_regions(run)
    from tools.pipeline.layout import audit_layout, persist_layout_audit

    layout_report = audit_layout(run)
    persist_layout_audit(run, layout_report)

    # 每次 check 都重新生成哈希绑定的像素证据。SVG marker 的 advisory 结构审计
    # 由专用命令 `autofigure arrows` 产出（qa/arrows-audit.json 是该命令的报告，
    # check 不再重复生成）；A/B 对比读取该文件时按存在性降级为 0。
    from tools.arrows.arrow_visual import audit_arrow_visual_contracts

    arrow_visual = audit_arrow_visual_contracts(run)

    # Recompute all artifact-bound evidence from the current root PPTX.  A
    # stale report from a different candidate must never satisfy strict.
    from tools.arrows.pptx_arrows import write_arrow_reports
    from tools.assets.primitives import audit_primitives
    from tools.providers.providers import write_case_capabilities

    arrow_compile, arrow_readback = write_arrow_reports(run)
    primitive_report = audit_primitives(run)
    provider_report = write_case_capabilities(run)
    from tools.pipeline.convert import write_asset_spec_audit

    asset_spec_report = write_asset_spec_audit(run)
    from tools.assets.asset_spec import asset_contract_blockers

    asset_contract_findings = asset_contract_blockers(run)
    from tools.regions.visual_contracts import evaluate_visual_contracts

    visual_contract_report = evaluate_visual_contracts(run)

    unmatched_svg: list[str] = []
    unmatched_ocr: list[str] = []
    if not args.skip_ocr:
        ocr_json = run.qa_dir / "ocr-texts.json"
        if args.re_ocr or not ocr_json.is_file():
            _run_ocr(run, ocr_json)
        ocr_texts = json.loads(ocr_json.read_text(encoding="utf-8"))
        unmatched_svg, unmatched_ocr = _match_texts(_svg_texts(run.redraw_svg), ocr_texts)

    report = run.report_md
    lines = [
        f"# check 报告（{args.profile}） — {run.root.name}",
        "",
        "## 像素诊断（figure_lint，软信号）",
        f"- mean_abs_rgb_delta: {metrics.get('mean_abs_rgb_delta')}",
        f"- changed_pixel_ratio: {metrics.get('changed_pixel_ratio_pct')}%",
        f"- top_roi: {metrics.get('top_roi')}",
        f"- ssim: {metrics.get('ssim')}",
        "- diff 图: qa/diff.png",
        "- 对照预览: preview.png",
        f"- 关键区域 strict_pass: {regions['strict_pass']}（{regions['critical_regions']} 个关键区域）",
        "- 区域明细: qa/regions-report.json",
        f"- 布局合同: {'PASS' if layout_report['pass'] else 'FAIL'}（{len(layout_report['findings'])} 项）",
        "- 布局明细: qa/layout-audit.json",
        f"- 箭头视觉物理门禁: {'PASS' if arrow_visual['pass'] else 'FAIL'}"
        f"（{arrow_visual['contract_count']} 个合同）",
        f"- ArrowSpec 编译: {'PASS' if arrow_compile['pass'] else 'FAIL'}（{arrow_compile['arrow_count']} 个逻辑箭头）",
        f"- PowerPoint 箭头读回: {'PASS' if arrow_readback['pass'] else 'FAIL'}",
        f"- 语义图元: {'PASS' if primitive_report['pass'] else 'FAIL'}（{primitive_report['primitive_count']} 个）",
        f"- AssetSpec 资产合同: {'PASS' if asset_spec_report['pass'] else 'FAIL'}"
        f"（{asset_spec_report['asset_spec_count']} 个逻辑资产，"
        f"{asset_spec_report['pptx_readback_count']} 个成员读回）",
        f"- 冻结资产输入 receipt: "
        f"{'PASS' if not asset_contract_findings else 'FAIL'}"
        f"（{len(asset_contract_findings)} 项）",
        f"- 字体/图标尺度/重叠合同: "
        f"{'PASS' if visual_contract_report['pass'] else 'FAIL'}"
        f"（{visual_contract_report['object_count']} 个冻结对象）",
        f"- PowerPoint Live 箭头创作: {'ENABLED' if provider_report['powerpoint_live']['arrow_authoring_allowed'] else 'DISABLED / inspect-only'}",
        "- 结构证据: qa/arrow-visual-report.json、qa/arrow-compile-report.json、"
        "qa/powerpoint-arrow-readback.json、qa/primitive-audit.json、"
        "qa/asset-spec-audit.json、qa/asset-contract-receipt.json、"
        "qa/visual-contracts-report.json、qa/provider-capabilities.json、"
        "qa/atomic-vector-report.json",
        "",
        "## 文本比对（SVG 文字 vs 参考图 OCR）",
        f"- SVG 侧未匹配 {len(unmatched_svg)} 条（可能：VLM 错字 / OCR 漏识 / 粒度差异）",
        f"- OCR 侧未匹配 {len(unmatched_ocr)} 条（可能：VLM 漏画 / OCR 误识）",
        "",
        "### SVG 侧未匹配",
        *[f"- {t}" for t in unmatched_svg],
        "",
        "### OCR 侧未匹配",
        *[f"- {t}" for t in unmatched_ocr],
        "",
    ]

    arrows_audit_path = run.qa_dir / "arrows-audit.json"
    if arrows_audit_path.is_file():
        # 历史证据或 `autofigure arrows` 命令产出的 advisory 审计：check 只读不写。
        from tools.arrows.arrows import render_report

        audit = json.loads(arrows_audit_path.read_text(encoding="utf-8"))
        lines.extend(render_report(audit) + [""])

    from tools.core.contracts import read_json

    strict_blockers = list(regions.get("blockers", []))
    if args.profile == "strict" and regions.get("critical_regions", 0) == 0:
        strict_blockers.append("regions:no-critical-regions")
    from tools.pipeline.layout import strict_blockers as layout_strict_blockers

    strict_blockers.extend(layout_strict_blockers(layout_report))
    strict_blockers.extend(arrow_visual.get("blockers", []))
    from tools.regions.visual_contracts import strict_blockers as visual_strict_blockers

    strict_blockers.extend(visual_strict_blockers(visual_contract_report))
    from tools.assets.reference_inventory import inventory_blockers

    strict_blockers.extend(inventory_blockers(run, include_svg_text=True))
    strict_blockers.extend(asset_contract_findings)
    if args.profile == "strict":
        if unmatched_svg:
            strict_blockers.append("ocr:svg-text-unmatched")
        if unmatched_ocr:
            strict_blockers.append("ocr:reference-text-unmatched")
    # The legacy SVG-marker audit remains useful as source diagnostics, but it
    # is not the compiled PowerPoint truth.  Native endpoint normalization can
    # intentionally replace imperfect marker geometry.  Strict blocking is
    # therefore owned by ArrowSpec compilation + artifact-bound OOXML readback
    # below; any real loss or multi-object fallback is reported there.
    from tools.arrows.pptx_arrows import strict_blockers as pptx_arrow_strict_blockers
    from tools.assets.primitives import strict_blockers as primitive_strict_blockers

    strict_blockers.extend(pptx_arrow_strict_blockers(run))
    strict_blockers.extend(primitive_strict_blockers(primitive_report))
    strict_blockers.extend(asset_spec_report.get("blockers", []))
    assets = read_json(run.assets_path)
    strict_blockers.extend(
        f"asset:{item.get('id', '[missing-id]')}:authorization-unverified"
        for item in assets.get("assets", [])
        if item.get("source") == "reference_crop" and item.get("authorized") is not True
    )
    bindings = read_json(run.bindings_path)
    if bindings.get("artifact_sha256") != common.sha256_file(run.pptx_path):
        strict_blockers.append("bindings:artifact-hash-mismatch")
    if bindings.get("saved_reopened") is not True:
        strict_blockers.append("bindings:save-reopen-not-verified")
    if bindings.get("bindings_complete") is not True:
        strict_blockers.append("bindings:incomplete")
    from tools.assets.asset_spec import ATOMIC_VECTOR_SOURCE

    has_atomic_vector = any(
        isinstance(item, dict) and item.get("source") == ATOMIC_VECTOR_SOURCE
        for item in assets.get("assets", [])
    )
    atomic_vector_report = audit_atomic_vector_qa(
        run.root,
        assets=assets,
        bindings=bindings,
        regions_payload=read_json(run.regions_path),
        regions_report=regions,
        provenance=read_json(run.provenance_path),
        shape_metadata=_pptx_shape_metadata(run.pptx_path) if has_atomic_vector else {},
    )
    from tools.core.contracts import write_json

    write_json(run.qa_dir / ATOMIC_VECTOR_REPORT_NAME, atomic_vector_report)
    strict_blockers.extend(atomic_vector_report["blockers"])
    from tools.pipeline.math import math_summary_blockers

    strict_blockers.extend(math_summary_blockers(run))
    strict_blockers.extend(_source_gate_blockers(run))
    from tools.core.revisions import lineage_blockers

    strict_blockers.extend(lineage_blockers(run))
    require_live = args.profile == "strict"
    live_blockers = _strict_live_blockers(run, regions, args.profile)
    strict_blockers.extend(live_blockers)
    strict_blockers = list(dict.fromkeys(strict_blockers))

    repair_evidence = _write_repair_evidence(run, strict_blockers)
    if not repair_evidence["validation"]["pass"]:
        strict_blockers.append("repair-plan:incomplete")
        strict_blockers = list(dict.fromkeys(strict_blockers))
        repair_evidence = _write_repair_evidence(run, strict_blockers)
    from tools.qa.qa_lineage import (
        validate_qa_lineage_manifest,
        write_qa_lineage_manifest,
    )

    write_qa_lineage_manifest(run)
    qa_lineage_blockers = validate_qa_lineage_manifest(run)
    if qa_lineage_blockers:
        strict_blockers.extend(qa_lineage_blockers)
        strict_blockers = list(dict.fromkeys(strict_blockers))
        repair_evidence = _write_repair_evidence(run, strict_blockers)
        write_qa_lineage_manifest(run)

    from tools.core.contracts import record_validation

    record_validation(run, args.profile, strict_blockers)

    exit_code = 0
    if args.profile == "strict":
        from tools.core.contracts import transition

        if strict_blockers:
            transition(run, "qa_failed", "strict-check-failed", details={"blockers": strict_blockers})
            exit_code = 2
        else:
            transition(run, "approved", "strict-check-passed")

    from tools.qa.qa_status import write_qa_status

    qa_status = write_qa_status(
        run,
        ocr_unmatched=None if args.skip_ocr else (len(unmatched_svg), len(unmatched_ocr)),
    )

    lines.extend(
        [
            "",
            f"## 验收状态（{args.profile}）",
            f"- blockers: {len(strict_blockers)}",
            f"- repair plan coverage: {'PASS' if repair_evidence['validation']['pass'] else 'FAIL'}",
            "- blocker inventory: qa/blockers.json",
            "- repair plan: qa/repair-plan.json",
            "- QA lineage: qa/qa-lineage-manifest.json",
            f"- atomic-vector 资产门禁: {'PASS' if atomic_vector_report['pass'] else 'FAIL'}"
            f"（{atomic_vector_report['asset_count']} 个资产;明细 qa/atomic-vector-report.json）",
            (
                "- PowerPoint Live: REQUIRED — "
                + ("PASS" if not live_blockers else "FAIL")
                if args.profile == "strict" and require_live
                else "- PowerPoint Live: not required"
            ),
            *[f"- {item}" for item in strict_blockers],
            "",
            "## QA 状态六维度",
            *[
                f"- {name}: {dimension['status']}"
                + (f"（{len(dimension['blockers'])} 项 blocker）" if dimension["blockers"] else "")
                for name, dimension in qa_status["dimensions"].items()
            ],
            "- 维度明细: qa/qa-status.json",
            "",
        ]
    )
    if args.profile == "standard":
        lines.append("> standard 结果为诊断；只有 strict 零 blocker 才能进入 approved。")
    else:
        lines.append("> strict 使用关键区域、箭头/图元结构与所声明的 Live 回读共同门禁；全图均值不能覆盖局部失败。")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sys.stdout.write(f"像素诊断 mean={metrics.get('mean_abs_rgb_delta')} top_roi_loss={metrics.get('top_roi', {}).get('loss_contribution_pct')}%\n")
    sys.stdout.write(f"文本比对: SVG 侧未匹配 {len(unmatched_svg)} / OCR 侧未匹配 {len(unmatched_ocr)}\n")
    sys.stdout.write(f"报告: {report}\n预览: {preview}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
