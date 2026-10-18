from __future__ import annotations

import re
from typing import Any


class UnsupportedSIFError(ValueError):
    pass


_FUNCTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def compile_sif(sif: dict[str, Any]) -> str:
    blocks = {
        str(block.get("block_id")): block for block in sif.get("query_blocks", [])
    }
    main_id = str(sif.get("main_block_id") or "")
    if main_id not in blocks:
        raise UnsupportedSIFError("main_block_id does not reference a query block")
    compiler = _Compiler(blocks)
    return compiler.query(main_id, top_level=True) + ";"


class _Compiler:
    def __init__(self, blocks: dict[str, dict[str, Any]]) -> None:
        self.blocks = blocks
        self.stack: set[str] = set()

    def query(self, block_id: str, *, top_level: bool = False) -> str:
        if block_id in self.stack:
            raise UnsupportedSIFError(f"cyclic query block reference: {block_id}")
        block = self.blocks.get(block_id)
        if block is None:
            raise UnsupportedSIFError(f"unknown query block: {block_id}")
        self.stack.add(block_id)
        try:
            sql = self._select(block)
            for operation in block.get("set_operations", []) or []:
                operator = str(operation.get("operator") or "").upper()
                if operator not in {"UNION", "INTERSECT", "EXCEPT"}:
                    raise UnsupportedSIFError(f"unsupported set operator: {operator}")
                if operation.get("all"):
                    if operator != "UNION":
                        raise UnsupportedSIFError(f"SQLite does not support {operator} ALL")
                    operator += " ALL"
                sql += f"\n{operator}\n" + self.query(
                    str(operation.get("query_block_ref")), top_level=False
                )
            return sql
        finally:
            self.stack.remove(block_id)

    def _select(self, block: dict[str, Any]) -> str:
        if block.get("derived_fields"):
            raise UnsupportedSIFError("derived fields require model fallback")
        windows = {
            str(window.get("output_field")): window
            for window in block.get("windows", []) or []
        }
        projections = []
        for projection in block.get("projection", []):
            expression = projection.get("expression", {})
            if expression.get("kind") == "reference" and str(expression.get("name")) in windows:
                rendered = self._window(windows[str(expression.get("name"))])
            else:
                rendered = self.expression(expression)
            label = projection.get("label")
            if label:
                rendered += " AS " + _quote(str(label))
            projections.append(rendered)
        if not projections:
            raise UnsupportedSIFError("query block has no projection")
        distinct = "DISTINCT " if block.get("distinct") else ""
        sql = f"SELECT {distinct}" + ", ".join(projections)
        sql += self._from(block)
        where = block.get("where", []) or []
        if where:
            sql += "\nWHERE " + " AND ".join(f"({self.expression(item)})" for item in where)
        group_by = block.get("group_by", []) or []
        if group_by:
            sql += "\nGROUP BY " + ", ".join(self.expression(item) for item in group_by)
        having = block.get("having", []) or []
        if having:
            sql += "\nHAVING " + " AND ".join(f"({self.expression(item)})" for item in having)
        ordering = block.get("order_by", []) or []
        if ordering:
            sql += "\nORDER BY " + ", ".join(self._ordering(item) for item in ordering)
        limit = block.get("limit")
        if limit:
            if limit.get("with_ties"):
                raise UnsupportedSIFError("WITH TIES requires model fallback")
            sql += f"\nLIMIT {int(limit['count'])}"
            if int(limit.get("offset", 0)):
                sql += f" OFFSET {int(limit['offset'])}"
        return sql

    def _from(self, block: dict[str, Any]) -> str:
        sources = block.get("sources", []) or []
        if not sources:
            return ""
        by_ref: dict[str, dict[str, Any]] = {}
        for source in sources:
            for reference in (source.get("source_id"), source.get("alias")):
                if reference:
                    by_ref[str(reference)] = source
        joined_right_sources = {
            id(by_ref[reference])
            for join in block.get("joins", []) or []
            if (reference := str(join.get("right_source"))) in by_ref
        }
        base = next(
            (
                source
                for source in sources
                if id(source) not in joined_right_sources
            ),
            sources[0],
        )
        sql = "\nFROM " + self._source(base)
        consumed = {id(base)}
        for join in block.get("joins", []) or []:
            join_type = str(join.get("type") or "inner").upper()
            if join_type not in {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}:
                raise UnsupportedSIFError(f"unsupported join type: {join_type}")
            right_reference = str(join.get("right_source"))
            right = by_ref.get(right_reference)
            if right is None:
                raise UnsupportedSIFError(f"join source not found: {right_reference}")
            sql += f"\n{join_type} JOIN {self._source(right)}"
            predicates = join.get("predicates", []) or []
            if join_type != "CROSS":
                if not predicates:
                    raise UnsupportedSIFError("non-cross join has no predicate")
                sql += " ON " + " AND ".join(
                    f"({self.expression(predicate)})" for predicate in predicates
                )
            consumed.add(id(right))
        for source in sources:
            if id(source) not in consumed:
                sql += ", " + self._source(source)
        return sql

    def _source(self, source: dict[str, Any]) -> str:
        kind = source.get("kind")
        alias = source.get("alias") or source.get("source_id")
        if kind in {"table", "view"}:
            name = source.get("name")
            if not name:
                raise UnsupportedSIFError("table source has no name")
            value = _quote(str(name))
        elif kind == "query_block":
            ref = str(source.get("block_ref") or "")
            value = f"({self.query(ref, top_level=False)})"
        else:
            raise UnsupportedSIFError(f"unsupported source kind: {kind}")
        return value + (f" AS {_quote(str(alias))}" if alias else "")

    def expression(self, expression: Any) -> str:
        if not isinstance(expression, dict):
            raise UnsupportedSIFError("expression must be an object")
        kind = expression.get("kind")
        if kind == "column":
            name = expression.get("name")
            if not name:
                raise UnsupportedSIFError("column has no name")
            source = expression.get("source")
            return (f"{_quote(str(source))}." if source else "") + _quote(str(name))
        if kind == "literal":
            return _literal(expression.get("value"))
        if kind == "star":
            source = expression.get("source")
            return f"{_quote(str(source))}.*" if source else "*"
        if kind == "reference":
            return _quote(str(expression.get("name")))
        if kind == "function":
            name = str(expression.get("name") or "")
            if not _FUNCTION.fullmatch(name):
                raise UnsupportedSIFError(f"unsafe function name: {name}")
            args = ", ".join(self.expression(item) for item in expression.get("args", []) or [])
            if expression.get("distinct"):
                args = "DISTINCT " + args
            return f"{name.upper()}({args})"
        if kind == "binary":
            return self._binary(expression)
        if kind == "unary":
            operator = str(expression.get("operator") or "").lower()
            operand = self.expression(expression.get("operand"))
            mapping = {
                "not": f"NOT ({operand})",
                "-": f"-({operand})",
                "is_null": f"{operand} IS NULL",
                "is null": f"{operand} IS NULL",
                "is_not_null": f"{operand} IS NOT NULL",
                "is not null": f"{operand} IS NOT NULL",
            }
            if operator not in mapping:
                raise UnsupportedSIFError(f"unsupported unary operator: {operator}")
            return mapping[operator]
        if kind == "conditional":
            branches = " ".join(
                f"WHEN {self.expression(branch['when'])} THEN {self.expression(branch['then'])}"
                for branch in expression.get("branches", []) or []
            )
            otherwise = expression.get("else_expression")
            return "CASE " + branches + (
                f" ELSE {self.expression(otherwise)}" if otherwise else ""
            ) + " END"
        if kind == "subquery":
            return f"({self.query(str(expression.get('query_block_ref')), top_level=False)})"
        raise UnsupportedSIFError(f"unsupported expression kind: {kind}")

    def _binary(self, expression: dict[str, Any]) -> str:
        operator = str(expression.get("operator") or "").lower()
        left = self.expression(expression.get("left"))
        right_value = expression.get("right")
        if operator in {"in", "not_in", "not in"}:
            if isinstance(right_value, dict) and right_value.get("kind") == "literal" and isinstance(right_value.get("value"), list):
                values = ", ".join(_literal(value) for value in right_value["value"])
                return f"{left} {'NOT IN' if operator != 'in' else 'IN'} ({values})"
        if operator == "between":
            if isinstance(right_value, dict) and right_value.get("kind") == "literal":
                bounds = right_value.get("value")
                if isinstance(bounds, list) and len(bounds) == 2:
                    return f"{left} BETWEEN {_literal(bounds[0])} AND {_literal(bounds[1])}"
                if isinstance(bounds, dict) and {"start", "end"} <= set(bounds):
                    return f"{left} BETWEEN {_literal(bounds['start'])} AND {_literal(bounds['end'])}"
        if operator in {"is", "is not", "is_not"} and isinstance(right_value, dict):
            if right_value.get("kind") == "literal" and right_value.get("value") is None:
                return f"{left} {'IS NOT' if operator != 'is' else 'IS'} NULL"
        right = self.expression(right_value)
        mapped = {
            "equals": "=", "==": "=", "=": "=",
            "not_equals": "!=", "!=": "!=", "<>": "!=",
            "greater_than": ">", ">": ">", "greater_or_equal": ">=", ">=": ">=",
            "less_than": "<", "<": "<", "less_or_equal": "<=", "<=": "<=",
            "and": "AND", "or": "OR", "+": "+", "-": "-", "*": "*", "/": "/",
            "like": "LIKE", "not_like": "NOT LIKE",
        }.get(operator)
        if mapped is None:
            raise UnsupportedSIFError(f"unsupported binary operator: {operator}")
        return f"{left} {mapped} {right}"

    def _window(self, window: dict[str, Any]) -> str:
        name = str(window.get("function") or "")
        if not _FUNCTION.fullmatch(name):
            raise UnsupportedSIFError(f"unsafe window function: {name}")
        args = ", ".join(self.expression(item) for item in window.get("args", []) or [])
        over = []
        partitions = window.get("partition_by", []) or []
        if partitions:
            over.append("PARTITION BY " + ", ".join(self.expression(item) for item in partitions))
        ordering = window.get("order_by", []) or []
        if ordering:
            over.append("ORDER BY " + ", ".join(self._ordering(item) for item in ordering))
        return f"{name.upper()}({args}) OVER ({' '.join(over)})"

    def _ordering(self, ordering: dict[str, Any]) -> str:
        value = self.expression(ordering.get("expression"))
        value += " " + str(ordering.get("direction") or "asc").upper()
        nulls = ordering.get("nulls")
        if nulls in {"first", "last"}:
            value += " NULLS " + str(nulls).upper()
        return value


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "(" + ", ".join(_literal(item) for item in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"
