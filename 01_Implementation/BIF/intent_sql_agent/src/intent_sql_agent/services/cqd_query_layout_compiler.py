from __future__ import annotations

import copy
import re
from typing import Any


def compile_cqd_query_layout(
    *, original_question: str, confirmed_cqd: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lower explicit CQD structure to a simple BIF without generative rewriting."""
    layout: dict[str, Any] = {
        "schema_version": "4.0", "phase": "bif", "sources": [], "fields": [],
        "select": [], "predicates": [], "where": None, "group_by": [],
        "having_predicates": [], "having": None, "order_by": [], "joins": [],
        "distinct": False, "limit": None,
    }
    coverage: list[dict[str, Any]] = []
    field_by_role: dict[str, str] = {}

    populations = confirmed_cqd.get("populations", []) or []
    for index, population in enumerate(populations):
        ref = f"/populations/{index}"
        entity = _value(population, "entity", "name", "description") or f"population {index + 1}"
        source_id = f"source_{_slug(entity)}_{index + 1}"
        layout["sources"].append({"id": source_id, "role": entity, "table": None})
        slots = [source_id]
        slot_semantic_refs = {source_id: f"{ref}/entity"}
        for key, value in (population.items() if isinstance(population, dict) else []):
            if key in {"entity", "source_text", "source_span"} or value in (None, "", [], {}):
                continue
            if _qualifier_already_has_atom(key, value, confirmed_cqd):
                continue
            field_id = _ensure_field(layout, field_by_role, str(key), source_id)
            predicate_id = f"predicate_population_{index + 1}_{_slug(str(key))}"
            layout["predicates"].append({
                "id": predicate_id, "field": field_id,
                "role": f"population {key}", "operator": "equals",
                "value": copy.deepcopy(value),
            })
            slots.extend([field_id, predicate_id])
            semantic_ref = f"{ref}/{_pointer_escape(str(key))}"
            slot_semantic_refs[field_id] = semantic_ref
            slot_semantic_refs[predicate_id] = semantic_ref
        coverage.append(_coverage(
            ref, "needs_review", slots, population,
            clarification_family="ENTITY_BOUNDARY",
            slot_semantic_refs=slot_semantic_refs,
        ))

    if not layout["sources"]:
        layout["sources"].append({"id": "source_primary", "role": "requested population", "table": None})
    primary_source = str(layout["sources"][0]["id"])

    atom_ids: list[str] = []
    for index, atom in enumerate((confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or []):
        ref = f"/predicates/atoms/{index}"
        role = _value(atom, "dimension", "subject", "description") or f"condition {index + 1}"
        source_id = _source_for(atom.get("applies_to"), layout["sources"], primary_source)
        field_id = _ensure_field(layout, field_by_role, role, source_id)
        predicate_id = str(atom.get("condition_id") or f"predicate_{index + 1}")
        predicate_id = f"predicate_{_slug(predicate_id)}"
        layout["predicates"].append({
            "id": predicate_id, "field": field_id, "role": role,
            "operator": atom.get("operator"), "value": copy.deepcopy(atom.get("value")),
        })
        atom_ids.append(predicate_id)
        coverage.append(_coverage(ref, "mapped", [field_id, predicate_id], atom))

    root = (confirmed_cqd.get("predicates", {}) or {}).get("root")
    layout["where"] = _boolean_logic(root, atom_ids)

    selection_fields: list[str] = []
    for index, selection in enumerate(confirmed_cqd.get("selections", []) or []):
        ref = f"/selections/{index}"
        role = _semantic_value(selection) or f"selection {index + 1}"
        field_id = _ensure_field(layout, field_by_role, role, primary_source)
        if field_id not in layout["select"]:
            layout["select"].append(field_id)
        selection_fields.append(field_id)
        coverage.append(_coverage(ref, "mapped", [field_id], selection))

    for index, output in enumerate(confirmed_cqd.get("outputs", []) or []):
        ref = f"/outputs/{index}"
        role = _semantic_value(output) or f"output {index + 1}"
        field_id = _find_related_field(role, layout["fields"])
        if field_id is None:
            field_id = _ensure_field(layout, field_by_role, role, primary_source)
            layout["select"].append(field_id)
        coverage.append(_coverage(ref, "mapped", [field_id], output))

    for index, expression in enumerate(confirmed_cqd.get("expressions", []) or []):
        ref = f"/expressions/{index}"
        role = _semantic_value(expression) or _value(
            expression, "name", "metric", "formula", "expression"
        ) or f"expression {index + 1}"
        field_id = _find_related_field(role, layout["fields"]) or _ensure_field(
            layout, field_by_role, role, primary_source
        )
        field = next(item for item in layout["fields"] if item["id"] == field_id)
        formula = _value(expression, "formula", "expression", "calculation")
        if formula:
            field["expression"] = formula
        coverage.append(_coverage(ref, "mapped", [field_id], expression))

    for index, grouping in enumerate(confirmed_cqd.get("groupings", []) or []):
        ref = f"/groupings/{index}"
        role = _semantic_value(grouping) or f"grouping {index + 1}"
        field_id = _find_related_field(role, layout["fields"]) or _ensure_field(
            layout, field_by_role, role, primary_source
        )
        if field_id not in layout["group_by"]:
            layout["group_by"].append(field_id)
        coverage.append(_coverage(ref, "mapped", [field_id], grouping))

    for index, aggregation in enumerate(confirmed_cqd.get("aggregations", []) or []):
        ref = f"/aggregations/{index}"
        kind = _norm(aggregation.get("type"))
        role = str(aggregation.get("target") or "").strip()
        field_id = _find_related_field(role, layout["fields"])
        if field_id is None:
            field_id = _ensure_field(layout, field_by_role, role or "aggregate target", primary_source)
        slots = [field_id]
        aggregation_status = "mapped"
        if kind == "mode":
            if field_id not in layout["select"]:
                layout["select"].append(field_id)
            if field_id not in layout["group_by"]:
                layout["group_by"].append(field_id)
            layout["order_by"].append({
                "field": field_id, "aggregate": "count", "direction": "desc"
            })
            if _norm(aggregation.get("tie_policy")) == "single":
                layout["limit"] = 1
            elif _norm(aggregation.get("tie_policy")) not in {"all", "all_ties"}:
                aggregation_status = "needs_review"
        elif kind in {"count", "sum", "avg", "average", "min", "max"}:
            field = next(item for item in layout["fields"] if item["id"] == field_id)
            field["aggregate"] = "avg" if kind == "average" else kind
            if field_id not in layout["select"]:
                layout["select"].append(field_id)
        coverage.append(_coverage(
            ref, aggregation_status, slots, aggregation,
            clarification_family="TIE_POLICY" if aggregation_status == "needs_review" else None,
        ))

    for index, time_item in enumerate(confirmed_cqd.get("time_constraints", []) or []):
        ref = f"/time_constraints/{index}"
        role = _value(time_item, "time_dimension", "dimension", "description") or "time"
        existing = _find_predicate_for_role(role, layout)
        if existing:
            coverage.append(_coverage(ref, "mapped", existing, time_item))
            continue
        field_id = _ensure_field(layout, field_by_role, role, primary_source)
        predicate_id = f"predicate_time_{index + 1}"
        start = time_item.get("start_date") or time_item.get("start")
        end = time_item.get("end_date") or time_item.get("end")
        layout["predicates"].append({
            "id": predicate_id, "field": field_id, "role": role,
            "operator": "between" if start is not None and end is not None else "equals",
            "value": [start, end] if start is not None and end is not None else start or end,
        })
        coverage.append(_coverage(ref, "mapped", [field_id, predicate_id], time_item))

    for index, comparison in enumerate(confirmed_cqd.get("comparisons", []) or []):
        ref = f"/comparisons/{index}"
        role = _value(
            comparison, "dimension", "subject", "metric", "left", "description"
        ) or f"comparison {index + 1}"
        field_id = _find_related_field(role, layout["fields"]) or _ensure_field(
            layout, field_by_role, role, primary_source
        )
        predicate_id = f"predicate_comparison_{index + 1}"
        operator = comparison.get("operator") or comparison.get("direction")
        value = comparison.get("value")
        if value is None:
            value = comparison.get("threshold", comparison.get("right"))
        layout["predicates"].append({
            "id": predicate_id, "field": field_id, "role": role,
            "operator": operator, "value": copy.deepcopy(value),
        })
        coverage.append(_coverage(ref, "mapped", [field_id, predicate_id], comparison))

    for index, scope in enumerate(confirmed_cqd.get("scopes", []) or []):
        ref = f"/scopes/{index}"
        role = _semantic_value(scope) or _value(scope, "scope_entity", "entity", "name")
        related = _find_related_field(role, layout["fields"]) if role else None
        coverage.append(_coverage(
            ref, "needs_review", [related] if related else [], scope,
            clarification_family="SELECTION_SCOPE",
        ))

    for index, deferred in enumerate(confirmed_cqd.get("deferred_grounding", []) or []):
        coverage.append(_coverage(
            f"/deferred_grounding/{index}", "deferred_to_sif", [], deferred
        ))

    # Assemble WHERE once, after every predicate-producing CQD section has run.
    # The CQD predicate root owns only its registered atoms; population, time,
    # comparison and scope constraints are conjunctive restrictions around it.
    atom_where = _boolean_logic(root, atom_ids)
    extra = [
        str(item["id"]) for item in layout["predicates"]
        if str(item["id"]) not in atom_ids
    ]
    where_parts = [f"({atom_where})"] if atom_where else []
    where_parts.extend(extra)
    layout["where"] = " AND ".join(where_parts) or None

    objective = confirmed_cqd.get("objective")
    if objective not in (None, "", [], {}):
        coverage.append(_coverage("/objective", "mapped", list(layout["select"]), objective))

    for item in coverage:
        item["question_supported"] = _question_support(item.get("payload"), original_question)
    evidence_registry = _build_evidence_registry(
        coverage, original_question, confirmed_cqd
    )
    return layout, {
        "status": "needs_completion" if any(item["status"] == "needs_review" for item in coverage) else "complete",
        "original_question": original_question,
        "evidence_registry": evidence_registry,
        "requirements": coverage,
    }


def _coverage(
    ref: str, status: str, slots: list[str], payload: Any, *,
    clarification_family: str | None = None, keep_allowed: bool = False,
    slot_semantic_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "cqd_ref": ref, "status": status,
        "slot_ids": list(dict.fromkeys(slots)), "payload": copy.deepcopy(payload),
        "clarification_family": clarification_family,
        "keep_allowed": keep_allowed,
        "slot_semantic_refs": copy.deepcopy(
            slot_semantic_refs or {str(slot): ref for slot in slots}
        ),
    }


def _ensure_field(
    layout: dict[str, Any], field_by_role: dict[str, str], role: str, source: str
) -> str:
    key = _norm(role)
    existing = field_by_role.get(key)
    if existing:
        return existing
    field_id = f"field_{_slug(role)}"
    used = {str(item["id"]) for item in layout["fields"]}
    base = field_id
    counter = 2
    while field_id in used:
        field_id = f"{base}_{counter}"
        counter += 1
    layout["fields"].append({
        "id": field_id, "role": role, "source": source, "column": None,
        "expression": None, "aggregate": None, "distinct": False,
    })
    field_by_role[key] = field_id
    return field_id


def _semantic_value(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item or "")
    return _value(item, "attribute", "dimension", "target", "content", "description")


def _value(item: Any, *keys: str) -> str:
    if not isinstance(item, dict):
        return str(item or "").strip()
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _find_related_field(role: str, fields: list[dict[str, Any]]) -> str | None:
    wanted = set(_tokens(role))
    best: tuple[int, str] | None = None
    for field in fields:
        tokens = set(_tokens(str(field.get("role") or "")))
        score = len(wanted & tokens)
        if score and (best is None or score > best[0]):
            best = (score, str(field["id"]))
    return best[1] if best else None


def _find_predicate_for_role(role: str, layout: dict[str, Any]) -> list[str]:
    field_id = _find_related_field(role, layout["fields"])
    if field_id is None:
        return []
    ids = [str(item["id"]) for item in layout["predicates"] if item.get("field") == field_id]
    return [field_id, *ids] if ids else []


def _source_for(applies_to: Any, sources: list[dict[str, Any]], default: str) -> str:
    wanted = _norm(applies_to)
    if wanted:
        for source in sources:
            role = _norm(source.get("role"))
            if wanted in role or role in wanted:
                return str(source["id"])
    return default


def _boolean_logic(root: Any, ids: list[str]) -> str | None:
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    operator = _norm(root).upper()
    if operator not in {"AND", "OR"}:
        operator = "AND"
    return f" {operator} ".join(ids)


def _qualifier_already_has_atom(key: Any, value: Any, cqd: dict[str, Any]) -> bool:
    key_tokens = set(_tokens(str(key)))
    value_tokens = set(_tokens(str(value)))
    for atom in (cqd.get("predicates", {}) or {}).get("atoms", []) or []:
        dimension_tokens = set(_tokens(str(atom.get("dimension") or "")))
        atom_value_tokens = set(_tokens(str(atom.get("value") or "")))
        if key_tokens & dimension_tokens or (value_tokens and value_tokens <= atom_value_tokens):
            return True
    return False


def _tokens(value: str) -> list[str]:
    return [
        item for item in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
        if item not in {"the", "of", "a", "an", "type", "valid"}
    ]


def _slug(value: str) -> str:
    return "_".join(_tokens(value)) or "item"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _question_support(payload: Any, original_question: str) -> bool:
    payload_tokens = set(_tokens(str(payload)))
    question_tokens = set(_tokens(original_question))
    return not payload_tokens or bool(payload_tokens & question_tokens)


def _build_evidence_registry(
    coverage: list[dict[str, Any]], original_question: str, cqd: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    question_folded = original_question.casefold()
    for item in coverage:
        ref = str(item["cqd_ref"])
        cqd_id = f"cqd:{ref}"
        registry[cqd_id] = {
            "type": "confirmed_cqd", "cqd_ref": ref,
            "value": copy.deepcopy(item.get("payload")),
        }
        evidence_ids = [cqd_id]
        for span in _payload_strings(item.get("payload")):
            if len(span) < 3 or span.casefold() not in question_folded:
                continue
            evidence_id = f"question:{_slug(span)}"
            registry.setdefault(evidence_id, {
                "type": "original_question", "text": span,
            })
            evidence_ids.append(evidence_id)
        item["evidence_ids"] = list(dict.fromkeys(evidence_ids))
    traceability = cqd.get("traceability", {}) or {}
    for decision_id in traceability.get("active_decision_ids", []) or []:
        evidence_id = f"decision:{decision_id}"
        registry[evidence_id] = {
            "type": "e2_decision", "decision_id": str(decision_id),
        }
    requirements_by_ref = {str(item["cqd_ref"]): item for item in coverage}
    for decision in traceability.get("layout_decisions", []) or []:
        if not isinstance(decision, dict):
            continue
        decision_id = str(decision.get("decision_id") or "")
        cqd_ref = str(decision.get("cqd_ref") or "")
        if not decision_id or cqd_ref not in requirements_by_ref:
            continue
        evidence_id = f"decision:{decision_id}"
        registry[evidence_id] = {
            "type": "e2_decision", "decision_id": decision_id,
            "cqd_ref": cqd_ref, "value": copy.deepcopy(decision.get("evidence")),
        }
        requirements_by_ref[cqd_ref].setdefault("evidence_ids", []).append(evidence_id)
    return registry


def _payload_strings(payload: Any) -> list[str]:
    result: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            result.extend(_payload_strings(value))
    elif isinstance(payload, list):
        for value in payload:
            result.extend(_payload_strings(value))
    elif isinstance(payload, str) and payload.strip():
        result.append(payload.strip())
    return result
