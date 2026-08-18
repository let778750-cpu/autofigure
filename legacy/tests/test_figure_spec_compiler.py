from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from compile_figure_spec import (  # noqa: E402
    FigureSpecCompileError,
    _latex_sequence_terms,
    _materialize_elements,
    compile_figure_spec,
)
from create_canvas_pptx import create_blank_canvas_pptx  # noqa: E402
from finalize_perception_review import finalize_review, sha256_file  # noqa: E402
from powerpoint_native_math import compile_formula  # noqa: E402
from prepare_authoritative_perception_review import (  # noqa: E402
    prepare_authoritative_review,
)
from tests.test_perception_review import build_raw_manifest  # noqa: E402


AUTHORITY_PATH = ROOT / "examples" / "modularagent.source-authority.json"
SOURCE_PATH = (
    ROOT
    / "examples"
    / "01_2026_CVPR_2026_ModularAgent_A_Task-Aware_Modular_Framework_for_Join.png"
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_compiler_closes_authority_review_canvas_and_all_formula_receipts(
    tmp_path: Path,
) -> None:
    authority = json.loads(AUTHORITY_PATH.read_text(encoding="utf-8"))
    manifest = build_raw_manifest()
    manifest["run_id"] = "figure-compiler-test-001"
    manifest["source"]["path"] = str(SOURCE_PATH)
    manifest["source"]["sha256"] = authority["source"]["sha256"]
    ordinary, formula_candidate = manifest["text_candidates"]
    ordinary.update(
        {
            "text": "MLLM",
            "normalized_text": "mllm",
            "bbox_source": {"x": 216.0, "y": 256.0, "w": 65.0, "h": 21.0},
            "review_flags": [],
        }
    )
    formula_candidate.update(
        {
            "text": "π",
            "normalized_text": "π",
            "bbox_source": {"x": 1022.0, "y": 416.0, "w": 18.0, "h": 17.0},
            "review_flags": [],
        }
    )
    manifest_path = tmp_path / "perception-manifest.json"
    _write_json(manifest_path, manifest)
    decisions_path = tmp_path / "perception-review-decisions.json"
    prepare_authoritative_review(manifest_path, AUTHORITY_PATH, decisions_path)
    review_path = tmp_path / "perception-review-receipt.json"
    receipt, exit_code = finalize_review(manifest_path, decisions_path, review_path)
    assert exit_code == 0
    assert receipt["status"] == "PERCEPTION_REVIEW_PASS"

    canvas_path = tmp_path / "canvas.pptx"
    canvas = create_blank_canvas_pptx(SOURCE_PATH, canvas_path)

    formula_items = [item for item in authority["items"] if item["kind"] == "FORMULA"]
    elements = []
    bindings = []
    for index, item in enumerate(formula_items, start=1):
        receipt_path = tmp_path / f"{item['subject_id']}.converter.json"
        converter = compile_formula(
            item["subject_id"], item["canonical_latex"], item["formula_mode"]
        )
        _write_json(receipt_path, converter)
        element_id = f"el.{item['subject_id']}"
        element = {
                "id": element_id,
                "type": "formula",
                "parent_id": None,
                "bbox": item["bbox_source"],
                "z_index": index,
                "semantic_role": item["subject_id"],
                "source_evidence": [item["source_evidence"][0]["kind"]],
                "disposition": "CONFIRMED",
                "confidence": 1.0,
                "uncertainty_px": 0,
                "strategy": "native_editable",
                "allowed_overlap": [],
                "status": "pending",
                "formula_id": item["subject_id"],
                "formula_style": {"font_size_px": 20, "color": "#000000", "margin_px": 0, "rotation_deg": 0},
                "authority_item_id": item["authority_item_id"],
            }
        if index == 1:
            element["type"] = "text"
            element.pop("formula_id")
            element.pop("formula_style")
            element["content_runs"] = [{"kind": "math", "formula_id": item["subject_id"]}]
            element["text_style"] = {
                "font_family": "Arial",
                "font_size_px": 20,
                "margin_px": 0,
                "wrap": False,
            }
            element["criticality"] = "critical"
            element["perception_candidate_ids"] = []
        elements.append(element)
        bindings.append(
            {
                "authority_item_id": item["authority_item_id"],
                "formula_id": item["subject_id"],
                "element_id": element_id,
                "converter_receipt_path": str(receipt_path),
                "converter_receipt_sha256": sha256_file(receipt_path),
            }
        )

    scene = {
        "schema_version": "1.0.0",
        "document_type": "FIGURE_SCENE_DECLARATION",
        "mode": "reconstruct_1to1",
        "source_authority": {
            "path": str(AUTHORITY_PATH),
            "sha256": sha256_file(AUTHORITY_PATH),
        },
        "perception_review": {
            "path": str(review_path),
            "sha256": sha256_file(review_path),
        },
        "canvas": {
            "path": str(canvas_path),
            "sha256": canvas["output_pptx_sha256"],
            "width_px": 1429,
            "height_px": 627,
            "background": "#FFFFFF",
            "background_evidence": "measured_reference",
        },
        "measurement_dpi": 96,
        "elements": elements,
        "edges": [],
        "formula_bindings": bindings,
        "uncertainties": [],
    }
    scene_path = tmp_path / "scene-declaration.json"
    _write_json(scene_path, scene)
    output_path = tmp_path / "figure-spec.json"

    spec = compile_figure_spec(scene_path, output_path)

    assert output_path.is_file()
    assert len(spec["formulas"]) == len(formula_items) == 11
    assert spec["authority"]["sha256"] == sha256_file(AUTHORITY_PATH)
    assert spec["perception"]["review_receipt_sha256"] == sha256_file(review_path)
    assert spec["canvas"]["pptx_sha256"] == sha256_file(canvas_path)
    assert all(
        formula["render_kind"] == "native_office_math"
        and formula["fallback_policy"] == "strict_no_raster_no_svg"
        for formula in spec["formulas"]
    )


def test_compiler_rejects_output_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "figure-spec.json"
    output.write_text("{}", encoding="utf-8")
    missing_scene = tmp_path / "missing-scene.json"

    with pytest.raises(FigureSpecCompileError, match="output already exists"):
        compile_figure_spec(missing_scene, output)


def test_materialize_elements_preserves_local_order_and_puts_children_above_parents() -> None:
    scene = {
        "elements": [
            {"id": "root", "parent_id": None, "z_index": 0},
            {"id": "panel", "parent_id": "root", "z_index": 7},
            {"id": "label", "parent_id": "panel", "z_index": 2},
        ]
    }

    root, panel, label = _materialize_elements(scene)

    assert [root["scene_z_index"], panel["scene_z_index"], label["scene_z_index"]] == [0, 7, 2]
    assert root["z_index"] < panel["z_index"] < label["z_index"]


def test_latex_sequence_terms_preserve_nested_subscripts_and_commands() -> None:
    assert _latex_sequence_terms(
        r"(z_t^\tau,z_{t+1}^\tau,\ldots,z_{t+h}^\tau)"
    ) == [r"z_t^\tau", r"z_{t+1}^\tau", r"\ldots", r"z_{t+h}^\tau"]


@pytest.mark.parametrize("expression", [r"z_t", r"(x)", r"(x,,y)", r"(x,{y)"])
def test_latex_sequence_terms_reject_non_sequences(expression: str) -> None:
    with pytest.raises(FigureSpecCompileError):
        _latex_sequence_terms(expression)
