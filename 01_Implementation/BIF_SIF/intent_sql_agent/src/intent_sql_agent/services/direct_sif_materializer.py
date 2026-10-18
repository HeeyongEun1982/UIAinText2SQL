from __future__ import annotations

import copy
import re
from collections import deque
from difflib import SequenceMatcher
from typing import Any


def compile_understanding_from_cqd(
    *, original_question: str, confirmed_cqd: dict[str, Any]
) -> dict[str, Any]:
    """Project confirmed business meaning into the public understanding format.

    This is intentionally a projection, not a model generation step.  Every
    executable phrase is copied from a CQD value or its source span.
    """
    populations = list(confirmed_cqd.get("populations", []) or [])
    outputs = list(confirmed_cqd.get("outputs", []) or [])
    selections = list(confirmed_cqd.get("selections", []) or [])
    atoms = list((confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or [])

    anchor = _first_text(
        population.get("entity") if isinstance(population, dict) else population
        for population in populations
    ) or "result rows"
    targets = _unique(
        _output_text(item) for item in [*outputs, *selections]
        if _output_text(item)
    )
    fields = list(targets)
    values: list[str] = []
    conditions: list[str] = []
    scopes: list[str] = []

    for population in populations:
        if not isinstance(population, dict):
            scopes.append(str(population))
            continue
        entity = str(population.get("entity") or population.get("population") or "").strip()
        if entity:
            scopes.append(entity)
            # A modified entity such as "charter schools" carries a restrictive
            # business class.  A bare entity such as "client" is only an anchor.
            if (
                len(_tokens(entity)) > 1
                and not re.search(r"\b(?:or|and/or)\b", entity, flags=re.I)
            ):
                conditions.append(entity)
        description = str(population.get("description") or "").strip()
        if description:
            scopes.append(description)
        for key, value in population.items():
            if key in {
                "entity", "population", "description", "source_text", "source_span",
                "entity_type", "role", "of_entity",
            } or value in (None, "", [], {}):
                continue
            if key in {"attributes", "filters", "conditions", "constraints"} and isinstance(value, dict):
                # Container aliases may repeat the canonical predicate atoms.
                # They are descriptive structure, never one scalar SQL literal.
                continue
            structured_field = (
                str(
                    value.get("dimension") or value.get("attribute")
                    or value.get("field") or ""
                )
                if isinstance(value, dict) else ""
            )
            structured_value = value.get("value") if isinstance(value, dict) else value
            fields.append(structured_field or str(key).replace("_", " "))
            values.append(_render_value(structured_value))
            conditions.append(
                str(
                    (value.get("source_text") or value.get("source_span"))
                    if isinstance(value, dict) else ""
                )
                or str(population.get("source_text") or population.get("source_span")
                    or f"{structured_field or str(key).replace('_', ' ')} = {_render_value(structured_value)}")
            )

    for atom in atoms:
        if not isinstance(atom, dict):
            continue
        dimension = str(atom.get("dimension") or "").strip()
        value = atom.get("value")
        if dimension:
            fields.append(dimension)
        if value not in (None, "", [], {}):
            values.append(_render_value(value))
        conditions.append(
            str(atom.get("source_text") or atom.get("source_span") or _atom_text(atom))
        )

    for root in ("time_constraints", "comparisons", "scopes"):
        for item in confirmed_cqd.get(root, []) or []:
            text = _source_or_render(item)
            if not text:
                continue
            (scopes if root == "scopes" else conditions).append(text)

    return {
        "normalized_query": str(confirmed_cqd.get("rendered_text") or original_question),
        "primary_subject_anchor": anchor,
        "anchor_reasoning": "Projected from the confirmed CQD population.",
        "targets": _unique(targets),
        "field_phrases": _unique(fields),
        "value_phrases": _unique(values),
        "conditions_text": _unique(conditions),
        "scope_text": _unique(scopes),
        "ambiguous_phrases": [],
    }


def materialize_grounding_bundle(
    *,
    original_question: str,
    benchmark_evidence: str | None,
    confirmed_cqd: dict[str, Any],
    understanding: dict[str, Any],
    schema_catalog: dict[str, Any],
    technical_feedback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the physical SIF by code from catalog-bounded candidates.

    No model-authored table, column, operator, value, join, or SQL-plan structure
    is accepted.  A unique score leader is selected; unresolved ties are exposed
    as clarification choices.
    """
    del original_question  # CQD/source spans are the authoritative semantics.
    tables = list(schema_catalog.get("tables", []) or [])
    evidence = str(benchmark_evidence or "")
    feedback = list(technical_feedback or [])
    terms: list[dict[str, Any]] = []
    codes: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    selected_columns: list[tuple[str, str]] = []
    select_sections: list[dict[str, Any]] = []
    where_sections: list[dict[str, Any]] = []

    output_specs = _output_specs(confirmed_cqd, understanding)
    predicate_specs = _predicate_specs(confirmed_cqd, understanding)
    for role, specs in (("SELECT", output_specs), ("WHERE", predicate_specs)):
        for index, spec in enumerate(specs):
            item_id = f"{role.lower()}:{index}"
            candidates = _column_candidates(
                spec=spec, tables=tables, evidence=evidence, role=role,
            )
            chosen = _choose_candidate(
                candidates, item_id=item_id, surface=spec["surface"], feedback=feedback,
            )
            if chosen is None:
                labels = [candidate["label"] for candidate in candidates[:5]]
                unresolved.append({
                    "surface": spec["surface"],
                    "status": (
                        "CLARIFICATION_REQUIRED" if len(labels) >= 2 else "UNRESOLVED_SYSTEM"
                    ),
                    "reason": (
                        "Catalog-bounded candidates do not have one materially supported leader."
                    ),
                    "candidates": labels,
                })
                continue
            table, column = chosen["table"], chosen["column"]
            selected_columns.append((table, column))
            term_id = item_id
            terms.append({
                "physicalName": column,
                "termId": term_id,
                "surface": spec["surface"],
                "selectionRule": chosen["selection_rule"],
            })
            qualified = f"{table}.{column}"
            if role == "SELECT":
                select_sections.append({
                    "column": qualified, "matched_snippet": spec["surface"],
                })
                continue
            literal_values = _literal_values(spec, chosen, evidence)
            raw_operator = str(spec.get("operator") or "=").strip().casefold()
            if raw_operator in {"between", "range", "from_to"} and len(literal_values) == 2:
                where_sections.extend([
                    {
                        "column": qualified, "operator": "GTE",
                        "values": [literal_values[0]], "is_negation": False,
                        "matched_snippet": spec["surface"],
                    },
                    {
                        "column": qualified, "operator": "LTE",
                        "values": [literal_values[1]], "is_negation": False,
                        "matched_snippet": spec["surface"],
                    },
                ])
            else:
                operator = _operator(spec.get("operator"))
                where_sections.append({
                    "column": qualified,
                    "operator": operator,
                    "values": literal_values,
                    "is_negation": operator in {"NEQ", "NOT_IN"},
                    "matched_snippet": spec["surface"],
                })
            for literal_value in literal_values:
                codes.append({
                    "physicalName": column,
                    "termId": term_id,
                    "code": literal_value,
                    "label": _render_value(spec.get("value")),
                    "sourceText": spec["surface"],
                })

    root = _root_table(selected_columns, confirmed_cqd)
    selected_tables, joins, disconnected = _connect_tables(
        root=root, required={table for table, _ in selected_columns}, tables=tables,
        semantic_context=" ".join([
            str(understanding.get("primary_subject_anchor") or ""),
            *[str(value) for value in understanding.get("targets", []) or []],
            *[str(value) for value in understanding.get("conditions_text", []) or []],
        ]),
    )
    for table in disconnected:
        unresolved.append({
            "surface": table,
            "status": "UNRESOLVED_SYSTEM",
            "reason": "No foreign-key path connects a selected physical source.",
            "candidates": [table],
        })
    bindings: dict[str, str] = {}
    for table, column in selected_columns:
        bindings[column] = table
    return {
        "confirmed_grounding": {"confirmed": {
            "terms": terms, "codes": codes, "unresolved": unresolved,
        }},
        "table_linking": {
            "root_anchor": root,
            "selected_tables": selected_tables,
            "joins": joins,
            "column_bindings": bindings,
            "reasoning": [
                "Columns were selected from catalog-bounded candidates; joins were derived from PK/FK paths."
            ],
            "diagnostics": {"missing_columns": []},
        },
        "sql_plan": {
            "select_section": select_sections,
            "where_sections": where_sections,
            "group_by_section": [],
            "order_by_section": [],
            "limit_section": {"count": None, "matched_snippet": ""},
            "dedupe_section": {"key_column": None, "policy": None, "matched_snippet": ""},
        },
    }


def _output_specs(cqd: dict[str, Any], understanding: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in [*(cqd.get("outputs", []) or []), *(cqd.get("selections", []) or [])]:
        if _is_control_selection(item):
            continue
        surface = _output_text(item)
        if surface and not any(_norm(surface) == _norm(existing["surface"]) for existing in result):
            entity = str(item.get("entity") or "") if isinstance(item, dict) else ""
            result.append({
                "surface": surface,
                "context": " ".join(value for value in (_source_or_render(item), entity) if value),
            })
    if not result:
        result = [{"surface": str(value), "context": str(value)} for value in understanding.get("targets", []) or []]
    return result


def _predicate_specs(cqd: dict[str, Any], understanding: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    atoms = [
        atom for atom in (cqd.get("predicates", {}) or {}).get("atoms", []) or []
        if isinstance(atom, dict)
    ]
    atom_tokens = [
        _tokens(" ".join(str(atom.get(key) or "") for key in (
            "dimension", "value", "source_text", "source_span",
        )))
        for atom in atoms
    ]
    for population in cqd.get("populations", []) or []:
        if not isinstance(population, dict):
            continue
        entity = str(population.get("entity") or "").strip()
        if (
            entity and len(_tokens(entity)) > 1
            and not re.search(r"\b(?:or|and/or)\b", entity, flags=re.I)
            and not any(_tokens(entity) <= represented for represented in atom_tokens)
        ):
            result.append({
                "surface": entity, "dimension": entity, "operator": "=", "value": True,
                "context": entity,
            })
        for key, value in population.items():
            if key in {
                "entity", "population", "description", "source_text", "source_span",
                "entity_type", "role", "of_entity",
            } or value in (None, "", [], {}):
                continue
            if key in {"attributes", "filters", "conditions", "constraints"} and isinstance(value, dict):
                continue
            if isinstance(value, dict) and (
                "value" in value and any(
                    key_name in value
                    for key_name in ("dimension", "attribute", "field", "operator")
                )
            ):
                result.append({
                    "surface": str(value.get("source_text") or value.get("source_span")
                                   or _atom_text(value)),
                    "dimension": str(
                        value.get("dimension") or value.get("attribute")
                        or value.get("field") or key
                    ).replace("_", " "),
                    "operator": value.get("operator") or "=",
                    "value": copy.deepcopy(value.get("value")),
                    "context": _source_or_render(value),
                    "grounding": copy.deepcopy(value.get("grounding") or {}),
                })
                continue
            surface = str(population.get("source_text") or population.get("source_span")
                          or f"{str(key).replace('_', ' ')} = {_render_value(value)}")
            result.append({
                "surface": surface, "dimension": str(key).replace("_", " "),
                "operator": "=", "value": copy.deepcopy(value), "context": surface,
            })
    for atom in atoms:
        if _is_output_owner_scope(atom, cqd):
            continue
        result.append({
            "surface": str(atom.get("source_text") or atom.get("source_span") or _atom_text(atom)),
            "dimension": str(atom.get("dimension") or ""),
            "operator": atom.get("operator"), "value": copy.deepcopy(atom.get("value")),
            "context": " ".join(str(atom.get(key) or "") for key in (
                "dimension", "applies_to", "source_text", "source_span",
            )),
            "grounding": copy.deepcopy(atom.get("grounding") or {}),
        })
    for root in ("time_constraints", "comparisons"):
        for item in cqd.get(root, []) or []:
            if not isinstance(item, dict):
                continue
            is_range = (
                item.get("start_date") is not None and item.get("end_date") is not None
            )
            result.append({
                "surface": str(item.get("source_text") or item.get("source_span") or _atom_text(item)),
                "dimension": str(
                    item.get("dimension") or item.get("time_dimension")
                    or item.get("attribute") or ""
                ),
                "operator": "BETWEEN" if is_range else (item.get("operator") or "="),
                "value": (
                    [copy.deepcopy(item.get("start_date")), copy.deepcopy(item.get("end_date"))]
                    if is_range else copy.deepcopy(item.get("value"))
                ),
                "context": _source_or_render(item),
                "grounding": copy.deepcopy(item.get("grounding") or {}),
            })
    # ``understanding`` is itself a deterministic CQD projection.  Re-parsing its
    # rendered strings would duplicate structured atoms or turn objects into SQL
    # literal values, so physical predicates are sourced only from CQD structures.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in result:
        key = (
            _norm(str(item.get("dimension") or item.get("surface") or "")),
            _operator(item.get("operator")),
            _norm(_render_value(item.get("value"))),
        )
        previous = unique.get(key)
        if previous is None or (
            item.get("grounding") and not previous.get("grounding")
        ):
            unique[key] = item
    return list(unique.values())


def _column_candidates(
    *, spec: dict[str, Any], tables: list[dict[str, Any]], evidence: str, role: str,
) -> list[dict[str, Any]]:
    grounding = spec.get("grounding") or {}
    locked_table = str(grounding.get("table") or "")
    locked_column = str(grounding.get("column") or "")
    query_tokens = _tokens(" ".join((
        str(spec.get("surface") or ""), str(spec.get("dimension") or ""),
        str(spec.get("context") or ""),
    )))
    value_text = _render_value(spec.get("value"))
    evidence_norm = _norm(evidence)
    result = []
    for table in tables:
        table_name = str(table.get("name") or "")
        table_tokens = _tokens(table_name)
        value_matches = table.get("question_value_matches", []) or []
        for column in table.get("columns", []) or []:
            column_name = str(column.get("name") or "")
            if locked_table and locked_column:
                if _norm(table_name) != _norm(locked_table) or _norm(column_name) != _norm(locked_column):
                    continue
            semantic_keys = ["name", "semantic_name", "description"]
            # Value descriptions describe stored literals.  They are useful for
            # predicates, but words such as "valid" must not make an unrelated
            # status/code column compete with an output such as "valid email".
            if role == "WHERE":
                semantic_keys.append("value_description")
            column_text = " ".join(
                str(column.get(key) or "") for key in semantic_keys
            )
            column_tokens = _tokens(column_text)
            overlap = len(query_tokens & column_tokens)
            table_overlap = len(query_tokens & table_tokens)
            score = overlap * 12 + table_overlap * (4 if role == "SELECT" else 8)
            reasons = []
            if overlap:
                reasons.append("semantic_overlap")
            dimension_text = str(spec.get("dimension") or spec.get("surface") or "")
            dimension_tokens = _tokens(dimension_text)
            column_name_tokens = _tokens(column_name)
            identifier_tokens = (
                column_name_tokens
                | _tokens(str(column.get("semantic_name") or ""))
                | table_tokens
            )
            if dimension_tokens and dimension_tokens <= identifier_tokens:
                score += 45
                reasons.append("dimension_phrase_match")
            if column_name_tokens and column_name_tokens <= dimension_tokens:
                score += 30
                reasons.append("dimension_names_column")
            normalized_dimension = _norm(dimension_text)
            comparable = [
                _norm(str(column.get(key) or ""))
                for key in ("name", "semantic_name", "description")
            ]
            similarity = max(
                (
                    SequenceMatcher(None, normalized_dimension, candidate).ratio()
                    if _common_prefix_length(normalized_dimension, candidate) >= 4 else 0.0
                )
                for candidate in comparable
            )
            if similarity >= 0.68:
                score += int(similarity * 35)
                reasons.append("identifier_description_similarity")
            scoped_evidence_norm = _norm(_scoped_evidence(evidence, str(spec.get("surface") or "")))
            if _norm(column_name) and _norm(column_name) in scoped_evidence_norm:
                score += 35
                reasons.append("evidence_names_column")
            matching_values = [
                match for match in value_matches
                if _norm(str(match.get("column") or "")) == _norm(column_name)
                and value_text and (
                    _norm(str(match.get("value") or "")) == _norm(value_text)
                    or _norm(str(match.get("value") or "")) in _norm(str(spec.get("surface") or ""))
                )
            ]
            if matching_values:
                score += 70
                reasons.append("catalog_value_match")
            if locked_table and locked_column:
                score += 1000
                reasons.append("confirmed_cqd_grounding")
            if role == "SELECT" and column.get("primary_key") and overlap == 0:
                score -= 5
            if score <= 0:
                continue
            result.append({
                "candidate_id": f"{table_name}.{column_name}",
                "label": f"{table_name}.{column_name}",
                "table": table_name, "column": column_name, "score": score,
                "reasons": reasons, "matching_values": matching_values,
            })
    result.sort(key=lambda item: (-item["score"], item["table"], item["column"]))
    return result[:8]


def _choose_candidate(
    candidates: list[dict[str, Any]], *, item_id: str, surface: str,
    feedback: list[dict[str, Any]],
) -> dict[str, Any] | None:
    labels = [item["label"] for item in candidates[:5]]
    forced = _feedback_choice(feedback, surface=surface, labels=labels)
    if forced:
        chosen = next((item for item in candidates if item["label"] == forced), None)
        if chosen:
            return {**chosen, "selection_rule": "TECHNICAL_CLARIFICATION"}
    if not candidates:
        return None
    leader = candidates[0]
    if len(candidates) == 1 or leader["score"] >= candidates[1]["score"] + 5:
        return {**leader, "selection_rule": "DETERMINISTIC_CATALOG_RANKING"}
    tied = [
        item for item in candidates
        if leader["score"] - item["score"] < 5
    ]
    family_keys = {_numbered_family_key(item) for item in tied}
    if len(tied) >= 2 and None not in family_keys and len(family_keys) == 1:
        return {**leader, "selection_rule": "NUMBERED_COLUMN_FAMILY_ANCHOR"}
    return None


def _literal_values(spec: dict[str, Any], candidate: dict[str, Any], evidence: str) -> list[Any]:
    grounding = spec.get("grounding") or {}
    if grounding.get("physical_value") is not None:
        return [grounding["physical_value"]]
    matches = candidate.get("matching_values") or []
    if len(matches) == 1:
        return [matches[0].get("value")]
    documented = _evidence_code(evidence, candidate["column"])
    if documented is not None:
        return [documented]
    value = spec.get("value")
    if value is True:
        return [documented if documented is not None else 1]
    if isinstance(value, str):
        encoded = re.fullmatch(r"[^=]+\s*=\s*(.+)", value.strip())
        if encoded:
            raw = encoded.group(1).strip()
            if re.fullmatch(r"[-+]?\d+", raw):
                return [int(raw)]
            if re.fullmatch(r"[-+]?\d+\.\d+", raw):
                return [float(raw)]
            return [raw]
    if isinstance(value, list):
        return copy.deepcopy(value)
    return [] if value is None else [copy.deepcopy(value)]


def _connect_tables(
    *, root: str | None, required: set[str], tables: list[dict[str, Any]],
    semantic_context: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    if not root and required:
        root = sorted(required)[0]
    if not root:
        return [], [], []
    graph: dict[str, list[tuple[str, dict[str, str], str]]] = {}
    for table in tables:
        source = str(table.get("name") or "")
        graph.setdefault(source, [])
        for fk in table.get("foreign_keys", []) or []:
            target = str(fk.get("target_table") or "")
            edge = {
                "from": source, "to": target,
                "from_col": str(fk.get("from_column") or ""),
                "to_col": str(fk.get("to_column") or ""),
            }
            graph.setdefault(source, []).append((target, edge, "to_parent"))
            graph.setdefault(target, []).append((source, edge, "to_child"))
    included = {root}
    joins: list[dict[str, str]] = []
    disconnected: list[str] = []
    for target in sorted(required - {root}):
        queue = deque([(root, [], [], {root})])
        paths: list[tuple[int, int, list[dict[str, str]]]] = []
        while queue:
            node, edges, directions, seen = queue.popleft()
            if node == target:
                sibling_parent_penalty = sum(
                    left == "to_parent" and right == "to_child"
                    for left, right in zip(directions, directions[1:])
                )
                context_tokens = _tokens(semantic_context)
                relevance = sum(
                    len(context_tokens & _tokens(" ".join((
                        edge["from_col"], edge["to_col"],
                    ))))
                    for edge in edges
                )
                paths.append((
                    len(edges) + sibling_parent_penalty * 3, relevance, edges,
                ))
                continue
            if len(edges) >= 6:
                continue
            for neighbor, edge, direction in graph.get(node, []):
                if neighbor not in seen:
                    queue.append((
                        neighbor, [*edges, edge], [*directions, direction],
                        {*seen, neighbor},
                    ))
        if not paths:
            disconnected.append(target)
            continue
        _, _, path = min(paths, key=lambda item: (
            item[0], -item[1], len(item[2]),
            tuple((edge["from"], edge["to"]) for edge in item[2]),
        ))
        for edge in path:
            if edge not in joins:
                joins.append(edge)
            included.update((edge["from"], edge["to"]))
    ordered = [root] + sorted(included - {root})
    return [
        {"table": table, "tier": index + 1, "role": "root" if index == 0 else "join"}
        for index, table in enumerate(ordered)
    ], joins, disconnected


def _root_table(columns: list[tuple[str, str]], cqd: dict[str, Any]) -> str | None:
    output_count = len(_output_specs(cqd, {"targets": []}))
    return columns[0][0] if columns and output_count else (columns[0][0] if columns else None)


def _operator(value: Any) -> str:
    normalized = str(value or "=").strip().casefold()
    return {
        "=": "EQ", "==": "EQ", "equals": "EQ", "eq": "EQ",
        "!=": "NEQ", "<>": "NEQ", "not equals": "NEQ",
        ">": "GT", ">=": "GTE", "<": "LT", "<=": "LTE",
        "in": "IN", "not in": "NOT_IN", "like": "LIKE",
        "between": "BETWEEN", "range": "BETWEEN", "from_to": "BETWEEN",
        "is null": "IS_NULL", "is not null": "IS_NOT_NULL",
    }.get(normalized, "EQ")


def _feedback_choice(feedback: list[dict[str, Any]], *, surface: str, labels: list[str]) -> str | None:
    for item in reversed(feedback):
        if str(item.get("surface") or "").casefold() != surface.casefold():
            continue
        if [str(value) for value in item.get("candidates", []) or []] != labels:
            continue
        selected = list((item.get("response") or {}).get("selected_option_ids") or [])
        if len(selected) != 1:
            continue
        match = re.fullmatch(r"option_(\d+)", str(selected[0]), re.I)
        if match and 0 < int(match.group(1)) <= len(labels):
            return labels[int(match.group(1)) - 1]
    return None


def _numbered_family_key(candidate: dict[str, Any]) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+?)(\d+)", str(candidate.get("column") or ""))
    if not match:
        return None
    return _norm(str(candidate.get("table") or "")), _norm(match.group(1))


def _evidence_code(evidence: str, column: str) -> Any:
    match = re.search(
        rf"(?<![\w]){re.escape(column)}(?![\w])\s*=\s*"
        r"(['\"]?)([-+]?\d+(?:\.\d+)?|true|false)\1",
        evidence,
        re.I,
    )
    if not match:
        return None
    raw = match.group(2)
    if raw.casefold() in {"true", "false"}:
        return raw.casefold() == "true"
    return float(raw) if "." in raw else int(raw)


def _scoped_evidence(evidence: str, surface: str) -> str:
    surface_tokens = _tokens(surface)
    clauses = [part.strip() for part in re.split(r"[;\n]+", evidence) if part.strip()]
    matches = [
        clause for clause in clauses
        if len(surface_tokens & _tokens(clause)) >= 2
    ]
    return "; ".join(matches)


def _common_prefix_length(left: str, right: str) -> int:
    length = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        length += 1
    return length


def _output_text(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item or "").strip()
    for key in (
        "attribute", "content", "field", "dimension", "selection", "target",
        "output", "value", "description"
    ):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return _render_value(value)
    output_type = item.get("output_type")
    if output_type not in (None, "", [], {}) and _norm(str(output_type)) not in {
        "list", "single", "singlevalue", "value", "attribute", "row", "rows",
        "table", "scalar", "collection",
    }:
        return _render_value(output_type)
    return ""


def _is_control_selection(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    kind = _norm(str(item.get("selection_type") or item.get("type") or ""))
    if kind not in {"top", "bottom", "limit", "first", "last", "rank"}:
        return False
    return not any(
        item.get(key) not in (None, "", [], {})
        for key in ("attribute", "content", "field", "dimension", "target", "output", "value")
    )


def _is_output_owner_scope(atom: dict[str, Any], cqd: dict[str, Any]) -> bool:
    """Recognize ownership/role identity as scope, not a row predicate."""
    dimension = _norm(str(atom.get("dimension") or ""))
    if not (dimension.endswith("role") or dimension in {"owner", "ownership"}):
        return False
    value = _norm(_render_value(atom.get("value")))
    if not value:
        return False
    output_entities = {
        _norm(str(item.get("entity") or item.get("of_entity") or ""))
        for item in cqd.get("outputs", []) or []
        if isinstance(item, dict)
    }
    return value in output_entities


def _source_or_render(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("source_text") or item.get("source_span") or _render_value(item))
    return _render_value(item)


def _atom_text(atom: dict[str, Any]) -> str:
    return " ".join(str(value) for value in (
        atom.get("dimension"), atom.get("operator"), _render_value(atom.get("value")),
    ) if value not in (None, ""))


def _render_value(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_render_value(item)}" for key, item in value.items() if item not in (None, "", [], {}))
    if isinstance(value, list):
        return ", ".join(_render_value(item) for item in value)
    return str(value if value is not None else "")


def _first_text(values: Any) -> str:
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


def _unique(values: Any) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _tokens(value: str) -> set[str]:
    ignored = {"a", "an", "the", "of", "for", "is", "are", "to", "all", "every", "when", "was"}
    expanded = re.sub(r"\be[\s-]?mail\b", "email", str(value), flags=re.I)
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", expanded)
    expanded = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", expanded)
    return {
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
        for token in re.findall(r"[^\W_]+", expanded.casefold(), flags=re.UNICODE)
        if token not in ignored
    }


def _norm(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value).casefold())
