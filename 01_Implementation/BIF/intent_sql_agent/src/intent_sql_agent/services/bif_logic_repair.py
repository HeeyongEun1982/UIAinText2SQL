from __future__ import annotations

import copy
import re
from typing import Any


def apply_structured_logic_response(
    bif: dict[str, Any],
    clarification: dict[str, Any],
    response: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Apply an explicit simultaneous/separate-dimensions choice without A3."""
    if response.get("action") != "select":
        return None, []
    selected = {str(value) for value in response.get("selected_option_ids", [])}
    options = clarification.get("clarification_request", {}).get("options", [])
    chosen = [option for option in options if str(option.get("option_id")) in selected]
    if len(chosen) != 1:
        return None, []
    meaning = f"{chosen[0].get('option_id') or ''} {chosen[0].get('label') or ''}".casefold()
    if "simultaneous" not in meaning and "separate dimension" not in meaning:
        return None, []

    affected = clarification.get("clarification_request", {}).get("affected_paths", [])
    indexes = [
        int(match.group(1))
        for path in affected
        if (match := re.fullmatch(r"/target_population/inclusion_conditions/(\d+)", str(path)))
    ]
    if len(indexes) != 1:
        return None, []
    updated = copy.deepcopy(bif)
    population = updated.get("target_population", {})
    conditions = population.get("inclusion_conditions", [])
    index = indexes[0]
    if index >= len(conditions):
        return None, []
    original = conditions[index]
    values = original.get("value")
    if not isinstance(values, list) or len(values) < 2:
        return None, []

    original_id = str(original.get("condition_id") or f"condition_{index + 1}")
    replacements = []
    for value_index, value in enumerate(values, start=1):
        replacement = copy.deepcopy(original)
        replacement["condition_id"] = f"{original_id}_{value_index}"
        replacement["semantic_dimension"] = (
            "confirmed_separate_dimension." + _slug(str(value))
        )
        replacement["subject"] = str(value)
        replacement["operator"] = "equals"
        replacement["value_mode"] = "scalar"
        replacement["set_relation"] = "not_applicable"
        replacement["value"] = value
        replacement["description"] = f"Must satisfy {value} as a separate business classification."
        replacements.append(replacement)
    conditions[index : index + 1] = replacements
    replacement_ids = [item["condition_id"] for item in replacements]
    _replace_logic_ref(population.get("inclusion_logic"), original_id, replacement_ids)
    updated.setdefault("lifecycle", {})["state"] = "corrected"
    updated["lifecycle"]["revision"] = int(updated["lifecycle"].get("revision") or 1) + 1
    updated["lifecycle"]["confirmed_by"] = "simulated_user"
    updated["readiness"] = {"state": "ready", "blocking_paths": [], "reason": None}
    return updated, [
        {
            "operation": "split_condition_into_simultaneous_atomic_dimensions",
            "condition_id": original_id,
            "replacement_condition_ids": replacement_ids,
            "selected_option_id": str(chosen[0].get("option_id")),
        }
    ]


def apply_structured_output_presentation_response(
    bif: dict[str, Any],
    clarification: dict[str, Any],
    response: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if response.get("action") != "select":
        return None, []
    selected = {str(value) for value in response.get("selected_option_ids", [])}
    options = clarification.get("clarification_request", {}).get("options", [])
    chosen = [option for option in options if str(option.get("option_id")) in selected]
    if len(chosen) != 1:
        return None, []
    text = " ".join(
        re.findall(
            r"[a-z0-9]+",
            f"{chosen[0].get('option_id') or ''} {chosen[0].get('label') or ''}".casefold(),
        )
    )
    if any(marker in text for marker in ("separate column", "side by side", "same row")):
        presentation = "separate_columns"
    elif any(marker in text for marker in ("repeated row", "one per row", "single column")):
        presentation = "repeated_rows"
    elif "collection" in text or "array" in text:
        presentation = "collection"
    else:
        return None, []
    affected = clarification.get("clarification_request", {}).get("affected_paths", [])
    indexes = {
        int(match.group(1))
        for path in affected
        if (match := re.match(r"^/output_expectation/fields/(\d+)(?:/|$)", str(path)))
    }
    if len(indexes) != 1:
        return None, []
    updated = copy.deepcopy(bif)
    fields = updated.get("output_expectation", {}).get("fields", [])
    index = next(iter(indexes))
    if index >= len(fields):
        return None, []
    fields[index]["presentation"] = presentation
    updated.setdefault("lifecycle", {})["state"] = "corrected"
    updated["lifecycle"]["revision"] = int(updated["lifecycle"].get("revision") or 1) + 1
    updated["lifecycle"]["confirmed_by"] = "simulated_user"
    updated["readiness"] = {"state": "ready", "blocking_paths": [], "reason": None}
    return updated, [{
        "operation": "set_output_presentation",
        "field_index": index,
        "presentation": presentation,
        "selected_option_id": str(chosen[0].get("option_id")),
    }]


def apply_structured_location_role_response(
    bif: dict[str, Any],
    clarification: dict[str, Any],
    response: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if response.get("action") != "select":
        return None, []
    selected = {str(value) for value in response.get("selected_option_ids", [])}
    options = clarification.get("clarification_request", {}).get("options", [])
    chosen = [option for option in options if str(option.get("option_id")) in selected]
    if len(chosen) != 1:
        return None, []
    selected_option = chosen[0]
    effect = selected_option.get("effect")
    role: str | None = None
    if not isinstance(effect, dict):
        option_texts = [
            f"{option.get('option_id') or ''} {option.get('label') or ''}".casefold()
            for option in options
        ]
        if not (
            any("city" in text for text in option_texts)
            and any("district" in text for text in option_texts)
        ):
            return None, []
        choice_text = f"{selected_option.get('option_id') or ''} {selected_option.get('label') or ''}".casefold()
        role = "district" if "district" in choice_text else "city" if "city" in choice_text else None
        if role is None:
            return None, []
    affected = clarification.get("clarification_request", {}).get("affected_paths", [])
    indexes = {
        int(match.group(1))
        for path in affected
        if (match := re.match(r"^/target_population/inclusion_conditions/(\d+)(?:/|$)", str(path)))
    }
    if len(indexes) != 1:
        return None, []
    updated = copy.deepcopy(bif)
    conditions = updated.get("target_population", {}).get("inclusion_conditions", [])
    index = next(iter(indexes))
    if index >= len(conditions):
        return None, []
    condition = conditions[index]
    changed_fields: dict[str, Any] = {}
    if isinstance(effect, dict):
        for key in (
            "semantic_dimension", "subject", "value", "description",
            "value_mode", "set_relation", "operator",
        ):
            if key in effect:
                condition[key] = copy.deepcopy(effect[key])
                changed_fields[key] = copy.deepcopy(effect[key])
    elif role == "city":
        previous = str(condition.get("value") or "")
        value = re.sub(r"\s+City\s+Unified\s*$", "", previous, flags=re.I).strip()
        changed_fields.update({
            "semantic_dimension": "geography.city",
            "subject": "city_name",
            "value": value or previous,
            "description": f"School located in the city of {value or previous}",
        })
        condition.update(changed_fields)
    elif role == "district":
        previous = str(condition.get("value") or "")
        changed_fields.update({
            "semantic_dimension": "organization.school_district",
            "subject": "school_district",
            "description": f"School belongs to the {previous} district",
        })
        condition.update(changed_fields)
    condition["semantic_role_decision"] = {
        "option_id": str(selected_option.get("option_id") or ""),
        "label": str(selected_option.get("label") or ""),
    }
    condition["semantic_dimension_status"] = "user_confirmed"
    updated.setdefault("lifecycle", {})["state"] = "corrected"
    updated["lifecycle"]["revision"] = int(updated["lifecycle"].get("revision") or 1) + 1
    updated["lifecycle"]["confirmed_by"] = "simulated_user"
    updated["readiness"] = {"state": "ready", "blocking_paths": [], "reason": None}
    return updated, [{
        "operation": "record_semantic_role_decision",
        "condition_index": index,
        "selected_option_id": str(selected_option.get("option_id") or ""),
        "changed_fields": changed_fields,
    }]


def _replace_logic_ref(logic: Any, original_id: str, replacements: list[str]) -> None:
    if not isinstance(logic, dict):
        return
    refs = [str(value) for value in logic.get("condition_refs", [])]
    if original_id in refs:
        remaining = [value for value in refs if value != original_id]
        operator = str(logic.get("operator") or "").lower()
        if operator == "all_of":
            logic["condition_refs"] = remaining + replacements
        elif not remaining and not logic.get("groups"):
            logic["operator"] = "all_of"
            logic["condition_refs"] = list(replacements)
        else:
            logic["condition_refs"] = remaining
            logic.setdefault("groups", []).append(
                {
                    "operator": "all_of",
                    "condition_refs": list(replacements),
                    "groups": [],
                }
            )
    for group in logic.get("groups", []):
        _replace_logic_ref(group, original_id, replacements)


def _slug(value: str) -> str:
    slug = "_".join(re.findall(r"[a-z0-9]+", value.casefold()))
    return slug or "unspecified"
