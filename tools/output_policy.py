#!/usr/bin/env python3
"""Fail-closed output-path policy for AI AutoFigure writers.

Generated files that resolve inside the project tree belong under
``examples/generated``.  Callers may still choose an explicit *absolute* path
outside the project (for example pytest's temporary directory or a user
delivery folder); relative paths never spill into an arbitrary caller CWD.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ROOT = PROJECT_ROOT / "examples" / "generated"


class OutputPolicyError(ValueError):
    """Raised before a writer can mutate a forbidden project path."""


_WINDOWS_DEVICE_NAMESPACE_PREFIXES = (
    "\\\\?\\",
    "\\\\.\\",
    "\\??\\",
    "\\\\??\\",
    "\\device\\",
    "\\global??\\",
    "\\dosdevices\\",
)


def _ordinary_path(path: str | os.PathLike[str], *, label: str) -> Path:
    """Reject Win32/NT device namespaces before pathlib/Win32 normalization."""
    try:
        raw_path = os.fspath(path)
    except TypeError as exc:
        raise OutputPolicyError(
            f"{label} must be path-like, got {type(path).__name__}"
        ) from exc
    if not isinstance(raw_path, str):
        raise OutputPolicyError(f"{label} must resolve to text, not bytes")
    windows_spelling = raw_path.replace("/", "\\").casefold()
    if windows_spelling.startswith(_WINDOWS_DEVICE_NAMESPACE_PREFIXES):
        raise OutputPolicyError(
            f"{label} must not use a Win32/NT device namespace: {raw_path}"
        )
    return Path(raw_path)


def _absolute_without_symlink_resolution(path: Path) -> Path:
    """Return an absolute normalized path while preserving symlink components."""
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(expanded))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_output_path(
    path: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str] = PROJECT_ROOT,
) -> Path:
    """Resolve and authorize an output path before any mkdir/write operation.

    A lexical path inside the project that escapes through a symlink is rejected
    rather than being treated as an intentional external destination.  An
    external symlink alias that resolves back into the project is classified by
    its real target, so it cannot bypass the project rule either.
    """
    requested = _ordinary_path(path, label="output path")
    requested_root = _ordinary_path(project_root, label="project root")
    root_lexical = _absolute_without_symlink_resolution(requested_root)
    root_resolved = root_lexical.resolve(strict=False)
    allowed_resolved = (root_resolved / "examples" / "generated").resolve(strict=False)
    if not _is_within(allowed_resolved, root_resolved):
        raise OutputPolicyError(
            "invalid output-policy configuration: examples/generated resolves "
            f"outside the project tree: {allowed_resolved}"
        )

    explicitly_absolute = requested.is_absolute()
    candidate_lexical = _absolute_without_symlink_resolution(requested)
    candidate_resolved = candidate_lexical.resolve(strict=False)
    lexical_in_project = _is_within(candidate_lexical, root_lexical)
    resolved_in_project = _is_within(candidate_resolved, root_resolved)

    if lexical_in_project and not resolved_in_project:
        raise OutputPolicyError(
            "refusing project-path symlink escape; use an explicit external output path: "
            f"{candidate_lexical} -> {candidate_resolved}"
        )
    if resolved_in_project and not _is_within(candidate_resolved, allowed_resolved):
        raise OutputPolicyError(
            "outputs inside the AI AutoFigure project must be under "
            f"{allowed_resolved}; refused: {candidate_resolved}"
        )
    if not resolved_in_project and not explicitly_absolute:
        raise OutputPolicyError(
            "external output paths must be explicitly absolute; refused relative path: "
            f"{path!s} -> {candidate_resolved}"
        )
    return candidate_resolved


__all__ = [
    "GENERATED_ROOT",
    "OutputPolicyError",
    "PROJECT_ROOT",
    "resolve_output_path",
]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="Requested output path")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="AI AutoFigure project root used for containment",
    )
    args = parser.parse_args(argv)
    try:
        authorized = resolve_output_path(args.path, project_root=args.project_root)
    except OutputPolicyError as exc:
        print(f"OUTPUT_POLICY_REJECTED: {exc}", file=sys.stderr)
        return 3
    print(authorized)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
