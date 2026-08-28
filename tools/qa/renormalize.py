"""Renormalize hash-bound case evidence to the repository's canonical LF bytes.

背景（Issue #18/#19 治理发现）：历史上 Windows 工作树以 CRLF 字节写入哈希绑定
的合同/收据/清单，git 以 LF blob 入库；在 ``core.autocrlf=true`` 的机器上检出
又被 smudge 回 CRLF。三种漂移形态需要不同处置：

1. ``crlf-bound, lf-bytes`` —— 记录绑定了旧 CRLF 字节，文件已是 LF（仓库规范）：
   以无损证明（当前内容的 CRLF 形态哈希 == 记录绑定）把绑定重写为 LF 哈希；
2. ``lf-bound, crlf-worktree`` —— 记录正确（LF），工作树被 autocrlf 检出成
   CRLF：把工作树文件规范化回 LF（内容等价，哈希随之与记录一致）；
3. ``real drift`` —— 内容真实变更（非换行差异）：拒绝改写，如实报告，交人工
   决策（例：redraw.svg 更新后 scene.canonical 未 rebind 的管线脱节）。

qa-lineage-manifest 是纯哈希索引（派生证据），直接按当前文件重建。

用法：
    autofigure renormalize <run_dir> [--check]
    autofigure renormalize --all [--check]     # examples/ 下全部案例

``--check`` 只读报告（CI 门禁形态）：任何未一致（EOL-ONLY 或 REAL-DRIFT）都
返回非零。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from tools.core import common
from tools.core.contracts import read_json, write_json

CONSISTENT = "consistent"
REBOUND = "rebound"
REWRITTEN = "rewritten-to-lf"
REAL_DRIFT = "real-drift"
MISSING = "missing"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _classify(data: bytes, bound: str | None) -> str | None:
    """Return 'consistent' | 'lf-bound-crlf-bytes' | 'crlf-bound-lf-bytes' | None."""
    if bound is None:
        return None
    if _sha(data) == bound:
        return "consistent"
    lf = data.replace(b"\r\n", b"\n")
    if _sha(lf) == bound:
        return "lf-bound-crlf-bytes"
    crlf = lf.replace(b"\n", b"\r\n")
    if _sha(crlf) == bound:
        return "crlf-bound-lf-bytes"
    return None


def _renormalize_file_binding(
    record_path: Path,
    record: dict[str, Any],
    field: str,
    target_path: Path,
    *,
    apply: bool,
) -> list[str]:
    """Repair one file-byte binding inside a JSON record (case 1/2)."""

    bound = record.get(field)
    if not target_path.is_file():
        return [f"{field}:{MISSING}"]
    data = target_path.read_bytes()
    state = _classify(data, bound)
    if state == "consistent":
        return [f"{field}:{CONSISTENT}"]
    if state == "crlf-bound-lf-bytes":
        if apply:
            record[field] = _sha(data)
            write_json(record_path, record)
        return [f"{field}:{REBOUND}"]
    if state == "lf-bound-crlf-bytes":
        if apply:
            target_path.write_bytes(data.replace(b"\r\n", b"\n"))
        return [f"{field}:{REWRITTEN}"]
    return [f"{field}:{REAL_DRIFT}"]


def _lf_normalize_hash_bound_text(run: common.Run) -> None:
    """把参与哈希绑定的文本证据统一为 LF 字节（仓库规范形态）。

    CRLF 工作树（autocrlf 检出残影）下直接重建 lineage 会把 CRLF 哈希写入
    manifest，而 git 入库时又规范化为 LF——跨检出必然失配。先统一 LF，
    之后的全部绑定与重建都落在规范字节上。
    """

    targets = [
        run.regions_path,
        run.scene_path,
        run.provenance_path,
        run.bindings_path,
        run.meta_path,
        run.external_seed_svg,
        run.redraw_svg,
        run.region_tasks_path,
    ]
    for path in targets:
        if path.is_file():
            data = path.read_bytes()
            if b"\r\n" in data:
                path.write_bytes(data.replace(b"\r\n", b"\n"))
    if run.qa_dir.is_dir():
        for path in run.qa_dir.rglob("*.json"):
            if path.name == "qa-lineage-manifest.json":
                continue
            data = path.read_bytes()
            if b"\r\n" in data:
                path.write_bytes(data.replace(b"\r\n", b"\n"))


def renormalize_case(run: common.Run, *, apply: bool, rebind_carrier: bool = False) -> list[str]:
    notes: list[str] = []
    if apply:
        _lf_normalize_hash_bound_text(run)

    # 1) inventory receipt 的文件字节绑定
    receipt_path = run.qa_dir / "reference-inventory-receipt.json"
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        for field, target in (
            ("regions_sha256", run.regions_path),
            ("region_tasks_sha256", run.region_tasks_path),
        ):
            notes.extend(
                _renormalize_file_binding(receipt_path, receipt, field, target, apply=apply)
            )

    # 2) provenance 的外部 seed 字节绑定
    provenance_path = run.provenance_path
    provenance = read_json(provenance_path)
    seed_record = provenance.get("external_svg_seed")
    if isinstance(seed_record, dict) and run.external_seed_svg.is_file():
        bound = seed_record.get("sha256")
        data = run.external_seed_svg.read_bytes()
        state = _classify(data, bound)
        if state == "consistent":
            notes.append("seed:consistent")
        elif state == "crlf-bound-lf-bytes":
            if apply:
                seed_record["sha256"] = _sha(data)
                write_json(provenance_path, provenance)
            notes.append("seed:" + REBOUND)
        elif state == "lf-bound-crlf-bytes":
            if apply:
                run.external_seed_svg.write_bytes(data.replace(b"\r\n", b"\n"))
            notes.append("seed:" + REWRITTEN)
        else:
            notes.append("seed:" + REAL_DRIFT)

    # 3) scene 的 canonical_svg 字节绑定
    scene_path = run.scene_path
    scene = read_json(scene_path)
    carrier = scene.get("canonical_svg", {})
    if isinstance(carrier, dict) and carrier.get("sha256") and run.redraw_svg.is_file():
        bound = carrier["sha256"]
        data = run.redraw_svg.read_bytes()
        state = _classify(data, bound)
        if state == "consistent":
            notes.append("scene.canonical:consistent")
        elif state == "crlf-bound-lf-bytes":
            if apply:
                carrier["sha256"] = _sha(data)
                write_json(scene_path, scene)
            notes.append("scene.canonical:" + REBOUND)
        elif state == "lf-bound-crlf-bytes":
            if apply:
                run.redraw_svg.write_bytes(data.replace(b"\r\n", b"\n"))
            notes.append("scene.canonical:" + REWRITTEN)
        elif rebind_carrier and apply:
            # 补做历史缺失的 rebind：redraw.svg 是后续修复流程的合法产物，
            # 当初更新载体后未同步 scene.canonical（管线缺口已由 check 的
            # carrier-vs-redraw 门禁防复发）。rebind 后用项目自身的
            # stamp_active_revision 同步 revision/receipt/bindings 全链。
            from tools.core.revisions import stamp_active_revision

            lf_bytes = data.replace(b"\r\n", b"\n")
            run.redraw_svg.write_bytes(lf_bytes)
            carrier["sha256"] = _sha(lf_bytes)
            write_json(scene_path, scene)
            stamp_active_revision(run)
            notes.append("scene.canonical:rebound-to-current-bytes")
        else:
            notes.append("scene.canonical:" + REAL_DRIFT)

    # 4) lineage manifest：纯哈希索引，按当前文件重建（apply 模式）
    from tools.qa.qa_lineage import validate_qa_lineage_manifest, write_qa_lineage_manifest

    lineage_blockers = validate_qa_lineage_manifest(run)
    if lineage_blockers:
        if apply:
            write_qa_lineage_manifest(run)
        notes.append("lineage:" + ("rebuilt" if apply else "stale"))

    return notes


def _iter_runs(cases_root: Path):
    from tools.qa.cases import discover_cases

    records, _ = discover_cases(cases_root)
    for record in records:
        yield common.open_run(record["path"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autofigure renormalize", description=__doc__)
    parser.add_argument("run_dir", type=Path, nargs="?")
    parser.add_argument("--all", action="store_true", help="examples/ 下全部案例")
    parser.add_argument("--cases-root", type=Path, default=common.CASES_ROOT)
    parser.add_argument("--check", action="store_true", help="只读报告；未一致即非零退出")
    parser.add_argument(
        "--rebind-carrier",
        action="store_true",
        help="对 scene.canonical 与 redraw.svg 的真实脱节补做 rebind"
        "（历史修复流程更新载体后未同步 scene 的管线缺口；"
        "rebind 后以 stamp_active_revision 同步 revision 全链）",
    )
    args = parser.parse_args(argv)

    if not args.all and args.run_dir is None:
        raise common.fail("renormalize: 需要 run_dir 或 --all")
    runs = (
        _iter_runs(args.cases_root.resolve())
        if args.all
        else iter([common.open_run(args.run_dir)])
    )
    bad = 0
    for run in runs:
        notes = renormalize_case(
            run, apply=not args.check, rebind_carrier=args.rebind_carrier
        )
        pending = any(
            note.rsplit(":", 1)[-1] in (REBOUND, REWRITTEN, REAL_DRIFT, MISSING, "stale")
            for note in notes
        )
        if pending:
            bad += 1
        sys.stdout.write(f"{'DRIFT' if pending else 'OK'} {run.load_meta()['case']}: {', '.join(notes)}\n")
    if bad:
        sys.stderr.write(f"renormalize: {bad} case(s) not consistent\n")
    return 1 if (bad and args.check) else 0


if __name__ == "__main__":
    raise SystemExit(main())
