"""Canonical scene revision and derived-artifact lineage helpers.

The route adapters may ingest different source forms, but formal artifacts are
always bound to one canonical ``scene.json`` revision.  SVG text is retained as
the deterministic offline carrier inside the scene until every element has a
fully native scene renderer.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from tools.core import common


LINEAGE_VERSION = "1.0.0"
DERIVED_SCENE_KEYS = {"artifact", "revision", "updated_at"}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_scene_payload(scene: dict[str, Any]) -> dict[str, Any]:
    """Return the route-neutral scene payload used to identify a revision."""

    payload = copy.deepcopy(scene)
    for key in DERIVED_SCENE_KEYS:
        payload.pop(key, None)
    canonical_svg = payload.get("canonical_svg")
    if isinstance(canonical_svg, dict):
        canonical_svg.pop("materialized_path", None)
    return payload


def scene_sha256(scene: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(canonical_scene_payload(scene))).hexdigest()


def revision_id(scene: dict[str, Any]) -> str:
    return f"scene-{scene_sha256(scene)[:16]}"


def compiler_fingerprint() -> str:
    """Hash deterministic compiler modules that materially affect artifacts.

    模块字节先规范化为 LF 再哈希：开发机 ``core.autocrlf`` 可能把工作树检出
    成 CRLF，而 fresh checkout/CI 是 LF——指纹必须 EOL 无关，否则 lineage
    manifest 绑定的工作树指纹跨机器必然失配（qa-compiler-fingerprint-mismatch）。
    """

    digest = hashlib.sha256()
    for name, subpackage in (
        ("convert.py", "pipeline"),
        ("svggeom.py", "core"),
        ("arrow_spec.py", "arrows"),
        ("pptx_arrows.py", "arrows"),
        ("primitives.py", "assets"),
        ("asset_spec.py", "assets"),
    ):
        path = Path(__file__).parent.parent / subpackage / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def bind_canonical_svg(
    scene: dict[str, Any],
    svg_text: str,
    *,
    source_role: str,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Bind SVG carrier bytes into scene without treating the root SVG as truth."""

    svg_sha = hashlib.sha256(svg_text.encode("utf-8")).hexdigest()
    if source_sha256 is not None and source_sha256 != svg_sha:
        raise ValueError(f"SVG source hash mismatch: expected {source_sha256}, got {svg_sha}")
    scene["canonical_svg"] = {
        "encoding": "utf-8",
        "content": svg_text,
        "sha256": svg_sha,
        "source_role": source_role,
        "materialized_path": "redraw.svg",
    }
    return scene


def read_svg_text_exact(path: Path) -> str:
    """Decode SVG bytes without Windows universal-newline rewriting."""

    return path.read_bytes().decode("utf-8")


def canonical_svg_text(scene: dict[str, Any]) -> str:
    carrier = scene.get("canonical_svg")
    if not isinstance(carrier, dict) or not isinstance(carrier.get("content"), str):
        raise ValueError("scene has no canonical_svg content")
    text = carrier["content"]
    actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if carrier.get("sha256") != actual:
        raise ValueError("scene canonical_svg hash mismatch")
    return text


def materialize_svg(run: common.Run, scene: dict[str, Any]) -> str:
    """Project canonical scene SVG bytes into the flat case root."""

    text = canonical_svg_text(scene)
    encoded = text.encode("utf-8")
    if not run.redraw_svg.is_file() or run.redraw_svg.read_bytes() != encoded:
        temporary = run.redraw_svg.with_suffix(".svg.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(run.redraw_svg)
    return text


def artifact_hashes(run: common.Run) -> dict[str, str]:
    paths = {
        "redraw_svg": run.redraw_svg,
        "redraw_pptx": run.pptx_path,
        "render_png": run.render_png,
        "bindings": run.bindings_path,
    }
    return {
        key: common.sha256_file(path)
        for key, path in paths.items()
        if path.is_file()
    }


def build_revision_record(run: common.Run, scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "lineage_version": LINEAGE_VERSION,
        "revision_id": revision_id(scene),
        "scene_sha256": scene_sha256(scene),
        "compiler_fingerprint": compiler_fingerprint(),
        "artifacts": artifact_hashes(run),
    }


def stamp_active_revision(run: common.Run) -> dict[str, Any]:
    """Bind the flat root projections to the current canonical scene revision."""

    from tools.core.contracts import read_json, utc_now, write_json

    scene = read_json(run.scene_path)
    base = {
        "lineage_version": LINEAGE_VERSION,
        "revision_id": revision_id(scene),
        "scene_sha256": scene_sha256(scene),
        "compiler_fingerprint": compiler_fingerprint(),
    }
    scene["revision"] = {**base, "stamped_at": utc_now()}
    write_json(run.scene_path, scene)

    if run.bindings_path.is_file():
        bindings = read_json(run.bindings_path)
        bindings["scene_revision"] = dict(base)
        write_json(run.bindings_path, bindings)

    scene = read_json(run.scene_path)
    record = build_revision_record(run, scene)
    record["stamped_at"] = utc_now()
    meta = run.load_meta()
    meta["active_revision"] = record
    write_json(run.meta_path, meta)
    write_json(
        run.revision_receipt_path,
        {
            "schema_version": "4.0.0",
            "kind": "revision_receipt",
            "case": meta["case"],
            "reference_sha256": meta["source_sha256"],
            **record,
        },
    )
    return record


def lineage_blockers(run: common.Run) -> list[str]:
    """Verify root projections and reports are bound to the active scene."""

    from tools.core.contracts import read_json

    blockers: list[str] = []
    scene = read_json(run.scene_path)
    expected = build_revision_record(run, scene)
    active = run.load_meta().get("active_revision")
    if not isinstance(active, dict):
        return ["lineage:active-revision-missing"]
    for key in ("revision_id", "scene_sha256", "compiler_fingerprint"):
        if active.get(key) != expected[key]:
            blockers.append(f"lineage:{key.replace('_', '-')}-mismatch")
    for key, digest in expected["artifacts"].items():
        if active.get("artifacts", {}).get(key) != digest:
            blockers.append(f"lineage:artifact-{key.replace('_', '-')}-mismatch")
    carrier = scene.get("canonical_svg")
    if isinstance(carrier, dict) and run.redraw_svg.is_file():
        if carrier.get("sha256") != common.sha256_file(run.redraw_svg):
            blockers.append("lineage:redraw-svg-not-derived-from-scene")
    return list(dict.fromkeys(blockers))
