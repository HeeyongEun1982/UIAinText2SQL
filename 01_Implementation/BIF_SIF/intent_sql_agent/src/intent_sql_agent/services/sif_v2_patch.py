from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from intent_sql_agent.services.frame_state import frame_hash
from intent_sql_agent.services.contract_validator import ContractError, validate_contract
from intent_sql_agent.services.sif_v2_semantic_validator import validate_sif_v2_semantics
from intent_sql_agent.services.sif_v2_validator import (
    validate_sif_v2_invariants,
    validate_sif_v2_physical_schema,
)
from intent_sql_agent.services.sif_v2_lowerer import (
    SIFV2LoweringError,
    lower_sif_v2_to_query_plan,
)
from intent_sql_agent.services.sif_v2 import (
    _refresh_semantic_slot_coverage,
    apply_semantic_slot_readiness,
)
from intent_sql_agent.services.grounding_validator import validate_literal_domains


SIF_V2_GROUNDING_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "request_id", "base_frame_hash", "source_additions", "join_additions",
        "primary_entity_source_ref", "primary_constraint_results", "slot_proposals",
    ],
    "properties": {
        "request_id": {"type": "string"},
        "base_frame_hash": {"type": "string"},
        "source_additions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["source_id", "kind", "name", "alias", "origin_refs"],
            "properties": {
                "source_id": {"type": "string"}, "kind": {"enum": ["table", "view"]},
                "name": {"type": "string"}, "alias": {"type": ["string", "null"]},
                "origin_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
        }},
        "join_additions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["join_id", "join_type", "left_source", "right_source", "key_pairs", "origin_refs"],
            "properties": {
                "join_id": {"type": "string"},
                "join_type": {"enum": ["inner", "left", "right", "full", "cross"]},
                "left_source": {"type": "string"}, "right_source": {"type": "string"},
                "key_pairs": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["left_column", "right_column"],
                    "properties": {"left_column": {"type": "string"}, "right_column": {"type": "string"}},
                }},
                "origin_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
        }},
        "primary_entity_source_ref": {"type": ["string", "null"]},
        "primary_constraint_results": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["constraint_id", "status", "evidence", "reason"],
            "properties": {
                "constraint_id": {"type": "string"},
                "status": {"enum": ["grounded", "not_applicable", "clarification_required", "unsupported"]},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": ["string", "null"]},
            },
        }},
        "slot_proposals": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["grounding_id", "binding_mode", "binding_composition", "bindings", "constraint_results", "confidence", "unresolved_reason"],
            "properties": {
                "grounding_id": {"type": "string"},
                "binding_mode": {"enum": ["direct", "code_mapping", "range", "aggregation", "derived_expression", "window", "subquery", "set_operation", "type_cast", "date_component"]},
                "binding_composition": {"type": ["string", "null"], "enum": ["all_of", "any_of", None]},
                "bindings": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["source_ref", "column", "sql_operator", "physical_value", "data_type", "transformation"],
                    "properties": {
                        "source_ref": {"type": "string"}, "column": {"type": "string"},
                        "sql_operator": {"type": ["string", "null"]}, "physical_value": {},
                        "data_type": {"type": ["string", "null"]},
                        "transformation": {"enum": ["direct", "code_mapping", "range", "aggregation", "derived_expression", "window", "subquery", "set_operation", "type_cast", "date_component"]},
                    },
                }},
                "constraint_results": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["constraint_id", "status", "evidence", "reason"],
                    "properties": {
                        "constraint_id": {"type": "string"},
                        "status": {"enum": ["grounded", "not_applicable", "clarification_required", "unsupported"]},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": ["string", "null"]},
                    },
                }},
                "confidence": {"enum": ["high", "medium", "low"]},
                "unresolved_reason": {"type": ["string", "null"]},
            },
        }},
    },
}


def apply_sif_v2_grounding_proposal(
    sif: dict[str, Any],
    proposal: dict[str, Any],
    *,
    expected_request_id: str,
    allowed_grounding_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Stage model grounding by slot ID; semantic refs and BIF logic are untouchable."""
    if proposal.get("request_id") != expected_request_id:
        return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "request_id_mismatch"}]
    if proposal.get("base_frame_hash") != frame_hash(sif):
        return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "stale_base_frame_hash"}]
    candidate = copy.deepcopy(sif)
    support = candidate["physical_support"]
    events: list[dict[str, Any]] = []
    source_ids = {str(item["source_id"]) for item in support["sources"]}
    for source in proposal.get("source_additions", []) or []:
        source_id = str(source["source_id"])
        if source_id in source_ids:
            existing = next(item for item in support["sources"] if str(item["source_id"]) == source_id)
            if existing != source:
                return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "source_redefinition", "source_id": source_id}]
            continue
        support["sources"].append(copy.deepcopy(source))
        source_ids.add(source_id)
        events.append({"operation": "stage_source", "source_id": source_id})
    join_ids = {str(item["join_id"]) for item in support["joins"]}
    for join in proposal.get("join_additions", []) or []:
        if str(join["left_source"]) not in source_ids or str(join["right_source"]) not in source_ids:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "join_unknown_source", "join_id": join.get("join_id")}]
        join_id = str(join["join_id"])
        if join_id in join_ids:
            existing = next(
                item for item in support["joins"]
                if str(item["join_id"]) == join_id
            )
            if existing != join:
                return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "join_redefinition", "join_id": join_id}]
            continue
        support["joins"].append(copy.deepcopy(join))
        join_ids.add(join_id)
        events.append({"operation": "stage_join", "join_id": join_id})

    primary_ref = proposal.get("primary_entity_source_ref")
    primary = candidate["target_population"]["primary_entity_grounding"]
    if primary_ref is not None:
        if "ground_primary_entity" not in allowed_grounding_ids:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "primary_outside_allowed_slots"}]
        if str(primary_ref) not in source_ids:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "primary_unknown_source"}]
        if primary.get("status") in {"confirmed", "system_resolved"} and primary.get("source_ref") != primary_ref:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "locked_primary_grounding"}]
        primary["source_ref"] = primary_ref
        primary["status"] = "proposed"
        constraint_error = _apply_constraint_results(
            primary, proposal.get("primary_constraint_results", []) or []
        )
        if constraint_error:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", **constraint_error}]
        events.append({"operation": "stage_primary_entity", "source_ref": primary_ref})

    slots = {str(item["grounding_id"]): item for item in _all_slots(candidate)}
    patch_ids = [str(item.get("grounding_id")) for item in proposal.get("slot_proposals", []) or []]
    if len(patch_ids) != len(set(patch_ids)):
        return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "duplicate_slot_patch"}]
    outside = sorted(set(patch_ids) - {str(item) for item in allowed_grounding_ids})
    if outside:
        return copy.deepcopy(sif), [{
            "operation": "reject_proposal", "reason": "slot_outside_allowed_scope",
            "grounding_ids": outside,
        }]
    for patch in proposal.get("slot_proposals", []) or []:
        grounding_id = str(patch["grounding_id"])
        slot = slots.get(grounding_id)
        if slot is None:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "unknown_grounding_id", "grounding_id": grounding_id}]
        if slot.get("status") in {"confirmed", "system_resolved"}:
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "locked_grounding_slot", "grounding_id": grounding_id}]
        bindings = copy.deepcopy(patch.get("bindings", []) or [])
        if any(str(item.get("source_ref")) not in source_ids for item in bindings):
            return copy.deepcopy(sif), [{"operation": "reject_proposal", "reason": "binding_unknown_source", "grounding_id": grounding_id}]
        slot["binding_mode"] = patch["binding_mode"]
        if "condition_ref" in slot and patch.get("binding_composition") is not None:
            slot["binding_composition"] = patch["binding_composition"]
        slot["bindings"] = bindings
        slot["status"] = "proposed" if bindings else "unresolved"
        constraint_error = _apply_constraint_results(
            slot, patch.get("constraint_results", []) or []
        )
        if constraint_error:
            return copy.deepcopy(sif), [{
                "operation": "reject_proposal", "grounding_id": grounding_id,
                **constraint_error,
            }]
        events.append({
            "operation": "stage_grounding_slot", "grounding_id": grounding_id,
            "binding_count": len(bindings), "confidence": patch.get("confidence"),
            "unresolved_reason": patch.get("unresolved_reason"),
        })
    _refresh_state(candidate)
    return candidate, [*events, {"operation": "proposal_staged"}]


def admit_sif_v2_grounding_proposal(
    bif: dict[str, Any],
    sif: dict[str, Any],
    proposal: dict[str, Any],
    schema_catalog: dict[str, Any],
    *,
    expected_request_id: str,
    allowed_grounding_ids: set[str],
    resolution: str,
    sif_schema: dict[str, Any] | None = None,
    database: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The sole admission path from a model proposal to committed SIF grounding."""
    staged, stage_events = apply_sif_v2_grounding_proposal(
        sif,
        proposal,
        expected_request_id=expected_request_id,
        allowed_grounding_ids=allowed_grounding_ids,
    )
    if any(item.get("operation") == "reject_proposal" for item in stage_events):
        return copy.deepcopy(sif), stage_events
    findings = _admission_findings(bif, staged, schema_catalog, sif_schema)
    if findings:
        return copy.deepcopy(sif), [
            *stage_events,
            {"operation": "reject_admission", "reason": "validation_failed", "findings": findings},
        ]
    commit_ids = [
        str(item.get("grounding_id"))
        for item in proposal.get("slot_proposals", []) or []
        if item.get("bindings") and all(
            result.get("status") in {"grounded", "not_applicable"}
            for result in item.get("constraint_results", []) or []
        )
    ]
    if proposal.get("primary_entity_source_ref") is not None and all(
        result.get("status") in {"grounded", "not_applicable"}
        for result in proposal.get("primary_constraint_results", []) or []
    ):
        commit_ids.append("ground_primary_entity")
    committed, commit_events = _commit_sif_v2_groundings_unchecked(
        staged, commit_ids, resolution=resolution
    )
    _refresh_semantic_slot_coverage(committed, bif)
    apply_semantic_slot_readiness(committed)
    findings = _admission_findings(bif, committed, schema_catalog, sif_schema)
    if not findings and database is not None and committed.get("status") == "ready":
        try:
            plan = lower_sif_v2_to_query_plan(bif, committed)
            findings.extend(validate_literal_domains(database, plan))
        except SIFV2LoweringError as exc:
            findings.append({"issue": "sif_v2_lowering_failure", "error": str(exc)})
    if findings:
        return copy.deepcopy(sif), [
            *stage_events,
            *commit_events,
            {"operation": "reject_admission", "reason": "post_commit_validation_failed", "findings": findings},
        ]
    return committed, [*stage_events, *commit_events, {"operation": "admission_committed"}]


def _commit_sif_v2_groundings_unchecked(
    sif: dict[str, Any], grounding_ids: list[str], *, resolution: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if resolution not in {"confirmed", "system_resolved"}:
        raise ValueError("resolution must be confirmed or system_resolved")
    candidate = copy.deepcopy(sif)
    primary = candidate["target_population"]["primary_entity_grounding"]
    slots = {str(item["grounding_id"]): item for item in _all_slots(candidate)}
    slots[str(primary["grounding_id"])] = primary
    events: list[dict[str, Any]] = []
    for grounding_id in grounding_ids:
        slot = slots.get(str(grounding_id))
        if slot is None:
            return copy.deepcopy(sif), [{"operation": "reject_commit", "reason": "unknown_grounding_id", "grounding_id": grounding_id}]
        has_value = (
            bool(slot.get("source_ref")) if "entity_ref" in slot
            else bool(slot.get("bindings") or slot.get("implementation"))
        )
        if slot.get("status") != "proposed" or not has_value:
            return copy.deepcopy(sif), [{"operation": "reject_commit", "reason": "slot_not_proposed", "grounding_id": grounding_id}]
        slot["status"] = resolution
        events.append({"operation": "commit_grounding_slot", "grounding_id": grounding_id, "resolution": resolution})
    _refresh_state(candidate)
    return candidate, events


def _admission_findings(
    bif: dict[str, Any],
    sif: dict[str, Any],
    schema_catalog: dict[str, Any],
    sif_schema: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if sif_schema:
        try:
            validate_contract(sif, sif_schema, "SIF 2.0 admission candidate")
        except ContractError as exc:
            findings.append({"issue": "sif_v2_contract_failure", "error": str(exc)})
    findings.extend(validate_sif_v2_invariants(bif, sif))
    findings.extend(validate_sif_v2_physical_schema(sif, schema_catalog))
    findings.extend(validate_sif_v2_semantics(bif, sif, schema_catalog))
    return findings


def _all_slots(sif: dict[str, Any]) -> list[dict[str, Any]]:
    population = sif["target_population"]
    result = [*population["inclusion_condition_groundings"], *population["exclusion_condition_groundings"]]
    for section, key in (
        ("metrics", "metric_groundings"), ("calculation", "step_groundings"),
        ("granularity", "dimension_groundings"), ("scope", "selection_groundings"),
        ("time_basis", "period_groundings"), ("comparisons", "comparison_groundings"),
    ):
        result.extend(sif[section][key])
    result.extend(sif["output_expectation"]["field_groundings"])
    result.extend(sif["output_expectation"]["ordering_groundings"])
    return result


def _refresh_state(sif: dict[str, Any]) -> None:
    primary = sif["target_population"]["primary_entity_grounding"]
    unresolved: list[dict[str, str]] = []
    if primary.get("status") not in {"confirmed", "system_resolved"}:
        unresolved.append({
            "grounding_id": primary["grounding_id"], "semantic_ref": primary["entity_ref"],
            "reason": "physical binding is not resolved", "owner": "system",
        })
    for slot in _all_slots(sif):
        if slot.get("status") not in {"confirmed", "system_resolved"}:
            unresolved.append({
                "grounding_id": slot["grounding_id"],
                "semantic_ref": str(slot.get("condition_ref") or slot.get("semantic_ref")),
                "reason": "physical binding is not resolved", "owner": "system",
            })
    sif["unresolved_groundings"] = unresolved
    ready = not unresolved
    sif["status"] = "ready" if ready else "needs_grounding"
    sif["readiness"] = {
        "state": "ready" if ready else "needs_grounding",
        "blocking_refs": [item["semantic_ref"] for item in unresolved],
    }


def _apply_constraint_results(
    slot: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any] | None:
    expected = {
        str(item.get("constraint_id"))
        for item in slot.get("constraint_coverage", []) or []
    }
    actual = [str(item.get("constraint_id")) for item in results]
    if len(actual) != len(set(actual)):
        return {"reason": "duplicate_constraint_result"}
    if set(actual) != expected:
        return {
            "reason": "constraint_coverage_mismatch",
            "missing_constraint_ids": sorted(expected - set(actual)),
            "orphan_constraint_ids": sorted(set(actual) - expected),
        }
    for result in results:
        status = str(result.get("status") or "")
        if status == "grounded" and not (result.get("evidence") or []):
            return {
                "reason": "grounded_constraint_without_evidence",
                "constraint_id": result.get("constraint_id"),
            }
        if status != "grounded" and not str(result.get("reason") or "").strip():
            return {
                "reason": "non_grounded_constraint_without_reason",
                "constraint_id": result.get("constraint_id"),
            }
    by_id = {str(item["constraint_id"]): item for item in results}
    slot["constraint_coverage"] = [copy.deepcopy(by_id[item]) for item in sorted(expected)]
    return None
