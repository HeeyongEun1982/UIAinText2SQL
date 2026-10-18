from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .direct_sif_compiler import _mode_aggregations, _requires_single_mode_result
from .direct_sif_materializer import (
    _choose_candidate,
    _column_candidates,
    _connect_tables,
    _literal_values,
    _operator,
    _output_specs,
    _predicate_specs,
    _root_table,
)


@dataclass(frozen=True)
class DirectCQDSQLResult:
    """Final SQL plus linking decisions; it is not an intermediate layout."""

    sql: str | None
    issues: list[dict[str, Any]] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    joins: list[dict[str, str]] = field(default_factory=list)


def compile_cqd_directly_to_sql(
    *,
    confirmed_cqd: dict[str, Any],
    schema_catalog: dict[str, Any],
    benchmark_evidence: str | None,
    technical_feedback: list[dict[str, Any]] | None = None,
) -> DirectCQDSQLResult:
    """Link confirmed CQD phrases and immediately emit SQL.

    The function deliberately creates no understanding, grounding, binding, or
    SQL-plan document.  CQD phrases remain the authority and receive physical
    identifiers only while the SQL string is assembled.
    """
    tables = list(schema_catalog.get("tables", []) or [])
    evidence = str(benchmark_evidence or "")
    feedback = list(technical_feedback or [])
    issues: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    selected: list[tuple[str, str]] = []
    predicates: list[tuple[str, str, list[Any], str]] = []

    outputs = _output_specs(confirmed_cqd, {"targets": []})
    for index, spec in enumerate(outputs):
        item_id = f"select:{index}"
        candidates = _direct_candidates(
            spec=spec, tables=tables, evidence=evidence, role="SELECT",
        )
        chosen = _choose_candidate(
            candidates, item_id=item_id, surface=spec["surface"], feedback=feedback,
        )
        if chosen is None:
            _append_candidate_issue(issues, spec["surface"], candidates)
            continue
        pair = (str(chosen["table"]), str(chosen["column"]))
        if pair not in selected:
            selected.append(pair)
        bindings.append(_binding(item_id, "SELECT", spec["surface"], chosen))
        siblings = _numbered_output_siblings(pair, tables)
        if len(siblings) >= 2:
            labels = [
                f"Use one physical field ({pair[0]}.{pair[1]})",
                "Use all sibling fields: " + ", ".join(
                    f"{pair[0]}.{column}" for column in siblings
                ),
            ]
            choice = _feedback_option(feedback, spec["surface"], labels)
            if choice == 1:
                for column in siblings:
                    sibling_pair = (pair[0], column)
                    if sibling_pair not in selected:
                        selected.append(sibling_pair)
                        bindings.append({
                            "cqd_surface": spec["surface"], "sql_role": "SELECT",
                            "physical": f"{pair[0]}.{column}",
                            "basis": "technical_clarification_numbered_family",
                        })
            elif choice is None and _looks_plural(spec["surface"]):
                issues.append({
                    "type": "CLARIFICATION_REQUIRED",
                    "surface": spec["surface"],
                    "reason": "One logical output maps to parallel numbered physical fields.",
                    "candidates": labels,
                })

    for index, spec in enumerate(_predicate_specs(confirmed_cqd, {})):
        item_id = f"where:{index}"
        candidates = _direct_candidates(
            spec=spec, tables=tables, evidence=evidence, role="WHERE",
        )
        chosen = _choose_candidate(
            candidates, item_id=item_id, surface=spec["surface"], feedback=feedback,
        )
        if chosen is None:
            _append_candidate_issue(issues, spec["surface"], candidates)
            continue
        pair = (str(chosen["table"]), str(chosen["column"]))
        values = _literal_values(spec, chosen, evidence)
        raw_operator = str(spec.get("operator") or "=").strip().casefold()
        if raw_operator in {"between", "range", "from_to"} and len(values) == 2:
            parts = [("GTE", [values[0]]), ("LTE", [values[1]])]
        else:
            parts = [(_operator(spec.get("operator")), values)]
        for operator, part_values in parts:
            predicate = (f"{pair[0]}.{pair[1]}", operator, part_values, spec["surface"])
            if not any(_same_predicate(predicate, existing) for existing in predicates):
                predicates.append(predicate)
        selected.append(pair)
        bindings.append(_binding(item_id, "WHERE", spec["surface"], chosen))

    output_pairs = [
        (item["physical"].split(".", 1)[0], item["physical"].split(".", 1)[1])
        for item in bindings if item["sql_role"] == "SELECT"
    ]
    output_pairs = list(dict.fromkeys(output_pairs))
    if not output_pairs:
        return DirectCQDSQLResult(None, issues or [{
            "type": "UNRESOLVED_SYSTEM", "surface": "output",
            "reason": "No CQD output could be linked to a physical column.",
            "candidates": [],
        }], bindings)

    required_pairs = list(dict.fromkeys(selected))
    root = _root_table(required_pairs, confirmed_cqd)
    linked_tables, joins, disconnected = _connect_tables(
        root=root, required={table for table, _ in required_pairs}, tables=tables,
        semantic_context=" ".join(spec["surface"] for spec in outputs),
    )
    for table in disconnected:
        issues.append({
            "type": "UNRESOLVED_SYSTEM", "surface": table,
            "reason": "No foreign-key path connects a required table.",
            "candidates": [],
        })
    if not root:
        return DirectCQDSQLResult(None, issues, bindings)

    select_sql = ", ".join(_qualified(table, column) for table, column in output_pairs)
    sql = f"SELECT {select_sql}\nFROM {_quote(root)}"
    joined = {root}
    pending = list(joins)
    while pending:
        progress = False
        for join in pending[:]:
            left, right = join["from"], join["to"]
            if left in joined and right not in joined:
                sql += _join_sql(right, left, join["from_col"], right, join["to_col"])
                joined.add(right)
            elif right in joined and left not in joined:
                sql += _join_sql(left, right, join["to_col"], left, join["from_col"])
                joined.add(left)
            else:
                continue
            pending.remove(join)
            progress = True
        if not progress:
            issues.append({
                "type": "UNRESOLVED_SYSTEM", "surface": "join graph",
                "reason": "The required join graph cannot be rooted.", "candidates": [],
            })
            break

    if predicates:
        sql += "\nWHERE " + " AND ".join(
            f"({_render_predicate(column, operator, values)})"
            for column, operator, values, _ in predicates
        )

    modes = _mode_aggregations(confirmed_cqd)
    if modes:
        group_pair = output_pairs[0]
        group_column = _qualified(*group_pair)
        sql += f"\nGROUP BY {group_column}"
        sql += f"\nORDER BY COUNT({group_column}) DESC"
        if _requires_single_mode_result(modes[0], confirmed_cqd):
            sql += "\nLIMIT 1"

    return DirectCQDSQLResult(
        sql + ";", issues, bindings,
        [str(item.get("table")) for item in linked_tables], joins,
    )


def _append_candidate_issue(
    issues: list[dict[str, Any]], surface: str, candidates: list[dict[str, Any]],
) -> None:
    labels = [str(item["label"]) for item in candidates[:5]]
    issues.append({
        "type": "CLARIFICATION_REQUIRED" if len(labels) >= 2 else "UNRESOLVED_SYSTEM",
        "surface": surface,
        "reason": "Physical schema evidence has no unique supported candidate.",
        "candidates": labels,
    })


def _direct_candidates(
    *, spec: dict[str, Any], tables: list[dict[str, Any]], evidence: str, role: str,
) -> list[dict[str, Any]]:
    """Balance logical-column semantics against the longest stored-value match."""
    candidates = _column_candidates(
        spec=spec, tables=tables, evidence=evidence, role=role,
    )
    if role != "WHERE" or not candidates:
        return candidates
    for candidate in candidates:
        candidate["matched_value_tokens"] = max(
            (
                len(re.findall(r"[^\W_]+", str(item.get("value") or ""), re.UNICODE))
                for item in candidate.get("matching_values", []) or []
            ),
            default=0,
        )
    maximal = max(item["matched_value_tokens"] for item in candidates)
    if maximal:
        for candidate in candidates:
            if candidate["matched_value_tokens"] == maximal:
                candidate["score"] += maximal * 40
                candidate["reasons"] = [*candidate["reasons"], "maximal_stored_value_match"]
    strong_semantic = [
        item for item in candidates
        if set(item.get("reasons", [])) & {
            "dimension_phrase_match", "dimension_names_column",
            "identifier_description_similarity",
        }
    ]
    maximal_values = [item for item in candidates if item["matched_value_tokens"] == maximal and maximal]
    if strong_semantic and maximal_values:
        semantic = max(strong_semantic, key=lambda item: item["score"])
        value = max(maximal_values, key=lambda item: item["score"])
        if semantic["candidate_id"] != value["candidate_id"]:
            tie_score = max(semantic["score"], value["score"])
            semantic["score"] = tie_score
            value["score"] = tie_score
            semantic["reasons"] = [*semantic["reasons"], "semantic_value_conflict"]
            value["reasons"] = [*value["reasons"], "semantic_value_conflict"]
    candidates.sort(key=lambda item: (-item["score"], item["table"], item["column"]))
    return candidates


def _binding(item_id: str, role: str, surface: str, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "cqd_ref": item_id, "cqd_surface": surface, "sql_role": role,
        "physical": f"{candidate['table']}.{candidate['column']}",
        "basis": candidate.get("selection_rule"),
    }


def _numbered_output_siblings(
    pair: tuple[str, str], tables: list[dict[str, Any]],
) -> list[str]:
    match = re.fullmatch(r"(.+?)(\d+)", pair[1])
    if not match:
        return []
    prefix = match.group(1)
    table = next((item for item in tables if str(item.get("name")) == pair[0]), None)
    if table is None:
        return []
    names = [
        str(item.get("name")) for item in table.get("columns", []) or []
        if re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", str(item.get("name") or ""), re.I)
        and not re.search(
            r"not useful|unused|deprecated|do not use|obsolete",
            " ".join(str(item.get(key) or "") for key in ("description", "value_description", "status", "comment")),
            re.I,
        )
    ]
    return sorted(names, key=lambda name: int(re.search(r"(\d+)$", name).group(1)))


def _feedback_option(feedback: list[dict[str, Any]], surface: str, labels: list[str]) -> int | None:
    for item in reversed(feedback):
        if item.get("type") != "TECHNICAL_CLARIFICATION_RESPONSE":
            continue
        if _norm(item.get("surface")) != _norm(surface):
            continue
        if [str(value) for value in item.get("candidates", []) or []] != labels:
            continue
        selected = list((item.get("response") or {}).get("selected_option_ids") or [])
        if len(selected) == 1 and re.fullmatch(r"option_[12]", str(selected[0]), re.I):
            return int(str(selected[0]).split("_")[-1]) - 1
    return None


def _looks_plural(value: str) -> bool:
    return bool(re.search(r"\b(addresses|emails|values|fields|columns|names|codes)\b", value, re.I))


def _same_predicate(
    left: tuple[str, str, list[Any], str], right: tuple[str, str, list[Any], str],
) -> bool:
    return left[0] == right[0] and left[1] == right[1] and left[2] == right[2]


def _join_sql(
    new_table: str, existing_table: str, existing_col: str,
    right_table: str, right_col: str,
) -> str:
    return (
        f"\nINNER JOIN {_quote(new_table)} ON "
        f"{_qualified(existing_table, existing_col)} = {_qualified(right_table, right_col)}"
    )


def _render_predicate(column: str, operator: str, values: list[Any]) -> str:
    rendered_column = _qualified(*column.split(".", 1))
    if operator in {"IS_NULL", "IS_NOT_NULL"}:
        return f"{rendered_column} IS {'NOT ' if operator == 'IS_NOT_NULL' else ''}NULL"
    sql_op = {"EQ": "=", "NEQ": "<>", "GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=", "LIKE": "LIKE"}.get(operator)
    if sql_op and len(values) == 1:
        return f"{rendered_column} {sql_op} {_literal(values[0])}"
    if operator in {"IN", "NOT_IN"} and values:
        return f"{rendered_column} {'NOT ' if operator == 'NOT_IN' else ''}IN ({', '.join(_literal(v) for v in values)})"
    return "0 = 1"


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _quote(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _qualified(table: str, column: str) -> str:
    return f"{_quote(table)}.{_quote(column)}"


def _norm(value: Any) -> str:
    return re.sub(r"[^\w]+", "", str(value or "").casefold())
