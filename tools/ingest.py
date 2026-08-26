"""Ingest model-independent SVG/scene/patch candidates or reject a bad SVG."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from tools import common
from tools.contracts import (
    CANDIDATE_ORIGINS,
    read_json,
    record_candidate_provenance,
    set_processing_mode,
    transition,
    write_json,
)


def build_region_tasks(run: common.Run) -> dict:
    regions = read_json(run.regions_path)
    meta = run.load_meta()
    from tools.reference_inventory import canonical_sha256

    tasks = {
        "schema_version": meta["schema_version"],
        "kind": "region_tasks",
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "input_route": meta["input_route"],
        "processing_mode": meta["processing_mode"],
        "fidelity_profile": meta["fidelity_profile"],
        "regions_sha256": common.sha256_file(run.regions_path),
        "reference_inventory_status": (
            regions.get("reference_inventory", {}).get("status")
            if isinstance(regions.get("reference_inventory"), dict)
            else None
        ),
        "reference_inventory_sha256": (
            canonical_sha256(regions["reference_inventory"])
            if isinstance(regions.get("reference_inventory"), dict)
            else None
        ),
        "result_contract": {
            "accepted_kinds": ["scene", "region_patch", "svg"],
            "stable_element_ids_required": True,
            "formal_content_must_remain_native": True,
            "offline_initial_render_carrier": "svg",
            "scene_or_patch_requires": "existing render carrier or powerpoint-live provider",
            "svg_authoring_contract": "prompt.md",
        },
        "tasks": [
            {
                "region_id": region["id"],
                "label": region.get("label", region["id"]),
                "bbox": region["bbox"],
                "critical": bool(region.get("critical", False)),
                **(
                    {"element_ids": copy.deepcopy(region["element_ids"])}
                    if "element_ids" in region
                    else {}
                ),
                **(
                    {"required_relations": copy.deepcopy(region["required_relations"])}
                    if "required_relations" in region
                    else {}
                ),
                **(
                    {"relations_exhaustive": region["relations_exhaustive"]}
                    if "relations_exhaustive" in region
                    else {}
                ),
                **(
                    {"asset_id": region["asset_id"]}
                    if "asset_id" in region
                    else {}
                ),
            }
            for region in regions.get("regions", [])
        ],
    }
    write_json(run.region_tasks_path, tasks)
    return tasks


def _ingest_scene(run: common.Run, source: Path) -> dict:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise common.fail("scene candidate must be a JSON object with an elements array")
    current = read_json(run.scene_path)
    required_identity = {
        "schema_version": current["schema_version"],
        "kind": "scene",
        "case": current["case"],
        "reference_sha256": current["reference_sha256"],
    }
    mismatches = [
        key for key, expected in required_identity.items() if payload.get(key) != expected
    ]
    if mismatches:
        raise common.fail(
            "scene candidate identity mismatch: " + ", ".join(mismatches)
        )
    ids = [item.get("id") for item in payload["elements"] if isinstance(item, dict)]
    if len(ids) != len(payload["elements"]) or any(not item for item in ids):
        raise common.fail("every scene element requires a stable id")
    if len(ids) != len(set(ids)):
        raise common.fail("scene candidate contains duplicate element ids")
    return payload


def _ingest_patch(run: common.Run, source: Path) -> dict:
    from tools.revisions import scene_sha256

    patch = json.loads(source.read_text(encoding="utf-8"))
    required = {
        "kind",
        "case",
        "reference_sha256",
        "base_scene_sha256",
        "region_ids",
        "allowed_element_ids",
        "operations",
    }
    if not isinstance(patch, dict) or not required.issubset(patch):
        raise common.fail(
            "scene patch requires kind/case/reference/base_scene/regions/allowed ids/operations"
        )
    scene = read_json(run.scene_path)
    if patch["kind"] != "scene_patch":
        raise common.fail("patch kind must be scene_patch")
    if patch["case"] != scene["case"]:
        raise common.fail("scene patch case mismatch")
    if patch["reference_sha256"] != scene["reference_sha256"]:
        raise common.fail("scene patch reference mismatch")
    if patch["base_scene_sha256"] != scene_sha256(scene):
        raise common.fail("scene patch base_scene_sha256 is stale")
    if not isinstance(patch["region_ids"], list) or not patch["region_ids"]:
        raise common.fail("scene patch requires at least one region id")
    if not isinstance(patch["allowed_element_ids"], list):
        raise common.fail("scene patch allowed_element_ids must be an array")
    allowed = set(patch["allowed_element_ids"])
    if not allowed or any(not isinstance(item, str) or not item for item in allowed):
        raise common.fail("scene patch allowed_element_ids must contain stable ids")
    if not isinstance(patch["operations"], list) or not patch["operations"]:
        raise common.fail("scene patch requires explicit operations")
    by_id = {item["id"]: item for item in scene.get("elements", []) if item.get("id")}
    for operation in patch["operations"]:
        if not isinstance(operation, dict) or operation.get("op") not in {"upsert", "remove"}:
            raise common.fail("scene patch operations must be upsert or remove")
        if operation["op"] == "upsert":
            item = operation.get("element")
            if not isinstance(item, dict) or not item.get("id"):
                raise common.fail("upsert operation requires an element with stable id")
            element_id = item["id"]
            if element_id not in allowed:
                raise common.fail(f"patch cannot modify protected element: {element_id}")
            by_id[element_id] = item
        else:
            element_id = operation.get("element_id")
            if element_id not in allowed:
                raise common.fail(f"patch cannot remove protected element: {element_id}")
            by_id.pop(element_id, None)
    scene["elements"] = list(by_id.values())
    return scene


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure ingest", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--kind", choices=("svg", "scene", "patch"), default="svg")
    parser.add_argument("--rejected", action="store_true", help="reject the current Web SVG and create repair tasks")
    parser.add_argument("--fallback", choices=("svg_repair", "png_reconstruct"), default="png_reconstruct")
    parser.add_argument(
        "--candidate-origin",
        choices=CANDIDATE_ORIGINS,
        default="unknown",
        help="候选的可审计来源；未知时保留 unknown，禁止猜测模型",
    )
    parser.add_argument(
        "--candidate-role",
        choices=("external-seed", "reconstruction-candidate", "repair-candidate"),
        default=None,
        help="省略时按不可变 input_route 和当前工作流状态推导",
    )
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    if args.rejected:
        set_processing_mode(
            run,
            processing_mode=args.fallback,
            fidelity_profile="hybrid_fidelity" if args.fallback == "png_reconstruct" else None,
        )
        transition(run, "qa_failed", "candidate-rejected")
        transition(run, "repairing", f"fallback:{args.fallback}")
        tasks = build_region_tasks(run)
        sys.stdout.write(f"已进入 {args.fallback}；区域任务 {len(tasks['tasks'])} 个: {run.region_tasks_path}\n")
        return 0

    if args.candidate is None or not args.candidate.is_file():
        raise common.fail("candidate file is required unless --rejected is used")
    meta = run.load_meta()
    role = args.candidate_role
    if role is None:
        provenance = read_json(run.provenance_path)
        if (
            meta["input_route"] == "svg-seeded"
            and args.kind == "svg"
            and provenance.get("external_svg_seed") is None
        ):
            role = "external-seed"
        elif (
            meta["input_route"] == "svg-seeded"
            and args.kind == "svg"
            and provenance.get("external_svg_seed") is not None
            and meta["processing_mode"] == "svg_repair"
        ) or meta["workflow"]["state"] in ("qa_failed", "repairing"):
            role = "repair-candidate"
        else:
            role = "reconstruction-candidate"
    if meta["input_route"] == "reference-only" and role == "external-seed":
        raise common.fail("reference-only 案例不能摄取 external-seed")
    from tools.reference_inventory import require_frozen_inventory

    require_frozen_inventory(run)
    staging_root = common.PROJECT_ROOT / ".autofigure-staging"
    from tools.transactions import recoverable_case_transaction

    transaction_paths = [
        run.meta_path,
        run.scene_path,
        run.provenance_path,
        run.redraw_svg,
        run.external_seed_svg,
        run.source_gate_report_path,
        run.external_seed_source_gate_report_path,
        run.region_tasks_path,
    ]
    with recoverable_case_transaction(
        transaction_paths,
        staging_root=staging_root,
        label=f"ingest-{meta['case']}",
    ):
        with tempfile.TemporaryDirectory(
            prefix=f"{meta['case']}-candidate-", dir=staging_root
        ) as temporary:
            staged = Path(temporary) / args.candidate.name
            shutil.copy2(args.candidate, staged)
            if args.kind == "svg":
                try:
                    ET.parse(staged)
                except ET.ParseError as exc:
                    raise common.fail(f"invalid SVG candidate: {exc}") from exc
                from tools.source_gate import evaluate_case_source_gate

                gate = evaluate_case_source_gate(run, staged, candidate_role=role)
                from tools.contracts import record_source_gate_provenance

                record_source_gate_provenance(
                    run,
                    gate,
                    immutable_external_seed=role == "external-seed",
                )
                if gate["decision"] == "reject":
                    transition(
                        run,
                        "qa_failed",
                        "source-gate-rejected",
                        details={"blockers": gate["blockers"]},
                    )
                    build_region_tasks(run)
                    sys.stdout.write(
                        "候选被 source gate 拒绝；processing_mode="
                        f"{run.load_meta()['processing_mode']}\n"
                    )
                    return 2
                scene = read_json(run.scene_path)
                from tools.revisions import (
                    bind_canonical_svg,
                    materialize_svg,
                    read_svg_text_exact,
                )

                bind_canonical_svg(
                    scene,
                    read_svg_text_exact(staged),
                    source_role=role,
                    source_sha256=common.sha256_file(staged),
                )
                write_json(run.scene_path, scene)
                materialize_svg(run, scene)
                if role == "external-seed":
                    if run.external_seed_svg.is_file():
                        if common.sha256_file(
                            run.external_seed_svg
                        ) != common.sha256_file(staged):
                            raise common.fail(
                                "external seed differs from the immutable case seed"
                            )
                    else:
                        shutil.copy2(staged, run.external_seed_svg)
                    canonical_path = "external-seed.svg"
                else:
                    canonical_path = "scene.json"
            elif args.kind == "scene":
                write_json(run.scene_path, _ingest_scene(run, staged))
                canonical_path = "scene.json"
            else:
                write_json(run.scene_path, _ingest_patch(run, staged))
                canonical_path = "scene.json"
        record_candidate_provenance(
            run,
            args.candidate,
            kind=args.kind,
            origin=args.candidate_origin,
            role=role,
            canonical_path=canonical_path,
        )
        transition(
            run,
            "candidate",
            f"ingested:{args.kind}",
            details={
                "source_name": args.candidate.name,
                "canonical_path": canonical_path,
            },
        )
    sys.stdout.write(f"候选已接收（{args.kind}），状态=candidate\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
