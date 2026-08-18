#!/usr/bin/env python3
"""Deterministically build the hash-bound agent-vision task package.

The package turns the outer agent's native-vision role into a protocol: it
receives crops, prompt templates, and per-query response skeletons, and its
filled response is later validated and fused as candidate evidence only.  The
builder itself never asks a model anything; it only measures, crops, hashes,
and binds.  Upstream manifests must be schema-valid, hash-consistent, and
produced by the pinned Host CV interpreter, mirroring the Phase-1 geometry
input contract.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import math
import os
import platform
import random
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import resolve_output_path
except ModuleNotFoundError:  # Support: python -m tools.prepare_agent_vision_task
    try:
        from .output_policy import resolve_output_path
    except ImportError:  # Support importlib loading a standalone file from the root.
        from tools.output_policy import resolve_output_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "perception-manifest.schema.json"
GEOMETRY_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "geometry-manifest.schema.json"
HOST_RECEIPT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "host-runtime-receipt.schema.json"
TASK_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "agent-vision-task.schema.json"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "agent-vision-config.json"

EXIT_OK = 0
EXIT_CONTRACT_REJECTED = 2
EXIT_INCONCLUSIVE = 3

TASK_TYPE_STRUCTURE = "STRUCTURE_GLOBAL"
TASK_TYPE_CONFLICT = "CONFLICT_ARBITRATION"
TASK_TYPE_FORMULA = "FORMULA_TRANSCRIPTION"
TASK_TYPE_MISS = "MISS_SCAN"

TEMPLATE_BY_TASK_TYPE = {
    TASK_TYPE_STRUCTURE: "structure_global",
    TASK_TYPE_CONFLICT: "conflict_arbitration",
    TASK_TYPE_FORMULA: "formula_transcription",
    TASK_TYPE_MISS: "miss_scan",
}

CANONICAL_RECEIPT_BINDINGS = {
    "runtime_config": PROJECT_ROOT / "host-runtime.json",
    "requirements": PROJECT_ROOT / "requirements.txt",
    "receipt_schema": HOST_RECEIPT_SCHEMA_PATH,
    "validator": TOOLS_DIRECTORY / "validate_host_runtime.py",
}


class TaskPackageError(RuntimeError):
    """A fail-closed validation or assembly error."""


class UpstreamInconclusive(RuntimeError):
    """Upstream evidence is not review-ready, so no package can be built."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TaskPackageError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TaskPackageError(f"{label} must be a JSON object")
    return value


def load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise TaskPackageError(f"Unable to read {label} {source}: {exc}") from exc
    return parse_json_bytes(payload, label=label)


def load_schema(path: str | Path, label: str) -> dict[str, Any]:
    schema = load_json_object(path, label)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise TaskPackageError(f"Invalid {label}: {exc.message}") from exc
    return schema


def validate_json(instance: Any, schema: Mapping[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    details = []
    for error in errors[:5]:
        location = "$"
        for part in error.absolute_path:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        details.append(f"{location}: {error.message}")
    if len(errors) > 5:
        details.append(f"... and {len(errors) - 5} more validation error(s)")
    raise TaskPackageError(f"{label} is not schema-valid: " + "; ".join(details))


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    destination = resolve_output_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, serialized)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def _file_binding(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _manifest_binding(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    binding = _file_binding(path)
    binding.update(
        {
            "schema_version": manifest["schema_version"],
            "run_id": manifest["run_id"],
            "source_sha256": str(manifest["source"]["sha256"]).upper(),
        }
    )
    return binding


def _validate_runtime_environment(
    receipt: Mapping[str, Any], *, require_isolated_runtime: bool
) -> None:
    runtime = receipt["runtime"]
    actual_python = Path(sys.executable).resolve(strict=False)
    expected_python = Path(runtime["python_executable"]).resolve(strict=False)
    if actual_python != expected_python:
        raise TaskPackageError(
            f"current interpreter does not match host receipt: {actual_python} != {expected_python}"
        )
    if platform.python_version() != runtime["python_version"]:
        raise TaskPackageError("current Python version does not match host receipt")
    if Path(sys.prefix).resolve(strict=False) != Path(runtime["prefix"]).resolve(strict=False):
        raise TaskPackageError("current sys.prefix does not match host receipt")
    if require_isolated_runtime and not (
        sys.flags.isolated
        and sys.flags.ignore_environment
        and sys.flags.no_user_site
        and sys.flags.safe_path
    ):
        raise TaskPackageError("task-package CLI must run under Host Python isolated mode (-I)")


def _validate_inputs(
    source_path: Path,
    ocr_path: Path,
    geometry_path: Path,
    receipt_path: Path,
    segment_dir: Path | None,
    config_path: Path,
    run_id: str,
    *,
    require_isolated_runtime: bool,
) -> tuple[
    Image.Image,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any],
    dict[str, Any],
    list[str],
]:
    ocr = load_json_object(ocr_path, "OCR perception manifest")
    geometry = load_json_object(geometry_path, "geometry manifest")
    receipt = load_json_object(receipt_path, "host runtime receipt")
    config = load_json_object(config_path, "agent-vision config")

    validate_json(ocr, load_schema(OCR_SCHEMA_PATH, "perception manifest schema"), "OCR manifest")
    validate_json(
        geometry,
        load_schema(GEOMETRY_SCHEMA_PATH, "geometry manifest schema"),
        "geometry manifest",
    )
    validate_json(
        receipt,
        load_schema(HOST_RECEIPT_SCHEMA_PATH, "host runtime receipt schema"),
        "host runtime receipt",
    )

    degradations: list[str] = []
    if str(ocr["status"]) != "OCR_HYPOTHESES_REVIEW_REQUIRED":
        raise UpstreamInconclusive(f"ocr_manifest_status={ocr['status']}")
    if str(geometry["status"]) != "GEOMETRY_OBSERVATIONS_READY":
        raise UpstreamInconclusive(f"geometry_manifest_status={geometry['status']}")
    if str(ocr["run_id"]) != run_id or str(geometry["run_id"]) != run_id:
        raise TaskPackageError("upstream manifests are not bound to the requested run_id")

    if receipt["status"] != "PASS" or any(not item["passed"] for item in receipt["checks"]):
        raise TaskPackageError("host runtime receipt is not an all-checks PASS")
    isolation = receipt["isolation"]
    if not all(
        isolation[key]
        for key in ("required", "isolated", "ignore_environment", "no_user_site", "safe_path")
    ):
        raise TaskPackageError("host runtime receipt does not prove isolated execution")
    for key, binding in receipt["bindings"].items():
        bound_path = Path(str(binding["path"])).resolve(strict=False)
        if key not in CANONICAL_RECEIPT_BINDINGS or bound_path != CANONICAL_RECEIPT_BINDINGS[
            key
        ].resolve(strict=False):
            raise TaskPackageError(f"host receipt {key} is not the canonical project binding")
        if sha256_file(bound_path) != str(binding["sha256"]).upper():
            raise TaskPackageError(f"host receipt binding {key} is stale")

    source_hash = sha256_file(source_path)
    try:
        image = Image.open(source_path)
        image.load()
    except Exception as exc:
        raise TaskPackageError(f"input image cannot be decoded: {exc}") from exc
    if image.format != "PNG":
        raise TaskPackageError(f"input must be a PNG, got {image.format!r}")
    source_mode = image.mode
    width, height = image.size

    for label, manifest in (("OCR", ocr), ("geometry", geometry)):
        manifest_source = manifest["source"]
        if str(manifest_source["sha256"]).upper() != source_hash:
            raise TaskPackageError(f"{label} manifest source SHA-256 does not match input PNG")
        if (int(manifest_source["width_px"]), int(manifest_source["height_px"])) != (width, height):
            raise TaskPackageError(f"{label} manifest source dimensions do not match input PNG")
        if int(manifest_source["size_bytes"]) != source_path.stat().st_size:
            raise TaskPackageError(f"{label} manifest source size_bytes does not match input PNG")
        if str(manifest_source["pixel_mode"]) != source_mode:
            raise TaskPackageError(f"{label} manifest source pixel_mode does not match input PNG")

    geometry_ocr_binding = geometry["inputs"]["ocr_manifest"]
    if str(geometry_ocr_binding["sha256"]).upper() != sha256_file(ocr_path):
        raise TaskPackageError("geometry manifest is not bound to the supplied OCR manifest")

    context = receipt["context"]
    expected_context = {"run_id": run_id, "source_sha256": source_hash}
    actual_context = {
        "run_id": context["run_id"],
        "source_sha256": str(context["source_sha256"]).upper()
        if context["source_sha256"] is not None
        else None,
    }
    if actual_context != expected_context:
        raise TaskPackageError("host receipt context does not match the requested run/source")

    _validate_runtime_environment(receipt, require_isolated_runtime=require_isolated_runtime)

    panels = None
    if segment_dir is not None:
        panels_path = Path(segment_dir) / "panels.json"
        if panels_path.is_file():
            panels = load_json_object(panels_path, "segmentation panels")
            if str(panels.get("source", {}).get("sha256", "")).upper() != source_hash:
                raise TaskPackageError("segmentation panels.json source hash does not match input")
        else:
            degradations.append("SEGMENTATION_PANELS_MISSING")

    _validate_config(config)

    source_record = {
        "path": str(source_path.resolve()),
        "sha256": source_hash,
        "size_bytes": source_path.stat().st_size,
        "width_px": width,
        "height_px": height,
        "pixel_mode": source_mode,
        "format": "PNG",
    }
    return (
        image.convert("RGB"),
        ocr,
        geometry,
        receipt,
        panels,
        config,
        source_record,
        degradations,
    )


def _validate_config(config: Mapping[str, Any]) -> None:
    for key in ("crops", "limits", "alignment", "prompts"):
        if key not in config:
            raise TaskPackageError(f"agent-vision config is missing '{key}'")
    template_ids = [str(item["template_id"]) for item in config["prompts"]]
    expected = set(TEMPLATE_BY_TASK_TYPE.values())
    if set(template_ids) != expected or len(template_ids) != len(expected):
        raise TaskPackageError(
            f"agent-vision config must declare exactly the templates {sorted(expected)}"
        )
    for prompt in config["prompts"]:
        if not str(prompt.get("text", "")).strip():
            raise TaskPackageError(f"prompt template {prompt['template_id']} has empty text")


def _deterministic_shuffle_seed(candidate_id: str, texts: Sequence[str]) -> int:
    material = candidate_id + "|" + "|".join(sorted(texts))
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)


def _crop_region(
    box: Mapping[str, Any], image_size: tuple[int, int], padding: float
) -> tuple[int, int, int, int, dict[str, float]]:
    width, height = image_size
    x, y, w, h = (float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"]))
    x0 = max(0.0, x - padding)
    y0 = max(0.0, y - padding)
    x1 = min(float(width), x + w + padding)
    y1 = min(float(height), y + h + padding)
    left, top = math.floor(x0), math.floor(y0)
    right, bottom = math.ceil(x1), math.ceil(y1)
    if right <= left or bottom <= top:
        raise TaskPackageError(f"crop region degenerated: {(left, top, right, bottom)}")
    region = {
        "x": round(x0, 3),
        "y": round(y0, 3),
        "w": round(x1 - x0, 3),
        "h": round(y1 - y0, 3),
    }
    return left, top, right, bottom, region


def _render_crop(
    image: Image.Image,
    box: Mapping[str, Any],
    relative_path: str,
    *,
    padding: float,
    upscale: float,
) -> tuple[dict[str, Any], dict[str, float]]:
    left, top, right, bottom, region = _crop_region(box, image.size, padding)
    crop = image.crop((left, top, right, bottom))
    if abs(upscale - 1.0) > 1e-9:
        crop = crop.resize(
            (
                max(1, int(round(crop.width * upscale))),
                max(1, int(round(crop.height * upscale))),
            ),
            Image.Resampling.LANCZOS,
        )
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    payload = buffer.getvalue()
    artifact = {
        "relative_path": relative_path,
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
        "width_px": crop.width,
        "height_px": crop.height,
    }
    return artifact, (region, payload)


def _build_structure_query(
    image: Image.Image,
    crops_dir: Path,
    config: Mapping[str, Any],
    template_versions: Mapping[str, str],
) -> tuple[dict[str, Any], list[tuple[Path, bytes]]]:
    crop_payloads: list[tuple[Path, bytes]] = []
    full_box = {"x": 0.0, "y": 0.0, "w": float(image.width), "h": float(image.height)}
    full_artifact, (_, full_bytes) = _render_crop(
        image, full_box, "crops/full.png", padding=0.0, upscale=1.0
    )
    crop_payloads.append((crops_dir / "full.png", full_bytes))

    width, height = image.size
    halves = (
        ("tl", 0, 0, width // 2, height // 2),
        ("tr", width // 2, 0, width, height // 2),
        ("bl", 0, height // 2, width // 2, height),
        ("br", width // 2, height // 2, width, height),
    )
    quadrant_paths: list[str] = []
    for suffix, x0, y0, x1, y1 in halves:
        relative = f"crops/quadrant_{suffix}.png"
        box = {"x": float(x0), "y": float(y0), "w": float(x1 - x0), "h": float(y1 - y0)}
        _, (_, payload) = _render_crop(image, box, relative, padding=0.0, upscale=1.0)
        crop_payloads.append((crops_dir / f"quadrant_{suffix}.png", payload))
        quadrant_paths.append(relative)

    query = {
        "query_id": "V0001",
        "task_type": TASK_TYPE_STRUCTURE,
        "image": full_artifact,
        "prompt_template": {
            "template_id": "structure_global",
            "version": template_versions["structure_global"],
        },
        "payload": {
            "view": "FULL_IMAGE",
            "quadrant_crop_relative_paths": quadrant_paths,
        },
    }
    return query, crop_payloads


def _build_conflict_queries(
    image: Image.Image,
    crops_dir: Path,
    ocr: Mapping[str, Any],
    config: Mapping[str, Any],
    template_versions: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[tuple[Path, bytes]]]:
    limits = config["limits"]
    padding = float(config["crops"]["padding_px"])
    upscale = float(config["crops"]["upscale"])
    crop_payloads: list[tuple[Path, bytes]] = []

    conflicting = [
        candidate
        for candidate in ocr["text_candidates"]
        if candidate.get("alternatives")
        or candidate.get("verification", {}).get("status") == "CONFLICT"
    ]
    conflicting.sort(
        key=lambda item: (-float(item["ocr_confidence"]), item["candidate_id"])
    )
    conflicting = conflicting[: int(limits["max_conflict_queries"])]

    queries: list[dict[str, Any]] = []
    for candidate in conflicting:
        candidate_id = candidate["candidate_id"]
        texts = [str(candidate["text"])] + [
            str(alternative["text"]) for alternative in candidate.get("alternatives", [])
        ]
        generator = random.Random(_deterministic_shuffle_seed(candidate_id, texts))
        order = list(range(len(texts)))
        generator.shuffle(order)
        selections = [{"index": position, "text": texts[original]} for position, original in enumerate(order)]

        relative = f"crops/conflict/{candidate_id}.png"
        artifact, (region, payload) = _render_crop(
            image, candidate["bbox_envelope_source"], relative, padding=padding, upscale=upscale
        )
        crop_payloads.append((crops_dir / "conflict" / f"{candidate_id}.png", payload))
        queries.append(
            {
                "query_id": "",
                "task_type": TASK_TYPE_CONFLICT,
                "image": artifact,
                "prompt_template": {
                    "template_id": "conflict_arbitration",
                    "version": template_versions["conflict_arbitration"],
                },
                "payload": {
                    "candidate_id": candidate_id,
                    "crop_bbox_source": region,
                    "selections": selections,
                },
            }
        )
    return queries, crop_payloads


def _build_formula_queries(
    image: Image.Image,
    crops_dir: Path,
    ocr: Mapping[str, Any],
    config: Mapping[str, Any],
    template_versions: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[tuple[Path, bytes]]]:
    limits = config["limits"]
    padding = float(config["crops"]["padding_px"])
    upscale = float(config["crops"]["upscale"])
    crop_payloads: list[tuple[Path, bytes]] = []

    formula_like = [
        candidate
        for candidate in ocr["text_candidates"]
        if any(
            str(flag) == "FORMULA_LIKE_REQUIRES_CONFIRMED_SOURCE"
            for flag in candidate.get("review_flags", [])
        )
    ]
    formula_like.sort(key=lambda item: (-float(item["ocr_confidence"]), item["candidate_id"]))
    formula_like = formula_like[: int(limits["max_formula_queries"])]

    queries: list[dict[str, Any]] = []
    for candidate in formula_like:
        candidate_id = candidate["candidate_id"]
        relative = f"crops/formula/{candidate_id}.png"
        artifact, (region, payload) = _render_crop(
            image, candidate["bbox_envelope_source"], relative, padding=padding, upscale=upscale
        )
        crop_payloads.append((crops_dir / "formula" / f"{candidate_id}.png", payload))
        queries.append(
            {
                "query_id": "",
                "task_type": TASK_TYPE_FORMULA,
                "image": artifact,
                "prompt_template": {
                    "template_id": "formula_transcription",
                    "version": template_versions["formula_transcription"],
                },
                "payload": {
                    "candidate_id": candidate_id,
                    "crop_bbox_source": region,
                    "samples_required": int(limits["formula_samples"]),
                },
            }
        )
    return queries, crop_payloads


def _build_miss_scan_queries(
    image: Image.Image,
    crops_dir: Path,
    panels: Mapping[str, Any] | None,
    ocr: Mapping[str, Any],
    config: Mapping[str, Any],
    template_versions: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[tuple[Path, bytes]]]:
    if panels is None:
        return [], []
    limits = config["limits"]
    padding = float(config["crops"]["padding_px"])
    upscale = float(config["crops"]["upscale"])
    crop_payloads: list[tuple[Path, bytes]] = []

    envelopes = [
        candidate["bbox_envelope_source"] for candidate in ocr["text_candidates"]
    ]

    def max_ocr_containment(region_box: dict[str, float]) -> float:
        best = 0.0
        for envelope in envelopes:
            score = _containment(region_box, envelope)
            if score > best:
                best = score
        return best

    min_area = int(limits["min_region_area_px"])
    max_aspect = float(limits["max_miss_region_aspect_ratio"])
    containment_max = float(limits["min_miss_region_ocr_containment"])

    selected: list[tuple[float, str, dict[str, float]]] = []
    for region in panels.get("region_candidates", []):
        x, y, w, h = (float(value) for value in region["bbox"])
        box = {"x": x, "y": y, "w": w, "h": h}
        aspect = max(w, h) / max(1e-6, min(w, h))
        if float(region["area"]) < min_area or aspect > max_aspect:
            continue
        if max_ocr_containment(box) >= containment_max:
            continue
        selected.append((float(region["area"]), str(region["candidate_id"]), box))
    selected.sort(key=lambda item: (-item[0], item[1]))
    selected = selected[: int(limits["max_miss_scan_queries"])]

    queries: list[dict[str, Any]] = []
    for _, region_id, box in selected:
        relative = f"crops/miss/{region_id}.png"
        artifact, (region, payload) = _render_crop(
            image, box, relative, padding=padding, upscale=upscale
        )
        crop_payloads.append((crops_dir / "miss" / f"{region_id}.png", payload))
        queries.append(
            {
                "query_id": "",
                "task_type": TASK_TYPE_MISS,
                "image": artifact,
                "prompt_template": {
                    "template_id": "miss_scan",
                    "version": template_versions["miss_scan"],
                },
                "payload": {
                    "region_candidate_id": region_id,
                    "crop_bbox_source": region,
                },
            }
        )
    return queries, crop_payloads


def _containment(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    ax0, ay0 = float(a["x"]), float(a["y"])
    ax1, ay1 = ax0 + float(a["w"]), ay0 + float(a["h"])
    bx0, by0 = float(b["x"]), float(b["y"])
    bx1, by1 = bx0 + float(b["w"]), by0 + float(b["h"])
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(
        0.0, min(ay1, by1) - max(ay0, by0)
    )
    smaller = min(float(a["w"]) * float(a["h"]), float(b["w"]) * float(b["h"]))
    return intersection / smaller if smaller > 0 else 0.0


def _instructions_markdown(package: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    counts = package["summary"]
    return f"""# Agent 视觉任务包操作说明（run `{package['run_id']}`）

本目录由 `tools/prepare_agent_vision_task.py` 确定性生成，全部产物哈希绑定。
你（外层 Agent，原生多模态视觉）的任务：**逐查询看图填写** `response-template.json` 的副本，
保存为 `agent-vision-response.json`，再交给 `tools/validate_agent_vision.py` 校验。

## 查询清单（共 {counts['query_count']} 条）

| 类型 | 数量 | 裁剪图 | 你要做什么 |
|---|---|---|---|
| STRUCTURE_GLOBAL | {counts['structure_query_count']} | `crops/full.png` + 4 象限 | 只看图提出 panels 结构候选（不给 OCR 结果，保持独立） |
| CONFLICT_ARBITRATION | {counts['conflict_query_count']} | `crops/conflict/T****.png` | 从 selections 中选 1 个或 REJECT_ALL，禁止新文本 |
| FORMULA_TRANSCRIPTION | {counts['formula_query_count']} | `crops/formula/T****.png` | 独立采样 {config['limits']['formula_samples']} 次 LaTeX 转写 |
| MISS_SCAN | {counts['miss_scan_query_count']} | `crops/miss/*.png` | 判断 OCR 漏检区域是否含文字 |

## 硬规则

1. **禁止从 OCR manifest 或任何上游产物抄答案**；结构盘点尤其必须独立看图。
2. 坐标永远 advisory（`coordinates_advisory_only=true`）；文字与公式的最终权威在用户/原文。
3. 看不清就填 `NOT_OBSERVABLE`——这是诚实逃生门，不是失败。
4. 冲突仲裁没有自由文本字段；公式提议永远 `PROPOSAL_ONLY_NOT_AUTHORITATIVE`。
5. 填完后自检：每个 query_id 都有应答、结构字段与 task_type 匹配、`multi_pass_independence_attested` 如实填写。

## 流程

```
1. cp response-template.json agent-vision-response.json
2. 逐条看 crops/ 下对应裁剪图，填写 queries[]
3. & <HostPython> -I -B -X utf8 tools/validate_agent_vision.py \
     --task-package agent-vision/task-package.json \
     --response agent-vision/agent-vision-response.json \
     --output agent-vision/agent-vision-document.json
4. & <HostPython> -I -B -X utf8 tools/cross_modal_fusion.py --help  # 融合与审核队列
```
"""


def _response_template(package: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "document_type": "AGENT_VISION_RESPONSE",
        "created_at_utc": None,
        "task_package": {
            "path": "",
            "sha256": None,
            "run_id": package["run_id"],
            "source_sha256": package["source"]["sha256"],
        },
        "agent": {
            "role": "OUTER_AGENT_NATIVE_VISION",
            "declared_model": None,
            "multi_pass_independence_attested": False,
        },
        "policy": {
            **dict(package["policy"]),
            "observations_are_candidate_evidence_only": True,
        },
        "queries": [
            {
                "query_id": query["query_id"],
                "task_type": query["task_type"],
                "observation_status": "NOT_OBSERVABLE",
                "structure": None,
                "conflict": None,
                "formula": None,
                "miss_scan": None,
            }
            for query in package["queries"]
        ],
        "validation": None,
    }


def build_task_package(
    source_path: Path,
    ocr_path: Path,
    geometry_path: Path,
    receipt_path: Path,
    segment_dir: Path | None,
    config_path: Path,
    output_dir: Path,
    run_id: str,
    *,
    degraded_reasons: Sequence[str] = (),
    require_isolated_runtime: bool = True,
) -> tuple[Path, dict[str, Any]]:
    (
        image,
        ocr,
        geometry,
        receipt,
        panels,
        config,
        source_record,
        degradations,
    ) = _validate_inputs(
        source_path,
        ocr_path,
        geometry_path,
        receipt_path,
        segment_dir,
        config_path,
        run_id,
        require_isolated_runtime=require_isolated_runtime,
    )
    degradations.extend(str(reason) for reason in degraded_reasons)

    output_dir = Path(resolve_output_path(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = output_dir / "crops"

    template_versions = {
        str(prompt["template_id"]): str(prompt["version"]) for prompt in config["prompts"]
    }
    prompt_templates = [
        {
            "template_id": str(prompt["template_id"]),
            "version": str(prompt["version"]),
            "language": str(prompt["language"]),
            "sha256": sha256_bytes(str(prompt["text"]).encode("utf-8")),
        }
        for prompt in sorted(config["prompts"], key=lambda item: str(item["template_id"]))
    ]

    queries: list[dict[str, Any]] = []
    crop_payloads: list[tuple[Path, bytes]] = []

    structure_query, structure_crops = _build_structure_query(
        image, crops_dir, config, template_versions
    )
    queries.append(structure_query)
    crop_payloads.extend(structure_crops)

    conflict_queries, conflict_crops = _build_conflict_queries(
        image, crops_dir, ocr, config, template_versions
    )
    formula_queries, formula_crops = _build_formula_queries(
        image, crops_dir, ocr, config, template_versions
    )
    miss_queries, miss_crops = _build_miss_scan_queries(
        image, crops_dir, panels, ocr, config, template_versions
    )
    queries.extend(conflict_queries)
    queries.extend(formula_queries)
    queries.extend(miss_queries)
    crop_payloads.extend(conflict_crops)
    crop_payloads.extend(formula_crops)
    crop_payloads.extend(miss_crops)

    if panels is None and not any(
        reason.startswith("SEGMENTATION_") for reason in degradations
    ):
        degradations.append("SEGMENTATION_UNAVAILABLE")

    for index, query in enumerate(queries, start=1):
        query["query_id"] = f"V{index:04d}"

    task_schema = load_schema(TASK_SCHEMA_PATH, "agent-vision task schema")
    task_package_path = output_dir / "task-package.json"
    script_path = Path(__file__).resolve()

    def crop_relative(record: dict[str, Any]) -> str:
        return str(record["relative_path"])

    unique_crops = sorted(
        {crop_relative(query["image"]) for query in queries}
        | {
            path
            for query in queries
            if query["task_type"] == TASK_TYPE_STRUCTURE
            for path in query["payload"]["quadrant_crop_relative_paths"]
        }
    )

    package: dict[str, Any] = {
        "schema_version": "1.0.0",
        "document_type": "AGENT_VISION_TASK_PACKAGE",
        # Inherit the upstream OCR timestamp: identical inputs must yield a
        # byte-identical package across fresh directories.
        "created_at_utc": str(ocr["created_at_utc"]),
        "run_id": run_id,
        "status": "TASK_PACKAGE_DEGRADED" if degradations else "TASK_PACKAGE_READY",
        "degradations": sorted(set(degradations)),
        "source": source_record,
        "inputs": {
            "ocr_manifest": _manifest_binding(ocr_path, ocr),
            "geometry_manifest": _manifest_binding(geometry_path, geometry),
            "host_runtime_receipt": {
                **_file_binding(receipt_path),
                "schema_version": receipt["schema_version"],
                "status": receipt["status"],
                "context": {
                    "run_id": receipt["context"]["run_id"],
                    "source_sha256": str(receipt["context"]["source_sha256"]).upper(),
                },
                "runtime": {
                    "runtime_id": receipt["runtime"]["runtime_id"],
                    "python_executable": receipt["runtime"]["python_executable"],
                    "python_version": receipt["runtime"]["python_version"],
                },
            },
            "segmentation": _file_binding(Path(segment_dir) / "panels.json")
            if panels is not None
            else None,
            "config": _file_binding(config_path),
        },
        "policy": {
            "vlm_is_ground_truth": False,
            "coordinates_advisory_only": True,
            "may_not_invent_text": True,
            "agent_must_observe_images_directly": True,
        },
        "limits": {
            "max_conflict_queries": int(config["limits"]["max_conflict_queries"]),
            "max_formula_queries": int(config["limits"]["max_formula_queries"]),
            "max_miss_scan_queries": int(config["limits"]["max_miss_scan_queries"]),
            "max_panel_proposals": int(config["limits"]["max_panel_proposals"]),
            "min_region_area_px": int(config["limits"]["min_region_area_px"]),
            "max_miss_region_aspect_ratio": float(config["limits"]["max_miss_region_aspect_ratio"]),
            "min_miss_region_ocr_containment": float(
                config["limits"]["min_miss_region_ocr_containment"]
            ),
            "max_text_length_chars": int(config["limits"]["max_text_length_chars"]),
            "formula_samples": int(config["limits"]["formula_samples"]),
        },
        "prompt_templates": prompt_templates,
        "queries": queries,
        "summary": {
            "query_count": len(queries),
            "structure_query_count": 1,
            "conflict_query_count": len(conflict_queries),
            "formula_query_count": len(formula_queries),
            "miss_scan_query_count": len(miss_queries),
            "crop_count": len(unique_crops),
        },
        "implementation": {
            "algorithm_id": "agent_vision_task_package_builder",
            "version": "1.0.0",
            "script": {
                "path": str(script_path),
                "relative_path": "tools/prepare_agent_vision_task.py",
                "size_bytes": script_path.stat().st_size,
                "sha256": sha256_file(script_path),
            },
            "schema": {
                "path": str(TASK_SCHEMA_PATH.resolve()),
                "relative_path": "schemas/agent-vision-task.schema.json",
                "size_bytes": TASK_SCHEMA_PATH.stat().st_size,
                "sha256": sha256_file(TASK_SCHEMA_PATH),
            },
        },
    }

    validate_json(package, task_schema, "generated task package")

    for destination, payload in crop_payloads:
        atomic_write_bytes(Path(destination), payload)
    written = {
        str(destination.relative_to(output_dir)).replace("\\", "/")
        for destination, _ in crop_payloads
    }
    for relative in unique_crops:
        if relative not in written:
            raise TaskPackageError(f"declared crop was never rendered: {relative}")

    atomic_write_json(task_package_path, package)
    atomic_write_text(output_dir / "INSTRUCTIONS.md", _instructions_markdown(package, config))
    response = _response_template(package)
    response["task_package"]["path"] = str(task_package_path.resolve())
    response["task_package"]["sha256"] = sha256_file(task_package_path)
    atomic_write_json(output_dir / "response-template.json", response)
    return task_package_path, package


def verify_task_package_file(path: Path) -> dict[str, Any]:
    package = load_json_object(path, "agent-vision task package")
    task_schema = load_schema(TASK_SCHEMA_PATH, "agent-vision task schema")
    validate_json(package, task_schema, "agent-vision task package")
    base = path.resolve().parent
    seen: set[str] = set()
    for query in package["queries"]:
        record = query["image"]
        crop_path = base / str(record["relative_path"])
        if not crop_path.is_file():
            raise TaskPackageError(f"task-package crop is missing: {crop_path}")
        if sha256_file(crop_path) != str(record["sha256"]).upper():
            raise TaskPackageError(f"task-package crop hash mismatch: {crop_path}")
        if crop_path.stat().st_size != int(record["size_bytes"]):
            raise TaskPackageError(f"task-package crop size mismatch: {crop_path}")
        seen.add(str(record["relative_path"]))
        if query["task_type"] == TASK_TYPE_STRUCTURE:
            for relative in query["payload"]["quadrant_crop_relative_paths"]:
                quadrant_path = base / relative
                if not quadrant_path.is_file():
                    raise TaskPackageError(f"task-package quadrant crop is missing: {quadrant_path}")
                seen.add(relative)
    for key, count in (
        ("query_count", len(package["queries"])),
        (
            "conflict_query_count",
            sum(1 for q in package["queries"] if q["task_type"] == TASK_TYPE_CONFLICT),
        ),
        (
            "formula_query_count",
            sum(1 for q in package["queries"] if q["task_type"] == TASK_TYPE_FORMULA),
        ),
        (
            "miss_scan_query_count",
            sum(1 for q in package["queries"] if q["task_type"] == TASK_TYPE_MISS),
        ),
    ):
        if int(package["summary"][key]) != count:
            raise TaskPackageError(f"task-package summary.{key} mismatch")
    if int(package["summary"]["crop_count"]) != len(seen):
        raise TaskPackageError("task-package summary.crop_count mismatch")
    return package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the hash-bound agent-vision task package for native-vision review."
    )
    parser.add_argument("--input", type=Path, help="Frozen source PNG")
    parser.add_argument("--ocr-manifest", type=Path, help="Bound OCR perception manifest")
    parser.add_argument("--geometry-manifest", type=Path, help="Bound Phase-1 geometry manifest")
    parser.add_argument(
        "--segment-dir", type=Path, default=None, help="Segmentation output directory"
    )
    parser.add_argument("--host-runtime-receipt", type=Path, help="PASS host runtime receipt")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="agent-vision-config.json"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="agent-vision stage output directory"
    )
    parser.add_argument("--run-id", default=None, help="Perception run id")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root for output policy",
    )
    parser.add_argument(
        "--degraded-reason",
        action="append",
        default=[],
        help="Explicit degradation reason to record (repeatable)",
    )
    parser.add_argument(
        "--verify-package",
        type=Path,
        default=None,
        help="Read-only strict validation of an existing task package",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verify_package is not None:
        try:
            verify_task_package_file(Path(args.verify_package))
        except TaskPackageError as exc:
            print(f"AGENT_VISION_TASK_PACKAGE_REJECTED: {exc}", file=sys.stderr)
            return EXIT_CONTRACT_REJECTED
        print(f"AGENT_VISION_TASK_PACKAGE_VERIFIED: {Path(args.verify_package).resolve()}")
        return EXIT_OK

    generation_fields = (
        args.input,
        args.ocr_manifest,
        args.geometry_manifest,
        args.host_runtime_receipt,
        args.output_dir,
        args.run_id,
    )
    if any(field is None for field in generation_fields):
        parser.error(
            "generation mode requires --input, --ocr-manifest, --geometry-manifest, "
            "--host-runtime-receipt, --output-dir and --run-id"
        )

    try:
        task_package_path, package = build_task_package(
            source_path=Path(args.input),
            ocr_path=Path(args.ocr_manifest),
            geometry_path=Path(args.geometry_manifest),
            receipt_path=Path(args.host_runtime_receipt),
            segment_dir=args.segment_dir,
            config_path=Path(args.config),
            output_dir=Path(args.output_dir),
            run_id=str(args.run_id),
            degraded_reasons=args.degraded_reason,
            require_isolated_runtime=True,
        )
    except UpstreamInconclusive as exc:
        print(f"AGENT_VISION_TASK_INCONCLUSIVE: {exc}", file=sys.stderr)
        return EXIT_INCONCLUSIVE
    except TaskPackageError as exc:
        print(f"AGENT_VISION_TASK_PACKAGE_REJECTED: {exc}", file=sys.stderr)
        return EXIT_CONTRACT_REJECTED

    print(
        json.dumps(
            {
                "status": package["status"],
                "run_id": package["run_id"],
                "query_count": package["summary"]["query_count"],
                "degradations": package["degradations"],
                "task_package": str(Path(task_package_path).resolve()),
                "sha256": sha256_file(task_package_path),
            },
            ensure_ascii=False,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
