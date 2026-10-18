from __future__ import annotations

import copy
import re
from typing import Any

from intent_sql_agent.services.direct_sif import (
    validate_grounded_condition_coverage,
)


class DirectSIFCompilationError(ValueError):
    pass


def normalize_direct_sif_bundle(
    *, bundle: dict[str, Any], schema_catalog: dict[str, Any]
) -> dict[str, Any]:
    """Normalize only mechanically identifiable format variants from model output.

    The supplied format defines column_bindings as {physical_column: table}.  This
    adapter accepts common inverse/qualified renderings only when the catalog makes
    the conversion exact; it never chooses between semantic candidates.
    """
    result = copy.deepcopy(bundle)
    linking = result["table_linking"]
    catalog = {
        str(table.get("name")): {
            str(column.get("name")) for column in table.get("columns", []) or []
        }
        for table in schema_catalog.get("tables", []) or []
    }
    table_lookup = {_norm(name): name for name in catalog}
    selected = [
        table_lookup.get(_norm(str(item.get("table") or "")))
        for item in linking.get("selected_tables", []) or []
    ]
    selected = [name for name in selected if name]

    def exact_column(table: str, value: str) -> str | None:
        return next(
            (column for column in catalog.get(table, set()) if _norm(column) == _norm(value)),
            None,
        )

    def qualified(value: str) -> tuple[str, str] | None:
        if "." not in value:
            return None
        raw_table, raw_column = value.split(".", 1)
        table = table_lookup.get(_norm(raw_table))
        column = exact_column(table, raw_column) if table else None
        return (table, column) if table and column else None

    normalized: dict[str, str] = {}
    for raw_key, raw_value in (linking.get("column_bindings", {}) or {}).items():
        key, value = str(raw_key), str(raw_value)
        table = table_lookup.get(_norm(value))
        key_qualified = qualified(key)
        value_qualified = qualified(value)
        if table:
            physical = exact_column(table, key)
            if not physical and key_qualified and key_qualified[0] == table:
                physical = key_qualified[1]
            if physical:
                normalized[physical] = table
                continue
        if value_qualified:
            normalized[value_qualified[1]] = value_qualified[0]
            continue
        candidate_tables = selected or list(catalog)
        matches = [
            (candidate, exact_column(candidate, value)) for candidate in candidate_tables
        ]
        matches = [(candidate, column) for candidate, column in matches if column]
        if len(matches) == 1:
            normalized[matches[0][1]] = matches[0][0]
            continue
        normalized[key] = value
    linking["column_bindings"] = normalized

    for term in result["confirmed_grounding"]["confirmed"].get("terms", []) or []:
        match = qualified(str(term.get("physicalName") or ""))
        if match:
            term["physicalName"] = match[1]
            normalized.setdefault(match[1], match[0])
    for code in result["confirmed_grounding"]["confirmed"].get("codes", []) or []:
        match = qualified(str(code.get("physicalName") or ""))
        if match:
            code["physicalName"] = match[1]
            normalized.setdefault(match[1], match[0])

    # The public format is strictly {physical_column: table}. Models sometimes
    # duplicate a valid physical binding under a logical term id. Such aliases
    # are trace metadata, not columns, and must not enter physical validation.
    normalized = {
        column: table for column, table in normalized.items()
        if table in catalog and exact_column(table, column) is not None
    }
    linking["column_bindings"] = normalized

    binding_lookup = {_norm(column): table for column, table in normalized.items()}
    for section_name in (
        "select_section", "where_sections", "group_by_section", "order_by_section"
    ):
        for section in result["sql_plan"].get(section_name, []) or []:
            raw_column = str(section.get("column") or "")
            if qualified(raw_column):
                table, column = qualified(raw_column)  # type: ignore[misc]
                section["column"] = f"{table}.{column}"
                continue
            table = binding_lookup.get(_norm(raw_column))
            physical = exact_column(table, raw_column) if table else None
            if table and physical:
                section["column"] = f"{table}.{physical}"
    return result


def validate_direct_sif_bundle(
    *, understanding: dict[str, Any], bundle: dict[str, Any], schema_catalog: dict[str, Any],
    confirmed_cqd: dict[str, Any] | None = None,
) -> dict[str, Any]:
    grounding = bundle["confirmed_grounding"]["confirmed"]
    linking = bundle["table_linking"]
    plan = bundle["sql_plan"]
    issues: list[dict[str, Any]] = []

    condition_coverage = validate_grounded_condition_coverage(understanding, plan)
    for item in condition_coverage["assessments"]:
        if item["status"] == "missing":
            issues.append({
                "type": "MISSING_CONDITION_GROUNDING",
                "surface": item["condition"],
                "reason": "conditions_text has no corresponding where_sections item",
            })

    for item in grounding.get("unresolved", []) or []:
        issues.append({
            "type": str(item.get("status") or "UNRESOLVED_SYSTEM"),
            "surface": item.get("surface"),
            "reason": item.get("reason"),
            "candidates": item.get("candidates", []),
        })
    for column in linking.get("diagnostics", {}).get("missing_columns", []) or []:
        issues.append({
            "type": "MISSING_COLUMN_BINDING", "surface": column,
            "reason": "table linking did not bind a confirmed column",
        })

    catalog = {
        str(table.get("name")): {
            str(column.get("name")) for column in table.get("columns", []) or []
        }
        for table in schema_catalog.get("tables", []) or []
    }
    bindings = linking.get("column_bindings", {}) or {}
    for physical, table in bindings.items():
        if str(table) not in catalog:
            issues.append({
                "type": "UNKNOWN_TABLE", "surface": table,
                "reason": f"column binding for {physical} uses an unknown table",
            })
        elif not _contains_identifier(catalog[str(table)], str(physical)):
            issues.append({
                "type": "UNKNOWN_COLUMN", "surface": physical,
                "reason": f"column is not present in linked table {table}",
            })
    for section_name in (
        "select_section", "where_sections", "group_by_section", "order_by_section"
    ):
        for item in plan.get(section_name, []) or []:
            table, column = _split_column(str(item.get("column") or ""))
            if not table or table not in catalog or not _contains_identifier(catalog[table], column):
                issues.append({
                    "type": f"UNKNOWN_{section_name.upper()}_COLUMN",
                    "surface": item.get("column"),
                    "reason": f"{section_name} column is not present in the schema catalog",
                })

    if confirmed_cqd:
        issues.extend(_validate_cqd_plan_semantics(
            confirmed_cqd=confirmed_cqd, bundle=bundle,
        ))

    return {
        "status": "complete" if not issues else "needs_grounding",
        "issues": issues,
        "condition_coverage": condition_coverage,
    }


def compile_direct_sif(
    *, understanding: dict[str, Any], bundle: dict[str, Any]
) -> str:
    grounding = bundle["confirmed_grounding"]["confirmed"]
    linking = bundle["table_linking"]
    plan = bundle["sql_plan"]
    bindings = {
        _norm(str(column)): str(table)
        for column, table in (linking.get("column_bindings", {}) or {}).items()
    }
    target_columns: list[tuple[str, str]] = []
    terms = grounding.get("terms", []) or []
    explicit_select = plan.get("select_section", []) or []
    if explicit_select:
        for item in explicit_select:
            table, physical = _split_column(str(item.get("column") or ""))
            if not table or not physical:
                raise DirectSIFCompilationError(
                    f"SELECT column must be qualified: {item.get('column')}"
                )
            pair = (table, physical)
            if pair not in target_columns:
                target_columns.append(pair)
    else:
        for target in understanding.get("targets", []) or []:
            candidates = [
                item for item in terms
                if _semantic_match(str(target), str(item.get("surface") or ""))
            ]
            if not candidates:
                raise DirectSIFCompilationError(
                    f"No confirmed term represents target: {target}"
                )
            for candidate in candidates:
                physical = str(candidate["physicalName"])
                table = bindings.get(_norm(physical))
                if not table:
                    raise DirectSIFCompilationError(
                        f"No table binding represents target column: {physical}"
                    )
                pair = (table, physical)
                if pair not in target_columns:
                    target_columns.append(pair)
    if not target_columns:
        raise DirectSIFCompilationError("Direct SIF contains no SELECT target")

    selected_tables = linking.get("selected_tables", []) or []
    root = str(linking.get("root_anchor") or "")
    if not root and selected_tables:
        root = str(selected_tables[0].get("table") or "")
    if not root:
        raise DirectSIFCompilationError("Direct SIF contains no root table")

    distinct = str(plan.get("dedupe_section", {}).get("policy") or "").upper() == "DISTINCT"
    select_sql = ", ".join(
        f"{_quote(table)}.{_quote(column)}" for table, column in target_columns
    )
    sql = f"SELECT {'DISTINCT ' if distinct else ''}{select_sql}\nFROM {_quote(root)}"
    joined = {root}
    pending = list(linking.get("joins", []) or [])
    while pending:
        progress = False
        for item in pending[:]:
            left = str(item["from"])
            right = str(item["to"])
            left_col = str(item["from_col"])
            right_col = str(item["to_col"])
            if left in joined and right not in joined:
                sql += (
                    f"\nINNER JOIN {_quote(right)} ON "
                    f"{_quote(left)}.{_quote(left_col)} = "
                    f"{_quote(right)}.{_quote(right_col)}"
                )
                joined.add(right)
            elif right in joined and left not in joined:
                sql += (
                    f"\nINNER JOIN {_quote(left)} ON "
                    f"{_quote(right)}.{_quote(right_col)} = "
                    f"{_quote(left)}.{_quote(left_col)}"
                )
                joined.add(left)
            else:
                continue
            pending.remove(item)
            progress = True
        if not progress:
            raise DirectSIFCompilationError("Join graph is disconnected or cyclically unrooted")

    predicates = [_compile_filter(item) for item in plan.get("where_sections", []) or []]
    if predicates:
        sql += "\nWHERE " + " AND ".join(f"({item})" for item in predicates)
    groups = [
        _quote_column(str(item["column"]))
        for item in plan.get("group_by_section", []) or []
    ]
    if groups:
        sql += "\nGROUP BY " + ", ".join(groups)
    orders = []
    for item in plan.get("order_by_section", []) or []:
        rendered = _quote_column(str(item["column"]))
        aggregate = str(item.get("aggregate") or "").upper()
        if aggregate:
            if aggregate not in {"COUNT", "SUM", "AVG", "MIN", "MAX"}:
                raise DirectSIFCompilationError(
                    f"Unsupported Direct SIF order aggregate: {aggregate}"
                )
            rendered = f"{aggregate}({rendered})"
        orders.append(
            f"{rendered} {str(item['direction']).upper()}"
        )
    if orders:
        sql += "\nORDER BY " + ", ".join(orders)
    limit = plan.get("limit_section", {}).get("count")
    if limit is not None:
        sql += f"\nLIMIT {int(limit)}"
    return sql + ";"


def apply_cqd_sql_semantics(
    *, bundle: dict[str, Any], confirmed_cqd: dict[str, Any]
) -> dict[str, Any]:
    """Materialize CQD SQL operators that must not be left to model prose."""
    result = copy.deepcopy(bundle)
    plan = result["sql_plan"]
    plan.setdefault("select_section", [])
    plan.setdefault("group_by_section", [])
    for aggregation in _mode_aggregations(confirmed_cqd):
        term = _aggregation_term(result, aggregation)
        if term is None:
            continue
        physical = str(term.get("physicalName") or "")
        table = _binding_table(result, physical)
        if not table:
            continue
        column = f"{table}.{physical}"
        snippet = _aggregation_text(aggregation)
        plan["select_section"] = [{
            "column": column, "matched_snippet": snippet,
        }]
        plan["group_by_section"] = [{
            "column": column, "matched_snippet": snippet,
        }]
        plan["order_by_section"] = [{
            "column": column, "aggregate": "COUNT", "direction": "DESC",
            "matched_snippet": snippet,
        }]
        if _requires_single_mode_result(aggregation, confirmed_cqd):
            plan["limit_section"] = {"count": 1, "matched_snippet": snippet}
    return result


def _compile_filter(item: dict[str, Any]) -> str:
    column = _quote_column(str(item["column"]))
    operator = str(item["operator"]).upper()
    values = item.get("values", []) or []
    if operator in {"IS_NULL", "IS_NOT_NULL"}:
        return f"{column} IS {'NOT ' if operator == 'IS_NOT_NULL' else ''}NULL"
    sql_operator = {
        "EQ": "=", "NEQ": "<>", "GT": ">", "GTE": ">=",
        "LT": "<", "LTE": "<=", "LIKE": "LIKE",
    }.get(operator)
    if sql_operator:
        if len(values) != 1:
            raise DirectSIFCompilationError(
                f"{operator} requires exactly one value"
            )
        return f"{column} {sql_operator} {_literal(values[0])}"
    if operator in {"IN", "NOT_IN"}:
        if not values:
            raise DirectSIFCompilationError(f"{operator} requires values")
        joined = ", ".join(_literal(value) for value in values)
        return f"{column} {'NOT ' if operator == 'NOT_IN' else ''}IN ({joined})"
    raise DirectSIFCompilationError(f"Unsupported Direct SIF operator: {operator}")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_column(value: str) -> str:
    table, column = _split_column(value)
    return f"{_quote(table)}.{_quote(column)}" if table else _quote(column)


def _split_column(value: str) -> tuple[str, str]:
    if "." not in value:
        return "", value
    return tuple(value.split(".", 1))  # type: ignore[return-value]


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _aggregation_kind(aggregation: Any) -> str:
    if not isinstance(aggregation, dict):
        return ""
    raw = (
        aggregation.get("aggregation_type")
        or aggregation.get("type")
        or aggregation.get("operator")
    )
    normalized = _norm(str(raw or ""))
    return "mode" if normalized in {"mode", "mostcommon", "frequencytop"} else normalized


def _mode_aggregations(confirmed_cqd: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize explicit mode and equivalent count-then-maximum CQD forms."""
    aggregations = [
        item for item in confirmed_cqd.get("aggregations", []) or []
        if isinstance(item, dict)
    ]
    explicit = [item for item in aggregations if _aggregation_kind(item) == "mode"]
    if explicit:
        return explicit
    counts = [item for item in aggregations if _aggregation_kind(item) == "count"]
    maxima = [item for item in aggregations if _aggregation_kind(item) == "max"]
    groupings = [
        item for item in confirmed_cqd.get("groupings", []) or []
        if isinstance(item, dict)
    ]
    if not counts or not maxima or not groupings:
        return []
    count = counts[0]
    count_target = str(count.get("target") or "")
    group_target = str(
        groupings[0].get("group_by") or groupings[0].get("attribute")
        or groupings[0].get("dimension") or groupings[0].get("target") or ""
    )
    max_text = " ".join(_aggregation_text(item) for item in maxima)
    if not count_target or not group_target:
        return []
    if not _semantic_tokens(group_target) <= _semantic_tokens(count_target):
        return []
    if "count" not in _semantic_tokens(max_text):
        return []
    return [{
        "aggregation_type": "mode",
        "target": group_target,
        "description": "most common " + group_target,
        "tie_policy": _selection_tie_policy(confirmed_cqd),
    }]


def _selection_tie_policy(confirmed_cqd: dict[str, Any]) -> str:
    for item in confirmed_cqd.get("selections", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("count") == 1 or _norm(str(item.get("tie_policy") or "")) in {
            "single", "returnone",
        }:
            return "single"
    return ""


def _aggregation_text(aggregation: dict[str, Any]) -> str:
    expression = aggregation.get("expression")
    expression_text = " ".join(
        str(expression.get(key) or "")
        for key in ("name", "attribute", "target", "description")
    ) if isinstance(expression, dict) else str(expression or "")
    parts = [
        aggregation.get("target"), expression_text,
        aggregation.get("description"), aggregation.get("source_text"),
    ]
    return " ".join(str(item) for item in parts if item).strip() or "aggregation"


def _aggregation_term(
    bundle: dict[str, Any], aggregation: dict[str, Any]
) -> dict[str, Any] | None:
    wanted = _semantic_tokens(_aggregation_text(aggregation))
    ranked: list[tuple[int, dict[str, Any]]] = []
    for term in bundle["confirmed_grounding"]["confirmed"].get("terms", []) or []:
        available = _semantic_tokens(str(term.get("surface") or ""))
        if not available or not (available <= wanted or wanted <= available):
            continue
        ranked.append((len(available & wanted), term))
    if not ranked:
        return None
    longest = max(length for length, _ in ranked)
    leaders = [term for length, term in ranked if length == longest]
    physical = {_norm(str(item.get("physicalName") or "")) for item in leaders}
    return leaders[0] if len(physical) == 1 else None


def _binding_table(bundle: dict[str, Any], physical: str) -> str | None:
    wanted = _norm(physical)
    return next((
        str(table)
        for column, table in (
            bundle["table_linking"].get("column_bindings", {}) or {}
        ).items()
        if _norm(str(column)) == wanted
    ), None)


def _requires_single_mode_result(
    aggregation: dict[str, Any], confirmed_cqd: dict[str, Any]
) -> bool:
    if _norm(str(aggregation.get("tie_policy") or "")) == "single":
        return True
    if any(
        isinstance(item, dict) and (
            item.get("count") == 1
            or _norm(str(item.get("tie_policy") or "")) in {"single", "returnone"}
        )
        for item in confirmed_cqd.get("selections", []) or []
    ):
        return True
    return any(
        "single" in str(item.get("output_type") or "").casefold()
        or "most common" in _aggregation_text(item).casefold()
        for item in confirmed_cqd.get("outputs", []) or []
        if isinstance(item, dict)
    )


def _validate_cqd_plan_semantics(
    *, confirmed_cqd: dict[str, Any], bundle: dict[str, Any]
) -> list[dict[str, Any]]:
    plan = bundle["sql_plan"]
    issues: list[dict[str, Any]] = []
    for aggregation in confirmed_cqd.get("aggregations", []) or []:
        if _aggregation_kind(aggregation) != "mode":
            continue
        term = _aggregation_term(bundle, aggregation)
        if term is None:
            issues.append({
                "type": "MISSING_AGGREGATION_TARGET",
                "surface": _aggregation_text(aggregation),
                "reason": "mode aggregation has no uniquely grounded target column",
            })
            continue
        table = _binding_table(bundle, str(term.get("physicalName") or ""))
        expected = _norm(f"{table}.{term.get('physicalName')}") if table else ""
        checks = {
            "select": any(
                _norm(str(item.get("column") or "")) == expected
                for item in plan.get("select_section", []) or []
            ),
            "group_by": any(
                _norm(str(item.get("column") or "")) == expected
                for item in plan.get("group_by_section", []) or []
            ),
            "frequency_order": any(
                _norm(str(item.get("column") or "")) == expected
                and str(item.get("aggregate") or "").upper() == "COUNT"
                and str(item.get("direction") or "").upper() == "DESC"
                for item in plan.get("order_by_section", []) or []
            ),
        }
        if _requires_single_mode_result(aggregation, confirmed_cqd):
            checks["limit"] = plan.get("limit_section", {}).get("count") == 1
        for role, complete in checks.items():
            if not complete:
                issues.append({
                    "type": "MISSING_AGGREGATION_PLAN_SECTION",
                    "surface": _aggregation_text(aggregation),
                    "reason": f"mode aggregation is missing required {role} semantics",
                })
    return issues


def _semantic_tokens(value: str) -> set[str]:
    ignored = {
        "a", "an", "the", "of", "for", "is", "are", "type", "most", "common",
    }
    return {
        token for token in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
        if token not in ignored
    }


def _norm(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())


def _contains_identifier(columns: set[str], wanted: str) -> bool:
    return _norm(wanted) in {_norm(item) for item in columns}


def _semantic_match(left: str, right: str) -> bool:
    left_tokens = set(re.findall(r"[^\W_]+", left.casefold(), flags=re.UNICODE))
    right_tokens = set(re.findall(r"[^\W_]+", right.casefold(), flags=re.UNICODE))
    return bool(left_tokens) and (left_tokens <= right_tokens or right_tokens <= left_tokens)
