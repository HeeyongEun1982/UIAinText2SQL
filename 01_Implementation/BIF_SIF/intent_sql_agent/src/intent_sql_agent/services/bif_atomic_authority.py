from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any


_INVENTED_VALIDATION_MARKERS = (
    "non null", "not null", "properly formatted", "standard email format",
    "standard e mail format", "deliverable", "verified", "regex",
)


_ORDER_MARKERS = (
    " order by ", " ordered by ", " sort by ", " sorted by ",
    " ascending", " descending", " highest", " lowest", " top ", " bottom ",
)
_DISTINCT_MARKERS = (" distinct ", " unique ", " without duplicates", " deduplicate")
def normalize_bif_atomic_authority(
    question: str, bif: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Preserve explicit atoms and defer schema-owned semantic dimensions.

    This component never reads a schema.  Its job is to stop a model from
    collapsing coordinated business phrases or enriching the request.
    """
    result = copy.deepcopy(bif)
    repairs: list[dict[str, Any]] = []
    population = result.get("target_population", {}) or {}
    for collection_name, logic_name in (
        ("inclusion_conditions", "inclusion_logic"),
        ("exclusion_conditions", "exclusion_logic"),
    ):
        conditions = population.get(collection_name, []) or []
        logic = population.get(logic_name, {}) or {}
        index = 0
        while index < len(conditions):
            condition = conditions[index]
            _annotate_condition(question, condition)
            values = condition.get("value")
            if (
                condition.get("operator") in {"in", "not_in"}
                and condition.get("set_relation") == "any_of"
                and isinstance(values, list)
                and len(values) >= 2
                and _question_joins_values_with_and(question, values)
            ):
                replacements = _split_atomic_condition(condition, values)
                conditions[index:index + 1] = replacements
                _replace_logic_ref(
                    logic,
                    str(condition.get("condition_id")),
                    [str(item["condition_id"]) for item in replacements],
                )
                repairs.append({
                    "operation": "preserve_coordinated_atomic_conditions",
                    "path": f"/target_population/{collection_name}/{index}",
                    "source_condition_id": condition.get("condition_id"),
                    "replacement_condition_ids": [
                        item["condition_id"] for item in replacements
                    ],
                    "grounding_stage": "sif",
                })
                index += len(replacements)
                continue
            index += 1

    _remove_unrequested_enrichment(question, result, repairs)
    return result, repairs


def apply_bif_atomic_authority(
    question: str,
    authoritative_draft: dict[str, Any],
    model_candidate: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Overlay immutable semantic inventory on an A3 whole-frame proposal."""
    baseline, baseline_repairs = normalize_bif_atomic_authority(
        question, authoritative_draft
    )
    candidate, candidate_repairs = normalize_bif_atomic_authority(
        question, model_candidate
    )
    events = [*baseline_repairs, *candidate_repairs]
    base_population = baseline.get("target_population", {}) or {}
    candidate_population = candidate.setdefault("target_population", {})
    for key in (
        "primary_entity", "entity_description", "inclusion_conditions",
        "inclusion_logic", "exclusion_conditions", "exclusion_logic",
    ):
        if candidate_population.get(key) != base_population.get(key):
            events.append({
                "operation": "restore_authoritative_bif_section",
                "path": f"/target_population/{key}",
                "reason": "A3_cannot_rewrite_atomic_population_inventory",
            })
            candidate_population[key] = copy.deepcopy(base_population.get(key))

    for key in ("metrics", "calculation", "granularity", "scope", "time_basis", "comparisons"):
        if candidate.get(key) != baseline.get(key):
            events.append({
                "operation": "restore_authoritative_bif_section",
                "path": f"/{key}",
                "reason": "A3_cannot_add_or_reinterpret_business_semantics",
            })
            candidate[key] = copy.deepcopy(baseline.get(key))

    base_output = baseline.get("output_expectation", {}) or {}
    candidate_output = candidate.setdefault("output_expectation", {})
    for key in ("shape", "fields", "ordering", "limit", "include_supporting_values"):
        if candidate_output.get(key) != base_output.get(key):
            events.append({
                "operation": "restore_authoritative_bif_section",
                "path": f"/output_expectation/{key}",
                "reason": "A3_cannot_add_or_reinterpret_output_intent",
            })
            candidate_output[key] = copy.deepcopy(base_output.get(key))
    return candidate, events


def validate_bif_atomic_admission(
    question: str,
    authoritative_draft: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline, _ = normalize_bif_atomic_authority(question, authoritative_draft)
    admitted, _ = normalize_bif_atomic_authority(question, candidate)
    findings: list[dict[str, Any]] = []
    for collection in ("inclusion_conditions", "exclusion_conditions"):
        expected = _atom_inventory(
            (baseline.get("target_population", {}) or {}).get(collection, []) or []
        )
        actual = _atom_inventory(
            (admitted.get("target_population", {}) or {}).get(collection, []) or []
        )
        if actual != expected:
            findings.append({
                "issue": "atomic_condition_inventory_changed",
                "path": f"/target_population/{collection}",
                "expected": dict(expected), "actual": dict(actual),
            })
    for path, expected, actual in (
        ("/target_population/inclusion_logic",
         (baseline.get("target_population", {}) or {}).get("inclusion_logic"),
         (admitted.get("target_population", {}) or {}).get("inclusion_logic")),
        ("/target_population/exclusion_logic",
         (baseline.get("target_population", {}) or {}).get("exclusion_logic"),
         (admitted.get("target_population", {}) or {}).get("exclusion_logic")),
        ("/output_expectation/fields",
         (baseline.get("output_expectation", {}) or {}).get("fields"),
         (admitted.get("output_expectation", {}) or {}).get("fields")),
        ("/output_expectation/ordering",
         (baseline.get("output_expectation", {}) or {}).get("ordering"),
         (admitted.get("output_expectation", {}) or {}).get("ordering")),
    ):
        if actual != expected:
            findings.append({
                "issue": "authoritative_bif_section_changed", "path": path,
            })
    return findings


def route_bif_issue_observations(
    bif: dict[str, Any],
    candidates: list[dict[str, Any]],
    semantic_findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep user-owned gaps and defer schema-owned observations to SIF."""
    finding_issues = {str(item.get("issue") or "") for item in semantic_findings}
    deferred_paths = _deferred_condition_paths(bif)
    accepted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for candidate in candidates:
        issue = str(candidate.get("issue_type") or "")
        reason: str | None = None
        if _candidate_targets_deferred_dimension(candidate, deferred_paths):
            reason = "schema_owned_deferred_dimension"
        elif issue == "LOGIC_RELATION" and "question_logic_mismatch" not in finding_issues:
            reason = "logic_already_preserved_by_atomic_normalizer"
        elif (
            issue == "POPULATION_CONDITION_STRUCTURE"
            and "compound_condition_not_atomic" not in finding_issues
            and "question_logic_mismatch" not in finding_issues
        ):
            reason = "condition_structure_already_normalized"
        if reason:
            deferred.append({
                "operation": "route_bif_observation",
                "target_stage": "sif" if "schema" in reason else "system",
                "reason": reason,
                "candidate": copy.deepcopy(candidate),
            })
        else:
            accepted.append(candidate)
    return accepted, deferred


def _deferred_condition_paths(bif: dict[str, Any]) -> set[str]:
    population = bif.get("target_population", {}) or {}
    result: set[str] = set()
    for collection in ("inclusion_conditions", "exclusion_conditions"):
        for index, condition in enumerate(population.get(collection, []) or []):
            if condition.get("semantic_dimension_status") != "deferred_to_sif":
                continue
            base = f"/target_population/{collection}/{index}"
            result.update({
                base,
                f"{base}/semantic_dimension",
                str(condition.get("condition_id") or ""),
            })
    result.discard("")
    return result


def _candidate_targets_deferred_dimension(
    candidate: dict[str, Any], deferred_paths: set[str]
) -> bool:
    """Route only an observation aimed at a deferred condition.

    A broad semantic-role observation still belongs to BIF. An unrelated
    deferred condition must not suppress a clarification about another atom.
    """
    subject_ref = str(candidate.get("subject_ref") or "")
    if not subject_ref:
        return False
    path_match = any(
        subject_ref == path
        or subject_ref.startswith(f"{path}/")
        for path in deferred_paths
        if path.startswith("/")
    )
    id_match = subject_ref in {path for path in deferred_paths if not path.startswith("/")}
    if not (path_match or id_match):
        return False
    issue = str(candidate.get("issue_type") or "")
    return issue in {
        "LOCATION_ROLE", "TARGET_POPULATION", "POPULATION_CONDITION_STRUCTURE",
    } or subject_ref.endswith("/semantic_dimension")


def _annotate_condition(question: str, condition: dict[str, Any]) -> None:
    value = condition.get("value")
    source_text = (
        str(value) if not isinstance(value, (list, dict))
        else " and ".join(str(item) for item in value) if isinstance(value, list)
        else str(condition.get("description") or "")
    )
    condition.setdefault("source_text", source_text)
    condition.setdefault("business_concept", str(condition.get("subject") or source_text))
    dimension = str(condition.get("semantic_dimension") or "")
    if _requires_schema_grounding(condition):
        condition["semantic_dimension"] = None
        condition["semantic_dimension_status"] = "deferred_to_sif"
        condition["grounding_requirement"] = "schema"
    else:
        condition.setdefault(
            "semantic_dimension_status",
            "explicit" if dimension and dimension.casefold() in _normalize(question)
            else "inferred_non_material",
        )
        condition.setdefault("grounding_requirement", "schema")
    condition.setdefault("state", "explicit")


def _requires_schema_grounding(condition: dict[str, Any]) -> bool:
    dimension = str(condition.get("semantic_dimension") or "").casefold()
    value = _normalize(str(condition.get("value") or ""))
    subject = _normalize(str(condition.get("subject") or ""))
    return (
        dimension in {"school.type", "classification", "custom", ""}
        or "classification" in dimension
        or (dimension.endswith(".city") and value.endswith("city unified"))
        or subject in {"school type", "location"}
    )


def _split_atomic_condition(
    condition: dict[str, Any], values: list[Any]
) -> list[dict[str, Any]]:
    base_id = str(condition.get("condition_id") or "condition")
    result: list[dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        atom = copy.deepcopy(condition)
        atom.update({
            "condition_id": f"{base_id}_{index}",
            "source_text": str(value),
            "business_concept": str(value),
            "semantic_dimension": None,
            "semantic_dimension_status": "deferred_to_sif",
            "grounding_requirement": "schema",
            "state": "explicit",
            "subject": str(value),
            "operator": "equals",
            "value_mode": "scalar",
            "set_relation": "not_applicable",
            "value": value,
            "description": f"The request explicitly requires {value}.",
        })
        result.append(atom)
    return result


def _remove_unrequested_enrichment(
    question: str, bif: dict[str, Any], repairs: list[dict[str, Any]]
) -> None:
    normalized = f" {_normalize(question)} "
    calculation = bif.get("calculation", {}) or {}
    if not any(marker in normalized for marker in _DISTINCT_MARKERS):
        if calculation.get("duplicate_handling") not in {None, "not_specified"}:
            repairs.append({
                "operation": "remove_unrequested_distinct_semantics",
                "from": calculation.get("duplicate_handling"),
            })
        calculation["duplicate_handling"] = None
    steps = calculation.get("steps", []) or []
    retained = []
    for step in steps:
        if step.get("operation") == "retrieve":
            repairs.append({
                "operation": "remove_non_calculation_retrieve_step",
                "step_id": step.get("step_id"),
            })
        else:
            retained.append(step)
    calculation["steps"] = retained

    output = bif.get("output_expectation", {}) or {}
    if not any(marker in normalized for marker in _ORDER_MARKERS):
        if output.get("ordering"):
            repairs.append({"operation": "remove_unrequested_ordering"})
        output["ordering"] = []
    metric_refs = {str(item.get("metric_id")) for item in bif.get("metrics", []) or []}
    question_terms = _meaningful_terms(question)
    kept_fields = []
    for field in output.get("fields", []) or []:
        semantic_ref = str(field.get("semantic_ref") or "")
        label_terms = _meaningful_terms(str(field.get("label") or ""))
        requested = semantic_ref in metric_refs or bool(label_terms and label_terms <= question_terms)
        if requested:
            field.setdefault("source_text", _best_source_text(question, field))
            field.setdefault("requested", True)
            kept_fields.append(field)
        else:
            repairs.append({
                "operation": "remove_unrequested_output_field",
                "field_id": field.get("field_id"),
            })
    if kept_fields:
        output["fields"] = kept_fields

    for metric in bif.get("metrics", []) or []:
        definition = _normalize(str(metric.get("definition") or ""))
        invented = [term for term in _INVENTED_VALIDATION_MARKERS
                    if term in definition and term not in normalized]
        if invented:
            metric["definition"] = (
                f"{metric.get('name') or 'Requested value'} as requested; "
                "the operational validation rule is deferred to schema grounding."
            )
            metric["validation_semantics"] = {
                "status": "deferred_to_sif", "rule": None,
            }
            repairs.append({
                "operation": "defer_unrequested_validation_rule",
                "metric_id": metric.get("metric_id"), "removed_markers": invented,
            })

def _replace_logic_ref(logic: Any, old: str, replacements: list[str]) -> None:
    if not isinstance(logic, dict):
        return
    refs = [str(item) for item in logic.get("condition_refs", []) or []]
    if old in refs:
        position = refs.index(old)
        refs[position:position + 1] = replacements
        logic["condition_refs"] = refs
        logic["operator"] = "all_of"
    for group in logic.get("groups", []) or []:
        _replace_logic_ref(group, old, replacements)


def _question_joins_values_with_and(question: str, values: list[Any]) -> bool:
    normalized = _normalize(question)
    terms = [_normalize(str(item)) for item in values]
    positions = [normalized.find(term) for term in terms]
    if any(position < 0 for position in positions):
        return False
    ordered = sorted(zip(positions, terms))
    for (left_at, left), (right_at, _) in zip(ordered, ordered[1:]):
        if re.search(r"\band\b", normalized[left_at + len(left):right_at]):
            return True
    return False


def _atom_inventory(conditions: list[dict[str, Any]]) -> Counter[str]:
    return Counter(
        json.dumps({
            "source_text": _normalize(str(item.get("source_text") or item.get("value") or "")),
            "operator": item.get("operator"),
            "value": item.get("value"),
        }, ensure_ascii=False, sort_keys=True, default=str)
        for item in conditions
    )


def _best_source_text(question: str, field: dict[str, Any]) -> str:
    label = str(field.get("label") or "").strip()
    return label if _normalize(label) in _normalize(question) else str(field.get("semantic_ref") or label)


def _meaningful_terms(value: str) -> set[str]:
    ignored = {"a", "an", "the", "of", "for", "school", "schools", "valid"}
    return {term for term in re.findall(r"[a-z0-9]+", value.casefold()) if term not in ignored}


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
