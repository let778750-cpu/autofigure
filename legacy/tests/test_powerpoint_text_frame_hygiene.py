from __future__ import annotations

import json
import zipfile
from pathlib import Path

from lxml import etree

from tools.powerpoint_text_frame_hygiene import A_NS, NS, apply_hygiene


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    pptx = tmp_path / "input.pptx"
    slide = b"""<p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"text.label\"/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr b=\"1\"/><a:t>Label</a:t></a:r><a:endParaRPr b=\"1\"/></a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:cNvPr id=\"3\" name=\"formula.x\"/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr/><a:t>x</a:t></a:r><a:endParaRPr/></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"""
    with zipfile.ZipFile(pptx, "w") as package:
        package.writestr("ppt/slides/slide1.xml", slide)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "schema_version": "4.0",
                "elements": [
                    {
                        "id": "text.label",
                        "type": "text",
                        "font_weight": "regular",
                        "text_style": {"font_size_pt": 13.5},
                    },
                    {
                        "id": "formula.x",
                        "type": "formula",
                        "formula_style": {"font_size_pt": 12},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return pptx, spec


def test_hygiene_sets_zero_insets_and_declared_weight(tmp_path: Path) -> None:
    source, spec = _fixture(tmp_path)
    output, receipt = tmp_path / "output.pptx", tmp_path / "receipt.json"

    result = apply_hygiene(source, spec, output, receipt)

    assert result["changed_shape_count"] == 2
    with zipfile.ZipFile(output) as package:
        root = etree.fromstring(package.read("ppt/slides/slide1.xml"))
    for body in root.xpath(".//a:bodyPr", namespaces=NS):
        assert {key: body.get(key) for key in ("lIns", "rIns", "tIns", "bIns")} == {
            "lIns": "0",
            "rIns": "0",
            "tIns": "0",
            "bIns": "0",
        }
    bold_values = root.xpath(".//a:rPr/@b | .//a:endParaRPr/@b", namespaces=NS)
    assert bold_values == ["0", "0", "0", "0"]
    size_values = root.xpath(".//a:rPr/@sz | .//a:endParaRPr/@sz", namespaces=NS)
    assert size_values == ["1350", "1350", "1200", "1200"]
    assert etree.QName(root.xpath(".//a:bodyPr", namespaces=NS)[0]).namespace == A_NS
