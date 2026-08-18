from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import validate_agent_vision as validator  # noqa: E402
from tests.test_agent_vision_task import _build, make_agent_vision_case  # noqa: E402
from tests.test_geometry_refinement import file_hash  # noqa: E402


def make_filled_response(
    tmp_path: Path, case: dict[str, Path]
) -> tuple[Path, Path, dict[str, dict]]:
    package_path, package = _build(case, "agent-vision")
    output_dir = package_path.parent
    template = json.loads((output_dir / "response-template.json").read_text("utf-8"))
    template["created_at_utc"] = "2026-08-16T00:00:00Z"
    template["agent"]["declared_model"] = "test-native-vision"
    template["agent"]["multi_pass_independence_attested"] = True

    by_type = {q["task_type"]: q for q in template["queries"]}
    by_type["STRUCTURE_GLOBAL"].update(
        {
            "observation_status": "OBSERVED",
            "structure": {
                "panels": [
                    {
                        "panel_id": "P001",
                        "bbox_source": {"x0": 4, "y0": 4, "x1": 176, "y1": 96},
                        "kind": "PANEL",
                        "reading_order_rank": 1,
                        "reading_flow_hint": "L2R",
                        "label_text_guess": None,
                    },
                    {
                        "panel_id": "P002",
                        "bbox_source": {"x0": 110, "y0": 45, "x1": 170, "y1": 95},
                        "kind": "DIAGRAM_GROUP",
                        "reading_order_rank": 2,
                        "reading_flow_hint": None,
                        "label_text_guess": "右下模块",
                    },
                ],
                "diagram_types": ["flowchart"],
            },
        }
    )
    by_type["CONFLICT_ARBITRATION"].update(
        {
            "observation_status": "OBSERVED",
            "conflict": {
                "decision": "SELECT",
                "selected_index": 0,
                "confidence_self_rating": "MEDIUM",
                "reason_code": None,
            },
        }
    )
    by_type["FORMULA_TRANSCRIPTION"].update(
        {
            "observation_status": "OBSERVED",
            "formula": {
                "samples": [
                    {"sample_index": 1, "latex": r"x = y"},
                    {"sample_index": 2, "latex": r"x=y"},
                    {"sample_index": 3, "latex": r"x = y "},
                ],
                "self_consistency": None,
            },
        }
    )
    by_type["MISS_SCAN"].update(
        {
            "observation_status": "OBSERVED",
            "miss_scan": {
                "contains_text": False,
                "text_hypothesis": None,
                "reason_code": "GRAPHICS_ONLY",
            },
        }
    )

    response_path = output_dir / "agent-vision-response.json"
    response_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return package_path, response_path, {"by_type": by_type, "package": package}


def test_valid_response_is_stamped_with_computed_self_consistency(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, response_path, _ctx = make_filled_response(tmp_path, case)
    stamped = validator.validate_response(
        package_path=package_path,
        response_path=response_path,
        output_path=response_path.parent / "agent-vision-document.json",
    )

    assert stamped["document_type"] == "AGENT_VISION_OBSERVATIONS"
    formula = next(q for q in stamped["queries"] if q["task_type"] == "FORMULA_TRANSCRIPTION")
    assert formula["formula"]["self_consistency"] == "SELF_CONSISTENT_K3"
    assert stamped["validation"]["task_package_sha256"] == file_hash(package_path)
    assert "FORMULA_SELF_CONSISTENCY_COMPUTED" in stamped["validation"]["checks_passed"]

    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "agent-vision.schema.json").read_text("utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(stamped)


def test_inconsistent_formula_samples_are_recorded_not_trusted(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, response_path, _ctx = make_filled_response(tmp_path, case)
    response = json.loads(response_path.read_text("utf-8"))
    formula = next(q for q in response["queries"] if q["task_type"] == "FORMULA_TRANSCRIPTION")
    formula["formula"]["samples"][2]["latex"] = r"\sum_{i=1}^{N} x_i"  # third sample disagrees
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

    stamped = validator.validate_response(
        package_path=package_path,
        response_path=response_path,
        output_path=response_path.parent / "agent-vision-document.json",
    )
    stamped_formula = next(
        q for q in stamped["queries"] if q["task_type"] == "FORMULA_TRANSCRIPTION"
    )
    assert stamped_formula["formula"]["self_consistency"] == "INCONSISTENT"
    # Samples survive for audit; the proposal channel itself stays closed.
    assert len(stamped_formula["formula"]["samples"]) == 3


def _duplicate_rank(response: dict) -> dict:
    structure = next(
        q for q in response["queries"] if q["task_type"] == "STRUCTURE_GLOBAL"
    )
    structure["structure"]["panels"][1]["reading_order_rank"] = 1
    return response


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda r: r["queries"][0]["structure"]["panels"][0]["bbox_source"].update(x1=9999),
            id="panel-escapes-canvas",
        ),
        pytest.param(
            lambda r: next(
                q for q in r["queries"] if q["task_type"] == "CONFLICT_ARBITRATION"
            )["conflict"].update(selected_index=9),
            id="selection-out-of-bounds",
        ),
        pytest.param(
            lambda r: r["queries"].pop(),
            id="missing-query",
        ),
        pytest.param(
            lambda r: r["queries"].append(copy.deepcopy(r["queries"][0])),
            id="duplicate-query",
        ),
        pytest.param(
            lambda r: next(
                q for q in r["queries"] if q["task_type"] == "FORMULA_TRANSCRIPTION"
            )["formula"]["samples"].pop(),
            id="too-few-formula-samples",
        ),
        pytest.param(
            lambda r: r.update(validation={"checks_passed": ["FAKE"]}),
            id="self-declared-validation",
        ),
        pytest.param(
            lambda r: r.update(document_type="AGENT_VISION_OBSERVATIONS"),
            id="wrong-document-type",
        ),
        pytest.param(
            lambda r: r["task_package"].update(sha256="A" * 64),
            id="stale-package-binding",
        ),
        pytest.param(
            lambda r: next(
                q for q in r["queries"] if q["task_type"] == "MISS_SCAN"
            )["miss_scan"].update(contains_text=False, text_hypothesis="幻觉文本"),
            id="hypothesis-without-text",
        ),
        pytest.param(
            lambda r: next(
                q for q in r["queries"] if q["task_type"] == "CONFLICT_ARBITRATION"
            )["conflict"].update(decision="REJECT_ALL", selected_index=0),
            id="reject-all-with-index",
        ),
        pytest.param(
            _duplicate_rank,
            id="duplicate-reading-rank",
        ),
    ],
)
def test_malformed_responses_fail_closed(tmp_path: Path, mutate) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, response_path, _ctx = make_filled_response(tmp_path, case)
    response = json.loads(response_path.read_text("utf-8"))
    mutate(response)
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(validator.ResponseRejected):
        validator.validate_response(
            package_path=package_path,
            response_path=response_path,
            output_path=response_path.parent / "agent-vision-document.json",
        )
    assert not (response_path.parent / "agent-vision-document.json").exists()


def test_not_observable_escape_hatch_is_accepted(tmp_path: Path) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, response_path, _ctx = make_filled_response(tmp_path, case)
    response = json.loads(response_path.read_text("utf-8"))
    for query in response["queries"]:
        query["observation_status"] = "NOT_OBSERVABLE"
        query["structure"] = None
        query["conflict"] = None
        query["formula"] = None
        query["miss_scan"] = None
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

    stamped = validator.validate_response(
        package_path=package_path,
        response_path=response_path,
        output_path=response_path.parent / "agent-vision-document.json",
    )
    assert all(q["observation_status"] == "NOT_OBSERVABLE" for q in stamped["queries"])
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "agent-vision.schema.json").read_text("utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(stamped)


def test_cli_reports_rejection_exit_code(tmp_path: Path, capsys) -> None:
    case = make_agent_vision_case(tmp_path)
    package_path, response_path, _ctx = make_filled_response(tmp_path, case)
    response = json.loads(response_path.read_text("utf-8"))
    response["queries"][0]["structure"]["panels"][0]["bbox_source"]["x0"] = 500
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = validator.main(
        [
            "--task-package",
            str(package_path),
            "--response",
            str(response_path),
            "--output",
            str(response_path.parent / "agent-vision-document.json"),
        ]
    )
    assert exit_code == validator.EXIT_CONTRACT_REJECTED
    assert "AGENT_VISION_RESPONSE_REJECTED" in capsys.readouterr().err
