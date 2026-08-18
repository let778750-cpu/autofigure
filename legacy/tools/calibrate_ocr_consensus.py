"""Calibrate the ordinary-text OCR threshold from a versioned gold fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from perception_policy import load_json, load_profile, sha256_file
    from output_policy import resolve_output_path
except ModuleNotFoundError:
    from .perception_policy import load_json, load_profile, sha256_file
    from .output_policy import resolve_output_path


def calibrate(fixture_path: Path, profile_name: str, minimum_selected: int) -> dict[str, Any]:
    fixture = load_json(fixture_path, "OCR consensus fixture")
    samples = fixture.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("fixture.samples must be a non-empty array")
    eligible = [sample for sample in samples if sample.get("eligible") is True]
    if not eligible:
        raise ValueError("fixture has no policy-eligible samples")
    profile = load_profile(profile_name)
    target = float(profile["ocr"]["minimum_fixture_precision"])
    passing: list[tuple[float, list[dict[str, Any]]]] = []
    for threshold in sorted({float(sample["ocr_confidence"]) for sample in eligible}):
        selected = [sample for sample in eligible if float(sample["ocr_confidence"]) >= threshold]
        correct = sum(sample.get("correct") is True for sample in selected)
        precision = correct / len(selected) if selected else 0.0
        if len(selected) >= minimum_selected and precision >= target:
            passing.append((threshold, selected))
    if passing:
        threshold, selected = passing[0]
        status = "PASS"
    else:
        threshold, selected, status = 1.0, [], "FAIL"
    correct = sum(sample.get("correct") is True for sample in selected)
    return {
        "schema_version": "1.0.0",
        "document_type": "OCR_CONSENSUS_CALIBRATION_RECEIPT",
        "status": status,
        "fixture": {
            "path": str(fixture_path.resolve(strict=True)),
            "sha256": sha256_file(fixture_path),
            "version": str(fixture.get("version", "UNVERSIONED")),
        },
        "policy_profile": profile_name,
        "algorithm_id": "ordinary_text_consensus_v1",
        "selected_min_confidence": threshold,
        "metrics": {
            "fixture_count": len(samples),
            "eligible_count": len(eligible),
            "selected_count": len(selected),
            "correct_count": correct,
            "precision": correct / len(selected) if selected else 0.0,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--profile", default="standard", choices=("standard", "strict"))
    parser.add_argument("--minimum-selected", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = calibrate(args.fixture, args.profile, args.minimum_selected)
        output = resolve_output_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        print(f"OCR_CALIBRATION_REJECTED: {exc}", file=sys.stderr)
        return 2
    return 0 if receipt["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
