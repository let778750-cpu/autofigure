"""Create and finalize a hash-bound review of OCR perception candidates.

The raw PaddleOCR manifest deliberately contains hypotheses only.  This tool is
the only bridge from those hypotheses to confirmed text: it requires a separate
review document, authoritative evidence, an exact candidate set, and a receipt
that is bound to the raw manifest bytes, source image hash, and run id.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.finalize_perception_review
    try:
        from .output_policy import resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the project root.
        from tools.output_policy import resolve_output_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_SCHEMA = PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
DEFAULT_REVIEW_SCHEMA = PROJECT_ROOT / "schemas" / "perception-review.schema.json"
DEFAULT_FUSION_SCHEMA = PROJECT_ROOT / "schemas" / "fusion-manifest.schema.json"

TERMINAL_STATUSES = {
    "CONFIRMED",
    "CORRECTED",
    "NOT_TEXT",
    "FORMULA_CONFIRMED",
}
UNRESOLVED_STATUSES = {"PENDING", "INCONCLUSIVE"}
AUTHORITATIVE_EVIDENCE = {"user_confirmed", "source_text"}


class ReviewError(RuntimeError):
    """A fail-closed validation or review-policy error."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"Unable to read {label} JSON {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be a JSON object: {source}")
    return value


def load_schema(path: str | Path, label: str) -> dict[str, Any]:
    schema = load_json_object(path, label)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ReviewError(f"Invalid {label}: {exc.message}") from exc
    return schema


def validate_json(instance: Any, schema: Mapping[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    details = []
    for error in errors[:5]:
        location = "$"
        for part in error.absolute_path:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        details.append(f"{location}: {error.message}")
    if len(errors) > 5:
        details.append(f"... and {len(errors) - 5} more validation error(s)")
    raise ReviewError(f"{label} is not schema-valid: " + "; ".join(details))


def atomic_write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = resolve_output_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def is_formula_like(candidate: Mapping[str, Any]) -> bool:
    return any("FORMULA_LIKE" in str(flag).upper() for flag in candidate["review_flags"])


def _candidate_decision(candidate: Mapping[str, Any], status: str = "PENDING") -> dict[str, Any]:
    note = "" if status == "PENDING" else "Decision missing from review input."
    return {
        "candidate_id": candidate["candidate_id"],
        "ocr_text": candidate["text"],
        "ocr_confidence": candidate["ocr_confidence"],
        "review_flags": list(candidate["review_flags"]),
        "formula_like": is_formula_like(candidate),
        "status": status,
        "confirmed_text": None,
        "authoritative_latex": None,
        "evidence": {"kind": None, "detail": None},
        "review_note": note,
    }


def _validate_raw_semantics(
    manifest: Mapping[str, Any], raw_schema_path: Path, raw_schema_sha256: str
) -> None:
    candidate_ids = [item["candidate_id"] for item in manifest["text_candidates"]]
    duplicate_ids = sorted(
        candidate_id for candidate_id, count in Counter(candidate_ids).items() if count > 1
    )
    if duplicate_ids:
        raise ReviewError(f"Raw manifest contains duplicate candidate IDs: {duplicate_ids}")
    if manifest["summary"]["candidate_count"] != len(candidate_ids):
        raise ReviewError("Raw manifest summary.candidate_count does not match text_candidates")
    configured_hash = manifest["configuration"]["manifest_schema_sha256"]
    if configured_hash != raw_schema_sha256:
        raise ReviewError(
            "Raw manifest was not generated against the supplied raw schema: "
            f"manifest={configured_hash}, supplied={raw_schema_sha256}, "
            f"schema={raw_schema_path}"
        )


def load_validated_raw_manifest(
    manifest_path: str | Path, raw_schema_path: str | Path
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    resolved_manifest = Path(manifest_path).resolve()
    resolved_schema = Path(raw_schema_path).resolve()
    manifest = load_json_object(resolved_manifest, "raw perception manifest")
    schema = load_schema(resolved_schema, "raw perception schema")
    validate_json(manifest, schema, "raw perception manifest")
    _validate_raw_semantics(manifest, resolved_schema, sha256_file(resolved_schema))
    return manifest, schema, resolved_manifest, resolved_schema


def make_binding(
    manifest: Mapping[str, Any], manifest_path: Path, raw_schema_path: Path
) -> dict[str, Any]:
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_schema_path": str(raw_schema_path),
        "manifest_schema_sha256": sha256_file(raw_schema_path),
        "run_id": manifest["run_id"],
        "source_path": manifest["source"]["path"],
        "source_sha256": manifest["source"]["sha256"],
    }


def _fusion_note(fact: Mapping[str, Any]) -> str:
    detail = fact["detail"]
    parts = [
        f"rank_note tier={fact['consistency_tier']}",
        f"channels={''.join(k for k, on in fact['channels'].items() if on) or 'none'}",
    ]
    if detail.get("vlm_opinion") is not None:
        parts.append(f"vlm={detail['vlm_opinion']}")
    if fact["conflict_reasons"]:
        parts.append("reasons=" + ",".join(fact["conflict_reasons"]))
    if detail.get("proposal_latex") is not None:
        proposal = " ".join(str(detail["proposal_latex"]).split())
        digest = str(detail.get("proposal_latex_sha256") or "")[:8]
        parts.append(f"latex_proposal_sha256={digest}")
        parts.append(f"latex_proposal={proposal}")
        parts.append("proposal_status=PROPOSAL_ONLY_NOT_AUTHORITATIVE")
    return "fusion: " + "; ".join(parts)


def load_fusion_guidance(
    fusion_manifest_path: str | Path,
    manifest: Mapping[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    """Load and bind a fusion manifest; return per-candidate review guidance.

    Fusion only reorders attention and pre-fills traceable notes.  It never
    changes what counts as confirmation.
    """
    resolved = Path(fusion_manifest_path).resolve()
    fusion = load_json_object(resolved, "perception fusion manifest")
    fusion_schema = load_schema(DEFAULT_FUSION_SCHEMA, "perception fusion schema")
    validate_json(fusion, fusion_schema, "perception fusion manifest")
    if str(fusion["run_id"]) != str(manifest["run_id"]):
        raise ReviewError("fusion manifest run_id does not match the raw manifest")
    if str(fusion["inputs"]["ocr_manifest"]["sha256"]).upper() != sha256_file(manifest_path):
        raise ReviewError("fusion manifest is not bound to the current raw manifest")
    if str(fusion["source"]["sha256"]).upper() != str(manifest["source"]["sha256"]).upper():
        raise ReviewError("fusion manifest source hash does not match the raw manifest")
    if fusion["policy"]["human_review_required"] is not True:
        raise ReviewError("fusion manifest waived human review; refusing to consume it")

    guidance: dict[str, dict[str, Any]] = {}
    for item in fusion["review_queue"]:
        subject = str(item["subject_id"])
        fact = next(
            (f for f in fusion["facts"] if f["fact_id"] == item["fact_id"]),
            None,
        )
        if fact is None:
            raise ReviewError(f"fusion review queue references unknown fact {item['fact_id']}")
        entry = guidance.setdefault(
            subject, {"rank": item["rank"], "band": item["band"], "notes": []}
        )
        if item["rank"] < entry["rank"]:
            entry["rank"] = item["rank"]
            entry["band"] = item["band"]
        entry["notes"].append(_fusion_note(fact))
    return guidance


def initialize_review(
    manifest_path: str | Path,
    decisions_path: str | Path,
    *,
    raw_schema_path: str | Path = DEFAULT_RAW_SCHEMA,
    review_schema_path: str | Path = DEFAULT_REVIEW_SCHEMA,
    fusion_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    manifest, _, resolved_manifest, resolved_raw_schema = load_validated_raw_manifest(
        manifest_path, raw_schema_path
    )
    review_schema = load_schema(review_schema_path, "perception review schema")

    guidance: dict[str, Mapping[str, Any]] = {}
    if fusion_manifest_path is not None:
        guidance = load_fusion_guidance(
            fusion_manifest_path, manifest, resolved_manifest
        )

    decisions = [_candidate_decision(item) for item in manifest["text_candidates"]]
    if guidance:
        for decision in decisions:
            entry = guidance.get(decision["candidate_id"])
            if entry is not None and not decision["review_note"]:
                decision["review_note"] = " ".join(entry["notes"])
        # Fusion rank decides reading order; candidates without fusion facts
        # keep their manifest order after all ranked ones.
        max_rank = max((entry["rank"] for entry in guidance.values()), default=0)
        decisions.sort(
            key=lambda decision: (
                guidance[decision["candidate_id"]]["rank"]
                if decision["candidate_id"] in guidance
                else max_rank + 1,
                decision["candidate_id"],
            )
        )

    document = {
        "schema_version": "1.0.0",
        "document_type": "PERCEPTION_REVIEW_DECISIONS",
        "created_at_utc": utc_now(),
        "status": "REVIEW_PENDING",
        "raw_manifest": make_binding(manifest, resolved_manifest, resolved_raw_schema),
        "decisions": decisions,
    }
    validate_json(document, review_schema, "generated perception review decisions")
    atomic_write_json(decisions_path, document)
    return document


def _require_binding(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    required_fields = {
        "manifest_sha256",
        "manifest_schema_sha256",
        "run_id",
        "source_sha256",
    }
    mismatches = sorted(
        field for field in required_fields if actual.get(field) != expected.get(field)
    )
    if mismatches:
        descriptions = [
            f"{field}: decisions={actual.get(field)!r}, raw={expected.get(field)!r}"
            for field in mismatches
        ]
        raise ReviewError("Review decisions binding mismatch: " + "; ".join(descriptions))


def _validate_snapshot(decision: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    expected = _candidate_decision(candidate)
    snapshot_fields = ("ocr_text", "ocr_confidence", "review_flags", "formula_like")
    mismatches = [
        field for field in snapshot_fields if decision.get(field) != expected.get(field)
    ]
    if mismatches:
        raise ReviewError(
            f"{candidate['candidate_id']} candidate snapshot differs from the raw manifest: "
            + ", ".join(mismatches)
        )


def _require_nonblank(value: Any, field: str, candidate_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{candidate_id} {field} must be nonempty")
    return value


def _validate_authoritative_evidence(
    decision: Mapping[str, Any], candidate_id: str
) -> None:
    evidence = decision["evidence"]
    if evidence["kind"] not in AUTHORITATIVE_EVIDENCE:
        raise ReviewError(
            f"{candidate_id} requires user_confirmed or source_text evidence; "
            "OCR is not authoritative evidence"
        )
    _require_nonblank(evidence["detail"], "evidence.detail", candidate_id)


def _validate_decision_policy(
    decision: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    candidate_id = candidate["candidate_id"]
    status = decision["status"]
    formula_like = is_formula_like(candidate)
    if status in TERMINAL_STATUSES:
        _validate_authoritative_evidence(decision, candidate_id)
    if status in {"CONFIRMED", "CORRECTED"}:
        confirmed_text = _require_nonblank(
            decision["confirmed_text"], "confirmed_text", candidate_id
        )
        if formula_like:
            raise ReviewError(
                f"{candidate_id} is FORMULA_LIKE and cannot use ordinary {status}; "
                "use FORMULA_CONFIRMED or NOT_TEXT"
            )
        if status == "CONFIRMED" and confirmed_text != candidate["text"]:
            raise ReviewError(
                f"{candidate_id} CONFIRMED text must equal the OCR candidate; "
                "use CORRECTED for changed text"
            )
    elif status == "FORMULA_CONFIRMED":
        if not formula_like:
            raise ReviewError(
                f"{candidate_id} is not FORMULA_LIKE and cannot use FORMULA_CONFIRMED"
            )
        _require_nonblank(decision["authoritative_latex"], "authoritative_latex", candidate_id)
    elif status not in TERMINAL_STATUSES | UNRESOLVED_STATUSES:
        raise ReviewError(f"{candidate_id} has unsupported status {status!r}")


def _index_input_decisions(
    decisions: Sequence[Mapping[str, Any]], expected_ids: set[str]
) -> dict[str, Mapping[str, Any]]:
    identifiers = [item["candidate_id"] for item in decisions]
    duplicates = sorted(
        candidate_id for candidate_id, count in Counter(identifiers).items() if count > 1
    )
    if duplicates:
        raise ReviewError(f"Review decisions contain duplicate candidate IDs: {duplicates}")
    extra = sorted(set(identifiers) - expected_ids)
    if extra:
        raise ReviewError(f"Review decisions contain candidate IDs absent from raw manifest: {extra}")
    return {item["candidate_id"]: item for item in decisions}


def _raw_gate_blockers(manifest: Mapping[str, Any]) -> list[str]:
    blockers = []
    if manifest["status"] != "OCR_HYPOTHESES_REVIEW_REQUIRED":
        blockers.append(f"raw_manifest_status={manifest['status']}")
    if manifest["acceptance_checks"]["passed"] is not True:
        blockers.append("raw_manifest_acceptance_checks_failed")
    if not manifest["text_candidates"]:
        blockers.append("raw_manifest_has_zero_candidates")
    return blockers


def finalize_review(
    manifest_path: str | Path,
    decisions_path: str | Path,
    receipt_path: str | Path,
    *,
    raw_schema_path: str | Path = DEFAULT_RAW_SCHEMA,
    review_schema_path: str | Path = DEFAULT_REVIEW_SCHEMA,
) -> tuple[dict[str, Any], int]:
    manifest, _, resolved_manifest, resolved_raw_schema = load_validated_raw_manifest(
        manifest_path, raw_schema_path
    )
    review_schema = load_schema(review_schema_path, "perception review schema")
    resolved_decisions = Path(decisions_path).resolve()
    review_input = load_json_object(resolved_decisions, "perception review decisions")
    validate_json(review_input, review_schema, "perception review decisions")
    if review_input["document_type"] != "PERCEPTION_REVIEW_DECISIONS":
        raise ReviewError("--decisions must be a PERCEPTION_REVIEW_DECISIONS document")

    binding = make_binding(manifest, resolved_manifest, resolved_raw_schema)
    _require_binding(review_input["raw_manifest"], binding)

    candidates = manifest["text_candidates"]
    expected_ids = {item["candidate_id"] for item in candidates}
    indexed = _index_input_decisions(review_input["decisions"], expected_ids)
    missing_ids = [item["candidate_id"] for item in candidates if item["candidate_id"] not in indexed]

    complete_decisions = []
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        if candidate_id not in indexed:
            complete_decisions.append(_candidate_decision(candidate, "INCONCLUSIVE"))
            continue
        decision = dict(indexed[candidate_id])
        decision["review_flags"] = list(decision["review_flags"])
        decision["evidence"] = dict(decision["evidence"])
        _validate_snapshot(decision, candidate)
        _validate_decision_policy(decision, candidate)
        complete_decisions.append(decision)

    status_counts = Counter(item["status"] for item in complete_decisions)
    unresolved_ids = [
        item["candidate_id"]
        for item in complete_decisions
        if item["status"] in UNRESOLVED_STATUSES
    ]
    terminal_count = sum(status_counts[item] for item in TERMINAL_STATUSES)
    blockers = _raw_gate_blockers(manifest)
    receipt_status = (
        "PERCEPTION_REVIEW_PASS" if not unresolved_ids and not blockers else "INCONCLUSIVE"
    )
    counts = {
        "total_candidates": len(candidates),
        "input_decisions": len(review_input["decisions"]),
        "terminal_count": terminal_count,
        "confirmed_count": status_counts["CONFIRMED"],
        "corrected_count": status_counts["CORRECTED"],
        "not_text_count": status_counts["NOT_TEXT"],
        "formula_confirmed_count": status_counts["FORMULA_CONFIRMED"],
        "pending_count": status_counts["PENDING"],
        "inconclusive_count": status_counts["INCONCLUSIVE"],
        "missing_count": len(missing_ids),
        "unresolved_count": len(unresolved_ids),
        "gate_blocker_count": len(blockers),
    }
    receipt = {
        "schema_version": "1.0.0",
        "document_type": "PERCEPTION_REVIEW_RECEIPT",
        "created_at_utc": utc_now(),
        "status": receipt_status,
        "raw_manifest": binding,
        "review_input": {
            "path": str(resolved_decisions),
            "sha256": sha256_file(resolved_decisions),
        },
        "policy": {
            "ocr_is_ground_truth": False,
            "ocr_may_self_confirm": False,
            "all_terminal_text_requires_authoritative_evidence": True,
            "formula_requires_authoritative_latex": True,
        },
        "decisions": complete_decisions,
        "counts": counts,
        "missing_candidate_ids": missing_ids,
        "unresolved_candidate_ids": unresolved_ids,
        "gate_blockers": blockers,
    }
    validate_json(receipt, review_schema, "generated perception review receipt")
    atomic_write_json(receipt_path, receipt)
    return receipt, 0 if receipt_status == "PERCEPTION_REVIEW_PASS" else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize or finalize the authoritative review of an unverified OCR manifest."
        )
    )
    parser.add_argument("--manifest", required=True, help="Raw perception-manifest.json")
    parser.add_argument(
        "--decisions",
        required=True,
        help="Review decisions JSON (output in --init mode, input otherwise)",
    )
    parser.add_argument(
        "--output",
        help="Final perception review receipt JSON (required unless --init)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Create a PENDING decision template instead of finalizing",
    )
    parser.add_argument(
        "--raw-schema",
        default=str(DEFAULT_RAW_SCHEMA),
        help="Raw perception manifest Draft 2020-12 schema",
    )
    parser.add_argument(
        "--review-schema",
        default=str(DEFAULT_REVIEW_SCHEMA),
        help="Perception review Draft 2020-12 schema",
    )
    parser.add_argument(
        "--fusion-manifest",
        default=None,
        help=(
            "Optional perception fusion manifest: reorders the --init decision "
            "template by review priority and pre-fills traceable review notes"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.init and args.output:
        parser.error("--output is not used with --init; --decisions is the template output")
    if not args.init and not args.output:
        parser.error("--output is required when finalizing")
    try:
        if args.init:
            document = initialize_review(
                args.manifest,
                args.decisions,
                raw_schema_path=args.raw_schema,
                review_schema_path=args.review_schema,
                fusion_manifest_path=args.fusion_manifest,
            )
            print(
                json.dumps(
                    {
                        "status": document["status"],
                        "candidate_count": len(document["decisions"]),
                        "decisions": str(Path(args.decisions).resolve()),
                        "fusion_manifest": (
                            str(Path(args.fusion_manifest).resolve())
                            if args.fusion_manifest
                            else None
                        ),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.fusion_manifest:
            # Binding check only: fusion reorders attention, never confirmation.
            manifest_for_fusion, _, resolved_for_fusion, _ = load_validated_raw_manifest(
                args.manifest, args.raw_schema
            )
            load_fusion_guidance(
                args.fusion_manifest, manifest_for_fusion, resolved_for_fusion
            )
        receipt, exit_code = finalize_review(
            args.manifest,
            args.decisions,
            args.output,
            raw_schema_path=args.raw_schema,
            review_schema_path=args.review_schema,
        )
        print(
            json.dumps(
                {
                    "status": receipt["status"],
                    "unresolved_count": receipt["counts"]["unresolved_count"],
                    "gate_blocker_count": receipt["counts"]["gate_blocker_count"],
                    "receipt": str(Path(args.output).resolve()),
                    "fusion_manifest": (
                        str(Path(args.fusion_manifest).resolve())
                        if args.fusion_manifest
                        else None
                    ),
                },
                ensure_ascii=False,
            )
        )
        return exit_code
    except ReviewError as exc:
        print(f"perception review error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
