from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.build_native_math_plan import NativeMathPlanError, build_plan


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    receipt = tmp_path / "formula.x.converter.json"
    _write(receipt, {"status": "PASS", "formula_id": "formula.x"})
    receipt_hash = hashlib.sha256(receipt.read_bytes()).hexdigest()
    spec = tmp_path / "figure-spec.json"
    _write(
        spec,
        {
            "schema_version": "4.0",
            "measurement_dpi": 96,
            "elements": [
                {
                    "id": "formula.x",
                    "type": "formula",
                    "formula_id": "formula.x",
                    "formula_style": {"font_size_px": 16, "color": "#ffffff"},
                }
            ],
            "formulas": [
                {
                    "id": "formula.x",
                    "element_id": "formula.x",
                    "converter_receipt_path": str(receipt),
                    "converter_receipt_sha256": receipt_hash,
                }
            ],
        },
    )
    return spec, tmp_path / "native-math-plan.json"


def test_build_plan_binds_hash_style_and_same_named_placeholder(tmp_path: Path) -> None:
    spec, output = _fixture(tmp_path)
    operation = build_plan(spec, output)["operations"][0]
    assert operation["placeholder_name"] == "formula.x"
    assert operation["target_font_size_pt"] == 12.0
    assert operation["target_font_color"] == "#FFFFFF"
    assert len(operation["receipt_sha256"]) == 64


def test_build_plan_rejects_implicit_formula_color(tmp_path: Path) -> None:
    spec, output = _fixture(tmp_path)
    payload = json.loads(spec.read_text(encoding="utf-8"))
    payload["elements"][0]["formula_style"].pop("color")
    _write(spec, payload)
    with pytest.raises(NativeMathPlanError, match="explicit #RRGGBB color"):
        build_plan(spec, output)
