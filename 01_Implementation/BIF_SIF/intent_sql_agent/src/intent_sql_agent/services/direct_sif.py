from __future__ import annotations

import copy
import re
from typing import Any


QUERY_UNDERSTANDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "normalized_query", "primary_subject_anchor", "anchor_reasoning",
        "targets", "field_phrases", "value_phrases", "conditions_text",
        "scope_text", "ambiguous_phrases",
    ],
    "properties": {
        "normalized_query": {"type": "string", "minLength": 1},
        "primary_subject_anchor": {"type": "string", "minLength": 1},
        "anchor_reasoning": {"type": "string"},
        "targets": {"$ref": "#/$defs/strings"},
        "field_phrases": {"$ref": "#/$defs/strings"},
        "value_phrases": {"$ref": "#/$defs/strings"},
        "conditions_text": {"$ref": "#/$defs/strings"},
        "scope_text": {"$ref": "#/$defs/strings"},
        "ambiguous_phrases": {"$ref": "#/$defs/strings"},
    },
    "$defs": {
        "strings": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        }
    },
}


CONFIRMED_GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["confirmed"],
    "properties": {
        "confirmed": {
            "type": "object",
            "additionalProperties": False,
            "required": ["terms", "codes", "unresolved"],
            "properties": {
                "terms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "physicalName", "termId", "surface", "selectionRule"
                        ],
                        "properties": {
                            "physicalName": {"type": "string", "minLength": 1},
                            "termId": {"type": ["string", "null"]},
                            "surface": {"type": "string", "minLength": 1},
                            "selectionRule": {"type": "string", "minLength": 1},
                        },
                    },
                },
                "codes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "physicalName", "termId", "code", "label", "sourceText"
                        ],
                        "properties": {
                            "physicalName": {"type": "string", "minLength": 1},
                            "termId": {"type": ["string", "null"]},
                            "code": {"type": ["string", "number", "boolean", "null"]},
                            "label": {"type": "string"},
                            "sourceText": {"type": "string", "minLength": 1},
                        },
                    },
                },
                "unresolved": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["surface", "status", "reason", "candidates"],
                        "properties": {
                            "surface": {"type": "string", "minLength": 1},
                            "status": {
                                "enum": [
                                    "CLARIFICATION_REQUIRED", "UNRESOLVED_SYSTEM",
                                    "EQUIVALENT_CANDIDATES",
                                ]
                            },
                            "reason": {"type": "string", "minLength": 1},
                            "candidates": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        }
    },
}


TABLE_LINKING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "root_anchor", "selected_tables", "joins", "column_bindings",
        "reasoning", "diagnostics",
    ],
    "properties": {
        "root_anchor": {"type": ["string", "null"]},
        "selected_tables": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["table", "tier", "role"],
                "properties": {
                    "table": {"type": "string", "minLength": 1},
                    "tier": {"type": "integer", "minimum": 1},
                    "role": {"enum": ["root", "join", "bridge"]},
                },
            },
        },
        "joins": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["from", "to", "from_col", "to_col"],
                "properties": {
                    "from": {"type": "string", "minLength": 1},
                    "to": {"type": "string", "minLength": 1},
                    "from_col": {"type": "string", "minLength": 1},
                    "to_col": {"type": "string", "minLength": 1},
                },
            },
        },
        "column_bindings": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "reasoning": {"type": "array", "items": {"type": "string"}},
        "diagnostics": {
            "type": "object", "additionalProperties": True,
            "required": ["missing_columns"],
            "properties": {
                "missing_columns": {
                    "type": "array", "items": {"type": "string"}
                }
            },
        },
    },
}


SQL_SECTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "where_sections", "order_by_section", "limit_section", "dedupe_section"
    ],
    "properties": {
        "select_section": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["column", "matched_snippet"],
                "properties": {
                    "column": {"type": "string", "minLength": 1},
                    "matched_snippet": {"type": "string", "minLength": 1},
                },
            },
        },
        "where_sections": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": [
                    "column", "operator", "values", "is_negation", "matched_snippet"
                ],
                "properties": {
                    "column": {"type": "string", "minLength": 1},
                    "operator": {
                        "enum": [
                            "EQ", "NEQ", "IN", "NOT_IN", "GT", "GTE", "LT",
                            "LTE", "LIKE", "IS_NULL", "IS_NOT_NULL",
                        ]
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": ["string", "number", "boolean", "null"]},
                    },
                    "is_negation": {"type": "boolean"},
                    "matched_snippet": {"type": "string", "minLength": 1},
                },
            },
        },
        "order_by_section": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["column", "direction", "matched_snippet"],
                "properties": {
                    "column": {"type": "string", "minLength": 1},
                    "direction": {"enum": ["ASC", "DESC"]},
                    "aggregate": {"enum": ["COUNT", "SUM", "AVG", "MIN", "MAX", None]},
                    "matched_snippet": {"type": "string", "minLength": 1},
                },
            },
        },
        "group_by_section": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["column", "matched_snippet"],
                "properties": {
                    "column": {"type": "string", "minLength": 1},
                    "matched_snippet": {"type": "string", "minLength": 1},
                },
            },
        },
        "limit_section": {
            "type": "object", "additionalProperties": False,
            "required": ["count", "matched_snippet"],
            "properties": {
                "count": {"type": ["integer", "null"], "minimum": 1},
                "matched_snippet": {"type": "string"},
            },
        },
        "dedupe_section": {
            "type": "object", "additionalProperties": False,
            "required": ["key_column", "policy", "matched_snippet"],
            "properties": {
                "key_column": {"type": ["string", "null"]},
                "policy": {"type": ["string", "null"]},
                "matched_snippet": {"type": "string"},
            },
        },
    },
}


DIRECT_SIF_GROUNDING_BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["confirmed_grounding", "table_linking", "sql_plan"],
    "properties": {
        "confirmed_grounding": CONFIRMED_GROUNDING_SCHEMA,
        "table_linking": TABLE_LINKING_SCHEMA,
        "sql_plan": SQL_SECTION_PLAN_SCHEMA,
    },
}


def compile_understanding_obligations(confirmed_cqd: dict[str, Any]) -> list[dict[str, Any]]:
    """Map each SQL-relevant CQD value to the supplied understanding sections."""
    obligations: list[dict[str, Any]] = []

    def add(ref: str, value: Any, sections: tuple[str, ...], kind: str) -> None:
        if value in (None, "", [], {}):
            return
        obligations.append({
            "cqd_ref": ref,
            "kind": kind,
            "value": copy.deepcopy(value),
            "required_sections": list(sections),
        })

    populations = confirmed_cqd.get("populations", []) or []
    output_context = _semantic_tokens({
        "objective": confirmed_cqd.get("objective"),
        "outputs": confirmed_cqd.get("outputs"),
        "selections": confirmed_cqd.get("selections"),
    })
    for index, population in enumerate(populations):
        ref = f"/populations/{index}"
        if isinstance(population, dict):
            entity = population.get("entity")
            entity_tokens = _semantic_tokens(entity)
            is_output_owner = bool(
                entity_tokens
                and entity_tokens <= output_context
                and any(
                    isinstance(other, dict)
                    and _semantic_tokens(other.get("entity")) != entity_tokens
                    for other in populations
                )
            )
            add(
                f"{ref}/entity", entity,
                ("scope_text", "conditions_text") if is_output_owner else ("conditions_text",),
                "population_descriptor" if is_output_owner else "population_membership",
            )
            for key, value in population.items():
                if key in {"population", "entity_type", "role", "of_entity"}:
                    add(
                        f"{ref}/{key}", value,
                        ("scope_text", "conditions_text"), "population_descriptor",
                    )
                elif key not in {"entity", "source_text", "source_span"}:
                    add(f"{ref}/{key}", value, ("conditions_text",), "population_constraint")
        else:
            add(ref, population, ("conditions_text",), "population_membership")

    for index, atom in enumerate((confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or []):
        if not _is_non_restrictive_output_role_atom(atom, confirmed_cqd):
            add(f"/predicates/atoms/{index}", atom, ("conditions_text",), "predicate")
    for root in ("time_constraints", "comparisons"):
        for index, value in enumerate(confirmed_cqd.get(root, []) or []):
            add(f"/{root}/{index}", value, ("conditions_text",), root.rstrip("s"))
    for root in ("selections", "outputs", "expressions", "aggregations", "groupings"):
        for index, value in enumerate(confirmed_cqd.get(root, []) or []):
            sections = (
                ("targets", "field_phrases", "scope_text")
                if root in {"selections", "outputs"}
                else ("targets", "field_phrases")
            )
            add(
                f"/{root}/{index}", value,
                sections, root.rstrip("s"),
            )
    for index, value in enumerate(confirmed_cqd.get("scopes", []) or []):
        add(f"/scopes/{index}", value, ("scope_text", "conditions_text"), "scope")
    return obligations


def validate_understanding_coverage(
    confirmed_cqd: dict[str, Any], understanding: dict[str, Any]
) -> dict[str, Any]:
    obligations = compile_understanding_obligations(confirmed_cqd)
    assessments: list[dict[str, Any]] = []
    for obligation in obligations:
        wanted_alternatives = _obligation_token_alternatives(obligation["value"])
        matches: list[dict[str, str]] = []
        combined_texts: list[str] = []
        for section in obligation["required_sections"]:
            for text in understanding.get(section, []) or []:
                combined_texts.append(str(text))
                available = _semantic_tokens(text)
                if any(_covers(wanted, available) for wanted in wanted_alternatives):
                    matches.append({"section": section, "text": str(text)})
        # One logical output is commonly represented by its value in ``targets``
        # and its owner/dimension in ``field_phrases``. Coverage is about the
        # supplied SIF sections collectively; it must not force an LLM to repeat
        # the same synthetic phrase in both arrays.
        combine_across_items = (
            len(obligation["required_sections"]) > 1
            or (
                obligation.get("kind") == "population_constraint"
                and isinstance(obligation.get("value"), (dict, list))
            )
        )
        if not matches and combine_across_items:
            combined_available = _semantic_tokens(combined_texts)
            if any(_covers(wanted, combined_available) for wanted in wanted_alternatives):
                matches.append({
                    "section": "+".join(obligation["required_sections"]),
                    "text": " | ".join(combined_texts),
                })
        assessments.append({
            **copy.deepcopy(obligation),
            "status": "covered" if matches else "missing",
            "matches": matches,
            "reason": (
                "CQD meaning is present in an executable SIF-understanding section"
                if matches else
                "CQD meaning is absent from every required SIF-understanding section"
            ),
        })
    missing = [item for item in assessments if item["status"] == "missing"]
    return {
        "status": "complete" if not missing else "incomplete",
        "obligation_count": len(assessments),
        "covered_count": len(assessments) - len(missing),
        "missing_count": len(missing),
        "assessments": assessments,
    }


def normalize_understanding_roles(
    understanding: dict[str, Any], confirmed_cqd: dict[str, Any]
) -> dict[str, Any]:
    """Keep output-owner relationships out of WHERE-oriented conditions."""
    result = copy.deepcopy(understanding)
    relations: list[str] = []
    for population in confirmed_cqd.get("populations", []) or []:
        if not isinstance(population, dict) or not population.get("of_entity"):
            continue
        subject = population.get("entity") or population.get("population")
        owner = population.get("of_entity")
        if subject and owner:
            relations.append(f"{subject} of {owner}")

    predicate_sources = [
        str(atom.get("source_text") or "")
        for atom in (confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or []
        if isinstance(atom, dict) and atom.get("source_text")
        and not _is_non_restrictive_output_role_atom(atom, confirmed_cqd)
    ]
    relations.extend(
        str(atom.get("source_text") or "")
        for atom in (confirmed_cqd.get("predicates", {}) or {}).get("atoms", []) or []
        if isinstance(atom, dict)
        and atom.get("source_text")
        and _is_non_restrictive_output_role_atom(atom, confirmed_cqd)
    )
    kept: list[str] = []
    moved: list[str] = []
    for condition in result.get("conditions_text", []) or []:
        is_relation = any(_equivalent_role_phrase(str(condition), relation) for relation in relations)
        is_explicit_predicate = any(
            _equivalent_role_phrase(str(condition), source) for source in predicate_sources
        )
        (moved if is_relation and not is_explicit_predicate else kept).append(str(condition))
    result["conditions_text"] = kept
    scopes = list(result.get("scope_text", []) or [])
    for relation in moved:
        if relation not in scopes:
            scopes.append(relation)
    result["scope_text"] = scopes
    return result


def validate_grounded_condition_coverage(
    understanding: dict[str, Any], sql_plan: dict[str, Any]
) -> dict[str, Any]:
    sections = sql_plan.get("where_sections", []) or []
    grouped_sections: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        grouped_sections.setdefault(
            str(section.get("column") or "").casefold(), []
        ).append(section)
    assessments = []
    for condition in understanding.get("conditions_text", []) or []:
        condition_tokens = _semantic_tokens(condition)
        matches: list[dict[str, Any]] = []
        for group in grouped_sections.values():
            available = _semantic_tokens([
                {
                    "column": item.get("column"),
                    "values": item.get("values"),
                    "matched_snippet": item.get("matched_snippet"),
                }
                for item in group
            ])
            if _covers(condition_tokens, available) or _covers(available, condition_tokens):
                matches.extend(group)
        assessments.append({
            "condition": condition,
            "status": "grounded" if matches else "missing",
            "where_sections": copy.deepcopy(matches),
        })
    missing = [item for item in assessments if item["status"] == "missing"]
    return {
        "status": "complete" if not missing else "incomplete",
        "condition_count": len(assessments),
        "missing_count": len(missing),
        "assessments": assessments,
    }


def _semantic_tokens(value: Any) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result.update(_semantic_tokens(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_semantic_tokens(item))
        return result
    if value is None:
        return set()
    ignored = {
        "a", "an", "the", "of", "for", "is", "are", "to", "all", "every",
        "list", "return", "provide", "output", "type", "content", "attribute",
        "selection", "action", "equal", "equals", "between", "and", "or",
    }
    tokens = set()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value)).casefold()
    for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE):
        if token in ignored:
            continue
        if token.isdigit():
            token = str(int(token))
        if len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
        if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "status")):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _obligation_tokens(value: Any) -> set[str]:
    """Extract meaning-bearing tokens without requiring CQD bookkeeping prose.

    CQD objects contain trace fields such as condition_id, applies_to, source_text,
    and physical table hints. Requiring every such token in one SIF phrase turns a
    correct decomposition into a permanent false gap.
    """
    if not isinstance(value, dict):
        return _semantic_tokens(value)
    semantic_keys = (
        "entity", "dimension", "attribute", "target", "content", "metric",
        "measure", "value", "start", "end", "direction", "grain",
    )
    selected = [value.get(key) for key in semantic_keys if value.get(key) not in (None, "", [], {})]
    if selected:
        return _semantic_tokens(selected)
    ignored_keys = {
        "condition_id", "stable_id", "term_id", "applies_to", "source_text",
        "source_span", "table", "column", "physical_name", "reason",
    }
    return _semantic_tokens({
        key: item for key, item in value.items() if str(key).casefold() not in ignored_keys
    })


def _obligation_token_alternatives(value: Any) -> list[set[str]]:
    alternatives = [_obligation_tokens(value)]
    if isinstance(value, dict):
        for field in ("source_text", "source_span", "description"):
            field_tokens = _semantic_tokens(value.get(field))
            if field_tokens and field_tokens not in alternatives:
                alternatives.append(field_tokens)
    return [item for item in alternatives if item]


def _covers(wanted: set[str], available: set[str]) -> bool:
    return bool(wanted) and wanted <= available


def _equivalent_role_phrase(left: str, right: str) -> bool:
    ignored = {"the", "a", "an", "of", "for", "is", "are", "role", "exists"}
    left_tokens = _semantic_tokens(left) - ignored
    right_tokens = _semantic_tokens(right) - ignored
    return bool(left_tokens) and (
        left_tokens <= right_tokens or right_tokens <= left_tokens
    )


def _is_non_restrictive_output_role_atom(
    atom: Any, confirmed_cqd: dict[str, Any]
) -> bool:
    if not isinstance(atom, dict):
        return False
    operator = str(atom.get("operator") or "").strip().casefold()
    if operator not in {"=", "equals", "exists"}:
        return False
    dimension_tokens = _semantic_tokens(atom.get("dimension"))
    role_markers = {"role", "entity"}
    role_tokens = dimension_tokens - role_markers
    if not role_tokens or not (dimension_tokens & role_markers):
        return False
    value = atom.get("value")
    value_tokens = _semantic_tokens(value)
    population_roles = _semantic_tokens([
        {
            "population": item.get("population"),
            "entity": item.get("entity"),
            "role": item.get("role"),
        }
        for item in confirmed_cqd.get("populations", []) or []
        if isinstance(item, dict)
    ])
    identity = (
        operator == "exists" and value in (True, 1, "true", "yes")
    ) or bool(value_tokens and (
        value_tokens <= role_tokens or value_tokens <= population_roles
    ))
    if not identity:
        return False
    output_context = _semantic_tokens({
        "objective": confirmed_cqd.get("objective"),
        "outputs": confirmed_cqd.get("outputs"),
        "selections": confirmed_cqd.get("selections"),
    })
    return bool((role_tokens | value_tokens) & (output_context | population_roles))

