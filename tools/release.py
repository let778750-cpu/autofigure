"""autofigure release — 生成与校验案例根 release-manifest.json。

只有六个 QA 维度全部 pass（等价 strict approved）的案例才能生成 release
manifest；manifest 位于案例根（扁平布局，不建子目录），把发布面文件与
``qa/qa-status.json`` 哈希绑定到当前参考与产物。``--check`` 重算全部哈希与
六维度，任何漂移都非零退出。release 只读取 QA 证据，不改写
``qa/qa-status.json``（该文件由 ``autofigure check`` 重写）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import SCHEMA_VERSION, read_json, utc_now, write_json

RELEASE_MANIFEST_NAME = "release-manifest.json"
RELEASE_ARTIFACTS = (
    "redraw.pptx",
    "redraw.svg",
    "render.png",
    "preview.png",
    "check-report.md",
    "run.json",
    "scene.json",
    "assets.json",
    "regions.json",
    "bindings.json",
    "provenance.json",
)


def release_manifest_path(run: common.Run) -> Path:
    return run.root / RELEASE_MANIFEST_NAME


def build_release_manifest(run: common.Run) -> dict[str, Any]:
    from tools.qa_status import QA_STATUS_NAME

    meta = run.load_meta()
    qa_status_path = run.qa_dir / QA_STATUS_NAME
    if not qa_status_path.is_file():
        raise common.fail(f"缺少 qa/{QA_STATUS_NAME}，请先运行 autofigure check: {run.root}")
    artifacts: dict[str, str] = {}
    for name in RELEASE_ARTIFACTS:
        path = run.root / name
        if not path.is_file():
            raise common.fail(f"release 发布面文件缺失: {path}")
        artifacts[name] = common.sha256_file(path)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "release_manifest",
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "artifacts": artifacts,
        "qa_status_sha256": common.sha256_file(qa_status_path),
        "generated_at": utc_now(),
    }


def validate_release_manifest(run: common.Run) -> list[str]:
    path = release_manifest_path(run)
    if not path.is_file():
        return ["release-manifest:missing"]
    from tools.qa_status import QA_STATUS_NAME

    manifest = read_json(path)
    meta = run.load_meta()
    blockers: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        blockers.append("release-manifest:schema-version-mismatch")
    if manifest.get("kind") != "release_manifest":
        blockers.append("release-manifest:kind-mismatch")
    if manifest.get("case") != meta.get("case"):
        blockers.append("release-manifest:case-mismatch")
    if manifest.get("reference_sha256") != meta.get("source_sha256"):
        blockers.append("release-manifest:reference-mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(RELEASE_ARTIFACTS):
        blockers.append("release-manifest:artifact-set-mismatch")
    if isinstance(artifacts, dict):
        for name, digest in artifacts.items():
            artifact_path = run.root / name
            if not artifact_path.is_file():
                blockers.append(f"release-manifest:artifact-missing:{name}")
            elif common.sha256_file(artifact_path) != digest:
                blockers.append(f"release-manifest:artifact-drift:{name}")
    qa_status_path = run.qa_dir / QA_STATUS_NAME
    if not qa_status_path.is_file():
        blockers.append("release-manifest:qa-status-missing")
    elif manifest.get("qa_status_sha256") != common.sha256_file(qa_status_path):
        blockers.append("release-manifest:qa-status-drift")
    return list(dict.fromkeys(blockers))


def _print_failing_dimensions(dimensions: dict[str, dict[str, Any]]) -> bool:
    failing = False
    for name, dimension in dimensions.items():
        if dimension["status"] == "pass":
            continue
        failing = True
        sys.stdout.write(f"- {name}: {dimension['status']}\n")
        for blocker in dimension["blockers"]:
            sys.stdout.write(f"  - {blocker}\n")
    return failing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure release", description=__doc__)
    parser.add_argument("run_dir", type=Path, help="run 目录")
    parser.add_argument(
        "--check",
        action="store_true",
        help="重算哈希与六维度校验既有 manifest，漂移即失败",
    )
    args = parser.parse_args(argv)

    run = common.open_run(args.run_dir)
    from tools.qa_status import compute_qa_dimensions

    if args.check:
        manifest_blockers = validate_release_manifest(run)
        dimensions = compute_qa_dimensions(run, check_manifest=False)
        if manifest_blockers:
            for blocker in manifest_blockers:
                sys.stdout.write(f"manifest 漂移: {blocker}\n")
        failing = _print_failing_dimensions(dimensions)
        if manifest_blockers or failing:
            return 2
        sys.stdout.write(f"release manifest 校验通过: {release_manifest_path(run)}\n")
        return 0

    # 既有 manifest 即将被原子替换，其陈旧与否不构成生成闸门（check_manifest=False）。
    dimensions = compute_qa_dimensions(run, check_manifest=False)
    if _print_failing_dimensions(dimensions):
        sys.stdout.write("release 拒绝：上述 QA 维度未全部 pass。\n")
        return 2
    manifest = build_release_manifest(run)
    write_json(release_manifest_path(run), manifest)
    sys.stdout.write(f"release manifest 已生成: {release_manifest_path(run)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
