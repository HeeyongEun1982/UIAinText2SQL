from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any


_OPERATOR_ALIASES = {
    "equals": "equals", "=": "equals", "==": "equals",
    "not_equals": "not_equals", "!=": "not_equals", "<>": "not_equals",
    "greater_than": "greater_than", ">": "greater_than",
    "greater_or_equal": "greater_or_equal", ">=": "greater_or_equal",
    "less_than": "less_than", "<": "less_than",
    "less_or_equal": "less_or_equal", "<=": "less_or_equal",
    "between": "between", "in": "in", "not_in": "not_in", "not in": "not_in",
    "contains": "contains", "starts_with": "starts_with", "ends_with": "ends_with",
    "is_missing": "is_missing", "is_null": "is_missing",
    "is_not_missing": "is_not_missing", "is_not_null": "is_not_missing",
}
_GENERIC_TERMS = {
    "a", "an", "and", "are", "of", "or", "the",
}


def validate_sif_v2_semantics(
    bif: dict[str, Any], sif: dict[str, Any], schema_catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    """Check that physical bindings implement, rather than reinterpret, BIF nodes."""
    findings: list[dict[str, Any]] = []
    population = bif.get("target_population", {}) or {}
    grounded = sif.get("target_population", {}) or {}
    _validate_conditions(
        findings,
        population.get("inclusion_conditions", []) or [],
        grounded.get("inclusion_condition_groundings", []) or [],
        sif,
        schema_catalog,
    )
    _validate_conditions(
        findings,
        population.get("exclusion_conditions", []) or [],
        grounded.get("exclusion_condition_groundings", []) or [],
        sif,
        schema_catalog,
    )
    _validate_outputs(findings, bif, sif)
    return findings


def _validate_conditions(
    findings: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    sif: dict[str, Any],
    schema_catalog: dict[str, Any],
) -> None:
    by_ref = {str(item.get("condition_ref")): item for item in slots}
    sources = {
        str(item.get("source_id")): str(item.get("name"))
        for item in (sif.get("physical_support", {}) or {}).get("sources", []) or []
    }
    for condition in conditions:
        condition_ref = str(condition.get("condition_id"))
        slot = by_ref.get(condition_ref)
        if slot is None or not slot.get("bindings"):
            continue
        bindings = slot.get("bindings", []) or []
        expected_operator = _normalize_operator(condition.get("operator"))
        if expected_operator == "between":
            if not _valid_range_binding(condition, bindings):
                findings.append({
                    "issue": "condition_operator_or_value_changed",
                    "condition_ref": condition_ref,
                    "expected_operator": expected_operator,
                })
            continue
        for binding in bindings:
            actual_operator = _normalize_operator(binding.get("sql_operator"))
            if actual_operator != expected_operator:
                findings.append({
                    "issue": "condition_operator_changed",
                    "condition_ref": condition_ref,
                    "expected": expected_operator,
                    "actual": actual_operator,
                })
                continue
            transformation = str(binding.get("transformation") or slot.get("binding_mode") or "")
            if transformation == "code_mapping":
                if not _documented_code_mapping(
                    condition.get("value"), binding, sources, schema_catalog
                ):
                    findings.append({
                        "issue": "undocumented_code_mapping",
                        "condition_ref": condition_ref,
                        "column": binding.get("column"),
                        "physical_value": binding.get("physical_value"),
                    })
            elif transformation == "direct":
                if not _same_value(condition.get("value"), binding.get("physical_value")):
                    findings.append({
                        "issue": "condition_value_changed",
                        "condition_ref": condition_ref,
                        "expected": condition.get("value"),
                        "actual": binding.get("physical_value"),
                    })
            elif transformation not in {"type_cast", "date_component"}:
                findings.append({
                    "issue": "unverified_condition_transformation",
                    "condition_ref": condition_ref,
                    "transformation": transformation,
                })


def _validate_outputs(
    findings: list[dict[str, Any]], bif: dict[str, Any], sif: dict[str, Any]
) -> None:
    fields = {
        str(item.get("field_id")): item
        for item in (bif.get("output_expectation", {}) or {}).get("fields", []) or []
    }
    semantic_slots = {
        str(item.get("semantic_ref")): item
        for section, key in (
            ("metrics", "metric_groundings"),
            ("calculation", "step_groundings"),
            ("comparisons", "comparison_groundings"),
        )
        for item in (sif.get(section, {}) or {}).get(key, []) or []
    }
    for output in (sif.get("output_expectation", {}) or {}).get("field_groundings", []) or []:
        field_ref = str(output.get("semantic_ref"))
        field = fields.get(field_ref)
        if field is None:
            continue
        expected_source = str(field.get("semantic_ref") or "")
        actual_source = str(output.get("source_semantic_ref") or "")
        if actual_source != expected_source:
            findings.append({
                "issue": "output_semantic_source_changed", "field_ref": field_ref,
                "expected": expected_source, "actual": actual_source,
            })
            continue
        source_slot = semantic_slots.get(expected_source)
        if source_slot is not None and output.get("bindings"):
            expected_bindings = Counter(_binding_signature(item) for item in source_slot.get("bindings", []) or [])
            actual_bindings = Counter(_binding_signature(item) for item in output.get("bindings", []) or [])
            if actual_bindings != expected_bindings:
                findings.append({
                    "issue": "output_metric_binding_mismatch",
                    "field_ref": field_ref,
                    "source_semantic_ref": expected_source,
                })


def _valid_range_binding(condition: dict[str, Any], bindings: list[dict[str, Any]]) -> bool:
    value = condition.get("value")
    if isinstance(value, dict):
        start, end = value.get("start"), value.get("end")
    elif isinstance(value, list) and len(value) == 2:
        start, end = value
    else:
        return False
    if len(bindings) == 1:
        binding = bindings[0]
        actual = binding.get("physical_value")
        actual_bounds = (
            (actual.get("start"), actual.get("end")) if isinstance(actual, dict)
            else tuple(actual) if isinstance(actual, list) and len(actual) == 2
            else None
        )
        return _normalize_operator(binding.get("sql_operator")) == "between" and actual_bounds == (start, end)
    if len(bindings) == 2:
        pairs = {
            (_normalize_operator(item.get("sql_operator")), _stable(item.get("physical_value")))
            for item in bindings
        }
        return pairs == {
            ("greater_or_equal", _stable(start)),
            ("less_or_equal", _stable(end)),
        }
    return False


def _documented_code_mapping(
    business_value: Any,
    binding: dict[str, Any],
    sources: dict[str, str],
    schema_catalog: dict[str, Any],
) -> bool:
    table_name = sources.get(str(binding.get("source_ref")))
    column_name = str(binding.get("column"))
    physical_value = str(binding.get("physical_value"))
    table = next(
        (item for item in schema_catalog.get("tables", []) or [] if str(item.get("name")) == table_name),
        None,
    )
    column = next(
        (item for item in (table or {}).get("columns", []) or [] if str(item.get("name")) == column_name),
        None,
    )
    target_terms = _terms(str(business_value))
    if not target_terms or column is None:
        return False
    matches: list[tuple[float, str]] = []
    for line in str(column.get("value_description") or "").splitlines():
        match = re.match(r"^\D*(\d+)\D+?([A-Za-z].*?)\s*$", line)
        if not match:
            continue
        label_terms = _terms(match.group(2))
        score = len(target_terms & label_terms) / len(target_terms) if target_terms else 0.0
        if score >= 0.75:
            matches.append((score, match.group(1)))
    if not matches:
        return False
    best = max(score for score, _ in matches)
    codes = {code for score, code in matches if score == best}
    return codes == {physical_value}


def _normalize_operator(value: Any) -> str:
    return _OPERATOR_ALIASES.get(str(value or "").casefold(), str(value or "").casefold())


def _same_value(left: Any, right: Any) -> bool:
    return _stable(left) == _stable(right)


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _binding_signature(binding: dict[str, Any]) -> str:
    return _stable({
        "source_ref": binding.get("source_ref"),
        "column": binding.get("column"),
        "transformation": binding.get("transformation"),
    })


def _terms(value: str) -> set[str]:
    return {
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in _GENERIC_TERMS
    }
