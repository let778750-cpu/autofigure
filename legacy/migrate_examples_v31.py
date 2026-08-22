"""One-time, explicit migration map for the three historical showcase cases."""

from __future__ import annotations

from pathlib import Path

from tools.v2 import common
from tools.v2.contracts import TASK_MODE, migrate_legacy_run, read_json, utc_now, write_json

MIGRATIONS = {
    "01-modular-agent": {
        "reference_sha256": "792a16d4bd2c26cca9fca79668395a987825ab75eb2bc8a65f2d42a47c38a340",
        "processing_mode": "png_reconstruct",
        "workflow_state": "qa_failed",
        "source_original_name": "01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png",
        "comparison_group": "modular-agent-route-ab",
        "external_svg_seed": {
            "kind": "svg",
            "role": "external-seed",
            "origin": "web-vlm",
            "provider": "OpenAI",
            "model": None,
            "interface": "web",
            "sha256": None,
            "exact_bytes_available": False,
            "evidence": "examples README and git history; original R1 bytes are no longer canonical",
        },
        "current_role": "repair-candidate",
        "current_origin": "mixed-vlm-and-deterministic-repair",
        "validation": {
            "profile": "strict",
            "status": "failed",
            "checked_at": "20260821T113032Z",
            "blockers": [
                "region:task-guided-allocator-topology",
                "region:six-bicolor-state-circles",
                "region:rollout-arrow-topology",
                "region:observation-arrows",
                "live-evidence-missing",
            ],
        },
    },
    "02-thinking-diffusion": {
        "reference_sha256": "3e66655ae080dc92cc04d3c011f908a3aec83ca1ad89cf0559f503c81c970b54",
        "processing_mode": "svg_import",
        "workflow_state": "candidate",
        "source_original_name": "02_2026_CVPR_2026_Thinking_Diffusion_Penalize_and_Guide_Visual-Grounde.png",
        "comparison_group": None,
        "external_svg_seed": {
            "kind": "svg",
            "role": "external-seed",
            "origin": "human",
            "provider": "Kimi",
            "model": None,
            "interface": "web-assisted-manual",
            "sha256": "CURRENT",
            "exact_bytes_available": True,
            "evidence": "examples README and git history",
        },
        "current_role": "external-seed",
        "current_origin": "human",
        "validation": {
            "profile": "standard",
            "status": "diagnostic",
            "checked_at": None,
            "blockers": ["regions:no-critical-regions"],
        },
    },
    "03-llmind": {
        "reference_sha256": "997a8f665b52cec1113cfe0c5507e0bd4b44217329f93b101bf79f2118bf3953",
        "processing_mode": "svg_import",
        "workflow_state": "candidate",
        "source_original_name": "03_2026_CVPR_2026_LLMind_Bio-inspired_Training-free_Adaptive_Visual_Re.png",
        "comparison_group": None,
        "external_svg_seed": {
            "kind": "svg",
            "role": "external-seed",
            "origin": "web-vlm",
            "provider": "OpenAI",
            "model": None,
            "interface": "web",
            "sha256": None,
            "exact_bytes_available": False,
            "evidence": "examples README and git history; canonical SVG includes later atomic repair",
        },
        "current_role": "repair-candidate",
        "current_origin": "deterministic-atomic-repair",
        "validation": {
            "profile": "standard",
            "status": "diagnostic",
            "checked_at": None,
            "blockers": ["regions:no-critical-regions"],
        },
    },
}


def migrate(project_root: Path = common.PROJECT_ROOT) -> None:
    route_root = project_root.resolve() / "examples" / "svg-seeded"
    for case_id, spec in MIGRATIONS.items():
        run = common.Run(route_root / case_id)
        if not run.meta_path.is_file():
            raise RuntimeError(f"missing migrated case directory: {run.root}")
        if common.sha256_file(run.source_png) != spec["reference_sha256"]:
            raise RuntimeError(f"reference hash mismatch before migration: {case_id}")
        migrate_legacy_run(
            run,
            input_route="svg-seeded",
            processing_mode=spec["processing_mode"],
            workflow_state=spec["workflow_state"],
        )
        meta = run.load_meta()
        meta["source_original_name"] = spec["source_original_name"]
        meta["reference_path"] = "reference.png"
        meta["task_mode"] = TASK_MODE
        meta["validation"] = spec["validation"]
        write_json(run.meta_path, meta)

        provenance = read_json(run.provenance_path)
        current_sha256 = common.sha256_file(run.redraw_svg)
        external_seed = dict(spec["external_svg_seed"])
        if external_seed["sha256"] == "CURRENT":
            external_seed["sha256"] = current_sha256
        provenance["input_route"] = "svg-seeded"
        provenance["task_mode"] = TASK_MODE
        provenance["reference"] = {
            "path": "reference.png",
            "sha256": spec["reference_sha256"],
            "original_name": spec["source_original_name"],
        }
        provenance["external_svg_seed"] = external_seed
        provenance["candidate_history"] = [
            {
                "kind": "svg",
                "role": spec["current_role"],
                "origin": spec["current_origin"],
                "source_name": "redraw.svg",
                "canonical_path": "redraw.svg",
                "sha256": current_sha256,
                "ingested_at": "legacy-before-v3.1",
            }
        ]
        provenance["comparison_group"] = spec["comparison_group"]
        provenance["updated_at"] = utc_now()
        write_json(run.provenance_path, provenance)


def main() -> int:
    migrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
