from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


ROOT_ROLE = {
    "objective": "objective",
    "populations": "population",
    "predicates": "filter",
    "expressions": "calculation",
    "aggregations": "aggregate",
    "groupings": "group",
    "selections": "selection",
    "time_constraints": "time",
    "comparisons": "comparison",
    "outputs": "output",
    "scopes": "scope",
    "deferred_grounding": "grounding",
    "relationships": "join",
    "ordering": "order",
    "limits": "limit",
    "predicate_logic": "logic",
}

ROLE_OPERATION = {
    "objective": "custom", "population": "include", "filter": "filter",
    "calculation": "calculate", "aggregate": "aggregate", "group": "group",
    "selection": "select", "time": "filter", "comparison": "compare",
    "output": "retrieve", "scope": "select", "grounding": "ground",
    "join": "relate", "order": "sort", "limit": "limit",
    "logic": "custom",
}

CQD_METADATA_ROOTS = {
    "schema_version", "question_id", "original_question", "traceability",
    "status", "readiness", "assumptions", "metadata", "rendered_text",
    "cqd_id", "contract_version",
}

CONSTRAINT_KEYS = {
    "operator": "relation", "relation": "relation", "value": "value",
    "values": "membership", "set_relation": "set_relation",
    "value_mode": "multiplicity", "cardinality": "multiplicity",
    "multiplicity": "multiplicity", "output_type": "multiplicity",
    "presentation": "presentation", "distinct": "distinctness",
    "null_handling": "null_handling", "tie_policy": "tie_policy",
    "validity": "validity", "validation": "validity",
    "polarity": "polarity", "scope": "scope", "boolean_attachment": "boolean_attachment",
    "start": "temporal", "end": "temporal", "grain": "temporal",
}


def attach_bif_slot_layout(
    confirmed_cqd: dict[str, Any], bif: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach the lossless CQD slot view to the compatibility BIF sections."""
    result = copy.deepcopy(bif)
    layout = build_bif_slot_layout(confirmed_cqd, bif)
    result["slot_layout"] = layout
    return result, validate_bif_slot_layout(layout)


def build_bif_slot_layout(
    confirmed_cqd: dict[str, Any], bif: dict[str, Any]
) -> dict[str, Any]:
    slots: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for source_ref, root, payload in _cqd_requirements(confirmed_cqd):
        role = ROOT_ROLE.get(root, "grounding")
        slot_id = _slot_id(role, source_ref)
        slot = {
            "slot_id": slot_id,
            "role": role,
            "concept": {
                "name": _concept_name(payload, role),
                "description": _description(payload, role),
            },
            "operation": _operation(payload, role),
            "semantic_payload": copy.deepcopy(payload) if isinstance(payload, dict)
            else {"value": copy.deepcopy(payload)},
            "constraints": _constraints(slot_id, source_ref, payload),
            "extensions": [],
            "source_refs": [source_ref],
            "source_spans": _source_spans(payload),
            "decision_refs": _decision_refs(payload),
            "legacy_refs": _legacy_refs(root, source_ref, bif),
            "state": "pending_grounding",
        }
        slots.append(slot)
        coverage.append({
            "requirement_ref": source_ref,
            "slot_ids": [slot_id],
            "status": "represented",
        })
    return {
        "layout_version": "2.0",
        "source_cqd_hash": (
            confirmed_cqd.get("traceability", {}) or {}
        ).get("state_hash") or _hash(confirmed_cqd),
        "slots": slots,
        "coverage": coverage,
    }


def validate_bif_slot_layout(layout: dict[str, Any]) -> dict[str, Any]:
    slots = layout.get("slots", []) or []
    ids = [str(item.get("slot_id") or "") for item in slots]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    known = set(ids)
    coverage = layout.get("coverage", []) or []
    uncovered = [
        item.get("requirement_ref") for item in coverage
        if item.get("status") == "represented"
        and not set(str(value) for value in item.get("slot_ids", []) or []) <= known
    ]
    represented = {
        str(item.get("requirement_ref")) for item in coverage
        if item.get("status") in {"represented", "deferred", "unsupported"}
    }
    source_refs = {
        str(ref) for slot in slots for ref in slot.get("source_refs", []) or []
    }
    missing_coverage = sorted(source_refs - represented)
    coverage_refs = [str(item.get("requirement_ref") or "") for item in coverage]
    duplicate_coverage_refs = sorted({
        item for item in coverage_refs if coverage_refs.count(item) > 1
    })
    orphan_coverage_refs = sorted(set(coverage_refs) - source_refs)
    status = "complete" if not (
        not slots or duplicate_ids or uncovered or missing_coverage
        or duplicate_coverage_refs or orphan_coverage_refs
    ) else "invalid"
    report = {
        "status": status,
        "slot_count": len(slots),
        "coverage_count": len(coverage),
        "duplicate_slot_ids": duplicate_ids,
        "coverage_with_unknown_slots": uncovered,
        "uncovered_requirement_refs": missing_coverage,
        "duplicate_coverage_refs": duplicate_coverage_refs,
        "orphan_coverage_refs": orphan_coverage_refs,
    }
    if status != "complete":
        raise ValueError(f"BIF_SLOT_LAYOUT_INVALID: {report}")
    return report


def find_semantic_slot_id(bif: dict[str, Any], *legacy_refs: str) -> str | None:
    wanted = {str(item) for item in legacy_refs if item}
    for slot in (bif.get("slot_layout", {}) or {}).get("slots", []) or []:
        if wanted & {str(item) for item in slot.get("legacy_refs", []) or []}:
            return str(slot.get("slot_id"))
    return None


def _cqd_requirements(cqd: dict[str, Any]) -> list[tuple[str, str, Any]]:
    result: list[tuple[str, str, Any]] = []
    for root in ROOT_ROLE:
        value = cqd.get(root)
        if value in (None, "", [], {}):
            continue
        if root == "predicates" and isinstance(value, dict):
            atoms = value.get("atoms", []) or []
            for index, item in enumerate(atoms):
                result.append((f"/predicates/atoms/{index}", root, item))
            if value.get("root") not in (None, "", [], {}):
                result.append(("/predicates/root", "predicate_logic", value["root"]))
        elif isinstance(value, list):
            result.extend((f"/{root}/{index}", root, item) for index, item in enumerate(value))
        else:
            result.append((f"/{root}", root, value))
    for root, value in cqd.items():
        if root in ROOT_ROLE or root in CQD_METADATA_ROOTS or value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            result.extend((f"/{root}/{index}", root, item) for index, item in enumerate(value))
        else:
            result.append((f"/{root}", root, value))
    return result


def _legacy_refs(root: str, source_ref: str, bif: dict[str, Any]) -> list[str]:
    match = re.search(r"/(\d+)$", source_ref)
    index = int(match.group(1)) if match else 0
    refs: list[str] = []
    if root == "populations":
        refs.append("/target_population/primary_entity")
    elif root == "predicate_logic":
        refs.append("/target_population/inclusion_logic")
    elif root == "predicates":
        conditions = [
            *((bif.get("target_population", {}) or {}).get("inclusion_conditions", []) or []),
            *((bif.get("target_population", {}) or {}).get("exclusion_conditions", []) or []),
        ]
        if index < len(conditions):
            refs.append(str(conditions[index].get("condition_id") or ""))
    elif root in {"expressions", "aggregations"}:
        metrics = bif.get("metrics", []) or []
        steps = (bif.get("calculation", {}) or {}).get("steps", []) or []
        if index < len(metrics): refs.append(str(metrics[index].get("metric_id") or ""))
        if index < len(steps): refs.append(str(steps[index].get("step_id") or ""))
    elif root == "groupings":
        dims = (bif.get("granularity", {}) or {}).get("dimensions", []) or []
        if index < len(dims): refs.append(f"dimension:{dims[index]}")
    elif root == "selections":
        items = (bif.get("scope", {}) or {}).get("selections", []) or []
        if index < len(items): refs.append(str(items[index].get("selection_id") or ""))
    elif root == "time_constraints":
        items = (bif.get("time_basis", {}) or {}).get("periods", []) or []
        if index < len(items):
            item = items[index]
            refs.append("period:" + "|".join(
                str(item.get(key) if item.get(key) is not None else "")
                for key in ("start", "end", "grain")
            ))
    elif root == "comparisons":
        items = bif.get("comparisons", []) or []
        if index < len(items): refs.append(str(items[index].get("comparison_id") or ""))
    elif root == "outputs":
        items = (bif.get("output_expectation", {}) or {}).get("fields", []) or []
        if index < len(items): refs.append(str(items[index].get("field_id") or ""))
    elif root == "scopes":
        selections = (bif.get("scope", {}) or {}).get("selections", []) or []
        dimensions = (bif.get("scope", {}) or {}).get("grouping_dimensions", []) or []
        if index < len(selections):
            refs.append(str(selections[index].get("selection_id") or ""))
        if index < len(dimensions):
            refs.append(f"dimension:{dimensions[index]}")
    return sorted({item for item in refs if item})


def _constraints(slot_id: str, source_ref: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return [{"constraint_id": f"{slot_id}_value", "type": "value", "value": copy.deepcopy(payload), "description": None, "source_ref": source_ref}]
    result: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for key, value in payload.items():
        constraint_type = CONSTRAINT_KEYS.get(str(key))
        if constraint_type is None or value in (None, "", [], {}):
            continue
        counts[constraint_type] = counts.get(constraint_type, 0) + 1
        result.append({
            "constraint_id": f"{slot_id}_{constraint_type}_{counts[constraint_type]}",
            "type": constraint_type,
            "value": copy.deepcopy(value),
            "description": f"Preserved from CQD field {key}",
            "source_ref": f"{source_ref}/{key}",
        })
    return result


def _concept_name(payload: Any, role: str) -> str:
    if isinstance(payload, dict):
        for key in ("concept", "name", "subject", "entity", "metric", "content", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _slug(value)[:80]
    if isinstance(payload, str) and payload.strip():
        return _slug(payload)[:80]
    return f"{role}_requirement"


def _description(payload: Any, role: str) -> str:
    if isinstance(payload, dict):
        for key in ("description", "content", "meaning", "source_text", "text", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip(): return value.strip()
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return str(payload) if str(payload).strip() else role


def _operation(payload: Any, role: str) -> str:
    raw = ""
    if isinstance(payload, dict): raw = str(payload.get("operation") or "").casefold()
    aliases = {"count": "aggregate", "sum": "aggregate", "average": "aggregate", "rank": "select", "order": "sort", "retrieve": "retrieve", "include": "include", "exclude": "exclude", "filter": "filter", "compare": "compare"}
    return aliases.get(raw, ROLE_OPERATION[role])


def _source_spans(payload: Any) -> list[str]:
    if not isinstance(payload, dict): return []
    result: list[str] = []
    for key in ("source_span", "source_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip(): result.append(value.strip())
        elif isinstance(value, list): result.extend(str(item) for item in value if str(item).strip())
    return list(dict.fromkeys(result))


def _decision_refs(payload: Any) -> list[str]:
    result: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if "decision" in str(key).casefold():
                    if isinstance(child, str) and child:
                        result.append(child)
                    elif isinstance(child, list):
                        result.extend(str(item) for item in child if item)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return list(dict.fromkeys(result))


def _slot_id(role: str, source_ref: str) -> str:
    return f"slot_{role}_{_slug(source_ref)}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "requirement"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
