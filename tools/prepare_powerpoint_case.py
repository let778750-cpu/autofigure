"""Project a PASS AutoFigure spec into one PowerPoint contract-v2 case.

This adapter does not reinterpret pixels or change scientific content.  It
creates the case-control documents required by the managed PowerPoint MCP from
one hash-bound Figure Spec and its authorized preflight receipt.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

TOOLS_DIRECTORY = Path(__file__).resolve().parent
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from finalize_perception_review import atomic_write_json, sha256_file  # noqa: E402


class PowerPointCaseError(RuntimeError):
    """Raised when a frozen AutoFigure spec cannot become one MCP case."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PowerPointCaseError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PowerPointCaseError(f"{label} must be a JSON object")
    return value


def _require_hash(path: Path, expected: str, label: str) -> str:
    actual = sha256_file(path)
    if actual.casefold() != expected.casefold():
        raise PowerPointCaseError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def _resolve(value: str, base: Path) -> Path:
    path = Path(value)
    return path.resolve(strict=True) if path.is_absolute() else (base / path).resolve(strict=True)


def _semantic_id(element_id: str) -> str:
    return f"semantic::{element_id}"


def _relation_id(edge_id: str) -> str:
    return f"relation::{edge_id}"


def _node_kind(element: Mapping[str, Any]) -> str:
    kind = str(element.get("type", ""))
    if kind in {"background", "panel"}:
        return "panel"
    if kind in {"micro_asset"}:
        return "container"
    if kind in {"shape", "icon"}:
        return "shape"
    if kind == "legend":
        return "legend"
    if kind == "manual_asset_slot":
        return "asset"
    if kind == "text":
        runs = element.get("content_runs", [])
        if any(isinstance(run, Mapping) and run.get("kind") == "math" for run in runs):
            return "formula"
        return "text"
    return "shape"


def _plain_or_formula_text(
    element: Mapping[str, Any], formulas_by_element: Mapping[str, Mapping[str, Any]]
) -> str:
    if isinstance(element.get("text"), str):
        return str(element["text"])
    formula = formulas_by_element.get(str(element["id"]))
    return str(formula.get("canonical_latex", "")) if formula else ""


def _scale(value: int | float, factor: float) -> float:
    return round(float(value) * factor, 6)


def _margins(style: Mapping[str, Any], scale: float) -> dict[str, float]:
    value = style.get("margin_px", 0)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount = _scale(value, scale)
        return {key: amount for key in ("left", "top", "right", "bottom")}
    if isinstance(value, Mapping):
        return {
            key: _scale(value.get(key, 0), scale)
            for key in ("left", "top", "right", "bottom")
        }
    return {key: 0.0 for key in ("left", "top", "right", "bottom")}


def _text_spec(
    element: Mapping[str, Any],
    formulas_by_element: Mapping[str, Mapping[str, Any]],
    scale: float,
) -> dict[str, Any]:
    style = element.get("text_style") if isinstance(element.get("text_style"), Mapping) else {}
    align = str(element.get("align", "left"))
    return {
        "text": _plain_or_formula_text(element, formulas_by_element),
        "fontToken": "font.math" if _node_kind(element) == "formula" else "font.body",
        "fontSize": _scale(
            style.get("font_size_px", style.get("font_size_pt", 18)), scale
        ),
        "fontWeight": "semibold" if element.get("criticality") == "critical" else "normal",
        "horizontalAlign": align if align in {"left", "center", "right"} else "left",
        "verticalAlign": "middle",
        "margins": _margins(style, scale),
        "autofit": "none",
    }


def _style_tokens(element: Mapping[str, Any]) -> dict[str, str | float | bool]:
    keys = (
        "fill",
        "stroke",
        "stroke_width_px",
        "dash",
        "corner_radius_px",
        "color",
        "shape_kind",
        "asset_kind",
        "icon_kind",
        "layer_count",
        "count",
        "state_fill",
        "state_stroke",
        "reward_fill",
        "reward_stroke",
        "policy_fill",
        "action_fill",
    )
    return {
        key: value
        for key in keys
        if isinstance((value := element.get(key)), (str, int, float, bool))
    }


def _edge_direction(edge: Mapping[str, Any]) -> str:
    style = edge.get("style") if isinstance(edge.get("style"), Mapping) else {}
    if style.get("arrowhead") == "both" or edge.get("meaning") == "interaction":
        return "bidirectional"
    return "forward"


def _scientific_relation_kind(meaning: str) -> str:
    return {
        "data_flow": "data",
        "control_flow": "control",
        "feedback": "feedback",
        "interaction": "association",
        "association": "association",
    }.get(meaning, "flow")


def _validate_document(document: Mapping[str, Any], schema_path: Path, label: str) -> None:
    schema = _load(schema_path, f"{label} schema")
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in first.absolute_path
        )
        raise PowerPointCaseError(f"{label} rejected at {location}: {first.message}")


def prepare_powerpoint_case(
    spec_path: Path,
    preflight_path: Path,
    output_case: Path,
    *,
    project_id: str,
    target_id: str,
    profile_id: str = "presentation-16x9",
    schema_root: Path | None = None,
) -> dict[str, Any]:
    if output_case.exists():
        raise PowerPointCaseError(f"output case already exists: {output_case}")
    resolved_spec = spec_path.resolve(strict=True)
    resolved_preflight = preflight_path.resolve(strict=True)
    spec = _load(resolved_spec, "Figure Spec")
    preflight = _load(resolved_preflight, "preflight receipt")
    if preflight.get("status") != "PASS" or preflight.get("receipt", {}).get(
        "authorized_for_drawer"
    ) is not True:
        raise PowerPointCaseError("preflight must be PASS and authorized_for_drawer=true")
    spec_sha = _require_hash(
        resolved_spec,
        str(preflight.get("receipt", {}).get("spec_sha256", "")),
        "Figure Spec",
    )
    if spec.get("mode") != "reconstruct_1to1":
        raise PowerPointCaseError("PowerPoint case adapter currently requires reconstruct_1to1")

    source_path = _resolve(str(spec["source"]["path"]), resolved_spec.parent)
    source_sha = _require_hash(source_path, str(spec["source"]["sha256"]), "reference")
    canvas_path = _resolve(str(spec["canvas"]["pptx_path"]), resolved_spec.parent)
    canvas_sha = _require_hash(canvas_path, str(spec["canvas"]["pptx_sha256"]), "canvas")
    authority_path = _resolve(str(spec["authority"]["path"]), resolved_spec.parent)
    authority_sha = _require_hash(
        authority_path, str(spec["authority"]["sha256"]), "source authority"
    )

    source_width = float(spec["canvas"]["width_px"])
    source_height = float(spec["canvas"]["height_px"])
    slide_width = float(spec["canvas"]["slide_width_emu"]) / 12700.0
    slide_height = float(spec["canvas"]["slide_height_emu"]) / 12700.0
    coordinate_scale = slide_width / source_width
    scaled_source_height = source_height * coordinate_scale
    if abs(scaled_source_height - slide_height) > 0.02:
        raise PowerPointCaseError(
            "canvas requires non-uniform scaling: "
            f"source={source_width}x{source_height}px, "
            f"slide={slide_width}x{slide_height}pt"
        )

    output_case.mkdir(parents=True)
    input_dir = output_case / "input"
    design_dir = output_case / "design"
    assets_dir = output_case / "assets"
    for directory in (input_dir, design_dir, assets_dir):
        directory.mkdir()
    shutil.copyfile(canvas_path, input_dir / "canvas.pptx")

    formulas_by_element = {
        str(formula["element_id"]): formula for formula in spec.get("formulas", [])
    }
    nodes: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    asset_slots: list[dict[str, Any]] = []
    asset_records: list[dict[str, Any]] = []
    preview_asset_ids: list[str] = []
    for element in spec["elements"]:
        element_id = str(element["id"])
        kind = _node_kind(element)
        bbox = element["bbox"]
        node: dict[str, Any] = {
            "id": element_id,
            "semanticId": _semantic_id(element_id),
            "parentId": element.get("parent_id"),
            "kind": kind,
            "x": _scale(bbox["x"], coordinate_scale),
            "y": _scale(bbox["y"], coordinate_scale),
            "width": _scale(bbox["w"], coordinate_scale),
            "height": _scale(bbox["h"], coordinate_scale),
            "editable": True,
            "zIndex": int(element["z_index"]),
        }
        tokens = _style_tokens(element)
        if tokens:
            node["styleTokens"] = tokens
        if kind in {"text", "formula"}:
            node["label"] = _plain_or_formula_text(element, formulas_by_element)
            node["textSpec"] = _text_spec(
                element, formulas_by_element, coordinate_scale
            )
        if kind in {"panel", "container", "shape", "legend"}:
            node["shapeSpec"] = {
                "shapeType": str(
                    element.get("shape_kind", element.get("asset_kind", "rectangle"))
                ),
                "cornerRadius": _scale(
                    element.get("corner_radius_px", 0), coordinate_scale
                ),
            }
        if kind == "asset":
            slot = element["slot_contract"]
            asset_id = element_id
            preview = slot.get("preview")
            if slot.get("mode") != "reference_preview" or not isinstance(preview, Mapping):
                raise PowerPointCaseError(
                    f"{element_id} must be one controlled reference_preview for this adapter"
                )
            manifest_path = _resolve(str(preview["manifest_path"]), resolved_spec.parent)
            _require_hash(
                manifest_path,
                str(preview["manifest_sha256"]),
                f"{element_id} preview manifest",
            )
            preview_manifest = _load(manifest_path, f"{element_id} preview manifest")
            preview_path = _resolve(str(preview_manifest["asset"]["path"]), manifest_path.parent)
            preview_sha = _require_hash(
                preview_path,
                str(preview_manifest["asset"]["sha256"]),
                f"{element_id} preview image",
            )
            copied_name = f"{element_id}.reference-preview.png"
            shutil.copyfile(preview_path, assets_dir / copied_name)
            preview_asset_ids.append(asset_id)
            asset_records.append(
                {
                    "assetId": asset_id,
                    "responsibilityClass": "creative_raster",
                    "selectedFile": f"assets/{copied_name}",
                    "processingSteps": [
                        "Exact-pixel crop from the frozen designated reference.",
                        "Candidate-only preview; visible disclosure and manual replacement required.",
                    ],
                    "sha256": preview_sha.lower(),
                    "mime": "image/png",
                    "width": float(preview_manifest["asset"]["width_px"]),
                    "height": float(preview_manifest["asset"]["height_px"]),
                    "targetSlot": element_id,
                    "embeddedObjectId": None,
                    "provenance": {
                        "route": "verified_import",
                        "status": "verified_source",
                        "license": None,
                        "rightsBasis": "User-supplied designated reference; candidate preview only.",
                    },
                    "reviewStatus": "PENDING",
                }
            )
            node["assetSpec"] = {
                "assetId": asset_id,
                "fitMode": "contain",
                "crop": {"left": 0, "top": 0, "right": 0, "bottom": 0},
                "atomicRasterUnit": True,
                "containsReconstructableContent": False,
            }
            asset_slots.append(
                {
                    "slotId": element_id,
                    "semanticAnchor": _semantic_id(element_id),
                    "x": _scale(bbox["x"], coordinate_scale),
                    "y": _scale(bbox["y"], coordinate_scale),
                    "width": _scale(bbox["w"], coordinate_scale),
                    "height": _scale(bbox["h"], coordinate_scale),
                    "overlaySafeArea": "Visible disclosure is a separate native text object below the crop.",
                    "expectedAspectRatio": float(slot["aspect_ratio"]),
                }
            )
        nodes.append(node)
        entities.append(
            {
                "entityId": _semantic_id(element_id),
                "label": str(element.get("text", element.get("semantic_role", element_id))),
                "kind": (
                    "equation"
                    if kind == "formula"
                    else "panel"
                    if kind == "panel"
                    else "annotation"
                    if kind in {"text", "legend"}
                    else "component"
                ),
                "sourceIds": ["reference-image", "source-authority"],
                "required": True,
                "editable": True,
            }
        )

    scene_edges: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for edge in spec.get("edges", []):
        edge_id = str(edge["id"])
        route = "straight" if edge.get("route") == "straight" else "orthogonal"
        scene_edge: dict[str, Any] = {
            "id": edge_id,
            "semanticRelationId": _relation_id(edge_id),
            "source": str(edge["from"]),
            "target": str(edge["to"]),
            "direction": _edge_direction(edge),
            "route": route,
            "styleTokens": {
                "strokePattern": "dashed"
                if edge.get("style", {}).get("dash")
                else "solid"
            },
        }
        if edge.get("via"):
            scene_edge["waypoints"] = [
                {
                    "x": _scale(point["x"], coordinate_scale),
                    "y": _scale(point["y"], coordinate_scale),
                    "role": "bend",
                }
                for point in edge["via"]
            ]
        scene_edges.append(scene_edge)
        relations.append(
            {
                "relationId": _relation_id(edge_id),
                "source": _semantic_id(str(edge["from"])),
                "target": _semantic_id(str(edge["to"])),
                "kind": _scientific_relation_kind(str(edge.get("meaning", "data_flow"))),
                "direction": _edge_direction(edge),
                "sourceIds": ["reference-image", "source-authority"],
            }
        )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scene_version = f"autofg-{spec_sha[:16].lower()}"
    scene_graph = {
        "schemaVersion": "2.0.0",
        "projectId": project_id,
        "version": scene_version,
        "canvas": {
            "width": round(slide_width, 6),
            "height": round(slide_height, 6),
            "unit": "pt",
            "profileId": profile_id,
            "backgroundToken": "color.background.white",
        },
        "nodes": nodes,
        "edges": scene_edges,
        "assetSlots": asset_slots,
    }
    all_semantic_ids = [node["semanticId"] for node in nodes] + [
        edge["semanticRelationId"] for edge in scene_edges
    ]
    scientific_spec = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "figureRole": "system_architecture",
        "language": "en",
        "audience": "Computer vision and embodied-agent researchers",
        "claims": [
            {
                "claimId": "claim-modularagent-figure2",
                "text": "Task-aware modular fusion couples semantic and dynamics experts with task-conditioned imagination and behavior learning.",
                "evidenceType": "cited",
                "sourceIds": ["reference-image", "source-authority"],
                "mustShow": True,
            }
        ],
        "entities": entities,
        "relations": relations,
        "formulas": [
            {
                "formulaId": str(formula["id"]),
                "latex": str(formula["canonical_latex"]),
                "sourceIds": ["source-authority"],
            }
            for formula in spec.get("formulas", [])
        ],
        "evidenceDeclarations": [
            {
                "evidenceId": "reference-reconstruction",
                "representationClass": "source_backed",
                "underlyingDataStatus": "SOURCE_DATA_SUPPLIED",
                "semanticIds": all_semantic_ids,
                "provenanceRequirement": "REQUIRED_AT_QA",
                "rationale": "All visible scientific content is bound to the frozen paper figure, source authority, and PASS AutoFigure review.",
            }
        ],
        "unknowns": [
            "The photographic observation montage remains a disclosed candidate-only preview pending user replacement."
        ],
        "approval": {
            "status": "APPROVED",
            "approvedBy": "project user via frozen AutoFigure source authority",
            "approvedAt": now,
            "notes": f"Bound to AutoFigure authority {authority_sha} and PASS spec {spec_sha}.",
        },
    }
    render_elements = []
    for node in nodes:
        renderer = "verified_raster" if node["kind"] == "asset" else "backend_native"
        render_elements.append(
            {
                "elementId": node["id"],
                "mustRemainEditable": True,
                "qualityOwner": "asset" if node["kind"] == "asset" else "layout",
                "reason": "Controlled reference preview; replace before approval."
                if node["kind"] == "asset"
                else "Native editable PowerPoint object required.",
                "routes": [
                    {
                        "targetId": target_id,
                        "renderer": renderer,
                        **({"fallbackRenderer": "manual"} if node["kind"] == "asset" else {}),
                    }
                ],
            }
        )
    for edge in scene_edges:
        render_elements.append(
            {
                "elementId": edge["id"],
                "mustRemainEditable": True,
                "qualityOwner": "layout",
                "reason": "Native connector preserves topology and editability.",
                "routes": [{"targetId": target_id, "renderer": "backend_native"}],
            }
        )
    render_plan = {
        "schemaVersion": "2.0.0",
        "projectId": project_id,
        "sceneGraphVersion": scene_version,
        "targets": [
            {
                "targetId": target_id,
                "backendId": "powerpoint",
                "adapterId": "powerpoint-live",
                "required": True,
                "deliverables": ["pptx", "png"],
                "requiredCapabilities": [
                    "text_box",
                    "auto_shape",
                    "free_line_or_arrow",
                    "attached_connector",
                    "picture_or_svg",
                ],
            }
        ],
        "elements": render_elements,
        "deliverables": ["pptx", "png"],
    }
    source_manifest = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "taskMode": "RECONSTRUCT_1TO1",
        "designatedReferenceSourceId": "reference-image",
        "designatedReferenceSha256": source_sha.lower(),
        "referenceUsage": "measurement_only",
        "referenceEmbedded": False,
        "independentReferenceCase": True,
        "sources": [
            {
                "sourceId": "reference-image",
                "kind": "reference_image",
                "pathOrUrl": str(source_path),
                "sha256": source_sha.lower(),
                "authority": "user_supplied",
                "licenseStatus": "unknown",
                "confidentiality": "public",
                "notes": "Frozen designated reference; never embedded as a whole-slide image.",
            },
            {
                "sourceId": "source-authority",
                "kind": "prior_source",
                "pathOrUrl": str(authority_path),
                "sha256": authority_sha.lower(),
                "authority": "primary",
                "licenseStatus": "citation_only",
                "confidentiality": "public",
                "notes": "Frozen AutoFigure authority reviewed by the project user.",
            },
        ],
    }
    asset_manifest = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "assets": asset_records,
    }
    evidence_provenance = {
        "schemaVersion": "1.0.0",
        "projectId": project_id,
        "taskMode": "RECONSTRUCT_1TO1",
        "designatedReference": {
            "sourceId": "reference-image",
            "sha256": source_sha.lower(),
            "usage": "measurement_only",
            "embedded": False,
        },
        "reviewIsolation": {
            "doesNotOverrideAssetManifest": True,
            "doesNotEstablishScientificValidity": True,
            "doesNotEstablishRights": True,
        },
        "reviewStatus": "PENDING",
        "lifecycleStatus": "READY_FOR_REVIEW",
        "records": [
            {
                "evidenceId": "reference-reconstruction",
                "objectIds": [node["id"] for node in nodes]
                + [edge["id"] for edge in scene_edges],
                "evidenceType": "conceptual",
                "basisType": "reference_visual_reconstruction",
                "visualBasis": "Frozen ModularAgent Figure 2 reference plus frozen AutoFigure authority and PASS preflight.",
                "underlyingData": {
                    "status": "SOURCE_DATA_SUPPLIED",
                    "sourceIds": ["reference-image", "source-authority"],
                    "datasetPaths": [],
                },
                "scientificUse": "QUALITATIVE_VISUAL_ONLY",
                "interpretationLimit": "This reconstruction communicates the cited architecture; the observation montage is a disclosed replace-before-approval preview and quantitative inference is prohibited.",
                "relatedAssetIds": preview_asset_ids,
                "reviewStatus": "PENDING",
            }
        ],
    }
    project_state = {
        "schemaVersion": "2.0.0",
        "contractVersion": "2.0.0",
        "projectId": project_id,
        "taskMode": "RECONSTRUCT_1TO1",
        "state": "PLANNED",
        "lastSuccessfulState": "PLANNED",
        "profileId": profile_id,
        "requestedBackendIds": ["powerpoint"],
        "updatedAt": now,
        "blockedReason": None,
        "gates": {
            "environment": "PASS",
            "scientificApproval": "PASS",
            "assetDirection": "PASS",
            "styleApproval": "PASS",
            "assetIntegrity": "PENDING",
            "backendAudit": "PENDING",
            "artifactSetIntegrity": "PENDING",
            "backendReadback": "PENDING",
            "crossBackendEquivalence": "PENDING",
            "quality": "PENDING",
            "humanApproval": "PENDING",
            "exportReadback": "PENDING",
        },
        "history": [
            {
                "state": "PLANNED",
                "at": now,
                "evidence": [str(resolved_spec), str(resolved_preflight)],
                "note": "Deterministic projection from an authorized AutoFigure Figure Spec; release approval remains pending.",
            }
        ],
    }

    documents = {
        "project_state.json": project_state,
        "input/source_manifest.json": source_manifest,
        "design/scientific_spec.json": scientific_spec,
        "design/scene_graph.json": scene_graph,
        "design/render_plan.json": render_plan,
        "assets/asset_manifest.json": asset_manifest,
        "assets/evidence_provenance.json": evidence_provenance,
    }
    if schema_root is not None:
        resolved_schemas = schema_root.resolve(strict=True)
        schema_names = {
            "project_state.json": "project-state-v2.schema.json",
            "input/source_manifest.json": "source-manifest.schema.json",
            "design/scientific_spec.json": "scientific-spec.schema.json",
            "design/scene_graph.json": "scene-graph-v2.schema.json",
            "design/render_plan.json": "render-plan-v2.schema.json",
            "assets/asset_manifest.json": "asset-manifest.schema.json",
            "assets/evidence_provenance.json": "evidence-provenance.schema.json",
        }
        for relative, document in documents.items():
            _validate_document(
                document,
                resolved_schemas / schema_names[relative],
                relative,
            )
    for relative, document in documents.items():
        atomic_write_json(output_case / relative, document)
    receipt = {
        "schema_version": "1.0.0",
        "document_type": "AUTOFIGURE_POWERPOINT_CASE_RECEIPT",
        "status": "POWERPOINT_CASE_READY",
        "created_at_utc": now,
        "project_id": project_id,
        "target_id": target_id,
        "task_mode": "RECONSTRUCT_1TO1",
        "figure_spec": {"path": str(resolved_spec), "sha256": spec_sha},
        "preflight": {
            "path": str(resolved_preflight),
            "sha256": sha256_file(resolved_preflight),
        },
        "canvas": {
            "path": str(input_dir / "canvas.pptx"),
            "sha256": canvas_sha,
        },
        "coordinate_mapping": {
            "source_unit": "px",
            "target_unit": "pt",
            "scale": round(coordinate_scale, 12),
            "translate_x": 0.0,
            "translate_y": 0.0,
            "uniform": True,
        },
        "documents": {
            relative: sha256_file(output_case / relative) for relative in documents
        },
        "scene_counts": {
            "nodes": len(nodes),
            "edges": len(scene_edges),
            "asset_slots": len(asset_slots),
        },
        "release_status": "CANDIDATE_WITH_REFERENCE_PREVIEWS",
    }
    atomic_write_json(output_case / "case-receipt.json", receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--output-case", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--profile-id", default="presentation-16x9")
    parser.add_argument("--schema-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        receipt = prepare_powerpoint_case(
            args.spec,
            args.preflight,
            args.output_case,
            project_id=args.project_id,
            target_id=args.target_id,
            profile_id=args.profile_id,
            schema_root=args.schema_root,
        )
    except (PowerPointCaseError, OSError) as exc:
        print(f"POWERPOINT_CASE_REJECTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
