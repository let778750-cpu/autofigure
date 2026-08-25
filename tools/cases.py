"""List and validate the two canonical Autofigure input-route case trees."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import (
    INPUT_ROUTES,
    PROCESSING_MODES,
    SCHEMA_VERSION,
    TASK_MODE,
    WORKFLOW_STATES,
    read_json,
)

INDEX_START = "<!-- AUTOFIGURE_CASE_INDEX:START -->"
INDEX_END = "<!-- AUTOFIGURE_CASE_INDEX:END -->"
WINDOWS_ABSOLUTE = re.compile(r"[A-Za-z]:\\")
PORTABLE_SUFFIXES = {".json", ".md"}
TRANSIENT_CASE_DIR_NAMES = {
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "cache",
    "candidate",
    "candidates",
    "session",
    "sessions",
    "temp",
    "tmp",
}
TRANSIENT_CASE_DIR_TOKENS = {
    "cache",
    "candidate",
    "candidates",
    "session",
    "sessions",
    "temp",
    "tmp",
}
REQUIRED_CONTRACTS = {
    "scene.json": "scene",
    "assets.json": "assets",
    "regions.json": "regions",
    "bindings.json": "bindings",
    "provenance.json": "provenance",
}


def _is_transient_case_directory(path: Path, case_dir: Path) -> bool:
    """Return whether a directory is runtime residue forbidden in a formal case."""

    relative = path.relative_to(case_dir)
    if relative.parts == ("qa", "powerpoint-live-case", "build"):
        return True
    name = path.name.lower()
    if name in TRANSIENT_CASE_DIR_NAMES:
        return True
    tokens = {token for token in re.split(r"[-_.]+", name.strip(".")) if token}
    return bool(tokens & TRANSIENT_CASE_DIR_TOKENS)


def _has_duplicate_reference_contract(group: list[dict[str, Any]]) -> bool:
    routes = {item["input_route"] for item in group}
    if len(routes) != len(group):
        return False

    comparison_groups = {item.get("comparison_group") for item in group}
    if None not in comparison_groups and len(comparison_groups) == 1:
        return True

    owners = [item for item in group if item.get("comparison_group")]
    if len(owners) != 1:
        return False
    owner = owners[0]
    peers = owner.get("comparison_peers")
    if not isinstance(peers, list) or not all(isinstance(item, str) for item in peers):
        return False
    if len(peers) != len(set(peers)):
        return False
    expected_peers = {
        f"{item['input_route']}/{item['case']}"
        for item in group
        if item is not owner
    }
    return set(peers) == expected_peers


def discover_cases(cases_root: Path = common.CASES_ROOT) -> tuple[list[dict[str, Any]], list[str]]:
    cases_root = cases_root.resolve()
    findings: list[str] = []
    records: list[dict[str, Any]] = []
    seen_ids: dict[str, Path] = {}

    for child in sorted(cases_root.iterdir()) if cases_root.is_dir() else []:
        if not child.is_dir() or child.name in INPUT_ROUTES or child.name == "generated":
            continue
        if (child / "run.json").is_file():
            findings.append(f"flat-case:{child}")

    for route in INPUT_ROUTES:
        route_root = cases_root / route
        if not route_root.is_dir():
            findings.append(f"missing-route-directory:{route_root}")
            continue
        for case_dir in sorted(path for path in route_root.iterdir() if path.is_dir()):
            case_findings, record = _inspect_case(case_dir, route)
            findings.extend(case_findings)
            if record is None:
                continue
            case_id = record["case"]
            if case_id in seen_ids:
                findings.append(f"duplicate-case-id:{case_id}:{seen_ids[case_id]}:{case_dir}")
            else:
                seen_ids[case_id] = case_dir
            records.append(record)

    by_reference: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_reference[record["reference_sha256"]].append(record)
    for sha256, group in by_reference.items():
        if len(group) < 2:
            continue
        if not _has_duplicate_reference_contract(group):
            findings.append(f"duplicate-reference-without-ab-contract:{sha256}")

    return records, findings


def _inspect_case(case_dir: Path, route: str) -> tuple[list[str], dict[str, Any] | None]:
    findings: list[str] = []
    run_path = case_dir / "run.json"
    if not run_path.is_file():
        return [f"missing-run:{case_dir}"], None
    try:
        meta = read_json(run_path)
    except Exception as exc:
        return [f"invalid-run:{run_path}:{exc}"], None

    case_id = meta.get("case")
    if case_id != case_dir.name:
        findings.append(f"case-directory-mismatch:{case_dir}:{case_id}")
    if meta.get("input_route") != route:
        findings.append(f"route-directory-mismatch:{case_dir}:{meta.get('input_route')}:{route}")
    if meta.get("processing_mode") not in PROCESSING_MODES:
        findings.append(f"invalid-processing-mode:{case_dir}:{meta.get('processing_mode')}")
    if meta.get("workflow", {}).get("state") not in WORKFLOW_STATES:
        findings.append(f"invalid-workflow-state:{case_dir}")
    if meta.get("schema_version") != SCHEMA_VERSION:
        findings.append(f"schema-version-mismatch:{run_path}:{meta.get('schema_version')}")
    if meta.get("task_mode") != TASK_MODE:
        findings.append(f"task-mode-mismatch:{run_path}:{meta.get('task_mode')}")
    for stale_key in ("source_mode", "source_abspath"):
        if stale_key in meta:
            findings.append(f"stale-run-key:{run_path}:{stale_key}")

    workflow_state = meta.get("workflow", {}).get("state")
    validation = meta.get("validation", {})
    release_manifest_path = case_dir / "release-manifest.json"
    if workflow_state == "approved":
        if validation.get("profile") != "strict":
            findings.append(f"approved-without-strict-validation:{run_path}")
        if validation.get("status") != "passed":
            findings.append(f"approved-validation-not-passed:{run_path}")
        blockers = validation.get("blockers")
        if not isinstance(blockers, list) or blockers:
            findings.append(f"approved-with-blockers:{run_path}")
        if meta.get("backend_mode") == "hybrid":
            live_evidence_path = case_dir / "qa" / "live-evidence.json"
            if not live_evidence_path.is_file():
                findings.append(f"approved-hybrid-live-evidence-missing:{live_evidence_path}")
        if not release_manifest_path.is_file():
            findings.append(f"approved-without-release-manifest:{release_manifest_path}")
    elif release_manifest_path.is_file():
        findings.append(f"release-manifest-without-approval:{release_manifest_path}")

    reference_path = case_dir / "reference.png"
    reference_sha256 = meta.get("source_sha256")
    if not reference_path.is_file():
        findings.append(f"missing-reference:{reference_path}")
    elif common.sha256_file(reference_path) != reference_sha256:
        findings.append(f"reference-hash-mismatch:{reference_path}")

    for filename, kind in REQUIRED_CONTRACTS.items():
        path = case_dir / filename
        if not path.is_file():
            findings.append(f"missing-contract:{path}")
            continue
        try:
            payload = read_json(path)
        except Exception as exc:
            findings.append(f"invalid-contract:{path}:{exc}")
            continue
        if payload.get("kind") != kind:
            findings.append(f"contract-kind-mismatch:{path}:{payload.get('kind')}:{kind}")
        if payload.get("case") != case_id:
            findings.append(f"contract-case-mismatch:{path}")
        if payload.get("reference_sha256") != reference_sha256:
            findings.append(f"contract-reference-mismatch:{path}")
        if payload.get("schema_version") != SCHEMA_VERSION:
            findings.append(f"contract-schema-mismatch:{path}:{payload.get('schema_version')}")
        if payload.get("task_mode") != TASK_MODE:
            findings.append(f"contract-task-mode-mismatch:{path}:{payload.get('task_mode')}")

    provenance_path = case_dir / "provenance.json"
    provenance = read_json(provenance_path) if provenance_path.is_file() else {}
    if provenance.get("input_route") != route:
        findings.append(f"provenance-route-mismatch:{provenance_path}")
    if provenance.get("task_mode") != TASK_MODE:
        findings.append(f"provenance-task-mode-mismatch:{provenance_path}")

    transient_roots: list[Path] = []
    directories = sorted(
        (item for item in case_dir.rglob("*") if item.is_dir()),
        key=lambda item: (len(item.relative_to(case_dir).parts), item.as_posix()),
    )
    for path in directories:
        if any(root in path.parents for root in transient_roots):
            continue
        if _is_transient_case_directory(path, case_dir):
            transient_roots.append(path)
            findings.append(f"transient-case-directory:{path}")

    for path in case_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in PORTABLE_SUFFIXES:
            continue
        relative_parts = path.relative_to(case_dir).parts
        if relative_parts[:2] == ("qa", "math"):
            # Native Office Math receipts intentionally record the local XSL engine path.
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if WINDOWS_ABSOLUTE.search(text):
            findings.append(f"nonportable-absolute-path:{path}")

    return findings, {
        "case": case_id or case_dir.name,
        "path": case_dir,
        "input_route": route,
        "processing_mode": meta.get("processing_mode", "[missing]"),
        "workflow_state": meta.get("workflow", {}).get("state", "[missing]"),
        "validation_status": meta.get("validation", {}).get("status", "not_run"),
        "reference_sha256": reference_sha256,
        "comparison_group": provenance.get("comparison_group"),
        "comparison_peers": provenance.get("comparison_peers"),
    }


def render_index(records: list[dict[str, Any]]) -> str:
    lines = [
        INDEX_START,
        "| 输入路线 | 案例 | 当前处理模式 | 工作流 | 最近验证 |",
        "|---|---|---|---|---|",
    ]
    for record in sorted(records, key=lambda item: (item["input_route"], item["case"])):
        rel = f"{record['input_route']}/{record['case']}"
        lines.append(
            f"| `{record['input_route']}` | [`{record['case']}/`]({rel}/) | "
            f"`{record['processing_mode']}` | `{record['workflow_state']}` | "
            f"`{record['validation_status']}` |"
        )
    lines.append(INDEX_END)
    return "\n".join(lines)


def _index_matches(cases_root: Path, records: list[dict[str, Any]]) -> bool:
    readme = cases_root / "README.md"
    if not readme.is_file():
        return False
    text = readme.read_text(encoding="utf-8")
    if INDEX_START not in text or INDEX_END not in text:
        return False
    actual = text[text.index(INDEX_START) : text.index(INDEX_END) + len(INDEX_END)]
    return actual == render_index(records)


def write_index(cases_root: Path, records: list[dict[str, Any]]) -> None:
    readme = cases_root / "README.md"
    text = readme.read_text(encoding="utf-8") if readme.is_file() else "# examples/ — 案例索引\n\n"
    generated = render_index(records)
    if INDEX_START in text and INDEX_END in text:
        start = text.index(INDEX_START)
        end = text.index(INDEX_END) + len(INDEX_END)
        text = text[:start] + generated + text[end:]
    else:
        text = text.rstrip() + "\n\n" + generated + "\n"
    readme.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure cases", description=__doc__)
    parser.add_argument("--cases-root", type=Path, default=common.CASES_ROOT)
    parser.add_argument("--check", action="store_true", help="验证分类、合同、哈希和索引")
    parser.add_argument("--write-index", action="store_true", help="重写 README 中的生成索引")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cases_root = args.cases_root.resolve()
    records, findings = discover_cases(cases_root)
    if args.write_index:
        write_index(cases_root, records)
    if args.check and not _index_matches(cases_root, records):
        findings.append(f"stale-case-index:{cases_root / 'README.md'}")

    serializable = [
        {**record, "path": str(record["path"])}
        for record in sorted(records, key=lambda item: (item["input_route"], item["case"]))
    ]
    if args.json:
        sys.stdout.write(
            json.dumps({"cases": serializable, "findings": findings}, ensure_ascii=False, indent=2)
            + "\n"
        )
    else:
        for record in serializable:
            sys.stdout.write(
                f"{record['input_route']}/{record['case']} "
                f"mode={record['processing_mode']} state={record['workflow_state']} "
                f"validation={record['validation_status']}\n"
            )
        for finding in findings:
            sys.stderr.write(f"ERROR {finding}\n")
    return 2 if args.check and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
