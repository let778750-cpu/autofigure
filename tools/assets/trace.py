"""Trace one authorized atomic microasset crop into an atomic-vector asset.

``autofigure trace`` re-crops the authorized atomic-raster entry's bbox from
the case's own ``reference.png``, measures deterministic trace eligibility,
runs the locked-parameter vtracer trace, and appends the derived atomic-vector
entry to the derived ``assets`` list of ``assets.json``.  The frozen ``policy``
and ``microasset_opportunity_map`` sections are never touched, so frozen
asset-contract receipts cannot drift.  Photographic crops stay on the
atomic-raster layer; ambiguous crops require explicit ``--allow-ambiguous``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path
from typing import Any

from tools import common
from tools.assets.asset_spec import (
    ATOMIC_VECTOR_FALLBACK_SOURCE,
    ATOMIC_VECTOR_ID_PREFIX,
    ATOMIC_VECTOR_SOURCE,
    audit_atomic_vector_assets,
    validate_atomic_vector_asset,
)
from tools.assets.asset_trace import (
    AssetTraceError,
    compute_trace_eligibility,
    run_vtracer_trace,
)
from tools.core.contracts import read_json, utc_now, write_json
from tools.core.transactions import recoverable_case_transaction

# contracts.CANDIDATE_ORIGINS 权威枚举成员:描摹产物的可审计来源标记。
TRACE_ORIGIN = "vtracer-provider"

# 派生 atomic-vector 条目的 id 在原位图条目 id 上加此后缀;两者同文档共存,
# 位图条目保留为显式回退层(fallback_atomic_raster 指回它)。
VECTOR_ASSET_ID_SUFFIX = "-vector"


def _normalize_asset_id(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise common.fail("--asset 不能为空")
    if not candidate.startswith(ATOMIC_VECTOR_ID_PREFIX):
        candidate = f"{ATOMIC_VECTOR_ID_PREFIX}{candidate}"
    slug = candidate[len(ATOMIC_VECTOR_ID_PREFIX) :]
    if not slug or slug != slug.strip() or "/" in slug or "\\" in slug:
        raise common.fail(f"无效的 atomic 资产 id: {value}")
    return candidate


def _vector_asset_id(raster_id: str) -> str:
    return f"{raster_id}{VECTOR_ASSET_ID_SUFFIX}"


def _case_relative_vector_path(vector_id: str) -> str:
    return f"assets/{vector_id[len(ATOMIC_VECTOR_ID_PREFIX):]}.svg"


def _case_relative_crop_path(raster_id: str) -> str:
    return f"assets/{raster_id[len(ATOMIC_VECTOR_ID_PREFIX):]}.png"


def _authorized_raster_entry(assets: dict[str, Any], atomic_id: str) -> dict[str, Any]:
    entries = assets.get("assets")
    if not isinstance(entries, list):
        raise common.fail("assets.json 缺少派生资产列表 assets")
    matches = [
        item
        for item in entries
        if isinstance(item, dict) and item.get("id") == atomic_id
    ]
    if not matches:
        raise common.fail(f"assets.json 中不存在条目 {atomic_id}")
    entry = matches[0]
    if entry.get("source") != ATOMIC_VECTOR_FALLBACK_SOURCE:
        raise common.fail(
            f"{atomic_id} 不是 reference_crop 位图条目"
            f"（source={entry.get('source')}），不能作为描摹输入"
        )
    if entry.get("authorized") is not True:
        raise common.fail(f"{atomic_id} 未经显式授权，禁止描摹")
    for field in ("authorization_basis", "rights_status"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise common.fail(f"{atomic_id} 缺少 {field}，无法继承授权信息")
    return entry


def _resolve_ink_contract_region_id(run: common.Run, entry: dict[str, Any]) -> str:
    declared = entry.get("ink_contract_region_id")
    if isinstance(declared, str) and declared and declared == declared.strip():
        return declared
    regions = read_json(run.regions_path)
    matches = [
        region["id"]
        for region in regions.get("regions", [])
        if isinstance(region, dict)
        and region.get("asset_id") == entry["id"]
        and isinstance(region.get("id"), str)
        and region["id"].strip()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise common.fail(
            f"{entry['id']} 没有可用的 ink_contract_region_id:"
            "regions.json 中没有区域以 asset_id 指向它"
        )
    raise common.fail(
        f"{entry['id']} 对应多个区域，无法确定 ink_contract_region_id: {matches}"
    )


def _recrop_authorized_crop(
    run: common.Run, entry: dict[str, Any]
) -> tuple[bytes, list[int], str]:
    bbox = entry.get("bbox")
    if not (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox)
    ):
        raise common.fail(f"{entry['id']} 的 bbox 无效")
    x, y, width, height = (float(value) for value in bbox)
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise common.fail(f"{entry['id']} 的 bbox 超出合法范围")
    from PIL import Image

    with Image.open(run.source_png) as image:
        left, top = round(x), round(y)
        right, bottom = round(x + width), round(y + height)
        if right > image.width or bottom > image.height:
            raise common.fail(f"{entry['id']} 的 bbox 超出 reference.png 画布")
        crop = image.crop((left, top, right, bottom))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    recorded = entry.get("source_sha256")
    if isinstance(recorded, str) and recorded and recorded.lower() != digest:
        raise common.fail(
            f"{entry['id']} 按 bbox 重裁的哈希 {digest} 与授权记录"
            f" {recorded} 不一致，已拒绝描摹"
        )
    return payload, [left, top, right - left, bottom - top], digest


def _eligibility_gate(
    atomic_id: str, eligibility: dict[str, Any], allow_ambiguous: bool
) -> str:
    classification = eligibility["classification"]
    if classification == "photographic":
        raise common.fail(
            f"{atomic_id} 实测分类为 photographic（连续调/照片类），描摹视觉不可接受；"
            "该微资产必须留在 atomic-raster 位图层"
        )
    if classification == "ambiguous" and not allow_ambiguous:
        raise common.fail(
            f"{atomic_id} 实测分类为 ambiguous，默认保持 atomic-raster 位图层；"
            "确认其为平面插画类后显式加 --allow-ambiguous 重试"
        )
    return classification


def _record_trace_provenance(
    run: common.Run,
    vector_entry: dict[str, Any],
    result: dict[str, Any],
    *,
    input_crop: dict[str, Any],
    statistics: dict[str, Any],
) -> None:
    """Append the trace to provenance following the candidate-history precedent."""

    provenance = read_json(run.provenance_path)
    history = provenance.setdefault("asset_trace_history", [])
    identity = (
        vector_entry["id"],
        result["output_sha256"],
        result["trace_engine_version"],
    )
    if any(
        isinstance(item, dict)
        and (item.get("asset_id"), item.get("sha256"), item.get("trace_engine_version"))
        == identity
        for item in history
    ):
        return
    traced_at = utc_now()
    history.append(
        {
            "asset_id": vector_entry["id"],
            "fallback_atomic_raster": vector_entry["fallback_atomic_raster"],
            "kind": "svg",
            "role": "reconstruction-candidate",
            "origin": TRACE_ORIGIN,
            "source_name": Path(vector_entry["vector_source_svg"]["path"]).name,
            "canonical_path": vector_entry["vector_source_svg"]["path"],
            "sha256": result["output_sha256"],
            "input_crop": input_crop,
            "trace_engine": result["trace_engine"],
            "trace_engine_version": result["trace_engine_version"],
            "trace_method": vector_entry["trace_method"],
            "parameters": result["parameters"],
            "trace_eligibility": vector_entry["trace_eligibility"],
            "trace_eligibility_statistics": statistics,
            "traced_at": traced_at,
        }
    )
    provenance.setdefault("events", []).append(
        {
            "event": "asset-traced",
            "at": traced_at,
            "asset_id": vector_entry["id"],
            "candidate_sha256": result["output_sha256"],
            "origin": TRACE_ORIGIN,
        }
    )
    provenance["updated_at"] = utc_now()
    write_json(run.provenance_path, provenance)


def trace_asset(
    run: common.Run, asset: str, *, allow_ambiguous: bool = False
) -> dict[str, Any]:
    from PIL import Image

    atomic_id = _normalize_asset_id(asset)
    assets = read_json(run.assets_path)
    raster_entry = _authorized_raster_entry(assets, atomic_id)
    region_id = _resolve_ink_contract_region_id(run, raster_entry)
    crop_bytes, crop_bbox, crop_sha256 = _recrop_authorized_crop(run, raster_entry)
    # 资格判定先于任何写入:被拒绝的分类(photographic / 未放行的 ambiguous)
    # 在案例内不产生任何文件。
    with Image.open(io.BytesIO(crop_bytes)) as crop_image:
        eligibility = compute_trace_eligibility(crop_image)
    classification = _eligibility_gate(atomic_id, eligibility, allow_ambiguous)

    vector_id = _vector_asset_id(atomic_id)
    vector_relative = _case_relative_vector_path(vector_id)
    crop_relative = _case_relative_crop_path(atomic_id)
    vector_path = run.root / vector_relative
    crop_path = run.root / crop_relative

    staging_root = common.PROJECT_ROOT / ".autofigure-staging"
    with recoverable_case_transaction(
        [run.assets_path, run.provenance_path, vector_path, crop_path],
        staging_root=staging_root,
        label=f"trace-{run.load_meta()['case']}",
    ):
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop_path.write_bytes(crop_bytes)
        try:
            result = run_vtracer_trace(crop_path, vector_path)
        except AssetTraceError as exc:
            raise common.fail(f"{atomic_id} 描摹失败: {exc}") from exc

        vector_entry = {
            "id": vector_id,
            "editable": True,
            "source": ATOMIC_VECTOR_SOURCE,
            "vector_source_svg": {
                "path": vector_relative,
                "sha256": result["output_sha256"],
            },
            "trace_method": result["trace_method"],
            "trace_engine_version": result["trace_engine_version"],
            "authorization_basis": raster_entry["authorization_basis"],
            "rights_status": raster_entry["rights_status"],
            "fallback_atomic_raster": atomic_id,
            "ink_contract_region_id": region_id,
            "trace_eligibility": classification,
        }
        contract_errors = validate_atomic_vector_asset(vector_entry)
        if contract_errors:
            raise common.fail(
                "atomic-vector 条目合同校验失败: " + ", ".join(contract_errors)
            )
        entries = assets["assets"]
        for index, item in enumerate(entries):
            if isinstance(item, dict) and item.get("id") == vector_id:
                entries[index] = vector_entry
                break
        else:
            entries.append(vector_entry)
        audit_errors = audit_atomic_vector_assets(assets)
        if audit_errors:
            raise common.fail(
                "atomic-vector 资产审计失败: " + ", ".join(audit_errors)
            )
        assets["updated_at"] = utc_now()
        write_json(run.assets_path, assets)

        _record_trace_provenance(
            run,
            vector_entry,
            result,
            input_crop={
                "path": crop_relative,
                "sha256": crop_sha256,
                "bbox": crop_bbox,
            },
            statistics=eligibility["statistics"],
        )
    return {
        "asset_id": vector_id,
        "trace_eligibility": classification,
        "vector_source_svg": vector_relative,
        "sha256": result["output_sha256"],
        "trace_engine_version": result["trace_engine_version"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure trace", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--asset",
        required=True,
        help="已授权 atomic-raster 条目 id（atomic: 前缀可省略）",
    )
    parser.add_argument(
        "--allow-ambiguous",
        action="store_true",
        help="实测分类为 ambiguous 时显式放行（photographic 一律拒绝）",
    )
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    summary = trace_asset(run, args.asset, allow_ambiguous=args.allow_ambiguous)
    sys.stdout.write(
        f"已描摹 {summary['asset_id']}"
        f"（eligibility={summary['trace_eligibility']}，"
        f"vtracer {summary['trace_engine_version']}）"
        f" → {summary['vector_source_svg']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
