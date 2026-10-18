from __future__ import annotations

import re
from typing import Any

from intent_sql_agent.services.frame_state import frame_hash
from intent_sql_agent.services.bif_slot_layout import find_semantic_slot_id


RESOLVED_GROUNDING_STATES = {"confirmed", "system_resolved"}


def build_sif_v2_scaffold(
    bif: dict[str, Any],
    *,
    dialect: str = "sqlite",
    schema_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Create one immutable physical-grounding slot per groundable BIF node."""
    frame_id = str(bif.get("frame_id") or "")
    revision = int((bif.get("lifecycle", {}) or {}).get("revision") or 1)
    db_id = str((bif.get("source", {}) or {}).get("db_id") or "")
    conditions = (bif.get("target_population", {}) or {}).get("inclusion_conditions", []) or []
    exclusions = (bif.get("target_population", {}) or {}).get("exclusion_conditions", []) or []

    scaffold: dict[str, Any] = {
        "schema_version": "2.0",
        "sql_intent_id": f"sif2_{_slug(frame_id or frame_hash(bif)[:16])}",
        "source_bif": {
            "frame_id": frame_id,
            "revision": revision,
            "frame_hash": frame_hash(bif),
        },
        "database": {
            "db_id": db_id,
            "dialect": dialect,
            "schema_fingerprint": schema_fingerprint,
        },
        "status": "needs_grounding",
        "target_population": {
            "primary_entity_grounding": {
                "grounding_id": "ground_primary_entity",
                "entity_ref": "/target_population/primary_entity",
                "source_ref": None,
                "status": "unresolved",
            },
            "inclusion_condition_groundings": [
                _condition_slot(item, inclusion=True) for item in conditions
            ],
            "exclusion_condition_groundings": [
                _condition_slot(item, inclusion=False) for item in exclusions
            ],
            "inclusion_logic_ref": "/target_population/inclusion_logic",
            "exclusion_logic_ref": "/target_population/exclusion_logic",
        },
        "metrics": {
            "metric_groundings": [
                _ref_slot("metric", str(item["metric_id"]))
                for item in bif.get("metrics", []) or []
            ]
        },
        "calculation": {
            "step_groundings": [
                _derived_ref_slot(
                    "calculation", str(item["step_id"]),
                    _calculation_implementation(item),
                )
                for item in (bif.get("calculation", {}) or {}).get("steps", []) or []
            ]
        },
        "granularity": {
            "dimension_groundings": [
                _ref_slot("granularity", f"dimension:{dimension}")
                for dimension in (bif.get("granularity", {}) or {}).get("dimensions", []) or []
            ]
        },
        "scope": {
            "selection_groundings": [
                _ref_slot("selection", str(item["selection_id"]))
                for item in (bif.get("scope", {}) or {}).get("selections", []) or []
            ]
        },
        "time_basis": {
            "period_groundings": [
                _ref_slot("period", _period_ref(item))
                for item in (bif.get("time_basis", {}) or {}).get("periods", []) or []
            ]
        },
        "comparisons": {
            "comparison_groundings": [
                _derived_ref_slot(
                    "comparison", str(item["comparison_id"]),
                    _comparison_implementation(item),
                )
                for item in bif.get("comparisons", []) or []
            ]
        },
        "output_expectation": {
            "field_groundings": [
                {
                    **_output_slot(item, bif),
                    "source_semantic_ref": str(item.get("semantic_ref") or ""),
                }
                for item in (bif.get("output_expectation", {}) or {}).get("fields", []) or []
            ],
            "ordering_groundings": [
                _ref_slot("ordering", _ordering_ref(item))
                for item in (bif.get("output_expectation", {}) or {}).get("ordering", []) or []
            ],
        },
        "semantic_slot_coverage": [],
        "physical_support": {
            "sources": [],
            "joins": [],
            "internal_expressions": [],
            "subquery_requirements": [],
        },
        "unresolved_groundings": [],
        "readiness": {"state": "needs_grounding", "blocking_refs": []},
    }
    unresolved = _unresolved_slots(scaffold)
    _attach_semantic_slot_metadata(scaffold, bif)
    _refresh_semantic_slot_coverage(scaffold, bif)
    unresolved = _unresolved_slots(scaffold)
    scaffold["unresolved_groundings"] = unresolved
    scaffold["readiness"]["blocking_refs"] = [item["semantic_ref"] for item in unresolved]
    return scaffold


def bif_v2_reference_sets(bif: dict[str, Any]) -> dict[str, set[str]]:
    population = bif.get("target_population", {}) or {}
    return {
        "inclusion": {
            str(item.get("condition_id"))
            for item in population.get("inclusion_conditions", []) or []
        },
        "exclusion": {
            str(item.get("condition_id"))
            for item in population.get("exclusion_conditions", []) or []
        },
        "metric": {str(item.get("metric_id")) for item in bif.get("metrics", []) or []},
        "calculation": {
            str(item.get("step_id"))
            for item in (bif.get("calculation", {}) or {}).get("steps", []) or []
        },
        "granularity": {
            f"dimension:{item}"
            for item in (bif.get("granularity", {}) or {}).get("dimensions", []) or []
        },
        "selection": {
            str(item.get("selection_id"))
            for item in (bif.get("scope", {}) or {}).get("selections", []) or []
        },
        "period": {
            _period_ref(item)
            for item in (bif.get("time_basis", {}) or {}).get("periods", []) or []
        },
        "comparison": {
            str(item.get("comparison_id")) for item in bif.get("comparisons", []) or []
        },
        "output": {
            str(item.get("field_id"))
            for item in (bif.get("output_expectation", {}) or {}).get("fields", []) or []
        },
        "ordering": {
            _ordering_ref(item)
            for item in (bif.get("output_expectation", {}) or {}).get("ordering", []) or []
        },
    }


def _condition_slot(condition: dict[str, Any], *, inclusion: bool) -> dict[str, Any]:
    condition_ref = str(condition["condition_id"])
    prefix = "inc" if inclusion else "exc"
    return {
        "grounding_id": f"ground_{prefix}_{_slug(condition_ref)}",
        "condition_ref": condition_ref,
        "binding_mode": _default_binding_mode(condition),
        "binding_composition": "all_of",
        "bindings": [],
        "status": "unresolved",
    }


def _ref_slot(kind: str, semantic_ref: str) -> dict[str, Any]:
    return {
        "grounding_id": f"ground_{kind}_{_slug(semantic_ref)}",
        "semantic_ref": semantic_ref,
        "binding_mode": "direct",
        "bindings": [],
        "implementation": None,
        "status": "unresolved",
    }


def _derived_ref_slot(
    kind: str, semantic_ref: str, implementation: dict[str, Any]
) -> dict[str, Any]:
    slot = _ref_slot(kind, semantic_ref)
    slot["binding_mode"] = (
        "aggregation" if implementation["kind"] == "aggregate"
        else "window" if implementation["kind"] == "window"
        else "set_operation" if implementation["kind"] == "set_operation"
        else "derived_expression"
    )
    slot["implementation"] = implementation
    slot["status"] = "system_resolved"
    return slot


def _output_slot(field: dict[str, Any], bif: dict[str, Any]) -> dict[str, Any]:
    slot = _ref_slot("output", str(field["field_id"]))
    semantic_ref = str(field.get("semantic_ref") or "")
    derived_refs = {
        str(item.get("output_ref") or "")
        for item in (bif.get("calculation", {}) or {}).get("steps", []) or []
    } | {
        str(item.get("output_ref") or "") for item in bif.get("comparisons", []) or []
    }
    if semantic_ref in derived_refs:
        slot["binding_mode"] = "derived_expression"
        slot["implementation"] = {
            "kind": "reference", "operator": "reference",
            "input_refs": [semantic_ref], "distinct": False,
        }
        slot["status"] = "system_resolved"
    return slot


def _calculation_implementation(step: dict[str, Any]) -> dict[str, Any]:
    operation = str(step.get("operation") or "custom")
    method = str(step.get("method") or step.get("description") or operation)
    return {
        "kind": (
            "aggregate" if operation == "aggregate"
            else "window" if operation == "rank"
            else "set_operation" if operation == "set_combine"
            else "existence" if operation == "existence"
            else "reference" if operation in {"retrieve", "filter", "select"}
            else "binary" if operation in {"derive", "compare"}
            else "custom"
        ),
        "operator": _semantic_operator(method, operation),
        "input_refs": [str(item) for item in step.get("input_refs", []) or []],
        "distinct": "distinct" in method.casefold(),
    }


def _comparison_implementation(comparison: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "binary",
        "operator": str(comparison.get("type") or "custom"),
        "input_refs": [str(comparison.get("left")), str(comparison.get("right"))],
        "distinct": False,
    }


def _semantic_operator(method: str, fallback: str) -> str:
    value = method.casefold()
    for terms, operator in (
        (("average", "avg", "mean"), "avg"),
        (("sum", "total"), "sum"),
        (("count",), "count"),
        (("minimum", "min", "least"), "min"),
        (("maximum", "max", "greatest"), "max"),
        (("percentage change", "percent change"), "percentage_change"),
        (("absolute difference",), "absolute_difference"),
        (("difference", "subtract"), "difference"),
        (("ratio", "divide"), "ratio"),
        (("multiply", "product"), "multiply"),
        (("row number",), "row_number"),
        (("dense rank",), "dense_rank"),
        (("rank",), "rank"),
    ):
        if any(term in value for term in terms):
            return operator
    return fallback


def _default_binding_mode(condition: dict[str, Any]) -> str:
    if condition.get("operator") == "between" or condition.get("value_mode") == "range":
        return "range"
    return "direct"


def _period_ref(period: dict[str, Any]) -> str:
    return "period:" + "|".join(
        str(period.get(key) if period.get(key) is not None else "")
        for key in ("start", "end", "grain")
    )


def _ordering_ref(ordering: dict[str, Any]) -> str:
    return f"ordering:{ordering.get('semantic_ref')}:{ordering.get('direction')}"


def _unresolved_slots(sif: dict[str, Any]) -> list[dict[str, str]]:
    slots: list[tuple[str, str]] = [
        ("ground_primary_entity", "/target_population/primary_entity")
    ]
    population = sif["target_population"]
    for key in ("inclusion_condition_groundings", "exclusion_condition_groundings"):
        slots.extend(
            (item["grounding_id"], item["condition_ref"])
            for item in population[key]
        )
    for section, key in (
        ("metrics", "metric_groundings"),
        ("calculation", "step_groundings"),
        ("granularity", "dimension_groundings"),
        ("scope", "selection_groundings"),
        ("time_basis", "period_groundings"),
        ("comparisons", "comparison_groundings"),
    ):
        slots.extend(
            (item["grounding_id"], item["semantic_ref"])
            for item in sif[section][key]
        )
    slots.extend(
        (item["grounding_id"], item["semantic_ref"])
        for key in ("field_groundings", "ordering_groundings")
        for item in sif["output_expectation"][key]
    )
    grounding_by_id = {
        str(item.get("grounding_id")): item
        for item in _iter_scaffold_slots(sif)
    }
    return [
        {
            "grounding_id": grounding_id,
            "semantic_ref": semantic_ref,
            "reason": "physical binding is not resolved",
            "owner": "system",
        }
        for grounding_id, semantic_ref in slots
        if grounding_by_id.get(grounding_id, {}).get("status") not in RESOLVED_GROUNDING_STATES
    ]


def _iter_scaffold_slots(sif: dict[str, Any]) -> list[dict[str, Any]]:
    population = sif["target_population"]
    result = [population["primary_entity_grounding"]]
    result.extend(population["inclusion_condition_groundings"])
    result.extend(population["exclusion_condition_groundings"])
    for section, key in (
        ("metrics", "metric_groundings"), ("calculation", "step_groundings"),
        ("granularity", "dimension_groundings"), ("scope", "selection_groundings"),
        ("time_basis", "period_groundings"), ("comparisons", "comparison_groundings"),
    ):
        result.extend(sif[section][key])
    result.extend(sif["output_expectation"]["field_groundings"])
    result.extend(sif["output_expectation"]["ordering_groundings"])
    return result


def _attach_semantic_slot_metadata(sif: dict[str, Any], bif: dict[str, Any]) -> None:
    """Keep the BIF semantic slot identity through every physical grounding."""
    for slot in _iter_scaffold_slots(sif):
        legacy_ref = str(
            slot.get("entity_ref") or slot.get("condition_ref")
            or slot.get("semantic_ref") or ""
        )
        semantic_slot_id = find_semantic_slot_id(bif, legacy_ref)
        slot["semantic_slot_id"] = semantic_slot_id or f"legacy:{legacy_ref}"
        semantic_slot = _semantic_slot(bif, semantic_slot_id)
        resolved = slot.get("status") in RESOLVED_GROUNDING_STATES
        evidence = [
            f"{item.get('source_ref')}.{item.get('column')}"
            for item in slot.get("bindings", []) or []
            if item.get("source_ref") and item.get("column")
        ]
        if resolved and not evidence and slot.get("implementation"):
            implementation = slot.get("implementation") or {}
            evidence = [
                f"implementation:{implementation.get('kind')}:{implementation.get('operator')}"
            ]
        slot["constraint_coverage"] = [
            {
                "constraint_id": str(item["constraint_id"]),
                "status": "grounded" if resolved else "pending",
                "evidence": evidence,
                "reason": None if resolved else "physical grounding is unresolved",
            }
            for item in (semantic_slot or {}).get("constraints", []) or []
        ]


def _semantic_slot(bif: dict[str, Any], slot_id: str | None) -> dict[str, Any] | None:
    if not slot_id:
        return None
    return next(
        (
            item for item in (bif.get("slot_layout", {}) or {}).get("slots", []) or []
            if str(item.get("slot_id")) == str(slot_id)
        ),
        None,
    )


def _refresh_semantic_slot_coverage(sif: dict[str, Any], bif: dict[str, Any]) -> None:
    groundings: dict[str, list[dict[str, Any]]] = {}
    for item in _iter_scaffold_slots(sif):
        groundings.setdefault(str(item.get("semantic_slot_id")), []).append(item)
    coverage: list[dict[str, Any]] = []
    for semantic_slot in (bif.get("slot_layout", {}) or {}).get("slots", []) or []:
        slot_id = str(semantic_slot.get("slot_id"))
        linked = groundings.get(slot_id, [])
        constraint_results = [
            result for item in linked
            for result in item.get("constraint_coverage", []) or []
        ]
        constraint_states = {str(result.get("status")) for result in constraint_results}
        expected_constraint_ids = {
            str(item.get("constraint_id"))
            for item in semantic_slot.get("constraints", []) or []
        }
        grounded_constraint_ids = {
            str(result.get("constraint_id")) for result in constraint_results
            if result.get("status") == "grounded"
        }
        resolved = bool(linked) and all(
            item.get("status") in RESOLVED_GROUNDING_STATES for item in linked
        ) and expected_constraint_ids <= grounded_constraint_ids and constraint_states <= {
            "grounded", "not_applicable"
        }
        if linked:
            status = "unsupported" if "unsupported" in constraint_states else (
                "grounded" if resolved else "pending"
            )
            reason = None if resolved else (
                "one or more constraints are unsupported" if status == "unsupported"
                else "linked physical grounding or constraint is unresolved"
            )
        elif semantic_slot.get("role") in {"objective", "limit", "logic"}:
            status = "not_applicable"
            reason = "logical-only slot does not require a physical binding"
        else:
            status = "unsupported"
            reason = "no SIF grounding slot represents this semantic slot"
        coverage.append({
            "semantic_slot_id": slot_id,
            "grounding_ids": [str(item.get("grounding_id")) for item in linked],
            "status": status,
            "reason": reason,
        })
    sif["semantic_slot_coverage"] = coverage


def apply_semantic_slot_readiness(sif: dict[str, Any]) -> None:
    """Prevent physical completion from hiding unrepresented semantic slots."""
    if sif.get("unresolved_groundings"):
        return
    unsupported = [
        str(item.get("semantic_slot_id"))
        for item in sif.get("semantic_slot_coverage", []) or []
        if item.get("status") == "unsupported"
    ]
    pending = [
        str(item.get("semantic_slot_id"))
        for item in sif.get("semantic_slot_coverage", []) or []
        if item.get("status") == "pending"
    ]
    if unsupported:
        sif["status"] = "blocked"
        sif["readiness"] = {"state": "blocked", "blocking_refs": unsupported}
    elif pending:
        sif["status"] = "needs_grounding"
        sif["readiness"] = {"state": "needs_grounding", "blocking_refs": pending}


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return result or "node"
