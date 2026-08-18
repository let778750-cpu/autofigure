"""Create a calibrated promotion sidecar without changing raw Phase-1 evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

try:
    from output_policy import resolve_output_path
    from perception_policy import load_json, sha256_file
except ModuleNotFoundError:
    from .output_policy import resolve_output_path
    from .perception_policy import load_json, sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_SCHEMA = PROJECT_ROOT / "schemas" / "geometry-calibration.schema.json"
PROMOTION_SCHEMA = PROJECT_ROOT / "schemas" / "geometry-promotion.schema.json"
HIGH_RISK_TOKENS = ("FORMULA", "VERTICAL", "MULTI", "CONTAMIN", "CONFLICT", "LOW_OCR")


def _validate(value: Mapping[str, Any], schema_path: Path, label: str) -> None:
    schema = load_json(schema_path, f"{label} schema")
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))
    if errors:
        raise ValueError(f"invalid {label}: {errors[0].message}")


def promote(raw_path: Path, calibration_path: Path) -> dict[str, Any]:
    raw = load_json(raw_path, "raw geometry manifest")
    calibration = load_json(calibration_path, "geometry calibration receipt")
    _validate(calibration, CALIBRATION_SCHEMA, "geometry calibration receipt")
    if raw.get("mode") != "observation_only" or raw.get("policy", {}).get("promotion_allowed") is not False:
        raise ValueError("raw Phase-1 evidence must remain observation_only")
    fixture_path = Path(str(calibration["fixture"]["path"])).resolve(strict=True)
    if sha256_file(fixture_path) != calibration["fixture"]["sha256"]:
        raise ValueError("geometry calibration fixture hash is stale")
    class_records = {
        str(record["class_id"]): record
        for record in calibration["classes"]
        if record["status"] == "PROMOTABLE"
    }
    exclusions: Counter[str] = Counter()
    promotions: list[dict[str, Any]] = []

    def add(observation_id: str, class_id: str, geometry: Mapping[str, Any], flags: list[str]) -> None:
        record = class_records.get(class_id)
        if record is None:
            exclusions[f"uncalibrated:{class_id}"] += 1
            return
        promotions.append(
            {
                "observation_id": observation_id,
                "class_id": class_id,
                "geometry": dict(geometry),
                "confidence": round(1.0 / (1.0 + float(record["p95_error_px"])), 6),
                "confidence_basis": "1/(1+calibrated_p95_error_px)",
                "source_quality_flags": flags,
            }
        )

    for item in raw.get("text_geometry", []):
        flags = [str(flag) for flag in item.get("quality_flags", [])]
        baseline = item.get("baseline", {})
        high_risk = any(token in " ".join(flags).upper() for token in HIGH_RISK_TOKENS)
        measurable = (
            item.get("status") == "MEASURED"
            and item.get("ink_bbox") is not None
            and baseline.get("status") == "MEASURED"
            and baseline.get("angle_degrees") is not None
            and abs(float(baseline["angle_degrees"])) <= 2.0
        )
        if high_risk or not measurable or flags:
            exclusions["text_high_risk_or_inconclusive"] += 1
            continue
        add(str(item["candidate_id"]), "horizontal_single_line_ink_bbox", item["ink_bbox"], flags)
    for item in raw.get("frame_candidates", []):
        flags = [str(flag) for flag in item.get("quality_flags", [])]
        if item.get("status") != "MEASURED" or flags:
            exclusions["frame_inconclusive"] += 1
            continue
        add(str(item["frame_id"]), "clear_frame", item["bbox_source"], flags)
    for index, item in enumerate(raw.get("neighbor_pairs", []), start=1):
        flags = [str(flag) for flag in item.get("quality_flags", [])]
        if item.get("status") != "MEASURED" or flags:
            exclusions["gap_inconclusive"] += 1
            continue
        identifier = str(item.get("pair_id", f"G{index:05d}"))
        geometry = item.get("gap") or item.get("geometry") or item
        add(identifier, "same_row_gap", geometry, flags)
    receipt = {
        "schema_version": "1.0.0",
        "document_type": "GEOMETRY_PROMOTION_RECEIPT",
        "status": "PROMOTIONS_READY" if promotions else "OBSERVATION_ONLY",
        "raw_geometry": {"path": str(raw_path.resolve(strict=True)), "sha256": sha256_file(raw_path)},
        "calibration": {"path": str(calibration_path.resolve(strict=True)), "sha256": sha256_file(calibration_path)},
        "promotions": promotions,
        "excluded_counts": dict(sorted(exclusions.items())),
    }
    _validate(receipt, PROMOTION_SCHEMA, "geometry promotion receipt")
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry-manifest", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = promote(args.geometry_manifest, args.calibration)
        output = resolve_output_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        print(f"GEOMETRY_PROMOTION_REJECTED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
