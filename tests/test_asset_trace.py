"""asset_trace 描摹资格分类、vtracer 描摹与合同子集校验的 case-neutral 测试。"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.assets.asset_trace import (
    TRACE_ELIGIBILITY_CLASSES,
    VTRACER_DEFAULT_MODE,
    VTRACER_LOCKED_PARAMETERS,
    AssetTraceError,
    check_svg_contract_subset,
    compute_trace_eligibility,
    run_vtracer_trace,
)

_SVG_OPEN = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8" viewBox="0 0 8 8">'
)


def _flat_blocks(size: int = 64) -> np.ndarray:
    image = np.full((size, size, 3), 245, np.uint8)
    image[8:28, 8:28] = (200, 60, 50)
    image[8:28, 36:56] = (60, 130, 200)
    image[36:56, 8:28] = (240, 190, 60)
    image[36:56, 36:56] = (90, 170, 90)
    return image


def _smooth_gradient(size: int = 64) -> np.ndarray:
    ramp = np.linspace(0, 255, size)
    image = np.stack(
        [
            np.broadcast_to(ramp, (size, size)),
            np.broadcast_to(np.linspace(120, 220, size)[:, None], (size, size)),
            np.broadcast_to(255 - ramp, (size, size)),
        ],
        axis=-1,
    )
    return np.clip(image, 0, 255).astype(np.uint8)


def _gradient_with_noise(size: int = 64) -> np.ndarray:
    rng = np.random.default_rng(0)
    image = _smooth_gradient(size).astype(np.float32) + rng.normal(
        0, 18, (size, size, 3)
    )
    return np.clip(image, 0, 255).astype(np.uint8)


def _save_png(tmp_path: Path, name: str, image: np.ndarray) -> Path:
    path = tmp_path / name
    Image.fromarray(image, "RGB").save(path)
    return path


def test_flat_color_blocks_classify_as_flat_illustration():
    result = compute_trace_eligibility(Image.fromarray(_flat_blocks(), "RGB"))
    assert result["classification"] == "flat-illustration"
    assert result["statistics"]["unique_colors_4bit"] == 5


def test_continuous_gradient_with_noise_classifies_as_photographic():
    result = compute_trace_eligibility(Image.fromarray(_gradient_with_noise(), "RGB"))
    assert result["classification"] == "photographic"


def test_smooth_gradient_without_hard_edges_is_ambiguous():
    result = compute_trace_eligibility(Image.fromarray(_smooth_gradient(), "RGB"))
    assert result["classification"] == "ambiguous"


def test_eligibility_reports_full_statistics_and_thresholds():
    result = compute_trace_eligibility(Image.fromarray(_flat_blocks(), "RGB"))
    assert set(result) == {"classification", "statistics", "thresholds"}
    assert set(result["statistics"]) == {
        "width",
        "height",
        "unique_colors_4bit",
        "mean_gradient_magnitude",
        "high_gradient_fraction",
        "local_variance_mean",
    }
    assert result["statistics"]["width"] == 64
    assert result["statistics"]["height"] == 64
    assert (
        result["thresholds"]["flat_max_unique_colors"]
        < result["thresholds"]["photo_min_unique_colors"]
    )


def test_eligibility_is_deterministic_across_input_forms(tmp_path: Path):
    from_image = compute_trace_eligibility(Image.fromarray(_gradient_with_noise(), "RGB"))
    from_path = compute_trace_eligibility(
        _save_png(tmp_path, "crop.png", _gradient_with_noise())
    )
    assert from_image == from_path
    repeated = compute_trace_eligibility(tmp_path / "crop.png")
    assert repeated == from_path


def test_eligibility_handles_single_pixel_wide_crop():
    image = np.zeros((16, 1, 3), np.uint8)
    image[:8, 0] = (255, 255, 255)
    result = compute_trace_eligibility(Image.fromarray(image, "RGB"))
    assert result["classification"] in {"photographic", "flat-illustration", "ambiguous"}


def test_eligibility_rejects_unreadable_image(tmp_path: Path):
    with pytest.raises(AssetTraceError):
        compute_trace_eligibility(tmp_path / "missing.png")


def test_trace_constants_have_a_single_authoritative_definition():
    from tools.core.contracts import TRACE_ELIGIBILITY_VALUES
    from tools.providers.providers import _VTRACER_TRACE_DEFAULT_PARAMS

    assert TRACE_ELIGIBILITY_CLASSES == TRACE_ELIGIBILITY_VALUES
    assert "mode" not in VTRACER_LOCKED_PARAMETERS
    assert _VTRACER_TRACE_DEFAULT_PARAMS == {
        **VTRACER_LOCKED_PARAMETERS,
        "mode": VTRACER_DEFAULT_MODE,
    }


def test_trace_output_is_byte_deterministic(tmp_path: Path):
    pytest.importorskip("vtracer")
    source = _save_png(tmp_path, "asset.png", _flat_blocks(32))
    first = run_vtracer_trace(source, tmp_path / "first.svg")
    second = run_vtracer_trace(source, tmp_path / "second.svg")
    first_bytes = (tmp_path / "first.svg").read_bytes()
    second_bytes = (tmp_path / "second.svg").read_bytes()
    assert first_bytes == second_bytes
    assert first["output_sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert first["output_sha256"] == second["output_sha256"]


def test_trace_fills_viewbox_and_reports_provenance(tmp_path: Path):
    pytest.importorskip("vtracer")
    source = _save_png(tmp_path, "asset.png", _flat_blocks(32))
    result = run_vtracer_trace(source, tmp_path / "out.svg")
    assert result["width"] == 32
    assert result["height"] == 32
    assert result["trace_engine"] == "vtracer"
    assert result["trace_engine_version"]
    assert result["mode"] == "spline"
    assert result["parameters"] == {"mode": "spline", **VTRACER_LOCKED_PARAMETERS}
    root = ET.parse(tmp_path / "out.svg").getroot()
    assert root.get("viewBox") == "0 0 32 32"
    assert root.get("width") == "32"
    assert root.get("height") == "32"


def test_trace_output_passes_contract_subset(tmp_path: Path):
    pytest.importorskip("vtracer")
    source = _save_png(tmp_path, "asset.png", _flat_blocks(32))
    output = tmp_path / "out.svg"
    run_vtracer_trace(source, output)
    assert check_svg_contract_subset(output) == []


def test_trace_rejects_missing_input(tmp_path: Path):
    with pytest.raises(AssetTraceError):
        run_vtracer_trace(tmp_path / "missing.png", tmp_path / "out.svg")


def test_contract_subset_accepts_pure_path_stacking(tmp_path: Path):
    svg = (
        f"{_SVG_OPEN}"
        '<g><path d="M0 0 L8 8" fill="#FF0000"/>'
        '<path d="M1 1 L7 7" fill="#00FF00"/></g></svg>'
    )
    path = tmp_path / "ok.svg"
    path.write_text(svg, encoding="utf-8")
    assert check_svg_contract_subset(path) == []


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('<image href="raster.png" width="8" height="8"/>', "forbidden-element:image"),
        ('<mask id="m"><path d="M0 0 L1 1"/></mask>', "forbidden-element:mask"),
        ('<meshgradient id="g"></meshgradient>', "forbidden-element:meshgradient"),
        ("<text>label</text>", "forbidden-element:text"),
        ("<script>alert(1)</script>", "forbidden-element:script"),
        ('<foreignObject width="8" height="8"/>', "forbidden-element:foreignObject"),
        ('<use href="#a"/>', "forbidden-element:use"),
        ('<filter id="f"><feBlend mode="multiply"/></filter>', "forbidden-element:filter"),
        ('<animate attributeName="x"/>', "forbidden-element:animate"),
        (
            '<path d="M0 0 L1 1" style="mix-blend-mode:multiply"/>',
            "forbidden-style:mix-blend-mode",
        ),
        ('<path d="M0 0 L1 1" mask="url(#m)"/>', "forbidden-attribute:mask"),
        ('<path d="M0 0 L1 1" filter="url(#f)"/>', "forbidden-attribute:filter"),
    ],
)
def test_contract_subset_rejects_out_of_subset_constructs(
    tmp_path: Path, snippet: str, expected: str
):
    path = tmp_path / "bad.svg"
    path.write_text(f"{_SVG_OPEN}{snippet}</svg>", encoding="utf-8")
    violations = check_svg_contract_subset(path)
    assert expected in violations


def test_contract_subset_flags_unparseable_and_non_svg(tmp_path: Path):
    broken = tmp_path / "broken.svg"
    broken.write_text("<svg><path", encoding="utf-8")
    assert check_svg_contract_subset(broken) == ["unparseable"]
    not_svg = tmp_path / "not-svg.svg"
    not_svg.write_text("<html/>", encoding="utf-8")
    assert check_svg_contract_subset(not_svg) == ["root-not-svg"]
    assert check_svg_contract_subset(tmp_path / "missing.svg") == ["unreadable"]
