from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from tools.render_source_authority_review import (
    MANIFEST_FILENAME,
    OVERLAY_FILENAME,
    ReviewPackageError,
    _canonical_output_directory,
    render_review_package,
)
from tools.validate_source_authority_review import (
    ReviewValidationError,
    validate_review_package,
)


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = ROOT / "examples" / "modularagent.source-authority.json"
REVIEW_SCHEMA_PATH = ROOT / "schemas" / "source-authority-review.schema.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_review_schema_is_valid_draft_2020_12() -> None:
    schema = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)


def test_real_modularagent_draft_renders_hash_bound_review_package(tmp_path: Path) -> None:
    output_dir = tmp_path.resolve() / "authority-review"

    result = render_review_package(
        AUTHORITY_PATH,
        run_id="modularagent-review-test",
        output_dir=output_dir,
    )

    assert result["status"] == "READY_FOR_HUMAN_REVIEW"
    manifest_path = output_dir / MANIFEST_FILENAME
    overlay_path = output_dir / OVERLAY_FILENAME
    assert result["manifest_sha256"] == _sha256(manifest_path)
    assert result["overlay_sha256"] == _sha256(overlay_path)
    validation = validate_review_package(manifest_path)
    assert validation["status"] == "PASS"
    assert validation["manifest_sha256"] == result["manifest_sha256"]
    assert validation["overlay_sha256"] == result["overlay_sha256"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(manifest)
    assert manifest["authority"]["status"] == "DRAFT"
    assert manifest["authority"]["item_count"] == 52
    assert manifest["counts"] == {
        "total": 52,
        "confirmed": 47,
        "inconclusive": 5,
        "manual_assets": 1,
        "relations": 15,
    }
    assert manifest["review_decision"] is None
    assert manifest["policy"] == {
        "authority_unchanged": True,
        "overlay_is_diagnostic_only": True,
        "approval_required_for_freeze": True,
    }
    with Image.open(overlay_path) as overlay:
        assert overlay.size == (3678, 1254)
        assert overlay.mode == "RGB"


def test_review_package_is_fresh_only(tmp_path: Path) -> None:
    output_dir = tmp_path.resolve() / "authority-review"
    render_review_package(
        AUTHORITY_PATH,
        run_id="modularagent-review-fresh",
        output_dir=output_dir,
    )
    overlay_before = (output_dir / OVERLAY_FILENAME).read_bytes()

    with pytest.raises(ReviewPackageError, match="already exists"):
        render_review_package(
            AUTHORITY_PATH,
            run_id="modularagent-review-fresh",
            output_dir=output_dir,
        )

    assert (output_dir / OVERLAY_FILENAME).read_bytes() == overlay_before


def test_review_verifier_rejects_overlay_tampering(tmp_path: Path) -> None:
    output_dir = tmp_path.resolve() / "authority-review"
    render_review_package(
        AUTHORITY_PATH,
        run_id="modularagent-review-overlay-tamper",
        output_dir=output_dir,
    )
    overlay_path = output_dir / OVERLAY_FILENAME
    with Image.open(overlay_path) as loaded:
        changed = loaded.copy()
    changed.putpixel((0, 0), (255, 0, 0))
    changed.save(overlay_path)

    with pytest.raises(ReviewValidationError, match="overlay SHA-256"):
        validate_review_package(output_dir / MANIFEST_FILENAME)


def test_review_verifier_rejects_index_tampering(tmp_path: Path) -> None:
    output_dir = tmp_path.resolve() / "authority-review"
    render_review_package(
        AUTHORITY_PATH,
        run_id="modularagent-review-index-tamper",
        output_dir=output_dir,
    )
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["review_value"] = "changed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewValidationError, match="does not exactly project"):
        validate_review_package(manifest_path)


def test_project_local_review_output_is_run_bound(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    wrong = project_root / "examples" / "generated" / "runs" / "review-run" / "wrong"
    (project_root / "examples" / "generated").mkdir(parents=True)

    with pytest.raises(ReviewPackageError, match="must be exactly"):
        _canonical_output_directory(
            wrong,
            run_id="review-run",
            project_root=project_root,
        )


def test_frozen_authority_cannot_be_misrepresented_as_pending_review(tmp_path: Path) -> None:
    document = json.loads(AUTHORITY_PATH.read_text(encoding="utf-8"))
    changed = copy.deepcopy(document)
    changed["status"] = "FROZEN"
    changed["review"] = {
        "approved": True,
        "reviewed_by": "test-reviewer",
        "reviewed_at_utc": "2026-08-16T00:00:00Z",
        "method": "test fixture",
    }
    frozen_path = tmp_path / "frozen-authority.json"
    frozen_path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ReviewPackageError, match="DRAFT authority only"):
        render_review_package(
            frozen_path,
            run_id="frozen-review-test",
            output_dir=tmp_path.resolve() / "review",
        )


@pytest.mark.parametrize("run_id", ["short", "bad run id", "../escape"])
def test_invalid_run_ids_fail_before_output(tmp_path: Path, run_id: str) -> None:
    output_dir = tmp_path.resolve() / "review"

    with pytest.raises(ReviewPackageError, match="invalid run_id"):
        render_review_package(
            AUTHORITY_PATH,
            run_id=run_id,
            output_dir=output_dir,
        )

    assert not output_dir.exists()
