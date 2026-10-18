from __future__ import annotations

from typing import Any

from intent_sql_agent.services.sif_v2_validator import validate_sif_v2_invariants


class SIFV2LoweringError(ValueError):
    pass


def lower_sif_v2_to_query_plan(
    bif: dict[str, Any], sif: dict[str, Any]
) -> dict[str, Any]:
    """Lower BIF logic plus SIF bindings into the legacy Query Plan IR.

    The BIF owns condition topology.  SIF condition slots only supply leaf
    expressions, so independent conditions cannot be merged by this component.
    """
    findings = validate_sif_v2_invariants(bif, sif)
    if findings:
        raise SIFV2LoweringError(f"SIF 2.0 invariant failure: {findings[:5]}")
    if sif.get("status") != "ready" or (sif.get("readiness", {}) or {}).get("state") != "ready":
        raise SIFV2LoweringError("SIF 2.0 is not ready")
    unsupported = validate_sif_v2_lowering_support(bif, sif)
    if unsupported:
        raise SIFV2LoweringError(
            "SIF 2.0 deterministic lowering does not support this frame: "
            + "; ".join(unsupported)
        )

    support = sif["physical_support"]
    qualifiers = {
        str(item["source_id"]): str(item.get("alias") or item["source_id"])
        for item in support.get("sources", []) or []
    }
    sources = [_lower_source(item) for item in support.get("sources", []) or []]
    joins = [_lower_join(item, qualifiers) for item in support.get("joins", []) or []]
    population = bif.get("target_population", {}) or {}
    grounded_population = sif.get("target_population", {}) or {}
    inclusion = _condition_expressions(
        population.get("inclusion_conditions", []) or [],
        grounded_population.get("inclusion_condition_groundings", []) or [],
        prefix="/target_population/inclusion_conditions",
        qualifiers=qualifiers,
    )
    exclusion = _condition_expressions(
        population.get("exclusion_conditions", []) or [],
        grounded_population.get("exclusion_condition_groundings", []) or [],
        prefix="/target_population/exclusion_conditions",
        qualifiers=qualifiers,
    )
    where: list[dict[str, Any]] = []
    inclusion_tree = _logic_expression(population.get("inclusion_logic", {}), inclusion)
    if inclusion_tree is not None:
        where.append(inclusion_tree)
    exclusion_tree = _logic_expression(population.get("exclusion_logic", {}), exclusion)
    if exclusion_tree is not None:
        where.append(exclusion_tree)

    semantic_expressions = _semantic_expressions(bif, sif, qualifiers)
    projection = _output_projection(bif, sif, qualifiers, semantic_expressions)
    group_by = _dimension_expressions(sif, qualifiers)
    order_by = _ordering_expressions(bif, sif, qualifiers)
    output = bif.get("output_expectation", {}) or {}
    limit_count = output.get("limit")
    block = {
        "block_id": "main",
        "role": "main",
        "sources": sources,
        "joins": joins,
        "where": where,
        "derived_fields": [],
        "group_by": group_by,
        "having": [],
        "windows": [],
        "projection": projection,
        "distinct": _requires_distinct(bif),
        "order_by": order_by,
        "limit": (
            {"count": int(limit_count), "offset": 0, "with_ties": False, "intent_paths": ["/output_expectation/limit"]}
            if limit_count is not None else None
        ),
        "set_operations": [],
        "intent_paths": sorted(_all_origin_refs(support)),
    }
    return {
        "schema_version": "1.0-query-plan",
        "sql_intent_id": str(sif.get("sql_intent_id")),
        "source_frame_id": str((sif.get("source_bif", {}) or {}).get("frame_id")),
        "database": sif.get("database"),
        "status": "ready",
        "query_blocks": [block],
        "main_block_id": "main",
        "traceability": _traceability(block),
        "unresolved_grounding": [],
    }


def validate_sif_v2_lowering_support(
    bif: dict[str, Any], sif: dict[str, Any]
) -> list[str]:
    """Reject semantic features that the lowerer cannot yet preserve exactly.

    This capability gate is intentionally fail-closed.  A model fallback must
    never silently reinterpret a Confirmed BIF merely because lowering is
    incomplete.
    """
    unsupported: list[str] = []
    if (bif.get("scope", {}) or {}).get("selections", []) or []:
        unsupported.append("scope.selections")
    if (bif.get("time_basis", {}) or {}).get("periods", []) or []:
        unsupported.append("time_basis.periods")
    support = sif.get("physical_support", {}) or {}
    if support.get("internal_expressions", []) or []:
        unsupported.append("physical_support.internal_expressions")
    if support.get("subquery_requirements", []) or []:
        unsupported.append("physical_support.subquery_requirements")
    for source in support.get("sources", []) or []:
        if source.get("kind") not in {"table", "view"}:
            unsupported.append(f"source_kind:{source.get('kind')}")
    allowed = {"direct", "code_mapping", "range"}
    for slot in _all_grounding_slots(sif):
        implementation = slot.get("implementation")
        if implementation:
            kind = str(implementation.get("kind") or "")
            operator = str(implementation.get("operator") or "")
            supported = (
                kind == "reference"
                or kind == "aggregate" and operator in {"avg", "sum", "count", "min", "max"}
                or kind == "binary" and operator in {
                    "difference", "absolute_difference", "ratio", "percentage",
                    "percentage_change", "multiply", "add", "equality",
                }
                or kind == "unary" and operator in {"absolute", "negate"}
            )
            if not supported:
                unsupported.append(
                    f"implementation:{slot.get('grounding_id')}:{kind}:{operator}"
                )
            continue
        mode = str(slot.get("binding_mode") or "direct")
        if mode not in allowed:
            unsupported.append(
                f"grounding:{slot.get('grounding_id')}:{mode}"
            )
    return sorted(set(unsupported))


def _all_grounding_slots(sif: dict[str, Any]) -> list[dict[str, Any]]:
    population = sif.get("target_population", {}) or {}
    result = [
        *(population.get("inclusion_condition_groundings", []) or []),
        *(population.get("exclusion_condition_groundings", []) or []),
    ]
    for section, key in (
        ("metrics", "metric_groundings"),
        ("calculation", "step_groundings"),
        ("granularity", "dimension_groundings"),
        ("scope", "selection_groundings"),
        ("time_basis", "period_groundings"),
        ("comparisons", "comparison_groundings"),
    ):
        result.extend((sif.get(section, {}) or {}).get(key, []) or [])
    output = sif.get("output_expectation", {}) or {}
    result.extend(output.get("field_groundings", []) or [])
    result.extend(output.get("ordering_groundings", []) or [])
    return result


def _condition_expressions(
    conditions: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    *,
    prefix: str,
    qualifiers: dict[str, str],
) -> dict[str, dict[str, Any]]:
    by_ref = {str(item["condition_ref"]): item for item in slots}
    result: dict[str, dict[str, Any]] = {}
    for condition in conditions:
        condition_ref = str(condition["condition_id"])
        slot = by_ref[condition_ref]
        path = f"{prefix}/{condition_ref}"
        leaves = [
            _binding_predicate(item, condition, path, qualifiers)
            for item in slot["bindings"]
        ]
        if not leaves:
            raise SIFV2LoweringError(f"condition has no bindings: {condition_ref}")
        result[condition_ref] = _combine(
            leaves, "and" if slot.get("binding_composition") == "all_of" else "or", [path]
        )
    return result


def _binding_predicate(
    binding: dict[str, Any], condition: dict[str, Any], path: str,
    qualifiers: dict[str, str],
) -> dict[str, Any]:
    operator = str(binding.get("sql_operator") or condition.get("operator") or "equals")
    return {
        "kind": "binary",
        "operator": operator,
        "left": {
            "kind": "column", "source": qualifiers[str(binding["source_ref"])],
            "name": binding["column"], "intent_paths": [path],
        },
        "right": {
            "kind": "literal", "value": binding.get("physical_value"),
            "data_type": binding.get("data_type"), "intent_paths": [path],
        },
        "intent_paths": [path],
    }


def _logic_expression(
    logic: dict[str, Any], expressions: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    members = [expressions[str(ref)] for ref in logic.get("condition_refs", []) or []]
    members.extend(
        item for group in logic.get("groups", []) or []
        if (item := _logic_expression(group, expressions)) is not None
    )
    if not members:
        return None
    operator = str(logic.get("operator") or "all_of")
    if operator == "all_of":
        return _combine(members, "and", sorted(_paths(members)))
    if operator == "any_of":
        return _combine(members, "or", sorted(_paths(members)))
    if operator == "none_of":
        operand = _combine(members, "or", sorted(_paths(members)))
        return {"kind": "unary", "operator": "not", "operand": operand, "intent_paths": sorted(_paths(members))}
    raise SIFV2LoweringError(f"unsupported BIF logic operator: {operator}")


def _combine(
    expressions: list[dict[str, Any]], operator: str, intent_paths: list[str]
) -> dict[str, Any]:
    current = expressions[0]
    for expression in expressions[1:]:
        current = {
            "kind": "binary", "operator": operator,
            "left": current, "right": expression,
            "intent_paths": intent_paths,
        }
    return current


def _output_projection(
    bif: dict[str, Any], sif: dict[str, Any], qualifiers: dict[str, str],
    semantic_expressions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = {
        str(item["field_id"]): item
        for item in (bif.get("output_expectation", {}) or {}).get("fields", []) or []
    }
    projections: list[dict[str, Any]] = []
    for slot in sif["output_expectation"]["field_groundings"]:
        field_ref = str(slot["semantic_ref"])
        field = fields[field_ref]
        bindings = slot.get("bindings", []) or []
        if not bindings and slot.get("implementation"):
            semantic_ref = str(slot.get("source_semantic_ref") or "")
            expression = semantic_expressions.get(semantic_ref)
            if expression is None:
                raise SIFV2LoweringError(
                    f"output semantic expression is unresolved: {semantic_ref}"
                )
            path = f"/output_expectation/fields/{field_ref}"
            projections.append({
                "output_id": f"{field_ref}_1",
                "label": str(field.get("label") or field_ref),
                "expression": _with_paths(expression, [path]),
                "intent_paths": [path],
            })
            continue
        for index, binding in enumerate(bindings, start=1):
            path = f"/output_expectation/fields/{field_ref}"
            label = str(field.get("label") or field_ref)
            if len(bindings) > 1:
                label = f"{label} {index}"
            projections.append({
                "output_id": f"{field_ref}_{index}",
                "label": label,
                "expression": {
                    "kind": "column", "source": qualifiers[str(binding["source_ref"])],
                    "name": binding["column"], "intent_paths": [path],
                },
                "intent_paths": [path],
            })
    if not projections:
        raise SIFV2LoweringError("SIF 2.0 has no output grounding")
    return projections


def _semantic_expressions(
    bif: dict[str, Any], sif: dict[str, Any], qualifiers: dict[str, str]
) -> dict[str, dict[str, Any]]:
    expressions: dict[str, dict[str, Any]] = {}
    metric_slots = {
        str(item["semantic_ref"]): item
        for item in sif["metrics"]["metric_groundings"]
    }
    for metric in bif.get("metrics", []) or []:
        ref = str(metric["metric_id"])
        slot = metric_slots[ref]
        bindings = slot.get("bindings", []) or []
        if len(bindings) != 1:
            # Multiple direct attributes are emitted through their output slot;
            # they are not a single operand for arithmetic.
            continue
        expression = _binding_column(bindings[0], qualifiers, f"/metrics/{ref}")
        if str(metric.get("measure_kind")) == "count":
            expression = {
                "kind": "function", "name": "count", "args": [expression],
                "distinct": bool((bif.get("calculation", {}) or {}).get("duplicate_handling") == "distinct"),
                "intent_paths": [f"/metrics/{ref}"],
            }
        expressions[ref] = expression

    calculation_slots = {
        str(item["semantic_ref"]): item
        for item in sif["calculation"]["step_groundings"]
    }
    for step in (bif.get("calculation", {}) or {}).get("steps", []) or []:
        ref = str(step["step_id"])
        expression = _implementation_expression(
            calculation_slots[ref]["implementation"], expressions,
            path=f"/calculation/steps/{ref}",
        )
        expressions[ref] = expression
        expressions[str(step.get("output_ref") or ref)] = expression

    comparison_slots = {
        str(item["semantic_ref"]): item
        for item in sif["comparisons"]["comparison_groundings"]
    }
    for comparison in bif.get("comparisons", []) or []:
        ref = str(comparison["comparison_id"])
        expression = _implementation_expression(
            comparison_slots[ref]["implementation"], expressions,
            path=f"/comparisons/{ref}",
        )
        expressions[ref] = expression
        expressions[str(comparison.get("output_ref") or ref)] = expression
    return expressions


def _implementation_expression(
    implementation: dict[str, Any],
    expressions: dict[str, dict[str, Any]],
    *, path: str,
) -> dict[str, Any]:
    refs = [str(item) for item in implementation.get("input_refs", []) or []]
    try:
        inputs = [expressions[item] for item in refs]
    except KeyError as exc:
        raise SIFV2LoweringError(
            f"semantic input is unresolved for {path}: {exc.args[0]}"
        ) from exc
    kind = str(implementation.get("kind"))
    operator = str(implementation.get("operator"))
    paths = [path]
    if kind == "reference":
        if len(inputs) != 1:
            raise SIFV2LoweringError(f"reference requires one input: {path}")
        return _with_paths(inputs[0], paths)
    if kind == "aggregate":
        if len(inputs) != 1:
            raise SIFV2LoweringError(f"aggregate requires one input: {path}")
        return {
            "kind": "function", "name": operator, "args": [inputs[0]],
            "distinct": bool(implementation.get("distinct")),
            "intent_paths": paths,
        }
    if kind == "unary":
        if len(inputs) != 1:
            raise SIFV2LoweringError(f"unary expression requires one input: {path}")
        if operator == "absolute":
            return {"kind": "function", "name": "abs", "args": inputs,
                    "distinct": False, "intent_paths": paths}
        return {"kind": "unary", "operator": "-", "operand": inputs[0],
                "intent_paths": paths}
    if kind == "binary":
        if len(inputs) != 2:
            raise SIFV2LoweringError(f"binary expression requires two inputs: {path}")
        left, right = inputs
        if operator == "absolute_difference":
            difference = {"kind": "binary", "operator": "-", "left": left,
                          "right": right, "intent_paths": paths}
            return {"kind": "function", "name": "abs", "args": [difference],
                    "distinct": False, "intent_paths": paths}
        if operator == "percentage_change":
            difference = {"kind": "binary", "operator": "-", "left": left,
                          "right": right, "intent_paths": paths}
            numerator = {"kind": "binary", "operator": "*", "left": difference,
                         "right": {"kind": "literal", "value": 100.0,
                                   "intent_paths": paths}, "intent_paths": paths}
            return {"kind": "binary", "operator": "/", "left": numerator,
                     "right": right, "intent_paths": paths}
        if operator == "percentage":
            numerator = {"kind": "binary", "operator": "*", "left": left,
                         "right": {"kind": "literal", "value": 100.0,
                                   "intent_paths": paths}, "intent_paths": paths}
            return {"kind": "binary", "operator": "/", "left": numerator,
                    "right": right, "intent_paths": paths}
        if operator == "ratio":
            numerator = {"kind": "binary", "operator": "*", "left": left,
                         "right": {"kind": "literal", "value": 1.0,
                                   "intent_paths": paths}, "intent_paths": paths}
            return {"kind": "binary", "operator": "/", "left": numerator,
                    "right": right, "intent_paths": paths}
        mapped = {
            "difference": "-",
            "multiply": "*", "add": "+", "equality": "equals",
        }.get(operator)
        if mapped is None:
            raise SIFV2LoweringError(f"unsupported binary operator: {operator}")
        return {"kind": "binary", "operator": mapped, "left": left,
                "right": right, "intent_paths": paths}
    raise SIFV2LoweringError(f"unsupported implementation kind: {kind}")


def _binding_column(
    binding: dict[str, Any], qualifiers: dict[str, str], path: str
) -> dict[str, Any]:
    return {
        "kind": "column", "source": qualifiers[str(binding["source_ref"])],
        "name": binding["column"], "intent_paths": [path],
    }


def _with_paths(expression: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    result = dict(expression)
    result["intent_paths"] = sorted(set(result.get("intent_paths", []) or []) | set(paths))
    return result


def _dimension_expressions(
    sif: dict[str, Any], qualifiers: dict[str, str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for slot in sif["granularity"]["dimension_groundings"]:
        path = f"/granularity/dimensions/{slot['semantic_ref']}"
        result.extend({
            "kind": "column", "source": qualifiers[str(item["source_ref"])],
            "name": item["column"], "intent_paths": [path],
        } for item in slot.get("bindings", []) or [])
    return result


def _ordering_expressions(
    bif: dict[str, Any], sif: dict[str, Any], qualifiers: dict[str, str]
) -> list[dict[str, Any]]:
    directions = {
        f"ordering:{item.get('semantic_ref')}:{item.get('direction')}": item.get("direction")
        for item in (bif.get("output_expectation", {}) or {}).get("ordering", []) or []
    }
    result: list[dict[str, Any]] = []
    for slot in sif["output_expectation"]["ordering_groundings"]:
        ref = str(slot["semantic_ref"])
        for binding in slot.get("bindings", []) or []:
            result.append({
                "expression": {
                    "kind": "column", "source": qualifiers[str(binding["source_ref"])],
                    "name": binding["column"], "intent_paths": [ref],
                },
                "direction": "desc" if directions[ref] == "descending" else "asc",
                "nulls": None,
            })
    return result


def _lower_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source["source_id"], "kind": "table" if source["kind"] == "table" else source["kind"],
        "name": source["name"], "block_ref": None, "alias": source.get("alias"),
        "intent_paths": list(source.get("origin_refs", [])),
    }


def _lower_join(join: dict[str, Any], qualifiers: dict[str, str]) -> dict[str, Any]:
    paths = list(join.get("origin_refs", []))
    return {
        "join_id": join["join_id"], "type": join["join_type"],
        "left_source": join["left_source"], "right_source": join["right_source"],
        "predicates": [{
            "kind": "binary", "operator": "equals",
            "left": {"kind": "column", "source": qualifiers[str(join["left_source"])], "name": pair["left_column"], "intent_paths": paths},
            "right": {"kind": "column", "source": qualifiers[str(join["right_source"])], "name": pair["right_column"], "intent_paths": paths},
            "intent_paths": paths,
        } for pair in join.get("key_pairs", []) or []],
        "intent_paths": paths,
    }


def _requires_distinct(bif: dict[str, Any]) -> bool:
    policy = str((bif.get("calculation", {}) or {}).get("duplicate_handling") or "").casefold()
    return policy in {"distinct", "remove_duplicates", "deduplicate"}


def _paths(values: list[dict[str, Any]]) -> set[str]:
    return {str(path) for value in values for path in value.get("intent_paths", []) or []}


def _all_origin_refs(support: dict[str, Any]) -> set[str]:
    return {
        str(ref)
        for key in ("sources", "joins", "internal_expressions", "subquery_requirements")
        for item in support.get(key, []) or []
        for ref in item.get("origin_refs", []) or []
    }


def _traceability(block: dict[str, Any]) -> list[dict[str, Any]]:
    paths = sorted({
        str(path)
        for section in ("where", "group_by", "projection")
        for item in block.get(section, []) or []
        for path in _walk_paths(item)
    })
    return [{"intent_path": path, "sql_intent_nodes": ["main"]} for path in paths]


def _walk_paths(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(item) for item in value.get("intent_paths", []) or [])
        for child in value.values():
            result.update(_walk_paths(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_walk_paths(child))
    return result
