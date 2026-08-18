"""Apply calibrated ordinary-text consensus to an initialized review document."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

try:
    from finalize_perception_review import (
    DEFAULT_RAW_SCHEMA,
    DEFAULT_REVIEW_SCHEMA,
    _require_binding,
    atomic_write_json,
    load_json_object,
    load_schema,
    load_validated_raw_manifest,
    make_binding,
    sha256_file,
    validate_json,
    )
    from perception_policy import (
    PerceptionPolicyError,
    consensus_eligible,
    deterministic_sample_ids,
    find_text_fact,
    load_profile,
    region_for_candidate,
    semantic_role_for_candidate,
    validate_calibration,
    )
except ModuleNotFoundError:
    from .finalize_perception_review import (
        DEFAULT_RAW_SCHEMA,
        DEFAULT_REVIEW_SCHEMA,
        _require_binding,
        atomic_write_json,
        load_json_object,
        load_schema,
        load_validated_raw_manifest,
        make_binding,
        sha256_file,
        validate_json,
    )
    from .perception_policy import (
        PerceptionPolicyError,
        consensus_eligible,
        deterministic_sample_ids,
        find_text_fact,
        load_profile,
        region_for_candidate,
        semantic_role_for_candidate,
        validate_calibration,
    )


def apply_consensus(
    manifest_path: Path,
    decisions_path: Path,
    fusion_path: Path,
    calibration_path: Path,
    output_path: Path,
    profile_name: str = "standard",
) -> dict:
    manifest, _, resolved_manifest, raw_schema = load_validated_raw_manifest(
        manifest_path, DEFAULT_RAW_SCHEMA
    )
    document = load_json_object(decisions_path, "perception review decisions")
    review_schema = load_schema(DEFAULT_REVIEW_SCHEMA, "perception review schema")
    validate_json(document, review_schema, "perception review decisions")
    _require_binding(document["raw_manifest"], make_binding(manifest, resolved_manifest, raw_schema))
    fusion = load_json_object(fusion_path, "fusion manifest")
    if str(fusion.get("run_id")) != str(manifest["run_id"]):
        raise PerceptionPolicyError("fusion run_id does not match OCR manifest")
    if str(fusion.get("source", {}).get("sha256", "")).upper() != str(manifest["source"]["sha256"]).upper():
        raise PerceptionPolicyError("fusion source does not match OCR manifest")
    if str(fusion.get("inputs", {}).get("ocr_manifest", {}).get("sha256", "")).upper() != sha256_file(resolved_manifest):
        raise PerceptionPolicyError("fusion is not bound to the exact OCR manifest")
    profile = load_profile(profile_name)
    if not profile["ocr"]["ordinary_consensus_enabled"]:
        raise PerceptionPolicyError(f"{profile_name} disables automatic OCR consensus")
    calibration = validate_calibration(calibration_path, profile_name, profile)
    eligible: dict[str, str] = {}
    for candidate in manifest["text_candidates"]:
        candidate_id = str(candidate["candidate_id"])
        region = region_for_candidate(candidate, fusion)
        role = semantic_role_for_candidate(candidate_id, fusion)
        allowed, _reason = consensus_eligible(
            candidate, find_text_fact(candidate_id, fusion), calibration, semantic_role=role
        )
        if allowed and region != "UNASSIGNED":
            eligible[candidate_id] = region
    sampled = deterministic_sample_ids(
        str(manifest["source"]["sha256"]),
        list(eligible),
        fraction=float(profile["ocr"]["deterministic_sample_fraction"]),
        minimum=int(profile["ocr"]["deterministic_sample_minimum"]),
    )
    candidates = {str(item["candidate_id"]): item for item in manifest["text_candidates"]}
    calibration_resolved = calibration_path.resolve(strict=True)
    fusion_resolved = fusion_path.resolve(strict=True)
    for decision in document["decisions"]:
        candidate_id = str(decision["candidate_id"])
        if candidate_id not in eligible or decision["status"] != "PENDING":
            continue
        if candidate_id in sampled:
            decision["review_note"] = (
                str(decision.get("review_note", "")) + " Deterministic consensus audit sample; authoritative review required."
            ).strip()
            continue
        candidate = candidates[candidate_id]
        decision.update(
            {
                "status": "CONFIRMED",
                "confirmed_text": candidate["text"],
                "evidence": {
                    "kind": "consensus_auto",
                    "detail": {
                        "policy_profile": profile_name,
                        "calibration_receipt_path": str(calibration_resolved),
                        "calibration_receipt_sha256": sha256_file(calibration_resolved),
                        "fusion_manifest_path": str(fusion_resolved),
                        "fusion_manifest_sha256": sha256_file(fusion_resolved),
                        "region_id": eligible[candidate_id],
                        "sampled": False,
                    },
                },
                "review_note": "Calibrated ordinary-text consensus; not user-confirmed.",
            }
        )
    validate_json(document, review_schema, "consensus-updated review decisions")
    atomic_write_json(output_path, document)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--fusion", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--profile", default="standard", choices=("standard", "strict"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        apply_consensus(
            args.manifest, args.decisions, args.fusion, args.calibration, args.output, args.profile
        )
    except (OSError, ValueError, PerceptionPolicyError) as exc:
        print(f"OCR_CONSENSUS_REJECTED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
