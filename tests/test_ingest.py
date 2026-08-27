"""ingest 命令面的 case-neutral 测试(candidate-origin 枚举与内联矢量组待遇)。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from tools.core.contracts import CANDIDATE_ORIGINS
from tools.ingest import main as ingest_main
from tools.source_gate import evaluate_source_gate


def test_candidate_origin_choices_come_from_contracts(capsys: pytest.CaptureFixture):
    with pytest.raises(SystemExit) as excinfo:
        ingest_main(["case-x", "--candidate-origin", "bogus"])
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    for origin in CANDIDATE_ORIGINS:
        assert origin in stderr


def test_inline_atomic_vector_group_passes_source_gate(tmp_path: Path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (160, 100), "white").save(reference)
    reference_sha256 = hashlib.sha256(reference.read_bytes()).hexdigest()
    candidate = tmp_path / "candidate.svg"
    candidate.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" '
        'viewBox="0 0 160 100">'
        '<g id="atomic:icon-a">'
        '<path id="icon-a-body" d="M10 10 L40 10 L40 40 Z" fill="#C83C32"/>'
        '<path id="icon-a-core" d="M14 14 L36 14 L36 36 Z" fill="#3C82C8"/>'
        "</g></svg>",
        encoding="utf-8",
    )
    report = evaluate_source_gate(
        candidate,
        reference_path=reference,
        input_route="reference-only",
        candidate_role="reconstruction-candidate",
        expected_reference_sha256=reference_sha256,
        expected_canvas=(160, 100),
        semantic_metadata={
            "semantic_schema_version": "4.0.0",
            "reference_sha256": reference_sha256,
            "object_inventory_sha256": "a" * 64,
            "stable_element_ids": True,
            "relations_exhaustive": True,
            "case": "case-01",
        },
        expected_case="case-01",
        expected_inventory_sha256="a" * 64,
        seed_gate_status="forbidden",
    )
    assert report["decision"] == "accept"
    assert report["blockers"] == []
