"""Evaluate Phase-1 geometry classes against a versioned gold fixture."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np

try:
    from output_policy import resolve_output_path
    from perception_policy import load_json, load_profile, sha256_file
except ModuleNotFoundError:
    from .output_policy import resolve_output_path
    from .perception_policy import load_json, load_profile, sha256_file


def calibrate(fixture_path: Path, profile_name: str) -> dict[str, Any]:
    fixture = load_json(fixture_path, "geometry gold fixture")
    samples = fixture.get("samples")
    legends = fixture.get("legend_classes")
    if not isinstance(samples, list) or not samples:
        raise ValueError("fixture.samples must be non-empty")
    if not isinstance(legends, list) or len(set(legends)) < 4:
        raise ValueError("geometry fixture requires at least four stable legend classes")
    profile = load_profile(profile_name)
    policy = profile["geometry"]
    records = []
    for class_id in sorted({str(sample["class_id"]) for sample in samples}):
        class_samples = [sample for sample in samples if sample["class_id"] == class_id]
        errors = [float(sample["error_px"]) for sample in class_samples]
        false_high_risk = sum(
            sample.get("high_risk") is True and sample.get("promoted") is True
            for sample in class_samples
        )
        median = statistics.median(errors)
        p95 = float(np.percentile(np.asarray(errors, dtype=float), 95))
        promotable = (
            bool(policy["promotion_enabled"])
            and class_id in policy["promotable_classes"]
            and len(errors) >= int(policy["minimum_samples_per_class"])
            and median <= float(policy["maximum_median_error_px"])
            and p95 <= float(policy["maximum_p95_error_px"])
            and false_high_risk <= int(policy["maximum_high_risk_false_promotions"])
        )
        records.append(
            {
                "class_id": class_id,
                "sample_count": len(errors),
                "median_error_px": median,
                "p95_error_px": p95,
                "high_risk_false_promotions": false_high_risk,
                "status": "PROMOTABLE" if promotable else "OBSERVATION_ONLY",
            }
        )
    required = set(policy["promotable_classes"])
    passed = required and all(
        any(record["class_id"] == class_id and record["status"] == "PROMOTABLE" for record in records)
        for class_id in required
    )
    return {
        "schema_version": "1.0.0",
        "document_type": "GEOMETRY_CALIBRATION_RECEIPT",
        "status": "PASS" if passed else "FAIL",
        "fixture": {
            "path": str(fixture_path.resolve(strict=True)),
            "sha256": sha256_file(fixture_path),
            "version": str(fixture.get("version", "UNVERSIONED")),
            "legend_classes": list(dict.fromkeys(str(value) for value in legends)),
        },
        "policy_profile": profile_name,
        "classes": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--profile", default="standard", choices=("standard", "strict"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = calibrate(args.fixture, args.profile)
        output = resolve_output_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        print(f"GEOMETRY_CALIBRATION_REJECTED: {exc}", file=sys.stderr)
        return 2
    return 0 if receipt["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
