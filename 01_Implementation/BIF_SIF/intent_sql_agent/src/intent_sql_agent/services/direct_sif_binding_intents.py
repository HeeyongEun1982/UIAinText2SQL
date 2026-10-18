from __future__ import annotations

import copy
import re
from typing import Any


def build_binding_intents(
    understanding: dict[str, Any], confirmed_cqd: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Create stable internal binding units without changing the public SIF format."""
    intents: list[dict[str, Any]] = []
    outputs = (confirmed_cqd or {}).get("outputs", []) or []
    for index, target in enumerate(understanding.get("targets", []) or []):
        matching_outputs = [
            item for item in outputs
            if _semantic_match(str(target), " ".join(
                str(item.get(key) or "")
                for key in (
                    "content", "source_text", "attribute", "output",
                    "selection", "description",
                )
            ))
        ]
        cardinality = _output_cardinality(matching_outputs)
        intents.append({
            "item_id": f"select:{index}",
            "role": "SELECT",
            "source_text": str(target),
            "operator": None,
            "literal_values": [],
            "cardinality": cardinality,
        })
    for index, condition in enumerate(understanding.get("conditions_text", []) or []):
        intents.append({
            "item_id": f"where:{index}",
            "role": "WHERE",
            "source_text": str(condition),
            "operator": None,
            "literal_values": [],
            "cardinality": "single",
        })
    return intents


def match_where_intent(
    intents: list[dict[str, Any]], snippet: str,
) -> dict[str, Any] | None:
    candidates = [item for item in intents if item.get("role") == "WHERE"]
    ranked = sorted(
        (
            (_overlap_score(snippet, str(item.get("source_text") or "")), item)
            for item in candidates
        ),
        key=lambda pair: (-pair[0], str(pair[1].get("item_id") or "")),
    )
    return ranked[0][1] if ranked and ranked[0][0] > 0 else None


def expand_plural_output_bindings(
    *,
    bundle: dict[str, Any],
    understanding: dict[str, Any],
    confirmed_cqd: dict[str, Any] | None,
    schema_catalog: dict[str, Any],
    technical_feedback: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand explicitly multi-valued SELECT bindings to usable numbered siblings."""
    result = copy.deepcopy(bundle)
    intents = build_binding_intents(understanding, confirmed_cqd)
    grounding = result["confirmed_grounding"]["confirmed"]
    terms = grounding.get("terms", []) or []
    bindings = result["table_linking"].setdefault("column_bindings", {})
    tables = {
        _norm(str(table.get("name") or "")): table
        for table in schema_catalog.get("tables", []) or []
    }
    decisions: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    processed_families: set[tuple[str, str]] = set()

    for intent in intents:
        if intent["role"] != "SELECT" or intent["cardinality"] == "single":
            continue
        target = str(intent["source_text"])
        discarded: list[str] = []
        for term in list(terms):
            if not _semantic_match(target, str(term.get("surface") or "")):
                continue
            physical_name = str(term.get("physicalName") or "")
            bound_table = _binding_table(bindings, physical_name)
            table_schema = tables.get(_norm(bound_table)) if bound_table else None
            column_schema = _column(table_schema, physical_name)
            if column_schema is not None and not _usable(column_schema):
                terms.remove(term)
                discarded.append(physical_name)
                if not any(
                    _norm(str(other.get("physicalName") or "")) == _norm(physical_name)
                    for other in terms
                ):
                    bindings.pop(physical_name, None)
        if discarded:
            decisions.append({
                "item_id": intent["item_id"], "role": "SELECT",
                "status": "discarded_unusable_schema_members",
                "discarded_columns": discarded,
                "basis": "schema_metadata_marks_column_unusable",
            })
        target_terms = [
            term for term in terms
            if _semantic_match(target, str(term.get("surface") or ""))
        ]
        for term in list(target_terms):
            physical = str(term.get("physicalName") or "")
            family = re.fullmatch(r"(.+?)(\d+)", physical)
            table_name = _binding_table(bindings, physical)
            table = tables.get(_norm(table_name)) if table_name else None
            if family is None or table is None:
                continue
            prefix = family.group(1)
            family_key = (_norm(table_name), _norm(prefix))
            if family_key in processed_families:
                continue
            processed_families.add(family_key)
            siblings = [
                column for column in table.get("columns", []) or []
                if re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", str(column.get("name") or ""), re.I)
                and _usable(column)
            ]
            siblings.sort(key=lambda column: int(re.search(r"(\d+)$", str(column["name"])).group(1)))
            sibling_names = [str(column.get("name") or "") for column in siblings]
            if len(sibling_names) < 2:
                continue
            if intent["cardinality"] in {"ambiguous", "unspecified"}:
                option_labels = [
                    f"Use one physical field ({table_name}.{physical}); return repeated rows if needed",
                    (
                        "Use all usable sibling fields as separate columns in the same row: "
                        + ", ".join(f"{table_name}.{name}" for name in sibling_names)
                    ),
                ]
                selected_index = _selected_option_index(
                    technical_feedback or [], surface=target,
                    option_labels=option_labels,
                )
                if selected_index == 0:
                    removed = []
                    for other in list(terms):
                        other_name = str(other.get("physicalName") or "")
                        if (
                            other is not term
                            and other_name in sibling_names
                            and _semantic_match(target, str(other.get("surface") or ""))
                        ):
                            terms.remove(other)
                            bindings.pop(other_name, None)
                            removed.append(other_name)
                    decisions.append({
                        "item_id": intent["item_id"], "role": "SELECT",
                        "status": "confirmed_single_field", "anchor_column": physical,
                        "removed_columns": removed,
                        "basis": "technical_clarification",
                    })
                    continue
                if selected_index == 1:
                    intent = {**intent, "cardinality": "multiple"}
                else:
                    issues.append({
                        "type": "CLARIFICATION_REQUIRED",
                        "surface": target,
                        "reason": (
                            "The CQD does not specify whether parallel schema fields are "
                            "separate output values or alternatives for one output."
                        ),
                        "candidates": option_labels,
                    })
                    decisions.append({
                        "item_id": intent["item_id"], "role": "SELECT",
                        "status": "clarification_required", "anchor_column": physical,
                        "candidate_columns": sibling_names,
                    })
                    continue
            added: list[str] = []
            for column in siblings:
                sibling = str(column.get("name") or "")
                if any(_norm(str(item.get("physicalName") or "")) == _norm(sibling) for item in terms):
                    continue
                clone = copy.deepcopy(term)
                clone["physicalName"] = sibling
                clone["selectionRule"] = "OUTPUT_FAMILY_EXPANSION"
                terms.append(clone)
                bindings[sibling] = table_name
                added.append(sibling)
            decisions.append({
                "item_id": intent["item_id"],
                "role": "SELECT",
                "status": "expanded" if added else "confirmed",
                "anchor_column": physical,
                "added_columns": added,
                "basis": "explicit_multiple_output_and_schema_numbered_family",
            })
    select_terms = [
        term for term in terms
        if str(term.get("termId") or "").startswith("select:")
    ]
    if select_terms and result["sql_plan"].get("select_section"):
        result["sql_plan"]["select_section"] = [
            {
                "column": f"{_binding_table(bindings, str(term['physicalName']))}.{term['physicalName']}",
                "matched_snippet": str(term.get("surface") or ""),
            }
            for term in select_terms
            if _binding_table(bindings, str(term.get("physicalName") or ""))
        ]
    return result, {
        "status": "clarification_required" if issues else "resolved",
        "decisions": decisions,
        "issues": issues,
    }


def _output_cardinality(outputs: list[dict[str, Any]]) -> str:
    if not outputs:
        return "unspecified"
    declared = {
        str(item.get(key) or "").strip().casefold()
        for item in outputs
        for key in ("value_multiplicity", "cardinality", "field_multiplicity")
        if item.get(key) not in (None, "")
    }
    if declared & {"multiple", "many", "plural", "one_or_more"}:
        return "multiple"
    if declared & {"single", "one", "scalar"}:
        return "single"
    # A list/collection describes result-set shape, not the number of physical
    # fields representing each result. Defer that distinction when siblings exist.
    output_types = {
        str(item.get("output_type") or "").strip().casefold() for item in outputs
    }
    if output_types & {"multiple", "multi_value", "multi_field"}:
        return "multiple"
    if output_types & {"list", "collection"}:
        return "ambiguous"
    return "unspecified"


def _selected_option_index(
    feedback: list[dict[str, Any]], *, surface: str, option_labels: list[str],
) -> int | None:
    for item in reversed(feedback):
        if item.get("type") != "TECHNICAL_CLARIFICATION_RESPONSE":
            continue
        if _norm(str(item.get("surface") or "")) != _norm(surface):
            continue
        if [str(value) for value in item.get("candidates", []) or []] != option_labels:
            continue
        selected = list((item.get("response") or {}).get("selected_option_ids") or [])
        if len(selected) != 1:
            return None
        match = re.fullmatch(r"option_(\d+)", str(selected[0]), re.I)
        if match:
            index = int(match.group(1)) - 1
            return index if 0 <= index < len(option_labels) else None
    return None


def _binding_table(bindings: dict[str, Any], physical: str) -> str | None:
    wanted = _norm(physical)
    return next(
        (str(table) for column, table in bindings.items() if _norm(str(column)) == wanted),
        None,
    )


def _column(table: dict[str, Any] | None, physical: str) -> dict[str, Any] | None:
    if table is None:
        return None
    wanted = _norm(physical)
    return next(
        (
            column for column in table.get("columns", []) or []
            if _norm(str(column.get("name") or "")) == wanted
        ),
        None,
    )


def _usable(column: dict[str, Any]) -> bool:
    metadata = " ".join(
        str(column.get(key) or "")
        for key in ("description", "value_description", "status", "comment")
    ).casefold()
    return not any(marker in metadata for marker in (
        "not useful", "unused", "deprecated", "do not use", "obsolete",
    ))


def _semantic_match(left: str, right: str) -> bool:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    return bool(left_tokens) and (
        left_tokens <= right_tokens or right_tokens <= left_tokens
    )


def _overlap_score(left: str, right: str) -> int:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    return len(left_tokens & right_tokens)


def _tokens(value: str) -> set[str]:
    ignored = {"a", "an", "the", "of", "for", "is", "are", "to", "all", "every"}
    return {
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
        for token in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
        if token not in ignored
    }


def _norm(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold())
