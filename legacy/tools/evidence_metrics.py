#!/usr/bin/env python3
"""Shared pure-function evidence metrics for cross-modal perception fusion.

The logic mirrors the deterministic helpers inside ``paddle_ocr_manifest.py``
(``normalize_text``/``text_similarity``/``bbox_iou``/``bbox_containment``) so
that fusion alignment uses exactly the same scoring basis as OCR deduplication.
That file must not be imported here: it runs under the pinned Paddle
interpreter and its script hash is embedded in existing evidence chains.

Only the Python standard library is used so any pinned interpreter can run the
``--self-test`` without a runtime receipt.
"""

from __future__ import annotations

import argparse
import difflib
import math
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

# Conservative LaTeX normalization for k-sample self-consistency only.  It is
# deliberately syntactic (never semantic): strip spacing macros and \left/\right
# decorations, then remove all whitespace.  Two renderings that differ only by
# spacing are the same proposal; anything deeper stays INCONSISTENT and goes to
# human review.
_LATEX_SPACING_MACRO_RE = re.compile(r"\\[,;!:]\s*|\\qquad|\\quad")
_LATEX_LEFT_RIGHT_RE = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg)\b")


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(character for character in normalized if not character.isspace())


def text_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _as_xwyh(box: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """Accept both ``{x, y, w, h}`` (OCR) and ``{x0, y0, x1, y1}`` (geometry) boxes."""
    if {"x", "y", "w", "h"} <= set(box):
        return (
            float(box["x"]),
            float(box["y"]),
            float(box["w"]),
            float(box["h"]),
        )
    if {"x0", "y0", "x1", "y1"} <= set(box):
        x0, y0 = float(box["x0"]), float(box["y0"])
        return (x0, y0, float(box["x1"]) - x0, float(box["y1"]) - y0)
    raise ValueError(f"Unsupported box coordinate convention: {sorted(box)}")


def box_to_xwyh(box: Mapping[str, Any]) -> dict[str, float]:
    x, y, w, h = _as_xwyh(box)
    return {"x": x, "y": y, "w": w, "h": h}


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def box_to_x0y0x1y1(box: Mapping[str, Any]) -> dict[str, int]:
    """Round to the geometry-manifest HALF_OPEN_X0_Y0_X1_Y1 integer convention."""
    x, y, w, h = _as_xwyh(box)
    return {
        "x0": _round_half_up(x),
        "y0": _round_half_up(y),
        "x1": _round_half_up(x + w),
        "y1": _round_half_up(y + h),
    }


def bbox_list_to_xwyh(box: Sequence[float]) -> dict[str, float]:
    """Convert segmentation ``[x, y, w, h]`` lists to the canonical dict form."""
    if len(box) != 4:
        raise ValueError(f"Segmentation bbox must have four entries: {list(box)}")
    return {"x": float(box[0]), "y": float(box[1]), "w": float(box[2]), "h": float(box[3])}


def bbox_iou(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    ax, ay, aw, ah = _as_xwyh(a)
    bx, by, bw, bh = _as_xwyh(b)
    intersection_w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    intersection_h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    intersection = intersection_w * intersection_h
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def bbox_containment(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    ax, ay, aw, ah = _as_xwyh(a)
    bx, by, bw, bh = _as_xwyh(b)
    intersection_w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    intersection_h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    intersection = intersection_w * intersection_h
    smaller = min(aw * ah, bw * bh)
    return intersection / smaller if smaller > 0 else 0.0


def bbox_overlap_score(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """The OCR-deduplication style spatial score: ``max(IoU, containment)``."""
    return max(bbox_iou(a, b), bbox_containment(a, b))


def normalize_latex_for_consistency(latex: str) -> str:
    text = str(latex)
    text = _LATEX_LEFT_RIGHT_RE.sub("", text)
    text = _LATEX_SPACING_MACRO_RE.sub("", text)
    text = text.replace("{", " { ").replace("}", " } ")
    tokens = [token for token in text.split() if token]
    return "".join(tokens)


def latex_samples_self_consistent(samples: Sequence[str]) -> bool:
    if not samples:
        return False
    normalized = {normalize_latex_for_consistency(sample) for sample in samples}
    return len(normalized) == 1 and "" not in normalized


def _self_test() -> int:
    assert normalize_text("  Hello World ") == "helloworld"
    assert text_similarity("ABC 123", "abc123") == 1.0
    assert text_similarity("abc", "xyz") == 0.0
    assert text_similarity("", "") == 1.0

    a = {"x": 0, "y": 0, "w": 10, "h": 10}
    identical = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert abs(bbox_iou(a, identical) - 1.0) < 1e-9
    inner = {"x": 2, "y": 2, "w": 5, "h": 5}
    assert abs(bbox_containment(inner, a) - 1.0) < 1e-9
    assert abs(bbox_iou(inner, a) - 25.0 / 100.0) < 1e-9
    disjoint = {"x": 20, "y": 20, "w": 5, "h": 5}
    assert bbox_iou(a, disjoint) == 0.0
    assert bbox_overlap_score(inner, a) == 1.0

    listed = bbox_list_to_xwyh([3, 4, 20, 10])
    assert listed == {"x": 3.0, "y": 4.0, "w": 20.0, "h": 10.0}
    converted = box_to_x0y0x1y1({"x": 2.6, "y": 3.4, "w": 10.2, "h": 5.1})
    assert converted == {"x0": 3, "y0": 3, "x1": 13, "y1": 9}

    assert latex_samples_self_consistent(
        [r"\sum_{i=1}^{N} x_i", r"\sum^N_{i=1}x_i "]
    ) is False  # intentionally different: superscript/subscript order is semantic
    assert latex_samples_self_consistent(
        [r"\left(x+y\right)^2", r"(x+y)^2"]
    ) is True
    assert latex_samples_self_consistent(
        [r"\alpha \, \beta", r"\alpha\beta"]
    ) is True
    assert latex_samples_self_consistent(
        [r"\alpha, \beta", r"\alpha\beta"]
    ) is False  # a comma is semantic LaTeX, not spacing
    assert latex_samples_self_consistent([]) is False

    print("evidence_metrics self-test: OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared evidence metric helpers.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in assertions.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    build_parser().error("nothing to do; pass --self-test")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
