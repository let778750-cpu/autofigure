#!/usr/bin/env python3
"""Local, provenance-bound PaddleOCR perception adapter.

The module intentionally imports only the Python standard library at import time.
Pillow, NumPy, PaddlePaddle, PaddleX, and PaddleOCR are imported only after the
configured interpreter, package versions, and local model hashes have passed
strict validation.

OCR output is evidence for review, never user-confirmed text.  Every candidate
therefore remains UNVERIFIED (or CONFLICT) regardless of the model's score.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping, Sequence

# The production launcher uses ``python -I`` to exclude user-site and CWD
# contamination.  Admit only this resolved sibling directory for the shared
# output-policy module.
TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import OutputPolicyError, resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.paddle_ocr_manifest
    try:
        from .output_policy import OutputPolicyError, resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the project root.
        from tools.output_policy import OutputPolicyError, resolve_output_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "ocr-config.json"
CANONICAL_ACCEPTANCE_FIXTURE = PROJECT_ROOT / "examples" / "target_figure.fixture.json"
HOST_RUNTIME_CONFIG_PATH = PROJECT_ROOT / "host-runtime.json"
HOST_RUNTIME_VALIDATOR_PATH = PROJECT_ROOT / "tools" / "validate_host_runtime.py"
HOST_RUNTIME_RECEIPT_NAME = "host-runtime-receipt.json"
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
DEVICE_RE = re.compile(r"^(?:auto|cpu|gpu(?::\d+)?)$")


class ManifestError(RuntimeError):
    """Raised when a reproducibility or perception contract is violated."""


def _validated_output(path: str | Path) -> Path:
    try:
        return resolve_output_path(path)
    except OutputPolicyError as exc:
        raise ManifestError(str(exc)) from exc


@dataclass(frozen=True)
class ViewSpec:
    """A source-space view submitted to OCR."""

    view_id: str
    kind: str
    x: int
    y: int
    width: int
    height: int
    upscale: float
    rotation_degrees: int = 0
    trigger_candidate_id: str | None = None
    trigger_view_id: str | None = None
    source_aspect_ratio_min: float | None = None

    @property
    def bbox_source(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.width, "h": self.height}

    def to_manifest(self) -> dict[str, Any]:
        input_width = int(round(self.width * self.upscale))
        input_height = int(round(self.height * self.upscale))
        if self.rotation_degrees in {90, 270}:
            input_width, input_height = input_height, input_width
        return {
            "view_id": self.view_id,
            "kind": self.kind,
            "bbox_source": self.bbox_source,
            "upscale": self.upscale,
            "rotation_degrees": self.rotation_degrees,
            "trigger_candidate_id": self.trigger_candidate_id,
            "trigger_view_id": self.trigger_view_id,
            "source_aspect_ratio_min": self.source_aspect_ratio_min,
            "ocr_input_size": {
                "w": input_width,
                "h": input_height,
            },
        }


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def atomic_write_bytes(path: str | Path, payload: bytes) -> None:
    target = _validated_output(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path).resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Unable to read OCR config {config_path}: {exc}") from exc
    validate_config(config)
    config["_config_path"] = str(config_path)
    return config


def validate_config(config: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "paddle_root",
        "runtime",
        "models_root_relative_path",
        "models",
        "inference",
        "tiling",
        "quarter_turn_review",
        "acceptance_fixture_relative_path",
        "confidence",
        "deduplication",
        "provenance_scripts",
        "manifest_schema",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ManifestError(f"OCR config is missing fields: {', '.join(missing)}")

    fixture_setting = str(config["acceptance_fixture_relative_path"])
    normalized_fixture_setting = fixture_setting.replace("\\", "/")
    if normalized_fixture_setting != "examples/target_figure.fixture.json":
        raise ManifestError(
            "OCR acceptance must use examples/target_figure.fixture.json as its "
            "single checked-in authority"
        )

    expected_runtime_packages = {
        "paddleocr",
        "paddlex",
        "paddle",
        "numpy",
        "pillow",
        "scipy",
        "opencv",
    }
    actual_runtime_packages = set(config["runtime"].get("packages", {}))
    if actual_runtime_packages != expected_runtime_packages:
        raise ManifestError(
            "OCR runtime must pin Paddle and every dependency imported by the OCR adapter; "
            f"got {sorted(actual_runtime_packages)}"
        )
    if config["runtime"].get("download_fallbacks_disabled") is not True:
        raise ManifestError("OCR runtime must disable package/model download fallbacks")

    expected_model_roles = {
        "text_detection",
        "text_recognition",
        "textline_orientation",
    }
    model_roles = set(config["models"])
    if model_roles != expected_model_roles:
        raise ManifestError(
            "OCR config must pin exactly the medium detection, recognition, and "
            f"text-line orientation models; got {sorted(model_roles)}"
        )
    expected_names = {
        "text_detection": "PP-OCRv6_medium_det",
        "text_recognition": "PP-OCRv6_medium_rec",
        "textline_orientation": "PP-LCNet_x1_0_textline_ori",
    }
    for role, expected_name in expected_names.items():
        model = config["models"][role]
        if model.get("name") != expected_name:
            raise ManifestError(f"{role} must be {expected_name}, got {model.get('name')!r}")
        files = model.get("files", {})
        if set(files) != {"inference.json", "inference.pdiparams", "inference.yml"}:
            raise ManifestError(f"{role} does not pin all three inference artifacts")
        for filename, expected_hash in files.items():
            if not SHA256_RE.fullmatch(str(expected_hash)):
                raise ManifestError(f"Invalid SHA-256 for {role}/{filename}")

    confidence = config["confidence"]
    high_min = float(confidence["high_min"])
    medium_min = float(confidence["medium_min"])
    if not 0 <= medium_min < high_min <= 1:
        raise ManifestError("Confidence thresholds must satisfy 0 <= medium < high <= 1")

    tiling = config["tiling"]
    if int(tiling["rows"]) <= 0 or int(tiling["columns"]) <= 0:
        raise ManifestError("Tile rows and columns must be positive")
    if int(tiling["overlap_px"]) < 0 or float(tiling["upscale"]) < 1:
        raise ManifestError("Tile overlap must be non-negative and upscale must be >= 1")

    quarter_turn = config["quarter_turn_review"]
    rotations = [int(value) for value in quarter_turn["rotations_degrees"]]
    if not rotations or any(value not in {90, 270} for value in rotations):
        raise ManifestError("Quarter-turn review rotations must contain only 90 and/or 270")
    if float(quarter_turn["vertical_aspect_ratio_min"]) <= 1:
        raise ManifestError("Quarter-turn review aspect ratio must be greater than 1")
    if int(quarter_turn["padding_px"]) < 0 or float(quarter_turn["upscale"]) < 1:
        raise ManifestError("Quarter-turn padding/upscale is invalid")
    if int(quarter_turn["max_regions"]) <= 0:
        raise ManifestError("Quarter-turn max_regions must be positive")
    if float(quarter_turn["tile_sweep_upscale"]) < 1:
        raise ManifestError("Quarter-turn tile sweep upscale must be >= 1")


def load_acceptance_fixture(
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and verify the sole OCR acceptance authority and its target image."""

    config_path = Path(config.get("_config_path", DEFAULT_CONFIG_PATH)).resolve()
    fixture_path = (config_path.parent / str(config["acceptance_fixture_relative_path"])).resolve()
    if _norm_path(fixture_path) != _norm_path(CANONICAL_ACCEPTANCE_FIXTURE):
        raise ManifestError(
            f"Resolved OCR acceptance fixture is not the canonical target fixture: {fixture_path}"
        )
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Unable to read acceptance fixture {fixture_path}: {exc}") from exc

    required = {
        "schemaVersion",
        "fixtureId",
        "referenceFile",
        "sha256",
        "referencePolicy",
        "ocrSmokeExpectations",
    }
    missing = sorted(required - set(fixture))
    if missing:
        raise ManifestError(f"Acceptance fixture is missing fields: {', '.join(missing)}")
    declared_source_hash = str(fixture["sha256"]).upper()
    if not SHA256_RE.fullmatch(declared_source_hash):
        raise ManifestError("Acceptance fixture source SHA-256 is invalid")
    if fixture["referencePolicy"].get("approvedAsTestFixture") is not True:
        raise ManifestError("Acceptance fixture is not approved as a test fixture")

    expectations = fixture["ocrSmokeExpectations"]
    minimum = expectations.get("minimumDetectedTextBoxes")
    anchors = expectations.get("requiredExactAnchors")
    formula_candidates = expectations.get("mustRemainFormulaCandidates")
    formula_similarity_min = expectations.get("formulaCandidateSimilarityMin")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
        raise ManifestError(
            "Acceptance fixture minimumDetectedTextBoxes must be a positive integer"
        )
    if (
        not isinstance(anchors, list)
        or not anchors
        or any(not isinstance(anchor, str) or not anchor for anchor in anchors)
        or len(set(anchors)) != len(anchors)
    ):
        raise ManifestError(
            "Acceptance fixture requiredExactAnchors must be unique non-empty strings"
        )
    if not isinstance(formula_candidates, list) or any(
        not isinstance(candidate, str) or not candidate for candidate in formula_candidates
    ):
        raise ManifestError("Acceptance fixture mustRemainFormulaCandidates must be a string array")
    if (
        isinstance(formula_similarity_min, bool)
        or not isinstance(formula_similarity_min, (int, float))
        or not 0 < float(formula_similarity_min) <= 1
    ):
        raise ManifestError("Acceptance fixture formulaCandidateSimilarityMin must be in (0, 1]")

    reference_path = (fixture_path.parent / str(fixture["referenceFile"])).resolve()
    try:
        reference_path.relative_to(fixture_path.parent.resolve())
    except ValueError as exc:
        raise ManifestError("Acceptance fixture referenceFile escapes examples/") from exc
    if not reference_path.is_file():
        raise ManifestError(f"Acceptance fixture reference image is missing: {reference_path}")
    actual_source_hash = sha256_file(reference_path)
    if actual_source_hash != declared_source_hash:
        raise ManifestError(
            "Acceptance fixture/reference hash mismatch: "
            f"declared {declared_source_hash}, got {actual_source_hash}"
        )

    evidence = {
        "path": str(fixture_path),
        "sha256": sha256_file(fixture_path),
        "schema_version": str(fixture["schemaVersion"]),
        "fixture_id": str(fixture["fixtureId"]),
        "reference_path": str(reference_path),
        "source_sha256": actual_source_hash,
    }
    return fixture, evidence


def _norm_path(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def validate_python_and_packages(config: Mapping[str, Any]) -> dict[str, Any]:
    runtime = config["runtime"]
    paddle_root = Path(config["paddle_root"])
    expected_python = (paddle_root / runtime["python_relative_path"]).resolve()
    if runtime.get("require_expected_python", True):
        if _norm_path(sys.executable) != _norm_path(expected_python):
            raise ManifestError(
                "Wrong Python interpreter. Expected "
                f"{expected_python}, running {Path(sys.executable).resolve()}"
            )

    actual_python = ".".join(map(str, sys.version_info[:3]))
    if actual_python != runtime["python_version"]:
        raise ManifestError(
            f"Python version mismatch: expected {runtime['python_version']}, got {actual_python}"
        )

    packages: dict[str, Any] = {}
    for import_name, package in runtime["packages"].items():
        distribution = package["distribution"]
        expected = package["version"]
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ManifestError(f"Required package is not installed: {distribution}") from exc
        if actual != expected:
            raise ManifestError(
                f"Package version mismatch for {distribution}: expected {expected}, got {actual}"
            )
        packages[import_name] = {
            "distribution": distribution,
            "expected_version": expected,
            "actual_version": actual,
            "expected_import_version": package.get("import_version", expected),
        }
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": actual_python,
        "packages": packages,
    }


def validate_model_files(config: Mapping[str, Any]) -> dict[str, Any]:
    paddle_root = Path(config["paddle_root"])
    models_root = paddle_root / config["models_root_relative_path"]
    evidence: dict[str, Any] = {}
    for role, model in config["models"].items():
        model_dir = models_root / model["relative_path"]
        if not model_dir.is_dir():
            raise ManifestError(
                f"Pinned local model is missing ({role}): {model_dir}. Downloads are disabled."
            )
        artifacts = []
        for filename, expected_hash in sorted(model["files"].items()):
            artifact_path = model_dir / filename
            if not artifact_path.is_file():
                raise ManifestError(f"Pinned model artifact is missing: {artifact_path}")
            actual_hash = sha256_file(artifact_path)
            if actual_hash != expected_hash:
                raise ManifestError(
                    f"Model hash mismatch for {artifact_path}: "
                    f"expected {expected_hash}, got {actual_hash}"
                )
            artifacts.append(
                {
                    "filename": filename,
                    "path": str(artifact_path.resolve()),
                    "size_bytes": artifact_path.stat().st_size,
                    "sha256": actual_hash,
                }
            )

        inference_yml = (model_dir / "inference.yml").read_text(encoding="utf-8")
        if f"model_name: {model['name']}" not in inference_yml:
            raise ManifestError(
                f"Model identity in {model_dir / 'inference.yml'} does not match {model['name']}"
            )
        model_record = {
            "role": role,
            "name": model["name"],
            "path": str(model_dir.resolve()),
            "artifacts": artifacts,
        }
        if "supported_angles_degrees" in model:
            model_record["supported_angles_degrees"] = list(model["supported_angles_degrees"])
        evidence[role] = model_record
    return evidence


def build_views(
    width: int,
    height: int,
    *,
    rows: int = 2,
    columns: int = 2,
    overlap_px: int = 96,
    upscale: float = 2.0,
    include_full: bool = True,
    tiles_enabled: bool = True,
) -> list[ViewSpec]:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    if rows <= 0 or columns <= 0:
        raise ValueError("Tile rows and columns must be positive")
    if overlap_px < 0 or upscale < 1:
        raise ValueError("Tile overlap must be non-negative and upscale must be >= 1")

    views: list[ViewSpec] = []
    if include_full:
        views.append(ViewSpec("full", "full", 0, 0, width, height, 1.0))
    if not tiles_enabled:
        return views

    overlap_before = overlap_px // 2
    overlap_after = overlap_px - overlap_before
    for row in range(rows):
        core_y0 = math.floor(row * height / rows)
        core_y1 = math.floor((row + 1) * height / rows)
        y0 = max(0, core_y0 - (overlap_before if row else 0))
        y1 = min(height, core_y1 + (overlap_after if row < rows - 1 else 0))
        for column in range(columns):
            core_x0 = math.floor(column * width / columns)
            core_x1 = math.floor((column + 1) * width / columns)
            x0 = max(0, core_x0 - (overlap_before if column else 0))
            x1 = min(width, core_x1 + (overlap_after if column < columns - 1 else 0))
            views.append(
                ViewSpec(
                    f"tile_r{row}_c{column}",
                    "tile",
                    x0,
                    y0,
                    x1 - x0,
                    y1 - y0,
                    float(upscale),
                )
            )
    return views


def map_polygon_to_source(
    polygon: Sequence[Sequence[float]],
    view: ViewSpec,
    *,
    source_width: int | None = None,
    source_height: int | None = None,
) -> list[list[float]]:
    mapped = []
    scaled_width = float(view.width) * view.upscale
    scaled_height = float(view.height) * view.upscale
    for point in polygon:
        rotated_x, rotated_y = float(point[0]), float(point[1])
        if view.rotation_degrees == 0:
            local_x, local_y = rotated_x, rotated_y
        elif view.rotation_degrees == 90:
            local_x, local_y = rotated_y, scaled_height - rotated_x
        elif view.rotation_degrees == 180:
            local_x, local_y = scaled_width - rotated_x, scaled_height - rotated_y
        elif view.rotation_degrees == 270:
            local_x, local_y = scaled_width - rotated_y, rotated_x
        else:
            raise ValueError(f"Unsupported clockwise rotation: {view.rotation_degrees} degrees")
        x = view.x + local_x / view.upscale
        y = view.y + local_y / view.upscale
        if source_width is not None:
            x = min(max(x, 0.0), float(source_width))
        if source_height is not None:
            y = min(max(y, 0.0), float(source_height))
        mapped.append([round(x, 3), round(y, 3)])
    return mapped


def build_rotation_review_views(
    candidates: Sequence[Mapping[str, Any]],
    source_width: int,
    source_height: int,
    settings: Mapping[str, Any],
) -> list[ViewSpec]:
    """Build bounded crop retries for likely vertical text only.

    The normal full/tile passes remain the cost-effective default path.  Quarter
    turns are applied only to tall candidate regions, avoiding whole-image
    rotations that would turn every normal horizontal label into noise.
    """

    if not settings.get("enabled", True):
        return []
    ratio_min = float(settings["vertical_aspect_ratio_min"])
    padding = int(settings["padding_px"])
    upscale = float(settings["upscale"])
    rotations = [int(value) for value in settings["rotations_degrees"]]
    max_regions = int(settings["max_regions"])
    suspects = [
        candidate
        for candidate in candidates
        if float(candidate["bbox_source"]["h"]) / max(float(candidate["bbox_source"]["w"]), 1.0)
        >= ratio_min
    ]
    suspects.sort(
        key=lambda candidate: (
            -float(candidate["bbox_source"]["h"]) / max(float(candidate["bbox_source"]["w"]), 1.0),
            float(candidate["bbox_source"]["y"]),
            float(candidate["bbox_source"]["x"]),
        )
    )
    views: list[ViewSpec] = []
    for candidate in suspects[:max_regions]:
        box = candidate["bbox_source"]
        x0 = max(0, math.floor(float(box["x"])) - padding)
        y0 = max(0, math.floor(float(box["y"])) - padding)
        x1 = min(
            source_width,
            math.ceil(float(box["x"]) + float(box["w"])) + padding,
        )
        y1 = min(
            source_height,
            math.ceil(float(box["y"]) + float(box["h"])) + padding,
        )
        if x1 <= x0 or y1 <= y0:
            continue
        for rotation in rotations:
            views.append(
                ViewSpec(
                    view_id=(f"quarter_turn_{candidate['candidate_id']}_cw{rotation}"),
                    kind="rotation_review",
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    upscale=upscale,
                    rotation_degrees=rotation,
                    trigger_candidate_id=candidate["candidate_id"],
                    source_aspect_ratio_min=ratio_min,
                )
            )
    return views


def build_quarter_turn_tile_sweep_views(
    base_views: Sequence[ViewSpec],
    settings: Mapping[str, Any],
) -> list[ViewSpec]:
    """Rotate every bounded tile to cover text missed by the first pass entirely."""

    if not settings.get("enabled", True) or not settings.get("tile_sweep_enabled", True):
        return []
    rotations = [int(value) for value in settings["rotations_degrees"]]
    upscale = float(settings["tile_sweep_upscale"])
    views = []
    for base in base_views:
        if base.kind != "tile":
            continue
        for rotation in rotations:
            views.append(
                ViewSpec(
                    view_id=f"quarter_turn_{base.view_id}_cw{rotation}",
                    kind="rotation_review",
                    x=base.x,
                    y=base.y,
                    width=base.width,
                    height=base.height,
                    upscale=upscale,
                    rotation_degrees=rotation,
                    trigger_view_id=base.view_id,
                    source_aspect_ratio_min=float(settings["vertical_aspect_ratio_min"]),
                )
            )
    return views


def bbox_from_polygon(polygon: Sequence[Sequence[float]]) -> dict[str, float]:
    if not polygon:
        raise ValueError("Cannot calculate a box from an empty polygon")
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return {
        "x": round(x0, 3),
        "y": round(y0, 3),
        "w": round(max(0.0, x1 - x0), 3),
        "h": round(max(0.0, y1 - y0), 3),
    }


def _intersection_area(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["w"]), ay1 + float(a["h"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["w"]), by1 + float(b["h"])
    return max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))


def bbox_iou(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    intersection = _intersection_area(a, b)
    union = float(a["w"]) * float(a["h"]) + float(b["w"]) * float(b["h"]) - intersection
    return intersection / union if union > 0 else 0.0


def bbox_containment(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    intersection = _intersection_area(a, b)
    smaller = min(float(a["w"]) * float(a["h"]), float(b["w"]) * float(b["h"]))
    return intersection / smaller if smaller > 0 else 0.0


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(character for character in normalized if not character.isspace())


def text_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def spatially_merge_candidate_pair(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> str | None:
    """Reconstruct one line split by overlapping OCR views without inventing missing text."""
    first_box = first.get("bbox_source")
    second_box = second.get("bbox_source")
    if not isinstance(first_box, Mapping) or not isinstance(second_box, Mapping):
        return None
    left, right = sorted((first, second), key=lambda item: float(item["bbox_source"]["x"]))
    left_box, right_box = left["bbox_source"], right["bbox_source"]
    left_text, right_text = normalize_text(left.get("text", "")), normalize_text(right.get("text", ""))
    if min(len(left_text), len(right_text)) < 3:
        return None
    left_top, left_bottom = float(left_box["y"]), float(left_box["y"]) + float(left_box["h"])
    right_top, right_bottom = float(right_box["y"]), float(right_box["y"]) + float(right_box["h"])
    vertical_overlap = min(left_bottom, right_bottom) - max(left_top, right_top)
    smaller_height = min(float(left_box["h"]), float(right_box["h"]))
    horizontal_overlap = min(
        float(left_box["x"]) + float(left_box["w"]),
        float(right_box["x"]) + float(right_box["w"]),
    ) - float(right_box["x"])
    if smaller_height <= 0 or vertical_overlap / smaller_height < 0.6 or horizontal_overlap <= 0:
        return None

    expected_shift = round(
        (float(right_box["x"]) - float(left_box["x"]))
        / max(float(left_box["w"]), 1e-6)
        * len(left_text)
    )
    best: tuple[float, int] | None = None
    for shift in range(max(1, expected_shift - 3), min(len(left_text) - 2, expected_shift + 3) + 1):
        overlap_length = min(len(left_text) - shift, len(right_text))
        if overlap_length < 3:
            continue
        similarity = SequenceMatcher(
            None,
            left_text[shift : shift + overlap_length],
            right_text[:overlap_length],
            autojunk=False,
        ).ratio()
        score = similarity - 0.025 * abs(shift - expected_shift)
        if similarity >= 0.65 and (best is None or score > best[0]):
            best = (score, shift)
    if best is None:
        return None
    return left_text[: best[1]] + right_text


def fixture_search_texts(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    normalized = [normalize_text(candidate.get("text", "")) for candidate in candidates]
    merged = [
        value
        for index, first in enumerate(candidates)
        for second in candidates[index + 1 :]
        if (value := spatially_merge_candidate_pair(first, second)) is not None
    ]
    return normalized + merged


def classify_confidence(
    score: float,
    thresholds: Mapping[str, float],
    *,
    conflict: bool = False,
) -> str:
    if conflict:
        return "OCR_CONFLICT"
    if score >= float(thresholds["high_min"]):
        return "OCR_HIGH"
    if score >= float(thresholds["medium_min"]):
        return "OCR_MEDIUM"
    return "OCR_LOW"


def _formula_like(text: str) -> bool:
    return bool(re.search(r"[=∑∫∆Δσλμ^_{}]|[₀-₉⁰-⁹]", text))


def _reduced_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": observation["observation_id"],
        "view_id": observation["view_id"],
        "text": observation["text"],
        "ocr_confidence": float(observation["ocr_confidence"]),
        "bbox_source": dict(observation["bbox_source"]),
        "polygon_source": observation["polygon_source"],
        "textline_orientation_degrees": observation.get("textline_orientation_degrees"),
        "input_rotation_degrees": observation.get("input_rotation_degrees", 0),
    }


def _bbox_envelope(observations: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    boxes = [observation["bbox_source"] for observation in observations]
    x0 = min(float(box["x"]) for box in boxes)
    y0 = min(float(box["y"]) for box in boxes)
    x1 = max(float(box["x"]) + float(box["w"]) for box in boxes)
    y1 = max(float(box["y"]) + float(box["h"]) for box in boxes)
    return {
        "x": round(x0, 3),
        "y": round(y0, 3),
        "w": round(x1 - x0, 3),
        "h": round(y1 - y0, 3),
    }


def merge_observations(
    observations: Sequence[Mapping[str, Any]],
    deduplication: Mapping[str, float],
    confidence_thresholds: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Merge spatial/text duplicates while retaining disagreeing alternatives."""

    iou_threshold = float(deduplication["iou_threshold"])
    containment_threshold = float(deduplication["containment_threshold"])
    conflict_iou_threshold = float(deduplication["conflict_iou_threshold"])
    conflict_containment_threshold = float(deduplication["conflict_containment_threshold"])
    similarity_threshold = float(deduplication["text_similarity_threshold"])

    sorted_observations = sorted(
        (dict(observation) for observation in observations),
        key=lambda observation: (
            -float(observation["ocr_confidence"]),
            float(observation["bbox_source"]["y"]),
            float(observation["bbox_source"]["x"]),
            observation["observation_id"],
        ),
    )
    clusters: list[dict[str, Any]] = []
    for observation in sorted_observations:
        best_cluster: dict[str, Any] | None = None
        best_spatial = -1.0
        best_is_duplicate = False
        for cluster in clusters:
            primary = cluster["primary"]
            iou = bbox_iou(observation["bbox_source"], primary["bbox_source"])
            containment = bbox_containment(observation["bbox_source"], primary["bbox_source"])
            duplicate_spatial = iou >= iou_threshold or containment >= containment_threshold
            conflict_spatial = (
                iou >= conflict_iou_threshold or containment >= conflict_containment_threshold
            )
            if not conflict_spatial:
                continue
            similarity = text_similarity(observation["text"], primary["text"])
            is_duplicate = duplicate_spatial and similarity >= similarity_threshold
            spatial_score = max(iou, containment)
            if is_duplicate:
                spatial_score += 1.0
            if spatial_score > best_spatial:
                best_cluster = cluster
                best_spatial = spatial_score
                best_is_duplicate = is_duplicate

        if best_cluster is None:
            clusters.append({"primary": observation, "observations": [observation]})
            continue

        best_cluster["observations"].append(observation)
        if not best_is_duplicate:
            best_cluster.setdefault("conflict_observation_ids", []).append(
                observation["observation_id"]
            )

    candidates: list[dict[str, Any]] = []
    for cluster in clusters:
        primary = cluster["primary"]
        primary_norm = normalize_text(primary["text"])
        all_observations = cluster["observations"]
        alternative_by_text: dict[str, Mapping[str, Any]] = {}
        for observation in all_observations:
            normalized = normalize_text(observation["text"])
            if normalized == primary_norm:
                continue
            current = alternative_by_text.get(normalized)
            if current is None or float(observation["ocr_confidence"]) > float(
                current["ocr_confidence"]
            ):
                alternative_by_text[normalized] = observation
        alternatives = [
            _reduced_observation(observation)
            for observation in sorted(
                alternative_by_text.values(),
                key=lambda item: (-float(item["ocr_confidence"]), item["text"]),
            )
        ]
        conflict = bool(alternatives)
        score = float(primary["ocr_confidence"])
        band = classify_confidence(score, confidence_thresholds, conflict=conflict)
        flags = []
        if conflict:
            flags.append("OCR_CONFLICT")
        if band == "OCR_LOW":
            flags.append("LOW_OCR_CONFIDENCE")
        if _formula_like(primary["text"]):
            flags.append("FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE")
        if len(normalize_text(primary["text"])) <= 1:
            flags.append("SINGLE_GLYPH_REVIEW")
        agreement_count = sum(
            normalize_text(observation["text"]) == primary_norm for observation in all_observations
        )
        candidates.append(
            {
                "candidate_id": "",
                "text": primary["text"],
                "normalized_text": primary_norm,
                "ocr_confidence": score,
                "confidence_band": band,
                "bbox_source": dict(primary["bbox_source"]),
                "bbox_envelope_source": _bbox_envelope(all_observations),
                "polygon_source": primary["polygon_source"],
                "primary_observation_id": primary["observation_id"],
                "source_views": sorted(
                    {observation["view_id"] for observation in all_observations}
                ),
                "agreement_count": agreement_count,
                "observations": [
                    _reduced_observation(observation) for observation in all_observations
                ],
                "alternatives": alternatives,
                "review_flags": flags,
                "evidence_kind": "OCR_HYPOTHESIS",
                "requires_human_review": True,
                "verification": {
                    "status": "CONFLICT" if conflict else "UNVERIFIED",
                    "user_confirmed_text": None,
                },
            }
        )

    candidates.sort(
        key=lambda candidate: (
            float(candidate["bbox_source"]["y"]),
            float(candidate["bbox_source"]["x"]),
            candidate["text"],
        )
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = f"T{index:04d}"
    return candidates


def _to_builtin(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Mapping):
        return {key: _to_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(item) for item in value]
    return value


def extract_observations(
    result: Mapping[str, Any],
    view: ViewSpec,
    *,
    source_width: int,
    source_height: int,
    start_index: int,
) -> list[dict[str, Any]]:
    texts = [str(text) for text in _to_builtin(result.get("rec_texts", []))]
    scores = [float(score) for score in _to_builtin(result.get("rec_scores", []))]
    polygons = _to_builtin(result.get("rec_polys", []))
    boxes = _to_builtin(result.get("rec_boxes", []))
    angles = _to_builtin(result.get("textline_orientation_angles", []))
    observations: list[dict[str, Any]] = []
    for local_index, text in enumerate(texts):
        if not text.strip():
            continue
        if local_index >= len(scores):
            raise ManifestError("OCR result has fewer scores than recognized texts")
        if local_index < len(polygons) and polygons[local_index]:
            local_polygon = polygons[local_index]
        elif local_index < len(boxes) and len(boxes[local_index]) >= 4:
            x0, y0, x1, y1 = boxes[local_index][:4]
            local_polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        else:
            raise ManifestError(f"OCR result is missing geometry for text index {local_index}")
        source_polygon = map_polygon_to_source(
            local_polygon,
            view,
            source_width=source_width,
            source_height=source_height,
        )
        angle = int(angles[local_index]) if local_index < len(angles) else None
        source_bbox = bbox_from_polygon(source_polygon)
        if (
            view.source_aspect_ratio_min is not None
            and source_bbox["h"] / max(source_bbox["w"], 1.0) < view.source_aspect_ratio_min
        ):
            continue
        observations.append(
            {
                "observation_id": f"O{start_index + len(observations):05d}",
                "view_id": view.view_id,
                "view_kind": view.kind,
                "text": text,
                "ocr_confidence": float(scores[local_index]),
                "polygon_source": source_polygon,
                "bbox_source": source_bbox,
                "textline_orientation_degrees": angle,
                "input_rotation_degrees": view.rotation_degrees,
                "evidence_kind": "OCR_HYPOTHESIS",
                "verification_status": "UNVERIFIED",
            }
        )
    return observations


def _resolve_device(requested: str, paddle: Any) -> tuple[str, dict[str, Any]]:
    if not DEVICE_RE.fullmatch(requested):
        raise ManifestError(f"Unsupported device selector: {requested}")
    compiled_with_cuda = bool(paddle.device.is_compiled_with_cuda())
    cuda_count = int(paddle.device.cuda.device_count()) if compiled_with_cuda else 0
    if requested == "auto":
        resolved = "gpu:0" if cuda_count > 0 else "cpu"
    elif requested == "gpu":
        resolved = "gpu:0"
    else:
        resolved = requested
    if resolved.startswith("gpu"):
        index = int(resolved.split(":", 1)[1]) if ":" in resolved else 0
        if not compiled_with_cuda or index >= cuda_count:
            raise ManifestError(
                f"Requested {resolved}, but CUDA is unavailable or device index is invalid"
            )
        gpu_name = paddle.device.cuda.get_device_name(index)
    else:
        gpu_name = None
    return resolved, {
        "compiled_with_cuda": compiled_with_cuda,
        "cuda_device_count": cuda_count,
        "cuda_version": paddle.version.cuda() if compiled_with_cuda else None,
        "cudnn_version": paddle.version.cudnn() if compiled_with_cuda else None,
        "gpu_name": gpu_name,
    }


def _prepare_download_restricted_runtime(output_dir: Path, config: Mapping[str, Any]) -> None:
    cache_root = output_dir / "runtime-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    environment = {
        "PYTHONNOUSERSITE": "1",
        "PADDLE_PDX_CACHE_HOME": str(cache_root / "paddlex"),
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "PADDLE_HOME": str(cache_root / "paddle"),
        "PADDLE_EXTENSION_DIR": str(cache_root / "paddle-extension"),
        "XDG_CACHE_HOME": str(cache_root),
        "HF_HOME": str(cache_root / "huggingface"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "MODELSCOPE_CACHE": str(cache_root / "modelscope"),
        "MODELSCOPE_OFFLINE": "1",
        "PIP_NO_INDEX": "1",
        "TEMP": str(cache_root / "tmp"),
        "TMP": str(cache_root / "tmp"),
    }
    os.environ.pop("PADDLE_PDX_MODEL_SOURCE", None)
    for path_key in (
        "PADDLE_HOME",
        "PADDLE_EXTENSION_DIR",
        "PADDLE_PDX_CACHE_HOME",
        "HF_HOME",
        "MODELSCOPE_CACHE",
        "TEMP",
        "TMP",
    ):
        Path(environment[path_key]).mkdir(parents=True, exist_ok=True)
    os.environ.update(environment)
    sys.dont_write_bytecode = True

    original_expanduser = os.path.expanduser
    isolated_home = cache_root / "home"
    isolated_home.mkdir(parents=True, exist_ok=True)

    def isolated_expanduser(path: os.PathLike[str] | str) -> str:
        value = os.fspath(path)
        if value.startswith("~"):
            return str(isolated_home) + value[1:]
        return original_expanduser(value)

    os.path.expanduser = isolated_expanduser


def _hash_project_scripts(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = []
    for relative in config["provenance_scripts"]:
        path = (PROJECT_ROOT / relative).resolve()
        if not path.is_file():
            raise ManifestError(f"Provenance script is missing: {path}")
        records.append(
            {
                "path": str(path),
                "relative_path": Path(relative).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def inventory_artifact_directory(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    directory = Path(path).resolve()
    if not directory.is_dir():
        raise ManifestError(f"Upstream artifact directory does not exist: {directory}")
    files = []
    for artifact in sorted(item for item in directory.rglob("*") if item.is_file()):
        files.append(
            {
                "relative_path": artifact.relative_to(directory).as_posix(),
                "size_bytes": artifact.stat().st_size,
                "sha256": sha256_file(artifact),
            }
        )
    return {"path": str(directory), "files": files}


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Unable to read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"{label} must contain one JSON object: {path}")
    return payload


def _project_contract_file(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"Host runtime contract {label} must be a non-empty path")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(PROJECT_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise ManifestError(f"Host runtime contract {label} escaped the project root") from exc
    if not resolved.is_file():
        raise ManifestError(f"Host runtime contract {label} is missing: {resolved}")
    return resolved


def _path_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve(strict=False)))


def _validate_file_binding(binding: Any, expected_path: Path, *, label: str) -> None:
    if not isinstance(binding, Mapping):
        raise ManifestError(f"Host runtime receipt binding {label} is not an object")
    if _path_key(str(binding.get("path", ""))) != _path_key(expected_path):
        raise ManifestError(f"Host runtime receipt binding {label} points to the wrong file")
    expected_size = expected_path.stat().st_size
    if binding.get("size_bytes") != expected_size:
        raise ManifestError(
            f"Host runtime receipt binding {label} size mismatch: "
            f"expected {expected_size}, got {binding.get('size_bytes')!r}"
        )
    expected_hash = sha256_file(expected_path)
    if str(binding.get("sha256", "")).upper() != expected_hash:
        raise ManifestError(f"Host runtime receipt binding {label} hash mismatch")


def validate_host_runtime_receipt(
    directory: str | Path,
    *,
    run_id: str,
    source_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one canonical host-runtime receipt without trusting its claims alone."""

    runtime_directory = Path(directory).resolve()
    if not runtime_directory.is_dir():
        raise ManifestError(f"Host runtime artifact directory does not exist: {runtime_directory}")
    artifact_files = sorted(item for item in runtime_directory.rglob("*") if item.is_file())
    expected_receipt = runtime_directory / HOST_RUNTIME_RECEIPT_NAME
    if len(artifact_files) != 1 or _path_key(artifact_files[0]) != _path_key(expected_receipt):
        relative_files = [item.relative_to(runtime_directory).as_posix() for item in artifact_files]
        raise ManifestError(
            "Host runtime artifact directory must contain exactly one canonical "
            f"{HOST_RUNTIME_RECEIPT_NAME}; found {relative_files}"
        )

    contract = _load_json_object(HOST_RUNTIME_CONFIG_PATH, label="host runtime contract")
    required_contract_keys = {
        "schema_version",
        "runtime_id",
        "root",
        "python_relative_path",
        "python_version",
        "requirements_path",
        "requirements_sha256",
        "receipt_schema_path",
        "receipt_schema_sha256",
        "allowed_opencv_distribution",
    }
    missing_contract_keys = sorted(required_contract_keys - set(contract))
    if missing_contract_keys:
        raise ManifestError(
            f"Host runtime contract is missing fields: {', '.join(missing_contract_keys)}"
        )
    if contract["schema_version"] != "1.0.0":
        raise ManifestError("Unsupported host runtime contract schema_version")

    requirements_path = _project_contract_file(
        contract["requirements_path"], label="requirements_path"
    )
    receipt_schema_path = _project_contract_file(
        contract["receipt_schema_path"], label="receipt_schema_path"
    )
    for label, path, configured_hash in (
        ("requirements", requirements_path, contract["requirements_sha256"]),
        ("receipt schema", receipt_schema_path, contract["receipt_schema_sha256"]),
    ):
        actual_hash = sha256_file(path)
        if str(configured_hash).upper() != actual_hash:
            raise ManifestError(
                f"Host runtime contract {label} hash is stale: "
                f"expected {configured_hash}, got {actual_hash}"
            )

    receipt = _load_json_object(expected_receipt, label="host runtime receipt")
    receipt_schema = _load_json_object(receipt_schema_path, label="host runtime receipt schema")
    validate_against_schema(receipt, receipt_schema)
    if receipt["status"] != "PASS":
        raise ManifestError("Host runtime receipt status is not PASS")
    expected_context = {"run_id": run_id, "source_sha256": source_sha256.upper()}
    if receipt["context"] != expected_context:
        raise ManifestError(
            "Host runtime receipt context does not match the current run/source"
        )

    expected_bindings = {
        "runtime_config": HOST_RUNTIME_CONFIG_PATH,
        "requirements": requirements_path,
        "receipt_schema": receipt_schema_path,
        "validator": HOST_RUNTIME_VALIDATOR_PATH,
    }
    for label, path in expected_bindings.items():
        _validate_file_binding(receipt["bindings"].get(label), path, label=label)

    runtime = receipt["runtime"]
    configured_python = (
        Path(str(contract["root"])) / str(contract["python_relative_path"])
    ).resolve(strict=False)
    if runtime["runtime_id"] != contract["runtime_id"]:
        raise ManifestError("Host runtime receipt runtime_id differs from host-runtime.json")
    if _path_key(runtime["python_executable"]) != _path_key(configured_python):
        raise ManifestError("Host runtime receipt used the wrong Python interpreter")
    if _path_key(runtime["expected_python"]) != _path_key(configured_python):
        raise ManifestError("Host runtime receipt expected_python differs from host-runtime.json")
    if runtime["python_version"] != contract["python_version"]:
        raise ManifestError("Host runtime receipt Python version differs from host-runtime.json")
    if runtime["expected_python_version"] != contract["python_version"]:
        raise ManifestError(
            "Host runtime receipt expected Python version differs from host-runtime.json"
        )
    if any(not bool(receipt["isolation"][key]) for key in (
        "required",
        "isolated",
        "ignore_environment",
        "no_user_site",
        "safe_path",
    )):
        raise ManifestError("Host runtime receipt does not prove isolated execution")
    if any(not bool(check["passed"]) for check in receipt["checks"]):
        raise ManifestError("Host runtime receipt contains a failed validation check")
    if any(not bool(check["passed"]) for check in receipt["smoke_tests"]):
        raise ManifestError("Host runtime receipt contains a failed smoke test")
    if any(not bool(package["passed"]) for package in receipt["packages"]["required"]):
        raise ManifestError("Host runtime receipt contains a failed required package pin")
    if receipt["packages"]["forbidden_distributions_present"]:
        raise ManifestError("Host runtime receipt reports forbidden distributions")
    if receipt["packages"]["duplicate_distributions"]:
        raise ManifestError("Host runtime receipt reports duplicate distributions")
    expected_opencv = re.sub(
        r"[-_.]+", "-", str(contract["allowed_opencv_distribution"])
    ).lower()
    actual_opencv = [
        re.sub(r"[-_.]+", "-", str(value)).lower()
        for value in receipt["packages"]["opencv_distributions"]
    ]
    if actual_opencv != [expected_opencv]:
        raise ManifestError("Host runtime receipt does not prove one allowed OpenCV wheel")
    if any(
        not bool(module["imported"]) or not bool(module["within_prefix"])
        for module in receipt["modules"]
    ):
        raise ManifestError("Host runtime receipt contains an untrusted module origin")

    inventory = inventory_artifact_directory(runtime_directory)
    if inventory is None:  # pragma: no cover - guarded by the directory check above
        raise ManifestError("Host runtime receipt inventory could not be constructed")
    return receipt, inventory


def validate_host_stage_artifact(
    directory: str | Path,
    *,
    stage_name: str,
    artifact_name: str,
    source_sha256: str,
    host_runtime_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate a deterministic host-stage inventory and its runtime provenance."""

    stage_directory = Path(directory).resolve()
    if not stage_directory.is_dir():
        raise ManifestError(f"{stage_name} artifact directory does not exist: {stage_directory}")
    matches = sorted(
        item for item in stage_directory.rglob(artifact_name) if item.is_file()
    )
    expected_artifact = stage_directory / artifact_name
    if len(matches) != 1 or _path_key(matches[0]) != _path_key(expected_artifact):
        raise ManifestError(
            f"{stage_name} artifacts must contain exactly one root {artifact_name}"
        )
    payload = _load_json_object(expected_artifact, label=f"{stage_name} inventory")
    if str(payload.get("schema_version", "")) != "1.0.0":
        raise ManifestError(f"{stage_name} inventory has an unsupported schema_version")
    source = payload.get("source")
    if not isinstance(source, Mapping) or str(source.get("sha256", "")).upper() != source_sha256:
        raise ManifestError(f"{stage_name} inventory is not bound to the frozen source")
    runtime = payload.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ManifestError(f"{stage_name} inventory is missing runtime evidence")
    if not isinstance(runtime.get("python_executable"), str) or not isinstance(
        runtime.get("python"), str
    ):
        raise ManifestError(f"{stage_name} inventory runtime evidence is incomplete")
    if host_runtime_receipt is not None:
        expected_runtime = host_runtime_receipt["runtime"]
        if _path_key(runtime["python_executable"]) != _path_key(
            expected_runtime["python_executable"]
        ):
            raise ManifestError(
                f"{stage_name} inventory used a different interpreter than the host receipt"
            )
        if runtime["python"] != expected_runtime["python_version"]:
            raise ManifestError(
                f"{stage_name} inventory used a different Python version than the host receipt"
            )
    inventory = inventory_artifact_directory(stage_directory)
    if inventory is None:  # pragma: no cover - guarded by the directory check above
        raise ManifestError(f"{stage_name} artifact inventory could not be constructed")
    return inventory


def validate_upstream_bindings(
    *,
    host_runtime_dir: str | Path | None,
    analysis_dir: str | Path | None,
    segment_dir: str | Path | None,
    run_id: str,
    source_sha256: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bind upstream evidence, degrading only when runtime evidence was not supplied."""

    upstream_stages: list[dict[str, Any]] = []
    degradations: list[str] = []
    host_receipt: Mapping[str, Any] | None = None
    if host_runtime_dir is None:
        degradations.append("HOST_RUNTIME_RECEIPT_NOT_BOUND")
    else:
        host_receipt, inventory = validate_host_runtime_receipt(
            host_runtime_dir,
            run_id=run_id,
            source_sha256=source_sha256,
        )
        upstream_stages.append({"name": "host_runtime", **inventory})

    for stage_name, directory, artifact_name, missing_degradation in (
        ("analysis", analysis_dir, "inventory.json", "ANALYSIS_ARTIFACTS_NOT_BOUND"),
        (
            "segmentation",
            segment_dir,
            "panels.json",
            "SEGMENTATION_ARTIFACTS_NOT_BOUND",
        ),
    ):
        if directory is None:
            degradations.append(missing_degradation)
            continue
        inventory = validate_host_stage_artifact(
            directory,
            stage_name=stage_name,
            artifact_name=artifact_name,
            source_sha256=source_sha256,
            host_runtime_receipt=host_receipt,
        )
        upstream_stages.append({"name": stage_name, **inventory})
        if host_receipt is None:
            degradations.append(f"{stage_name.upper()}_RUNTIME_NOT_VALIDATED")

    return upstream_stages, sorted(set(degradations))


def _markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def build_text_review(
    run_id: str,
    source: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# OCR Text Review — unverified hypotheses",
        "",
        f"- Run ID: `{run_id}`",
        f"- Source SHA-256: `{source['sha256']}`",
        "- Policy: OCR scores are model self-reports, not user-confirmed truth.",
        "- Action: verify every row against the frozen source or an authoritative text/LaTeX source.",
        "- Formula-like rows must not be promoted from OCR; obtain confirmed LaTeX.",
        "",
        "| ID | OCR hypothesis | OCR score | Band | bbox (x,y,w,h) | Alternatives | Flags | User-confirmed text | Decision |",
        "|---|---|---:|---|---|---|---|---|---|",
    ]
    for candidate in candidates:
        box = candidate["bbox_source"]
        alternatives = "; ".join(
            f"{item['text']} ({item['ocr_confidence']:.3f})" for item in candidate["alternatives"]
        )
        flags = ", ".join(candidate["review_flags"])
        lines.append(
            "| {id} | {text} | {score:.4f} | {band} | {box} | {alts} | {flags} |  | PENDING |".format(
                id=candidate["candidate_id"],
                text=_markdown_escape(candidate["text"]),
                score=float(candidate["ocr_confidence"]),
                band=candidate["confidence_band"],
                box=(f"{box['x']:.1f},{box['y']:.1f},{box['w']:.1f},{box['h']:.1f}"),
                alts=_markdown_escape(alternatives),
                flags=_markdown_escape(flags),
            )
        )
    lines.extend(
        [
            "",
            "## Decision vocabulary",
            "",
            "Use `CONFIRMED`, `CORRECTED`, `INCONCLUSIVE`, or `NOT_TEXT`. Keep the exact confirmed wording in the user-confirmed column.",
            "",
        ]
    )
    return "\n".join(lines)


def write_overlay(
    source_path: str | Path,
    output_path: str | Path,
    candidates: Sequence[Mapping[str, Any]],
) -> None:
    from PIL import Image, ImageDraw, ImageFont  # lazy dependency

    image = Image.open(source_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    colors = {
        "OCR_HIGH": (230, 140, 0),
        "OCR_MEDIUM": (216, 90, 0),
        "OCR_LOW": (210, 25, 45),
        "OCR_CONFLICT": (145, 45, 170),
    }
    for candidate in candidates:
        box = candidate["bbox_source"]
        x0, y0 = float(box["x"]), float(box["y"])
        x1, y1 = x0 + float(box["w"]), y0 + float(box["h"])
        color = colors[candidate["confidence_band"]]
        width = 3 if candidate["confidence_band"] == "OCR_CONFLICT" else 2
        draw.rectangle((x0, y0, x1, y1), outline=color, width=width)
        label = f"{candidate['candidate_id']} {candidate['ocr_confidence']:.2f}"
        label_box = draw.textbbox((x0, max(0, y0 - 12)), label, font=font)
        draw.rectangle(label_box, fill=(255, 255, 255))
        draw.text((x0, max(0, y0 - 12)), label, fill=color, font=font)

    target = _validated_output(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp{target.suffix}"
    )
    try:
        image.save(temporary, format="PNG")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _generate_run_id(source_hash: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"perception-{stamp}-{source_hash[:8].lower()}-{uuid.uuid4().hex[:6]}"


def _build_summary(
    observations: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    scores = [float(candidate["ocr_confidence"]) for candidate in candidates]
    bands = {
        band: sum(candidate["confidence_band"] == band for candidate in candidates)
        for band in ("OCR_HIGH", "OCR_MEDIUM", "OCR_LOW", "OCR_CONFLICT")
    }
    return {
        "raw_observation_count": len(observations),
        "candidate_count": len(candidates),
        "deduplicated_observation_count": len(observations) - len(candidates),
        "conflict_count": bands["OCR_CONFLICT"],
        "confidence_band_counts": bands,
        "ocr_confidence_stats": {
            "min": min(scores) if scores else None,
            "mean": statistics.fmean(scores) if scores else None,
            "median": statistics.median(scores) if scores else None,
            "max": max(scores) if scores else None,
        },
        "user_confirmed_count": 0,
        "review_required": True,
    }


def evaluate_acceptance(
    source_hash: str,
    candidates: Sequence[Mapping[str, Any]],
    acceptance_fixture: Mapping[str, Any],
) -> dict[str, Any]:
    checks = [
        {
            "name": "nonzero_candidates",
            "required": True,
            "passed": bool(candidates),
            "detail": f"candidate_count={len(candidates)}",
        }
    ]
    fixture_source_hash = str(acceptance_fixture["sha256"]).upper()
    fixture_applied = source_hash.upper() == fixture_source_hash
    if fixture_applied:
        expectations = acceptance_fixture["ocrSmokeExpectations"]
        minimum = int(expectations["minimumDetectedTextBoxes"])
        checks.append(
            {
                "name": "fixture_minimum_detected_text_boxes",
                "required": True,
                "passed": len(candidates) >= minimum,
                "detail": f"candidate_count={len(candidates)}, required>={minimum}",
            }
        )
        normalized_candidates = fixture_search_texts(candidates)
        for anchor in expectations["requiredExactAnchors"]:
            normalized_anchor = normalize_text(anchor)
            found = any(
                normalized_anchor in candidate_text for candidate_text in normalized_candidates
            )
            checks.append(
                {
                    "name": f"fixture_anchor:{anchor}",
                    "required": True,
                    "passed": found,
                    "detail": "found" if found else "missing",
                }
            )
        formula_candidates = [
            candidate
            for candidate in candidates
            if "FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"
            in {str(flag) for flag in candidate.get("review_flags", []) or []}
        ]
        similarity_min = float(expectations["formulaCandidateSimilarityMin"])
        for expected_formula in expectations["mustRemainFormulaCandidates"]:
            matches = [
                (text_similarity(expected_formula, str(candidate.get("text", ""))), candidate)
                for candidate in formula_candidates
            ]
            best_similarity, best_candidate = max(matches, default=(0.0, None), key=lambda item: item[0])
            found = best_similarity >= similarity_min
            checks.append(
                {
                    "name": f"fixture_formula_candidate:{expected_formula}",
                    "required": True,
                    "passed": found,
                    "detail": (
                        f"candidate_id={best_candidate.get('candidate_id')}, similarity={best_similarity:.4f}"
                        if found and best_candidate is not None
                        else f"missing, best_similarity={best_similarity:.4f}, required>={similarity_min:.4f}"
                    ),
                }
            )
    return {
        "fixture_applied": fixture_applied,
        "passed": all(check["passed"] for check in checks if check["required"]),
        "checks": checks,
    }


def _resolve_local_ref(root_schema: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ManifestError(f"Only local JSON Schema references are supported: {reference}")
    node: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _schema_type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, Mapping)
    raise ManifestError(f"Unsupported JSON Schema type: {expected}")


def validate_against_schema(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    root_schema: Mapping[str, Any] | None = None,
    path: str = "$",
) -> None:
    """Validate the manifest with the checked-in schema without a new dependency.

    This implements the Draft 2020-12 keywords used by our schema. Unsupported
    keywords are intentionally not guessed; the checked-in schema is the contract.
    """

    root = root_schema or schema
    if "$ref" in schema:
        validate_against_schema(
            instance,
            _resolve_local_ref(root, schema["$ref"]),
            root_schema=root,
            path=path,
        )
        return
    if "oneOf" in schema:
        match_count = 0
        errors = []
        for option in schema["oneOf"]:
            try:
                validate_against_schema(instance, option, root_schema=root, path=path)
                match_count += 1
            except ManifestError as exc:
                errors.append(str(exc))
        if match_count != 1:
            raise ManifestError(f"{path}: oneOf matched {match_count} branches; errors={errors}")
        return

    if "type" in schema:
        expected_types = schema["type"]
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        if not any(_schema_type_matches(instance, item) for item in expected_types):
            raise ManifestError(
                f"{path}: expected type {expected_types}, got {type(instance).__name__}"
            )
    if "const" in schema and instance != schema["const"]:
        raise ManifestError(f"{path}: expected constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise ManifestError(f"{path}: value {instance!r} is outside enum {schema['enum']!r}")

    if isinstance(instance, Mapping):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise ManifestError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(properties))
            if extra:
                raise ManifestError(f"{path}: additional properties are forbidden: {extra}")
        for key, value in instance.items():
            if key in properties:
                validate_against_schema(
                    value,
                    properties[key],
                    root_schema=root,
                    path=f"{path}.{key}",
                )

    if isinstance(instance, list):
        if len(instance) < int(schema.get("minItems", 0)):
            raise ManifestError(f"{path}: array is shorter than minItems")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            raise ManifestError(f"{path}: array is longer than maxItems")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in instance]
            if len(serialized) != len(set(serialized)):
                raise ManifestError(f"{path}: array items are not unique")
        if "items" in schema:
            for index, value in enumerate(instance):
                validate_against_schema(
                    value,
                    schema["items"],
                    root_schema=root,
                    path=f"{path}[{index}]",
                )

    if isinstance(instance, str):
        if len(instance) < int(schema.get("minLength", 0)):
            raise ManifestError(f"{path}: string is shorter than minLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ManifestError(f"{path}: string does not match {schema['pattern']!r}")
        if schema.get("format") == "date-time":
            try:
                dt.datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ManifestError(f"{path}: invalid date-time {instance!r}") from exc

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ManifestError(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ManifestError(f"{path}: number is above maximum")


def validate_manifest_against_schema(manifest: Mapping[str, Any], schema_path: str | Path) -> None:
    validate_manifest_shape(manifest)
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Unable to load perception manifest schema: {exc}") from exc
    validate_against_schema(manifest, schema)


def run_perception(args: argparse.Namespace) -> Path:
    config = load_config(args.config)
    acceptance_fixture, acceptance_fixture_evidence = load_acceptance_fixture(config)
    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        raise ManifestError(f"Source image does not exist: {source_path}")
    source_hash = sha256_file(source_path)
    run_id = args.run_id or _generate_run_id(source_hash)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,127}", run_id):
        raise ManifestError("run_id contains unsupported characters or has invalid length")
    upstream_stages, upstream_degradations = validate_upstream_bindings(
        host_runtime_dir=args.host_runtime_dir,
        analysis_dir=args.analysis_dir,
        segment_dir=args.segment_dir,
        run_id=run_id,
        source_sha256=source_hash,
    )
    output_dir = _validated_output(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_evidence = validate_python_and_packages(config)
    model_evidence = validate_model_files(config)
    script_evidence = _hash_project_scripts(config)
    config_path = Path(config["_config_path"])
    schema_path = (PROJECT_ROOT / config["manifest_schema"]).resolve()
    if not schema_path.is_file():
        raise ManifestError(f"Manifest schema is missing: {schema_path}")

    _prepare_download_restricted_runtime(output_dir, config)

    # Heavy imports occur only after every cheap reproducibility check above.
    import cv2
    import numpy as np
    import paddle
    import paddleocr
    import paddlex
    import PIL
    import scipy
    from PIL import Image
    from paddleocr import PaddleOCR

    imported_versions = {
        "paddle": str(paddle.__version__),
        "paddleocr": str(paddleocr.__version__),
        "paddlex": str(paddlex.__version__),
        "numpy": str(np.__version__),
        "pillow": str(PIL.__version__),
        "scipy": str(scipy.__version__),
        "opencv": str(cv2.__version__),
    }
    for name, actual in imported_versions.items():
        package = config["runtime"]["packages"][name]
        expected = package.get("import_version", package["version"])
        if actual != expected:
            raise ManifestError(
                f"Imported {name} version mismatch: expected {expected}, got {actual}"
            )

    requested_device = args.device or config["runtime"]["device"]
    resolved_device, device_evidence = _resolve_device(requested_device, paddle)

    with Image.open(source_path) as source_image:
        source_image.load()
        source_format = source_image.format or source_path.suffix.lstrip(".").upper()
        source_mode = source_image.mode
        source_rgb = source_image.convert("RGB")
        source_width, source_height = source_rgb.size

    tile_config = dict(config["tiling"])
    if args.no_tiles:
        tile_config["enabled"] = False
    if args.tile_grid:
        match = re.fullmatch(r"(\d+)x(\d+)", args.tile_grid.lower())
        if not match:
            raise ManifestError("--tile-grid must use ROWSxCOLUMNS, for example 2x2")
        tile_config["rows"], tile_config["columns"] = map(int, match.groups())
    if args.tile_overlap_px is not None:
        tile_config["overlap_px"] = args.tile_overlap_px
    if args.tile_upscale is not None:
        tile_config["upscale"] = args.tile_upscale
    base_views = build_views(
        source_width,
        source_height,
        rows=int(tile_config["rows"]),
        columns=int(tile_config["columns"]),
        overlap_px=int(tile_config["overlap_px"]),
        upscale=float(tile_config["upscale"]),
        include_full=True,
        tiles_enabled=bool(tile_config["enabled"]),
    )

    inference = config["inference"]
    model_args = {
        "text_detection_model_name": model_evidence["text_detection"]["name"],
        "text_detection_model_dir": model_evidence["text_detection"]["path"],
        "text_recognition_model_name": model_evidence["text_recognition"]["name"],
        "text_recognition_model_dir": model_evidence["text_recognition"]["path"],
        "textline_orientation_model_name": model_evidence["textline_orientation"]["name"],
        "textline_orientation_model_dir": model_evidence["textline_orientation"]["path"],
    }
    ocr_args = {
        **model_args,
        **inference,
        "device": resolved_device,
        "enable_hpi": bool(config["runtime"]["enable_hpi"]),
        "use_tensorrt": bool(config["runtime"]["use_tensorrt"]),
    }

    initialized_at = time.perf_counter()
    ocr = PaddleOCR(**ocr_args)
    initialization_seconds = time.perf_counter() - initialized_at
    observations: list[dict[str, Any]] = []
    view_timings = []

    def run_views(view_batch: Sequence[ViewSpec]) -> None:
        for view in view_batch:
            if view.kind == "full" and view.rotation_degrees == 0:
                ocr_input: Any = str(source_path)
            else:
                crop = source_rgb.crop((view.x, view.y, view.x + view.width, view.y + view.height))
                if view.upscale != 1:
                    crop = crop.resize(
                        (
                            int(round(view.width * view.upscale)),
                            int(round(view.height * view.upscale)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                if view.rotation_degrees == 90:
                    crop = crop.transpose(Image.Transpose.ROTATE_270)
                elif view.rotation_degrees == 180:
                    crop = crop.transpose(Image.Transpose.ROTATE_180)
                elif view.rotation_degrees == 270:
                    crop = crop.transpose(Image.Transpose.ROTATE_90)
                elif view.rotation_degrees != 0:
                    raise ManifestError(f"Unsupported view rotation: {view.rotation_degrees}")
                ocr_input = np.asarray(crop, dtype=np.uint8)[:, :, ::-1].copy()
            started = time.perf_counter()
            results = ocr.predict(ocr_input)
            duration = time.perf_counter() - started
            before = len(observations)
            for result in results:
                observations.extend(
                    extract_observations(
                        result,
                        view,
                        source_width=source_width,
                        source_height=source_height,
                        start_index=len(observations) + 1,
                    )
                )
            view_timings.append(
                {
                    "view_id": view.view_id,
                    "seconds": round(duration, 6),
                    "observation_count": len(observations) - before,
                }
            )

    try:
        run_views(base_views)
        first_pass_candidates = merge_observations(
            observations,
            config["deduplication"],
            config["confidence"],
        )
        quarter_turn_settings = dict(config["quarter_turn_review"])
        if args.no_quarter_turn_review:
            quarter_turn_settings["enabled"] = False
        rotation_views = build_quarter_turn_tile_sweep_views(
            base_views,
            quarter_turn_settings,
        )
        if quarter_turn_settings.get("candidate_crop_enabled", False):
            rotation_views.extend(
                build_rotation_review_views(
                    first_pass_candidates,
                    source_width,
                    source_height,
                    quarter_turn_settings,
                )
            )
        run_views(rotation_views)
    finally:
        close = getattr(ocr, "close", None)
        if callable(close):
            close()

    views = [*base_views, *rotation_views]

    candidates = merge_observations(
        observations,
        config["deduplication"],
        config["confidence"],
    )
    source_record = {
        "path": str(source_path),
        "sha256": source_hash,
        "size_bytes": source_path.stat().st_size,
        "width_px": source_width,
        "height_px": source_height,
        "pixel_mode": source_mode,
        "format": source_format,
    }

    review_path = output_dir / "text_review.md"
    overlay_path = output_dir / "ocr_overlay.png"
    manifest_path = output_dir / "perception-manifest.json"
    atomic_write_text(review_path, build_text_review(run_id, source_record, candidates))
    write_overlay(source_path, overlay_path, candidates)

    degradations = [*(args.degraded_reason or []), *upstream_degradations]
    if args.no_tiles:
        degradations.append("TILING_DISABLED")
    if args.no_quarter_turn_review:
        degradations.append("QUARTER_TURN_REVIEW_DISABLED")
    degradations = sorted(set(degradations))
    acceptance = evaluate_acceptance(source_hash, candidates, acceptance_fixture)
    status = (
        "OCR_HYPOTHESES_REVIEW_REQUIRED"
        if acceptance["passed"] and not degradations
        else "OCR_HYPOTHESES_INCONCLUSIVE"
    )

    manifest = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "created_at_utc": utc_now(),
        "status": status,
        "degradations": degradations,
        "acceptance_checks": acceptance,
        "policy": {
            "ocr_is_ground_truth": False,
            "all_candidates_require_human_review": True,
            "formula_ocr_may_not_be_promoted_without_confirmed_source": True,
            "network_access": "NETWORK_NOT_REQUESTED_BY_PIPELINE",
            "model_downloads": "DISABLED",
        },
        "source": source_record,
        "runtime": {
            **runtime_evidence,
            "imported_versions": imported_versions,
            "device_requested": requested_device,
            "device_resolved": resolved_device,
            "backend": "native_paddle_inference",
            "enable_hpi": bool(config["runtime"]["enable_hpi"]),
            "use_tensorrt": bool(config["runtime"]["use_tensorrt"]),
            "download_fallbacks_disabled": True,
            **device_evidence,
        },
        "models": model_evidence,
        "configuration": {
            "path": str(config_path.resolve()),
            "sha256": sha256_file(config_path),
            "manifest_schema_path": str(schema_path),
            "manifest_schema_sha256": sha256_file(schema_path),
            "inference": inference,
            "tiling": tile_config,
            "quarter_turn_review": quarter_turn_settings,
            "acceptance_fixture": acceptance_fixture_evidence,
            "confidence": config["confidence"],
            "deduplication": config["deduplication"],
        },
        "scripts": script_evidence,
        "upstream_stages": upstream_stages,
        "views": [view.to_manifest() for view in views],
        "timings": {
            "initialization_seconds": round(initialization_seconds, 6),
            "views": view_timings,
            "prediction_seconds": round(sum(item["seconds"] for item in view_timings), 6),
        },
        "raw_observations": observations,
        "text_candidates": candidates,
        "summary": _build_summary(observations, candidates),
        "artifacts": {
            "manifest": {"path": str(manifest_path), "sha256": None},
            "text_review": {
                "path": str(review_path),
                "sha256": sha256_file(review_path),
            },
            "overlay": {
                "path": str(overlay_path),
                "sha256": sha256_file(overlay_path),
            },
        },
    }
    validate_manifest_against_schema(manifest, schema_path)
    atomic_write_json(manifest_path, manifest)
    return manifest_path


def validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "run_id",
        "created_at_utc",
        "status",
        "degradations",
        "acceptance_checks",
        "policy",
        "source",
        "runtime",
        "models",
        "configuration",
        "scripts",
        "views",
        "raw_observations",
        "text_candidates",
        "summary",
        "artifacts",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ManifestError(f"Manifest is missing fields: {', '.join(missing)}")
    if manifest["policy"].get("ocr_is_ground_truth") is not False:
        raise ManifestError("Manifest must explicitly state that OCR is not ground truth")
    if manifest["policy"].get("network_access") != "NETWORK_NOT_REQUESTED_BY_PIPELINE":
        raise ManifestError(
            "Manifest must not claim process-level network blocking without evidence"
        )
    for candidate in manifest["text_candidates"]:
        if candidate["verification"]["status"] not in {"UNVERIFIED", "CONFLICT"}:
            raise ManifestError("OCR candidates cannot self-promote to confirmed text")
        if candidate["requires_human_review"] is not True:
            raise ManifestError("Every OCR candidate must require human review")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local PP-OCRv6-medium perception and write a review manifest."
    )
    parser.add_argument("source", nargs="?", help="Frozen source image")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Pinned OCR config JSON")
    parser.add_argument("--output-dir", help="Isolated output directory")
    parser.add_argument("--run-id", help="Run identifier supplied by the launcher")
    parser.add_argument(
        "--device",
        default=None,
        help="auto, cpu, gpu, or gpu:N (default comes from config)",
    )
    parser.add_argument("--no-tiles", action="store_true", help="Run full image only")
    parser.add_argument("--tile-grid", help="Override tile grid, e.g. 2x2")
    parser.add_argument("--tile-overlap-px", type=int)
    parser.add_argument("--tile-upscale", type=float)
    parser.add_argument(
        "--no-quarter-turn-review",
        action="store_true",
        help="Disable bounded 90/270-degree retries for likely vertical text regions",
    )
    parser.add_argument("--analysis-dir", help="Optional deterministic-analysis artifacts")
    parser.add_argument("--segment-dir", help="Optional segmentation artifacts")
    parser.add_argument(
        "--host-runtime-dir",
        help="Optional hash-bound host CV runtime receipt directory",
    )
    parser.add_argument(
        "--degraded-reason",
        action="append",
        default=[],
        help="Record an intentional stage degradation; makes the gate INCONCLUSIVE",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate interpreter, package versions, and model hashes without importing Paddle",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.validate_only:
            _, acceptance_fixture_evidence = load_acceptance_fixture(config)
            evidence = {
                "runtime": validate_python_and_packages(config),
                "models": validate_model_files(config),
                "acceptance_fixture": acceptance_fixture_evidence,
                "config_sha256": sha256_file(config["_config_path"]),
                "validated_at_utc": utc_now(),
            }
            print(json.dumps(evidence, ensure_ascii=True, indent=2))
            return 0
        if not args.source or not args.output_dir:
            parser.error("source and --output-dir are required unless --validate-only is used")
        if args.device is not None and not DEVICE_RE.fullmatch(args.device):
            raise ManifestError(f"Invalid --device value: {args.device}")
        manifest_path = run_perception(args)
        manifest_status = json.loads(manifest_path.read_text(encoding="utf-8"))["status"]
        print(
            json.dumps(
                {
                    "status": manifest_status,
                    "manifest": str(manifest_path),
                },
                ensure_ascii=True,
            )
        )
        return 0 if manifest_status == "OCR_HYPOTHESES_REVIEW_REQUIRED" else 3
    except ManifestError as exc:
        print(f"PERCEPTION_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
