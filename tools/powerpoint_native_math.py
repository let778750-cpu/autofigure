#!/usr/bin/env python3
"""Compile canonical LaTeX and inject native editable Office Math into PPTX.

This module deliberately operates on a *closed* PPTX package.  It never uses
clipboard, SendKeys, OLE equation objects, SVG, or raster formula pictures.
The active PowerPoint representation is DrawingML ``a14:m`` containing OMML.
"""

from __future__ import annotations

import argparse
import base64
import copy
import ctypes
import functools
import hashlib
import importlib.metadata
import json
import math
import os
import posixpath
import re
import secrets
import subprocess
import tempfile
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from latex2mathml.converter import convert as latex_to_mathml
from lxml import etree
from PIL import Image, ImageChops

try:
    from output_policy import OutputPolicyError, resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.powerpoint_native_math
    try:
        from .output_policy import OutputPolicyError, resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the project root.
        from tools.output_policy import OutputPolicyError, resolve_output_path


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
A14_NS = "http://schemas.microsoft.com/office/drawing/2010/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_NS = "http://www.w3.org/XML/1998/namespace"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

NS = {"p": P_NS, "a": A_NS, "a14": A14_NS, "m": M_NS, "mc": MC_NS, "r": R_NS}
META_PREFIX = "AI_AUTOFIGURE_NATIVE_MATH_V1:"
FALLBACK_PREFIX = "AI_AUTOFIGURE_MATH_FALLBACK_ONLY_V1:"
RECEIPT_TYPE = "NATIVE_OFFICE_MATH_CONVERTER_RECEIPT"
REPORT_TYPE = "NATIVE_OFFICE_MATH_INJECTION_REPORT"
AUDIT_TYPE = "NATIVE_OFFICE_MATH_AUDIT"
ROUNDTRIP_RECEIPT_TYPE = "POWERPOINT_NATIVE_MATH_ROUNDTRIP_RECEIPT"
ROUNDTRIP_RECEIPT_VERSION = "2.0"
SEMANTIC_OMML_PROFILE = "office-math-semantic-v2"
FINALIZATION_CHALLENGE_BYTES = 32


def _validated_output(path: str | os.PathLike[str]) -> Path:
    try:
        return resolve_output_path(path)
    except OutputPolicyError as exc:
        raise NativeMathError(str(exc)) from exc

# The converter does not execute TeX, but strict mode also rejects constructs
# that read files, define macros, silently lose numbering, or create links.
FORBIDDEN_COMMANDS = {
    "def",
    "edef",
    "gdef",
    "xdef",
    "input",
    "include",
    "includeonly",
    "write",
    "write18",
    "openin",
    "openout",
    "read",
    "usepackage",
    "documentclass",
    "newcommand",
    "renewcommand",
    "providecommand",
    "DeclareMathOperator",
    "href",
    "url",
    "htmlClass",
    "htmlId",
    "htmlStyle",
    "tag",
    "label",
    "ref",
    "eqref",
    "notag",
    "nonumber",
    "operatorname",
    "mathop",
    "dfrac",
    "tfrac",
    "displaystyle",
    "textstyle",
    "scriptstyle",
    "scriptscriptstyle",
}
COMMAND_RE = re.compile(r"\\([A-Za-z]+|.)")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SINGLE_MATHRM_RE = re.compile(r"\\mathrm\{([^{}\\])\}")
SLIDE_PART_RE = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")


class NativeMathError(RuntimeError):
    """A deterministic native-math contract failure."""


def _qn(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_xml(element: etree._Element) -> bytes:
    # Exclusive C14N prevents unrelated slide-level namespace declarations from
    # changing a formula hash after insertion into PresentationML.
    return etree.tostring(element, method="c14n", exclusive=True, with_comments=False)


def _mathematical_alphanumeric(character: str) -> tuple[str, str, str] | None:
    """Return (base, script, style) for a styled mathematical code point.

    NFKC is intentionally used only after a Unicode-name allowlist identifies
    a Mathematical Alphanumeric Symbol (or one of that block's legacy holes).
    Compatibility characters such as superscripts, circled digits, fractions,
    and squared units therefore remain distinct.
    """
    name = unicodedata.name(character, "")
    descriptor = ""
    if name.startswith("MATHEMATICAL "):
        descriptor = name.removeprefix("MATHEMATICAL ")
    elif name.startswith("DOUBLE-STRUCK "):
        descriptor = name
    elif name.startswith("SCRIPT "):
        descriptor = name
    elif name.startswith("BLACK-LETTER "):
        descriptor = name.replace("BLACK-LETTER", "FRAKTUR", 1)
    elif name == "PLANCK CONSTANT":
        descriptor = "ITALIC SMALL H"
    else:
        return None
    base = unicodedata.normalize("NFKC", character)
    if len(base) != 1:
        return None
    variants = (
        ("SANS-SERIF BOLD ITALIC ", "sans-serif", "bi"),
        ("SANS-SERIF BOLD ", "sans-serif", "b"),
        ("SANS-SERIF ITALIC ", "sans-serif", "i"),
        ("SANS-SERIF ", "sans-serif", "p"),
        ("BOLD SCRIPT ", "script", "b"),
        ("SCRIPT ", "script", "p"),
        ("BOLD FRAKTUR ", "fraktur", "b"),
        ("FRAKTUR ", "fraktur", "p"),
        ("DOUBLE-STRUCK ", "double-struck", "p"),
        ("BOLD ITALIC ", "roman", "bi"),
        ("BOLD ", "roman", "b"),
        ("ITALIC ", "roman", "i"),
        ("MONOSPACE ", "monospace", "p"),
    )
    for prefix, script, style in variants:
        if descriptor.startswith(prefix):
            return base, script, style
    return None


def _omml_boolean(node: etree._Element | None) -> bool:
    if node is None:
        return False
    value = node.get(_qn(M_NS, "val"))
    return value is None or value.casefold() not in {"0", "false", "off"}


def _semantic_run_tokens(run: etree._Element) -> list[dict[str, Any]] | None:
    if run.attrib:
        return None
    allowed = {_qn(M_NS, "rPr"), _qn(M_NS, "t"), _qn(A_NS, "rPr")}
    if any(child.tag not in allowed for child in run):
        return None
    text_nodes = [child for child in run if child.tag == _qn(M_NS, "t")]
    math_properties = [child for child in run if child.tag == _qn(M_NS, "rPr")]
    if len(text_nodes) != 1 or len(math_properties) > 1 or text_nodes[0].attrib:
        return None
    properties = math_properties[0] if math_properties else None
    known_property_tags = {
        _qn(M_NS, "scr"),
        _qn(M_NS, "sty"),
        _qn(M_NS, "nor"),
        _qn(M_NS, "lit"),
    }
    if properties is not None and (
        properties.attrib or any(child.tag not in known_property_tags for child in properties)
    ):
        return None
    script_node = None if properties is None else properties.find("m:scr", namespaces=NS)
    style_node = None if properties is None else properties.find("m:sty", namespaces=NS)
    normal_node = None if properties is None else properties.find("m:nor", namespaces=NS)
    literal_node = None if properties is None else properties.find("m:lit", namespaces=NS)
    declared_script = "roman" if script_node is None else script_node.get(_qn(M_NS, "val"), "roman")
    declared_style = "i" if style_node is None else style_node.get(_qn(M_NS, "val"), "i")
    normal = _omml_boolean(normal_node)
    literal = _omml_boolean(literal_node)
    tokens: list[dict[str, Any]] = []
    for character in text_nodes[0].text or "":
        encoded = _mathematical_alphanumeric(character)
        if encoded is not None:
            base, encoded_script, encoded_style = encoded
            declared_nondefault = script_node is not None or style_node is not None
            # PowerPoint commonly adds ``m:sty m:val="p"`` to a glyph whose
            # Unicode code point already carries its mathematical alphabet
            # (for example U+2113 SCRIPT SMALL L).  With no explicit script,
            # normal, or literal flag this is a neutral "do not add another
            # style" marker, not a request to turn the glyph into roman text.
            powerpoint_neutral_style = (
                script_node is None
                and style_node is not None
                and declared_style == "p"
                and normal_node is None
                and literal_node is None
            )
            if (
                declared_nondefault
                and not powerpoint_neutral_style
                and (declared_script, declared_style)
                != (encoded_script, encoded_style)
            ):
                tokens.append(
                    {
                        "kind": "math-character-conflict",
                        "base": base,
                        "encoded_script": encoded_script,
                        "encoded_style": encoded_style,
                        "declared_script": declared_script,
                        "declared_style": declared_style,
                        "normal": normal,
                        "literal": literal,
                    }
                )
                continue
            script, style = encoded_script, encoded_style
        else:
            base = character
            unicode_name = unicodedata.name(character, "")
            is_math_letter = "LATIN" in unicode_name or "GREEK" in unicode_name
            script = declared_script if is_math_letter else "literal"
            style = declared_style if is_math_letter else "literal"
        tokens.append(
            {
                "kind": "math-character",
                "base": base,
                "script": script,
                "style": style,
                "normal": normal,
                "literal": literal,
            }
        )
    return tokens


def _semantic_omml(element: etree._Element) -> bytes:
    """Build a versioned token AST for semantics-preserving Office rewrites."""
    root = copy.deepcopy(element)
    for node in list(root.xpath(".//a:rPr|.//m:ctrlPr", namespaces=NS)):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for fraction_properties in root.xpath(".//m:fPr", namespaces=NS):
        for type_node in list(fraction_properties.findall("m:type", namespaces=NS)):
            value = type_node.get(_qn(M_NS, "val"))
            if value in {None, "bar"}:
                fraction_properties.remove(type_node)
    for node in reversed(list(root.iter())):
        if node is root:
            continue
        name = etree.QName(node)
        if (
            name.namespace == M_NS
            and name.localname.endswith("Pr")
            and not node.attrib
            and len(node) == 0
            and not (node.text or "").strip()
        ):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    def ast(node: etree._Element) -> dict[str, Any]:
        name = etree.QName(node)
        children: list[dict[str, Any]] = []
        for child in node:
            if child.tag == _qn(M_NS, "r"):
                tokens = _semantic_run_tokens(child)
                if tokens is not None:
                    children.extend(tokens)
                    continue
            children.append(ast(child))
        result: dict[str, Any] = {
            "kind": "element",
            "name": [name.namespace or "", name.localname],
            "attributes": sorted(
                ([etree.QName(key).namespace or "", etree.QName(key).localname, value])
                for key, value in node.attrib.items()
            ),
            "children": children,
        }
        if (node.text or "").strip():
            result["text"] = node.text
        return result

    payload = {
        "profile": SEMANTIC_OMML_PROFILE,
        "root": ast(root),
    }
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _semantic_omml_sha256(element: etree._Element) -> str:
    return _sha256_bytes(_semantic_omml(element))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path = _validated_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, pretty: bool = False) -> None:
    if pretty:
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    else:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        ) + b"\n"
    _atomic_write_bytes(path, data)


def _atomic_write_json_fresh(
    path: Path, payload: Mapping[str, Any], *, pretty: bool = False
) -> None:
    """Commit a new JSON file atomically without ever replacing a raced destination."""
    if pretty:
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    else:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        ) + b"\n"
    path = _validated_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise NativeMathError(
                f"fresh final audit output already exists: {path}"
            ) from exc
        temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NativeMathError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def discover_mml2omml_xsl(explicit: str | os.PathLike[str] | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    configured = os.environ.get("AI_AUTOFIGURE_MML2OMML_XSL")
    if configured:
        candidates.append(Path(configured).expanduser())
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for root in program_files:
        if root:
            candidates.append(Path(root) / "Microsoft Office" / "root" / "Office16" / "MML2OMML.XSL")
    candidates.append(Path(r"C:\Program Files\Microsoft Office\root\Office16\MML2OMML.XSL"))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise NativeMathError(
        "Office MML2OMML.XSL was not found; native Office Math conversion is INCONCLUSIVE."
    )


@functools.lru_cache(maxsize=4)
def _load_mml2omml_transform(xsl_path: str) -> etree.XSLT:
    """Parse the large Office stylesheet once per resolved path and process."""
    return etree.XSLT(etree.parse(xsl_path))


def _latex_expression(canonical_latex: str) -> str:
    value = canonical_latex.strip()
    pairs = (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]"))
    for opening, closing in pairs:
        if value.startswith(opening) and value.endswith(closing) and len(value) > len(opening) + len(closing):
            return value[len(opening) : -len(closing)].strip()
    return value


def _latex_for_converter(expression: str) -> str:
    # latex2mathml 3.81.0 drops mathvariant=normal for a one-character
    # \mathrm{...} group.  Adding an empty group makes the intended upright
    # style explicit without changing the canonical LaTeX or rendered meaning.
    return SINGLE_MATHRM_RE.sub(lambda match: rf"\mathrm{{{match.group(1)}{{}}}}", expression)


def validate_latex_source(canonical_latex: str) -> None:
    if not isinstance(canonical_latex, str) or not canonical_latex.strip():
        raise NativeMathError("canonical_latex must be a non-empty string")
    if len(canonical_latex) > 10_000:
        raise NativeMathError("canonical_latex exceeds the strict 10,000-character limit")
    if CONTROL_RE.search(canonical_latex):
        raise NativeMathError("canonical_latex contains forbidden control characters")
    commands = {match.group(1) for match in COMMAND_RE.finditer(canonical_latex)}
    forbidden = sorted(command for command in commands if command in FORBIDDEN_COMMANDS)
    if forbidden:
        raise NativeMathError(f"forbidden or lossy LaTeX commands: {', '.join(forbidden)}")


def _parse_xml(value: str | bytes, *, label: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    try:
        return etree.fromstring(value.encode("utf-8") if isinstance(value, str) else value, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise NativeMathError(f"invalid {label} XML: {exc}") from exc


def compile_formula(
    formula_id: str,
    canonical_latex: str,
    mode: str,
    xsl_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Compile one frozen formula and return its hash-bound PASS receipt."""
    if not formula_id or not isinstance(formula_id, str):
        raise NativeMathError("formula_id must be a non-empty string")
    if mode not in {"inline", "display"}:
        raise NativeMathError("mode must be inline or display")
    validate_latex_source(canonical_latex)
    expression = _latex_expression(canonical_latex)
    converter_expression = _latex_for_converter(expression)
    try:
        mathml_text = latex_to_mathml(
            converter_expression, display="block" if mode == "display" else "inline"
        )
    except Exception as exc:  # latex2mathml exposes several parser exception classes.
        raise NativeMathError(f"LaTeX to MathML conversion failed: {exc}") from exc
    mathml = _parse_xml(mathml_text, label="MathML")
    unresolved = sorted({text for text in mathml.itertext() if "\\" in text})
    if unresolved:
        raise NativeMathError(f"unsupported LaTeX survived conversion literally: {unresolved!r}")

    xsl = discover_mml2omml_xsl(xsl_path)
    try:
        transform = _load_mml2omml_transform(str(xsl.resolve()))
        transformed = transform(mathml)
    except (etree.XMLSyntaxError, etree.XSLTError, OSError) as exc:
        raise NativeMathError(f"MathML to OMML conversion failed: {exc}") from exc
    omath = transformed.getroot()
    actual = None if omath is None else etree.QName(omath).localname
    if omath is None or etree.QName(omath).namespace != M_NS:
        raise NativeMathError(f"MML2OMML did not produce Office Math (got {actual!r})")
    if mode == "display":
        if omath.tag == _qn(M_NS, "oMathPara"):
            final_omml = copy.deepcopy(omath)
        elif omath.tag == _qn(M_NS, "oMath"):
            final_omml = etree.Element(_qn(M_NS, "oMathPara"), nsmap={"m": M_NS})
            final_omml.append(copy.deepcopy(omath))
        else:
            raise NativeMathError(f"MML2OMML produced an invalid display root: {actual!r}")
        omml_root = "m:oMathPara"
    else:
        if omath.tag != _qn(M_NS, "oMath"):
            raise NativeMathError(f"MML2OMML produced an invalid inline root: {actual!r}")
        final_omml = copy.deepcopy(omath)
        omml_root = "m:oMath"

    mathml_bytes = _canonical_xml(mathml)
    omml_bytes = _canonical_xml(final_omml)
    return {
        "document_type": RECEIPT_TYPE,
        "schema_version": "1.1",
        "status": "PASS",
        "formula_id": formula_id,
        "mode": mode,
        "canonical_latex": canonical_latex,
        "latex_sha256": _sha256_text(canonical_latex),
        "mathml_sha256": _sha256_bytes(mathml_bytes),
        "omml_sha256": _sha256_bytes(omml_bytes),
        "semantic_omml_profile": SEMANTIC_OMML_PROFILE,
        "semantic_omml_sha256": _semantic_omml_sha256(final_omml),
        "converter": {
            "name": "latex2mathml+office-mml2omml-xsl",
            "latex2mathml_version": importlib.metadata.version("latex2mathml"),
            "lxml_version": importlib.metadata.version("lxml"),
            "latex_normalization": "single-character-mathrm-empty-group-v1",
            "xsl_path": str(xsl),
            "xsl_sha256": _sha256_file(xsl),
        },
        "native_target": {
            "kind": "office_math",
            "wrapper": "a14:m",
            "omml_root": omml_root,
        },
        "artifacts": {
            "mathml_xml": etree.tostring(mathml, encoding="unicode"),
            "omml_xml": etree.tostring(final_omml, encoding="unicode"),
        },
    }


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_json_object
        )
    except (OSError, json.JSONDecodeError, NativeMathError) as exc:
        raise NativeMathError(f"cannot read {label} JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise NativeMathError(f"{label} JSON root must be an object: {path}")
    return payload


def _load_json_text(value: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value, object_pairs_hook=_strict_json_object)
    except (json.JSONDecodeError, NativeMathError) as exc:
        raise NativeMathError(f"cannot read {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise NativeMathError(f"{label} JSON root must be an object")
    return payload


def _png_evidence(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise NativeMathError(f"fresh render is not PNG: {path}")
            image.load()
            rgba = image.convert("RGBA")
            width, height = rgba.size
            pixel_sha256 = _sha256_bytes(rgba.tobytes())
    except (OSError, ValueError) as exc:
        raise NativeMathError(f"cannot decode fresh PNG render {path}: {exc}") from exc
    if width <= 0 or height <= 0:
        raise NativeMathError(f"fresh PNG render has invalid dimensions: {path}")
    return {"width": width, "height": height, "pixel_sha256": pixel_sha256}


def _rgb_contrast_ratio(first_rgb: int, second_rgb: int) -> float:
    if not (0 <= first_rgb <= 0xFFFFFF and 0 <= second_rgb <= 0xFFFFFF):
        raise NativeMathError("Office RGB color is outside the 24-bit range")

    def luminance(value: int) -> float:
        components = (
            value & 0xFF,
            (value >> 8) & 0xFF,
            (value >> 16) & 0xFF,
        )
        linear = [
            component / 255 / 12.92
            if component / 255 <= 0.04045
            else ((component / 255 + 0.055) / 1.055) ** 2.4
            for component in components
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first_luminance, second_luminance = luminance(first_rgb), luminance(second_rgb)
    lighter, darker = max(first_luminance, second_luminance), min(
        first_luminance, second_luminance
    )
    return (lighter + 0.05) / (darker + 0.05)


def _minimum_native_math_contrast(audit_profile: str) -> float:
    """Return the visibility floor enforced by the selected audit profile.

    ``standard`` is the source-faithful reconstruction profile. It still
    rejects effectively invisible ink, but it does not replace a verified
    reference palette with an accessibility redesign. ``strict`` retains the
    4.5:1 publication/accessibility gate.
    """
    if audit_profile == "standard":
        return 1.8
    if audit_profile == "strict":
        return 4.5
    raise NativeMathError("audit_profile must be standard or strict")


def _hex_to_office_rgb(value: str) -> int:
    if not re.fullmatch(r"#[A-Fa-f0-9]{6}", value):
        raise NativeMathError("target font color must be #RRGGBB")
    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    return red + (green << 8) + (blue << 16)


def _counterfactual_pixel_evidence(
    baseline_path: Path,
    control_path: Path,
    *,
    left: float,
    top: float,
    shape_width: float,
    shape_height: float,
    slide_width: float,
    slide_height: float,
) -> dict[str, Any]:
    try:
        with Image.open(baseline_path) as baseline_image, Image.open(control_path) as control_image:
            baseline = baseline_image.convert("RGB")
            control = control_image.convert("RGB")
    except (OSError, ValueError) as exc:
        raise NativeMathError(f"cannot decode counterfactual PNG pair: {exc}") from exc
    if baseline.size != control.size or slide_width <= 0 or slide_height <= 0:
        raise NativeMathError("counterfactual PNG dimensions or slide geometry are invalid")
    image_width, image_height = baseline.size
    # Office Math glyphs may overhang the nominal text-box rectangle (italic
    # correction, accents, and sub/superscripts are the common cases).  Treat
    # a small resolution-scaled halo as part of the formula evidence region;
    # changes beyond that halo remain forbidden.  A fixed two-pixel halo was
    # too small at the canonical 1600 px export and made valid counterfactual
    # controls fail solely because of glyph antialiasing outside the box.
    padding = max(2, int(image_height * 0.01 + 0.999))
    x0 = max(0, int(left / slide_width * image_width) - padding)
    y0 = max(0, int(top / slide_height * image_height) - padding)
    x1 = min(
        image_width,
        int((left + shape_width) / slide_width * image_width + 0.999) + padding,
    )
    y1 = min(
        image_height,
        int((top + shape_height) / slide_height * image_height + 0.999) + padding,
    )
    if x1 <= x0 or y1 <= y0:
        raise NativeMathError("counterfactual formula bounding box is empty")
    difference = ImageChops.difference(baseline, control)

    def changed_pixels(image: Image.Image) -> int:
        red, green, blue = image.split()
        maximum_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        threshold_mask = maximum_channel.point(lambda value: 255 if value >= 4 else 0)
        return sum(threshold_mask.histogram()[1:])

    total_changed = changed_pixels(difference)
    inside_changed = changed_pixels(difference.crop((x0, y0, x1, y1)))
    outside_changed = total_changed - inside_changed
    region_area = (x1 - x0) * (y1 - y0)
    required_changed = max(8, int(region_area * 0.0001))
    return {
        "pixel_bbox": [x0, y0, x1, y1],
        "padding_pixels": padding,
        "inside_changed_pixels": inside_changed,
        "outside_changed_pixels": outside_changed,
        "required_changed_pixels": required_changed,
        "pass": inside_changed >= required_changed
        and outside_changed <= max(10, int(inside_changed * 0.05)),
    }


def _project_powerpoint_normalized_inline_root(
    root: etree._Element, expected_mode: str
) -> tuple[etree._Element, bool]:
    """Project PowerPoint's standalone-inline storage rewrite back to m:oMath.

    PowerPoint rewrites a text box whose only content is one inline ``m:oMath``
    run as ``m:oMathPara/m:oMath`` during Save As.  The inner expression is
    unchanged and the external plan/metadata still bind it as inline.  Accept
    only that narrow, empirically observed wrapper normalization; mixed text
    runs and malformed/multi-expression paragraphs remain strict failures.
    """
    if expected_mode != "inline" or etree.QName(root).localname != "oMathPara":
        return root, False
    direct_math = [
        child
        for child in root
        if child.tag == _qn(M_NS, "oMath")
    ]
    unexpected = [
        child
        for child in root
        if etree.QName(child).namespace != M_NS
        or etree.QName(child).localname not in {"oMathParaPr", "oMath"}
    ]
    if len(direct_math) != 1 or unexpected:
        return root, False
    return direct_math[0], True


def _validated_receipt(path: Path) -> dict[str, Any]:
    receipt = _load_json_object(path, label="converter receipt")
    if (
        receipt.get("document_type") != RECEIPT_TYPE
        or receipt.get("schema_version") != "1.1"
        or receipt.get("status") != "PASS"
    ):
        raise NativeMathError(f"converter receipt is not PASS: {path}")
    formula_id = receipt.get("formula_id")
    if not isinstance(formula_id, str) or not formula_id.strip():
        raise NativeMathError(f"converter receipt formula_id is empty: {path}")
    mode = receipt.get("mode")
    if mode not in {"inline", "display"}:
        raise NativeMathError(f"converter receipt mode is invalid: {path}")
    canonical_latex = str(receipt.get("canonical_latex", ""))
    validate_latex_source(canonical_latex)
    if _sha256_text(canonical_latex) != receipt.get("latex_sha256"):
        raise NativeMathError(f"converter receipt LaTeX hash mismatch: {path}")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise NativeMathError(f"converter receipt has no embedded XML artifacts: {path}")
    mathml = _parse_xml(str(artifacts.get("mathml_xml", "")), label="receipt MathML")
    omml = _parse_xml(str(artifacts.get("omml_xml", "")), label="receipt OMML")
    if _sha256_bytes(_canonical_xml(mathml)) != receipt.get("mathml_sha256"):
        raise NativeMathError(f"converter receipt MathML hash mismatch: {path}")
    if _sha256_bytes(_canonical_xml(omml)) != receipt.get("omml_sha256"):
        raise NativeMathError(f"converter receipt OMML hash mismatch: {path}")
    if receipt.get("semantic_omml_profile") != SEMANTIC_OMML_PROFILE:
        raise NativeMathError(f"converter receipt semantic OMML profile mismatch: {path}")
    if _semantic_omml_sha256(omml) != receipt.get("semantic_omml_sha256"):
        raise NativeMathError(f"converter receipt semantic OMML hash mismatch: {path}")
    expected_root = "oMathPara" if mode == "display" else "oMath"
    if etree.QName(omml).namespace != M_NS or etree.QName(omml).localname != expected_root:
        raise NativeMathError(f"converter receipt OMML root/mode mismatch: {path}")
    expected_target = {
        "kind": "office_math",
        "wrapper": "a14:m",
        "omml_root": f"m:{expected_root}",
    }
    if receipt.get("native_target") != expected_target:
        raise NativeMathError(f"converter receipt native target is invalid: {path}")
    converter = receipt.get("converter")
    if not isinstance(converter, Mapping) or converter.get("name") != "latex2mathml+office-mml2omml-xsl":
        raise NativeMathError(f"converter receipt provenance is invalid: {path}")
    trusted_xsl = discover_mml2omml_xsl()
    try:
        receipt_xsl = Path(str(converter.get("xsl_path", ""))).resolve()
    except (OSError, ValueError) as exc:
        raise NativeMathError(f"converter receipt XSL path is invalid: {path}") from exc
    if receipt_xsl != trusted_xsl or converter.get("xsl_sha256") != _sha256_file(trusted_xsl):
        raise NativeMathError(f"converter receipt XSL binding is not trusted: {path}")

    # A receipt is evidence only after deterministic recompilation with the
    # currently pinned converter and trusted Office stylesheet.  Self-consistent
    # attacker-authored JSON cannot authorize native insertion.
    recompiled = compile_formula(formula_id, canonical_latex, str(mode), trusted_xsl)
    for key in (
        "latex_sha256",
        "mathml_sha256",
        "omml_sha256",
        "semantic_omml_profile",
        "semantic_omml_sha256",
        "native_target",
    ):
        if receipt.get(key) != recompiled.get(key):
            raise NativeMathError(f"converter receipt deterministic recompile mismatch ({key}): {path}")
    if receipt.get("converter") != recompiled.get("converter"):
        raise NativeMathError(f"converter receipt runtime provenance mismatch: {path}")
    return receipt


def _metadata_value(payload: Mapping[str, Any], prefix: str = META_PREFIX) -> str:
    packed = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return prefix + base64.urlsafe_b64encode(packed).decode("ascii")


def _decode_metadata(value: str, prefix: str = META_PREFIX) -> dict[str, Any] | None:
    if not value.startswith(prefix):
        return None
    try:
        decoded = base64.urlsafe_b64decode(value[len(prefix) :].encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _shape_name(shape: etree._Element) -> str | None:
    c_nv_pr = shape.find("./p:nvSpPr/p:cNvPr", namespaces=NS)
    return None if c_nv_pr is None else c_nv_pr.get("name")


def _shape_description(shape: etree._Element) -> str:
    c_nv_pr = shape.find("./p:nvSpPr/p:cNvPr", namespaces=NS)
    return "" if c_nv_pr is None else str(c_nv_pr.get("descr", ""))


def _set_shape_description(shape: etree._Element, value: str) -> None:
    c_nv_pr = shape.find("./p:nvSpPr/p:cNvPr", namespaces=NS)
    if c_nv_pr is None:
        raise NativeMathError("placeholder shape has no p:cNvPr")
    c_nv_pr.set("descr", value)


def _find_shape_target(root: etree._Element, name: str) -> tuple[etree._Element, etree._Element]:
    matches: list[tuple[etree._Element, etree._Element]] = []
    for shape in root.xpath(".//p:sp", namespaces=NS):
        if _shape_name(shape) != name:
            continue
        if shape.xpath("ancestor::mc:Fallback", namespaces=NS):
            continue
        alternate = shape.xpath("ancestor::mc:AlternateContent[1]", namespaces=NS)
        target = alternate[0] if alternate else shape
        matches.append((target, shape))
    unique: dict[int, tuple[etree._Element, etree._Element]] = {id(target): (target, shape) for target, shape in matches}
    if len(unique) != 1:
        raise NativeMathError(f"placeholder_name {name!r} matched {len(unique)} objects")
    return next(iter(unique.values()))


def _run_properties_template(shape: etree._Element, *, math: bool) -> etree._Element:
    source = shape.find(".//a:rPr", namespaces=NS)
    if source is None:
        source = shape.find(".//a:defRPr", namespaces=NS)
    if source is None:
        source = shape.find(".//a:endParaRPr", namespaces=NS)
    result = etree.Element(_qn(A_NS, "rPr"))
    if source is not None:
        for key, value in source.attrib.items():
            result.set(key, value)
        for child in source:
            result.append(copy.deepcopy(child))
    if result.get("lang") is None:
        result.set("lang", "en-US")
    if result.get("dirty") is None:
        result.set("dirty", "0")
    if math:
        latin = result.find("a:latin", namespaces=NS)
        if latin is None:
            latin = etree.SubElement(result, _qn(A_NS, "latin"))
        latin.set("typeface", "Cambria Math")
    return result


def _style_omml_runs(omml: etree._Element, shape: etree._Element) -> None:
    for math_run in omml.xpath(".//m:r", namespaces=NS):
        if math_run.find("a:rPr", namespaces=NS) is None:
            math_run.insert(0, _run_properties_template(shape, math=True))


def _clear_paragraph(paragraph: etree._Element) -> etree._Element | None:
    paragraph_properties = paragraph.find("a:pPr", namespaces=NS)
    saved = copy.deepcopy(paragraph_properties) if paragraph_properties is not None else None
    for child in list(paragraph):
        paragraph.remove(child)
    if saved is not None:
        paragraph.append(saved)
    return saved


def _append_text_run(paragraph: etree._Element, text: str, template_shape: etree._Element) -> None:
    if not text:
        return
    run = etree.SubElement(paragraph, _qn(A_NS, "r"))
    run.append(_run_properties_template(template_shape, math=False))
    text_node = etree.SubElement(run, _qn(A_NS, "t"))
    if text[:1].isspace() or text[-1:].isspace() or "  " in text:
        text_node.set(_qn(XML_NS, "space"), "preserve")
    text_node.text = text


def _append_math_run(paragraph: etree._Element, receipt: Mapping[str, Any], template_shape: etree._Element) -> None:
    artifacts = receipt.get("artifacts")
    assert isinstance(artifacts, Mapping)
    omml = _parse_xml(str(artifacts["omml_xml"]), label="injection OMML")
    _style_omml_runs(omml, template_shape)
    math_wrapper = etree.Element(_qn(A14_NS, "m"), nsmap={"a14": A14_NS})
    math_wrapper.append(omml)
    paragraph.append(math_wrapper)


def _end_paragraph(paragraph: etree._Element, template_shape: etree._Element) -> None:
    end = _run_properties_template(template_shape, math=False)
    end.tag = _qn(A_NS, "endParaRPr")
    paragraph.append(end)


def _replace_shape_text(
    shape: etree._Element,
    runs: Sequence[Mapping[str, Any]],
    *,
    native: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text_body = shape.find("p:txBody", namespaces=NS)
    if text_body is None:
        raise NativeMathError("placeholder must be a PowerPoint text shape")
    paragraphs = text_body.findall("a:p", namespaces=NS)
    if not paragraphs:
        paragraph = etree.SubElement(text_body, _qn(A_NS, "p"))
    else:
        paragraph = paragraphs[0]
        for extra in paragraphs[1:]:
            text_body.remove(extra)
    _clear_paragraph(paragraph)
    formulas: list[dict[str, Any]] = []
    content_runs: list[dict[str, Any]] = []
    for run in runs:
        kind = run.get("kind")
        if kind == "text":
            value = str(run.get("text", ""))
            _append_text_run(paragraph, value, shape)
            content_runs.append(
                {
                    "kind": "text",
                    "text": value,
                    "text_sha256": _sha256_text(value),
                }
            )
        elif kind == "math":
            receipt = run.get("receipt")
            if not isinstance(receipt, Mapping):
                raise NativeMathError("math run has no validated receipt")
            if native:
                _append_math_run(paragraph, receipt, shape)
            else:
                delimiter = "\\[" if receipt.get("mode") == "display" else "\\("
                closer = "\\]" if receipt.get("mode") == "display" else "\\)"
                _append_text_run(paragraph, f"{delimiter}{receipt['canonical_latex']}{closer}", shape)
            formula_row = {
                "formula_id": receipt["formula_id"],
                "mode": receipt["mode"],
                "canonical_latex": receipt["canonical_latex"],
                "latex_sha256": receipt["latex_sha256"],
                "omml_sha256": receipt["omml_sha256"],
                "semantic_omml_profile": receipt["semantic_omml_profile"],
                "semantic_omml_sha256": receipt["semantic_omml_sha256"],
            }
            formulas.append(formula_row)
            content_runs.append({"kind": "math", **formula_row})
        else:
            raise NativeMathError(f"unsupported run kind: {kind!r}")
    _end_paragraph(paragraph, shape)
    return formulas, content_runs


def _operation_runs(operation: Mapping[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    raw_runs = operation.get("runs")
    if raw_runs is None:
        raw_runs = [
            {
                "kind": "math",
                "formula_id": operation.get("formula_id"),
                "receipt_path": operation.get("receipt_path"),
                "receipt_sha256": operation.get("receipt_sha256"),
            }
        ]
    if not isinstance(raw_runs, list) or not raw_runs:
        raise NativeMathError("operation runs must be a non-empty array")
    resolved: list[dict[str, Any]] = []
    for raw_run in raw_runs:
        if not isinstance(raw_run, Mapping):
            raise NativeMathError("each operation run must be an object")
        kind = raw_run.get("kind")
        if kind == "text":
            text = raw_run.get("text")
            if not isinstance(text, str) or not text:
                raise NativeMathError("text run must contain non-empty text")
            resolved.append({"kind": "text", "text": text})
            continue
        if kind != "math":
            raise NativeMathError(f"unsupported operation run kind: {kind!r}")
        path_value = raw_run.get("receipt_path")
        if not isinstance(path_value, str) or not path_value:
            raise NativeMathError("math run must contain receipt_path")
        receipt_path = Path(path_value).expanduser()
        if not receipt_path.is_absolute():
            receipt_path = base_dir / receipt_path
        receipt_path = receipt_path.resolve()
        expected_receipt_hash = raw_run.get("receipt_sha256")
        if not isinstance(expected_receipt_hash, str) or not re.fullmatch(
            r"[A-Fa-f0-9]{64}", expected_receipt_hash
        ):
            raise NativeMathError("math run must bind receipt_sha256")
        actual_receipt_hash = _sha256_file(receipt_path)
        if actual_receipt_hash.casefold() != expected_receipt_hash.casefold():
            raise NativeMathError(f"math run converter receipt hash mismatch: {receipt_path}")
        receipt = _validated_receipt(receipt_path)
        expected_formula_id = raw_run.get("formula_id")
        if not isinstance(expected_formula_id, str) or not expected_formula_id:
            raise NativeMathError("math run must bind a non-empty formula_id")
        if expected_formula_id != receipt.get("formula_id"):
            raise NativeMathError(f"formula_id does not match receipt: {receipt_path}")
        resolved.append(
            {
                "kind": "math",
                "receipt": receipt,
                "receipt_path": str(receipt_path),
                "receipt_sha256": actual_receipt_hash,
            }
        )
    math_runs = [run for run in resolved if run["kind"] == "math"]
    if not math_runs:
        raise NativeMathError("an Office Math operation must contain at least one math run")
    if len(resolved) > 1 and any(run["receipt"].get("mode") != "inline" for run in math_runs):
        raise NativeMathError("mixed text/math operations require inline formula receipts")
    if len(resolved) == 1 and math_runs[0]["receipt"].get("mode") not in {"inline", "display"}:
        raise NativeMathError("formula receipt has invalid mode")
    return resolved


def _validated_plan_operations(
    plan_path: Path, *, label: str = "injection plan"
) -> list[Mapping[str, Any]]:
    plan = _load_json_object(plan_path, label=label)
    if plan.get("schema_version") != "1.0":
        raise NativeMathError(f"{label} schema_version must be exactly '1.0'")
    if set(plan).difference({"schema_version", "operations"}):
        raise NativeMathError(
            f"{label} has unsupported keys: {sorted(set(plan).difference({'schema_version', 'operations'}))}"
        )
    raw_operations = plan.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise NativeMathError(f"{label} operations must be a non-empty array")
    operations: list[Mapping[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    allowed_operation_keys = {
        "slide_index",
        "placeholder_name",
        "formula_id",
        "receipt_path",
        "receipt_sha256",
        "runs",
        "target_font_size_pt",
        "target_font_color",
    }
    allowed_run_keys = {"kind", "text", "formula_id", "receipt_path", "receipt_sha256"}
    for index, operation in enumerate(raw_operations):
        if not isinstance(operation, Mapping):
            raise NativeMathError(f"{label} operation {index} must be an object")
        extras = set(operation).difference(allowed_operation_keys)
        if extras:
            raise NativeMathError(f"{label} operation {index} has unsupported keys: {sorted(extras)}")
        slide_index = operation.get("slide_index")
        name = operation.get("placeholder_name")
        if not isinstance(slide_index, int) or isinstance(slide_index, bool) or slide_index < 1:
            raise NativeMathError(f"{label} operation {index} slide_index must be a positive integer")
        if not isinstance(name, str) or not name:
            raise NativeMathError(f"{label} operation {index} placeholder_name must be non-empty")
        target_font_size = operation.get("target_font_size_pt")
        target_font_color = operation.get("target_font_color")
        if (target_font_size is None) != (target_font_color is None):
            raise NativeMathError(
                f"{label} operation {index} must provide both target font style fields"
            )
        if target_font_size is not None:
            if (
                isinstance(target_font_size, bool)
                or not isinstance(target_font_size, (int, float))
                or not math.isfinite(float(target_font_size))
                or not 6.0 <= float(target_font_size) <= 72.0
            ):
                raise NativeMathError(
                    f"{label} operation {index} target_font_size_pt must be 6..72"
                )
            if not isinstance(target_font_color, str) or not re.fullmatch(
                r"#[A-Fa-f0-9]{6}", target_font_color
            ):
                raise NativeMathError(
                    f"{label} operation {index} target_font_color must be #RRGGBB"
                )
        key = (slide_index, name)
        if key in seen:
            raise NativeMathError(
                f"duplicate plan target on slide {slide_index}: {name!r}"
            )
        seen.add(key)
        if "runs" in operation:
            if any(field in operation for field in ("formula_id", "receipt_path", "receipt_sha256")):
                raise NativeMathError(
                    f"{label} operation {index} cannot mix runs with legacy formula fields"
                )
            runs = operation.get("runs")
            if not isinstance(runs, list) or not runs:
                raise NativeMathError(f"{label} operation {index} runs must be non-empty")
            for run_index, run in enumerate(runs):
                if not isinstance(run, Mapping):
                    raise NativeMathError(
                        f"{label} operation {index} run {run_index} must be an object"
                    )
                run_extras = set(run).difference(allowed_run_keys)
                if run_extras:
                    raise NativeMathError(
                        f"{label} operation {index} run {run_index} has unsupported keys: "
                        f"{sorted(run_extras)}"
                    )
        elif not all(field in operation for field in ("formula_id", "receipt_path", "receipt_sha256")):
            raise NativeMathError(
                f"{label} operation {index} must bind formula_id, receipt_path, and receipt_sha256"
            )
        operations.append(operation)
    return operations


def _build_alternate_content(
    template_shape: etree._Element,
    runs: Sequence[Mapping[str, Any]],
    placeholder_name: str,
    *,
    target_font_size_pt: float | None = None,
    target_font_color: str | None = None,
) -> tuple[etree._Element, dict[str, Any]]:
    choice_shape = copy.deepcopy(template_shape)
    fallback_shape = copy.deepcopy(template_shape)
    for shape in (choice_shape, fallback_shape):
        body_properties = shape.find("p:txBody/a:bodyPr", namespaces=NS)
        if body_properties is None:
            raise NativeMathError(
                f"formula placeholder has no DrawingML body properties: {placeholder_name}"
            )
        for inset_name in ("lIns", "rIns", "tIns", "bIns"):
            body_properties.set(inset_name, "0")
    formulas, content_runs = _replace_shape_text(choice_shape, runs, native=True)
    _replace_shape_text(fallback_shape, runs, native=False)
    metadata = {
        "schema_version": "1.0",
        "kind": "native_office_math",
        "placeholder_name": placeholder_name,
        "formulas": formulas,
        "content_runs": content_runs,
    }
    _set_shape_description(choice_shape, _metadata_value(metadata))
    _set_shape_description(fallback_shape, _metadata_value(metadata, prefix=FALLBACK_PREFIX))
    alternate = etree.Element(_qn(MC_NS, "AlternateContent"), nsmap={"mc": MC_NS})
    choice = etree.SubElement(alternate, _qn(MC_NS, "Choice"), nsmap={"a14": A14_NS})
    choice.set("Requires", "a14")
    choice.append(choice_shape)
    fallback = etree.SubElement(alternate, _qn(MC_NS, "Fallback"))
    fallback.append(fallback_shape)
    if target_font_size_pt is not None and target_font_color is not None:
        size_hundredths = str(round(target_font_size_pt * 100))
        color_value = target_font_color[1:].upper()
        run_properties = alternate.xpath(
            ".//a:rPr|.//a:defRPr|.//a:endParaRPr", namespaces=NS
        )
        if not run_properties:
            raise NativeMathError(
                f"target font style has no DrawingML run properties: {placeholder_name}"
            )
        fill_tags = {
            _qn(A_NS, local_name)
            for local_name in (
                "noFill",
                "solidFill",
                "gradFill",
                "blipFill",
                "pattFill",
                "grpFill",
            )
        }
        for properties in run_properties:
            properties.set("sz", size_hundredths)
            for child in list(properties):
                if child.tag in fill_tags:
                    properties.remove(child)
            solid_fill = etree.Element(_qn(A_NS, "solidFill"))
            color = etree.SubElement(solid_fill, _qn(A_NS, "srgbClr"))
            color.set("val", color_value)
            properties.insert(0, solid_fill)
    return alternate, metadata


def _ensure_closed_package(path: Path) -> None:
    lock = path.with_name(f"~${path.name}")
    if lock.exists():
        raise NativeMathError(f"PowerPoint lock file exists; close the deck before injection: {lock}")


def _ordered_slide_parts(package: zipfile.ZipFile) -> list[str]:
    """Resolve UI slide order through presentation relationships.

    A PowerPoint slide's visible index is not guaranteed to equal the numeric
    suffix of ``ppt/slides/slideN.xml`` after slides are reordered or deleted.
    """
    presentation_part = "ppt/presentation.xml"
    relationships_part = "ppt/_rels/presentation.xml.rels"
    names = set(package.namelist())
    if presentation_part not in names or relationships_part not in names:
        raise NativeMathError("PPTX lacks presentation slide-order relationships")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    presentation = etree.fromstring(package.read(presentation_part), parser=parser)
    relationships = etree.fromstring(package.read(relationships_part), parser=parser)
    targets: dict[str, str] = {}
    for relationship in relationships.findall(_qn(PKG_REL_NS, "Relationship")):
        if relationship.get("TargetMode") == "External":
            continue
        relation_id = relationship.get("Id")
        target = relationship.get("Target")
        relation_type = relationship.get("Type", "")
        if relation_id and target and relation_type.endswith("/slide"):
            normalized = posixpath.normpath(posixpath.join("ppt", target)).lstrip("/")
            if not SLIDE_PART_RE.fullmatch(normalized):
                raise NativeMathError(f"invalid internal slide relationship target: {target!r}")
            targets[relation_id] = normalized
    result: list[str] = []
    for slide_id in presentation.xpath("./p:sldIdLst/p:sldId", namespaces=NS):
        relation_id = slide_id.get(_qn(R_NS, "id"))
        part_name = targets.get(str(relation_id))
        if not part_name or part_name not in names:
            raise NativeMathError(f"slide relationship is missing or unresolved: {relation_id!r}")
        if part_name in result:
            raise NativeMathError(f"duplicate slide relationship target: {part_name}")
        result.append(part_name)
    if not result:
        raise NativeMathError("PPTX contains no ordered slides")
    return result


def _opc_findings(package: zipfile.ZipFile) -> list[dict[str, Any]]:
    """Validate the package relationships needed for a safe PPTX transaction."""
    findings: list[dict[str, Any]] = []
    names = set(package.namelist())
    required = {
        "[Content_Types].xml",
        "_rels/.rels",
        "ppt/presentation.xml",
        "ppt/_rels/presentation.xml.rels",
    }
    for part in sorted(required.difference(names)):
        findings.append({"code": "OPC_REQUIRED_PART_MISSING", "part": part})
    if findings:
        return findings
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    try:
        content_types = etree.fromstring(package.read("[Content_Types].xml"), parser=parser)
    except etree.XMLSyntaxError as exc:
        return [{"code": "OPC_CONTENT_TYPES_INVALID", "error": str(exc)}]
    if content_types.tag != _qn(CT_NS, "Types"):
        findings.append({"code": "OPC_CONTENT_TYPES_ROOT_INVALID"})
    overrides = {
        str(node.get("PartName", "")).lstrip("/"): str(node.get("ContentType", ""))
        for node in content_types.findall(_qn(CT_NS, "Override"))
    }
    presentation_type = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
    )
    slide_type = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
    if overrides.get("ppt/presentation.xml") != presentation_type:
        findings.append({"code": "OPC_PRESENTATION_CONTENT_TYPE_INVALID"})
    for slide_part in _ordered_slide_parts(package):
        if overrides.get(slide_part) != slide_type:
            findings.append({"code": "OPC_SLIDE_CONTENT_TYPE_INVALID", "part": slide_part})

    for relationships_part in sorted(name for name in names if name.endswith(".rels")):
        try:
            relationships = etree.fromstring(package.read(relationships_part), parser=parser)
        except etree.XMLSyntaxError as exc:
            findings.append(
                {
                    "code": "OPC_RELATIONSHIPS_INVALID",
                    "part": relationships_part,
                    "error": str(exc),
                }
            )
            continue
        if relationships.tag != _qn(PKG_REL_NS, "Relationships"):
            findings.append({"code": "OPC_RELATIONSHIPS_ROOT_INVALID", "part": relationships_part})
            continue
        base_directory = (
            ""
            if relationships_part == "_rels/.rels"
            else posixpath.dirname(posixpath.dirname(relationships_part))
        )
        for relationship in relationships.findall(_qn(PKG_REL_NS, "Relationship")):
            if relationship.get("TargetMode") == "External":
                continue
            target = str(relationship.get("Target", "")).split("#", 1)[0]
            if not target:
                findings.append(
                    {"code": "OPC_RELATIONSHIP_TARGET_EMPTY", "part": relationships_part}
                )
                continue
            if target.startswith("/"):
                resolved = posixpath.normpath(target).lstrip("/")
            else:
                resolved = posixpath.normpath(posixpath.join(base_directory, target))
            if resolved == ".." or resolved.startswith("../") or resolved not in names:
                findings.append(
                    {
                        "code": "OPC_RELATIONSHIP_TARGET_MISSING",
                        "part": relationships_part,
                        "target": target,
                        "resolved": resolved,
                    }
                )
    return findings


def _rewrite_pptx(
    source: Path,
    output: Path,
    replacements: Mapping[str, bytes],
    *,
    overwrite: bool,
) -> None:
    output = _validated_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(source, "r") as input_zip, zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as output_zip:
            names = set(input_zip.namelist())
            missing = set(replacements).difference(names)
            if missing:
                raise NativeMathError(f"PPTX parts are missing: {sorted(missing)}")
            for info in input_zip.infolist():
                output_zip.writestr(info, replacements.get(info.filename, input_zip.read(info.filename)))
        with zipfile.ZipFile(temporary, "r") as check_zip:
            if check_zip.testzip() is not None:
                raise NativeMathError("rewritten PPTX failed ZIP integrity validation")
            opc_issues = _opc_findings(check_zip)
            if opc_issues:
                raise NativeMathError(f"rewritten PPTX failed OPC validation: {opc_issues}")
        if overwrite:
            if output.exists():
                _ensure_closed_package(output)
            os.replace(temporary, output)
        else:
            try:
                os.link(temporary, output)
            except FileExistsError as exc:
                raise NativeMathError(
                    f"injection output appeared during rewrite; refusing overwrite: {output}"
                ) from exc
            temporary.unlink()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def inject_plan(
    input_pptx: str | os.PathLike[str],
    plan_path: str | os.PathLike[str],
    output_pptx: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    source = Path(input_pptx).expanduser().resolve()
    plan_file = Path(plan_path).expanduser().resolve()
    output = _validated_output(output_pptx)
    if not source.is_file() or source.suffix.casefold() != ".pptx":
        raise NativeMathError(f"input must be an existing .pptx: {source}")
    if source == output:
        raise NativeMathError("injection output must differ from the source PPTX")
    _ensure_closed_package(source)
    if output.exists():
        _ensure_closed_package(output)
        if not overwrite:
            raise NativeMathError(f"output already exists; pass overwrite=True explicitly: {output}")
    operations = _validated_plan_operations(plan_file)

    by_slide: dict[int, list[Mapping[str, Any]]] = {}
    for operation in operations:
        slide_index = operation["slide_index"]
        assert isinstance(slide_index, int)
        by_slide.setdefault(slide_index, []).append(operation)

    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    replacements: dict[str, bytes] = {}
    results: list[dict[str, Any]] = []
    with zipfile.ZipFile(source, "r") as package:
        opc_issues = _opc_findings(package)
        if opc_issues:
            raise NativeMathError(f"input PPTX failed OPC validation: {opc_issues}")
        ordered_slide_parts = _ordered_slide_parts(package)
        for slide_index, slide_operations in sorted(by_slide.items()):
            if slide_index > len(ordered_slide_parts):
                raise NativeMathError(f"slide_index {slide_index} does not exist")
            part_name = ordered_slide_parts[slide_index - 1]
            root = etree.fromstring(package.read(part_name), parser=parser)
            seen_names: set[str] = set()
            for operation in slide_operations:
                name = operation.get("placeholder_name")
                if not isinstance(name, str) or not name:
                    raise NativeMathError("placeholder_name must be a non-empty string")
                if name in seen_names:
                    raise NativeMathError(f"duplicate placeholder operation on slide {slide_index}: {name}")
                seen_names.add(name)
                target, template_shape = _find_shape_target(root, name)
                parent = target.getparent()
                if parent is None:
                    raise NativeMathError(f"placeholder has no parent: {name}")
                runs = _operation_runs(operation, plan_file.parent)
                alternate, metadata = _build_alternate_content(
                    template_shape,
                    runs,
                    name,
                    target_font_size_pt=(
                        float(operation["target_font_size_pt"])
                        if "target_font_size_pt" in operation
                        else None
                    ),
                    target_font_color=(
                        str(operation["target_font_color"])
                        if "target_font_color" in operation
                        else None
                    ),
                )
                parent.replace(target, alternate)
                results.append(
                    {
                        "slide_index": slide_index,
                        "placeholder_name": name,
                        **(
                            {
                                "target_font_size_pt": float(
                                    operation["target_font_size_pt"]
                                ),
                                "target_font_color": str(
                                    operation["target_font_color"]
                                ).upper(),
                            }
                            if "target_font_size_pt" in operation
                            else {}
                        ),
                        "formula_ids": [item["formula_id"] for item in metadata["formulas"]],
                        "latex_sha256": [item["latex_sha256"] for item in metadata["formulas"]],
                        "omml_sha256": [item["omml_sha256"] for item in metadata["formulas"]],
                        "semantic_omml_sha256": [
                            item["semantic_omml_sha256"] for item in metadata["formulas"]
                        ],
                    }
                )
            replacements[part_name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )

    source_hash = _sha256_file(source)
    _rewrite_pptx(source, output, replacements, overwrite=overwrite)
    output_hash = _sha256_file(output)
    return {
        "document_type": REPORT_TYPE,
        "schema_version": "1.0",
        "status": "INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP",
        "source_pptx": str(source),
        "source_sha256": source_hash,
        "output_pptx": str(output),
        "output_sha256": output_hash,
        "plan_path": str(plan_file),
        "plan_sha256": _sha256_file(plan_file),
        "operations": results,
        "requires_powerpoint_save_reopen_readback": True,
    }


def _expected_from_plan(plan_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    expected: dict[tuple[int, str], dict[str, Any]] = {}
    operations = _validated_plan_operations(plan_path, label="expected injection plan")
    for operation in operations:
        slide_index = operation["slide_index"]
        name = operation["placeholder_name"]
        assert isinstance(slide_index, int) and isinstance(name, str)
        runs = _operation_runs(operation, plan_path.parent)
        expected_runs: list[dict[str, Any]] = []
        for run in runs:
            if run["kind"] == "text":
                value = str(run["text"])
                expected_runs.append(
                    {"kind": "text", "text": value, "text_sha256": _sha256_text(value)}
                )
                continue
            receipt = run["receipt"]
            expected_runs.append(
                {
                    "kind": "math",
                    "formula_id": receipt["formula_id"],
                    "mode": receipt["mode"],
                    "canonical_latex": receipt["canonical_latex"],
                    "latex_sha256": receipt["latex_sha256"],
                    "omml_sha256": receipt["omml_sha256"],
                    "semantic_omml_profile": receipt["semantic_omml_profile"],
                    "semantic_omml_sha256": receipt["semantic_omml_sha256"],
                    "receipt_sha256": run["receipt_sha256"],
                }
            )
        key = (slide_index, name)
        if key in expected:
            raise NativeMathError(f"duplicate expected plan target: slide {slide_index}, {name!r}")
        expected[key] = {
            "content_runs": expected_runs,
            "formula_ids": [
                str(run["formula_id"]) for run in expected_runs if run["kind"] == "math"
            ],
            **(
                {
                    "target_font_size_pt": float(operation["target_font_size_pt"]),
                    "target_font_color": str(operation["target_font_color"]).upper(),
                    "target_font_color_rgb": _hex_to_office_rgb(
                        str(operation["target_font_color"])
                    ),
                }
                if "target_font_size_pt" in operation
                else {}
            ),
        }
    return expected


def _path_matches(value: Any, expected: Path) -> bool:
    try:
        return Path(str(value)).expanduser().resolve() == expected
    except (OSError, ValueError):
        return False


def _expected_injection_operations(
    expected: Mapping[tuple[int, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (slide_index, name), shape in sorted(expected.items()):
        formulas = [run for run in shape["content_runs"] if run["kind"] == "math"]
        rows.append(
            {
                "slide_index": slide_index,
                "placeholder_name": name,
                **(
                    {
                        "target_font_size_pt": shape["target_font_size_pt"],
                        "target_font_color": shape["target_font_color"],
                    }
                    if "target_font_size_pt" in shape
                    else {}
                ),
                "formula_ids": [run["formula_id"] for run in formulas],
                "latex_sha256": [run["latex_sha256"] for run in formulas],
                "omml_sha256": [run["omml_sha256"] for run in formulas],
                "semantic_omml_sha256": [run["semantic_omml_sha256"] for run in formulas],
            }
        )
    return rows


def _validate_injection_report(
    report_path: Path,
    *,
    input_pptx: Path,
    plan_path: Path,
    expected: Mapping[tuple[int, str], Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _load_json_object(report_path, label="native-math injection report")
    findings: list[dict[str, Any]] = []
    expected_values = {
        "document_type": REPORT_TYPE,
        "schema_version": "1.0",
        "status": "INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP",
        "output_sha256": _sha256_file(input_pptx),
        "plan_sha256": _sha256_file(plan_path),
        "requires_powerpoint_save_reopen_readback": True,
    }
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected_values.items()
        if payload.get(key) != value
    }
    raw_operations = payload.get("operations")
    actual_operations = (
        [dict(item) for item in raw_operations if isinstance(item, Mapping)]
        if isinstance(raw_operations, list)
        else []
    )
    actual_operations.sort(
        key=lambda item: (int(item.get("slide_index", 0)), str(item.get("placeholder_name", "")))
    )
    expected_operations = _expected_injection_operations(expected)
    if actual_operations != expected_operations:
        mismatches["operations"] = {
            "expected": expected_operations,
            "actual": actual_operations,
        }
    if not _path_matches(payload.get("output_pptx"), input_pptx):
        mismatches["output_pptx"] = {
            "expected": str(input_pptx),
            "actual": payload.get("output_pptx"),
        }
    if not _path_matches(payload.get("plan_path"), plan_path):
        mismatches["plan_path"] = {
            "expected": str(plan_path),
            "actual": payload.get("plan_path"),
        }
    source_path_value = payload.get("source_pptx")
    try:
        source_path = Path(str(source_path_value)).expanduser().resolve()
    except (OSError, ValueError):
        source_path = Path()
    if (
        not source_path.is_file()
        or source_path.suffix.casefold() != ".pptx"
        or payload.get("source_sha256") != _sha256_file(source_path)
    ):
        mismatches["source_pptx"] = {
            "expected": "an existing hash-bound source PPTX",
            "actual": source_path_value,
        }
    if mismatches:
        findings.append({"code": "INJECTION_REPORT_BINDING_MISMATCH", "mismatches": mismatches})
    return (
        {
            "path": str(report_path),
            "sha256": _sha256_file(report_path),
            "source_pptx": payload.get("source_pptx"),
            "source_sha256": payload.get("source_sha256"),
            "input_sha256": payload.get("output_sha256"),
        },
        findings,
    )


def _validate_runtime_roundtrip_receipt(
    receipt_path: Path,
    *,
    direct_stdout_payload: Mapping[str, Any],
    powershell_executable: Path,
    challenge: str,
    powershell_process_id: int,
    input_pptx: Path,
    output_pptx: Path,
    plan_path: Path,
    injection_report_path: Path,
    render_directory: Path,
    expected: Mapping[tuple[int, str], Mapping[str, Any]],
    slide_count: int,
    audit_profile: str = "strict",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate evidence created by the child process launched by ``finalize``.

    This function is intentionally not used by :func:`audit_pptx`; a detached
    JSON receipt is declarative data and can never elevate a static audit to
    PASS.  The random challenge and child PID are capabilities held only by the
    currently executing finalization transaction.
    """
    payload = _load_json_object(receipt_path, label="fresh PowerPoint roundtrip receipt")
    findings: list[dict[str, Any]] = []
    if payload != direct_stdout_payload:
        findings.append({"code": "POWERPOINT_DIRECT_STDOUT_RECEIPT_MISMATCH"})
    script_path = Path(__file__).with_name("powerpoint_native_math_roundtrip.ps1").resolve()
    expected_values = {
        "document_type": ROUNDTRIP_RECEIPT_TYPE,
        "schema_version": ROUNDTRIP_RECEIPT_VERSION,
        "status": "OBSERVED_PASS",
        "challenge": challenge,
        "powershell_process_id": powershell_process_id,
        "parent_process_id": os.getpid(),
        "input_sha256": _sha256_file(input_pptx),
        "output_sha256": _sha256_file(output_pptx),
        "expected_plan_sha256": _sha256_file(plan_path),
        "injection_report_sha256": _sha256_file(injection_report_path),
        "roundtrip_script_sha256": _sha256_file(script_path),
        "powershell_executable_sha256": _sha256_file(powershell_executable),
        "powershell_signature_status": "Valid",
        "failure": None,
        "violations": [],
    }
    if "audit_profile" in payload or audit_profile != "strict":
        expected_values["audit_profile"] = audit_profile
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected_values.items()
        if payload.get(key) != value
    }
    path_bindings = {
        "input_pptx": input_pptx,
        "output_pptx": output_pptx,
        "expected_plan_path": plan_path,
        "injection_report_path": injection_report_path,
        "roundtrip_script_path": script_path,
        "powershell_executable_path": powershell_executable,
        "render_directory": render_directory,
    }
    for key, expected_path in path_bindings.items():
        if not _path_matches(payload.get(key), expected_path):
            mismatches[key] = {"expected": str(expected_path), "actual": payload.get(key)}
    if mismatches:
        findings.append({"code": "POWERPOINT_RUNTIME_BINDING_MISMATCH", "mismatches": mismatches})

    stages = payload.get("stages") if isinstance(payload.get("stages"), Mapping) else {}
    required_stages = (
        "opened",
        "formula_styles_verified_at_open",
        "saved_as_staging_pptx",
        "first_close",
        "reopened_read_only",
        "math_zones_read",
        "visibility_scanned",
        "masquerade_scanned",
        "render_exported",
        "counterfactual_rendered",
        "second_close",
        "output_committed",
        "render_committed",
    )
    if any(stages.get(stage) is not True for stage in required_stages):
        findings.append({"code": "POWERPOINT_RUNTIME_STAGE_INCOMPLETE", "stages": dict(stages)})
    if not str(payload.get("powerpoint_version", "")).strip():
        findings.append({"code": "POWERPOINT_RUNTIME_VERSION_MISSING"})
    try:
        if int(payload.get("powerpoint_process_id", 0)) < 1:
            raise ValueError
    except (TypeError, ValueError):
        findings.append({"code": "POWERPOINT_RUNTIME_PROCESS_ID_INVALID"})
    if "Microsoft" not in str(payload.get("powershell_signer_subject", "")):
        findings.append({"code": "POWERSHELL_AUTHENTICODE_SIGNER_INVALID"})
    if not re.fullmatch(r"[A-Fa-f0-9]{40,64}", str(payload.get("powershell_signer_thumbprint", ""))):
        findings.append({"code": "POWERSHELL_AUTHENTICODE_THUMBPRINT_INVALID"})
    powerpoint_executable_value = payload.get("powerpoint_executable_path")
    try:
        powerpoint_executable = Path(str(powerpoint_executable_value)).expanduser().resolve()
    except (OSError, ValueError):
        powerpoint_executable = Path()
    if (
        not powerpoint_executable.is_file()
        or powerpoint_executable.name.casefold() != "powerpnt.exe"
        or payload.get("powerpoint_executable_sha256") != _sha256_file(powerpoint_executable)
        or payload.get("powerpoint_signature_status") != "Valid"
        or "Microsoft" not in str(payload.get("powerpoint_signer_subject", ""))
        or not re.fullmatch(
            r"[A-Fa-f0-9]{40,64}", str(payload.get("powerpoint_signer_thumbprint", ""))
        )
        or not str(payload.get("powerpoint_executable_version", "")).strip()
    ):
        findings.append(
            {
                "code": "POWERPOINT_EXECUTABLE_IDENTITY_INVALID",
                "path": powerpoint_executable_value,
                "signature_status": payload.get("powerpoint_signature_status"),
                "signer_subject": payload.get("powerpoint_signer_subject"),
            }
        )

    raw_shapes = payload.get("math_shapes")
    shape_rows = (
        [item for item in raw_shapes if isinstance(item, Mapping)]
        if isinstance(raw_shapes, list)
        else []
    )
    observed: dict[tuple[int, str], int] = {}
    zone_inventory_by_shape: dict[tuple[int, str], dict[int, dict[str, Any]]] = {}
    invalid_shape_rows: list[dict[str, Any]] = []
    minimum_contrast_required = _minimum_native_math_contrast(audit_profile)
    for item in shape_rows:
        try:
            key = (int(item.get("slide_index", 0)), str(item.get("shape_name", "")))
            zone_count = int(item.get("math_zone_count", 0))
            zone_length = int(item.get("math_zone_length", 0))
            shape_type = int(item.get("shape_type", 0))
            left = float(item.get("left"))
            top = float(item.get("top"))
            width = float(item.get("width"))
            height = float(item.get("height"))
            slide_width = float(item.get("slide_width"))
            slide_height = float(item.get("slide_height"))
            z_order = int(item.get("z_order_position", 0))
            text_transparency = float(item.get("text_fill_transparency", 1.0))
            minimum_font_size = float(item.get("minimum_font_size", 0))
            minimum_contrast = float(item.get("minimum_contrast_ratio", 0))
            observed_contrast_requirement = float(
                item.get("minimum_contrast_ratio_required", 0)
            )
            maximum_character_transparency = float(
                item.get("maximum_character_transparency", 1.0)
            )
            checked_character_count = int(item.get("checked_character_count", 0))
            background_rgb = int(item.get("background_rgb", -1))
            raw_colors = item.get("color_rgb_values")
            color_values = (
                [int(value) for value in raw_colors] if isinstance(raw_colors, list) else []
            )
            expected_shape = expected.get(key, {})
            style_valid = True
            if "target_font_size_pt" in expected_shape:
                target_size = float(expected_shape["target_font_size_pt"])
                target_color = str(expected_shape["target_font_color"])
                target_color_rgb = int(expected_shape["target_font_color_rgb"])
                style_valid = (
                    abs(float(item.get("target_font_size_pt")) - target_size) <= 1e-6
                    and str(item.get("target_font_color")) == target_color
                    and int(item.get("target_font_color_rgb")) == target_color_rgb
                    and abs(minimum_font_size - target_size) <= 0.15
                    and color_values == [target_color_rgb]
                )
            recomputed_contrast = min(
                (_rgb_contrast_ratio(color, background_rgb) for color in color_values),
                default=0.0,
            )
            raw_zone_inventory = item.get("math_zones")
            zone_inventory = (
                [dict(zone) for zone in raw_zone_inventory if isinstance(zone, Mapping)]
                if isinstance(raw_zone_inventory, list)
                else []
            )
            parsed_zones = {
                int(zone["zone_index"]): {
                    "start": int(zone["start"]),
                    "length": int(zone["length"]),
                    "text_sha256": str(zone["text_sha256"]),
                }
                for zone in zone_inventory
            }
            zone_boundaries = [
                (parsed_zones[index]["start"], parsed_zones[index]["length"])
                for index in range(1, zone_count + 1)
            ]
            zone_inventory_valid = (
                len(zone_inventory) == zone_count
                and len(parsed_zones) == zone_count
                and set(parsed_zones) == set(range(1, zone_count + 1))
                and all(start >= 1 and length >= 1 for start, length in zone_boundaries)
                and all(
                    zone_boundaries[index - 1][0] + zone_boundaries[index - 1][1]
                    <= zone_boundaries[index][0]
                    for index in range(1, len(zone_boundaries))
                )
                and all(
                    re.fullmatch(r"[a-f0-9]{64}", zone["text_sha256"])
                    for zone in parsed_zones.values()
                )
            )
        except (TypeError, ValueError, NativeMathError):
            invalid_shape_rows.append(dict(item))
            continue
        on_canvas = (
            left >= 0
            and top >= 0
            and width > 0
            and height > 0
            and left + width <= slide_width + 0.01
            and top + height <= slide_height + 0.01
        )
        if (
            key in observed
            or key[0] < 1
            or not key[1]
            or zone_count < 1
            or zone_length < 1
            or shape_type != 17
            or item.get("math_zone_error") not in {None, ""}
            or item.get("visible") is not True
            or not on_canvas
            or z_order < 1
            or text_transparency > 0.05
            or minimum_font_size < 6.0
            or minimum_contrast < minimum_contrast_required
            or recomputed_contrast < minimum_contrast_required
            or abs(observed_contrast_requirement - minimum_contrast_required) > 0.001
            or abs(minimum_contrast - recomputed_contrast) > 0.001
            or maximum_character_transparency > 0.05
            or checked_character_count < 1
            or item.get("ink_evidence_error") not in {None, ""}
            or not zone_inventory_valid
            or not style_valid
        ):
            invalid_shape_rows.append(dict(item))
            continue
        observed[key] = zone_count
        zone_inventory_by_shape[key] = parsed_zones
    if invalid_shape_rows:
        findings.append({"code": "POWERPOINT_RUNTIME_MATH_SHAPE_INVALID", "rows": invalid_shape_rows})
    expected_counts = {
        key: sum(1 for run in value["content_runs"] if run["kind"] == "math")
        for key, value in expected.items()
    }
    if observed != expected_counts:
        findings.append(
            {
                "code": "POWERPOINT_RUNTIME_MATHZONES_MISMATCH",
                "expected": [
                    {"slide_index": key[0], "shape_name": key[1], "math_zone_count": count}
                    for key, count in sorted(expected_counts.items())
                ],
                "actual": [
                    {"slide_index": key[0], "shape_name": key[1], "math_zone_count": count}
                    for key, count in sorted(observed.items())
                ],
            }
        )

    expected_styles = [
        {
            "slide_index": slide_index,
            "shape_name": shape_name,
            "target_font_size_pt": shape["target_font_size_pt"],
            "target_font_color": shape["target_font_color"],
            "target_font_color_rgb": shape["target_font_color_rgb"],
        }
        for (slide_index, shape_name), shape in sorted(expected.items())
        if "target_font_size_pt" in shape
    ]
    raw_verified_styles = payload.get("verified_input_formula_styles")
    verified_styles = (
        [dict(item) for item in raw_verified_styles if isinstance(item, Mapping)]
        if isinstance(raw_verified_styles, list)
        else []
    )
    verified_styles.sort(
        key=lambda item: (int(item.get("slide_index", 0)), str(item.get("shape_name", "")))
    )
    verified_targets: list[dict[str, Any]] = []
    invalid_verified_styles: list[dict[str, Any]] = []
    for item in verified_styles:
        try:
            target = {
                "slide_index": int(item["slide_index"]),
                "shape_name": str(item["shape_name"]),
                "target_font_size_pt": float(item["target_font_size_pt"]),
                "target_font_color": str(item["target_font_color"]),
                "target_font_color_rgb": int(item["target_font_color_rgb"]),
            }
            observed_valid = (
                abs(float(item["observed_font_size_pt"]) - target["target_font_size_pt"])
                <= 0.15
                and int(item["observed_font_color_rgb"])
                == target["target_font_color_rgb"]
                and 0 <= float(item["observed_font_transparency"]) <= 0.05
            )
        except (KeyError, TypeError, ValueError):
            invalid_verified_styles.append(item)
            continue
        verified_targets.append(target)
        if not observed_valid:
            invalid_verified_styles.append(item)
    if verified_targets != expected_styles or invalid_verified_styles:
        findings.append(
            {
                "code": "POWERPOINT_RUNTIME_FORMULA_STYLE_BINDING_INVALID",
                "expected": expected_styles,
                "actual": verified_styles,
                "invalid": invalid_verified_styles,
            }
        )

    raw_renders = payload.get("renders")
    renders = (
        [item for item in raw_renders if isinstance(item, Mapping)]
        if isinstance(raw_renders, list)
        else []
    )
    seen_slides: set[int] = set()
    invalid_renders: list[dict[str, Any]] = []
    for item in renders:
        try:
            slide_index = int(item.get("slide_index", 0))
            render_path = Path(str(item.get("path", ""))).expanduser().resolve()
            verification_path = Path(
                str(item.get("verification_path", ""))
            ).expanduser().resolve()
            byte_length = int(item.get("byte_length", 0))
            verification_byte_length = int(item.get("verification_byte_length", 0))
        except (TypeError, ValueError, OSError):
            invalid_renders.append(dict(item))
            continue
        expected_path = (render_directory / f"slide-{slide_index}.png").resolve()
        expected_verification_path = (
            render_directory / f"slide-{slide_index}.verify.png"
        ).resolve()
        valid = (
            slide_index not in seen_slides
            and 1 <= slide_index <= slide_count
            and render_path == expected_path
            and verification_path == expected_verification_path
            and render_path.is_file()
            and verification_path.is_file()
            and render_path.stat().st_size == byte_length
            and verification_path.stat().st_size == verification_byte_length
            and byte_length > 0
            and verification_byte_length > 0
            and _sha256_file(render_path) == item.get("sha256")
            and _sha256_file(verification_path) == item.get("verification_sha256")
        )
        if valid:
            try:
                primary_pixels = _png_evidence(render_path)
                verification_pixels = _png_evidence(verification_path)
                valid = (
                    primary_pixels == verification_pixels
                    and primary_pixels["width"] == int(item.get("width", 0))
                    and primary_pixels["height"] == int(item.get("height", 0))
                )
            except NativeMathError:
                valid = False
        if not valid:
            invalid_renders.append(dict(item))
        seen_slides.add(slide_index)
    if invalid_renders or seen_slides != set(range(1, slide_count + 1)):
        findings.append(
            {
                "code": "POWERPOINT_FRESH_RENDER_BINDING_INVALID",
                "invalid": invalid_renders,
                "expected_slide_count": slide_count,
                "observed_slides": sorted(seen_slides),
            }
        )

    baseline_by_slide = {
        int(item["slide_index"]): Path(str(item["path"])).expanduser().resolve()
        for item in renders
        if "slide_index" in item and "path" in item
    }
    shape_by_key = {
        (int(item.get("slide_index", 0)), str(item.get("shape_name", ""))): item
        for item in shape_rows
    }
    raw_controls = payload.get("counterfactual_renders")
    controls = (
        [dict(item) for item in raw_controls if isinstance(item, Mapping)]
        if isinstance(raw_controls, list)
        else []
    )
    expected_control_keys: set[tuple[int, str, int, str]] = set()
    if audit_profile == "strict":
        for (slide_index, shape_name), expected_shape in expected.items():
            math_run_index = 0
            for run in expected_shape["content_runs"]:
                if run["kind"] != "math":
                    continue
                math_run_index += 1
                expected_control_keys.add(
                    (slide_index, shape_name, math_run_index, str(run["formula_id"]))
                )
    validated_controls: list[dict[str, Any]] = []
    invalid_controls: list[dict[str, Any]] = []
    seen_control_keys: set[tuple[int, str, int, str]] = set()
    for item in controls:
        try:
            slide_index = int(item.get("slide_index", 0))
            shape_name = str(item.get("shape_name", ""))
            shape_id = int(item.get("shape_id", 0))
            formula_id = str(item.get("formula_id", ""))
            math_run_index = int(item.get("math_run_index", 0))
            zone_index = int(item.get("zone_index", 0))
            key = (slide_index, shape_name, math_run_index, formula_id)
            shape_key = (slide_index, shape_name)
            shape_row = shape_by_key[shape_key]
            expected_zone = zone_inventory_by_shape[shape_key][zone_index]
            control_path = Path(str(item.get("path", ""))).expanduser().resolve()
            expected_control_path = (
                render_directory
                / f"control-slide-{slide_index}-shape-{shape_id}-zone-{zone_index}.png"
            ).resolve()
            byte_length = int(item.get("byte_length", 0))
            geometry_fields = {
                "left": float(item.get("left")),
                "top": float(item.get("top")),
                "shape_width": float(item.get("shape_width")),
                "shape_height": float(item.get("shape_height")),
                "slide_width": float(item.get("slide_width")),
                "slide_height": float(item.get("slide_height")),
            }
            geometry_matches = all(
                abs(geometry_fields[control_field] - float(shape_row[shape_field])) <= 0.01
                for control_field, shape_field in (
                    ("left", "left"),
                    ("top", "top"),
                    ("shape_width", "width"),
                    ("shape_height", "height"),
                    ("slide_width", "slide_width"),
                    ("slide_height", "slide_height"),
                )
            )
            valid = (
                key not in seen_control_keys
                and key in expected_control_keys
                and zone_index == math_run_index
                and shape_id == int(shape_row.get("shape_id", 0))
                and int(item.get("selected_zone_count", 0)) == 1
                and int(item.get("zone_start", 0)) == expected_zone["start"]
                and int(item.get("zone_length", 0)) == expected_zone["length"]
                and item.get("zone_text_sha256") == expected_zone["text_sha256"]
                and control_path == expected_control_path
                and control_path.is_file()
                and byte_length > 0
                and control_path.stat().st_size == byte_length
                and _sha256_file(control_path) == item.get("sha256")
                and int(item.get("width", 0)) == 1600
                and int(item.get("height", 0)) > 0
                and item.get("mutation") == "mathzone-font-fill-transparency-1.0"
                and 0 <= float(item.get("baseline_transparency", 1.0)) <= 0.05
                and geometry_matches
                and slide_index in baseline_by_slide
            )
            pixel_evidence = (
                _counterfactual_pixel_evidence(
                    baseline_by_slide[slide_index], control_path, **geometry_fields
                )
                if valid
                else {"pass": False}
            )
            valid = valid and pixel_evidence["pass"] is True
        except (KeyError, TypeError, ValueError, OSError, NativeMathError):
            valid = False
            pixel_evidence = {"pass": False}
            key = (0, "", 0, "")
        item["pixel_evidence"] = pixel_evidence
        if valid:
            validated_controls.append(item)
        else:
            invalid_controls.append(item)
        seen_control_keys.add(key)
    if invalid_controls or seen_control_keys != expected_control_keys:
        findings.append(
            {
                "code": "POWERPOINT_COUNTERFACTUAL_RENDER_INVALID",
                "invalid": invalid_controls,
                "expected": [
                    {
                        "slide_index": key[0],
                        "shape_name": key[1],
                        "math_run_index": key[2],
                        "formula_id": key[3],
                    }
                    for key in sorted(expected_control_keys)
                ],
                "observed": [
                    {
                        "slide_index": key[0],
                        "shape_name": key[1],
                        "math_run_index": key[2],
                        "formula_id": key[3],
                    }
                    for key in sorted(seen_control_keys)
                ],
            }
        )

    return (
        {
            "path": str(receipt_path),
            "sha256": _sha256_file(receipt_path),
            "challenge_sha256": _sha256_text(challenge),
            "powerpoint_version": payload.get("powerpoint_version"),
            "powerpoint_process_id": payload.get("powerpoint_process_id"),
            "powerpoint_executable_path": payload.get("powerpoint_executable_path"),
            "powerpoint_executable_sha256": payload.get("powerpoint_executable_sha256"),
            "powerpoint_executable_version": payload.get("powerpoint_executable_version"),
            "powerpoint_signer_subject": payload.get("powerpoint_signer_subject"),
            "powerpoint_signer_thumbprint": payload.get("powerpoint_signer_thumbprint"),
            "input_sha256": payload.get("input_sha256"),
            "output_sha256": payload.get("output_sha256"),
            "injection_report_sha256": payload.get("injection_report_sha256"),
            "math_shapes": shape_rows,
            "shape_inventory": payload.get("shape_inventory"),
            "template_inventory": payload.get("template_inventory"),
            "renders": renders,
            "counterfactual_renders": validated_controls,
            "audit_profile": audit_profile,
        },
        findings,
    )


def _paragraph_content_runs(
    paragraph: etree._Element,
) -> tuple[list[dict[str, Any]], list[str]]:
    runs: list[dict[str, Any]] = []
    invalid_children: list[str] = []

    def append_text(value: str) -> None:
        if not value:
            return
        if runs and runs[-1]["kind"] == "text":
            runs[-1]["text"] += value
            runs[-1]["text_sha256"] = _sha256_text(runs[-1]["text"])
        else:
            runs.append({"kind": "text", "text": value, "text_sha256": _sha256_text(value)})

    for child in paragraph:
        if child.tag in {_qn(A_NS, "pPr"), _qn(A_NS, "endParaRPr")}:
            continue
        if child.tag == _qn(A_NS, "r"):
            append_text("".join(child.xpath("./a:t/text()", namespaces=NS)))
            continue
        if child.tag == _qn(A_NS, "br"):
            append_text("\n")
            continue
        if child.tag == _qn(A14_NS, "m"):
            runs.append({"kind": "math", "wrapper": child})
            continue
        invalid_children.append(etree.QName(child).localname)
    return runs, invalid_children


def _nonvisual_properties(shape: etree._Element) -> etree._Element | None:
    for expression in (
        "./p:nvSpPr/p:cNvPr",
        "./p:nvPicPr/p:cNvPr",
        "./p:nvGraphicFramePr/p:cNvPr",
        "./p:nvGrpSpPr/p:cNvPr",
    ):
        node = shape.find(expression, namespaces=NS)
        if node is not None:
            return node
    return None


def _generic_shape_name(shape: etree._Element) -> str:
    properties = _nonvisual_properties(shape)
    return "" if properties is None else str(properties.get("name", ""))


def _generic_shape_description(shape: etree._Element) -> str:
    properties = _nonvisual_properties(shape)
    return "" if properties is None else str(properties.get("descr", ""))


def _shape_geometry(shape: etree._Element) -> dict[str, int] | None:
    transform = shape.find("./p:spPr/a:xfrm", namespaces=NS)
    if transform is None:
        transform = shape.find("./p:xfrm", namespaces=NS)
    if transform is None:
        return None
    offset = transform.find("a:off", namespaces=NS)
    extent = transform.find("a:ext", namespaces=NS)
    if offset is None or extent is None:
        return None
    try:
        return {
            "x": int(offset.get("x", "")),
            "y": int(offset.get("y", "")),
            "cx": int(extent.get("cx", "")),
            "cy": int(extent.get("cy", "")),
        }
    except ValueError:
        return None


def _intersection_ratio(subject: Mapping[str, int], other: Mapping[str, int]) -> float:
    subject_area = subject["cx"] * subject["cy"]
    if subject_area <= 0:
        return 0.0
    left = max(subject["x"], other["x"])
    top = max(subject["y"], other["y"])
    right = min(subject["x"] + subject["cx"], other["x"] + other["cx"])
    bottom = min(subject["y"] + subject["cy"], other["y"] + other["cy"])
    return max(0, right - left) * max(0, bottom - top) / subject_area


def _is_explicitly_hidden(shape: etree._Element) -> bool:
    properties = _nonvisual_properties(shape)
    if properties is None:
        return True
    return str(properties.get("hidden", "0")).casefold() in {"1", "true", "on"}


def _has_nearly_transparent_text(shape: etree._Element) -> bool:
    for alpha in shape.xpath(
        ".//a:rPr//a:alpha|.//a:defRPr//a:alpha|.//a:endParaRPr//a:alpha",
        namespaces=NS,
    ):
        try:
            if int(alpha.get("val", "100000")) < 5000:
                return True
        except ValueError:
            return True
    return False


FORMULA_TEXT_PATTERNS = (
    re.compile(r"\\(?:frac|sqrt|sum|prod|int|alpha|beta|gamma|theta|mathrm|mathbf|mathcal)\b"),
    re.compile(r"(?:\\\(|\\\[|\$\$?).+(?:\\\)|\\\]|\$\$?)"),
    re.compile(r"[A-Za-z\u0370-\u03ff0-9]\s*(?:=|\u2260|\u2248|\u2264|\u2265|<|>)\s*[A-Za-z\u0370-\u03ff0-9]"),
    re.compile(r"[A-Za-z0-9)]\s*[\^_]\s*[{(]?[A-Za-z0-9]"),
    re.compile(r"[\u2211\u220f\u222b\u221a\u2202\u2207\u221e]\s*[A-Za-z0-9({]"),
    re.compile(r"[A-Za-z0-9][\u00b2\u00b3\u2070-\u209f]"),
    re.compile(r"^\s*[A-Za-z\u0370-\u03ff](?:\s*[_^]\s*[{(]?[A-Za-z0-9]+[})]?)?\s*$"),
)


def _looks_like_formula_text(value: str) -> bool:
    compact = " ".join(value.split())
    return bool(compact) and any(pattern.search(compact) for pattern in FORMULA_TEXT_PATTERNS)


def _slide_size(package: zipfile.ZipFile) -> tuple[int, int]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    root = etree.fromstring(package.read("ppt/presentation.xml"), parser=parser)
    node = root.find("p:sldSz", namespaces=NS)
    if node is None:
        raise NativeMathError("presentation has no p:sldSz canvas definition")
    try:
        cx, cy = int(node.get("cx", "")), int(node.get("cy", ""))
    except ValueError as exc:
        raise NativeMathError("presentation p:sldSz is invalid") from exc
    if cx <= 0 or cy <= 0:
        raise NativeMathError("presentation canvas must have positive dimensions")
    return cx, cy


def _static_formula_visibility_findings(
    shape: etree._Element,
    *,
    slide_index: int,
    shape_name: str,
    canvas: tuple[int, int],
) -> tuple[dict[str, int] | None, list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    geometry = _shape_geometry(shape)
    if geometry is None:
        findings.append(
            {"code": "NATIVE_MATH_GEOMETRY_MISSING", "slide_index": slide_index, "shape": shape_name}
        )
    else:
        canvas_width, canvas_height = canvas
        if (
            geometry["cx"] <= 0
            or geometry["cy"] <= 0
            or geometry["x"] < 0
            or geometry["y"] < 0
            or geometry["x"] + geometry["cx"] > canvas_width
            or geometry["y"] + geometry["cy"] > canvas_height
        ):
            findings.append(
                {
                    "code": "NATIVE_MATH_OUTSIDE_CANVAS",
                    "slide_index": slide_index,
                    "shape": shape_name,
                    "geometry": geometry,
                    "canvas": {"cx": canvas_width, "cy": canvas_height},
                }
            )
    if _is_explicitly_hidden(shape):
        findings.append(
            {"code": "NATIVE_MATH_HIDDEN", "slide_index": slide_index, "shape": shape_name}
        )
    if _has_nearly_transparent_text(shape):
        findings.append(
            {
                "code": "NATIVE_MATH_TEXT_TRANSPARENT",
                "slide_index": slide_index,
                "shape": shape_name,
            }
        )
    if shape.xpath("ancestor::p:grpSp", namespaces=NS):
        findings.append(
            {
                "code": "NATIVE_MATH_GROUPED_VISIBILITY_UNVERIFIABLE",
                "slide_index": slide_index,
                "shape": shape_name,
            }
        )
    return geometry, findings


def _slide_masquerade_findings(
    root: etree._Element,
    *,
    slide_index: int,
    formula_geometry: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    if not formula_geometry:
        return findings, inventory
    suspicious_name = re.compile(r"(?:formula|equation|latex|math|omml)", re.IGNORECASE)

    for background in root.xpath("./p:cSld/p:bg//a:blipFill|./p:cSld/p:bg//a:blip", namespaces=NS):
        row = {
            "kind": "slide_background_picture",
            "slide_index": slide_index,
            "element": etree.QName(background).localname,
        }
        inventory.append(row)
        findings.append({"code": "SLIDE_BACKGROUND_PICTURE_MASQUERADE_RISK", **row})

    ole_nodes = root.xpath(".//p:oleObj", namespaces=NS)
    for ole in ole_nodes:
        owner = ole.xpath("ancestor::p:graphicFrame[1]", namespaces=NS)
        owner_shape = owner[0] if owner else ole
        row = {
            "kind": "ole",
            "slide_index": slide_index,
            "shape_name": _generic_shape_name(owner_shape),
        }
        inventory.append(row)
        findings.append({"code": "OLE_OBJECT_MASQUERADE_RISK", **row})

    for picture in root.xpath(".//p:pic[not(ancestor::mc:AlternateContent)]", namespaces=NS):
        name = _generic_shape_name(picture)
        description = _generic_shape_description(picture)
        geometry = _shape_geometry(picture)
        overlaps = [
            {
                "shape": str(item["shape"]),
                "ratio": round(_intersection_ratio(item["geometry"], geometry), 6),
            }
            for item in formula_geometry
            if geometry is not None
            and isinstance(item.get("geometry"), Mapping)
            and _intersection_ratio(item["geometry"], geometry) >= 0.005
        ]
        row = {
            "kind": "picture",
            "slide_index": slide_index,
            "shape_name": name,
            "description": description,
            "geometry": geometry,
            "formula_overlaps": overlaps,
        }
        inventory.append(row)
        if overlaps or suspicious_name.search(f"{name} {description}"):
            findings.append({"code": "PICTURE_FORMULA_MASQUERADE_RISK", **row})

    for shape in root.xpath(".//p:sp[not(ancestor::mc:AlternateContent)]", namespaces=NS):
        text_value = "".join(shape.xpath("./p:txBody//a:t/text()", namespaces=NS))
        if not _looks_like_formula_text(text_value):
            continue
        row = {
            "kind": "plain_text_formula_candidate",
            "slide_index": slide_index,
            "shape_name": _shape_name(shape) or "",
            "text": text_value,
            "text_sha256": _sha256_text(text_value),
            "geometry": _shape_geometry(shape),
        }
        inventory.append(row)
        findings.append({"code": "PLAIN_TEXT_FORMULA_MASQUERADE_RISK", **row})

    shape_tree = root.find("./p:cSld/p:spTree", namespaces=NS)
    if shape_tree is not None:
        top_level = list(shape_tree)
        for formula in formula_geometry:
            target = formula.get("target")
            geometry = formula.get("geometry")
            if target not in top_level or not isinstance(geometry, Mapping):
                continue
            formula_index = top_level.index(target)
            for candidate in top_level[formula_index + 1 :]:
                candidate_geometry = _shape_geometry(candidate)
                if candidate_geometry is None or _is_explicitly_hidden(candidate):
                    continue
                ratio = _intersection_ratio(geometry, candidate_geometry)
                if ratio < 0.005:
                    continue
                candidate_kind = etree.QName(candidate).localname
                has_opaque_fill = bool(
                    candidate.xpath("./p:spPr/a:solidFill|./p:grpSpPr/a:solidFill", namespaces=NS)
                )
                is_cover = candidate_kind in {"pic", "graphicFrame", "grpSp"} or has_opaque_fill
                if is_cover:
                    findings.append(
                        {
                            "code": "NATIVE_MATH_ZORDER_COVER_RISK",
                            "slide_index": slide_index,
                            "shape": formula["shape"],
                            "cover_shape": _generic_shape_name(candidate),
                            "cover_kind": candidate_kind,
                            "overlap_ratio": round(ratio, 6),
                        }
                    )
    return findings, inventory


def _template_masquerade_findings(
    package: zipfile.ZipFile,
    formula_geometry: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Inventory content outside ``Slide.Shapes`` for runtime-visible review.

    PowerPoint renders slide masters, custom layouts, and background picture
    fills, but none of those objects appear in ``Slide.Shapes``.  They must be
    scanned independently or they can visually replace a hidden native shape.
    """
    findings: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    suspicious_name = re.compile(r"(?:formula|equation|latex|math|omml)", re.IGNORECASE)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    template_parts = sorted(
        name
        for name in package.namelist()
        if re.fullmatch(r"ppt/(?:slideMasters|slideLayouts)/[^/]+\.xml", name)
    )
    for part_name in template_parts:
        root = etree.fromstring(package.read(part_name), parser=parser)
        for picture in root.xpath(".//p:pic", namespaces=NS):
            geometry = _shape_geometry(picture)
            name = _generic_shape_name(picture)
            description = _generic_shape_description(picture)
            overlaps = [
                {
                    "slide_index": int(item["slide_index"]),
                    "shape": str(item["shape"]),
                    "ratio": round(_intersection_ratio(item["geometry"], geometry), 6),
                }
                for item in formula_geometry
                if geometry is not None
                and isinstance(item.get("geometry"), Mapping)
                and _intersection_ratio(item["geometry"], geometry) >= 0.05
            ]
            row = {
                "kind": "template_picture",
                "part": part_name,
                "shape_name": name,
                "description": description,
                "geometry": geometry,
                "formula_overlaps": overlaps,
            }
            inventory.append(row)
            row["suspicious_name"] = bool(suspicious_name.search(f"{name} {description}"))
        for ole in root.xpath(".//p:oleObj", namespaces=NS):
            row = {"kind": "template_ole", "part": part_name}
            inventory.append(row)
        for background in root.xpath(".//p:bg//a:blipFill|.//p:bg//a:blip", namespaces=NS):
            row = {
                "kind": "template_background_picture",
                "part": part_name,
                "element": etree.QName(background).localname,
            }
            inventory.append(row)
        for shape in root.xpath(".//p:sp", namespaces=NS):
            text_value = "".join(shape.xpath("./p:txBody//a:t/text()", namespaces=NS))
            if not _looks_like_formula_text(text_value):
                continue
            row = {
                "kind": "template_plain_text_formula_candidate",
                "part": part_name,
                "shape_name": _shape_name(shape) or "",
                "text": text_value,
                "text_sha256": _sha256_text(text_value),
            }
            inventory.append(row)
    return findings, inventory


def audit_pptx(
    pptx_path: str | os.PathLike[str],
    expected_plan: str | os.PathLike[str] | None = None,
    roundtrip_receipt: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    path = Path(pptx_path).expanduser().resolve()
    if not path.is_file():
        raise NativeMathError(f"PPTX does not exist: {path}")
    plan_path = Path(expected_plan).expanduser().resolve() if expected_plan else None
    expected = _expected_from_plan(plan_path) if plan_path else {}
    seen: dict[tuple[int, str], list[str]] = {}
    findings: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    masquerade_inventory: list[dict[str, Any]] = []
    all_formula_geometry: list[dict[str, Any]] = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    with zipfile.ZipFile(path, "r") as package:
        bad_part = package.testzip()
        if bad_part:
            findings.append({"code": "PPTX_ZIP_CORRUPT", "part": bad_part})
        findings.extend(_opc_findings(package))
        canvas = _slide_size(package)
        for slide_index, part_name in enumerate(_ordered_slide_parts(package), start=1):
            root = etree.fromstring(package.read(part_name), parser=parser)
            formula_geometry: list[dict[str, Any]] = []
            for alternate in root.xpath(".//mc:AlternateContent", namespaces=NS):
                choices = alternate.xpath("./mc:Choice[@Requires='a14']/p:sp", namespaces=NS)
                if len(choices) != 1:
                    continue
                choice_shape = choices[0]
                shape_name = _shape_name(choice_shape) or ""
                all_math = choice_shape.xpath(".//a14:m", namespaces=NS)
                if not all_math:
                    continue
                geometry, visibility_findings = _static_formula_visibility_findings(
                    choice_shape,
                    slide_index=slide_index,
                    shape_name=shape_name,
                    canvas=canvas,
                )
                findings.extend(visibility_findings)
                formula_geometry.append(
                    {
                        "shape": shape_name,
                        "geometry": geometry,
                        "target": alternate,
                    }
                )
                all_formula_geometry.append(
                    {
                        "slide_index": slide_index,
                        "shape": shape_name,
                        "geometry": geometry,
                    }
                )
                key = (slide_index, shape_name)
                expected_shape = expected.get(key)
                expected_runs = (
                    expected_shape["content_runs"] if expected_shape is not None else []
                )
                paragraphs = choice_shape.xpath("./p:txBody/a:p", namespaces=NS)
                direct_math = choice_shape.xpath("./p:txBody/a:p/a14:m", namespaces=NS)
                if len(paragraphs) != 1 or len(direct_math) != len(all_math):
                    findings.append(
                        {
                            "code": "NATIVE_MATH_INVALID_OOXML_LOCATION",
                            "slide_index": slide_index,
                            "shape": shape_name,
                            "paragraph_count": len(paragraphs),
                            "direct_math_count": len(direct_math),
                            "descendant_math_count": len(all_math),
                        }
                    )
                actual_runs: list[dict[str, Any]] = []
                invalid_children: list[str] = []
                if len(paragraphs) == 1:
                    actual_runs, invalid_children = _paragraph_content_runs(paragraphs[0])
                if invalid_children:
                    findings.append(
                        {
                            "code": "NATIVE_MATH_PARAGRAPH_CHILD_INVALID",
                            "slide_index": slide_index,
                            "shape": shape_name,
                            "children": invalid_children,
                        }
                    )

                metadata = _decode_metadata(_shape_description(choice_shape))
                metadata_formulas: list[Mapping[str, Any]] = []
                metadata_runs: list[Mapping[str, Any]] = []
                if metadata is None:
                    findings.append(
                        {
                            "code": "NATIVE_MATH_METADATA_MISSING",
                            "slide_index": slide_index,
                            "shape": shape_name,
                        }
                    )
                else:
                    raw_formulas = metadata.get("formulas")
                    raw_runs = metadata.get("content_runs")
                    if isinstance(raw_formulas, list):
                        metadata_formulas = [
                            item for item in raw_formulas if isinstance(item, Mapping)
                        ]
                    if isinstance(raw_runs, list):
                        metadata_runs = [item for item in raw_runs if isinstance(item, Mapping)]

                expected_math = [run for run in expected_runs if run["kind"] == "math"]
                actual_math = [run for run in actual_runs if run["kind"] == "math"]
                if len(actual_math) != len(expected_math):
                    findings.append(
                        {
                            "code": "NATIVE_MATH_COUNT_MISMATCH",
                            "slide_index": slide_index,
                            "shape": shape_name,
                            "expected": len(expected_math),
                            "actual": len(actual_math),
                        }
                    )
                formula_ids: list[str] = []
                formula_rows: list[dict[str, Any]] = []
                for index, actual_math_run in enumerate(actual_math):
                    wrapper = actual_math_run["wrapper"]
                    roots = [child for child in wrapper if etree.QName(child).namespace == M_NS]
                    expected_formula = expected_math[index] if index < len(expected_math) else None
                    meta = metadata_formulas[index] if index < len(metadata_formulas) else {}
                    formula_id = str(
                        (expected_formula or {}).get("formula_id", meta.get("formula_id", ""))
                    )
                    if (
                        len(roots) != 1
                        or etree.QName(roots[0]).localname not in {"oMath", "oMathPara"}
                        or not roots[0].xpath(".//m:t[string-length(text()) > 0]", namespaces=NS)
                    ):
                        findings.append(
                            {
                                "code": "NATIVE_OMML_ROOT_INVALID",
                                "slide_index": slide_index,
                                "shape": shape_name,
                                "formula_id": formula_id,
                                "index": index,
                            }
                        )
                        continue
                    root_name = etree.QName(roots[0]).localname
                    omml_hash = _sha256_bytes(_canonical_xml(roots[0]))
                    projected_root = roots[0]
                    powerpoint_mode_normalized = False
                    if expected_formula is not None:
                        projected_root, powerpoint_mode_normalized = (
                            _project_powerpoint_normalized_inline_root(
                                roots[0], str(expected_formula["mode"])
                            )
                        )
                    semantic_omml_hash = _semantic_omml_sha256(projected_root)
                    storage_semantic_omml_hash = _semantic_omml_sha256(roots[0])
                    if expected_formula is None:
                        findings.append(
                            {
                                "code": "NATIVE_FORMULA_HAS_NO_EXTERNAL_EXPECTATION",
                                "slide_index": slide_index,
                                "shape": shape_name,
                                "formula_id": formula_id,
                            }
                        )
                    else:
                        expected_root = (
                            "oMathPara" if expected_formula["mode"] == "display" else "oMath"
                        )
                        if root_name != expected_root and not powerpoint_mode_normalized:
                            findings.append(
                                {
                                    "code": "NATIVE_FORMULA_MODE_ROOT_MISMATCH",
                                    "slide_index": slide_index,
                                    "shape": shape_name,
                                    "formula_id": formula_id,
                                    "expected": f"m:{expected_root}",
                                    "actual": f"m:{root_name}",
                                }
                            )
                        if semantic_omml_hash != expected_formula["semantic_omml_sha256"]:
                            findings.append(
                                {
                                    "code": "NATIVE_SEMANTIC_OMML_HASH_MISMATCH",
                                    "slide_index": slide_index,
                                    "shape": shape_name,
                                    "formula_id": formula_id,
                                    "actual": semantic_omml_hash,
                                    "expected": expected_formula["semantic_omml_sha256"],
                                }
                            )
                        binding_keys = (
                            "formula_id",
                            "mode",
                            "canonical_latex",
                            "latex_sha256",
                            "omml_sha256",
                            "semantic_omml_profile",
                            "semantic_omml_sha256",
                        )
                        metadata_mismatches = {
                            field: {
                                "expected": expected_formula[field],
                                "actual": meta.get(field),
                            }
                            for field in binding_keys
                            if meta.get(field) != expected_formula[field]
                        }
                        if metadata_mismatches:
                            findings.append(
                                {
                                    "code": "NATIVE_METADATA_BINDING_MISMATCH",
                                    "slide_index": slide_index,
                                    "shape": shape_name,
                                    "formula_id": formula_id,
                                    "mismatches": metadata_mismatches,
                                }
                            )
                    formula_ids.append(formula_id)
                    formula_rows.append(
                        {
                            "formula_id": formula_id,
                            "mode": None if expected_formula is None else expected_formula["mode"],
                            "latex_sha256": (
                                None if expected_formula is None else expected_formula["latex_sha256"]
                            ),
                            "omml_sha256": omml_hash,
                            "compiled_omml_sha256": (
                                None if expected_formula is None else expected_formula["omml_sha256"]
                            ),
                            "powerpoint_normalized": (
                                expected_formula is not None
                                and omml_hash != expected_formula["omml_sha256"]
                            ),
                            "powerpoint_mode_normalized": powerpoint_mode_normalized,
                            "semantic_omml_profile": SEMANTIC_OMML_PROFILE,
                            "semantic_omml_sha256": semantic_omml_hash,
                            "storage_semantic_omml_sha256": storage_semantic_omml_hash,
                            "root": f"m:{root_name}",
                        }
                    )

                actual_signature: list[dict[str, Any]] = []
                math_index = 0
                for run in actual_runs:
                    if run["kind"] == "text":
                        actual_signature.append(
                            {
                                "kind": "text",
                                "text": run["text"],
                                "text_sha256": run["text_sha256"],
                            }
                        )
                    else:
                        formula_id = formula_ids[math_index] if math_index < len(formula_ids) else ""
                        actual_signature.append({"kind": "math", "formula_id": formula_id})
                        math_index += 1
                expected_signature = [
                    (
                        {
                            "kind": "text",
                            "text": run["text"],
                            "text_sha256": run["text_sha256"],
                        }
                        if run["kind"] == "text"
                        else {"kind": "math", "formula_id": run["formula_id"]}
                    )
                    for run in expected_runs
                ]
                if actual_signature != expected_signature:
                    findings.append(
                        {
                            "code": "NATIVE_CONTENT_RUN_SEQUENCE_MISMATCH",
                            "slide_index": slide_index,
                            "shape": shape_name,
                            "expected": expected_signature,
                            "actual": actual_signature,
                        }
                    )
                metadata_signature = [
                    (
                        {
                            "kind": "text",
                            "text": run.get("text"),
                            "text_sha256": run.get("text_sha256"),
                        }
                        if run.get("kind") == "text"
                        else {"kind": "math", "formula_id": run.get("formula_id")}
                    )
                    for run in metadata_runs
                ]
                if metadata_signature != expected_signature:
                    findings.append(
                        {
                            "code": "NATIVE_CONTENT_METADATA_MISMATCH",
                            "slide_index": slide_index,
                            "shape": shape_name,
                        }
                    )

                fallback = alternate.find("mc:Fallback", namespaces=NS)
                fallback_shapes = [] if fallback is None else fallback.xpath("./p:sp", namespaces=NS)
                fallback_valid = fallback is not None and len(fallback_shapes) == 1
                fallback_metadata_normalized = False
                fallback_diagnostics: dict[str, Any] = {
                    "fallback_present": fallback is not None,
                    "shape_count": len(fallback_shapes),
                }
                if fallback_valid:
                    fallback_shape = fallback_shapes[0]
                    fallback_description = _shape_description(fallback_shape)
                    fallback_metadata = _decode_metadata(
                        fallback_description, prefix=FALLBACK_PREFIX
                    )
                    if fallback_metadata is None:
                        # PowerPoint copies the active Choice description into the
                        # fallback while saving a styled MathZone.  Accept only that
                        # exact, observed normalization: the metadata must still equal
                        # the Choice and the fallback must remain plain DrawingML text.
                        fallback_metadata = _decode_metadata(fallback_description)
                        fallback_metadata_normalized = fallback_metadata is not None
                    forbidden_fallback_content = fallback.xpath(
                        ".//p:pic|.//a:blip|.//p:oleObj|.//a14:m|.//m:oMath|.//m:oMathPara",
                        namespaces=NS,
                    )
                    fallback_paragraphs = fallback_shape.xpath(
                        "./p:txBody/a:p", namespaces=NS
                    )
                    fallback_text = "".join(
                        fallback_shape.xpath("./p:txBody/a:p//a:t/text()", namespaces=NS)
                    )
                    expected_fallback_text = "".join(
                        (
                            str(run["text"])
                            if run["kind"] == "text"
                            else (
                                ("\\[" if run["mode"] == "display" else "\\(")
                                + str(run["canonical_latex"])
                                + ("\\]" if run["mode"] == "display" else "\\)")
                            )
                        )
                        for run in expected_runs
                    )
                    fallback_text_matches = (
                        expected_shape is None
                        or fallback_text == expected_fallback_text
                        or (
                            fallback_metadata_normalized
                            and not fallback_text.strip(" \t\r\n\u00a0")
                        )
                    )
                    fallback_valid = (
                        not forbidden_fallback_content
                        and len(fallback_paragraphs) == 1
                        and fallback_metadata == metadata
                        and fallback_text_matches
                    )
                    fallback_diagnostics.update(
                        {
                            "description_prefix": (
                                META_PREFIX
                                if fallback_description.startswith(META_PREFIX)
                                else (
                                    FALLBACK_PREFIX
                                    if fallback_description.startswith(FALLBACK_PREFIX)
                                    else "UNRECOGNIZED"
                                )
                            ),
                            "metadata_equal": fallback_metadata == metadata,
                            "forbidden_content_count": len(forbidden_fallback_content),
                            "forbidden_content_tags": [
                                etree.QName(node).localname
                                for node in forbidden_fallback_content
                            ],
                            "paragraph_count": len(fallback_paragraphs),
                            "text_sha256": _sha256_text(fallback_text),
                            "text_is_normalized_blank": not fallback_text.strip(
                                " \t\r\n\u00a0"
                            ),
                            "text_matches_expected": fallback_text == expected_fallback_text,
                        }
                    )
                if not fallback_valid:
                    findings.append(
                        {
                            "code": "NATIVE_MATH_FALLBACK_INVALID",
                            "slide_index": slide_index,
                            "shape": shape_name,
                            "diagnostics": fallback_diagnostics,
                        }
                    )
                seen[key] = formula_ids
                inventory.append(
                    {
                        "slide_index": slide_index,
                        "shape_name": shape_name,
                        "native_kind": "office_math",
                        "wrapper": "a14:m",
                        "geometry": geometry,
                        "content_runs": actual_signature,
                        "formulas": formula_rows,
                        "powerpoint_fallback_metadata_normalized": (
                            fallback_metadata_normalized
                        ),
                    }
                )
            masquerade_findings, masquerade_rows = _slide_masquerade_findings(
                root,
                slide_index=slide_index,
                formula_geometry=formula_geometry,
            )
            findings.extend(masquerade_findings)
            masquerade_inventory.extend(masquerade_rows)
        if inventory:
            template_findings, template_rows = _template_masquerade_findings(
                package, all_formula_geometry
            )
            findings.extend(template_findings)
            masquerade_inventory.extend(template_rows)
    if not inventory:
        findings.append({"code": "NO_NATIVE_OFFICE_MATH"})
    elif not expected:
        findings.append({"code": "EXPECTED_PLAN_REQUIRED"})
    for key, expected_shape in expected.items():
        expected_ids = expected_shape["formula_ids"]
        actual_ids = seen.get(key)
        if actual_ids != expected_ids:
            findings.append(
                {
                    "code": "EXPECTED_NATIVE_MATH_MISSING_OR_CHANGED",
                    "slide_index": key[0],
                    "shape": key[1],
                    "expected_formula_ids": expected_ids,
                    "actual_formula_ids": actual_ids,
                }
            )
    unexpected = sorted(set(seen).difference(expected)) if expected else []
    roundtrip_record: dict[str, Any] | None = None
    if roundtrip_receipt is not None:
        receipt_path = Path(roundtrip_receipt).expanduser().resolve()
        if not receipt_path.is_file():
            findings.append({"code": "DETACHED_ROUNDTRIP_RECEIPT_MISSING", "path": str(receipt_path)})
        else:
            roundtrip_record = {
                "path": str(receipt_path),
                "sha256": _sha256_file(receipt_path),
                "trusted_for_final_pass": False,
                "reason": "Detached JSON cannot prove a fresh PowerPoint COM transaction.",
            }
    status = "FAIL" if findings else "STRUCTURE_PASS_REQUIRES_POWERPOINT_FINALIZE"
    return {
        "document_type": AUDIT_TYPE,
        "schema_version": "1.1",
        "status": status,
        "pptx_path": str(path),
        "pptx_sha256": _sha256_file(path),
        "expected_plan_path": str(Path(expected_plan).resolve()) if expected_plan else None,
        "expected_plan_sha256": _sha256_file(Path(expected_plan).resolve()) if expected_plan else None,
        "powerpoint_roundtrip": roundtrip_record,
        "native_formula_count": sum(len(item["formulas"]) for item in inventory),
        "native_shape_count": len(inventory),
        "inventory": inventory,
        "masquerade_inventory": masquerade_inventory,
        "findings": findings,
        "unexpected_native_shapes": [
            {"slide_index": slide, "shape_name": shape} for slide, shape in unexpected
        ],
    }


def _powershell_executable() -> str | None:
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        return None
    executable = (
        Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    ).resolve()
    return str(executable) if executable.is_file() else None


def finalize_native_math(
    input_pptx: str | os.PathLike[str],
    plan_path: str | os.PathLike[str],
    injection_report_path: str | os.PathLike[str],
    output_pptx: str | os.PathLike[str],
    roundtrip_receipt_path: str | os.PathLike[str],
    render_directory: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    timeout_seconds: int = 240,
    audit_profile: str = "standard",
) -> dict[str, Any]:
    """Run the only transaction that can produce a final native-math PASS.

    A detached receipt is never accepted here.  This process creates a random
    challenge, starts one PowerShell child, verifies that exact child PID and
    challenge, and immediately audits its output and fresh slide renders.
    """
    input_path = Path(input_pptx).expanduser().resolve()
    plan_file = Path(plan_path).expanduser().resolve()
    injection_report = Path(injection_report_path).expanduser().resolve()
    output_path = _validated_output(output_pptx)
    receipt_path = _validated_output(roundtrip_receipt_path)
    render_dir = _validated_output(render_directory)
    if overwrite:
        raise NativeMathError(
            "fresh finalization refuses overwrite; use a new run_id and new evidence paths"
        )
    if audit_profile not in {"standard", "strict"}:
        raise NativeMathError("audit_profile must be standard or strict")
    for candidate, label in (
        (input_path, "injected input PPTX"),
        (plan_file, "expected plan"),
        (injection_report, "injection report"),
    ):
        if not candidate.is_file():
            raise NativeMathError(f"{label} does not exist: {candidate}")
    if input_path.suffix.casefold() != ".pptx" or output_path.suffix.casefold() != ".pptx":
        raise NativeMathError("finalize input and output must both be .pptx")
    if input_path == output_path:
        raise NativeMathError("finalize output must differ from the injected input")
    if render_dir == Path(render_dir.anchor) or any(
        candidate == render_dir or render_dir in candidate.parents
        for candidate in (input_path, plan_file, injection_report, output_path, receipt_path)
    ):
        raise NativeMathError("render_directory must be a dedicated child directory")
    for candidate, label in (
        (output_path, "roundtripped output"),
        (receipt_path, "roundtrip receipt"),
        (render_dir, "render directory"),
    ):
        if candidate.exists():
            raise NativeMathError(f"{label} already exists; finalization requires a fresh path: {candidate}")

    expected = _expected_from_plan(plan_file)
    injection_record, injection_findings = _validate_injection_report(
        injection_report,
        input_pptx=input_path,
        plan_path=plan_file,
        expected=expected,
    )
    structural_input = audit_pptx(input_path, plan_file)
    preflight_findings = [*injection_findings]
    if structural_input["status"] == "FAIL":
        preflight_findings.append(
            {
                "code": "INJECTED_INPUT_STRUCTURE_FAILED",
                "findings": structural_input["findings"],
            }
        )
    if preflight_findings:
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": "FAIL",
            "phase": "pre_powerpoint",
            "input_pptx": str(input_path),
            "input_sha256": _sha256_file(input_path),
            "expected_plan_path": str(plan_file),
            "expected_plan_sha256": _sha256_file(plan_file),
            "injection_report": injection_record,
            "structural_input_audit": structural_input,
            "findings": preflight_findings,
        }

    source_path = Path(str(injection_record["source_pptx"])).expanduser().resolve()
    temporary_reinjection = output_path.with_name(
        f".{output_path.stem}.{secrets.token_hex(12)}.fresh-injected.pptx"
    )
    try:
        fresh_injection = inject_plan(source_path, plan_file, temporary_reinjection)
        fresh_hash = _sha256_file(temporary_reinjection)
    finally:
        temporary_reinjection.unlink(missing_ok=True)
    declared_input_hash = _sha256_file(input_path)
    if fresh_hash != declared_input_hash:
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": "FAIL",
            "phase": "fresh_reinjection",
            "findings": [
                {
                    "code": "FRESH_REINJECTION_HASH_MISMATCH",
                    "declared_input_sha256": declared_input_hash,
                    "fresh_reinjection_sha256": fresh_hash,
                }
            ],
        }
    injection_record["fresh_reinjection"] = {
        "performed_in_current_process": True,
        "sha256": fresh_hash,
        "operations": fresh_injection["operations"],
    }

    powershell = _powershell_executable()
    if powershell is None:
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": "INCONCLUSIVE",
            "phase": "powerpoint_runtime",
            "findings": [{"code": "POWERSHELL_NOT_AVAILABLE"}],
        }
    script_path = Path(__file__).with_name("powerpoint_native_math_roundtrip.ps1").resolve()
    if not script_path.is_file():
        raise NativeMathError(f"PowerPoint roundtrip script is missing: {script_path}")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_receipt = receipt_path.with_name(
        f".{receipt_path.name}.{secrets.token_hex(12)}.runtime.tmp"
    )
    challenge = secrets.token_hex(FINALIZATION_CHALLENGE_BYTES)
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-InputPath",
        str(input_path),
        "-OutputPath",
        str(output_path),
        "-ExpectedPlanPath",
        str(plan_file),
        "-InjectionReportPath",
        str(injection_report),
        "-ReceiptPath",
        str(temporary_receipt),
        "-RenderDirectory",
        str(render_dir),
        "-Challenge",
        challenge,
        "-ParentProcessId",
        str(os.getpid()),
        "-AuditProfile",
        audit_profile,
    ]
    child = subprocess.Popen(  # noqa: S603 - fixed local PowerShell helper and argv list.
        command,
        cwd=script_path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        temporary_receipt.unlink(missing_ok=True)
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": "INCONCLUSIVE",
            "phase": "powerpoint_runtime",
            "findings": [
                {
                    "code": "POWERPOINT_FINALIZE_TIMEOUT",
                    "timeout_seconds": timeout_seconds,
                    "stderr": stderr[-2000:],
                }
            ],
        }

    if child.returncode != 0 or not temporary_receipt.is_file():
        runtime_payload: dict[str, Any] | None = None
        if temporary_receipt.is_file():
            try:
                runtime_payload = _load_json_object(
                    temporary_receipt, label="failed PowerPoint runtime receipt"
                )
            except NativeMathError:
                runtime_payload = None
        temporary_receipt.unlink(missing_ok=True)
        violations = (
            runtime_payload.get("violations")
            if isinstance(runtime_payload, Mapping)
            and isinstance(runtime_payload.get("violations"), list)
            else []
        )
        status = "FAIL" if violations else "INCONCLUSIVE"
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": status,
            "phase": "powerpoint_runtime",
            "child_process_id": child.pid,
            "runtime_receipt": runtime_payload,
            "findings": violations
            or [
                {
                    "code": "POWERPOINT_FINALIZE_NOT_COMPLETED",
                    "returncode": child.returncode,
                    "stdout": stdout[-2000:],
                    "stderr": stderr[-2000:],
                }
            ],
        }

    try:
        direct_stdout_payload = _load_json_text(
            stdout.strip(), label="direct PowerPoint child stdout"
        )
    except NativeMathError as exc:
        temporary_receipt.unlink(missing_ok=True)
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": "FAIL",
            "phase": "powerpoint_runtime",
            "findings": [{"code": "POWERPOINT_DIRECT_STDOUT_INVALID", "error": str(exc)}],
        }
    with zipfile.ZipFile(output_path, "r") as package:
        slide_count = len(_ordered_slide_parts(package))
    runtime_record, runtime_findings = _validate_runtime_roundtrip_receipt(
        temporary_receipt,
        direct_stdout_payload=direct_stdout_payload,
        powershell_executable=Path(powershell).resolve(),
        challenge=challenge,
        powershell_process_id=child.pid,
        input_pptx=input_path,
        output_pptx=output_path,
        plan_path=plan_file,
        injection_report_path=injection_report,
        render_directory=render_dir,
        expected=expected,
        slide_count=slide_count,
        audit_profile=audit_profile,
    )
    structural_output = audit_pptx(output_path, plan_file)
    findings = [*runtime_findings]
    if structural_output["status"] == "FAIL":
        findings.append(
            {
                "code": "ROUNDTRIPPED_OUTPUT_STRUCTURE_FAILED",
                "findings": structural_output["findings"],
            }
        )
    if findings:
        temporary_receipt.unlink(missing_ok=True)
        return {
            "document_type": AUDIT_TYPE,
            "schema_version": "2.0",
            "status": "FAIL",
            "phase": "post_powerpoint",
            "injection_report": injection_record,
            "powerpoint_runtime": runtime_record,
            "structural_output_audit": structural_output,
            "findings": findings,
        }

    os.rename(temporary_receipt, receipt_path)
    runtime_record["path"] = str(receipt_path)
    runtime_record["sha256"] = _sha256_file(receipt_path)
    evidence_binding = {
        "input_sha256": _sha256_file(input_path),
        "output_sha256": _sha256_file(output_path),
        "plan_sha256": _sha256_file(plan_file),
        "injection_report_sha256": _sha256_file(injection_report),
        "roundtrip_receipt_sha256": _sha256_file(receipt_path),
        "roundtrip_script_sha256": _sha256_file(script_path),
        "powershell_executable_sha256": _sha256_file(Path(powershell).resolve()),
        "powerpoint_executable_sha256": runtime_record["powerpoint_executable_sha256"],
        "finalizer_python_sha256": _sha256_file(Path(__file__).resolve()),
        "render_sha256": [
            [item["sha256"], item["verification_sha256"]]
            for item in runtime_record["renders"]
        ],
        "counterfactual_render_sha256": [
            item["sha256"] for item in runtime_record["counterfactual_renders"]
        ],
        "counterfactual_controls": [
            {
                "slide_index": item["slide_index"],
                "shape_name": item["shape_name"],
                "formula_id": item["formula_id"],
                "math_run_index": item["math_run_index"],
                "zone_index": item["zone_index"],
                "zone_start": item["zone_start"],
                "zone_length": item["zone_length"],
                "zone_text_sha256": item["zone_text_sha256"],
                "render_sha256": item["sha256"],
            }
            for item in runtime_record["counterfactual_renders"]
        ],
        "audit_profile": audit_profile,
        "fresh_render_count": 2,
    }
    return {
        "document_type": AUDIT_TYPE,
        "schema_version": "2.0",
        "status": "MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW",
        "phase": "fresh_powerpoint_finalize",
        "input_pptx": str(input_path),
        "output_pptx": str(output_path),
        "expected_plan_path": str(plan_file),
        "injection_report": injection_record,
        "powerpoint_runtime": runtime_record,
        "structural_output_audit": structural_output,
        "evidence_binding": evidence_binding,
        "evidence_binding_sha256": _sha256_bytes(
            json.dumps(evidence_binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "audit_profile": audit_profile,
        "findings": [],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="Compile one canonical LaTeX formula")
    compile_parser.add_argument("--formula-id", required=True)
    compile_parser.add_argument("--latex", required=True)
    compile_parser.add_argument("--mode", choices=("inline", "display"), required=True)
    compile_parser.add_argument("--xsl")
    compile_parser.add_argument("--output", type=Path, required=True)
    compile_parser.add_argument("--overwrite", action="store_true")
    compile_parser.add_argument("--pretty", action="store_true")

    inject_parser = subparsers.add_parser("inject", help="Replace named PPTX placeholders with Office Math")
    inject_parser.add_argument("--input", type=Path, required=True)
    inject_parser.add_argument("--plan", type=Path, required=True)
    inject_parser.add_argument("--output", type=Path, required=True)
    inject_parser.add_argument("--report", type=Path)
    inject_parser.add_argument("--overwrite", action="store_true")
    inject_parser.add_argument("--overwrite-report", action="store_true")
    inject_parser.add_argument("--pretty", action="store_true")

    audit_parser = subparsers.add_parser("audit", help="Audit native Office Math in a PPTX")
    audit_parser.add_argument("--input", type=Path, required=True)
    audit_parser.add_argument("--plan", type=Path)
    audit_parser.add_argument("--roundtrip-receipt", type=Path)
    audit_parser.add_argument("--output", type=Path)
    audit_parser.add_argument("--overwrite", action="store_true")
    audit_parser.add_argument("--pretty", action="store_true")

    finalize_parser = subparsers.add_parser(
        "finalize",
        help="Run fresh PowerPoint save/reopen/MathZones/render evidence and final audit",
    )
    finalize_parser.add_argument("--input", type=Path, required=True)
    finalize_parser.add_argument("--plan", type=Path, required=True)
    finalize_parser.add_argument("--injection-report", type=Path, required=True)
    finalize_parser.add_argument("--output-pptx", type=Path, required=True)
    finalize_parser.add_argument("--roundtrip-receipt", type=Path, required=True)
    finalize_parser.add_argument("--render-directory", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path)
    finalize_parser.add_argument("--timeout-seconds", type=int, default=240)
    finalize_parser.add_argument(
        "--audit-profile", choices=("standard", "strict"), default="standard"
    )
    finalize_parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            compile_output = _validated_output(args.output)
            if compile_output.exists() and not args.overwrite:
                raise NativeMathError(
                    f"compiled receipt output already exists; use --overwrite explicitly: "
                    f"{compile_output}"
                )
            payload = compile_formula(args.formula_id, args.latex, args.mode, args.xsl)
            writer = _atomic_write_json if args.overwrite else _atomic_write_json_fresh
            writer(compile_output, payload, pretty=args.pretty)
        elif args.command == "inject":
            report_output = _validated_output(args.report) if args.report else None
            if report_output and report_output.exists() and not args.overwrite_report:
                raise NativeMathError(
                    f"injection report output already exists; use --overwrite-report explicitly: "
                    f"{report_output}"
                )
            payload = inject_plan(args.input, args.plan, args.output, overwrite=args.overwrite)
            if report_output:
                writer = (
                    _atomic_write_json if args.overwrite_report else _atomic_write_json_fresh
                )
                writer(report_output, payload, pretty=args.pretty)
        elif args.command == "audit":
            audit_output = _validated_output(args.output) if args.output else None
            if audit_output and audit_output.exists() and not args.overwrite:
                raise NativeMathError(
                    f"audit output already exists; use --overwrite explicitly: {audit_output}"
                )
            payload = audit_pptx(args.input, args.plan, args.roundtrip_receipt)
            if audit_output:
                writer = _atomic_write_json if args.overwrite else _atomic_write_json_fresh
                writer(audit_output, payload, pretty=args.pretty)
        else:
            final_audit_output = _validated_output(args.output) if args.output else None
            if final_audit_output and final_audit_output.exists():
                raise NativeMathError(
                    f"final audit output already exists; use a fresh path: {final_audit_output}"
                )
            payload = finalize_native_math(
                args.input,
                args.plan,
                args.injection_report,
                args.output_pptx,
                args.roundtrip_receipt,
                args.render_directory,
                timeout_seconds=args.timeout_seconds,
                audit_profile=args.audit_profile,
            )
            if final_audit_output:
                _atomic_write_json_fresh(final_audit_output, payload, pretty=args.pretty)
        rendered = json.dumps(
            payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True
        )
        try:
            print(rendered)
        except UnicodeEncodeError:
            print(json.dumps(payload, ensure_ascii=True, indent=2 if args.pretty else None, sort_keys=True))
        successful_statuses = {
            "PASS",
            "MECHANICAL_GATE_PASS_REQUIRES_INDEPENDENT_REVIEW",
            "INJECTED_REQUIRES_POWERPOINT_ROUNDTRIP",
            "STRUCTURE_PASS_REQUIRES_POWERPOINT_FINALIZE",
        }
        return 0 if payload.get("status") in successful_statuses else 3
    except NativeMathError as exc:
        print(json.dumps({"status": "INCONCLUSIVE", "error": str(exc)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
