from __future__ import annotations

import copy
import re
from typing import Any


_PRESENTATION_MARKERS = (
    "separate column", "separate field", "side by side", "same row",
    "one per row", "one value per row", "repeated row", "single column",
    "array", "collection",
)
_UNSUPPORTED_VALIDATION_MARKERS = (
    "active", "verified", "verification", "standard email format",
    "standard e-mail format", "domain restriction", "specific domain", "regex",
    "accurate", "complete", "recorded", "reliable", "valid",
)


def normalize_initial_bif(
    question: str, bif: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove model defaults for business choices the user did not state."""
    repaired = copy.deepcopy(bif)
    repairs: list[dict[str, Any]] = []
    question_text = _normalize(question)
    _dedupe_scalar_lists(repaired, repairs)

    entity_match = re.search(r"\bthe\s+(set of [a-z][a-z0-9_-]*)\s+that\b", question_text)
    population = repaired.get("target_population", {})
    primary_entity = _normalize(str(population.get("primary_entity") or ""))
    if entity_match:
        exact_entity = entity_match.group(1)
        member = exact_entity.removeprefix("set of ").rstrip("s")
        if primary_entity.rstrip("s") == member and primary_entity != exact_entity:
            population["primary_entity"] = exact_entity
            description = str(population.get("entity_description") or "")
            if exact_entity not in _normalize(description):
                population["entity_description"] = f"The user-stated {exact_entity}"
            repairs.append({
                "operation": "preserve_user_entity_phrase",
                "path": "/target_population/primary_entity",
                "from": primary_entity,
                "to": exact_entity,
            })

    if not any(marker in question_text for marker in _PRESENTATION_MARKERS):
        for index, field in enumerate(
            repaired.get("output_expectation", {}).get("fields", []) or []
        ):
            if (
                field.get("value_multiplicity") == "multiple"
                and field.get("presentation") != "unresolved"
            ):
                previous = field.get("presentation")
                field["presentation"] = "unresolved"
                repairs.append({
                    "operation": "defer_unstated_output_presentation",
                    "path": f"/output_expectation/fields/{index}/presentation",
                    "from": previous,
                    "to": "unresolved",
                })

    unsupported = [
        marker for marker in _UNSUPPORTED_VALIDATION_MARKERS
        if marker not in question_text
    ]
    if unsupported:
        for index, metric in enumerate(repaired.get("metrics", []) or []):
            definition = str(metric.get("definition") or "")
            if any(marker in _normalize(definition) for marker in unsupported):
                metric["definition"] = (
                    f"{metric.get('name') or 'Requested value'} as stored; "
                    "the user did not specify an additional operational validation test."
                )
                repairs.append({
                    "operation": "remove_unstated_validation_rule",
                    "path": f"/metrics/{index}/definition",
                })
        calculation = repaired.get("calculation", {})
        kept_steps = []
        for step in calculation.get("steps", []) or []:
            text = _normalize(f"{step.get('description') or ''} {step.get('method') or ''}")
            if any(marker in text for marker in unsupported):
                repairs.append({
                    "operation": "remove_unstated_validation_step",
                    "step_id": step.get("step_id"),
                })
            else:
                kept_steps.append(step)
        calculation["steps"] = kept_steps
        repaired["assumptions"] = [
            assumption for assumption in repaired.get("assumptions", []) or []
            if not any(
                marker in _normalize(str(assumption.get("description") or ""))
                for marker in unsupported
            )
        ]
    return repaired, repairs


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _dedupe_scalar_lists(
    node: Any,
    repairs: list[dict[str, Any]],
    path: str = "",
) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _dedupe_scalar_lists(value, repairs, f"{path}/{key}")
        return
    if not isinstance(node, list):
        return
    if all(not isinstance(value, (dict, list)) for value in node):
        unique: list[Any] = []
        for value in node:
            if value not in unique:
                unique.append(value)
        if len(unique) != len(node):
            removed = len(node) - len(unique)
            node[:] = unique
            repairs.append({
                "operation": "dedupe_scalar_list",
                "path": path or "/",
                "removed": removed,
            })
        return
    for index, value in enumerate(node):
        _dedupe_scalar_lists(value, repairs, f"{path}/{index}")
