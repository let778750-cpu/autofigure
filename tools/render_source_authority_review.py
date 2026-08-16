#!/usr/bin/env python3
"""Render a hash-bound, diagnostic-only review overlay for a DRAFT authority file."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, ImageFont


TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

try:
    from output_policy import OutputPolicyError, resolve_output_path
    from validate_source_authority import SourceAuthorityError, validate_authority
except ModuleNotFoundError:  # Support: python -m tools.render_source_authority_review
    from .output_policy import OutputPolicyError, resolve_output_path
    from .validate_source_authority import SourceAuthorityError, validate_authority


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTHORITY_SCHEMA = PROJECT_ROOT / "schemas" / "source-authority.schema.json"
DEFAULT_REVIEW_SCHEMA = PROJECT_ROOT / "schemas" / "source-authority-review.schema.json"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")
RENDERER_NAME = "source-authority-review-overlay"
RENDERER_VERSION = "1.0.0"
OVERLAY_FILENAME = "authority-overlay.png"
MANIFEST_FILENAME = "review-manifest.json"

COLOR_CONFIRMED = (22, 163, 74)
COLOR_INCONCLUSIVE = (245, 158, 11)
COLOR_MANUAL_ASSET = (192, 38, 211)
COLOR_RELATION = (37, 99, 235)


class ReviewPackageError(RuntimeError):
    """Raised before a misleading or unbound review package can be published."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewPackageError(f"cannot parse {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewPackageError(f"{label} must be a JSON object")
    return value


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewPackageError(f"cannot read review schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewPackageError("review schema must be a JSON object")
    return value


def _validate_manifest(manifest: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in first.absolute_path
        )
        raise ReviewPackageError(
            f"review manifest failed schema validation at {location}: {first.message}"
        )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def _canonical_output_directory(
    output_dir: Path,
    *,
    run_id: str,
    project_root: Path,
) -> Path:
    authorized = resolve_output_path(output_dir, project_root=project_root)
    root = project_root.resolve(strict=True)
    if _is_within(authorized, root):
        expected = (
            root / "examples" / "generated" / "runs" / run_id / "authority-review"
        ).resolve(strict=False)
        if authorized != expected:
            raise ReviewPackageError(
                "project-local review output must be exactly "
                f"examples/generated/runs/{run_id}/authority-review"
            )
    return authorized


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before scalable default-font support.
        return ImageFont.load_default()


def _item_color(item: Mapping[str, Any]) -> tuple[int, int, int]:
    if item["kind"] == "MANUAL_ASSET":
        return COLOR_MANUAL_ASSET
    if item["kind"] == "RELATION":
        return COLOR_RELATION
    if item["disposition"] == "INCONCLUSIVE":
        return COLOR_INCONCLUSIVE
    return COLOR_CONFIRMED


def _review_value(item: Mapping[str, Any]) -> str | None:
    kind = str(item["kind"])
    if kind == "TEXT":
        value = item["text"]
    elif kind == "FORMULA":
        value = item["canonical_latex"]
    elif kind in {"SEMANTIC_REGION", "MANUAL_ASSET"}:
        value = item["label"]
    else:
        relation = item["relation"]
        value = (
            f"{relation['from_subject_id']} -> {relation['to_subject_id']} "
            f"({relation['direction']}/{relation['meaning']})"
        )
    return str(value) if value is not None else None


def _scaled_bbox(bbox: Mapping[str, Any], scale: int) -> tuple[int, int, int, int]:
    x0 = int(round(float(bbox["x"]) * scale))
    y0 = int(round(float(bbox["y"]) * scale))
    x1 = int(round((float(bbox["x"]) + float(bbox["w"])) * scale))
    y1 = int(round((float(bbox["y"]) + float(bbox["h"])) * scale))
    return x0, y0, x1, y1


def _draw_overlay(
    source: Image.Image,
    items: Sequence[Mapping[str, Any]],
    *,
    scale: int,
    authority_id: str,
) -> Image.Image:
    source_width = source.width * scale
    source_height = source.height * scale
    panel_width = 820
    title_font = _font(21)
    body_font = _font(14)
    small_font = _font(12)
    row_height = 20
    panel_content_height = 142 + row_height * len(items)
    output_height = max(source_height, panel_content_height)

    canvas = Image.new("RGB", (source_width + panel_width, output_height), "white")
    resized = source.resize((source_width, source_height), Image.Resampling.LANCZOS)
    canvas.paste(resized, (0, 0))

    tint = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    tint_draw = ImageDraw.Draw(tint)
    for item in items:
        bbox = item["bbox_source"]
        if bbox is None:
            continue
        color = _item_color(item)
        box = _scaled_bbox(bbox, scale)
        tint_draw.rectangle(box, fill=(*color, 34), outline=(*color, 255), width=4)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), tint)
    draw = ImageDraw.Draw(canvas)

    for item in items:
        bbox = item["bbox_source"]
        if bbox is None:
            continue
        color = _item_color(item)
        x0, y0, _x1, _y1 = _scaled_bbox(bbox, scale)
        tag = str(item["authority_item_id"])
        text_box = draw.textbbox((0, 0), tag, font=small_font)
        tag_width = text_box[2] - text_box[0] + 8
        tag_height = text_box[3] - text_box[1] + 6
        tag_y = y0 if y0 + tag_height <= output_height else output_height - tag_height
        draw.rectangle((x0, tag_y, x0 + tag_width, tag_y + tag_height), fill=(*color, 255))
        draw.text((x0 + 4, tag_y + 2), tag, fill="white", font=small_font)

    panel_x = source_width
    draw.rectangle((panel_x, 0, canvas.width, output_height), fill=(248, 250, 252, 255))
    draw.line((panel_x, 0, panel_x, output_height), fill=(15, 23, 42, 255), width=2)
    cursor_x = panel_x + 18
    draw.text((cursor_x, 14), "SOURCE AUTHORITY REVIEW", fill=(15, 23, 42), font=title_font)
    draw.text((cursor_x, 43), authority_id, fill=(51, 65, 85), font=body_font)
    draw.text(
        (cursor_x, 68),
        "DRAFT - DIAGNOSTIC ONLY - HUMAN APPROVAL REQUIRED",
        fill=(185, 28, 28),
        font=body_font,
    )
    legend = [
        (COLOR_CONFIRMED, "confirmed by primary source"),
        (COLOR_INCONCLUSIVE, "inconclusive candidate"),
        (COLOR_MANUAL_ASSET, "manual asset slot"),
        (COLOR_RELATION, "relation (index only)"),
    ]
    legend_x = cursor_x
    for index, (color, label) in enumerate(legend):
        x = legend_x + (index % 2) * 380
        y = 98 + (index // 2) * 22
        draw.rectangle((x, y, x + 14, y + 14), fill=(*color, 255))
        draw.text((x + 20, y - 2), label, fill=(51, 65, 85), font=small_font)

    item_y = 142
    kind_code = {
        "SEMANTIC_REGION": "REG",
        "MANUAL_ASSET": "AST",
        "TEXT": "TXT",
        "FORMULA": "MTH",
        "RELATION": "REL",
    }
    disposition_code = {"CONFIRMED": "C", "INCONCLUSIVE": "?", "NOT_APPLICABLE": "N"}
    for index, item in enumerate(items):
        if index % 2:
            draw.rectangle(
                (panel_x + 8, item_y - 2, canvas.width - 8, item_y + row_height - 2),
                fill=(241, 245, 249, 255),
            )
        color = _item_color(item)
        draw.rectangle((cursor_x, item_y + 2, cursor_x + 11, item_y + 13), fill=(*color, 255))
        prefix = (
            f"{item['authority_item_id']} "
            f"{disposition_code[str(item['disposition'])]} "
            f"{kind_code[str(item['kind'])]} "
        )
        subject = str(item["subject_id"])
        if len(subject) > 63:
            subject = subject[:60] + "..."
        draw.text((cursor_x + 18, item_y), prefix + subject, fill=(15, 23, 42), font=body_font)
        item_y += row_height

    if output_height > source_height:
        draw.rectangle((0, source_height, source_width, output_height), fill=(255, 255, 255, 255))
        draw.text(
            (18, source_height + 14),
            "Blank extension: index-panel height exceeds the source image.",
            fill=(100, 116, 139),
            font=small_font,
        )
    return canvas.convert("RGB")


def _manifest_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "authority_item_id": item["authority_item_id"],
            "subject_id": item["subject_id"],
            "kind": item["kind"],
            "disposition": item["disposition"],
            "criticality": item["criticality"],
            "bbox_source": item["bbox_source"],
            "review_value": _review_value(item),
            "notes": item["notes"],
        }
        for item in items
    ]


def _create_inheriting_stage_directory(parent: Path, target_name: str) -> Path:
    """Create a fresh staging directory without tempfile's owner-only Windows ACL."""
    for _attempt in range(16):
        candidate = parent / f".{target_name}.{uuid.uuid4().hex}.tmp"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise ReviewPackageError("cannot allocate a fresh review staging directory")


def render_review_package(
    authority_path: Path,
    *,
    run_id: str,
    output_dir: Path,
    scale: int = 2,
    project_root: Path = PROJECT_ROOT,
    authority_schema_path: Path = DEFAULT_AUTHORITY_SCHEMA,
    review_schema_path: Path = DEFAULT_REVIEW_SCHEMA,
) -> dict[str, Any]:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ReviewPackageError(f"invalid run_id: {run_id!r}")
    if not 1 <= scale <= 4:
        raise ReviewPackageError("scale must be between 1 and 4")

    project_root = project_root.resolve(strict=True)
    authority_path = authority_path.resolve(strict=True)
    validation = validate_authority(
        authority_path,
        schema_path=authority_schema_path,
        project_root=project_root,
    )
    authority_bytes = authority_path.read_bytes()
    authority_hash = _sha256_bytes(authority_bytes)
    if authority_hash != validation["authority_sha256"]:
        raise ReviewPackageError("authority changed while it was being validated")
    authority = _load_json_bytes(authority_bytes, label="source authority")
    if authority["status"] != "DRAFT":
        raise ReviewPackageError("review overlay accepts DRAFT authority only")

    source_path = Path(validation["source_path"]).resolve(strict=True)
    source_bytes = source_path.read_bytes()
    source_hash = _sha256_bytes(source_bytes)
    if source_hash != validation["source_sha256"]:
        raise ReviewPackageError("source changed while it was being validated")
    try:
        with Image.open(io.BytesIO(source_bytes)) as loaded:
            loaded.load()
            source = loaded.convert("RGB")
    except Exception as exc:
        raise ReviewPackageError(f"cannot decode bound source image: {exc}") from exc

    target = _canonical_output_directory(
        output_dir,
        run_id=run_id,
        project_root=project_root,
    )
    if target.exists():
        raise ReviewPackageError(f"fresh review output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    items = authority["items"]
    overlay = _draw_overlay(
        source,
        items,
        scale=scale,
        authority_id=str(authority["authority_id"]),
    )

    stage = _create_inheriting_stage_directory(target.parent, target.name)
    try:
        overlay_path = stage / OVERLAY_FILENAME
        overlay.save(overlay_path, format="PNG", optimize=False, compress_level=6)
        overlay_bytes = overlay_path.read_bytes()
        review_schema = _load_schema(review_schema_path)
        review_schema_hash = _sha256_file(review_schema_path.resolve(strict=True))
        script_hash = _sha256_file(Path(__file__).resolve(strict=True))
        manifest: dict[str, Any] = {
            "schema_version": "1.0.0",
            "document_type": "SOURCE_AUTHORITY_REVIEW_PACKAGE",
            "status": "READY_FOR_HUMAN_REVIEW",
            "run_id": run_id,
            "policy": {
                "authority_unchanged": True,
                "overlay_is_diagnostic_only": True,
                "approval_required_for_freeze": True,
            },
            "authority": {
                "path": _display_path(authority_path, project_root),
                "sha256": authority_hash,
                "authority_id": authority["authority_id"],
                "status": authority["status"],
                "item_count": len(items),
            },
            "source": {
                "path": _display_path(source_path, project_root),
                "sha256": source_hash,
                "width_px": source.width,
                "height_px": source.height,
                "pixel_format": "RGB",
            },
            "renderer": {
                "name": RENDERER_NAME,
                "version": RENDERER_VERSION,
                "script_sha256": script_hash,
                "schema_sha256": review_schema_hash,
                "scale": scale,
            },
            "counts": {
                "total": len(items),
                "confirmed": sum(item["disposition"] == "CONFIRMED" for item in items),
                "inconclusive": sum(
                    item["disposition"] == "INCONCLUSIVE" for item in items
                ),
                "manual_assets": sum(item["kind"] == "MANUAL_ASSET" for item in items),
                "relations": sum(item["kind"] == "RELATION" for item in items),
            },
            "items": _manifest_items(items),
            "outputs": {
                "overlay": {
                    "file": OVERLAY_FILENAME,
                    "sha256": _sha256_bytes(overlay_bytes),
                    "width_px": overlay.width,
                    "height_px": overlay.height,
                    "pixel_format": "RGB",
                }
            },
            "review_decision": None,
        }
        _validate_manifest(manifest, review_schema)
        manifest_path = stage / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        if _sha256_file(authority_path) != authority_hash:
            raise ReviewPackageError("authority changed during overlay rendering")
        if _sha256_file(source_path) != source_hash:
            raise ReviewPackageError("source changed during overlay rendering")
        if target.exists():
            raise ReviewPackageError(f"fresh review output appeared concurrently: {target}")
        stage.rename(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    final_manifest_path = target / MANIFEST_FILENAME
    final_overlay_path = target / OVERLAY_FILENAME
    return {
        "document_type": "SOURCE_AUTHORITY_REVIEW_RENDER_RESULT",
        "schema_version": "1.0.0",
        "status": "READY_FOR_HUMAN_REVIEW",
        "run_id": run_id,
        "manifest_path": str(final_manifest_path),
        "manifest_sha256": _sha256_file(final_manifest_path),
        "overlay_path": str(final_overlay_path),
        "overlay_sha256": _sha256_file(final_overlay_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--authority-schema", type=Path, default=DEFAULT_AUTHORITY_SCHEMA)
    parser.add_argument("--review-schema", type=Path, default=DEFAULT_REVIEW_SCHEMA)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = render_review_package(
            args.authority,
            run_id=str(args.run_id),
            output_dir=args.output_dir,
            scale=int(args.scale),
            project_root=args.project_root,
            authority_schema_path=args.authority_schema,
            review_schema_path=args.review_schema,
        )
    except (ReviewPackageError, SourceAuthorityError, OutputPolicyError, OSError) as exc:
        print(f"SOURCE_AUTHORITY_REVIEW_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
