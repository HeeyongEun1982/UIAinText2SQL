from __future__ import annotations

import copy
from typing import Any


CQD_TO_BIF_ROOTS: dict[str, tuple[str, ...]] = {
    "objective": ("decision_objective",),
    "populations": ("target_population",),
    "predicates": ("target_population",),
    "expressions": ("metrics", "calculation"),
    "aggregations": ("calculation",),
    "groupings": ("granularity", "scope"),
    "selections": ("scope",),
    "time_constraints": ("time_basis",),
    "comparisons": ("comparisons",),
    "outputs": ("output_expectation",),
    "scopes": ("scope",),
    "deferred_grounding": ("target_population", "metrics", "output_expectation"),
}


def complete_bif_layout_structure(
    confirmed_cqd: dict[str, Any], bif: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retained compatibility hook; never invent a missing semantic choice."""
    completed = copy.deepcopy(bif)
    repairs: list[dict[str, Any]] = []
    del confirmed_cqd
    return completed, repairs


def validate_cqd_bif_layout_coverage(
    confirmed_cqd: dict[str, Any], bif: dict[str, Any]
) -> dict[str, Any]:
    coverage: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for cqd_ref, root in _semantic_refs(confirmed_cqd):
        candidates = CQD_TO_BIF_ROOTS[root]
        mapped = [f"/{name}" for name in candidates if _material(bif.get(name))]
        item = {
            "cqd_ref": cqd_ref,
            "bif_refs": mapped,
            "status": "mapped" if mapped else "missing",
        }
        coverage.append(item)
        if not mapped:
            missing.append(item)
    if missing:
        raise ValueError(
            "BIF_LAYOUT_COVERAGE_FAILED: "
            + ", ".join(str(item["cqd_ref"]) for item in missing)
        )
    return {
        "status": "complete",
        "confirmed_cqd_hash": confirmed_cqd.get("traceability", {}).get("state_hash"),
        "coverage": coverage,
        "unmapped_cqd_refs": [],
    }


def _semantic_refs(cqd: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for root in CQD_TO_BIF_ROOTS:
        value = cqd.get(root)
        if not _material(value):
            continue
        if root == "predicates" and isinstance(value, dict):
            if _material(value.get("root")):
                refs.append(("/predicates/root", root))
            refs.extend(
                (f"/predicates/atoms/{index}", root)
                for index, item in enumerate(value.get("atoms", []))
                if _material(item)
            )
        elif isinstance(value, list):
            refs.extend(
                (f"/{root}/{index}", root)
                for index, item in enumerate(value)
                if _material(item)
            )
        else:
            refs.append((f"/{root}", root))
    return refs


def _material(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True
