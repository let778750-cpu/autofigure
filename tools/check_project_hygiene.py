"""Check that generated artifacts cannot silently repopulate the project root."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


FORBIDDEN_ROOT_DIRECTORIES = {
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "env",
    "output",
    "site-packages",
    "venv",
    "work",
}
FORBIDDEN_DIRECTORY_NAMES = {"__pycache__"}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    "AGENTS.md",
    "OPTIMIZATION_DETAILS.md",
    "PROJECT_ARCHITECTURE.md",
    "README.md",
    "SKILL.md",
    "agent-vision-config.json",
    "autofigure.cmd",
    "examples",
    "host-runtime.json",
    "mcp.json",
    "ocr-config.json",
    "publication-profiles.yaml",
    "pyproject.toml",
    "references",
    "requirements.txt",
    "schemas",
    "tests",
    "tools",
    "优化方案参考",
}
REQUIRED_FIXTURES = {
    "target_figure.fixture.json",
    "target_figure.png",
}
BENCHMARK_SOURCE_IMAGES = {
    "01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png",
    "02_2026_CVPR_2026_Thinking_Diffusion_Penalize_and_Guide_Visual-Grounde.png",
    "03_2026_CVPR_2026_LLMind_Bio-inspired_Training-free_Adaptive_Visual_Re.png",
}


def _normalized(path: Path) -> str:
    return path.as_posix()


def inspect_project(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    findings: list[dict[str, str]] = []

    def add(code: str, path: Path, message: str) -> None:
        findings.append(
            {
                "code": code,
                "path": _normalized(path.relative_to(root)),
                "message": message,
            }
        )

    for name in sorted(FORBIDDEN_ROOT_DIRECTORIES):
        candidate = root / name
        if candidate.exists():
            add(
                "FORBIDDEN_ROOT_ARTIFACT_DIRECTORY",
                candidate,
                "Transient artifacts must not live at the project root.",
            )

    for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if child.name not in ALLOWED_ROOT_ENTRIES and child.name not in FORBIDDEN_ROOT_DIRECTORIES:
            add(
                "UNCLASSIFIED_PROJECT_ROOT_ENTRY",
                child,
                "Project-root entries must be source, contract, configuration, or a canonical directory.",
            )

    excluded = {".git"}
    for current, directory_names, _ in os.walk(root):
        current_path = Path(current)
        directory_names[:] = [name for name in directory_names if name not in excluded]
        for name in sorted(set(directory_names) & FORBIDDEN_DIRECTORY_NAMES):
            add(
                "BYTECODE_CACHE_IN_PROJECT",
                current_path / name,
                "Python bytecode belongs outside the maintained project tree.",
            )

    examples = root / "examples"
    if not examples.is_dir():
        add("EXAMPLES_DIRECTORY_MISSING", examples, "The canonical examples directory is missing.")
    else:
        for fixture in sorted(REQUIRED_FIXTURES):
            candidate = examples / fixture
            if not candidate.is_file():
                add("CANONICAL_FIXTURE_MISSING", candidate, "A stable source fixture is missing.")
        allowed_entries = REQUIRED_FIXTURES | BENCHMARK_SOURCE_IMAGES | {"generated"}
        for child in sorted(examples.iterdir(), key=lambda item: item.name.casefold()):
            if child.name not in allowed_entries:
                add(
                    "UNCLASSIFIED_EXAMPLES_ENTRY",
                    child,
                    "Generated outputs must be classified under examples/generated.",
                )

    return {
        "document_type": "AI_AUTOFIGURE_PROJECT_HYGIENE_REPORT",
        "schema_version": "1.0",
        "status": "PASS" if not findings else "FAIL",
        "root": str(root),
        "policy": {
            "stable_fixtures": "examples/",
            "generated_runs": "examples/generated/runs/<run_id>/",
            "curated_integration_evidence": "examples/generated/<case>/",
            "unit_test_scratch": "operating-system temporary directory",
        },
        "findings": findings,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to inspect (defaults to this tool's project).",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = inspect_project(args.root)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
