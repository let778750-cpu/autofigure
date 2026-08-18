"""Prepare fail-closed OCR decisions from a FROZEN source authority.

Only exact normalized text within a unique authority bbox, or a candidate whose
center falls inside one unique confirmed formula bbox, is promoted. Everything
else remains INCONCLUSIVE for explicit source/user review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from finalize_perception_review import (  # noqa: E402
    DEFAULT_RAW_SCHEMA,
    DEFAULT_REVIEW_SCHEMA,
    _candidate_decision,
    atomic_write_json,
    load_fusion_guidance,
    load_schema,
    load_validated_raw_manifest,
    make_binding,
    sha256_file,
    utc_now,
    validate_json,
)
from validate_source_authority import (  # noqa: E402
    SourceAuthorityError,
    validate_authority,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTHORITY_SCHEMA = PROJECT_ROOT / "schemas" / "source-authority.schema.json"
DEFAULT_OVERRIDES_SCHEMA = (
    PROJECT_ROOT / "schemas" / "perception-review-overrides.schema.json"
)


class AuthorityReviewPreparationError(RuntimeError):
    """Raised when exact authority matching cannot be performed safely."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorityReviewPreparationError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorityReviewPreparationError(f"{label} must be a JSON object")
    return value


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _bbox_contains_center(
    outer: Mapping[str, Any], inner: Mapping[str, Any], *, tolerance: float = 4.0
) -> bool:
    center_x = float(inner["x"]) + float(inner["w"]) / 2
    center_y = float(inner["y"]) + float(inner["h"]) / 2
    return (
        float(outer["x"]) - tolerance
        <= center_x
        <= float(outer["x"]) + float(outer["w"]) + tolerance
        and float(outer["y"]) - tolerance
        <= center_y
        <= float(outer["y"]) + float(outer["h"]) + tolerance
    )


def _text_segments(value: str) -> list[str]:
    segments: list[str] = []
    for line in value.splitlines() or [value]:
        stripped = line.strip()
        if not stripped:
            continue
        segments.append(stripped)
        tokens = re.findall(r"\S+", stripped)
        for start in range(len(tokens)):
            for end in range(start + 1, len(tokens) + 1):
                segments.append(" ".join(tokens[start:end]))
    unique: list[str] = []
    seen: set[tuple[str, str]] = set()
    for segment in segments:
        key = (_normalized(segment), segment)
        if key[0] and key not in seen:
            seen.add(key)
            unique.append(segment)
    return unique


def _authority_value(item: Mapping[str, Any]) -> str | None:
    if item["kind"] == "TEXT":
        return item["text"]
    if item["kind"] in {"SEMANTIC_REGION", "MANUAL_ASSET"}:
        return item["label"]
    return None


def _evidence(item: Mapping[str, Any], authority_hash: str) -> dict[str, str]:
    source_evidence = item["source_evidence"]
    if not source_evidence:
        raise AuthorityReviewPreparationError(
            f"{item['authority_item_id']} has no authoritative evidence"
        )
    first = source_evidence[0]
    return {
        "kind": first["kind"],
        "detail": (
            f"{item['authority_item_id']} from {first['locator']}: {first['detail']} "
            f"source-authority-sha256={authority_hash}"
        ),
    }


def _authority_binding(
    authority_path: Path,
    authority_schema_path: Path,
    validation: Mapping[str, Any],
) -> dict[str, str]:
    return {
        "path": str(authority_path),
        "sha256": validation["authority_sha256"],
        "schema_path": str(authority_schema_path),
        "schema_sha256": sha256_file(authority_schema_path),
        "authority_id": validation["authority_id"],
        "source_sha256": validation["source_sha256"],
    }


def _apply_overrides(
    decisions: list[dict[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    overrides_path: Path,
    overrides_schema_path: Path,
    *,
    manifest_sha256: str,
    source_sha256: str,
    authority_sha256: str,
) -> None:
    overrides = _load_object(overrides_path.resolve(strict=True), "review overrides")
    schema = load_schema(overrides_schema_path, "perception review overrides schema")
    validate_json(overrides, schema, "perception review overrides")
    expected_bindings = {
        "manifest_sha256": manifest_sha256,
        "source_sha256": source_sha256,
        "source_authority_sha256": authority_sha256,
    }
    for field, expected in expected_bindings.items():
        if overrides[field] != expected:
            raise AuthorityReviewPreparationError(
                f"override {field} is stale: expected {expected}, got {overrides[field]}"
            )
    by_candidate = {str(item["candidate_id"]): item for item in candidates}
    by_decision = {str(item["candidate_id"]): item for item in decisions}
    identifiers = [str(item["candidate_id"]) for item in overrides["items"]]
    if len(identifiers) != len(set(identifiers)):
        raise AuthorityReviewPreparationError("override candidate IDs must be unique")
    override_sha256 = sha256_file(overrides_path)
    for override in overrides["items"]:
        candidate_id = str(override["candidate_id"])
        if candidate_id not in by_candidate:
            raise AuthorityReviewPreparationError(
                f"override references unknown candidate {candidate_id}"
            )
        decision = by_decision[candidate_id]
        if decision["status"] != "INCONCLUSIVE":
            raise AuthorityReviewPreparationError(
                f"override may only resolve INCONCLUSIVE candidates: {candidate_id}"
            )
        status = str(override["status"])
        confirmed_text = override["confirmed_text"]
        if status == "CONFIRMED" and confirmed_text != by_candidate[candidate_id]["text"]:
            raise AuthorityReviewPreparationError(
                f"{candidate_id} CONFIRMED override must equal the OCR candidate"
            )
        decision["status"] = status
        decision["confirmed_text"] = confirmed_text
        decision["evidence"] = {
            "kind": "source_text",
            "detail": (
                f"{overrides['source_locator']}: {override['detail']} "
                f"override-sha256={override_sha256}"
            ),
        }
        decision["review_note"] = str(override["rationale"])


def prepare_authoritative_review(
    manifest_path: Path,
    authority_path: Path,
    output_path: Path,
    *,
    raw_schema_path: Path = DEFAULT_RAW_SCHEMA,
    review_schema_path: Path = DEFAULT_REVIEW_SCHEMA,
    authority_schema_path: Path = DEFAULT_AUTHORITY_SCHEMA,
    fusion_manifest_path: Path | None = None,
    overrides_path: Path | None = None,
    overrides_schema_path: Path = DEFAULT_OVERRIDES_SCHEMA,
) -> dict[str, Any]:
    if output_path.exists():
        raise AuthorityReviewPreparationError(f"output already exists: {output_path}")
    manifest, _, resolved_manifest, resolved_raw_schema = load_validated_raw_manifest(
        manifest_path, raw_schema_path
    )
    resolved_authority = authority_path.resolve(strict=True)
    resolved_authority_schema = authority_schema_path.resolve(strict=True)
    try:
        validation = validate_authority(
            resolved_authority,
            schema_path=resolved_authority_schema,
            project_root=PROJECT_ROOT,
        )
    except (SourceAuthorityError, OSError) as exc:
        raise AuthorityReviewPreparationError(str(exc)) from exc
    if validation["authority_status"] != "FROZEN":
        raise AuthorityReviewPreparationError("source authority must be FROZEN")
    if validation["source_sha256"] != manifest["source"]["sha256"]:
        raise AuthorityReviewPreparationError(
            "source authority does not bind the raw perception source"
        )
    authority = _load_object(resolved_authority, "source authority")
    authority_hash = validation["authority_sha256"]
    confirmed_items = [
        item for item in authority["items"] if item["disposition"] == "CONFIRMED"
    ]

    text_entries: list[tuple[Mapping[str, Any], str]] = []
    formula_items: list[Mapping[str, Any]] = []
    for item in confirmed_items:
        value = _authority_value(item)
        if value is not None:
            text_entries.extend((item, segment) for segment in _text_segments(value))
        elif item["kind"] == "FORMULA":
            formula_items.append(item)

    guidance: dict[str, Mapping[str, Any]] = {}
    if fusion_manifest_path is not None:
        guidance = load_fusion_guidance(
            fusion_manifest_path, manifest, resolved_manifest
        )

    decisions = []
    for candidate in manifest["text_candidates"]:
        decision = _candidate_decision(candidate, "INCONCLUSIVE")
        candidate_bbox = candidate["bbox_source"]
        candidate_normalized = _normalized(candidate["text"])
        text_matches: list[tuple[Mapping[str, Any], str]] = []
        for item, segment in text_entries:
            if (
                _normalized(segment) == candidate_normalized
                and _bbox_contains_center(item["bbox_source"], candidate_bbox)
            ):
                text_matches.append((item, segment))
        explicit_text_matches = [
            (item, segment) for item, segment in text_matches if item["kind"] == "TEXT"
        ]
        if explicit_text_matches:
            text_matches = explicit_text_matches
        unique_text_matches = {
            str(item["authority_item_id"]): (item, segment)
            for item, segment in text_matches
        }

        formula_matches = [
            item
            for item in formula_items
            if _bbox_contains_center(
                item["bbox_source"], candidate_bbox, tolerance=0.0
            )
        ]
        if len(unique_text_matches) == 1:
            item, segment = next(iter(unique_text_matches.values()))
            decision["authority_item_id"] = item["authority_item_id"]
            decision["status"] = (
                "CONFIRMED" if segment == candidate["text"] else "CORRECTED"
            )
            decision["confirmed_text"] = segment
            decision["evidence"] = _evidence(item, authority_hash)
            decision["review_note"] = "Exact normalized text and unique spatial authority match."
        elif not unique_text_matches and len(formula_matches) == 1:
            item = formula_matches[0]
            decision["authority_item_id"] = item["authority_item_id"]
            decision["status"] = "FORMULA_CONFIRMED"
            decision["authoritative_latex"] = item["canonical_latex"]
            decision["evidence"] = _evidence(item, authority_hash)
            decision["review_note"] = "Unique formula authority bbox association."
        else:
            match_ids = sorted(unique_text_matches)
            formula_ids = sorted(item["authority_item_id"] for item in formula_matches)
            decision["review_note"] = (
                "No terminal authority promotion: "
                f"text_matches={match_ids}; formula_matches={formula_ids}."
            )
        entry = guidance.get(candidate["candidate_id"])
        if entry is not None:
            fusion_notes = " ".join(entry["notes"])
            decision["review_note"] = f"{decision['review_note']} {fusion_notes}".strip()
        decisions.append(decision)

    if overrides_path is not None:
        _apply_overrides(
            decisions,
            manifest["text_candidates"],
            overrides_path,
            overrides_schema_path.resolve(strict=True),
            manifest_sha256=sha256_file(resolved_manifest),
            source_sha256=manifest["source"]["sha256"],
            authority_sha256=authority_hash,
        )

    document = {
        "schema_version": "1.0.0",
        "document_type": "PERCEPTION_REVIEW_DECISIONS",
        "created_at_utc": utc_now(),
        "status": "REVIEW_PENDING",
        "raw_manifest": make_binding(manifest, resolved_manifest, resolved_raw_schema),
        "source_authority": _authority_binding(
            resolved_authority, resolved_authority_schema, validation
        ),
        "decisions": decisions,
    }
    review_schema = load_schema(review_schema_path, "perception review schema")
    validate_json(document, review_schema, "authoritative perception review decisions")
    atomic_write_json(output_path, document)
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--authority", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--raw-schema", type=Path, default=DEFAULT_RAW_SCHEMA)
    parser.add_argument("--review-schema", type=Path, default=DEFAULT_REVIEW_SCHEMA)
    parser.add_argument("--authority-schema", type=Path, default=DEFAULT_AUTHORITY_SCHEMA)
    parser.add_argument("--fusion-manifest", type=Path)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--overrides-schema", type=Path, default=DEFAULT_OVERRIDES_SCHEMA)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        document = prepare_authoritative_review(
            args.manifest,
            args.authority,
            args.output,
            raw_schema_path=args.raw_schema,
            review_schema_path=args.review_schema,
            authority_schema_path=args.authority_schema,
            fusion_manifest_path=args.fusion_manifest,
            overrides_path=args.overrides,
            overrides_schema_path=args.overrides_schema,
        )
    except (AuthorityReviewPreparationError, OSError) as exc:
        print(f"AUTHORITATIVE_REVIEW_PREPARATION_REJECTED: {exc}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for decision in document["decisions"]:
        counts[decision["status"]] = counts.get(decision["status"], 0) + 1
    print(json.dumps({"status": "PREPARED", "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
