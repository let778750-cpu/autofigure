"""QA 状态语义：把 strict 验收结论拆成六个机器可读维度。

``autofigure check`` 在 record_validation 之后把六个维度重写进
``qa/qa-status.json``；``autofigure release`` 复用同一计算，并把该文件哈希绑定进
``release-manifest.json``。维度只读取既有证据文件，不新建 OCR 等证据。

六个维度（``QA_DIMENSIONS`` 顺序固定）：

- ``offline_package_consistency``：bindings 三项（artifact 哈希、保存重开、绑定
  完整）与 scene revision / QA lineage 的哈希闭合。
- ``saved_reopened_consistency``：``bindings.saved_reopened`` 加上 PowerPoint
  Live save/reopen 摘要与证据中绑定当前产物哈希的子集；案例没有任何 Live 证据
  文件时为 ``not_evaluated``（strict 既有语义不变）。
- ``reference_fidelity``：regions / layout / arrow-visual / visual-contracts
  报告中的 strict blocker，外加 check 传入的 OCR 未匹配计数。
- ``repair_plan_coverage``：``qa/repair-plan.json`` 对当前参考、产物与 QA 报告
  哈希的重校验。
- ``repair_execution``：当前 ``qa/blockers.json`` 无剩余 blocker 且 repair plan
  校验通过 → ``closed``，否则 ``open``。这是按当前证据重算的闭环判定，不是修复
  动作的执行回执。
- ``release_eligibility``：纯派生维度——其余五个维度全部 pass、工作流状态为
  ``approved``，且存在 ``release-manifest.json`` 时其重哈希校验必须通过。

每个维度为 ``{status: pass|fail|not_evaluated, blockers: [...], evidence: [...]}``；
evidence 记录参与判定的案例相对路径与 SHA-256。``qa-status.json`` 不含时间戳，
证据不变时内容字节级确定。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import SCHEMA_VERSION, read_json, write_json

QA_STATUS_NAME = "qa-status.json"

QA_DIMENSIONS = (
    "offline_package_consistency",
    "saved_reopened_consistency",
    "reference_fidelity",
    "repair_plan_coverage",
    "repair_execution",
    "release_eligibility",
)

_LIVE_SAVE_REOPEN_SUMMARY = "live-save-reopen-summary.json"


def _sha256_or_none(path: Path) -> str | None:
    return common.sha256_file(path) if path.is_file() else None


def _evidence(run: common.Run, *paths: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in paths:
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(run.root).as_posix(),
                    "sha256": common.sha256_file(path),
                }
            )
    return entries


def _dimension(
    status: str,
    blockers: list[str],
    evidence: list[dict[str, str]],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "blockers": list(dict.fromkeys(blockers)),
        "evidence": evidence,
        **extra,
    }


def _offline_package_consistency(
    run: common.Run, artifact_sha256: str | None
) -> dict[str, Any]:
    from tools.qa_lineage import MANIFEST_NAME, validate_qa_lineage_manifest
    from tools.revisions import lineage_blockers

    blockers: list[str] = []
    if run.bindings_path.is_file():
        bindings = read_json(run.bindings_path)
        if artifact_sha256 is None:
            blockers.append("offline-package:artifact-missing")
        elif bindings.get("artifact_sha256") != artifact_sha256:
            blockers.append("bindings:artifact-hash-mismatch")
        if bindings.get("saved_reopened") is not True:
            blockers.append("bindings:save-reopen-not-verified")
        if bindings.get("bindings_complete") is not True:
            blockers.append("bindings:incomplete")
    else:
        blockers.append("offline-package:missing-evidence:bindings.json")
    blockers.extend(lineage_blockers(run))
    blockers.extend(validate_qa_lineage_manifest(run))
    status = "fail" if blockers else "pass"
    evidence = _evidence(run, run.bindings_path, run.qa_dir / MANIFEST_NAME)
    return _dimension(status, blockers, evidence)


def _live_subset_blockers(
    prefix: str,
    document: dict[str, Any],
    meta: dict[str, Any],
    artifact_sha256: str | None,
    *,
    candidate_field: str,
    root_field: str,
) -> list[str]:
    """Live save/reopen 文档中与当前产物哈希绑定的最小一致性子集。"""

    blockers: list[str] = []
    if document.get("reference_sha256") != meta.get("source_sha256"):
        blockers.append(f"{prefix}:reference-mismatch")
    if document.get("saved_reopened") is not True:
        blockers.append(f"{prefix}:not-saved-reopened")
    if document.get("bindings_complete") is not True:
        blockers.append(f"{prefix}:bindings-incomplete")
    if document.get("reopened_artifact_sha256") != document.get(candidate_field):
        blockers.append(f"{prefix}:reopened-hash-mismatch")
    if artifact_sha256 is None:
        blockers.append(f"{prefix}:artifact-missing")
    elif document.get(root_field) != artifact_sha256:
        blockers.append(f"{prefix}:not-current-artifact")
    return blockers


def _saved_reopened_consistency(
    run: common.Run, artifact_sha256: str | None
) -> dict[str, Any]:
    meta = run.load_meta()
    blockers: list[str] = []
    saved_reopened = (
        run.bindings_path.is_file()
        and read_json(run.bindings_path).get("saved_reopened") is True
    )
    if not saved_reopened:
        blockers.append("bindings:save-reopen-not-verified")
    summary_path = run.qa_dir / _LIVE_SAVE_REOPEN_SUMMARY
    live_paths = [
        path for path in (summary_path, run.live_evidence_path) if path.is_file()
    ]
    if blockers:
        status = "fail"
    elif not live_paths:
        # 没有 Live 证据时不虚构结论；strict 闸门独立决定是否要求 Live。
        status = "not_evaluated"
    else:
        if summary_path.is_file():
            blockers.extend(
                _live_subset_blockers(
                    "live-save-reopen",
                    read_json(summary_path),
                    meta,
                    artifact_sha256,
                    candidate_field="live_candidate_sha256",
                    root_field="current_root_candidate_sha256",
                )
            )
        if run.live_evidence_path.is_file():
            blockers.extend(
                _live_subset_blockers(
                    "live-evidence",
                    read_json(run.live_evidence_path),
                    meta,
                    artifact_sha256,
                    candidate_field="candidate_sha256",
                    root_field="candidate_sha256",
                )
            )
        status = "fail" if blockers else "pass"
    evidence = _evidence(run, run.bindings_path, summary_path, run.live_evidence_path)
    return _dimension(status, blockers, evidence)


def _reference_fidelity(
    run: common.Run, ocr_unmatched: tuple[int, int] | None
) -> dict[str, Any]:
    from tools.layout import strict_blockers as layout_strict_blockers
    from tools.visual_contracts import strict_blockers as visual_strict_blockers

    report_paths = {
        "regions-report.json": run.qa_dir / "regions-report.json",
        "layout-audit.json": run.layout_audit_path,
        "arrow-visual-report.json": run.qa_dir / "arrow-visual-report.json",
        "visual-contracts-report.json": run.qa_dir / "visual-contracts-report.json",
    }
    missing = [name for name, path in report_paths.items() if not path.is_file()]
    blockers: list[str] = []
    if missing:
        blockers.extend(f"reference-fidelity:missing-evidence:{name}" for name in missing)
        status = "not_evaluated"
    else:
        regions = read_json(report_paths["regions-report.json"])
        blockers.extend(
            item for item in regions.get("blockers", []) if isinstance(item, str)
        )
        if regions.get("critical_regions", 0) == 0:
            blockers.append("regions:no-critical-regions")
        blockers.extend(
            layout_strict_blockers(read_json(report_paths["layout-audit.json"]))
        )
        arrow_visual = read_json(report_paths["arrow-visual-report.json"])
        blockers.extend(
            item for item in arrow_visual.get("blockers", []) if isinstance(item, str)
        )
        blockers.extend(
            visual_strict_blockers(read_json(report_paths["visual-contracts-report.json"]))
        )
        if ocr_unmatched is not None:
            svg_count, ocr_count = ocr_unmatched
            if svg_count:
                blockers.append("ocr:svg-text-unmatched")
            if ocr_count:
                blockers.append("ocr:reference-text-unmatched")
        status = "fail" if blockers else "pass"
    return _dimension(status, blockers, _evidence(run, *report_paths.values()))


def _repair_plan_validation(
    run: common.Run, artifact_sha256: str | None
) -> dict[str, Any]:
    from tools.check import _qa_report_hashes
    from tools.repair_plan import validate_repair_plan

    return validate_repair_plan(
        read_json(run.repair_plan_path),
        expected_reference_sha256=run.load_meta().get("source_sha256"),
        expected_artifact_sha256=artifact_sha256,
        expected_qa_report_sha256=_qa_report_hashes(run),
    )


def _repair_plan_coverage(
    run: common.Run, artifact_sha256: str | None
) -> dict[str, Any]:
    if not run.repair_plan_path.is_file():
        return _dimension(
            "not_evaluated",
            ["repair-plan-coverage:missing-evidence:qa/repair-plan.json"],
            [],
        )
    blockers: list[str] = []
    if artifact_sha256 is None:
        blockers.append("repair-plan-coverage:artifact-missing")
    validation = _repair_plan_validation(run, artifact_sha256)
    blockers.extend(f"repair-plan:{error}" for error in validation["errors"])
    status = "fail" if blockers else "pass"
    return _dimension(status, blockers, _evidence(run, run.repair_plan_path))


def _repair_execution(
    run: common.Run, artifact_sha256: str | None, *, plan_pass: bool
) -> dict[str, Any]:
    if not run.blockers_path.is_file():
        return _dimension(
            "not_evaluated",
            ["repair-execution:missing-evidence:qa/blockers.json"],
            [],
            execution="open",
        )
    inventory = read_json(run.blockers_path)
    blockers = [item for item in inventory.get("blockers", []) if isinstance(item, str)]
    if artifact_sha256 is None:
        blockers.append("repair-execution:artifact-missing")
    elif inventory.get("artifact_sha256") != artifact_sha256:
        blockers.append("repair-execution:inventory-not-current")
    if not plan_pass:
        blockers.append("repair-execution:repair-plan-not-pass")
    execution = "closed" if not blockers else "open"
    status = "pass" if execution == "closed" else "fail"
    evidence = _evidence(run, run.blockers_path, run.repair_plan_path)
    return _dimension(status, blockers, evidence, execution=execution)


def _release_eligibility(
    run: common.Run,
    dimensions: dict[str, dict[str, Any]],
    *,
    check_manifest: bool,
) -> dict[str, Any]:
    from tools.release import RELEASE_MANIFEST_NAME, validate_release_manifest

    blockers: list[str] = []
    for name in QA_DIMENSIONS[:-1]:
        status = dimensions[name]["status"]
        if status != "pass":
            blockers.append(f"release-eligibility:{name.replace('_', '-')}:{status}")
    if run.load_meta().get("workflow", {}).get("state") != "approved":
        blockers.append("release-eligibility:not-approved")
    manifest_path = run.root / RELEASE_MANIFEST_NAME
    if check_manifest and manifest_path.is_file():
        blockers.extend(validate_release_manifest(run))
    status = "fail" if blockers else "pass"
    # evidence 只绑定 run.json：release manifest 本身由 release --check 重哈希，
    # 不进入本文件，保证 check 重跑时 qa-status.json 保持字节级确定。
    return _dimension(status, blockers, _evidence(run, run.meta_path))


def _guard(
    name: str, compute: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    try:
        return compute()
    except Exception as exc:  # 状态读出不得中断 check/release 主流程
        return _dimension(
            "fail", [f"{name.replace('_', '-')}:evaluation-error:{exc}"], []
        )


def compute_qa_dimensions(
    run: common.Run,
    *,
    ocr_unmatched: tuple[int, int] | None = None,
    check_manifest: bool = True,
) -> dict[str, dict[str, Any]]:
    """按 ``QA_DIMENSIONS`` 顺序计算六个维度的当前状态（纯读取证据）。

    ``ocr_unmatched`` 为 check 已算出的（SVG 侧、OCR 侧）未匹配计数；为 ``None``
    表示本次没有运行 OCR，文本比对不在维度内单独计 blocker。
    ``check_manifest=False`` 跳过 release manifest 重哈希校验（release 生成路径：
    既有 manifest 即将被原子替换）。
    """

    artifact_sha256 = _sha256_or_none(run.pptx_path)
    dimensions: dict[str, dict[str, Any]] = {}
    dimensions["offline_package_consistency"] = _guard(
        "offline_package_consistency",
        lambda: _offline_package_consistency(run, artifact_sha256),
    )
    dimensions["saved_reopened_consistency"] = _guard(
        "saved_reopened_consistency",
        lambda: _saved_reopened_consistency(run, artifact_sha256),
    )
    dimensions["reference_fidelity"] = _guard(
        "reference_fidelity",
        lambda: _reference_fidelity(run, ocr_unmatched),
    )
    dimensions["repair_plan_coverage"] = _guard(
        "repair_plan_coverage",
        lambda: _repair_plan_coverage(run, artifact_sha256),
    )
    dimensions["repair_execution"] = _guard(
        "repair_execution",
        lambda: _repair_execution(
            run,
            artifact_sha256,
            plan_pass=dimensions["repair_plan_coverage"]["status"] == "pass",
        ),
    )
    dimensions["release_eligibility"] = _guard(
        "release_eligibility",
        lambda: _release_eligibility(run, dimensions, check_manifest=check_manifest),
    )
    return dimensions


def write_qa_status(
    run: common.Run,
    *,
    ocr_unmatched: tuple[int, int] | None = None,
    check_manifest: bool = True,
) -> dict[str, Any]:
    """计算六个维度并原子写入 ``qa/qa-status.json``，返回写入的文档。"""

    meta = run.load_meta()
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": "qa_status",
        "case": meta["case"],
        "reference_sha256": meta["source_sha256"],
        "artifact_sha256": _sha256_or_none(run.pptx_path),
        "dimensions": compute_qa_dimensions(
            run, ocr_unmatched=ocr_unmatched, check_manifest=check_manifest
        ),
    }
    write_json(run.qa_dir / QA_STATUS_NAME, document)
    return document
