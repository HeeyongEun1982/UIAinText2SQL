from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterator


def validate_literal_domains(database: Path, sif: dict[str, Any]) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    warnings: list[dict[str, Any]] = []
    try:
        blocks = {
            str(block.get("block_id")): block
            for block in sif.get("query_blocks", [])
            if block.get("block_id")
        }
        for block in sif.get("query_blocks", []):
            aliases: dict[str, str] = {}
            table_columns: dict[str, set[str]] = {}
            source_details: dict[str, dict[str, Any]] = {}
            for source in block.get("sources", []):
                kind = str(source.get("kind") or "")
                if kind == "table" and source.get("name"):
                    target = str(source["name"])
                    escaped_table = target.replace("'", "''")
                    table_columns[target] = {
                        str(row[1]).lower()
                        for row in connection.execute(f"PRAGMA table_info('{escaped_table}')")
                    }
                    source_details[target] = {
                        "source_kind": "table", "table": target, "source_ref": None,
                    }
                elif kind == "query_block" and source.get("block_ref"):
                    block_ref = str(source["block_ref"])
                    target = f"@query_block:{block_ref}"
                    table_columns[target] = _block_output_columns(blocks.get(block_ref))
                    source_details[target] = {
                        "source_kind": "query_block", "table": None, "source_ref": block_ref,
                    }
                else:
                    continue
                for key in (source.get("alias"), source.get("source_id"), source.get("name")):
                    if key:
                        aliases[str(key)] = target
            expressions = list(_block_expressions(block))
            seen_column_warnings: set[tuple[str, str, str]] = set()
            for expression in expressions:
                for column in _column_references(expression):
                    source = str(column.get("source") or "")
                    column_name = str(column.get("name") or "")
                    target = aliases.get(source)
                    details = source_details.get(target or "", {})
                    issue = None
                    if not target:
                        issue = "column_source_not_in_query_block"
                    elif column_name.lower() not in table_columns.get(target, set()):
                        issue = "column_not_in_source"
                    if issue:
                        signature = (issue, source, column_name)
                        if signature in seen_column_warnings:
                            continue
                        seen_column_warnings.add(signature)
                        warnings.append(
                            {
                                "block_id": block.get("block_id"),
                                "source": source,
                                "table": details.get("table"),
                                "source_kind": details.get("source_kind"),
                                "source_ref": details.get("source_ref"),
                                "column": column_name,
                                "issue": issue,
                                "intent_paths": column.get("intent_paths", []),
                            }
                        )
            for expression in expressions:
                for column, literal, intent_paths in _equality_pairs(expression):
                    source = column.get("source")
                    column_name = column.get("name")
                    target = aliases.get(str(source)) if source is not None else None
                    table = source_details.get(target or "", {}).get("table")
                    if not table or not column_name:
                        continue
                    if str(column_name).lower() not in table_columns.get(table, set()):
                        continue
                    sql = (
                        f"SELECT 1 FROM {_quote_identifier(table)} "
                        f"WHERE {_quote_identifier(str(column_name))} = ? LIMIT 1"
                    )
                    try:
                        exists = connection.execute(sql, (literal,)).fetchone() is not None
                    except sqlite3.DatabaseError as exc:
                        warnings.append(
                            {
                                "block_id": block.get("block_id"),
                                "table": table,
                                "column": column_name,
                                "literal": literal,
                                "issue": "predicate_validation_error",
                                "error": str(exc),
                                "intent_paths": intent_paths,
                            }
                        )
                        continue
                    if not exists:
                        warnings.append(
                            {
                                "block_id": block.get("block_id"),
                                "table": table,
                                "column": column_name,
                                "literal": literal,
                                "issue": "literal_not_in_column_domain",
                                "intent_paths": intent_paths,
                            }
                        )
        return warnings
    finally:
        connection.close()


def _equality_pairs(
    expression: Any,
) -> Iterator[tuple[dict[str, Any], Any, list[str]]]:
    if not isinstance(expression, dict):
        return
    if expression.get("kind") == "binary":
        operator = str(expression.get("operator") or "").lower()
        left = expression.get("left")
        right = expression.get("right")
        if operator in {"equals", "=", "=="}:
            if _kind(left) == "column" and _kind(right) == "literal":
                yield left, right.get("value"), expression.get("intent_paths", [])
            elif _kind(right) == "column" and _kind(left) == "literal":
                yield right, left.get("value"), expression.get("intent_paths", [])
        elif operator in {"in", "not_in", "not in"}:
            if _kind(left) == "column" and _kind(right) == "literal":
                values = right.get("value")
                for value in values if isinstance(values, list) else [values]:
                    yield left, value, expression.get("intent_paths", [])
        yield from _equality_pairs(left)
        yield from _equality_pairs(right)
    for key in ("args", "branches"):
        for child in expression.get(key, []) or []:
            yield from _equality_pairs(child)
    for key in ("operand", "else_expression"):
        yield from _equality_pairs(expression.get(key))


def _block_expressions(block: dict[str, Any]) -> Iterator[Any]:
    for key in ("where", "having", "group_by"):
        yield from block.get(key, []) or []
    for join in block.get("joins", []) or []:
        yield from join.get("predicates", []) or []
    for projection in block.get("projection", []) or []:
        yield projection.get("expression")
    for derived in block.get("derived_fields", []) or []:
        yield derived.get("expression")
    for ordering in block.get("order_by", []) or []:
        yield ordering.get("expression")
    for window in block.get("windows", []) or []:
        yield from window.get("args", []) or []
        yield from window.get("partition_by", []) or []
        for ordering in window.get("order_by", []) or []:
            yield ordering.get("expression")


def _column_references(expression: Any) -> Iterator[dict[str, Any]]:
    if isinstance(expression, dict):
        if expression.get("kind") == "column":
            yield expression
        for value in expression.values():
            yield from _column_references(value)
    elif isinstance(expression, list):
        for value in expression:
            yield from _column_references(value)


def _kind(value: Any) -> str | None:
    return value.get("kind") if isinstance(value, dict) else None


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _block_output_columns(block: dict[str, Any] | None) -> set[str]:
    """Return the names physically exposed by a referenced query block."""
    if not isinstance(block, dict):
        return set()
    outputs: set[str] = set()
    for projection in block.get("projection", []) or []:
        expression = projection.get("expression", {})
        names = [projection.get("output_id"), projection.get("label")]
        if expression.get("kind") in {"column", "reference"}:
            names.append(expression.get("name"))
        for name in names:
            if name:
                outputs.add(str(name).lower())
    return outputs
