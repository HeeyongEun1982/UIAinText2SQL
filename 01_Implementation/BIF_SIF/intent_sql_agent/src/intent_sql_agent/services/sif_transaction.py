from __future__ import annotations

import copy
from typing import Any

from intent_sql_agent.services.sif_coverage import validate_sif_coverage
from intent_sql_agent.services.sif_repair import (
    enforce_locked_population_sources,
    missing_required_sources,
)


def reduce_sif_candidate(
    previous: dict[str, Any],
    candidate: dict[str, Any],
    *,
    bif: dict[str, Any],
    schema_catalog: dict[str, Any],
    locked_tables: set[str],
    previous_grounding_warnings: list[dict[str, Any]] | None = None,
    candidate_grounding_warnings: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Commit a candidate only when it preserves already-grounded SIF invariants."""
    restored, events = enforce_locked_population_sources(
        previous, candidate, locked_tables, schema_catalog
    )
    if missing_required_sources(restored, locked_tables):
        return copy.deepcopy(previous), [
            *events,
            {
                "operation": "reject_sif_candidate",
                "reason": "locked_source_missing",
                "tables": sorted(missing_required_sources(restored, locked_tables)),
            },
        ]

    previous_warnings = validate_sif_coverage(bif, previous, schema_catalog)
    candidate_warnings = validate_sif_coverage(bif, restored, schema_catalog)
    previous_missing = _missing_intent_paths(previous_warnings)
    candidate_missing = _missing_intent_paths(candidate_warnings)
    lost_paths = sorted(candidate_missing - previous_missing)
    if lost_paths:
        return copy.deepcopy(previous), [
            *events,
            {
                "operation": "reject_sif_candidate",
                "reason": "bif_coverage_regression",
                "lost_intent_paths": lost_paths,
            },
        ]
    previous_untraced = _warning_count(previous_warnings, "untraced_predicate")
    candidate_untraced = _warning_count(candidate_warnings, "untraced_predicate")
    if candidate_untraced > previous_untraced:
        return copy.deepcopy(previous), [
            *events,
            {
                "operation": "reject_sif_candidate",
                "reason": "untraced_predicate_regression",
                "before": previous_untraced,
                "after": candidate_untraced,
            },
        ]
    if previous_grounding_warnings is not None and candidate_grounding_warnings is not None:
        before = {_warning_signature(item) for item in previous_grounding_warnings}
        after = {_warning_signature(item) for item in candidate_grounding_warnings}
        introduced = sorted(after - before)
        if introduced:
            return copy.deepcopy(previous), [
                *events,
                {
                    "operation": "reject_sif_candidate",
                    "reason": "grounding_warning_regression",
                    "introduced_warnings": introduced,
                },
            ]
    return restored, [
        *events,
        {
            "operation": "commit_sif_candidate",
            "coverage_warning_count_before": len(previous_warnings),
            "coverage_warning_count_after": len(candidate_warnings),
        },
    ]


def _missing_intent_paths(warnings: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("intent_path") or "")
        for item in warnings
        if item.get("issue") == "missing_bif_intent_path"
    }


def _warning_count(warnings: list[dict[str, Any]], issue: str) -> int:
    return sum(item.get("issue") == issue for item in warnings)


def _warning_signature(warning: dict[str, Any]) -> str:
    keys = ("issue", "block_id", "table", "column", "literal", "intent_path", "error")
    return "|".join(str(warning.get(key) or "") for key in keys)
