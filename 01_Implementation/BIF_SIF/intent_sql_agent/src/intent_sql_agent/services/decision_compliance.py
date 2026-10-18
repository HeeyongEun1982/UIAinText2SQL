from __future__ import annotations

import json
import copy
import re
from typing import Any


def validate_decision_compliance(
    frame: dict[str, Any], ledger: list[dict[str, Any]], *, layer: str
) -> list[dict[str, Any]]:
    """Verify policy-known decision effects against the materialized frame."""
    findings: list[dict[str, Any]] = []
    for decision in ledger:
        effect = decision.get("canonical_effect", {}) or {}
        values = (effect.get("value", {}) or {}).get("canonical_values", []) or []
        if not values:
            continue
        paths = [str(path) for path in decision.get("affected_paths", []) or []]
        issue = str(decision.get("issue") or "")
        if issue in {"LOGIC_RELATION", "POPULATION_CONDITION_STRUCTURE"}:
            materialized = [frame.get("target_population"), frame.get("calculation")]
        elif issue in {"OUTPUT_SHAPE", "QUERY_OUTPUT_SEMANTICS"}:
            materialized = [
                frame.get("output_expectation"),
                frame.get("output_contract"),
                frame.get("query_blocks"),
            ]
        else:
            materialized = [_pointer_get(frame, path) for path in paths]
        searchable = _normalize(json.dumps(materialized, ensure_ascii=False, sort_keys=True))
        for value in values:
            if _effect_is_represented(issue, str(value), searchable):
                continue
            findings.append({
                "issue_type": issue,
                "subject_ref": paths[0] if paths else "",
                "description": (
                    f"The provisional {layer} does not represent confirmed decision "
                    f"{decision.get('decision_id')} value {value!r}."
                ),
                "alternatives": list(decision.get("choice_domain", []) or []),
                "decision_id": decision.get("decision_id"),
                "canonical_value": value,
                "kind": "decision_effect_mismatch",
            })
    return findings


def validate_unrequested_bif_outputs(
    question: str, bif: dict[str, Any]
) -> list[dict[str, Any]]:
    normalized_question = _normalize(question)
    findings: list[dict[str, Any]] = []
    for index, field in enumerate(bif.get("output_expectation", {}).get("fields", []) or []):
        label = _normalize(str(field.get("label") or field.get("name") or ""))
        if not label:
            continue
        is_identifier = bool(re.search(r"\b(id|identifier|code|key)\b", label))
        identifier_requested = any(
            term in normalized_question
            for term in (" id ", " identifier ", " code ", " key ")
        )
        if is_identifier and not identifier_requested:
            findings.append({
                "kind": "unrequested_output",
                "path": f"/output_expectation/fields/{index}",
                "description": f"Output field {label!r} was not requested by the user.",
                "material": True,
                "user_answerable": False,
            })
    return findings


def remove_unrequested_bif_outputs(
    question: str, bif: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repaired = copy.deepcopy(bif)
    findings = validate_unrequested_bif_outputs(question, repaired)
    indexes = sorted(
        (int(item["path"].rsplit("/", 1)[1]) for item in findings),
        reverse=True,
    )
    fields = repaired.get("output_expectation", {}).get("fields", [])
    repairs: list[dict[str, Any]] = []
    for index in indexes:
        if 0 <= index < len(fields):
            removed = fields.pop(index)
            repairs.append({
                "operation": "remove_unrequested_bif_output",
                "path": f"/output_expectation/fields/{index}",
                "removed": removed,
                "reason": "identifier_output_not_requested_by_user",
            })
    return repaired, repairs


def _pointer_get(value: Any, pointer: str) -> Any:
    current = value
    if not pointer or pointer == "/":
        return current
    try:
        for token in pointer.lstrip("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]
        return current
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _effect_is_represented(issue: str, value: str, searchable: str) -> bool:
    if value == "as_recorded_no_additional_validation":
        return not any(
            term in searchable
            for term in ("syntax valid", "verified", "deliverability", "not null")
        )
    aliases = {
        "city": ("city",),
        "organization_or_district": ("district", "organization"),
        "all_of": ("all of", "and", "simultaneous"),
        "any_of": ("any of", "or", "alternative"),
        "separate_fields": ("separate fields", "separate columns", "side by side"),
        "repeated_rows": ("repeated rows", "one value per row", "multiple rows"),
    }
    if value not in aliases:
        # Unknown business meanings are not guessed by code; path coverage remains the guard.
        return True
    return any(alias in searchable for alias in aliases[value])


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
