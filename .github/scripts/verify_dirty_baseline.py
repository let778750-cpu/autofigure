"""Capture and verify the sanitized dirty-worktree migration inventory.

The inventory is intentionally stored in the governance worktree.  The source
worktree is an explicit, read-only input and its absolute path is never written
to the repository.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs" / "dirty-baseline-migration.json"
DEFAULT_INVENTORY = ROOT / "docs" / "dirty-baseline-files.jsonl"
HEX_256_RE = re.compile(r"^[0-9a-f]{64}$")
OID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
CASE_ROOT_RE = re.compile(r"^examples/(?:reference-only|svg-seeded)/[^/]+/(.+)$")
CASE_SOURCE_BASENAMES = {
    "assets.json",
    "external-seed.svg",
    "prompt.md",
    "reference.png",
    "regions.json",
    "scene.json",
}


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _git(root: Path, *args: str) -> bytes:
    command = ["git", "-C", str(root), *args]
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_status(lines: list[str]) -> bytes:
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _status_lines(source_root: Path) -> list[str]:
    raw = _git(
        source_root,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    text = raw.decode("utf-8")
    if "\r" in text:
        text = text.replace("\r\n", "\n")
    lines = text.splitlines()
    for line in lines:
        if len(line) < 4 or line[2] != " ":
            raise ValueError(f"unsupported porcelain record: {line!r}")
        if "\n" in line[3:] or "\r" in line[3:]:
            raise ValueError("newline-bearing paths are not supported by this frozen baseline")
        if line[0] in "RC" or line[1] in "RC" or " -> " in line[3:]:
            raise ValueError("rename/copy records require a new baseline capture contract")
    return lines


def _legacy_sorted_status(source_root: Path, lines: list[str]) -> list[str]:
    """Reproduce the originally published PowerShell Sort-Object witness order."""
    executable = (
        shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    )
    if not executable:
        raise ValueError(
            "external capture/verification requires Windows PowerShell to reproduce "
            "the frozen status witness"
        )
    environment = os.environ.copy()
    environment["AUTOFIGURE_BASELINE_SOURCE_ROOT"] = str(source_root)
    command = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$lines = git -C $env:AUTOFIGURE_BASELINE_SOURCE_ROOT -c core.quotepath=false status --porcelain=v1 --untracked-files=all | Sort-Object
[Console]::Out.Write(($lines -join "`n") + "`n")
"""
    result = subprocess.run(
        [executable, "-NoProfile", "-Command", command],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    ordered = result.stdout.decode("utf-8").replace("\r\n", "\n").splitlines()
    if Counter(ordered) != Counter(lines):
        raise ValueError("PowerShell status witness differs from the direct git status records")
    return ordered


def _head_tree(source_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    raw = _git(source_root, "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "HEAD")
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, separator, raw_path = item.partition(b"\t")
        if not separator:
            raise ValueError("invalid git ls-tree record")
        tokens = header.decode("ascii").split()
        if len(tokens) != 3:
            raise ValueError("invalid git ls-tree header")
        path = raw_path.decode("utf-8").replace("\\", "/")
        result[path] = tokens[2]
    return result


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not normalized or ".." in path.parts or normalized != path.as_posix():
        raise ValueError(f"unsafe or non-canonical repository path: {value!r}")
    return normalized


def _explicit_groups(manifest: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    explicit: dict[str, str] = {}
    group_ids: set[str] = set()
    for group in manifest["groups"]:
        group_id = str(group["id"])
        group_ids.add(group_id)
        for path in group.get("explicit_paths", []):
            normalized = _safe_relative_path(str(path))
            previous = explicit.setdefault(normalized, group_id)
            if previous != group_id:
                raise ValueError(f"path is explicitly assigned to two groups: {normalized}")
    return explicit, group_ids


def _classify(path: str, explicit: dict[str, str]) -> str:
    if path in explicit:
        return explicit[path]
    if re.fullmatch(r"examples/route-comparison-[^/]+\.(?:json|md)", path):
        return "comparison"
    case_match = CASE_ROOT_RE.fullmatch(path)
    if case_match:
        remainder = case_match.group(1)
        if "/" not in remainder and remainder in CASE_SOURCE_BASENAMES:
            return "case-source"
        return "case-evidence"
    raise ValueError(f"unclassified dirty-baseline path: {path}")


def _content_record(path: Path) -> tuple[str, int | None, str | None]:
    if path.is_symlink():
        data = os.readlink(path).encode("utf-8")
        return "symlink", len(data), _sha256(data)
    if path.is_file():
        data = path.read_bytes()
        return "file", len(data), _sha256(data)
    if not path.exists():
        return "missing", None, None
    raise ValueError(f"unsupported dirty-baseline entry type: {path.name}")


def _snapshot(source_root: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    resolved = source_root.absolute()
    inside = _git(resolved, "rev-parse", "--is-inside-work-tree").decode("ascii").strip()
    prefix = _git(resolved, "rev-parse", "--show-prefix").decode("utf-8").strip()
    if inside != "true" or prefix:
        raise ValueError("--source-root must be the repository root")

    expected_commit = str(manifest["baseline"]["commit"])
    actual_commit = _git(resolved, "rev-parse", "HEAD").decode("ascii").strip()
    if actual_commit != expected_commit:
        raise ValueError(f"baseline HEAD mismatch: expected {expected_commit}, got {actual_commit}")

    lines = _status_lines(resolved)
    ordered_lines = _legacy_sorted_status(resolved, lines)
    status_sha = _sha256(_canonical_status(ordered_lines))
    expected_status_sha = str(manifest["baseline"]["status"]["sha256"])
    if status_sha != expected_status_sha:
        raise ValueError(
            f"baseline status mismatch: expected {expected_status_sha}, got {status_sha}"
        )

    explicit, group_ids = _explicit_groups(manifest)
    tree = _head_tree(resolved)
    status_order = {line: index for index, line in enumerate(ordered_lines)}
    records: list[dict[str, Any]] = []
    for line in lines:
        status = line[:2]
        relative = _safe_relative_path(line[3:])
        entry_type = "untracked" if status == "??" else "tracked"
        group = _classify(relative, explicit)
        if group not in group_ids:
            raise ValueError(f"classifier returned unknown group {group!r} for {relative}")
        content_kind, byte_count, content_sha = _content_record(resolved / relative)
        base_oid = tree.get(relative) if entry_type == "tracked" else None
        if entry_type == "tracked" and base_oid is None:
            raise ValueError(f"tracked baseline member has no HEAD tree object: {relative}")
        records.append(
            {
                "base_blob_oid": base_oid,
                "bytes": byte_count,
                "content_kind": content_kind,
                "entry_type": entry_type,
                "group": group,
                "index_status": status[0],
                "path": relative,
                "status": status,
                "status_order": status_order[line],
                "worktree_sha256": content_sha,
                "worktree_status": status[1],
            }
        )
    records.sort(key=lambda item: item["path"])
    return records, status_sha


def _record_line(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _inventory_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(_record_line(record) for record in records)


def _group_inventory_sha(records: list[dict[str, Any]], group_id: str) -> str:
    return _sha256(
        b"".join(_record_line(record) for record in records if record["group"] == group_id)
    )


def _validate_records(records: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_fields = {
        "base_blob_oid",
        "bytes",
        "content_kind",
        "entry_type",
        "group",
        "index_status",
        "path",
        "status",
        "status_order",
        "worktree_sha256",
        "worktree_status",
    }
    paths: set[str] = set()
    groups = {str(group["id"]): group for group in manifest["groups"]}
    for index, record in enumerate(records, start=1):
        if set(record) != required_fields:
            errors.append(f"record {index} has an unexpected field set")
            continue
        try:
            path = _safe_relative_path(str(record["path"]))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path in paths:
            errors.append(f"duplicate inventory path: {path}")
        paths.add(path)
        if record["group"] not in groups:
            errors.append(f"unknown group for {path}: {record['group']}")
        status = record["status"]
        if (
            not isinstance(status, str)
            or len(status) != 2
            or record["index_status"] != status[0]
            or record["worktree_status"] != status[1]
        ):
            errors.append(f"incoherent status fields for {path}")
        status_order = record["status_order"]
        if not isinstance(status_order, int) or status_order < 0:
            errors.append(f"invalid status_order for {path}")
        entry_type = record["entry_type"]
        if entry_type not in {"tracked", "untracked"}:
            errors.append(f"invalid entry_type for {path}")
        if (status == "??") != (entry_type == "untracked"):
            errors.append(f"status/entry_type mismatch for {path}")
        base_oid = record["base_blob_oid"]
        if entry_type == "tracked" and (
            not isinstance(base_oid, str) or not OID_RE.fullmatch(base_oid)
        ):
            errors.append(f"invalid tracked base_blob_oid for {path}")
        if entry_type == "untracked" and base_oid is not None:
            errors.append(f"untracked record has base_blob_oid: {path}")
        content_kind = record["content_kind"]
        content_sha = record["worktree_sha256"]
        byte_count = record["bytes"]
        if content_kind == "missing":
            if content_sha is not None or byte_count is not None:
                errors.append(f"missing record has content evidence for {path}")
        elif (
            content_kind not in {"file", "symlink"}
            or not isinstance(content_sha, str)
            or not HEX_256_RE.fullmatch(content_sha)
            or not isinstance(byte_count, int)
            or byte_count < 0
        ):
            errors.append(f"invalid content evidence for {path}")

    expected_count = int(manifest["coverage"]["expected_file_count"])
    if len(records) != expected_count:
        errors.append(f"inventory count mismatch: expected {expected_count}, got {len(records)}")
    if [record["path"] for record in records] != sorted(paths):
        errors.append("inventory records are not sorted by repository path")

    orders = [record["status_order"] for record in records]
    if sorted(orders) != list(range(len(records))):
        errors.append("status_order values must be unique and contiguous")
    ordered_records = sorted(records, key=lambda record: record["status_order"])
    status_lines = [f"{record['status']} {record['path']}" for record in ordered_records]
    actual_status_sha = _sha256(_canonical_status(status_lines))
    if actual_status_sha != manifest["baseline"]["status"]["sha256"]:
        errors.append("inventory does not reproduce the frozen baseline status SHA-256")

    for group_id, group in groups.items():
        members = [record for record in records if record["group"] == group_id]
        tracked = sum(record["entry_type"] == "tracked" for record in members)
        untracked = sum(record["entry_type"] == "untracked" for record in members)
        expected = group["counts"]
        actual_counts = {"tracked": tracked, "untracked": untracked, "total": len(members)}
        if actual_counts != expected:
            errors.append(f"group count mismatch for {group_id}: {actual_counts} != {expected}")
        ordered_members = sorted(members, key=lambda record: record["status_order"])
        group_status = _sha256(
            _canonical_status(
                [f"{record['status']} {record['path']}" for record in ordered_members]
            )
        )
        if group_status != group["status_sha256"]:
            errors.append(f"group status SHA-256 mismatch for {group_id}")
        if _group_inventory_sha(records, group_id) != group.get("inventory_sha256"):
            errors.append(f"group inventory SHA-256 mismatch for {group_id}")
    return errors


def _read_inventory(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    raw = path.read_bytes()
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line:
            raise ValueError(f"blank inventory line at {line_number}")
        value = json.loads(raw_line.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"inventory line {line_number} is not an object")
        records.append(value)
    if raw and not raw.endswith(b"\n"):
        raise ValueError("inventory must end in LF")
    return records, raw


def capture(source_root: Path, manifest_path: Path, inventory_path: Path) -> None:
    manifest = _load_object(manifest_path)
    records, _ = _snapshot(source_root, manifest)
    errors = _validate_records(records, {
        **manifest,
        "groups": [
            {**group, "inventory_sha256": _group_inventory_sha(records, str(group["id"]))}
            for group in manifest["groups"]
        ],
    })
    if errors:
        raise ValueError("; ".join(errors))

    data = _inventory_bytes(records)
    for group in manifest["groups"]:
        group["inventory_sha256"] = _group_inventory_sha(records, str(group["id"]))
    manifest["file_inventory"] = {
        "path": inventory_path.relative_to(ROOT).as_posix(),
        "schema_version": "1.0.0",
        "record_count": len(records),
        "sha256": _sha256(data),
        "canonicalization": {
            "encoding": "UTF-8",
            "line_format": "canonical JSON with sorted keys and compact separators",
            "record_order": "repository path ascending",
            "record_separator": "LF",
            "trailing_separator": True,
        },
        "source_root_persisted": False,
    }

    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_bytes(data)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"captured {len(records)} records: {_sha256(data)}")


def verify(
    manifest_path: Path, inventory_path: Path, source_root: Path | None = None
) -> list[str]:
    manifest = _load_object(manifest_path)
    records, raw = _read_inventory(inventory_path)
    errors = _validate_records(records, manifest)
    inventory = manifest.get("file_inventory")
    if not isinstance(inventory, dict):
        errors.append("manifest file_inventory metadata is missing")
    else:
        if inventory.get("path") != inventory_path.relative_to(ROOT).as_posix():
            errors.append("manifest file_inventory.path is not repository-relative and canonical")
        if inventory.get("record_count") != len(records):
            errors.append("manifest file_inventory.record_count mismatch")
        if inventory.get("sha256") != _sha256(raw):
            errors.append("manifest file_inventory.sha256 mismatch")
        if inventory.get("source_root_persisted") is not False:
            errors.append("manifest must state that the absolute source root is not persisted")

    if source_root is not None:
        current, _ = _snapshot(source_root, manifest)
        if current != records:
            errors.append("external source worktree does not match the frozen per-file inventory")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--source-root", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source-root", type=Path)
    args = parser.parse_args(argv)

    manifest_path = args.manifest.resolve()
    inventory_path = args.inventory.resolve()
    try:
        if args.command == "capture":
            capture(args.source_root, manifest_path, inventory_path)
            return 0
        errors = verify(manifest_path, inventory_path, args.source_root)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print("dirty baseline inventory: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
