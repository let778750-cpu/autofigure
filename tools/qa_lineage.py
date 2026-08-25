"""Bind every machine-readable QA document to one canonical scene revision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import read_json, write_json
from tools.revisions import compiler_fingerprint, revision_id, scene_sha256


MANIFEST_NAME = "qa-lineage-manifest.json"


def _reports(run: common.Run) -> list[Path]:
    return sorted(
        (
            path
            for path in run.qa_dir.rglob("*.json")
            if path.name != MANIFEST_NAME and ".autofigure" not in path.parts
        ),
        key=lambda path: path.relative_to(run.root).as_posix(),
    )


def write_qa_lineage_manifest(run: common.Run) -> dict[str, Any]:
    scene = read_json(run.scene_path)
    meta = run.load_meta()
    identity = {
        "revision_id": revision_id(scene),
        "scene_sha256": scene_sha256(scene),
        "compiler_fingerprint": compiler_fingerprint(),
    }
    manifest = {
        "schema_version": "4.0.0",
        "kind": "qa_lineage_manifest",
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        **identity,
        "reports": [
            {
                "path": path.relative_to(run.root).as_posix(),
                "sha256": common.sha256_file(path),
                **identity,
            }
            for path in _reports(run)
        ],
    }
    write_json(run.qa_dir / MANIFEST_NAME, manifest)
    return manifest


def validate_qa_lineage_manifest(run: common.Run) -> list[str]:
    path = run.qa_dir / MANIFEST_NAME
    if not path.is_file():
        return ["lineage:qa-manifest-missing"]
    manifest = read_json(path)
    scene = read_json(run.scene_path)
    expected = {
        "revision_id": revision_id(scene),
        "scene_sha256": scene_sha256(scene),
        "compiler_fingerprint": compiler_fingerprint(),
    }
    blockers: list[str] = []
    for key, value in expected.items():
        if manifest.get(key) != value:
            blockers.append(f"lineage:qa-{key.replace('_', '-')}-mismatch")
    expected_paths = {
        item["path"]: item
        for item in manifest.get("reports", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    current_paths = {
        item.relative_to(run.root).as_posix(): item for item in _reports(run)
    }
    if set(expected_paths) != set(current_paths):
        blockers.append("lineage:qa-report-set-mismatch")
    for relative, report_path in current_paths.items():
        record = expected_paths.get(relative, {})
        if record.get("sha256") != common.sha256_file(report_path):
            blockers.append(f"lineage:qa-report-hash-mismatch:{relative}")
        for key, value in expected.items():
            if record.get(key) != value:
                blockers.append(f"lineage:qa-report-revision-mismatch:{relative}")
                break
    return list(dict.fromkeys(blockers))

