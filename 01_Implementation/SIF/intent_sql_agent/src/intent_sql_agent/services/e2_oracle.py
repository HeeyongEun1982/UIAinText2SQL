from __future__ import annotations

import re
from typing import Any


_TABLE_RE = re.compile(r"\b(?:from|join)\s+[`\"\[]?([A-Za-z_][A-Za-z0-9_]*)", re.I)
_QUALIFIED_COLUMN_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*(?:`([^`]+)`|\"([^\"]+)\"|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))"
)


def build_e2_oracle_context(
    gold_sql: str,
    clarification: dict[str, Any],
    source_ambiguities: list[dict[str, Any]] | None = None,
    *,
    original_question: str = "",
) -> dict[str, Any]:
    """Build restricted, machine-checkable guidance for the simulated user."""
    request = clarification.get("clarification_request", {})
    options = request.get("options", [])
    if clarification.get("decision") is None:
        affirmative = _affirmative_option(options)
        if affirmative:
            return {
                "resolution_method": "no_gap_confirmation",
                "oracle_target_option_ids": [affirmative],
                "option_resolution_map": [],
            }

    if source_ambiguities:
        context = _resolve_source_population(gold_sql, source_ambiguities, options)
        if context:
            return context
    role_context = _resolve_option_from_gold_columns(gold_sql, options)
    if role_context:
        return role_context
    logic_context = _resolve_explicit_question_logic(
        original_question, request, options
    )
    if logic_context:
        return logic_context
    gold_logic_context = _resolve_gold_boolean_logic(gold_sql, request, options)
    if gold_logic_context:
        return gold_logic_context
    presentation_context = _resolve_output_presentation(gold_sql, options)
    if presentation_context:
        return presentation_context
    return {
        "resolution_method": "gold_semantic_inference",
        "oracle_target_option_ids": [],
        "option_resolution_map": [],
    }


def enforce_oracle_response(
    response: dict[str, Any],
    *,
    mode: str,
    request_id: str,
    oracle_context: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    targets = list(oracle_context.get("oracle_target_option_ids") or [])
    if not targets:
        return response, False
    canonical = {
        "request_id": request_id,
        "mode": mode,
        "action": "select",
        "selected_option_ids": targets,
        "free_text": None,
        "confidence": "high",
    }
    if response == canonical:
        return response, False
    return canonical, True


def _resolve_source_population(
    gold_sql: str,
    ambiguities: list[dict[str, Any]],
    options: list[dict[str, Any]],
) -> dict[str, Any] | None:
    option_ids = [str(option.get("option_id")) for option in options]
    tables = {match.lower() for match in _TABLE_RE.findall(gold_sql)}
    columns = {
        next(value for value in match if value).strip().lower()
        for match in _QUALIFIED_COLUMN_RE.findall(gold_sql)
    }
    resolution_map = []
    for ambiguity in ambiguities:
        for index, candidate in enumerate(ambiguity.get("candidates", [])):
            if index >= len(option_ids):
                continue
            option_id = option_ids[index]
            table = str(candidate.get("table") or "")
            mapped_columns = [
                str(mapping.get("column") or "")
                for mapping in candidate.get("condition_mappings", [])
            ]
            table_match = table.lower() in tables
            matched_columns = (
                [column for column in mapped_columns if column.lower() in columns]
                if table_match
                else []
            )
            # A same-named column in another table is not evidence for this
            # candidate. Gold table membership is authoritative for source choice.
            score = (100 if table_match else 0) + 10 * len(matched_columns)
            resolution_map.append(
                {
                    "option_id": option_id,
                    "table": table,
                    "condition_columns": mapped_columns,
                    "table_match_in_gold": table_match,
                    "matched_condition_columns_in_gold": matched_columns,
                    "score": score,
                }
            )
    if not resolution_map:
        return None
    best_score = max(item["score"] for item in resolution_map)
    winners = [item for item in resolution_map if item["score"] == best_score]
    targets = [winners[0]["option_id"]] if len(winners) == 1 and best_score > 3 else []
    return {
        "resolution_method": "gold_identifier_to_source_candidate",
        "oracle_target_option_ids": targets,
        "option_resolution_map": resolution_map,
    }


def _affirmative_option(options: list[dict[str, Any]]) -> str | None:
    preferred_ids = {"yes", "confirm", "confirmed", "correct", "approve", "approved"}
    for option in options:
        option_id = str(option.get("option_id") or "").strip().lower()
        label = str(option.get("label") or "").strip().lower()
        if option_id in preferred_ids or label.startswith(("yes", "confirm", "correct", "approve")):
            return str(option.get("option_id"))
    return None


def _resolve_output_presentation(
    gold_sql: str, options: list[dict[str, Any]]
) -> dict[str, Any] | None:
    roles: dict[str, str] = {}
    for option in options:
        option_id = str(option.get("option_id") or "")
        text = _normalized_words(f"{option_id} {option.get('label') or ''}")
        if any(marker in text for marker in ("separate column", "side by side", "same row")):
            roles["separate_columns"] = option_id
        if any(marker in text for marker in ("repeated row", "one per row", "single column")):
            roles["repeated_rows"] = option_id
    if set(roles) != {"separate_columns", "repeated_rows"}:
        return None
    role = "separate_columns" if _select_expression_count(gold_sql) > 1 else "repeated_rows"
    return {
        "resolution_method": "gold_output_shape",
        "oracle_target_option_ids": [roles[role]],
        "option_resolution_map": [
            {"option_id": option_id, "presentation": option_role}
            for option_role, option_id in roles.items()
        ],
    }


def _resolve_option_from_gold_columns(
    gold_sql: str, options: list[dict[str, Any]]
) -> dict[str, Any] | None:
    ignored = {
        "a", "an", "as", "interpret", "interpretation", "it", "no", "option",
        "the", "this", "use", "yes",
    }
    columns = [
        _normalized_words(next(value for value in match if value))
        for match in _QUALIFIED_COLUMN_RE.findall(gold_sql)
    ]
    column_tokens = [set(value.split()) for value in columns if value]
    if not column_tokens:
        return None
    scored: list[tuple[int, str, str]] = []
    for option in options:
        option_id = str(option.get("option_id") or "")
        label = str(option.get("label") or "")
        id_tokens = set(_normalized_words(option_id).split()) - ignored
        label_tokens = set(_normalized_words(label).split()) - ignored
        score = max((3 * len(id_tokens & item) + len(label_tokens & item) for item in column_tokens), default=0)
        scored.append((score, option_id, label))
    best = max((item[0] for item in scored), default=0)
    leaders = [item for item in scored if item[0] == best]
    if best <= 0 or len(leaders) != 1:
        return None
    return {
        "resolution_method": "gold_column_option_overlap",
        "oracle_target_option_ids": [leaders[0][1]],
        "option_resolution_map": [
            {"option_id": option_id, "column_token_overlap": score}
            for score, option_id, _ in scored
        ],
    }


def _select_expression_count(sql: str) -> int:
    match = re.search(r"\bselect\b", sql, re.I)
    if not match:
        return 0
    depth = 0
    quote: str | None = None
    commas = 0
    index = match.end()
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            commas += 1
        elif depth == 0 and re.match(r"(?i)from\b", sql[index:]):
            return commas + 1
        index += 1
    return commas + 1


def _resolve_explicit_question_logic(
    original_question: str,
    request: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any] | None:
    option_roles: dict[str, str] = {}
    for option in options:
        option_id = str(option.get("option_id") or "")
        text = f"{option_id} {option.get('label') or ''}".casefold()
        if any(
            marker in text
            for marker in (
                "simultaneous", "separate dimension", "must be both",
                "both", "all of", "together", "and logic",
            )
        ):
            option_roles["and"] = option_id
        if any(
            marker in text
            for marker in (
                "alternative", "single dimension", "either", "one of", "or logic",
            )
        ):
            option_roles["or"] = option_id
    if set(option_roles) != {"and", "or"}:
        return None

    quoted = re.findall(r"['\"]([^'\"]+)['\"]", str(request.get("question") or ""))
    if len(quoted) < 2:
        return None
    question = _normalized_words(original_question)
    first = _normalized_words(quoted[0])
    second = _normalized_words(quoted[1])
    first_at = question.find(first)
    second_at = question.find(second, first_at + len(first)) if first_at >= 0 else -1
    if first_at < 0 or second_at < 0:
        return None
    connector = question[first_at + len(first) : second_at]
    role = "and" if re.search(r"\band\b", connector) else "or" if re.search(r"\bor\b", connector) else None
    if role is None:
        return None
    return {
        "resolution_method": "explicit_question_connector",
        "oracle_target_option_ids": [option_roles[role]],
        "option_resolution_map": [
            {"option_id": option_id, "logical_role": option_role}
            for option_role, option_id in option_roles.items()
        ],
    }


def _resolve_gold_boolean_logic(
    gold_sql: str,
    request: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve an explicit AND-vs-OR clarification from Gold query shape.

    This is restricted to questions whose wording and choices explicitly
    contrast simultaneous/both with alternative/either.  It is not a general
    SQL semantic parser.
    """
    roles: dict[str, str] = {}
    for option in options:
        option_id = str(option.get("option_id") or "")
        text = _normalized_words(f"{option_id} {option.get('label') or ''}")
        if any(marker in text for marker in (
            "simultaneous", "must be both", "both", "all of", "together", "and logic",
        )):
            roles["and"] = option_id
        if any(marker in text for marker in (
            "alternative", "either", "one of", "or logic",
        )):
            roles["or"] = option_id
    if set(roles) != {"and", "or"}:
        return None
    question = _normalized_words(str(request.get("question") or ""))
    if not (
        any(marker in question for marker in ("simultaneous", "both", "all of", "at the same time"))
        and any(marker in question for marker in ("alternative", "either type", "either one"))
    ):
        return None
    where_match = re.search(r"\bwhere\b(.+)$", gold_sql, re.I | re.S)
    if where_match is None:
        return None
    where = re.sub(r"'(?:''|[^'])*'", "?", where_match.group(1))
    # Gold IN/OR is direct evidence of alternatives. Otherwise two or more
    # independently conjoined equality predicates are evidence of simultaneous
    # conditions.
    if re.search(r"\bin\s*\(", where, re.I) or re.search(r"\bor\b", where, re.I):
        role = "or"
    elif len(re.findall(r"(?<![<>!])=(?!=)", where)) >= 2 and re.search(r"\band\b", where, re.I):
        role = "and"
    else:
        return None
    return {
        "resolution_method": "gold_boolean_shape",
        "oracle_target_option_ids": [roles[role]],
        "option_resolution_map": [
            {"option_id": option_id, "logical_role": logical_role}
            for logical_role, option_id in roles.items()
        ],
    }


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
