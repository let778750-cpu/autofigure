"""Apply Figure Spec-bound text-frame hygiene to a closed editable PPTX.

The managed PowerPoint bridge exposes geometry and text styling but not text
frame insets.  PowerPoint therefore falls back to 7.2 pt horizontal and 3.6 pt
vertical margins, which can create false overflows and visible displacement.
This deterministic OOXML adapter sets only the named text/formula shapes from
Figure Spec v4 to zero insets and restores the declared bold/regular weight
and point-valued font size.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from lxml import etree


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"p": P_NS, "a": A_NS}
SLIDE_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")


class TextFrameHygieneError(RuntimeError):
    """Raised when a closed PPTX cannot be changed without ambiguity."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TextFrameHygieneError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TextFrameHygieneError(f"{label} must be one JSON object")
    return value


def _target_styles(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if spec.get("schema_version") != "4.0":
        raise TextFrameHygieneError("text-frame hygiene requires Figure Spec 4.0")
    targets: dict[str, dict[str, Any]] = {}
    for element in spec.get("elements", []):
        if not isinstance(element, Mapping) or element.get("type") not in {"text", "formula"}:
            continue
        element_id = str(element.get("id", ""))
        if not element_id or element_id in targets:
            raise TextFrameHygieneError(f"invalid or duplicate text element id: {element_id!r}")
        raw_style = (
            element.get("formula_style")
            if element.get("type") == "formula"
            else element.get("text_style")
        )
        style = raw_style if isinstance(raw_style, Mapping) else {}
        font_size_pt = style.get("font_size_pt")
        targets[element_id] = {
            "bold": str(element.get("font_weight", "regular"))
            in {"bold", "semibold"},
            "font_size_hundredths_pt": (
                round(float(font_size_pt) * 100)
                if isinstance(font_size_pt, (int, float))
                and not isinstance(font_size_pt, bool)
                else None
            ),
        }
    if not targets:
        raise TextFrameHygieneError("Figure Spec contains no text or formula elements")
    return targets


def _rewrite_slide(
    data: bytes, targets: Mapping[str, Mapping[str, Any]]
) -> tuple[bytes, list[str]]:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        raise TextFrameHygieneError(f"cannot parse slide XML: {exc}") from exc
    changed: list[str] = []
    for shape in root.xpath(".//p:sp", namespaces=NS):
        name_nodes = shape.xpath("./p:nvSpPr/p:cNvPr/@name", namespaces=NS)
        if len(name_nodes) != 1 or str(name_nodes[0]) not in targets:
            continue
        shape_name = str(name_nodes[0])
        body_nodes = shape.xpath("./p:txBody/a:bodyPr", namespaces=NS)
        if len(body_nodes) != 1:
            raise TextFrameHygieneError(f"{shape_name} lacks exactly one a:bodyPr")
        body = body_nodes[0]
        for attribute in ("lIns", "rIns", "tIns", "bIns"):
            body.set(attribute, "0")
        target = targets[shape_name]
        bold = "1" if target["bold"] else "0"
        font_size = target.get("font_size_hundredths_pt")
        properties = shape.xpath(
            "./p:txBody//a:rPr | ./p:txBody//a:defRPr | ./p:txBody//a:endParaRPr",
            namespaces=NS,
        )
        if not properties:
            raise TextFrameHygieneError(f"{shape_name} lacks text run properties")
        for prop in properties:
            prop.set("b", bold)
            if font_size is not None:
                prop.set("sz", str(font_size))
        changed.append(shape_name)
    return etree.tostring(root, xml_declaration=False, encoding="UTF-8"), changed


def apply_hygiene(
    input_path: Path,
    spec_path: Path,
    output_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    source = input_path.resolve(strict=True)
    spec_source = spec_path.resolve(strict=True)
    if source.suffix.casefold() != ".pptx" or output_path.suffix.casefold() != ".pptx":
        raise TextFrameHygieneError("input and output must be .pptx files")
    spec = _load_object(spec_source, "Figure Spec")
    targets = _target_styles(spec)
    destination = output_path.resolve()
    receipt_destination = receipt_path.resolve()
    if destination == source:
        raise TextFrameHygieneError("input PPTX is read-only evidence; output must be a new path")
    if destination.exists() or receipt_destination.exists():
        raise TextFrameHygieneError("output and receipt paths must be fresh")
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt_destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    changed_names: list[str] = []
    changed_parts: list[str] = []
    try:
        with zipfile.ZipFile(source, "r") as input_zip, zipfile.ZipFile(
            temporary, "w"
        ) as output_zip:
            for info in input_zip.infolist():
                data = input_zip.read(info.filename)
                if SLIDE_RE.fullmatch(info.filename):
                    rewritten, changed = _rewrite_slide(data, targets)
                    if changed:
                        data = rewritten
                        changed_parts.append(info.filename)
                        changed_names.extend(changed)
                output_zip.writestr(info, data)
        observed = set(changed_names)
        expected = set(targets)
        if len(changed_names) != len(observed) or observed != expected:
            missing = sorted(expected - observed)
            duplicate = sorted(name for name in observed if changed_names.count(name) > 1)
            raise TextFrameHygieneError(
                f"text-shape inventory mismatch: missing={missing}, duplicate={duplicate}"
            )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    receipt = {
        "schema_version": "1.0",
        "document_type": "POWERPOINT_TEXT_FRAME_HYGIENE_RECEIPT",
        "status": "PASS",
        "source_pptx": str(source),
        "source_sha256": _sha256(source),
        "figure_spec": str(spec_source),
        "figure_spec_sha256": _sha256(spec_source),
        "output_pptx": str(destination),
        "output_sha256": _sha256(destination),
        "changed_slide_parts": sorted(changed_parts),
        "changed_shape_names": sorted(changed_names),
        "changed_shape_count": len(changed_names),
        "text_frame_policy": {
            "insets_emu": {"left": 0, "right": 0, "top": 0, "bottom": 0},
            "font_weight_source": "figure_spec.font_weight",
            "font_size_source": "figure_spec.text_style/formula_style.font_size_pt",
        },
    }
    temporary_receipt = receipt_destination.with_name(f".{receipt_destination.name}.tmp")
    temporary_receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_receipt, receipt_destination)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        receipt = apply_hygiene(args.input, args.spec, args.output, args.receipt)
    except (OSError, zipfile.BadZipFile, TextFrameHygieneError) as exc:
        print(f"POWERPOINT_TEXT_FRAME_HYGIENE_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "changed_shape_count": receipt["changed_shape_count"],
                "output": receipt["output_pptx"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
