from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from clarification_v2.models.source_analysis import SourceAnalysis


TOP_LEVEL_DEFAULTS = {
    "objective": {},
    "populations": [],
    "predicates": {"root": None, "atoms": []},
    "expressions": [],
    "aggregations": [],
    "groupings": [],
    "selections": [],
    "time_constraints": [],
    "comparisons": [],
    "outputs": [],
    "scopes": [],
    "deferred_grounding": [],
    "traceability": {},
    "rendered_text": "",
}

OBJECT_ARRAY_SECTIONS = {
    "populations", "expressions", "aggregations", "groupings", "selections",
    "time_constraints", "comparisons", "outputs", "scopes", "deferred_grounding",
}


def _object_array(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) if isinstance(item, Mapping) else {"description": item} for item in value]


def _normalise_observations(raw: Any) -> dict[str, Any]:
    """Accept both the intended object form and common LLM list forms.

    Intended form:
        {"objective": {...}, "populations": [...], ...}

    Also accepts list items such as:
        {"section": "outputs", "value": [...]}
        {"key": "outputs", "value": [...]}
        {"outputs": [...]} 

    Unknown list items are ignored rather than crashing the runtime.  They remain
    available in SourceAnalysis/LLM logs for diagnosis, but are not invented into
    CQD semantics here.
    """
    if raw is None:
        return {}

    if isinstance(raw, Mapping):
        return dict(raw)

    if not isinstance(raw, list):
        raise TypeError(
            "SourceAnalysis.observations must be a dict or list; "
            f"received {type(raw).__name__}"
        )

    normalised: dict[str, Any] = {}

    for item in raw:
        if not isinstance(item, Mapping):
            continue

        # Form 1: {"section": "outputs", "value": [...]}
        # Form 2: {"key": "outputs", "value": [...]}
        section = item.get("section") or item.get("key")
        if section in TOP_LEVEL_DEFAULTS and "value" in item:
            normalised[section] = deepcopy(item["value"])
            continue

        # Form 3: {"outputs": [...]} or an object containing several CQD sections.
        for key, value in item.items():
            if key in TOP_LEVEL_DEFAULTS:
                normalised[key] = deepcopy(value)

    return normalised


class InitialCQDBuilder:
    def build(self, analysis: SourceAnalysis) -> dict:
        cqd = {
            "cqd_id": f"cqd-{analysis.question_id}-initial",
            "contract_version": "1.0",
            "status": "initial",
        }
        cqd.update(deepcopy(TOP_LEVEL_DEFAULTS))

        observations = _normalise_observations(analysis.observations)
        for key, value in observations.items():
            if key in TOP_LEVEL_DEFAULTS:
                cqd[key] = (
                    _object_array(value) if key in OBJECT_ARRAY_SECTIONS
                    else deepcopy(value)
                )

        predicates = cqd.get("predicates")
        if isinstance(predicates, Mapping):
            predicates = dict(predicates)
            predicates["atoms"] = _object_array(predicates.get("atoms", []))
            predicates.setdefault("root", None)
            cqd["predicates"] = predicates

        cqd["traceability"] = {
            "question_id": analysis.question_id,
            "source": "original_question_only",
            "active_decision_ids": [],
        }
        return cqd
