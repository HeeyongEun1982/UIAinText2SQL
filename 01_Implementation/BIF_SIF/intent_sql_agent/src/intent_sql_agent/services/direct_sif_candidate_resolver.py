from __future__ import annotations

import copy
import re
from typing import Any

from intent_sql_agent.services.direct_sif_binding_intents import (
    build_binding_intents,
    match_where_intent,
)


def resolve_column_candidates(
    *,
    bundle: dict[str, Any],
    schema_catalog: dict[str, Any],
    original_question: str,
    benchmark_evidence: str | None,
    understanding: dict[str, Any] | None = None,
    confirmed_cqd: dict[str, Any] | None = None,
    technical_feedback: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recheck model-selected WHERE columns against catalog value candidates.

    Resolution is deterministic: maximal/full stored-value matches dominate
    substring matches.  Remaining candidates are selected only when catalog or
    evidence-backed table affinity gives one candidate a material lead.  Ties are
    returned as technical clarification issues rather than guessed.
    """
    result = copy.deepcopy(bundle)
    linking = result["table_linking"]
    plan = result["sql_plan"]
    selected_tables = {
        _norm(str(item.get("table") or "")): str(item.get("table") or "")
        for item in linking.get("selected_tables", []) or []
    }
    evidence = str(benchmark_evidence or "")
    intents = build_binding_intents(understanding or {}, confirmed_cqd)
    target_surfaces = [
        str(item.get("source_text") or "")
        for item in intents if item.get("role") == "SELECT"
    ]
    decisions: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for index, section in enumerate(plan.get("where_sections", []) or []):
        snippet = str(section.get("matched_snippet") or original_question)
        intent = match_where_intent(intents, snippet)
        operator = str(section.get("operator") or "").upper()
        literal_values = list(section.get("values") or [])
        if operator not in {"EQ", "NEQ", "IN", "NOT_IN", "LIKE", "BETWEEN"} or (
            not literal_values or any(value is None for value in literal_values)
        ):
            decisions.append({
                "where_index": index,
                "item_id": intent.get("item_id") if intent else None,
                "role": "WHERE",
                "status": "not_applicable",
                "reason": "operator_has_no_literal_value_candidate_resolution",
            })
            continue
        old_table, old_column = _split_column(str(section.get("column") or ""))
        old_values = list(section.get("values") or [])
        cqd_atom = _matching_cqd_atom(snippet, confirmed_cqd or {})
        locked = _catalog_grounding(cqd_atom, schema_catalog)
        if locked is not None:
            locked_table, locked_column = locked
            if _norm(locked_table) not in selected_tables:
                issues.append({
                    "type": "COLUMN_CANDIDATE_RELINK_REQUIRED",
                    "surface": snippet,
                    "reason": (
                        f"Confirmed CQD grounding {locked_table}.{locked_column} "
                        "is not connected by the current table-linking plan."
                    ),
                    "candidates": [f"{locked_table}.{locked_column}"],
                })
                decisions.append({
                    "where_index": index,
                    "status": "relink_required",
                    "original": {"table": old_table, "column": old_column},
                    "selected": {"table": locked_table, "column": locked_column},
                })
                continue
            locked_values = _grounded_values(cqd_atom, old_values)
            section["column"] = f"{locked_table}.{locked_column}"
            section["values"] = locked_values
            _replace_binding_and_grounding(
                result,
                old_table=old_table, old_column=old_column,
                new_table=locked_table, new_column=locked_column,
                new_value=locked_values[0] if locked_values else None,
                matched_snippet=snippet, target_surfaces=target_surfaces,
            )
            decisions.append({
                "where_index": index,
                "item_id": intent.get("item_id") if intent else None,
                "role": "WHERE", "status": "cqd_grounding_locked",
                "original": {"table": old_table, "column": old_column},
                "selected": {
                    "table": locked_table, "column": locked_column,
                    "values": locked_values,
                    "reasons": ["confirmed_cqd_physical_grounding"],
                },
                "candidates": [],
            })
            continue
        scoped_evidence = _scoped_evidence(evidence, snippet)
        evidence_tokens = _tokens(scoped_evidence)
        evidence_supported_tables = _evidence_supported_tables([section], scoped_evidence)
        source_context = _condition_source_context(
            snippet, confirmed_cqd or {},
        )
        candidates = _value_candidates(
            schema_catalog, " ".join(item for item in (snippet, source_context) if item)
        )
        if not candidates:
            continue
        maximum_value_tokens = max(
            len(_tokens(str(item.get("value") or ""))) for item in candidates
        )
        snippet_tokens = _tokens(snippet)
        other_tables = {
            _split_column(str(other.get("column") or ""))[0]
            for other_index, other in enumerate(plan.get("where_sections", []) or [])
            if other_index != index
        }
        scored = []
        for candidate in candidates:
            value_token_count = len(_tokens(str(candidate.get("value") or "")))
            maximal_value_match = value_token_count == maximum_value_tokens
            reasons = [
                "maximal_full_value_match"
                if maximal_value_match else "partial_value_match"
            ]
            table = candidate["table"]
            column = candidate["column"]
            column_tokens = _tokens(column) - {"name", "code", "id"}
            semantic_match = bool(column_tokens and column_tokens <= snippet_tokens)
            evidence_column_match = bool(
                _tokens(column) and _tokens(column) <= evidence_tokens
            )
            evidence_table_match = table in evidence_supported_tables
            colocated = table in other_tables
            if semantic_match:
                reasons.append("column_semantics_match_condition")
            if evidence_column_match:
                reasons.append("column_named_by_evidence")
            if evidence_table_match:
                reasons.append("evidence_supported_table_affinity")
            if colocated:
                reasons.append("co_located_with_another_confirmed_condition")
            scored.append({
                **candidate,
                "evidence_column_match": evidence_column_match,
                "semantic_match": semantic_match,
                "maximal_value_match": maximal_value_match,
                "evidence_table_match": evidence_table_match,
                "colocated": colocated,
                "value_token_count": value_token_count,
                "reasons": reasons,
            })
        scored.sort(key=lambda item: (item["table"], item["column"], str(item["value"])))
        forced_label = _selected_candidate_label(
            technical_feedback or [], snippet=snippet,
        )
        forced = [
            item for item in scored
            if _candidate_label(item) == forced_label
        ]
        leaders = forced if len(forced) == 1 else _select_leaders(scored)
        if len(leaders) != 1:
            candidate_labels = [
                f"{item['table']}.{item['column']} = {item['value']!r}"
                for item in leaders
            ]
            issues.append({
                "type": "CLARIFICATION_REQUIRED",
                "surface": snippet,
                "reason": (
                    "Multiple maximal value-matching columns remain equally supported "
                    "after schema and evidence evaluation."
                ),
                "candidates": candidate_labels,
            })
            decisions.append({
                "where_index": index,
                "status": "clarification_required",
                "original": {"table": old_table, "column": old_column, "values": section.get("values")},
                "candidates": scored,
            })
            continue

        chosen = leaders[0]
        if forced:
            chosen["reasons"] = list(chosen.get("reasons", [])) + [
                "selected_by_technical_clarification"
            ]
        candidate_labels = [
            f"{item['table']}.{item['column']} = {item['value']!r}"
            for item in leaders
        ]
        if _norm(chosen["table"]) not in selected_tables:
            issues.append({
                "type": "COLUMN_CANDIDATE_RELINK_REQUIRED",
                "surface": snippet,
                "reason": (
                    f"The supported candidate {chosen['table']}.{chosen['column']} is not "
                    "connected by the current table-linking plan."
                ),
                "candidates": [candidate_labels[0]],
            })
            decisions.append({
                "where_index": index,
                "status": "relink_required",
                "original": {"table": old_table, "column": old_column, "values": section.get("values")},
                "selected": chosen,
                "candidates": scored,
            })
            continue

        section["column"] = f"{chosen['table']}.{chosen['column']}"
        section["values"] = [chosen["value"]]
        _replace_binding_and_grounding(
            result,
            old_table=old_table,
            old_column=old_column,
            new_table=chosen["table"],
            new_column=chosen["column"],
            new_value=chosen["value"],
            matched_snippet=snippet,
            target_surfaces=target_surfaces,
        )
        changed = (
            _norm(old_table) != _norm(chosen["table"])
            or _norm(old_column) != _norm(chosen["column"])
            or old_values != [chosen["value"]]
        )
        decisions.append({
            "where_index": index,
            "item_id": intent.get("item_id") if intent else None,
            "role": "WHERE",
            "status": "auto_corrected" if changed else "confirmed",
            "original": {"table": old_table, "column": old_column},
            "selected": chosen,
            "candidates": scored,
        })

    return result, {
        "status": "clarification_required" if issues else "resolved",
        "decisions": decisions,
        "issues": issues,
    }


def _select_leaders(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply evidence as ordered constraints; conflicting signals stay ambiguous."""
    evidence_named = [item for item in candidates if item["evidence_column_match"]]
    if evidence_named:
        pool = evidence_named
    else:
        semantic = [item for item in candidates if item["semantic_match"]]
        maximal = [item for item in candidates if item["maximal_value_match"]]
        compatible = [
            item for item in candidates
            if item["semantic_match"] and item["maximal_value_match"]
        ]
        if compatible:
            pool = compatible
        elif semantic and maximal:
            # A dimension-word match and a longer stored-value match disagree.
            # Neither fact authorizes silently rewriting the other.
            return semantic + [item for item in maximal if item not in semantic]
        else:
            pool = maximal or semantic or list(candidates)

    for flag in ("evidence_table_match", "colocated"):
        supported = [item for item in pool if item[flag]]
        if supported:
            pool = supported
    longest = max((item["value_token_count"] for item in pool), default=0)
    return [item for item in pool if item["value_token_count"] == longest]


def _candidate_label(item: dict[str, Any]) -> str:
    return f"{item['table']}.{item['column']} = {item['value']!r}"


def _selected_candidate_label(
    feedback: list[dict[str, Any]], *, snippet: str,
) -> str | None:
    for item in reversed(feedback):
        if item.get("type") != "TECHNICAL_CLARIFICATION_RESPONSE":
            continue
        if _text(str(item.get("surface") or "")) != _text(snippet):
            continue
        response = item.get("response") or {}
        selected = list(response.get("selected_option_ids") or [])
        if len(selected) != 1:
            return None
        match = re.fullmatch(r"option_(\d+)", str(selected[0]), re.I)
        if match is None:
            return None
        labels = list(item.get("candidates") or [])
        index = int(match.group(1)) - 1
        return str(labels[index]) if 0 <= index < len(labels) else None
    return None


def _value_candidates(
    schema_catalog: dict[str, Any], snippet: str
) -> list[dict[str, Any]]:
    normalized_snippet = _text(snippet)
    result = []
    for table in schema_catalog.get("tables", []) or []:
        table_name = str(table.get("name") or "")
        for match in table.get("question_value_matches", []) or []:
            value = str(match.get("value") or "").strip()
            if value and _text(value) in normalized_snippet:
                result.append({
                    "table": table_name,
                    "column": str(match.get("column") or ""),
                    "value": match.get("value"),
                    "match": "value_in_condition_text",
                })
    unique = {}
    for item in result:
        key = (_norm(item["table"]), _norm(item["column"]), _text(str(item["value"])))
        unique[key] = item
    return list(unique.values())


def _condition_source_context(snippet: str, confirmed_cqd: dict[str, Any]) -> str:
    """Recover authoritative CQD source spans lost in a normalized SIF phrase."""
    snippet_tokens = _tokens(snippet)
    ranked: list[tuple[int, str]] = []
    for atom in (confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or []:
        if not isinstance(atom, dict):
            continue
        source = str(atom.get("source_text") or atom.get("source_span") or "").strip()
        if not source:
            continue
        signature = " ".join((
            str(atom.get("dimension") or ""),
            str(atom.get("value") or ""),
            source,
        ))
        overlap = len(snippet_tokens & _tokens(signature))
        if overlap:
            ranked.append((overlap, source))
    if not ranked:
        return ""
    best = max(score for score, _ in ranked)
    return " ".join(dict.fromkeys(source for score, source in ranked if score == best))


def _matching_cqd_atom(
    snippet: str, confirmed_cqd: dict[str, Any]
) -> dict[str, Any] | None:
    snippet_tokens = _tokens(snippet)
    ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for atom in (confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or []:
        if not isinstance(atom, dict):
            continue
        dimension = _tokens(str(atom.get("dimension") or ""))
        value = _tokens(str(atom.get("value") or ""))
        source = _tokens(str(atom.get("source_text") or atom.get("source_span") or ""))
        signature = dimension | value | source
        overlap = len(snippet_tokens & signature)
        if not overlap:
            continue
        ranked.append(((
            int(bool(dimension) and dimension <= snippet_tokens),
            int(bool(value) and value <= snippet_tokens),
            overlap,
        ), atom))
    if not ranked:
        return None
    best = max(rank for rank, _ in ranked)
    leaders = [atom for rank, atom in ranked if rank == best]
    return leaders[0] if len(leaders) == 1 else None


def _catalog_grounding(
    atom: dict[str, Any] | None, schema_catalog: dict[str, Any]
) -> tuple[str, str] | None:
    grounding = (atom or {}).get("grounding") or {}
    raw_table = str(grounding.get("table") or "")
    raw_column = str(grounding.get("column") or "")
    if not raw_table or not raw_column:
        return None
    for table in schema_catalog.get("tables", []) or []:
        table_name = str(table.get("name") or "")
        if _norm(table_name) != _norm(raw_table):
            continue
        for column in table.get("columns", []) or []:
            column_name = str(column.get("name") or "")
            if _norm(column_name) == _norm(raw_column):
                return table_name, column_name
    return None


def _grounded_values(
    atom: dict[str, Any] | None, fallback: list[Any]
) -> list[Any]:
    grounding = (atom or {}).get("grounding") or {}
    physical = grounding.get("physical_value")
    if physical is not None:
        return [physical]
    logical = (atom or {}).get("value")
    if isinstance(logical, str):
        encoded = re.fullmatch(r"[^=]+\s*=\s*(.+)", logical.strip())
        if encoded:
            value = encoded.group(1).strip()
            return [int(value) if re.fullmatch(r"[-+]?\d+", value) else value]
    return list(fallback)


def _evidence_supported_tables(
    sections: list[dict[str, Any]], evidence: str
) -> set[str]:
    evidence_normalized = _norm(evidence)
    supported = set()
    for section in sections:
        table, column = _split_column(str(section.get("column") or ""))
        if table and column and _norm(column) in evidence_normalized:
            supported.add(table)
    return supported


def _replace_binding_and_grounding(
    bundle: dict[str, Any], *, old_table: str, old_column: str,
    new_table: str, new_column: str, new_value: Any,
    matched_snippet: str, target_surfaces: list[str],
) -> None:
    linking = bundle["table_linking"]
    bindings = linking.setdefault("column_bindings", {})
    bindings[new_column] = new_table
    used_columns = {
        _norm(_split_column(str(item.get("column") or ""))[1])
        for item in bundle["sql_plan"].get("where_sections", []) or []
    }
    used_columns.update(
        _norm(str(item.get("physicalName") or ""))
        for item in bundle["confirmed_grounding"]["confirmed"].get("terms", []) or []
    )
    if _norm(old_column) not in used_columns and old_column != new_column:
        bindings.pop(old_column, None)
    confirmed = bundle["confirmed_grounding"]["confirmed"]
    updated_term_ids: set[str] = set()
    for term in confirmed.get("terms", []) or []:
        surface = str(term.get("surface") or "")
        protects_target = any(_semantic_match(surface, target) for target in target_surfaces)
        if (
            _norm(str(term.get("physicalName") or "")) == _norm(old_column)
            and _semantic_match(surface, matched_snippet)
            and not protects_target
        ):
            term["physicalName"] = new_column
            term["selectionRule"] = "DETERMINISTIC_CANDIDATE_RESOLUTION"
            updated_term_ids.add(str(term.get("termId") or ""))
    for code in confirmed.get("codes", []) or []:
        if (
            _norm(str(code.get("physicalName") or "")) == _norm(old_column)
            and str(code.get("termId") or "") in updated_term_ids
        ):
            code["physicalName"] = new_column
            code["code"] = new_value


def _scoped_evidence(evidence: str, snippet: str) -> str:
    snippet_tokens = _tokens(snippet)
    clauses = [item.strip() for item in re.split(r"[;\n]+", evidence) if item.strip()]
    matching = [
        clause for clause in clauses
        if len(_tokens(clause) & snippet_tokens) >= 2
    ]
    return "; ".join(matching)


def _semantic_match(left: str, right: str) -> bool:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    return bool(left_tokens) and (
        left_tokens <= right_tokens or right_tokens <= left_tokens
    )


def _split_column(value: str) -> tuple[str, str]:
    if "." not in value:
        return "", value
    table, column = value.split(".", 1)
    return table, column


def _text(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _norm(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())
