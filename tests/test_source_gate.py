from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from tools import common
from tools.contracts import (
    read_json,
    record_candidate_provenance,
    record_seed_unavailable,
    write_json,
)
from tools.source_gate import (
    evaluate_case_source_gate,
    evaluate_source_gate,
    write_source_gate_report,
)


def _reference(tmp_path: Path, size: tuple[int, int] = (160, 100)) -> tuple[Path, str]:
    path = tmp_path / "reference.png"
    Image.new("RGB", size, "white").save(path)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(reference_sha256: str, **overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "semantic_schema_version": "4.0.0",
        "reference_sha256": reference_sha256,
        "object_inventory_sha256": "a" * 64,
        "stable_element_ids": True,
        "relations_exhaustive": True,
        "case": "case-01",
    }
    metadata.update(overrides)
    return metadata


def _svg(tmp_path: Path, body: str, *, root_attributes: str = "") -> Path:
    path = tmp_path / "candidate.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        f'viewBox="0 0 160 100" {root_attributes}>{body}</svg>',
        encoding="utf-8",
    )
    return path


def _evaluate(
    tmp_path: Path,
    candidate: Path,
    *,
    route: str = "svg-seeded",
    role: str = "external-seed",
    seed_gate_status: str | None = None,
    metadata: dict[str, object] | None = None,
    authorized_image_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    reference, reference_sha256 = _reference(tmp_path)
    return evaluate_source_gate(
        candidate,
        reference_path=reference,
        input_route=route,
        candidate_role=role,
        expected_reference_sha256=reference_sha256,
        expected_canvas=(160, 100),
        semantic_metadata=metadata or _metadata(reference_sha256),
        expected_case="case-01",
        expected_inventory_sha256="a" * 64,
        seed_gate_status=seed_gate_status,
        authorized_image_ids=authorized_image_ids,
    )


def _codes(report: dict[str, object]) -> set[str]:
    return {item["code"] for item in report["findings"]}  # type: ignore[index]


def _case_run(tmp_path: Path, *, with_seed: bool = False) -> common.Run:
    reference, _ = _reference(tmp_path)
    run = common.create_run(
        reference,
        case="case-01",
        cases_root=tmp_path / "examples",
        input_route="svg-seeded",
    )
    write_json(
        run.qa_dir / "reference-inventory-receipt.json",
        {"inventory_sha256": "a" * 64},
    )
    if with_seed:
        run.external_seed_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
            'viewBox="0 0 160 100"><rect id="seed-panel" width="10" height="10"/></svg>',
            encoding="utf-8",
        )
        record_candidate_provenance(
            run,
            run.external_seed_svg,
            kind="svg",
            origin="test",
            role="external-seed",
            canonical_path="external-seed.svg",
        )
    return run


def _case_metadata(run: common.Run) -> dict[str, object]:
    meta = run.load_meta()
    return _metadata(
        meta["source_sha256"],
        case=meta["case"],
        object_inventory_sha256="a" * 64,
    )


def test_accepts_hash_bound_supported_external_seed(tmp_path: Path) -> None:
    candidate = _svg(
        tmp_path,
        '<rect id="panel" x="10" y="10" width="140" height="80" fill="#eef4fb"/>',
    )
    report = _evaluate(tmp_path, candidate)

    assert report["schema_version"] == "4.0.0"
    assert report["kind"] == "source_gate_report"
    assert report["decision"] == "accept"
    assert report["pass"] is True
    assert report["next_action"] == "normalize-seed-to-scene"
    assert report["blockers"] == []
    assert report["candidate"]["source_name"] == "candidate.svg"  # type: ignore[index]
    assert "tmp" not in report["candidate"]["path_base"]  # type: ignore[index]


def test_reference_only_rejects_external_seed(tmp_path: Path) -> None:
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    report = _evaluate(
        tmp_path,
        candidate,
        route="reference-only",
        role="external-seed",
        seed_gate_status="forbidden",
    )

    assert report["decision"] == "reject"
    assert "source-gate:route:reference-only-external-seed" in _codes(report)


def test_seed_gate_rejects_duplicate_and_requires_explicit_abandonment(tmp_path: Path) -> None:
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    duplicate = _evaluate(tmp_path, candidate, seed_gate_status="accepted")
    assert "source-gate:seed-gate:duplicate-seed" in _codes(duplicate)

    reconstruction = _evaluate(
        tmp_path,
        candidate,
        role="reconstruction-candidate",
        seed_gate_status="accepted",
    )
    assert "source-gate:seed-gate:abandonment-required" in _codes(reconstruction)


def test_reference_and_candidate_hash_drift_are_hard_rejections(tmp_path: Path) -> None:
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    reference, reference_sha256 = _reference(tmp_path)
    report = evaluate_source_gate(
        candidate,
        reference_path=reference,
        input_route="svg-seeded",
        candidate_role="external-seed",
        expected_reference_sha256="b" * 64,
        expected_canvas=(160, 100),
        semantic_metadata=_metadata(reference_sha256),
        expected_case="case-01",
        expected_inventory_sha256="a" * 64,
        expected_candidate_sha256="c" * 64,
    )

    assert report["decision"] == "reject"
    assert {
        "source-gate:hash:reference-drift",
        "source-gate:hash:candidate-drift",
        "source-gate:hash:declared-reference-mismatch",
    }.issubset(_codes(report))


def test_canvas_mismatch_rejects_but_missing_metadata_is_repairable(tmp_path: Path) -> None:
    mismatch = tmp_path / "mismatch.svg"
    mismatch.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="161" height="100" '
        'viewBox="0 0 161 100"><rect id="panel" width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    mismatch_report = _evaluate(tmp_path, mismatch)
    assert mismatch_report["decision"] == "reject"
    assert "source-gate:canvas:width-mismatch" in _codes(mismatch_report)
    assert "source-gate:canvas:viewbox-mismatch" in _codes(mismatch_report)

    missing = tmp_path / "missing.svg"
    missing.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect id="panel" width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    missing_report = _evaluate(tmp_path, missing)
    assert missing_report["decision"] == "repair"
    assert "source-gate:canvas:width-missing-or-invalid" in _codes(missing_report)


def test_image_policy_accepts_only_small_authorized_case_bound_assets(tmp_path: Path) -> None:
    tiny_png = "data:image/png;base64,iVBORw0KGgo="
    candidate = _svg(
        tmp_path,
        f'<image id="atomic:icon" x="5" y="5" width="20" height="20" href="{tiny_png}"/>',
    )
    unauthorized = _evaluate(tmp_path, candidate)
    assert unauthorized["decision"] == "repair"
    assert "source-gate:image:authorization-missing" in _codes(unauthorized)

    authorized = _evaluate(tmp_path, candidate, authorized_image_ids=("atomic:icon",))
    assert authorized["decision"] == "accept"


def test_whole_reference_and_tiled_rasters_are_rejected(tmp_path: Path) -> None:
    whole = _svg(
        tmp_path,
        '<image id="atomic:whole" width="160" height="100" href="data:image/png;base64,AA=="/>',
    )
    whole_report = _evaluate(tmp_path, whole, authorized_image_ids=("atomic:whole",))
    assert whole_report["decision"] == "reject"
    assert "source-gate:image:whole-reference-like" in _codes(whole_report)

    tiled = _svg(
        tmp_path,
        '<image id="atomic:a" width="80" height="60" href="data:image/png;base64,AA=="/>'
        '<image id="atomic:b" x="80" width="80" height="60" href="data:image/png;base64,AA=="/>',
    )
    tiled_report = _evaluate(
        tmp_path,
        tiled,
        authorized_image_ids=("atomic:a", "atomic:b"),
    )
    assert tiled_report["decision"] == "reject"
    assert "source-gate:image:aggregate-coverage" in _codes(tiled_report)


def test_unsupported_features_are_repairable_or_rejected_by_risk(tmp_path: Path) -> None:
    repairable = _svg(
        tmp_path,
        '<defs><filter id="blur"/></defs><rect id="panel" width="10" height="10" filter="url(#blur)"/>',
    )
    repair_report = _evaluate(tmp_path, repairable)
    assert repair_report["decision"] == "repair"
    assert "source-gate:unsupported-feature:repair:filter" in _codes(repair_report)

    active = _svg(
        tmp_path,
        '<script>alert(1)</script><rect id="panel" width="10" height="10"/>',
    )
    reject_report = _evaluate(tmp_path, active)
    assert reject_report["decision"] == "reject"
    assert "source-gate:unsupported-feature:reject:script" in _codes(reject_report)

    relative_image = _svg(
        tmp_path,
        '<image id="atomic:icon" width="10" height="10" href="outside.png"/>',
    )
    relative_report = _evaluate(
        tmp_path,
        relative_image,
        authorized_image_ids=("atomic:icon",),
    )
    assert relative_report["decision"] == "reject"
    assert "source-gate:image:external-reference" in _codes(relative_report)


def test_split_line_and_polyline_arrow_requires_source_repair(tmp_path: Path) -> None:
    candidate = _svg(
        tmp_path,
        '<g id="split-arrow">'
        '<line id="split-arrow-shaft" x1="10" y1="50" x2="110" y2="50"/>'
        '<polyline id="split-arrow-head" points="100,42 110,50 100,58"/>'
        "</g>",
    )

    report = _evaluate(tmp_path, candidate)

    assert report["decision"] == "repair"
    assert "source-gate:semantic-metadata:split-arrow-composition" in _codes(report)
    assert report["semantic_metadata"]["structure"]["split_arrow_group_count"] == 1


def test_semantic_metadata_missing_is_repairable_but_conflict_and_duplicates_reject(
    tmp_path: Path,
) -> None:
    candidate = _svg(
        tmp_path,
        '<rect id="duplicate" width="10" height="10"/>'
        '<circle id="duplicate" cx="20" cy="20" r="5"/>',
        root_attributes='data-reference-sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"',
    )
    reference, reference_sha256 = _reference(tmp_path)
    report = evaluate_source_gate(
        candidate,
        reference_path=reference,
        input_route="svg-seeded",
        candidate_role="external-seed",
        expected_reference_sha256=reference_sha256,
        expected_canvas=(160, 100),
        semantic_metadata=_metadata(reference_sha256),
        expected_case="case-01",
        expected_inventory_sha256="a" * 64,
    )
    assert report["decision"] == "reject"
    assert "source-gate:semantic-metadata:conflict:reference_sha256" in _codes(report)
    assert "source-gate:semantic-metadata:duplicate-element-ids" in _codes(report)

    missing = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    missing_report = evaluate_source_gate(
        missing,
        reference_path=reference,
        input_route="svg-seeded",
        candidate_role="external-seed",
        expected_reference_sha256=reference_sha256,
        expected_canvas=(160, 100),
        semantic_metadata={},
        expected_case="case-01",
    )
    assert missing_report["decision"] == "repair"
    assert "reference_sha256" in missing_report["semantic_metadata"]["missing_fields"]


def test_report_writer_emits_atomic_schema_4_payload(tmp_path: Path) -> None:
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    report = _evaluate(tmp_path, candidate)
    output = tmp_path / "qa" / "source-gate-report.json"

    assert write_source_gate_report(report, output) == output
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == report
    assert not output.with_suffix(".json.tmp").exists()


def test_case_gate_rejects_repair_when_external_seed_is_missing(tmp_path: Path) -> None:
    run = _case_run(tmp_path)
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')

    report = evaluate_case_source_gate(
        run,
        candidate,
        candidate_role="repair-candidate",
        semantic_metadata=_case_metadata(run),
    )

    assert report["decision"] == "reject"
    assert "source-gate:seed:unavailable" in _codes(report)
    assert report["next_action"] == "declare-seed-unavailable-and-reconstruct-from-reference"
    assert read_json(run.source_gate_report_path) == report


def test_case_gate_rejects_repair_when_external_seed_hash_drifts(tmp_path: Path) -> None:
    run = _case_run(tmp_path, with_seed=True)
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    run.external_seed_svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        'viewBox="0 0 160 100"><circle id="drift" cx="10" cy="10" r="5"/></svg>',
        encoding="utf-8",
    )

    report = evaluate_case_source_gate(
        run,
        candidate,
        candidate_role="repair-candidate",
        semantic_metadata=_case_metadata(run),
    )

    assert report["decision"] == "reject"
    assert "source-gate:seed:hash-mismatch" in _codes(report)
    assert report["next_action"] == "declare-seed-unavailable-and-reconstruct-from-reference"


@pytest.mark.parametrize(
    ("disallowed_role", "expected_code"),
    [
        ("external-seed", "source-gate:seed-gate:closed-after-rejection"),
        ("repair-candidate", "source-gate:seed-gate:reconstruction-required"),
    ],
)
def test_seed_unavailable_allows_only_reconstruction_candidate(
    tmp_path: Path,
    disallowed_role: str,
    expected_code: str,
) -> None:
    run = _case_run(tmp_path)
    candidate = _svg(tmp_path, '<rect id="panel" width="10" height="10"/>')
    record_seed_unavailable(
        run,
        reason="legacy seed bytes were not retained",
        expected_sha256="b" * 64,
    )

    rejected = evaluate_case_source_gate(
        run,
        candidate,
        candidate_role=disallowed_role,
        semantic_metadata=_case_metadata(run),
    )
    accepted = evaluate_case_source_gate(
        run,
        candidate,
        candidate_role="reconstruction-candidate",
        semantic_metadata=_case_metadata(run),
    )

    assert rejected["decision"] == "reject"
    assert expected_code in _codes(rejected)
    assert accepted["decision"] == "accept"
    assert accepted["next_action"] == "normalize-candidate-to-scene"
