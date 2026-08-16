r"""Project frozen aggregate trajectory formulas into editable Office Math parts.

The source authority remains immutable: a confirmed sequence such as
``(z_t,z_{t+1},z_{t+2},\ldots,z_{t+h})`` is decomposed losslessly into native
PowerPoint placeholders.  The detached report records the parent formula and
authority item for every derived receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.build_powerpoint_draw_batch import (  # noqa: E402
    TRAJECTORY_FORMULA_PROJECTIONS,
    _formula_geometry_batch,
)
from tools.powerpoint_native_math import (  # noqa: E402
    NativeMathError,
    _atomic_write_json_fresh,
    compile_formula,
)


class NativeMathPlanError(RuntimeError):
    """Raised when a frozen formula cannot be projected without guessing."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeMathPlanError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise NativeMathPlanError(f"{label} must be a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_sequence(canonical_latex: str, expected_count: int) -> list[str]:
    value = canonical_latex.strip()
    if len(value) >= 2 and value[0] == "(" and value[-1] == ")":
        value = value[1:-1]
    parts: list[str] = []
    start = 0
    brace_depth = 0
    paren_depth = 0
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "{":
            brace_depth += 1
        elif character == "}":
            brace_depth -= 1
        elif character == "(":
            paren_depth += 1
        elif character == ")":
            paren_depth -= 1
        elif character == "," and brace_depth == 0 and paren_depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
        if brace_depth < 0 or paren_depth < 0:
            raise NativeMathPlanError(f"unbalanced aggregate LaTeX: {canonical_latex}")
    if brace_depth or paren_depth:
        raise NativeMathPlanError(f"unbalanced aggregate LaTeX: {canonical_latex}")
    parts.append(value[start:].strip())
    visible_parts = [
        part for part in parts if part not in {r"\ldots", r"\dots", r"\cdots", "…"}
    ]
    if len(visible_parts) != expected_count or any(not part for part in visible_parts):
        raise NativeMathPlanError(
            f"aggregate formula expected {expected_count} parts, got {len(visible_parts)}: "
            f"{canonical_latex}"
        )
    return visible_parts


def _relative(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()


def build_native_math_plan(
    case_root: Path,
    output_path: Path,
    derived_directory: Path,
    report_path: Path,
) -> dict[str, Any]:
    case_root = case_root.resolve(strict=True)
    receipt = _load(case_root / "case-receipt.json", "PowerPoint case receipt")
    if receipt.get("status") != "POWERPOINT_CASE_READY":
        raise NativeMathPlanError("PowerPoint case receipt is not READY")
    spec_path = Path(str(receipt["figure_spec"]["path"])).resolve(strict=True)
    spec = _load(spec_path, "Figure Spec")
    scene = _load(case_root / "design" / "scene_graph.json", "scene graph")
    formulas = spec.get("formulas")
    if not isinstance(formulas, list) or not formulas:
        raise NativeMathPlanError("Figure Spec has no formulas")
    output_path = output_path.resolve()
    derived_directory = derived_directory.resolve()
    report_path = report_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    derived_directory.mkdir(parents=True, exist_ok=True)
    operations: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    geometry_batch = _formula_geometry_batch(spec, scene, slide_id=1)
    target_styles = {
        str(operation["shape_name"]): {
            "target_font_size_pt": round(float(operation["font_size"]), 6),
            "target_font_color": str(operation["font_color"]),
        }
        for operation in geometry_batch["operations"]
        if operation.get("type") == "update_shape"
    }

    def with_target_style(operation: dict[str, Any]) -> dict[str, Any]:
        placeholder_name = str(operation["placeholder_name"])
        style = target_styles.get(placeholder_name)
        if style is None:
            raise NativeMathPlanError(
                f"formula target style is missing for {placeholder_name}"
            )
        return {**operation, **style}

    for raw_formula in formulas:
        if not isinstance(raw_formula, Mapping):
            raise NativeMathPlanError("Figure Spec formula must be an object")
        formula_id = str(raw_formula["id"])
        projection = TRAJECTORY_FORMULA_PROJECTIONS.get(formula_id)
        if projection is None:
            receipt_path = Path(str(raw_formula["converter_receipt_path"])).resolve(
                strict=True
            )
            operations.append(
                with_target_style(
                    {
                        "slide_index": 1,
                        "placeholder_name": str(raw_formula["element_id"]),
                        "formula_id": formula_id,
                        "receipt_path": _relative(receipt_path, output_path.parent),
                        "receipt_sha256": _sha256_file(receipt_path),
                    }
                )
            )
            continue
        asset_id, role, count = projection
        parts = _split_sequence(str(raw_formula["canonical_latex"]), count)
        derived_rows: list[dict[str, Any]] = []
        for index, part in enumerate(parts, start=1):
            derived_id = f"{formula_id}.part.{index}"
            derived_path = derived_directory / f"{derived_id}.converter.json"
            compiled = compile_formula(derived_id, part, "inline")
            _atomic_write_json_fresh(derived_path, compiled, pretty=False)
            derived_hash = _sha256_file(derived_path)
            placeholder_name = f"{formula_id}::part-{index}"
            operations.append(
                with_target_style(
                    {
                        "slide_index": 1,
                        "placeholder_name": placeholder_name,
                        "formula_id": derived_id,
                        "receipt_path": _relative(derived_path, output_path.parent),
                        "receipt_sha256": derived_hash,
                    }
                )
            )
            derived_rows.append(
                {
                    "formula_id": derived_id,
                    "canonical_latex": part,
                    "placeholder_name": placeholder_name,
                    "receipt_path": str(derived_path),
                    "receipt_sha256": derived_hash,
                }
            )
        projections.append(
            {
                "source_formula_id": formula_id,
                "source_canonical_latex": str(raw_formula["canonical_latex"]),
                "source_latex_sha256": str(raw_formula["latex_sha256"]),
                "authority_item_id": raw_formula.get("authority_item_id"),
                "asset_id": asset_id,
                "role": role,
                "projection_kind": "LOSSLESS_SEQUENCE_DECOMPOSITION",
                "parts": derived_rows,
            }
        )
    if set(target_styles) != {
        str(operation["placeholder_name"]) for operation in operations
    }:
        raise NativeMathPlanError("formula target styles and plan operations diverged")
    plan = {"schema_version": "1.0", "operations": operations}
    _atomic_write_json_fresh(output_path, plan, pretty=True)
    report = {
        "schema_version": "1.0.0",
        "document_type": "NATIVE_MATH_PROJECTION_REPORT",
        "status": "PASS",
        "authority_mutated": False,
        "figure_spec_path": str(spec_path),
        "figure_spec_sha256": _sha256_file(spec_path),
        "plan_path": str(output_path),
        "plan_sha256": _sha256_file(output_path),
        "operation_count": len(operations),
        "source_formula_count": len(formulas),
        "native_formula_count": len(operations),
        "projections": projections,
    }
    _atomic_write_json_fresh(report_path, report, pretty=True)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--derived-directory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_native_math_plan(
            args.case_root, args.output, args.derived_directory, args.report
        )
    except (NativeMathPlanError, NativeMathError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
