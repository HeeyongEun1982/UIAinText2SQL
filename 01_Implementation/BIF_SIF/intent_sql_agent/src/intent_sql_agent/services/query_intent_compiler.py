from __future__ import annotations

import re
from typing import Any


class UnsupportedQueryIntentLayout(ValueError):
    pass


_OPERATORS = {
    "=": "=", "==": "=", "equals": "=", "!=": "!=", "<>": "!=",
    "not_equals": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<=",
    "like": "LIKE", "not_like": "NOT LIKE", "in": "IN", "not_in": "NOT IN",
    "is": "IS", "is_not": "IS NOT", "between": "BETWEEN",
}
_AGGREGATES = {"count", "sum", "avg", "min", "max"}


def compile_query_intent_layout(
    bif_layout: dict[str, Any], sif_layout: dict[str, Any]
) -> str:
    """Compile the flat SIF layout. BIF is retained only for call compatibility."""
    if sif_layout.get("phase") != "sif":
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_COMPILER_REQUIRES_SIF")
    fields = _field_map(sif_layout)
    selected = sif_layout.get("select", []) or []
    if not selected:
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_HAS_NO_PROJECTION")

    projections = [_render_field(_get_field(fields, field_id), fields) for field_id in selected]
    sql = "SELECT " + ("DISTINCT " if sif_layout.get("distinct") else "")
    sql += ", ".join(projections)
    sql += _from_clause(sif_layout, fields)

    where = _filter_clause(
        sif_layout.get("predicates", []) or [], sif_layout.get("where"), fields
    )
    if where:
        sql += "\nWHERE " + where

    groups = [
        _render_field(_get_field(fields, field_id), fields, ignore_aggregate=True)
        for field_id in sif_layout.get("group_by", []) or []
    ]
    if groups:
        sql += "\nGROUP BY " + ", ".join(groups)

    having = _filter_clause(
        sif_layout.get("having_predicates", []) or [], sif_layout.get("having"), fields
    )
    if having:
        sql += "\nHAVING " + having

    orders = [_render_order(item, fields) for item in sif_layout.get("order_by", []) or []]
    if orders:
        sql += "\nORDER BY " + ", ".join(orders)

    limit = sif_layout.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_INVALID_LIMIT")
        sql += f"\nLIMIT {limit}"
    return sql + ";"


def _field_map(layout: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sources = {
        str(item.get("id")): item.get("table")
        for item in layout.get("sources", []) or []
    }
    result: dict[str, dict[str, Any]] = {}
    for field in layout.get("fields", []) or []:
        field_id = str(field.get("id") or "")
        if not field_id or field_id in result:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_INVALID_FIELD_ID")
        resolved = dict(field)
        resolved["table"] = sources.get(str(field.get("source")))
        result[field_id] = resolved
    return result


def _get_field(fields: dict[str, dict[str, Any]], field_id: Any) -> dict[str, Any]:
    field = fields.get(str(field_id))
    if field is None:
        raise UnsupportedQueryIntentLayout(f"QUERY_LAYOUT_UNKNOWN_FIELD: {field_id}")
    return field


def _render_field(
    field: dict[str, Any], fields: dict[str, dict[str, Any]], *, ignore_aggregate: bool = False
) -> str:
    expression = field.get("expression")
    if expression:
        rendered = _render_expression(str(expression), fields)
    else:
        table = field.get("table")
        column = field.get("column")
        if not table or not column:
            raise UnsupportedQueryIntentLayout(
                f"QUERY_LAYOUT_FIELD_NOT_LINKED: {field.get('id')}"
            )
        rendered = f"{_quote(str(table))}.{_quote(str(column))}"
    aggregate = None if ignore_aggregate else field.get("aggregate")
    if aggregate:
        aggregate = str(aggregate).lower()
        if aggregate not in _AGGREGATES:
            raise UnsupportedQueryIntentLayout(f"QUERY_LAYOUT_INVALID_AGGREGATE: {aggregate}")
        if field.get("distinct"):
            rendered = "DISTINCT " + rendered
        rendered = f"{aggregate.upper()}({rendered})"
    return rendered


def _render_expression(expression: str, fields: dict[str, dict[str, Any]]) -> str:
    if ";" in expression or "--" in expression or "/*" in expression:
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_UNSAFE_EXPRESSION")

    def replace(match: re.Match[str]) -> str:
        field = _get_field(fields, match.group(1))
        return _render_field(field, fields)

    rendered = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, expression)
    if "{" in rendered or "}" in rendered:
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_UNKNOWN_EXPRESSION_FIELD")
    return rendered


def _tables(layout: dict[str, Any], fields: dict[str, dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for field in fields.values():
        table = field.get("table")
        if table and str(table) not in result:
            result.append(str(table))
    source_tables = {
        str(item.get("id")): item.get("table")
        for item in layout.get("sources", []) or []
    }
    for join in layout.get("joins", []) or []:
        for key in ("left_source", "right_source"):
            table = source_tables.get(str(join.get(key)))
            if table and str(table) not in result:
                result.append(str(table))
    return result


def _from_clause(layout: dict[str, Any], fields: dict[str, dict[str, Any]]) -> str:
    tables = _tables(layout, fields)
    if not tables:
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_HAS_NO_LINKED_TABLE")
    source_tables = {
        str(item.get("id")): item.get("table")
        for item in layout.get("sources", []) or []
    }
    joins = [
        {
            **item,
            "left_table": source_tables.get(str(item.get("left_source"))),
            "right_table": source_tables.get(str(item.get("right_source"))),
        }
        for item in layout.get("joins", []) or []
    ]
    if not joins:
        return "\nFROM " + ", ".join(_quote(table) for table in tables)

    right_tables = {str(item.get("right_table")) for item in joins if item.get("right_table")}
    base = next((table for table in tables if table not in right_tables), tables[0])
    sql = "\nFROM " + _quote(base)
    connected = {base}
    pending = list(joins)
    while pending:
        progressed = False
        for join in list(pending):
            left = join.get("left_table")
            right = join.get("right_table")
            join_type = str(join.get("type") or "").lower()
            if not left or not right or left not in connected:
                continue
            if join_type not in {"inner", "left", "right", "full", "cross"}:
                raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_JOIN_NOT_LINKED")
            sql += f"\n{join_type.upper()} JOIN {_quote(str(right))}"
            if join_type != "cross":
                left_column = join.get("left_column")
                right_column = join.get("right_column")
                if not left_column or not right_column:
                    raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_JOIN_NOT_LINKED")
                sql += (
                    f" ON {_quote(str(left))}.{_quote(str(left_column))}"
                    f" = {_quote(str(right))}.{_quote(str(right_column))}"
                )
            connected.add(str(right))
            pending.remove(join)
            progressed = True
        if not progressed:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_DISCONNECTED_JOIN")
    for table in tables:
        if table not in connected:
            sql += ", " + _quote(table)
    return sql


def _render_predicate(item: dict[str, Any], fields: dict[str, dict[str, Any]]) -> str:
    left = _render_field(_get_field(fields, item.get("field")), fields)
    operator_key = str(item.get("operator") or "").lower()
    operator = _OPERATORS.get(operator_key)
    if operator is None:
        raise UnsupportedQueryIntentLayout(
            f"QUERY_LAYOUT_INVALID_OPERATOR: {item.get('id')}"
        )
    value = item.get("value")
    if operator in {"IN", "NOT IN"}:
        if not isinstance(value, list) or not value:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_IN_REQUIRES_VALUES")
        return f"{left} {operator} (" + ", ".join(_literal(v) for v in value) + ")"
    if operator == "BETWEEN":
        if not isinstance(value, list) or len(value) != 2:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_BETWEEN_REQUIRES_TWO_VALUES")
        return f"{left} BETWEEN {_literal(value[0])} AND {_literal(value[1])}"
    return f"{left} {operator} {_literal(value)}"


def _filter_clause(
    predicates: list[dict[str, Any]], logic: Any, fields: dict[str, dict[str, Any]]
) -> str:
    rendered = {str(item["id"]): _render_predicate(item, fields) for item in predicates}
    if not rendered:
        return ""
    if logic is None:
        if len(rendered) != 1:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_FILTER_LOGIC_MISSING")
        return next(iter(rendered.values()))
    expression = str(logic)
    syntax = expression
    for predicate_id in sorted(rendered, key=len, reverse=True):
        syntax = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(predicate_id)}(?![A-Za-z0-9_])",
            "",
            syntax,
        )
    residue = re.sub(r"\b(?:AND|OR|NOT)\b|[()\s]", "", syntax, flags=re.I)
    if residue:
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_INVALID_FILTER_LOGIC")
    for predicate_id in sorted(rendered, key=len, reverse=True):
        expression = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(predicate_id)}(?![A-Za-z0-9_])",
            f"({rendered[predicate_id]})",
            expression,
        )
    return expression


def _render_order(item: dict[str, Any], fields: dict[str, dict[str, Any]]) -> str:
    rendered = _render_field(_get_field(fields, item.get("field")), fields, ignore_aggregate=True)
    aggregate = item.get("aggregate")
    if aggregate:
        aggregate = str(aggregate).lower()
        if aggregate not in _AGGREGATES:
            raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_INVALID_ORDER_AGGREGATE")
        rendered = f"{aggregate.upper()}({rendered})"
    direction = str(item.get("direction") or "").upper()
    if direction not in {"ASC", "DESC"}:
        raise UnsupportedQueryIntentLayout("QUERY_LAYOUT_INVALID_ORDER_DIRECTION")
    return f"{rendered} {direction}"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
