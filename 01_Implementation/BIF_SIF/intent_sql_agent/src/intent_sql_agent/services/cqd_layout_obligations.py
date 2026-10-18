from __future__ import annotations

import copy
from typing import Any


def canonicalize_layout_control_fields(
    case: dict[str, Any], cqd: dict[str, Any], layout: dict[str, Any]
) -> dict[str, Any]:
    """Take IDs/provenance coverage away from the model without changing semantics."""
    result = copy.deepcopy(layout)
    state_hash = (cqd.get("traceability", {}) or {}).get("state_hash")
    result["layout_id"] = f"query-layout-{case['question_id']}-{str(state_hash or 'none')[:12]}"
    result["phase"] = "bif"
    result["source"] = {
        "question_id": case["question_id"],
        "db_id": case["db_id"],
        "question": case["question"],
        "confirmed_cqd_hash": state_hash,
    }
    active_decisions = {
        str(item)
        for item in (cqd.get("traceability", {}) or {}).get("active_decision_ids", []) or []
    }
    allowed_refs = _cqd_refs(cqd)
    query = result.get("query", {}) or {}
    for clause_name in ("where", "having"):
        clause = query.get(clause_name, {}) or {}
        predicates = clause.get("predicates", []) or []
        if len(predicates) == 1 and clause.get("logic") is None:
            clause["logic"] = {
                "operator": "none",
                "node_refs": [str(predicates[0]["node_id"])],
                "groups": [],
            }
    coverage: dict[str, list[str]] = {}
    for node in _layout_nodes(result):
        node["decision_refs"] = [
            str(item) for item in node.get("decision_refs", []) or []
            if str(item) in active_decisions
        ]
        for ref in node.get("source_refs", []) or []:
            ref = str(ref)
            if ref not in allowed_refs:
                raise ValueError(f"QUERY_LAYOUT_UNKNOWN_CQD_SOURCE_REF: {ref}")
            coverage.setdefault(ref, []).append(str(node["node_id"]))
    result["coverage"] = [
        {
            "requirement_ref": ref,
            "node_ids": list(dict.fromkeys(node_ids)),
            "status": "represented",
        }
        for ref, node_ids in sorted(coverage.items())
    ]
    result["readiness"] = {
        "state": "logical_ready", "blocking_node_ids": [], "reason": None
    }
    return result


def compile_layout_obligations(cqd: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive SQL-logical completeness requirements without database knowledge."""
    obligations: list[dict[str, Any]] = []
    for index, aggregation in enumerate(cqd.get("aggregations", []) or []):
        kind = _norm(aggregation.get("type"))
        target = str(aggregation.get("target") or "").strip()
        ref = f"/aggregations/{index}"
        if kind == "mode":
            obligations.extend([
                _obligation(f"mode_{index}_projection", "select", target, ref),
                _obligation(f"mode_{index}_grouping", "group_by", target, ref),
                {
                    "obligation_id": f"mode_{index}_frequency_order",
                    "role": "order_by_frequency_desc",
                    "target": target,
                    "source_ref": ref,
                },
            ])
            if _norm(aggregation.get("tie_policy")) == "single":
                obligations.append({
                    "obligation_id": f"mode_{index}_single_result",
                    "role": "limit",
                    "value": 1,
                    "source_ref": ref,
                })
        elif kind:
            obligations.append({
                "obligation_id": f"aggregation_{index}",
                "role": "aggregate",
                "operator": kind,
                "target": target,
                "source_ref": ref,
            })
    for index, selection in enumerate(cqd.get("selections", []) or []):
        obligations.append(_obligation(
            f"selection_{index}", "select", _semantic_value(selection),
            f"/selections/{index}",
        ))
    for index, atom in enumerate((cqd.get("predicates", {}) or {}).get("atoms", []) or []):
        obligations.append({
            "obligation_id": f"predicate_{index}",
            "role": "where_predicate",
            "target": str(atom.get("dimension") or ""),
            "operator": str(atom.get("operator") or ""),
            "value": atom.get("value"),
            "source_ref": f"/predicates/atoms/{index}",
        })
    return obligations


def validate_layout_obligations(
    cqd: dict[str, Any], layout: dict[str, Any]
) -> dict[str, Any]:
    obligations = compile_layout_obligations(cqd)
    query = layout.get("query", {}) or {}
    missing: list[dict[str, Any]] = []
    for obligation in obligations:
        role = obligation["role"]
        source_ref = str(obligation.get("source_ref") or "")
        if role == "select" and not _has_source_ref(query.get("select", []), source_ref):
            missing.append(obligation)
        elif role == "group_by" and not _has_source_ref(query.get("group_by", []), source_ref):
            missing.append(obligation)
        elif role == "order_by_frequency_desc" and not _has_descending_order(
            query.get("order_by", []), source_ref
        ):
            missing.append(obligation)
        elif role == "limit":
            limit = query.get("limit") or {}
            constraints = limit.get("constraints", []) or []
            values = [item.get("value") for item in constraints]
            if (
                source_ref not in (limit.get("source_refs", []) or [])
                or obligation.get("value") not in values
            ):
                missing.append(obligation)
        elif role == "aggregate" and not _has_aggregate(
            query.get("select", []), source_ref
        ):
            missing.append(obligation)
        elif role == "where_predicate" and not _has_predicate(
            (query.get("where", {}) or {}).get("predicates", []), source_ref,
            obligation.get("operator"), obligation.get("value")
        ):
            missing.append(obligation)
    return {
        "status": "complete" if not missing else "incomplete",
        "expected": len(obligations),
        "satisfied": len(obligations) - len(missing),
        "obligations": obligations,
        "missing": missing,
    }


def _obligation(
    obligation_id: str, role: str, target: str, source_ref: str
) -> dict[str, Any]:
    return {
        "obligation_id": obligation_id,
        "role": role,
        "target": target,
        "source_ref": source_ref,
    }


def _semantic_value(item: dict[str, Any]) -> str:
    for key in ("attribute", "dimension", "target", "content", "description"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _has_source_ref(nodes: list[dict[str, Any]], source_ref: str) -> bool:
    return any(source_ref in (node.get("source_refs", []) or []) for node in nodes or [])


def _has_descending_order(nodes: list[dict[str, Any]], source_ref: str) -> bool:
    for node in nodes or []:
        if source_ref not in (node.get("source_refs", []) or []):
            continue
        constraints = node.get("constraints", []) or []
        if any(
            _norm(item.get("value")) in {"desc", "descending"}
            for item in constraints
        ):
            return True
    return False


def _has_aggregate(
    nodes: list[dict[str, Any]], source_ref: str
) -> bool:
    return any(
        node.get("node_kind") == "aggregate"
        and source_ref in (node.get("source_refs", []) or [])
        for node in nodes or []
    )


def _has_predicate(
    nodes: list[dict[str, Any]], source_ref: str, operator: Any, value: Any
) -> bool:
    for node in nodes or []:
        if source_ref not in (node.get("source_refs", []) or []):
            continue
        constraints = node.get("constraints", []) or []
        operator_values = [
            item.get("value") for item in constraints if item.get("type") == "operator"
        ]
        semantic_values = [
            item.get("value") for item in constraints
            if item.get("type") in {"value", "range", "membership"}
        ]
        if operator is not None and not any(
            _norm(item) == _norm(operator) for item in operator_values
        ):
            continue
        if value is not None and not any(item == value for item in semantic_values):
            continue
        return True
    return False


def _norm(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_norm(item) for item in value)
    return " ".join(str(value or "").strip().lower().split())


def _cqd_refs(cqd: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    collections = (
        "populations", "expressions", "aggregations", "groupings", "selections",
        "time_constraints", "comparisons", "outputs", "scopes", "deferred_grounding",
    )
    for key in collections:
        refs.update(f"/{key}/{index}" for index, _ in enumerate(cqd.get(key, []) or []))
    refs.update(
        f"/predicates/atoms/{index}"
        for index, _ in enumerate((cqd.get("predicates", {}) or {}).get("atoms", []) or [])
    )
    return refs


def _layout_nodes(layout: dict[str, Any]) -> list[dict[str, Any]]:
    query = layout.get("query", {}) or {}
    nodes: list[dict[str, Any]] = []
    for key in ("sources", "joins", "select", "group_by", "order_by", "set_operations"):
        nodes.extend(query.get(key, []) or [])
    nodes.extend((query.get("where", {}) or {}).get("predicates", []) or [])
    nodes.extend((query.get("having", {}) or {}).get("predicates", []) or [])
    if query.get("limit") is not None:
        nodes.append(query["limit"])
    return nodes
