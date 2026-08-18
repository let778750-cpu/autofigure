"""Build a hash-bound Office Math injection plan from Figure Spec v4.

The adapter is intentionally mechanical: it maps standalone formula elements
to same-named PowerPoint placeholders and never infers LaTeX, color, or size.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


class NativeMathPlanError(RuntimeError):
    """Raised when a Figure Spec cannot authorize deterministic injection."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeMathPlanError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise NativeMathPlanError(f"{label} must be one JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(value: str, base: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=True)


def _portable_path(path: Path, output_parent: Path) -> str:
    try:
        return path.relative_to(output_parent).as_posix()
    except ValueError:
        return str(path)


def build_plan(spec_path: Path, output_path: Path, slide_index: int = 1) -> dict[str, Any]:
    if slide_index < 1:
        raise NativeMathPlanError("slide_index must be positive")
    resolved_spec = spec_path.resolve(strict=True)
    spec = _load(resolved_spec, "Figure Spec")
    if spec.get("schema_version") != "4.0":
        raise NativeMathPlanError("native math plan requires Figure Spec 4.0")
    elements = {
        str(element["id"]): element
        for element in spec.get("elements", [])
        if isinstance(element, Mapping) and isinstance(element.get("id"), str)
    }
    formulas = spec.get("formulas")
    if not isinstance(formulas, list) or not formulas:
        raise NativeMathPlanError("Figure Spec contains no formula bindings")
    dpi = float(spec.get("measurement_dpi", 96.0))
    if dpi <= 0:
        raise NativeMathPlanError("measurement_dpi must be positive")
    output_parent = output_path.resolve().parent
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for formula in formulas:
        if not isinstance(formula, Mapping):
            raise NativeMathPlanError("formula bindings must be objects")
        formula_id = str(formula.get("id", ""))
        element_id = str(formula.get("element_id", ""))
        if not formula_id or not element_id or formula_id in seen:
            raise NativeMathPlanError(f"invalid or duplicate formula binding: {formula_id!r}")
        seen.add(formula_id)
        element = elements.get(element_id)
        if element is None or element.get("type") != "formula":
            raise NativeMathPlanError(f"{formula_id} does not bind one standalone formula element")
        if str(element.get("formula_id")) != formula_id:
            raise NativeMathPlanError(f"{formula_id} element binding disagrees with formula_id")
        style = element.get("formula_style")
        if not isinstance(style, Mapping):
            raise NativeMathPlanError(f"{formula_id} lacks formula_style")
        color = style.get("color")
        if not isinstance(color, str) or not re.fullmatch(r"#[A-Fa-f0-9]{6}", color):
            raise NativeMathPlanError(f"{formula_id} requires an explicit #RRGGBB color")
        if isinstance(style.get("font_size_pt"), (int, float)):
            font_size = float(style["font_size_pt"])
        elif isinstance(style.get("font_size_px"), (int, float)):
            font_size = float(style["font_size_px"]) * 72.0 / dpi
        else:
            raise NativeMathPlanError(f"{formula_id} requires a numeric formula font size")
        receipt_value = formula.get("converter_receipt_path")
        receipt_hash = formula.get("converter_receipt_sha256")
        if not isinstance(receipt_value, str) or not isinstance(receipt_hash, str):
            raise NativeMathPlanError(f"{formula_id} lacks a converter receipt binding")
        receipt_path = _resolve(receipt_value, resolved_spec.parent)
        actual_hash = _sha256(receipt_path)
        if actual_hash.casefold() != receipt_hash.casefold():
            raise NativeMathPlanError(f"{formula_id} converter receipt hash mismatch")
        receipt = _load(receipt_path, f"{formula_id} converter receipt")
        if receipt.get("status") != "PASS" or receipt.get("formula_id") != formula_id:
            raise NativeMathPlanError(f"{formula_id} converter receipt is not a matching PASS")
        operations.append(
            {
                "slide_index": slide_index,
                "placeholder_name": element_id,
                "formula_id": formula_id,
                "receipt_path": _portable_path(receipt_path, output_parent),
                "receipt_sha256": actual_hash,
                "target_font_size_pt": round(font_size, 6),
                "target_font_color": color.upper(),
            }
        )
    return {"schema_version": "1.0", "operations": operations}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--slide-index", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        plan = build_plan(args.spec, args.output, args.slide_index)
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
    except (NativeMathPlanError, OSError) as exc:
        print(f"NATIVE_MATH_PLAN_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "PASS", "operation_count": len(plan["operations"]), "output": str(destination)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
