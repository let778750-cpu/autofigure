"""Fail-closed closure checks for reference-derived region object inventories.

Pixel similarity can miss a small, semantically required connector inside a
large white region.  This module does not attempt to infer relations from the
pixels a second time.  Instead it verifies that every object and relation that
was frozen in ``regions.json`` survives through scene construction and the
PowerPoint binding manifest.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from tools.core import common
from tools.arrows.arrow_spec import (
    HEAD_TYPES,
    arrow_direction,
    spec_sha256,
    validate_arrow_spec,
)
from tools.core.contracts import SCHEMA_VERSION, read_json

_DIRECTIONS = {"forward", "backward", "bidirectional", "undirected"}


def _backend_object_count(binding: dict[str, Any]) -> int | None:
    """Return the declared visible-object count when the binding exposes it."""

    explicit = binding.get("visible_object_count")
    if isinstance(explicit, int) and not isinstance(explicit, bool) and explicit >= 0:
        return explicit
    backend_ids = binding.get("backend_object_ids")
    if isinstance(backend_ids, list):
        return len(backend_ids)
    # Autofigure's current offline/PowerPoint-live binding model stores one
    # exact shape identity as a paired numeric id and name.
    if binding.get("shape_id") is not None and isinstance(binding.get("shape_name"), str):
        return 1
    return None


def audit_region_contract(
    run: common.Run,
    regions_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit regions -> scene -> binding closure without guessing visual truth.

    ``required_relations`` is optional for legacy compatibility.  When present,
    every item is a frozen reference-derived record with this schema::

        {
          "id": "stable-edge-id",
          "source_id": "stable-source-id",
          "target_id": "stable-target-id",
          "direction": "forward",
          "start_head_type": "none",
          "end_head_type": "triangle",
          "representation": "line_arrow",
          "visible_object_count": 1
        }

    A declared record is fail-closed: it must be present in ``element_ids``, in
    both scene carriers, in exactly one binding, and its ArrowSpec must preserve
    the declared endpoints, direction, and end-head type.
    """

    regions_payload = regions_payload or read_json(run.regions_path)
    scene = read_json(run.scene_path)
    bindings = read_json(run.bindings_path)
    meta = run.load_meta()

    scene_elements: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scene.get("elements", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            scene_elements[item["id"]].append(item)

    scene_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scene.get("edges", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            scene_edges[item["id"]].append(item)

    physical_binding_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in bindings.get("bindings", []):
        if isinstance(item, dict) and isinstance(item.get("element_id"), str):
            physical_binding_rows[item["element_id"]].append(item)

    logical_group_binding_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in bindings.get("logical_group_bindings", []):
        if isinstance(item, dict) and isinstance(item.get("element_id"), str):
            logical_group_binding_rows[item["element_id"]].append(item)

    binding_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for element_id, rows in physical_binding_rows.items():
        binding_rows[element_id].extend(rows)
    for element_id, rows in logical_group_binding_rows.items():
        binding_rows[element_id].extend(rows)

    arrow_readback_present = run.powerpoint_arrow_readback_path.is_file()
    arrow_readback_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if arrow_readback_present:
        arrow_readback = read_json(run.powerpoint_arrow_readback_path)
        for item in arrow_readback.get("records", []):
            if isinstance(item, dict) and isinstance(item.get("element_id"), str):
                arrow_readback_rows[item["element_id"]].append(item)

    blockers: list[str] = []
    region_reports: list[dict[str, Any]] = []

    for index, region in enumerate(regions_payload.get("regions", [])):
        region_id = region.get("id") if isinstance(region, dict) else None
        region_id = region_id if isinstance(region_id, str) and region_id else f"index-{index}"
        findings: list[dict[str, Any]] = []

        def finding(code: str, *, element_id: str | None = None, detail: Any = None) -> None:
            suffix = f":{element_id}" if element_id else ""
            blocker = f"region-contract:{region_id}:{code}{suffix}"
            blockers.append(blocker)
            findings.append(
                {
                    "code": code,
                    **({"element_id": element_id} if element_id else {}),
                    **({"detail": detail} if detail is not None else {}),
                    "blocker": blocker,
                }
            )

        element_ids_value = region.get("element_ids") if isinstance(region, dict) else None
        element_ids: list[str] = []
        if element_ids_value is not None:
            if not isinstance(element_ids_value, list) or not element_ids_value:
                finding("element-ids-invalid")
            elif any(not isinstance(value, str) or not value for value in element_ids_value):
                finding("element-ids-invalid")
            else:
                element_ids = element_ids_value
                for element_id, count in Counter(element_ids).items():
                    if count != 1:
                        finding("element-id-duplicate", element_id=element_id, detail={"count": count})

                for element_id in dict.fromkeys(element_ids):
                    element_count = len(scene_elements.get(element_id, []))
                    if element_count == 0:
                        finding("scene-element-missing", element_id=element_id)
                    elif element_count != 1:
                        finding(
                            "scene-element-duplicate",
                            element_id=element_id,
                            detail={"count": element_count},
                        )

                    rows = binding_rows.get(element_id, [])
                    if not rows:
                        finding("binding-missing", element_id=element_id)
                    elif len(rows) != 1:
                        finding(
                            "binding-duplicate",
                            element_id=element_id,
                            detail={"count": len(rows)},
                        )
                    else:
                        binding = rows[0]
                        scene_element = (
                            scene_elements[element_id][0]
                            if element_count == 1
                            else {}
                        )
                        element_is_logical_group = (
                            scene_element.get("kind") == "logical_group"
                        )
                        if element_is_logical_group:
                            if binding.get("binding_kind") != "logical-group-composite":
                                finding(
                                    "logical-group-binding-kind",
                                    element_id=element_id,
                                )
                            if (
                                binding.get("shape_id") is not None
                                or binding.get("shape_name") is not None
                            ):
                                finding(
                                    "logical-group-phantom-object",
                                    element_id=element_id,
                                )
                            member_ids = scene_element.get("member_ids")
                            bound_member_ids = binding.get("member_element_ids")
                            if (
                                not isinstance(member_ids, list)
                                or not member_ids
                                or any(
                                    not isinstance(value, str) or not value
                                    for value in member_ids
                                )
                                or len(set(member_ids)) != len(member_ids)
                            ):
                                finding(
                                    "logical-group-scene-members-invalid",
                                    element_id=element_id,
                                )
                                member_ids = []
                            if bound_member_ids != member_ids:
                                finding(
                                    "logical-group-member-drift",
                                    element_id=element_id,
                                    detail={
                                        "scene": member_ids,
                                        "binding": bound_member_ids,
                                    },
                                )

                            backend_ids = binding.get("backend_object_ids")
                            backend_names = binding.get("backend_object_names")
                            composite_identities = binding.get(
                                "backend_object_identities"
                            )
                            backend_well_formed = (
                                isinstance(backend_ids, list)
                                and isinstance(backend_names, list)
                                and bool(backend_ids)
                                and len(backend_ids) == len(backend_names)
                                and all(
                                    isinstance(shape_id, int)
                                    and not isinstance(shape_id, bool)
                                    and shape_id > 0
                                    for shape_id in backend_ids
                                )
                                and all(
                                    isinstance(shape_name, str) and shape_name
                                    for shape_name in backend_names
                                )
                                and len(set(zip(backend_ids, backend_names)))
                                == len(backend_ids)
                            )
                            if not backend_well_formed:
                                finding(
                                    "logical-group-backend-identities-invalid",
                                    element_id=element_id,
                                )
                            expected_identity_records = (
                                [
                                    {
                                        "shape_id": shape_id,
                                        "shape_name": shape_name,
                                    }
                                    for shape_id, shape_name in zip(
                                        backend_ids,
                                        backend_names,
                                        strict=True,
                                    )
                                ]
                                if backend_well_formed
                                else []
                            )
                            if composite_identities != expected_identity_records:
                                finding(
                                    "logical-group-identity-record-drift",
                                    element_id=element_id,
                                )

                            member_identities: list[tuple[int, str]] = []
                            for member_id in member_ids:
                                member_rows = physical_binding_rows.get(member_id, [])
                                if len(member_rows) != 1:
                                    finding(
                                        "logical-group-member-binding-count",
                                        element_id=element_id,
                                        detail={
                                            "member_id": member_id,
                                            "count": len(member_rows),
                                        },
                                    )
                                    continue
                                member_binding = member_rows[0]
                                shape_id = member_binding.get("shape_id")
                                shape_name = member_binding.get("shape_name")
                                if (
                                    not isinstance(shape_id, int)
                                    or isinstance(shape_id, bool)
                                    or shape_id <= 0
                                    or not isinstance(shape_name, str)
                                    or not shape_name
                                ):
                                    finding(
                                        "logical-group-member-identity-invalid",
                                        element_id=element_id,
                                        detail={"member_id": member_id},
                                    )
                                    continue
                                member_identities.append((shape_id, shape_name))
                                if member_binding.get("readback_found") is not True:
                                    finding(
                                        "logical-group-member-readback-missing",
                                        element_id=element_id,
                                        detail={"member_id": member_id},
                                    )
                            composite_pairs = (
                                list(zip(backend_ids, backend_names, strict=True))
                                if backend_well_formed
                                else []
                            )
                            if composite_pairs != member_identities:
                                finding(
                                    "logical-group-backend-member-drift",
                                    element_id=element_id,
                                    detail={
                                        "members": [list(value) for value in member_identities],
                                        "backend": [list(value) for value in composite_pairs],
                                    },
                                )
                            if binding.get("visible_object_count") != len(
                                composite_pairs
                            ):
                                finding(
                                    "logical-group-visible-object-count",
                                    element_id=element_id,
                                )
                        element_is_edge = (
                            element_count == 1
                            and scene_elements[element_id][0].get("kind") == "edge"
                        )
                        requires_single_object = (
                            element_is_edge
                            or binding.get("single_visible_object") is True
                        )
                        count = _backend_object_count(binding)
                        if requires_single_object and count != 1:
                            finding(
                                "visible-object-count",
                                element_id=element_id,
                                detail={"count": count},
                            )
                        if binding.get("readback_found") is not True:
                            finding("binding-readback-missing", element_id=element_id)

        required_value = region.get("required_relations") if isinstance(region, dict) else None
        required_relations: list[dict[str, Any]] = []
        if required_value is not None:
            if not isinstance(required_value, list) or not required_value:
                finding("required-relations-invalid")
            elif any(not isinstance(item, dict) for item in required_value):
                finding("required-relations-invalid")
            else:
                required_relations = required_value

        exhaustive_value = (
            region.get("relations_exhaustive") if isinstance(region, dict) else None
        )
        if exhaustive_value is not None and not isinstance(exhaustive_value, bool):
            finding("relations-exhaustive-invalid")
        relations_exhaustive = exhaustive_value is True

        relation_ids = [
            item.get("id")
            for item in required_relations
            if isinstance(item.get("id"), str) and item.get("id")
        ]
        for relation_id, count in Counter(relation_ids).items():
            if count != 1:
                finding(
                    "required-relation-duplicate",
                    element_id=relation_id,
                    detail={"count": count},
                )

        if relations_exhaustive:
            scoped_edge_ids = {
                element_id
                for element_id in element_ids
                if len(scene_elements.get(element_id, [])) == 1
                and scene_elements[element_id][0].get("kind") == "edge"
            }
            for undeclared_id in sorted(scoped_edge_ids - set(relation_ids)):
                finding(
                    "exhaustive-relation-missing",
                    element_id=undeclared_id,
                )

        for relation_index, relation in enumerate(required_relations):
            relation_id = relation.get("id")
            source_id = relation.get("source_id")
            target_id = relation.get("target_id")
            direction = relation.get("direction")
            start_head_type = relation.get("start_head_type")
            end_head_type = relation.get("end_head_type")
            representation = relation.get("representation")
            visible_object_count = relation.get("visible_object_count")
            if not all(
                isinstance(value, str) and value
                for value in (relation_id, source_id, target_id)
            ) or direction not in _DIRECTIONS or end_head_type not in HEAD_TYPES or (
                start_head_type is not None and start_head_type not in HEAD_TYPES
            ) or (
                representation is not None
                and representation not in {"line_arrow", "block_arrow"}
            ) or (
                visible_object_count is not None
                and (
                    not isinstance(visible_object_count, int)
                    or isinstance(visible_object_count, bool)
                    or visible_object_count < 0
                )
            ):
                finding(
                    "required-relation-schema",
                    element_id=relation_id if isinstance(relation_id, str) else None,
                    detail={"index": relation_index},
                )
                continue

            if relation_id not in element_ids:
                finding("required-relation-not-scoped", element_id=relation_id)

            for endpoint_role, endpoint_id in (
                ("source", source_id),
                ("target", target_id),
            ):
                if endpoint_id not in element_ids:
                    finding(
                        f"required-relation-{endpoint_role}-not-scoped",
                        element_id=endpoint_id,
                        detail={"relation_id": relation_id},
                    )
                endpoint_elements = scene_elements.get(endpoint_id, [])
                if not endpoint_elements:
                    finding(
                        f"required-relation-{endpoint_role}-scene-missing",
                        element_id=endpoint_id,
                        detail={"relation_id": relation_id},
                    )
                elif len(endpoint_elements) != 1:
                    finding(
                        f"required-relation-{endpoint_role}-scene-duplicate",
                        element_id=endpoint_id,
                        detail={
                            "relation_id": relation_id,
                            "count": len(endpoint_elements),
                        },
                    )
                endpoint_bindings = binding_rows.get(endpoint_id, [])
                if not endpoint_bindings:
                    finding(
                        f"required-relation-{endpoint_role}-binding-missing",
                        element_id=endpoint_id,
                        detail={"relation_id": relation_id},
                    )
                elif len(endpoint_bindings) != 1:
                    finding(
                        f"required-relation-{endpoint_role}-binding-duplicate",
                        element_id=endpoint_id,
                        detail={
                            "relation_id": relation_id,
                            "count": len(endpoint_bindings),
                        },
                    )
                elif endpoint_bindings[0].get("readback_found") is not True:
                    finding(
                        f"required-relation-{endpoint_role}-readback-missing",
                        element_id=endpoint_id,
                        detail={"relation_id": relation_id},
                    )

            edges = scene_edges.get(relation_id, [])
            if not edges:
                finding("scene-edge-missing", element_id=relation_id)
                continue
            if len(edges) != 1:
                finding(
                    "scene-edge-duplicate",
                    element_id=relation_id,
                    detail={"count": len(edges)},
                )
                continue
            edge = edges[0]
            if edge.get("source") != source_id:
                finding(
                    "scene-edge-source-mismatch",
                    element_id=relation_id,
                    detail={"expected": source_id, "actual": edge.get("source")},
                )
            if edge.get("target") != target_id:
                finding(
                    "scene-edge-target-mismatch",
                    element_id=relation_id,
                    detail={"expected": target_id, "actual": edge.get("target")},
                )

            elements = scene_elements.get(relation_id, [])
            spec = elements[0].get("arrow_spec") if len(elements) == 1 else None
            edge_spec = edge.get("arrow_spec")
            if not isinstance(spec, dict) or not isinstance(edge_spec, dict):
                finding("arrow-spec-missing", element_id=relation_id)
                continue
            if spec_sha256(spec) != spec_sha256(edge_spec):
                finding("arrow-spec-scene-drift", element_id=relation_id)

            spec_errors = validate_arrow_spec(
                spec,
                expected_input_route=meta.get("input_route"),
                expected_reference_sha256=meta.get("source_sha256"),
            )
            for error in spec_errors:
                finding(
                    "arrow-spec-invalid",
                    element_id=relation_id,
                    detail={"error": error},
                )

            topology = spec.get("topology", {})
            if topology.get("source_id") != source_id:
                finding(
                    "arrow-spec-source-mismatch",
                    element_id=relation_id,
                    detail={"expected": source_id, "actual": topology.get("source_id")},
                )
            if topology.get("target_id") != target_id:
                finding(
                    "arrow-spec-target-mismatch",
                    element_id=relation_id,
                    detail={"expected": target_id, "actual": topology.get("target_id")},
                )
            actual_direction = arrow_direction(spec)
            if actual_direction != direction:
                finding(
                    "arrow-direction-mismatch",
                    element_id=relation_id,
                    detail={"expected": direction, "actual": actual_direction},
                )
            actual_start_head = spec.get("start_head", {}).get("type")
            if start_head_type is not None and actual_start_head != start_head_type:
                finding(
                    "arrow-start-head-mismatch",
                    element_id=relation_id,
                    detail={"expected": start_head_type, "actual": actual_start_head},
                )
            actual_end_head = spec.get("end_head", {}).get("type")
            if actual_end_head != end_head_type:
                finding(
                    "arrow-end-head-mismatch",
                    element_id=relation_id,
                    detail={"expected": end_head_type, "actual": actual_end_head},
                )
            if representation is not None and spec.get("representation") != representation:
                finding(
                    "arrow-representation-mismatch",
                    element_id=relation_id,
                    detail={
                        "expected": representation,
                        "actual": spec.get("representation"),
                    },
                )

            rows = binding_rows.get(relation_id, [])
            if len(rows) == 1:
                binding = rows[0]
                if binding.get("single_visible_object") is not True:
                    finding("arrow-binding-not-single-object", element_id=relation_id)
                actual_visible_object_count = _backend_object_count(binding)
                if (
                    visible_object_count is not None
                    and actual_visible_object_count != visible_object_count
                ):
                    finding(
                        "arrow-visible-object-count-mismatch",
                        element_id=relation_id,
                        detail={
                            "expected": visible_object_count,
                            "actual": actual_visible_object_count,
                        },
                    )
                if binding.get("arrow_spec_sha256") != spec_sha256(spec):
                    finding("arrow-binding-spec-hash-mismatch", element_id=relation_id)

                if not arrow_readback_present:
                    finding("arrow-readback-report-missing", element_id=relation_id)
                else:
                    readbacks = arrow_readback_rows.get(relation_id, [])
                    if not readbacks:
                        finding("arrow-readback-record-missing", element_id=relation_id)
                    elif len(readbacks) != 1:
                        finding(
                            "arrow-readback-record-duplicate",
                            element_id=relation_id,
                            detail={"count": len(readbacks)},
                        )
                    else:
                        readback = readbacks[0]
                        if readback.get("status") != "PASS":
                            finding(
                                "arrow-readback-status",
                                element_id=relation_id,
                                detail={"actual": readback.get("status")},
                            )
                        if readback.get("arrow_spec_sha256") != spec_sha256(spec):
                            finding(
                                "arrow-readback-spec-hash-mismatch",
                                element_id=relation_id,
                            )
                        expected_identity = (
                            binding.get("shape_id"),
                            binding.get("shape_name"),
                        )
                        actual_identity = (
                            readback.get("shape_id"),
                            readback.get("shape_name"),
                        )
                        if (
                            expected_identity[0] is None
                            or not isinstance(expected_identity[1], str)
                            or not expected_identity[1]
                            or actual_identity != expected_identity
                        ):
                            finding(
                                "arrow-readback-shape-identity-mismatch",
                                element_id=relation_id,
                                detail={
                                    "expected": list(expected_identity),
                                    "actual": list(actual_identity),
                                },
                            )

        region_reports.append(
            {
                "id": region_id,
                "critical": bool(region.get("critical", False)) if isinstance(region, dict) else False,
                "declared_element_ids": element_ids,
                "required_relation_ids": relation_ids,
                "relations_exhaustive": relations_exhaustive,
                "pass": not findings,
                "findings": findings,
            }
        )

    unique_blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "region_contract_audit",
        "reference_sha256": meta.get("source_sha256"),
        "pass": not unique_blockers,
        "blockers": unique_blockers,
        "regions": region_reports,
    }
