from __future__ import annotations

from collections import Counter
from typing import Any

from intent_sql_agent.services.frame_state import frame_hash
from intent_sql_agent.services.sif_v2 import (
    RESOLVED_GROUNDING_STATES,
    bif_v2_reference_sets,
    build_sif_v2_scaffold,
)


def validate_sif_v2_invariants(
    bif: dict[str, Any], sif: dict[str, Any]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    source = sif.get("source_bif", {}) or {}
    expected_source = {
        "frame_id": str(bif.get("frame_id") or ""),
        "revision": int((bif.get("lifecycle", {}) or {}).get("revision") or 1),
        "frame_hash": frame_hash(bif),
    }
    for key, expected in expected_source.items():
        if source.get(key) != expected:
            findings.append({
                "issue": "source_bif_mismatch", "field": key,
                "expected": expected, "actual": source.get(key),
            })

    expected = bif_v2_reference_sets(bif)
    population = sif.get("target_population", {}) or {}
    _check_constraint_coverage(
        findings, bif, population.get("primary_entity_grounding", {}) or {}
    )
    _check_exact_refs(
        findings, "inclusion", expected["inclusion"],
        population.get("inclusion_condition_groundings", []) or [], "condition_ref",
    )
    _check_exact_refs(
        findings, "exclusion", expected["exclusion"],
        population.get("exclusion_condition_groundings", []) or [], "condition_ref",
    )
    for section, key, kind in (
        ("metrics", "metric_groundings", "metric"),
        ("calculation", "step_groundings", "calculation"),
        ("granularity", "dimension_groundings", "granularity"),
        ("scope", "selection_groundings", "selection"),
        ("time_basis", "period_groundings", "period"),
        ("comparisons", "comparison_groundings", "comparison"),
    ):
        _check_exact_refs(
            findings, kind, expected[kind],
            (sif.get(section, {}) or {}).get(key, []) or [], "semantic_ref",
        )
    output = sif.get("output_expectation", {}) or {}
    _check_exact_refs(
        findings, "output", expected["output"],
        output.get("field_groundings", []) or [], "semantic_ref",
    )
    _check_exact_refs(
        findings, "ordering", expected["ordering"],
        output.get("ordering_groundings", []) or [], "semantic_ref",
    )
    _check_derived_implementations(findings, bif, sif)
    _check_semantic_slot_coverage(findings, bif, sif)

    if population.get("inclusion_logic_ref") != "/target_population/inclusion_logic":
        findings.append({"issue": "bif_logic_reference_changed", "kind": "inclusion"})
    if population.get("exclusion_logic_ref") != "/target_population/exclusion_logic":
        findings.append({"issue": "bif_logic_reference_changed", "kind": "exclusion"})

    sources = {
        str(item.get("source_id")): item
        for item in (sif.get("physical_support", {}) or {}).get("sources", []) or []
    }
    for slot in _all_slots(sif):
        _check_constraint_coverage(findings, bif, slot)
        for binding in slot.get("bindings", []) or []:
            if str(binding.get("source_ref")) not in sources:
                findings.append({
                    "issue": "unknown_physical_source",
                    "grounding_id": slot.get("grounding_id"),
                    "source_ref": binding.get("source_ref"),
                })

    known_refs = _known_bif_origin_refs(bif, expected)
    support = sif.get("physical_support", {}) or {}
    for node in [
        *(support.get("sources", []) or []),
        *(support.get("joins", []) or []),
        *(support.get("internal_expressions", []) or []),
        *(support.get("subquery_requirements", []) or []),
    ]:
        origins = {str(item) for item in node.get("origin_refs", []) or []}
        if not origins:
            findings.append({"issue": "physical_node_without_origin", "node": node})
        for origin in origins - known_refs:
            findings.append({"issue": "orphan_physical_origin", "origin_ref": origin})

    unresolved = sif.get("unresolved_groundings", []) or []
    if (sif.get("readiness", {}) or {}).get("state") == "ready" or sif.get("status") == "ready":
        for item in sif.get("semantic_slot_coverage", []) or []:
            if item.get("status") not in {"grounded", "not_applicable"}:
                findings.append({
                    "issue": "ready_with_unresolved_semantic_slot",
                    "semantic_slot_id": item.get("semantic_slot_id"),
                    "status": item.get("status"),
                })
        if unresolved:
            findings.append({"issue": "ready_with_unresolved_groundings"})
        primary = population.get("primary_entity_grounding", {}) or {}
        if primary.get("status") not in RESOLVED_GROUNDING_STATES or not primary.get("source_ref"):
            findings.append({"issue": "ready_with_unresolved_primary_entity"})
        for slot in _all_slots(sif):
            if (
                slot.get("status") not in RESOLVED_GROUNDING_STATES
                or not (slot.get("bindings") or slot.get("implementation"))
            ):
                findings.append({
                    "issue": "ready_with_unresolved_slot",
                    "grounding_id": slot.get("grounding_id"),
                })
    return findings


def _check_constraint_coverage(
    findings: list[dict[str, Any]], bif: dict[str, Any], slot: dict[str, Any]
) -> None:
    semantic_slot_id = str(slot.get("semantic_slot_id") or "")
    semantic_slot = next((
        item for item in (bif.get("slot_layout", {}) or {}).get("slots", []) or []
        if str(item.get("slot_id")) == semantic_slot_id
    ), None)
    if semantic_slot is None:
        return
    expected = {
        str(item.get("constraint_id"))
        for item in semantic_slot.get("constraints", []) or []
    }
    actual_items = slot.get("constraint_coverage", []) or []
    actual = [str(item.get("constraint_id")) for item in actual_items]
    if set(actual) != expected:
        findings.append({
            "issue": "constraint_coverage_mismatch",
            "grounding_id": slot.get("grounding_id"),
            "missing": sorted(expected - set(actual)),
            "orphan": sorted(set(actual) - expected),
        })
    if len(actual) != len(set(actual)):
        findings.append({
            "issue": "duplicate_constraint_coverage",
            "grounding_id": slot.get("grounding_id"),
        })
    if slot.get("status") in RESOLVED_GROUNDING_STATES:
        for item in actual_items:
            if item.get("status") not in {"grounded", "not_applicable"}:
                findings.append({
                    "issue": "resolved_slot_with_unresolved_constraint",
                    "grounding_id": slot.get("grounding_id"),
                    "constraint_id": item.get("constraint_id"),
                    "status": item.get("status"),
                })
            if item.get("status") == "grounded" and not (item.get("evidence") or []):
                findings.append({
                    "issue": "grounded_constraint_without_evidence",
                    "grounding_id": slot.get("grounding_id"),
                    "constraint_id": item.get("constraint_id"),
                })


def _check_semantic_slot_coverage(
    findings: list[dict[str, Any]], bif: dict[str, Any], sif: dict[str, Any]
) -> None:
    expected = {
        str(item.get("slot_id"))
        for item in (bif.get("slot_layout", {}) or {}).get("slots", []) or []
    }
    coverage = sif.get("semantic_slot_coverage", []) or []
    actual = [str(item.get("semantic_slot_id")) for item in coverage]
    counts = Counter(actual)
    for slot_id in sorted(expected - set(actual)):
        findings.append({"issue": "missing_semantic_slot_coverage", "semantic_slot_id": slot_id})
    for slot_id in sorted(set(actual) - expected):
        findings.append({"issue": "orphan_semantic_slot_coverage", "semantic_slot_id": slot_id})
    for slot_id, count in counts.items():
        if count > 1:
            findings.append({"issue": "duplicate_semantic_slot_coverage", "semantic_slot_id": slot_id})
    grounding_ids = {
        str(item.get("grounding_id")) for item in _all_slots(sif)
    } | {"ground_primary_entity"}
    for item in coverage:
        for grounding_id in item.get("grounding_ids", []) or []:
            if str(grounding_id) not in grounding_ids:
                findings.append({
                    "issue": "semantic_slot_unknown_grounding",
                    "semantic_slot_id": item.get("semantic_slot_id"),
                    "grounding_id": grounding_id,
                })


def _check_derived_implementations(
    findings: list[dict[str, Any]], bif: dict[str, Any], sif: dict[str, Any]
) -> None:
    expected = build_sif_v2_scaffold(
        bif,
        dialect=str((sif.get("database", {}) or {}).get("dialect") or "sqlite"),
        schema_fingerprint=(sif.get("database", {}) or {}).get("schema_fingerprint"),
    )
    expected_slots = {
        str(item.get("grounding_id")): item
        for item in _all_slots(expected)
        if item.get("implementation") is not None
    }
    actual_slots = {
        str(item.get("grounding_id")): item for item in _all_slots(sif)
    }
    for grounding_id, expected_slot in expected_slots.items():
        actual = actual_slots.get(grounding_id, {})
        if actual.get("implementation") != expected_slot.get("implementation"):
            findings.append({
                "issue": "bif_derived_implementation_changed",
                "grounding_id": grounding_id,
                "expected": expected_slot.get("implementation"),
                "actual": actual.get("implementation"),
            })


def _known_bif_origin_refs(
    bif: dict[str, Any], expected: dict[str, set[str]]
) -> set[str]:
    """Accept stable semantic IDs and their canonical BIF pointer forms.

    Model proposals historically used both forms.  They describe the same
    immutable origin, so validation must not reject a valid pointer merely
    because the scaffold uses stable IDs internally.
    """
    refs = set().union(*expected.values()) | {
        "/target_population/primary_entity",
        "/target_population/inclusion_logic",
        "/target_population/exclusion_logic",
    }
    population = bif.get("target_population", {}) or {}
    for key in ("inclusion_conditions", "exclusion_conditions"):
        for index, item in enumerate(population.get(key, []) or []):
            refs.add(f"/target_population/{key}/{index}")
            refs.add(str(item.get("condition_id") or ""))
    for section, key, id_key in (
        ("metrics", None, "metric_id"),
        ("calculation", "steps", "step_id"),
        ("scope", "selections", "selection_id"),
        ("comparisons", None, "comparison_id"),
        ("output_expectation", "fields", "field_id"),
    ):
        container = bif.get(section, {}) if key else bif.get(section, [])
        items = (container or {}).get(key, []) if key else (container or [])
        for index, item in enumerate(items):
            refs.add(f"/{section}/{key + '/' if key else ''}{index}")
            refs.add(str(item.get(id_key) or ""))
    refs.discard("")
    return refs


def validate_sif_v2_physical_schema(
    sif: dict[str, Any], schema_catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate proposed physical bindings without interpreting BIF semantics."""
    findings: list[dict[str, Any]] = []
    tables = {
        str(table.get("name")): {
            str(column.get("name")) for column in table.get("columns", []) or []
        }
        for table in schema_catalog.get("tables", []) or []
    }
    support = sif.get("physical_support", {}) or {}
    sources = {
        str(source.get("source_id")): str(source.get("name"))
        for source in support.get("sources", []) or []
    }
    for source_id, table_name in sources.items():
        if table_name not in tables:
            findings.append({
                "issue": "source_table_not_in_schema", "source_id": source_id,
                "table": table_name,
            })
    for slot in _all_slots(sif):
        for binding in slot.get("bindings", []) or []:
            source_ref = str(binding.get("source_ref"))
            table_name = sources.get(source_ref)
            column = str(binding.get("column"))
            if table_name in tables and column not in tables[table_name]:
                findings.append({
                    "issue": "binding_column_not_in_source",
                    "grounding_id": slot.get("grounding_id"),
                    "source_ref": source_ref, "table": table_name, "column": column,
                })
    for join in support.get("joins", []) or []:
        left_table = sources.get(str(join.get("left_source")))
        right_table = sources.get(str(join.get("right_source")))
        for pair in join.get("key_pairs", []) or []:
            for side, table_name in (("left", left_table), ("right", right_table)):
                column = str(pair.get(f"{side}_column"))
                if table_name in tables and column not in tables[table_name]:
                    findings.append({
                        "issue": "join_column_not_in_source", "join_id": join.get("join_id"),
                        "side": side, "table": table_name, "column": column,
                    })
    return findings


def _check_exact_refs(
    findings: list[dict[str, Any]],
    kind: str,
    expected: set[str],
    slots: list[dict[str, Any]],
    ref_key: str,
) -> None:
    actual = [str(item.get(ref_key)) for item in slots]
    counts = Counter(actual)
    for ref in sorted(expected - set(actual)):
        findings.append({"issue": "missing_grounding_slot", "kind": kind, "semantic_ref": ref})
    for ref in sorted(set(actual) - expected):
        findings.append({"issue": "orphan_grounding_slot", "kind": kind, "semantic_ref": ref})
    for ref, count in sorted(counts.items()):
        if count > 1:
            findings.append({
                "issue": "duplicate_grounding_slot", "kind": kind,
                "semantic_ref": ref, "count": count,
            })


def _all_slots(sif: dict[str, Any]) -> list[dict[str, Any]]:
    population = sif.get("target_population", {}) or {}
    result = [
        *(population.get("inclusion_condition_groundings", []) or []),
        *(population.get("exclusion_condition_groundings", []) or []),
    ]
    for section, key in (
        ("metrics", "metric_groundings"),
        ("calculation", "step_groundings"),
        ("granularity", "dimension_groundings"),
        ("scope", "selection_groundings"),
        ("time_basis", "period_groundings"),
        ("comparisons", "comparison_groundings"),
    ):
        result.extend((sif.get(section, {}) or {}).get(key, []) or [])
    output = sif.get("output_expectation", {}) or {}
    result.extend(output.get("field_groundings", []) or [])
    result.extend(output.get("ordering_groundings", []) or [])
    return result
