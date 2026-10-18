from __future__ import annotations

import hashlib
import re
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "all", "an", "and", "as", "be", "by", "for", "in", "is", "must",
    "of", "or", "school", "schools", "the", "to", "type", "value", "within",
}


def detect_source_population_ambiguities(
    bif: dict[str, Any],
    sif: dict[str, Any],
    schema_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find tables that independently encode the same set of BIF population conditions.

    This is deliberately metadata based. It does not use Gold SQL and it does not decide
    which source is correct. Its output is internal evidence for a user-facing A5 question.
    """
    conditions = bif.get("target_population", {}).get("inclusion_conditions", [])
    if len(conditions) < 2:
        return []

    candidates: list[dict[str, Any]] = []
    for table in schema_catalog.get("tables", []):
        mappings = []
        for index, condition in enumerate(conditions):
            mapping = _best_column_mapping(table, condition, index)
            if mapping is None:
                break
            mappings.append(mapping)
        if len(mappings) == len(conditions):
            candidates.append(
                {
                    "table": table.get("name"),
                    "condition_mappings": mappings,
                    "record_context": _record_context(table),
                    "user_facing_definition": _user_facing_definition(table),
                }
            )

    if len(candidates) < 2:
        return []

    for index, candidate in enumerate(candidates, start=1):
        candidate["candidate_id"] = f"candidate_{index}"

    current_sources = sorted(
        {
            source.get("name")
            for block in sif.get("query_blocks", [])
            for source in block.get("sources", [])
            if source.get("kind") == "table" and source.get("name")
        }
    )
    candidate_tables = sorted(str(candidate["table"]) for candidate in candidates)
    signature = "|".join(candidate_tables) + "::" + "|".join(
        str(condition.get("condition_id", index))
        for index, condition in enumerate(conditions)
    )
    ambiguity_id = "source_population_" + hashlib.sha256(signature.encode()).hexdigest()[:12]
    return [
        {
            "ambiguity_id": ambiguity_id,
            "kind": "semantic_source_population",
            "material": True,
            "reason": (
                "Multiple record sources independently represent every population condition; "
                "they may contain different record populations even when both mappings are executable."
            ),
            "intent_paths": [
                f"/target_population/inclusion_conditions/{index}"
                for index in range(len(conditions))
            ],
            "current_sources": current_sources,
            "candidates": candidates,
        }
    ]


def _best_column_mapping(
    table: dict[str, Any], condition: dict[str, Any], condition_index: int
) -> dict[str, Any] | None:
    condition_text = " ".join(
        str(condition.get(key) or "") for key in ("subject", "value", "description")
    )
    column_token_sets = [
        _tokens(" ".join(str(column.get(key) or "") for key in (
            "name", "semantic_name", "description", "value_description",
        )))
        for column in table.get("columns", [])
    ]
    frequent_tokens = {
        token for token in set().union(*column_token_sets) if column_token_sets
        if sum(token in item for item in column_token_sets) * 2 > len(column_token_sets)
    }
    condition_tokens = _tokens(condition_text) - frequent_tokens
    subject_tokens = _tokens(str(condition.get("subject") or "")) - frequent_tokens
    exact_matches = {
        (str(match.get("column")), str(match.get("value")))
        for match in table.get("question_value_matches", [])
    }

    best: tuple[int, dict[str, Any]] | None = None
    for column in table.get("columns", []):
        column_name = str(column.get("name") or "")
        column_name_text = " ".join(
            str(column.get(key) or "") for key in ("name", "semantic_name")
        )
        column_text = " ".join(
            str(column.get(key) or "")
            for key in ("name", "semantic_name", "description", "value_description")
        )
        name_overlap = condition_tokens & _tokens(column_name_text)
        description_overlap = condition_tokens & _tokens(column_text)
        subject_overlap = subject_tokens & _tokens(column_name_text)
        value = str(condition.get("value") or "").strip()
        exact_value = (column_name, value) in exact_matches
        score = (
            len(name_overlap) * 5
            + len(description_overlap) * 2
            + len(subject_overlap) * 3
            + (12 if exact_value else 0)
        )
        if not exact_value and score < 5:
            continue
        evidence = {
            "intent_path": f"/target_population/inclusion_conditions/{condition_index}",
            "condition_id": condition.get("condition_id"),
            "condition_summary": condition.get("description") or condition_text,
            "column": column_name,
            "column_description": column.get("description"),
            "value_description": column.get("value_description"),
            "exact_value_match": exact_value,
            "score": score,
        }
        if best is None or score > best[0]:
            best = (score, evidence)
    return best[1] if best else None


def _record_context(table: dict[str, Any]) -> list[str]:
    """Give A5 enough metadata to phrase source definitions without table names."""
    useful: list[str] = []
    for column in table.get("columns", []):
        text = " ".join(filter(None, (
            str(column.get(key) or "") for key in ("name", "semantic_name", "description")
        ))).strip()
        tokens = _tokens(text)
        if tokens & {"academic", "date", "eligibility", "enrollment", "meal", "status", "update"}:
            useful.append(text.strip())
        if len(useful) == 6:
            break
    return useful


def _user_facing_definition(table: dict[str, Any]) -> str:
    for key in ("user_facing_definition", "semantic_name", "description", "comment"):
        value = str(table.get(key) or "").strip()
        if value:
            return value
    column_tokens = set()
    for column in table.get("columns", []):
        column_tokens |= _tokens(" ".join(
            str(column.get(key) or "")
            for key in ("name", "semantic_name", "description")
        ))
    if column_tokens & {"meal", "eligibility", "frpm", "nslp"}:
        return "the annual program and eligibility reporting records"
    if column_tokens & {"opened", "closed", "operational", "latitude", "longitude", "website"}:
        return "the master school registry and operational-status records"
    if "academic" in column_tokens or "enrollment" in column_tokens:
        return "the periodic reporting records"
    return "records represented by this candidate source"


def _tokens(value: str) -> set[str]:
    aliases = {"highest": "high", "lowest": "low", "lunch": "meal"}
    tokens = {
        _singular(aliases.get(token, token))
        for token in _TOKEN_RE.findall(value.lower()) if token not in _STOPWORDS
    }
    if "meal" in tokens:
        tokens.add("nslp")
    return tokens


def _singular(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
