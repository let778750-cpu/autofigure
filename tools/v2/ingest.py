"""Ingest model-independent SVG/scene/patch candidates or reject a bad SVG."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.v2 import common
from tools.v2.contracts import (
    read_json,
    record_candidate_provenance,
    set_processing_mode,
    transition,
    write_json,
)


def build_region_tasks(run: common.Run) -> dict:
    regions = read_json(run.regions_path)
    meta = run.load_meta()
    tasks = {
        "schema_version": meta["schema_version"],
        "kind": "region_tasks",
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "input_route": meta["input_route"],
        "processing_mode": meta["processing_mode"],
        "fidelity_profile": meta["fidelity_profile"],
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
            }
            for region in regions.get("regions", [])
        ],
    }
    write_json(run.region_tasks_path, tasks)
    return tasks


def _ingest_scene(run: common.Run, source: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise common.fail("scene candidate must be a JSON object with an elements array")
    current = read_json(run.scene_path)
    payload.setdefault("schema_version", current["schema_version"])
    payload.setdefault("kind", "scene")
    payload.setdefault("case", current["case"])
    payload.setdefault("reference_sha256", current["reference_sha256"])
    if payload["reference_sha256"] != current["reference_sha256"]:
        raise common.fail("scene candidate reference hash does not match this run")
    write_json(run.scene_path, payload)


def _ingest_patch(run: common.Run, source: Path) -> None:
    patch = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(patch, dict) or not isinstance(patch.get("elements"), list):
        raise common.fail("region patch must contain an elements array")
    scene = read_json(run.scene_path)
    by_id = {item["id"]: item for item in scene.get("elements", []) if item.get("id")}
    for item in patch["elements"]:
        if not isinstance(item, dict) or not item.get("id"):
            raise common.fail("every patched scene element requires a stable id")
        by_id[item["id"]] = item
    scene["elements"] = list(by_id.values())
    write_json(run.scene_path, scene)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure ingest", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--kind", choices=("svg", "scene", "patch"), default="svg")
    parser.add_argument("--rejected", action="store_true", help="reject the current Web SVG and create repair tasks")
    parser.add_argument("--fallback", choices=("svg_repair", "png_reconstruct"), default="png_reconstruct")
    parser.add_argument(
        "--candidate-origin",
        choices=("web-vlm", "local-vlm", "codex", "human", "unknown"),
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
        elif meta["workflow"]["state"] in ("qa_failed", "repairing"):
            role = "repair-candidate"
        else:
            role = "reconstruction-candidate"
    if meta["input_route"] == "reference-only" and role == "external-seed":
        raise common.fail("reference-only 案例不能摄取 external-seed")
    if args.kind == "svg":
        try:
            ET.parse(args.candidate)
        except ET.ParseError as exc:
            raise common.fail(f"invalid SVG candidate: {exc}") from exc
        shutil.copy2(args.candidate, run.redraw_svg)
        canonical_path = "redraw.svg"
    elif args.kind == "scene":
        _ingest_scene(run, args.candidate)
        canonical_path = "scene.json"
    else:
        _ingest_patch(run, args.candidate)
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
        details={"source_name": args.candidate.name, "canonical_path": canonical_path},
    )
    sys.stdout.write(f"候选已接收（{args.kind}），状态=candidate\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
