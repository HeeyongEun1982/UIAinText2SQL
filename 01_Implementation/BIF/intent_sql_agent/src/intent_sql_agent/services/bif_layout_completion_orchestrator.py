from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Callable

from intent_sql_agent.services.cqd_query_layout_compiler import compile_cqd_query_layout


BIF_LAYOUT_GAP_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["patches"],
    "properties": {"patches": {"type": "array", "items": {"$ref": "#/$defs/patch"}}},
    "$defs": {
        "value": {"oneOf": [
            {"type": "null"}, {"type": "string"}, {"type": "number"},
            {"type": "boolean"},
            {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}},
        ]},
        "source": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "role"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "role": {"type": "string", "minLength": 1},
            },
        },
        "field": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "role", "source", "semantic_ref", "aggregate", "distinct"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "role": {"type": "string", "minLength": 1},
                "source": {"type": "string", "minLength": 1},
                "semantic_ref": {"type": "string", "minLength": 1},
                "aggregate": {"enum": ["count", "sum", "avg", "min", "max", None]},
                "distinct": {"type": "boolean"},
            },
        },
        "predicate": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "field", "role", "semantic_ref", "operator", "value"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "field": {"type": "string", "minLength": 1},
                "role": {"type": "string", "minLength": 1},
                "semantic_ref": {"type": "string", "minLength": 1},
                "operator": {"type": ["string", "null"]},
                "value": {"$ref": "#/$defs/value"},
            },
        },
        "order": {
            "type": "object", "additionalProperties": False,
            "required": ["field", "aggregate", "direction"],
            "properties": {
                "field": {"type": "string", "minLength": 1},
                "aggregate": {"enum": ["count", "sum", "avg", "min", "max", None]},
                "direction": {"enum": ["asc", "desc"]},
            },
        },
        "patch": {
            "type": "object", "additionalProperties": False,
            "required": [
                "cqd_ref", "operation", "target_slot_ids", "evidence_ids", "reason",
                "sources", "fields", "predicates", "select", "group_by", "order_by", "limit"
            ],
            "properties": {
                "cqd_ref": {"type": "string", "minLength": 1},
                "operation": {"enum": ["add", "keep", "replace", "clarification_required"]},
                "target_slot_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "evidence_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "reason": {"type": "string", "minLength": 1},
                "sources": {"type": "array", "items": {"$ref": "#/$defs/source"}},
                "fields": {"type": "array", "items": {"$ref": "#/$defs/field"}},
                "predicates": {"type": "array", "items": {"$ref": "#/$defs/predicate"}},
                "select": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "group_by": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "order_by": {"type": "array", "items": {"$ref": "#/$defs/order"}},
                "limit": {"type": ["integer", "null"], "minimum": 1},
            },
        },
    },
}


class BIFLayoutCompletionOrchestrator:
    """Own the draft and admit only evidence-bound, monotonic LLM generation."""

    def __init__(
        self, *, max_rounds: int | None = None,
        max_clarification_cycles: int | None = None,
    ) -> None:
        self.max_rounds = max_rounds
        self.max_clarification_cycles = max_clarification_cycles

    def run(
        self,
        *,
        original_question: str,
        confirmed_cqd: dict[str, Any],
        propose: Callable[[dict[str, Any], list[dict[str, Any]], int], dict[str, Any]],
        clarify: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current_cqd = copy.deepcopy(confirmed_cqd)
        history: list[dict[str, Any]] = []
        clarification_cycles = 0
        repeat_state: dict[str, dict[str, Any]] = {}
        while True:
            working, coverage = compile_cqd_query_layout(
                original_question=original_question, confirmed_cqd=current_cqd
            )
            history.append({
                "round": 0, "cycle": clarification_cycles,
                "source": "deterministic_ir", "layout": copy.deepcopy(working),
                "coverage": copy.deepcopy(coverage),
            })
            round_number = 0
            while True:
                gaps = [
                    item for item in coverage["requirements"]
                    if item["status"] in {"needs_review", "missing"}
                ]
                if not gaps:
                    break
                if self.max_rounds is not None and round_number >= self.max_rounds:
                    break
                round_number += 1
                proposal = propose(copy.deepcopy(working), copy.deepcopy(gaps), round_number)
                admitted = self._admit(working, coverage, proposal, gaps)
                history.append({
                    "round": round_number, "cycle": clarification_cycles,
                    "source": "llm_evidence_patch",
                    "proposal": copy.deepcopy(proposal), "admitted": admitted,
                    "layout": copy.deepcopy(working), "coverage": copy.deepcopy(coverage),
                })
                no_progress = self._track_no_progress(
                    repeat_state=repeat_state,
                    working=working,
                    proposal=proposal,
                    admitted=admitted,
                    round_number=round_number,
                    cycle=clarification_cycles,
                    history=history,
                )
                if no_progress:
                    for gap in coverage["requirements"]:
                        if gap["status"] in {"needs_review", "missing"}:
                            gap["continuation_status"] = "deferred_no_progress"
                    break
                if admitted["applied_count"] == 0:
                    rejected_by_ref = {
                        str(item.get("cqd_ref") or ""): str(item.get("reason") or "")
                        for item in admitted.get("rejected", [])
                    }
                    patches_by_ref = {
                        str(item.get("cqd_ref") or ""): copy.deepcopy(item)
                        for item in proposal.get("patches", []) or []
                    }
                    for gap in coverage["requirements"]:
                        ref = str(gap.get("cqd_ref") or "")
                        if ref in rejected_by_ref:
                            gap["last_rejection_reason"] = rejected_by_ref[ref]
                            gap["last_rejected_patch"] = patches_by_ref.get(ref, {})

            clarification_gaps = [
                item for item in coverage["requirements"]
                if item["status"] == "clarification_required"
            ]
            if (
                not clarification_gaps or clarify is None
                or (
                    self.max_clarification_cycles is not None
                    and clarification_cycles >= self.max_clarification_cycles
                )
            ):
                break
            revised_cqd = clarify(copy.deepcopy(current_cqd), copy.deepcopy(clarification_gaps))
            if not isinstance(revised_cqd, dict):
                for gap in clarification_gaps:
                    gap["clarification_retry_reason"] = "clarifier_returned_non_object"
                continue
            if revised_cqd == current_cqd:
                for gap in clarification_gaps:
                    gap["clarification_retry_reason"] = "clarifier_returned_unchanged_cqd"
                continue
            current_cqd = copy.deepcopy(revised_cqd)
            clarification_cycles += 1

        open_semantic = [
            item for item in coverage["requirements"]
            if item["status"] in {"needs_review", "missing", "clarification_required"}
        ]
        needs_clarification = any(
            item["status"] == "clarification_required" for item in open_semantic
        )
        coverage["status"] = (
            "needs_clarification" if needs_clarification
            else "incomplete" if open_semantic else "complete"
        )
        finalized = not open_semantic
        return copy.deepcopy(working), {
            "authority": "BIFLayoutCompletionOrchestrator",
            "finalized_once": finalized,
            "rounds": sum(item.get("source") == "llm_evidence_patch" for item in history),
            "clarification_cycles": clarification_cycles,
            "confirmed_cqd": copy.deepcopy(current_cqd),
            "coverage": coverage,
            "history": history,
        }

    @staticmethod
    def _track_no_progress(
        *, repeat_state: dict[str, dict[str, Any]], working: dict[str, Any],
        proposal: dict[str, Any], admitted: dict[str, Any], round_number: int,
        cycle: int, history: list[dict[str, Any]],
    ) -> bool:
        deferred = False
        patches = {
            str(item.get("cqd_ref") or ""): item
            for item in proposal.get("patches", []) or []
        }
        for result in admitted.get("results", []) or []:
            ref = str(result.get("cqd_ref") or "")
            action = str(result.get("action") or "")
            if action != "retry":
                repeat_state.pop(ref, None)
                continue
            if result.get("added_slot_ids"):
                # A partial admission changed the authoritative Layout. This is
                # progress, so a later failure starts a fresh repeat sequence.
                repeat_state.pop(ref, None)
                result["repeat_count"] = 0
                continue
            signature_payload = {
                "cqd_ref": ref,
                "remaining_components": result.get("remaining_components", []),
                "patch": patches.get(ref),
                "outcome": result.get("outcome"),
                "reason": result.get("reason"),
            }
            signature = hashlib.sha256(
                json.dumps(
                    signature_payload, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ).encode("utf-8")
            ).hexdigest()
            previous = repeat_state.get(ref)
            repeat_count = (
                int(previous.get("repeat_count", 0)) + 1
                if previous and previous.get("signature") == signature
                else 1
            )
            attempt = {
                "round": round_number,
                "cycle": cycle,
                "signature": signature,
                "outcome": result.get("outcome"),
                "reason": result.get("reason"),
            }
            attempts = (
                [*previous.get("attempts", []), attempt]
                if previous and previous.get("signature") == signature
                else [attempt]
            )
            repeat_state[ref] = {
                "signature": signature,
                "repeat_count": repeat_count,
                "attempts": attempts,
            }
            result["repeat_count"] = repeat_count
            if repeat_count >= 5:
                diagnostic = {
                    "warning_code": "BIF_LAYOUT_NO_PROGRESS_CONTINUED",
                    "cqd_ref": ref,
                    "repeat_count": repeat_count,
                    "layout_hash": hashlib.sha256(
                        json.dumps(
                            working, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), default=str,
                        ).encode("utf-8")
                    ).hexdigest(),
                    "proposal_hash": hashlib.sha256(
                        json.dumps(
                            patches.get(ref), ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), default=str,
                        ).encode("utf-8")
                    ).hexdigest(),
                    "classification": result.get("outcome"),
                    "last_rejection_reason": result.get("reason"),
                    "attempt_history": attempts,
                }
                history.append({
                    "round": round_number,
                    "cycle": cycle,
                    "source": "no_progress_warning",
                    "diagnostic": copy.deepcopy(diagnostic),
                })
                result["action"] = "deferred_no_progress"
                result["diagnostic"] = copy.deepcopy(diagnostic)
                deferred = True
        return deferred

    @staticmethod
    def _admit(
        working: dict[str, Any], coverage: dict[str, Any], proposal: dict[str, Any],
        gaps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        gap_by_ref = {str(item["cqd_ref"]): item for item in gaps}
        evidence_registry = coverage.get("evidence_registry", {}) or {}
        known_sources = {str(item["id"]) for item in working["sources"]}
        known_fields = {str(item["id"]) for item in working["fields"]}
        known_predicates = {str(item["id"]) for item in working["predicates"]}
        field_sources = {
            str(item["id"]): str(item["source"]) for item in working["fields"]
        }
        field_roles = {
            str(item["id"]): str(item["role"]) for item in working["fields"]
        }
        predicate_fields = {
            str(item["id"]): str(item["field"]) for item in working["predicates"]
        }
        closed_refs: set[str] = set()
        applied = 0
        rejected: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []

        def record(
            ref: str, outcome: str, action: str, reason: str,
            **extra: Any,
        ) -> None:
            results.append({
                "cqd_ref": ref, "outcome": outcome,
                "action": action, "reason": reason, **extra,
            })

        def reject(ref: str, outcome: str, reason: str, **extra: Any) -> None:
            rejected.append({
                "cqd_ref": ref, "reason": reason, "outcome": outcome,
            })
            record(ref, outcome, "retry", reason, **extra)

        for patch in proposal.get("patches", []) or []:
            ref = str(patch.get("cqd_ref") or "")
            gap = gap_by_ref.get(ref)
            if gap is None or ref in closed_refs:
                reject(ref, "INVALID_PROPOSAL", "not_an_open_unique_cqd_ref")
                continue
            evidence_ids = {str(item) for item in patch.get("evidence_ids", []) or []}
            allowed_evidence = set(gap.get("evidence_ids", []) or [])
            if not evidence_ids or not evidence_ids <= allowed_evidence:
                reject(ref, "INVALID_PROPOSAL", "missing_or_unowned_evidence")
                continue
            operation = str(patch.get("operation") or "")
            fragments_present = any(patch.get(key) for key in (
                "sources", "fields", "predicates", "select", "group_by", "order_by"
            )) or patch.get("limit") is not None

            if operation == "clarification_required":
                if fragments_present:
                    reject(ref, "INVALID_PROPOSAL", "clarification_with_fragment")
                    continue
                gap["status"] = "clarification_required"
                gap["clarification_reason"] = str(patch.get("reason") or "")
                applied += 1
                closed_refs.add(ref)
                record(
                    ref, "CLARIFICATION_REQUIRED", "clarify",
                    str(patch.get("reason") or ""),
                )
                continue
            if operation == "keep":
                target_ids = {str(item) for item in patch.get("target_slot_ids", []) or []}
                gap_slot_ids = {str(item) for item in gap.get("slot_ids", []) or []}
                if (
                    fragments_present or not gap.get("keep_allowed", False)
                    or not target_ids
                    or not target_ids <= gap_slot_ids
                ):
                    reject(ref, "INVALID_PROPOSAL", "invalid_keep")
                    continue
                gap["status"] = "mapped"
                applied += 1
                closed_refs.add(ref)
                record(ref, "ALREADY_PRESENT_EQUIVALENT", "complete", "valid_keep")
                continue
            if operation == "replace":
                gap["status"] = "clarification_required"
                gap["clarification_reason"] = (
                    "Layout replacement is forbidden; E2 must revise CQD before recompilation"
                )
                applied += 1
                closed_refs.add(ref)
                record(
                    ref, "CLARIFICATION_REQUIRED", "clarify",
                    "layout_replacement_requires_cqd_revision",
                )
                continue
            if operation != "add" or not fragments_present:
                reject(ref, "INVALID_PROPOSAL", "invalid_add_operation")
                continue
            normalized_patch, equivalence = _remove_existing_equivalents(
                working, patch, gap
            )
            normalized_fragments = any(normalized_patch.get(key) for key in (
                "sources", "fields", "predicates", "select", "group_by", "order_by"
            )) or normalized_patch.get("limit") is not None
            if (
                not normalized_fragments
                and equivalence["already_present"]
                and not equivalence["conflicts"]
            ):
                required_slot_ids = {
                    str(item) for item in gap.get("slot_ids", []) or []
                }
                equivalent_slot_ids = {
                    str(item.get("id") or "")
                    for item in equivalence["already_present"]
                    if item.get("kind") in {"source", "field", "predicate"}
                }
                if (
                    not required_slot_ids
                    or not required_slot_ids <= equivalent_slot_ids
                ):
                    reject(
                        ref, "PARTIALLY_MAPPED",
                        "equivalent_patch_does_not_cover_gap_slots",
                        already_present=equivalence["already_present"],
                    )
                    continue
                gap["status"] = "mapped"
                gap["processing_state"] = "COMPLETE"
                gap["completion_result"] = "ALREADY_PRESENT_EQUIVALENT"
                gap["completion_reason"] = "A3 proposal is already represented in the Layout"
                gap["completed_components"] = list(dict.fromkeys([
                    *(gap.get("completed_components", []) or []),
                    *equivalence.get("completed_components", []),
                ]))
                gap["remaining_components"] = []
                gap["admitted_evidence_ids"] = sorted(evidence_ids)
                applied += 1
                closed_refs.add(ref)
                record(
                    ref, "ALREADY_PRESENT_EQUIVALENT", "complete",
                    "proposal_already_fully_represented",
                    already_present=equivalence["already_present"],
                )
                continue
            admitted_patch, fragment_rejections, fragment_completed = _partition_valid_addition(
                normalized_patch, ref, known_sources, known_fields, known_predicates,
                gap=gap, evidence_registry=evidence_registry,
                field_sources=field_sources, field_roles=field_roles,
                predicate_fields=predicate_fields,
            )
            added: list[str] = []
            if _patch_has_fragments(admitted_patch):
                added = _apply_addition(working, admitted_patch)
                known_sources.update(
                    item["id"] for item in admitted_patch.get("sources", []) or []
                )
                known_fields.update(
                    item["id"] for item in admitted_patch.get("fields", []) or []
                )
                known_predicates.update(
                    item["id"] for item in admitted_patch.get("predicates", []) or []
                )
                field_sources.update({
                    str(item["id"]): str(item["source"])
                    for item in admitted_patch.get("fields", []) or []
                })
                field_roles.update({
                    str(item["id"]): str(item["role"])
                    for item in admitted_patch.get("fields", []) or []
                })
                predicate_fields.update({
                    str(item["id"]): str(item["field"])
                    for item in admitted_patch.get("predicates", []) or []
                })
                gap["slot_ids"] = list(dict.fromkeys([
                    *gap.get("slot_ids", []), *added,
                ]))
                slot_refs = gap.setdefault("slot_semantic_refs", {})
                for item in [
                    *(admitted_patch.get("fields", []) or []),
                    *(admitted_patch.get("predicates", []) or []),
                ]:
                    slot_refs[str(item["id"])] = str(item.get("semantic_ref") or "")

            remaining_components = [
                *[
                    {
                        "component": f"{item['kind']}:{item['id']}.{component}",
                        "reason": f"{item['kind']}_id_conflict",
                        "existing": copy.deepcopy(item["existing"].get(component)),
                        "proposed": copy.deepcopy(item["proposed"].get(component)),
                    }
                    for item in equivalence["conflicts"]
                    for component in item["components"]
                ],
                *fragment_rejections,
            ]
            completed_components = list(dict.fromkeys([
                *(gap.get("completed_components", []) or []),
                *equivalence.get("completed_components", []),
                *fragment_completed,
                *_completed_patch_components(admitted_patch),
            ]))
            if remaining_components:
                gap["processing_state"] = "PARTIAL"
                gap["completed_components"] = completed_components
                gap["remaining_components"] = copy.deepcopy(remaining_components)
                if equivalence["conflicts"] and not added:
                    gap["last_result"] = "ID_CONFLICT"
                elif completed_components or added:
                    gap["last_result"] = "PARTIALLY_MAPPED"
                else:
                    gap["last_result"] = "INVALID_PROPOSAL"
                reason = str(remaining_components[0]["reason"])
                reject(
                    ref,
                    str(gap["last_result"]),
                    reason,
                    already_present=equivalence["already_present"],
                    completed_components=completed_components,
                    remaining_components=copy.deepcopy(remaining_components),
                    added_slot_ids=added,
                )
                continue
            gap["status"] = "mapped"
            gap["processing_state"] = "COMPLETE"
            gap["completed_components"] = completed_components
            gap["remaining_components"] = []
            gap["admitted_evidence_ids"] = sorted(evidence_ids)
            applied += 1
            closed_refs.add(ref)
            record(
                ref, "APPLIED_NEW", "complete", "new_fragments_admitted",
                added_slot_ids=added,
                already_present=equivalence["already_present"],
            )
        result_refs = {str(item.get("cqd_ref") or "") for item in results}
        for ref in gap_by_ref:
            if ref not in closed_refs and ref not in result_refs:
                reject(ref, "MISSING_PATCH", "missing_patch_for_open_gap")
        return {"applied_count": applied, "rejected": rejected, "results": results}


def _remove_existing_equivalents(
    working: dict[str, Any], patch: dict[str, Any], gap: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove exact duplicates and distinguish them from same-ID conflicts."""
    normalized = copy.deepcopy(patch)
    already_present: list[dict[str, str]] = []
    completed_components: list[str] = []
    conflicts: list[dict[str, Any]] = []
    slot_refs = {
        str(key): str(value)
        for key, value in (gap.get("slot_semantic_refs", {}) or {}).items()
    }

    specifications = (
        ("sources", "source", ("role",)),
        ("fields", "field", ("role", "source", "aggregate", "distinct")),
        ("predicates", "predicate", ("field", "role", "operator", "value")),
    )
    for collection, kind, keys in specifications:
        existing_by_id = {
            str(item.get("id") or ""): item
            for item in working.get(collection, []) or []
        }
        remaining = []
        for proposed in normalized.get(collection, []) or []:
            item_id = str(proposed.get("id") or "")
            existing = existing_by_id.get(item_id)
            if existing is None:
                remaining.append(proposed)
                continue
            component_keys = list(keys)
            if kind in {"field", "predicate"}:
                component_keys.append("semantic_ref")
            differing: list[str] = []
            for key in component_keys:
                existing_value = (
                    slot_refs.get(item_id) if key == "semantic_ref"
                    else existing.get(key)
                )
                proposed_value = proposed.get(key)
                component_ref = f"{kind}:{item_id}.{key}"
                if existing_value == proposed_value:
                    completed_components.append(component_ref)
                else:
                    differing.append(key)
            if differing:
                conflicts.append({
                    "kind": kind, "id": item_id,
                    "components": differing,
                    "existing": {
                        key: slot_refs.get(item_id) if key == "semantic_ref"
                        else copy.deepcopy(existing.get(key))
                        for key in differing
                    },
                    "proposed": {
                        key: copy.deepcopy(proposed.get(key)) for key in differing
                    },
                })
            else:
                already_present.append({"kind": kind, "id": item_id})
        normalized[collection] = remaining

    for collection in ("select", "group_by"):
        existing = {str(item) for item in working.get(collection, []) or []}
        remaining = []
        for item in normalized.get(collection, []) or []:
            item_id = str(item)
            if item_id in existing:
                already_present.append({"kind": collection, "id": item_id})
            else:
                remaining.append(item)
        normalized[collection] = remaining

    existing_orders = working.get("order_by", []) or []
    remaining_orders = []
    for index, item in enumerate(normalized.get("order_by", []) or []):
        if item in existing_orders:
            already_present.append({
                "kind": "order_by", "id": str(item.get("field") or index)
            })
        else:
            remaining_orders.append(item)
    normalized["order_by"] = remaining_orders

    if normalized.get("limit") is not None and normalized.get("limit") == working.get("limit"):
        already_present.append({"kind": "limit", "id": str(normalized["limit"])})
        normalized["limit"] = None

    return normalized, {
        "already_present": already_present,
        "completed_components": list(dict.fromkeys(completed_components)),
        "conflicts": conflicts,
    }


def _validate_root_permissions(patch: dict[str, Any], ref: str) -> str | None:
    root = ref.strip("/").split("/", 1)[0]
    sources = patch.get("sources", []) or []
    predicates = patch.get("predicates", []) or []
    if sources and root not in {"populations", "scopes"}:
        return "source_not_allowed_for_cqd_root"
    if predicates and root not in {
        "populations", "predicates", "time_constraints", "comparisons", "scopes"
    }:
        return "predicate_not_allowed_for_cqd_root"
    if (patch.get("group_by") or patch.get("order_by") or patch.get("limit") is not None) and root not in {
        "aggregations", "groupings", "comparisons"
    }:
        return "group_order_limit_not_allowed_for_cqd_root"
    if patch.get("select") and root not in {
        "selections", "outputs", "expressions", "aggregations", "scopes"
    }:
        return "select_not_allowed_for_cqd_root"
    return None


def _patch_has_fragments(patch: dict[str, Any]) -> bool:
    return any(patch.get(key) for key in (
        "sources", "fields", "predicates", "select", "group_by", "order_by"
    )) or patch.get("limit") is not None


def _empty_add_patch(patch: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: copy.deepcopy(value)
            for key, value in patch.items()
            if key not in {
                "sources", "fields", "predicates", "select",
                "group_by", "order_by", "limit",
            }
        },
        "sources": [], "fields": [], "predicates": [],
        "select": [], "group_by": [], "order_by": [], "limit": None,
    }


def _completed_patch_components(patch: dict[str, Any]) -> list[str]:
    completed: list[str] = []
    for kind in ("source", "field", "predicate"):
        collection = f"{kind}s"
        for item in patch.get(collection, []) or []:
            item_id = str(item.get("id") or "")
            if kind == "predicate":
                completed.extend(
                    f"predicate:{item_id}.{key}"
                    for key in ("field", "role", "semantic_ref", "operator", "value")
                )
            elif kind == "field":
                completed.extend(
                    f"field:{item_id}.{key}"
                    for key in ("role", "source", "semantic_ref", "aggregate", "distinct")
                )
            else:
                completed.append(f"source:{item_id}.role")
    completed.extend(f"select:{item}" for item in patch.get("select", []) or [])
    completed.extend(f"group_by:{item}" for item in patch.get("group_by", []) or [])
    completed.extend(
        f"order_by:{item.get('field')}"
        for item in patch.get("order_by", []) or []
    )
    if patch.get("limit") is not None:
        completed.append("limit")
    return completed


def _rejection_components(kind: str, item: Any, reason: str) -> list[str]:
    item_id = (
        str(item.get("id") or item.get("field") or "unknown")
        if isinstance(item, dict) else str(item)
    )
    if kind == "predicate":
        if "operator" in reason:
            return [f"predicate:{item_id}.operator"]
        if any(token in reason for token in ("value", "literal", "semantics")):
            return [f"predicate:{item_id}.value"]
        if "semantic_ref" in reason:
            return [f"predicate:{item_id}.semantic_ref"]
        if "field" in reason:
            return [f"predicate:{item_id}.field"]
    if kind == "field":
        if "source" in reason:
            return [f"field:{item_id}.source"]
        if "semantic_ref" in reason:
            return [f"field:{item_id}.semantic_ref"]
        if "aggregate" in reason:
            return [f"field:{item_id}.aggregate"]
        if "distinct" in reason:
            return [f"field:{item_id}.distinct"]
    return [f"{kind}:{item_id}"]


def _components_valid_before_failure(kind: str, item: Any, reason: str) -> list[str]:
    if not isinstance(item, dict):
        return []
    item_id = str(item.get("id") or item.get("field") or "unknown")
    if kind == "predicate":
        base = [
            f"predicate:{item_id}.field",
            f"predicate:{item_id}.role",
            f"predicate:{item_id}.semantic_ref",
        ]
        if "operator" in reason:
            return base
        if any(token in reason for token in ("value", "literal", "semantics")):
            return [*base, f"predicate:{item_id}.operator"]
    if kind == "field":
        base = [
            f"field:{item_id}.role",
            f"field:{item_id}.source",
            f"field:{item_id}.semantic_ref",
        ]
        if "aggregate" in reason:
            return base
        if "distinct" in reason:
            return [*base, f"field:{item_id}.aggregate"]
    return []


def _partition_valid_addition(
    patch: dict[str, Any], ref: str, known_sources: set[str],
    known_fields: set[str], known_predicates: set[str], *,
    gap: dict[str, Any], evidence_registry: dict[str, dict[str, Any]],
    field_sources: dict[str, str], field_roles: dict[str, str],
    predicate_fields: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Admit independent fragments in dependency order and retain only failures."""
    admitted = _empty_add_patch(patch)
    rejected: list[dict[str, Any]] = []
    partially_completed: list[str] = []
    units: list[tuple[str, str, Any]] = []
    for collection, kind in (
        ("sources", "source"), ("fields", "field"),
        ("predicates", "predicate"), ("select", "select"),
        ("group_by", "group_by"), ("order_by", "order_by"),
    ):
        units.extend(
            (collection, kind, copy.deepcopy(item))
            for item in patch.get(collection, []) or []
        )
    if patch.get("limit") is not None:
        units.append(("limit", "limit", copy.deepcopy(patch["limit"])))

    for collection, kind, item in units:
        candidate = copy.deepcopy(admitted)
        if collection == "limit":
            candidate["limit"] = item
        else:
            candidate[collection].append(item)
        reason = _validate_addition(
            candidate, ref, known_sources, known_fields, known_predicates,
            gap=gap, evidence_registry=evidence_registry,
            field_sources=field_sources, field_roles=field_roles,
            predicate_fields=predicate_fields,
        )
        if reason is None:
            admitted = candidate
            continue
        partially_completed.extend(
            _components_valid_before_failure(kind, item, reason)
        )
        for component in _rejection_components(kind, item, reason):
            rejected.append({
                "component": component,
                "reason": reason,
                "proposed": copy.deepcopy(item),
            })
    return admitted, rejected, list(dict.fromkeys(partially_completed))


def _validate_addition(
    patch: dict[str, Any], ref: str, known_sources: set[str],
    known_fields: set[str], known_predicates: set[str], *,
    gap: dict[str, Any], evidence_registry: dict[str, dict[str, Any]],
    field_sources: dict[str, str], field_roles: dict[str, str],
    predicate_fields: dict[str, str],
) -> str | None:
    permission_error = _validate_root_permissions(patch, ref)
    if permission_error:
        return permission_error
    root = ref.strip("/").split("/", 1)[0]
    sources = patch.get("sources", []) or []
    fields = patch.get("fields", []) or []
    predicates = patch.get("predicates", []) or []
    source_ids = [str(item.get("id") or "") for item in sources]
    field_ids = [str(item.get("id") or "") for item in fields]
    predicate_ids = [str(item.get("id") or "") for item in predicates]
    if "" in source_ids:
        return "invalid_source"
    if len(source_ids) != len(set(source_ids)):
        return "duplicate_source_in_proposal"
    if set(source_ids) & known_sources:
        return "unclassified_existing_source"
    if "" in field_ids:
        return "invalid_field"
    if len(field_ids) != len(set(field_ids)):
        return "duplicate_field_in_proposal"
    if set(field_ids) & known_fields:
        return "unclassified_existing_field"
    available_sources = known_sources | set(source_ids)
    if any(str(item.get("source") or "") not in available_sources for item in fields):
        return "field_uses_unknown_source"
    if "" in predicate_ids:
        return "invalid_predicate"
    if len(predicate_ids) != len(set(predicate_ids)):
        return "duplicate_predicate_in_proposal"
    if set(predicate_ids) & known_predicates:
        return "unclassified_existing_predicate"
    available_fields = known_fields | set(field_ids)
    if any(str(item.get("field") or "") not in available_fields for item in predicates):
        return "predicate_uses_unknown_field"
    refs = [
        *[str(item) for item in patch.get("select", []) or []],
        *[str(item) for item in patch.get("group_by", []) or []],
        *[str(item.get("field") or "") for item in patch.get("order_by", []) or []],
    ]
    if any(item not in available_fields for item in refs):
        return "clause_uses_unknown_field"
    owned_slots = {str(item) for item in gap.get("slot_ids", []) or []}
    owned_fields = owned_slots & known_fields
    owned_predicates = owned_slots & known_predicates
    for predicate_id in owned_predicates:
        field_id = predicate_fields.get(predicate_id)
        if field_id:
            owned_fields.add(field_id)
    owned_sources = owned_slots & known_sources
    owned_sources.update(
        field_sources[field_id]
        for field_id in owned_fields if field_id in field_sources
    )
    target_slot_ids = {
        str(item) for item in patch.get("target_slot_ids", []) or []
    }
    if not target_slot_ids <= owned_slots:
        return "patch_targets_slot_owned_by_another_cqd_requirement"
    if any(
        str(item.get("source") or "") in known_sources
        and str(item.get("source") or "") not in owned_sources
        for item in fields
    ):
        return "field_uses_source_owned_by_another_cqd_requirement"
    if any(
        str(item.get("field") or "") in known_fields
        and str(item.get("field") or "") not in owned_fields
        for item in predicates
    ):
        return "predicate_uses_field_owned_by_another_cqd_requirement"
    if any(item in known_fields and item not in owned_fields for item in refs):
        return "clause_uses_field_owned_by_another_cqd_requirement"
    new_field_refs = {
        str(item["id"]): str(item.get("semantic_ref") or "") for item in fields
    }
    existing_slot_refs = {
        str(key): str(value)
        for key, value in (gap.get("slot_semantic_refs", {}) or {}).items()
    }
    for item in [*fields, *predicates]:
        semantic_ref = str(item.get("semantic_ref") or "")
        if _semantic_ref_value(gap, semantic_ref) is _MISSING:
            return "semantic_ref_is_not_owned_by_cqd_requirement"
    for predicate in predicates:
        field_id = str(predicate.get("field") or "")
        field_ref = new_field_refs.get(field_id) or existing_slot_refs.get(field_id)
        if not field_ref or field_ref != str(predicate.get("semantic_ref") or ""):
            return "predicate_semantic_ref_does_not_match_field"
    evidence_values = [gap.get("payload")]
    for evidence_id in patch.get("evidence_ids", []) or []:
        evidence = evidence_registry.get(str(evidence_id), {})
        evidence_values.append(evidence.get("value", evidence.get("text")))
    evidence_tokens = set(_semantic_tokens(evidence_values))
    available_field_roles = {
        **field_roles,
        **{str(item["id"]): str(item["role"]) for item in fields},
    }
    semantic_scaffold = {
        "status", "flag", "indicator", "value", "field", "result",
        "population", "constraint", "filter", "category", "type",
        "measure", "metric", "count", "sum", "avg", "average",
        "min", "max", "total",
    }
    for predicate in predicates:
        field_role_tokens = set(
            _semantic_tokens(available_field_roles.get(str(predicate["field"]), ""))
        ) - semantic_scaffold
        predicate_tokens = set(_semantic_tokens([
            predicate.get("role"), predicate.get("value")
        ])) - semantic_scaffold
        if field_role_tokens and predicate_tokens and not field_role_tokens & predicate_tokens:
            return "predicate_semantics_do_not_match_field_role"
    for field_id in refs:
        role_tokens = set(
            _semantic_tokens(available_field_roles.get(field_id, ""))
        ) - semantic_scaffold
        if role_tokens and evidence_tokens and not role_tokens & evidence_tokens:
            return "clause_field_semantics_not_supported_by_cqd_requirement"
    allowed_operators = _allowed_operators(root, evidence_values)
    for predicate in predicates:
        operator = _normalize_operator(predicate.get("operator"))
        if not operator or operator not in allowed_operators:
            return "generated_operator_not_supported_by_evidence"
    allowed_aggregates = _allowed_aggregates(root, evidence_values)
    for field in fields:
        aggregate = _normalize_aggregate(field.get("aggregate"))
        if aggregate and aggregate not in allowed_aggregates:
            return "generated_aggregate_not_supported_by_evidence"
        if field.get("distinct") is True and not _evidence_mentions(evidence_values, "distinct"):
            return "generated_distinct_not_supported_by_evidence"
    allowed_directions = _allowed_directions(root, evidence_values)
    for order in patch.get("order_by", []) or []:
        aggregate = _normalize_aggregate(order.get("aggregate"))
        direction = _normalize_direction(order.get("direction"))
        if aggregate and aggregate not in allowed_aggregates:
            return "generated_order_aggregate_not_supported_by_evidence"
        if direction not in allowed_directions:
            return "generated_order_direction_not_supported_by_evidence"
    if patch.get("limit") is not None and not _limit_supported(
        int(patch["limit"]), evidence_values
    ):
        return "generated_limit_not_supported_by_evidence"
    semantic_values: list[Any] = []
    semantic_values.extend(item.get("role") for item in sources)
    semantic_values.extend(item.get("role") for item in fields)
    for predicate in predicates:
        semantic_values.extend((predicate.get("role"), predicate.get("value")))
    for value in semantic_values:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            value_tokens = set(_semantic_tokens(value))
            if value_tokens - evidence_tokens - semantic_scaffold:
                return "generated_semantics_not_supported_by_evidence"
        elif not _contains_primitive(evidence_values, value):
            return "generated_literal_not_supported_by_evidence"
    for item in fields:
        supported = _semantic_ref_value(gap, str(item["semantic_ref"]))
        if not _semantic_value_supported(item.get("role"), supported, semantic_scaffold):
            return "field_role_not_supported_by_semantic_ref"
    for item in predicates:
        supported = _semantic_ref_value(gap, str(item["semantic_ref"]))
        if not _semantic_value_supported(
            [item.get("role"), item.get("value")], supported, semantic_scaffold
        ):
            return "predicate_value_not_supported_by_semantic_ref"
    return None


def _semantic_tokens(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [token for item in value.values() for token in _semantic_tokens(item)]
    if isinstance(value, list):
        return [token for item in value for token in _semantic_tokens(item)]
    if not isinstance(value, str):
        return []
    ignored = {"a", "an", "the", "of", "for", "is", "are", "to", "all"}
    result: list[str] = []
    for token in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE):
        if token in ignored:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "status")):
            token = token[:-1]
        result.append(token)
    return result


def _contains_primitive(values: Any, wanted: Any) -> bool:
    if isinstance(values, dict):
        return any(_contains_primitive(value, wanted) for value in values.values())
    if isinstance(values, list):
        return any(_contains_primitive(value, wanted) for value in values)
    return type(values) is type(wanted) and values == wanted


_MISSING = object()


def _semantic_ref_value(gap: dict[str, Any], semantic_ref: str) -> Any:
    base = str(gap.get("cqd_ref") or "")
    if semantic_ref == base:
        return gap.get("payload")
    prefix = f"{base}/"
    if not semantic_ref.startswith(prefix):
        return _MISSING
    node: Any = gap.get("payload")
    suffix = semantic_ref[len(prefix):]
    try:
        for raw in suffix.split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            node = node[int(key)] if isinstance(node, list) else node[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return _MISSING
    return node


def _semantic_value_supported(value: Any, evidence: Any, scaffold: set[str]) -> bool:
    value_tokens = set(_semantic_tokens(value)) - scaffold
    evidence_tokens = set(_semantic_tokens(evidence))
    if value_tokens - evidence_tokens:
        return False
    primitives: list[Any] = []
    if isinstance(value, list):
        primitives.extend(item for item in value if not isinstance(item, str))
    elif not isinstance(value, (dict, str)):
        primitives.append(value)
    return all(_contains_primitive(evidence, item) for item in primitives)


def _named_values(value: Any, names: set[str]) -> list[Any]:
    result: list[Any] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in names:
                result.append(item)
            result.extend(_named_values(item, names))
    elif isinstance(value, list):
        for item in value:
            result.extend(_named_values(item, names))
    return result


def _normalize_operator(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace(" ", "_")
    aliases = {"=": "equals", "==": "equals", "eq": "equals", "equal": "equals"}
    return aliases.get(normalized, normalized)


def _normalize_aggregate(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    return "avg" if normalized == "average" else normalized


def _normalize_direction(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    return {"ascending": "asc", "descending": "desc"}.get(normalized, normalized)


def _allowed_operators(root: str, evidence_values: list[Any]) -> set[str]:
    result = {
        _normalize_operator(item)
        for item in _named_values(evidence_values, {"operator", "comparison_operator"})
        if item not in (None, "")
    }
    if root in {"populations", "scopes"}:
        result.add("equals")
    starts = _named_values(evidence_values, {"start", "start_date"})
    ends = _named_values(evidence_values, {"end", "end_date"})
    if starts and ends:
        result.add("between")
    return result


def _allowed_aggregates(root: str, evidence_values: list[Any]) -> set[str]:
    result = {
        _normalize_aggregate(item)
        for item in _named_values(evidence_values, {"aggregate", "aggregation", "type"})
        if _normalize_aggregate(item) in {"count", "sum", "avg", "min", "max"}
    }
    modes = {
        str(item).casefold() for item in _named_values(evidence_values, {"type"})
    }
    if root == "aggregations" and "mode" in modes:
        result.add("count")
    return result


def _allowed_directions(root: str, evidence_values: list[Any]) -> set[str]:
    result = {
        _normalize_direction(item)
        for item in _named_values(evidence_values, {"direction", "order", "sort_direction"})
        if _normalize_direction(item) in {"asc", "desc"}
    }
    modes = {
        str(item).casefold() for item in _named_values(evidence_values, {"type"})
    }
    if root == "aggregations" and "mode" in modes:
        result.add("desc")
    return result


def _evidence_mentions(values: Any, word: str) -> bool:
    return word.casefold() in set(_semantic_tokens(values)) or bool(
        _named_values(values, {word.casefold()})
    )


def _limit_supported(limit: int, evidence_values: list[Any]) -> bool:
    explicit_limits = _named_values(
        evidence_values, {"limit", "top_k", "top_n", "row_limit"}
    )
    if _contains_primitive(explicit_limits, limit):
        return True
    tie_policies = {
        str(item).strip().casefold()
        for item in _named_values(evidence_values, {"tie_policy"})
    }
    return limit == 1 and bool(tie_policies & {"single", "one", "single_tie"})


def _apply_addition(working: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    added: list[str] = []
    for source in patch.get("sources", []) or []:
        working["sources"].append({"id": source["id"], "role": source["role"], "table": None})
        added.append(str(source["id"]))
    for field in patch.get("fields", []) or []:
        working["fields"].append({
            "id": field["id"], "role": field["role"], "source": field["source"],
            "column": None, "expression": None, "aggregate": field["aggregate"],
            "distinct": field["distinct"],
        })
        added.append(str(field["id"]))
    for predicate in patch.get("predicates", []) or []:
        working["predicates"].append(copy.deepcopy(predicate))
        predicate_id = str(predicate["id"])
        working["where"] = (
            predicate_id if not working.get("where") else f"({working['where']}) AND {predicate_id}"
        )
        added.append(predicate_id)
    for field_id in patch.get("select", []) or []:
        if field_id not in working["select"]:
            working["select"].append(field_id)
    for field_id in patch.get("group_by", []) or []:
        if field_id not in working["group_by"]:
            working["group_by"].append(field_id)
    working["order_by"].extend(copy.deepcopy(patch.get("order_by", []) or []))
    if patch.get("limit") is not None:
        working["limit"] = int(patch["limit"])
    return added
