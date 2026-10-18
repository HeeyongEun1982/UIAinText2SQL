from __future__ import annotations

import re
from typing import Any


def validate_bif_semantics(question: str, bif: dict[str, Any]) -> list[dict[str, Any]]:
    """Return user-level BIF findings that JSON Schema alone cannot express."""
    findings: list[dict[str, Any]] = []
    population = bif.get("target_population", {})
    confirmed_paths = {
        str(item.get("path") or "")
        for item in bif.get("field_states", []) or []
        if item.get("state") in {"user_confirmed", "user_corrected"}
    }
    findings.extend(
        _validate_condition_collection(
            question,
            population.get("inclusion_conditions", []),
            population.get("inclusion_logic"),
            "/target_population/inclusion_conditions",
            "/target_population/inclusion_logic",
            confirmed_paths,
        )
    )
    findings.extend(
        _validate_condition_collection(
            question,
            population.get("exclusion_conditions", []),
            population.get("exclusion_logic"),
            "/target_population/exclusion_conditions",
            "/target_population/exclusion_logic",
            confirmed_paths,
        )
    )
    findings.extend(_validate_output_multiplicity(question, bif))
    return findings


def _validate_condition_collection(
    question: str,
    conditions: Any,
    logic: Any,
    conditions_path: str,
    logic_path: str,
    confirmed_paths: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(conditions, list):
        return []
    findings: list[dict[str, Any]] = []
    ids = [str(item.get("condition_id")) for item in conditions if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        findings.append(_finding("duplicate_condition_id", conditions_path, "Condition IDs must be unique."))
    if not isinstance(logic, dict):
        if conditions:
            findings.append(_finding("missing_condition_logic", logic_path, "The AND/OR relationship between conditions is not explicit."))
    else:
        refs = _logic_refs(logic)
        missing = sorted(set(ids) - refs)
        unknown = sorted(refs - set(ids))
        if missing or unknown:
            findings.append(
                _finding(
                    "condition_logic_reference_mismatch",
                    logic_path,
                    f"Logic must reference every condition exactly by ID; missing={missing}, unknown={unknown}.",
                )
            )

    normalized_question = _normalize(question)
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            continue
        path = f"{conditions_path}/{index}"
        operator = str(condition.get("operator") or "")
        value = condition.get("value")
        value_mode = condition.get("value_mode")
        set_relation = condition.get("set_relation")
        if operator in {"in", "not_in"}:
            if value_mode != "set" or set_relation not in {"any_of", "all_of"} or not isinstance(value, list):
                findings.append(_finding("invalid_set_semantics", path, "Set conditions require an explicit set value mode and any_of/all_of relation."))
            if isinstance(value, list) and _question_uses_and_between_values(normalized_question, value) and set_relation == "any_of":
                findings.append(
                    _finding(
                        "question_logic_mismatch",
                        path,
                        "The question connects these values with AND, but the BIF represents them as alternatives. Confirm whether they are simultaneous conditions on separate business dimensions.",
                    )
                )
        if _looks_like_compound_condition(condition):
            findings.append(
                _finding(
                    "compound_condition_not_atomic",
                    path,
                    "Multiple business dimensions are represented by one condition and must be separated or confirmed.",
                )
            )
        dimension = str(condition.get("semantic_dimension") or "").casefold()
        subject = str(condition.get("subject") or "").casefold()
        value_text = _normalize(str(condition.get("value") or ""))
        deferred = condition.get("semantic_dimension_status") == "deferred_to_sif"
        if not deferred and dimension.endswith(".city") and (
            "district" in subject or value_text.endswith(" city unified")
        ) and not any(
            confirmed == path or confirmed.startswith(path + "/")
            for confirmed in confirmed_paths
        ):
            findings.append(
                _finding(
                    "location_role_mismatch",
                    path,
                    "The condition labels the same phrase as both a city and a school district. Confirm whether the intended location is the city name or the district name, including the exact business name.",
                )
            )
    return findings


def _validate_output_multiplicity(question: str, bif: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = _normalize(question)
    plural_requested = any(term in normalized for term in ("email addresses", "e mail addresses", "e-mails", "emails"))
    findings: list[dict[str, Any]] = []
    for index, field in enumerate(bif.get("output_expectation", {}).get("fields", [])):
        label = _normalize(str(field.get("label") or ""))
        if plural_requested and ("email" in label or "e mail" in label) and (
            field.get("value_multiplicity") != "multiple"
            or field.get("coverage") not in {"all_requested", "all_available"}
        ):
            findings.append(
                _finding(
                    "output_multiplicity_mismatch",
                    f"/output_expectation/fields/{index}",
                    "The user requested e-mail addresses in the plural; record that all requested or all available values are expected.",
                )
            )
        if (
            field.get("value_multiplicity") == "multiple"
            and field.get("coverage") not in {"all_requested", "all_available"}
        ):
            findings.append(
                _finding(
                    "output_multiplicity_mismatch",
                    f"/output_expectation/fields/{index}",
                    "A multi-valued output must state whether all requested or all available values are expected.",
                )
            )
        if (
            field.get("value_multiplicity") == "multiple"
            and field.get("presentation") in {None, "unresolved"}
        ):
            findings.append(
                _finding(
                    "output_presentation_unresolved",
                    f"/output_expectation/fields/{index}/presentation",
                    "Clarify whether multiple values should be shown side-by-side in separate fields or as repeated rows.",
                )
            )
    return findings


def _logic_refs(logic: dict[str, Any]) -> set[str]:
    refs = {str(value) for value in logic.get("condition_refs", [])}
    for group in logic.get("groups", []):
        if isinstance(group, dict):
            refs.update(_logic_refs(group))
    return refs


def _question_uses_and_between_values(question: str, values: list[Any]) -> bool:
    normalized_values = [_normalize(str(value)) for value in values]
    if len(normalized_values) != 2 or not all(normalized_values):
        return False
    first = question.find(normalized_values[0])
    second = question.find(normalized_values[1], first + len(normalized_values[0])) if first >= 0 else -1
    if first < 0 or second < 0:
        return False
    between = question[first + len(normalized_values[0]) : second]
    return bool(re.search(r"\band\b", between))


def _looks_like_compound_condition(condition: dict[str, Any]) -> bool:
    dimensions = condition.get("semantic_dimensions")
    components = condition.get("components")
    structured = (
        isinstance(dimensions, list) and len(dimensions) > 1
    ) or (
        isinstance(components, list) and len(components) > 1
    )
    dimension = str(condition.get("semantic_dimension") or "").casefold()
    subject = str(condition.get("subject") or "").casefold()
    text = _normalize(f"{condition.get('value')} {condition.get('description')}")
    location_terms = sum(term in text for term in ("county", "city", "district", "authority"))
    return structured or (
        (dimension in {"location", "geography", "geographic_location"} or subject == "location")
        and location_terms >= 2
    )


def _finding(issue: str, path: str, description: str) -> dict[str, Any]:
    return {
        "issue": issue,
        "path": path,
        "description": description,
        "material": True,
        "user_answerable": True,
    }


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
