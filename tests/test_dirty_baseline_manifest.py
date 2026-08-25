from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "dirty-baseline-migration.json"
INVENTORY_PATH = ROOT / "docs" / "dirty-baseline-files.jsonl"
VERIFIER = ROOT / ".github" / "scripts" / "verify_dirty_baseline.py"


def _records() -> tuple[dict, list[dict], bytes]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    raw = INVENTORY_PATH.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    return manifest, records, raw


def test_dirty_baseline_inventory_is_complete_sanitized_and_hash_bound() -> None:
    manifest, records, raw = _records()
    inventory = manifest["file_inventory"]

    assert raw.endswith(b"\n")
    assert len(records) == inventory["record_count"] == 432
    assert hashlib.sha256(raw).hexdigest() == inventory["sha256"]
    assert inventory["source_root_persisted"] is False

    paths = [record["path"] for record in records]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))
    assert sorted(record["status_order"] for record in records) == list(range(432))
    assert all(not PurePosixPath(path).is_absolute() and ".." not in path.split("/") for path in paths)
    assert all(record["worktree_sha256"] and len(record["worktree_sha256"]) == 64 for record in records)

    published = MANIFEST_PATH.read_text(encoding="utf-8") + raw.decode("utf-8")
    assert "D:\\" not in published
    assert "D:/" not in published
    assert "AI智能绘图" not in published
    assert "/history/" not in published


def test_dirty_baseline_groups_bind_exact_serial_stage_branches() -> None:
    manifest, records, _ = _records()
    expected = [
        ("geometry", "codex/schema4-geometry-foundation-v1", 16),
        ("reference-contracts", "codex/schema4-reference-contracts-v1", 13),
        ("source-lineage", "codex/schema4-source-lineage-v1", 19),
        ("strict-repair", "codex/schema4-strict-repair-v1", 13),
        ("docs", "codex/schema4-doc-sync-v1", 8),
        ("case-source", "codex/case-<case-id>-source-v1", 29),
        ("case-evidence", "codex/case-<case-id>-evidence-v1", 327),
        ("comparison", "codex/route-comparison-v1", 7),
    ]
    actual = [
        (group["id"], group["branch_hint"], group["counts"]["total"])
        for group in manifest["groups"]
    ]
    assert actual == expected

    for group in manifest["groups"]:
        canonical = b"".join(
            (
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            for record in records
            if record["group"] == group["id"]
        )
        assert hashlib.sha256(canonical).hexdigest() == group["inventory_sha256"]


def test_dirty_baseline_verifier_passes_without_original_worktree() -> None:
    result = subprocess.run(
        [sys.executable, str(VERIFIER), "verify"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dirty baseline inventory: PASS" in result.stdout
