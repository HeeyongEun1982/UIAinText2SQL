from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from clarification_v2.models.source_analysis import SourceAnalysis, AmbiguityHint


SYSTEM = r'''You are the Source Analyzer for a CQD clarification system.

INPUT BOUNDARY
- You receive the user's original natural-language question and may receive benchmark_evidence.
- Use benchmark_evidence as supplied supporting context throughout semantic analysis. It may resolve terminology, codes, or intended database concepts, but it must never override an explicit user statement.
- You MUST NOT request, use, or infer Gold SQL or Gold CQD. Do not invent schema facts absent from the supplied evidence.
- Your job is to represent what is explicit in the question and identify user-owned semantic choices that genuinely need clarification.

OUTPUT GOAL
Build a useful Initial CQD observation object. Never return an empty CQD for a non-empty question.
Extract all semantics that are explicit and safe from the wording or supplied evidence. Do NOT turn information established by evidence into an ambiguity merely because the database implementation is unknown. Keep physical table/column/code details in deferred_grounding while preserving their business meaning in the relevant CQD sections.

The observations object MUST use exactly these CQD sections:
- objective: object
- populations: array
- predicates: {"root": ..., "atoms": [...]}
- expressions: array
- aggregations: array
- groupings: array
- selections: array
- time_constraints: array
- comparisons: array
- outputs: array
- scopes: array
- deferred_grounding: array

MINIMUM COMPLETENESS
For every non-empty question:
1. objective MUST be non-empty.
2. populations MUST contain at least one user-level population/entity.
3. outputs MUST contain at least one requested result.
4. Explicit filters, dates, ranking, aggregation, comparison, grouping, calculation, and output multiplicity MUST be represented when present in the question.
5. predicates MUST preserve explicit AND/OR/NOT structure. Do not flatten OR into an AND list.

AMBIGUITY POLICY
Allowed ambiguity families only:
SEMANTIC_DIMENSION, ENTITY_BOUNDARY, BOOLEAN_ATTACHMENT, METRIC_DEFINITION,
CALCULATION_FORMULA, AGGREGATE_TARGET, GROUPING_GRAIN, SELECTION_SCOPE,
TIME_ATTACHMENT, COMPARISON_DIRECTION, OUTPUT_EXPECTATION, SET_RELATION, TIE_POLICY.

Ask-worthy ambiguity means:
- at least two plausible USER meanings exist from the wording, AND
- choosing between them could change population, predicate logic, expression/aggregation, grain/scope, comparison, time attachment, or output/cardinality.
Do NOT flag schema grounding choices, synonyms with the same meaning, table/column choices, join choices, or implementation details.
Do NOT choose the correct interpretation.

UNRESOLVED PLACEHOLDERS
When a material user-owned ambiguity exists, create an explicit placeholder object in the relevant CQD section rather than guessing. The placeholder MUST have:
{
  "resolution_status": "unresolved",
  "source_text": "exact ambiguous phrase",
  "semantic_role": "what is undecided",
  "stable_id": "a stable id that candidate replacements should preserve where useful"
}
Then create an ambiguity_hint whose target_path is an RFC-6901 JSON pointer to THAT WHOLE placeholder object, for example /predicates/atoms/2 or /selections/0.
The target_path MUST point to an object that actually exists in observations.

PREDICATE GUIDANCE
- Certain conditions should be normal atoms with condition_id, dimension, operator, value, applies_to, source_text.
- Ambiguous predicate meaning should be one unresolved placeholder atom in predicates.atoms and the predicate root should refer to its stable_id using an atom_ref just like other atoms.
- Preserve AND/OR/NOT in predicates.root.

OUTPUT FORMAT
Return exactly one JSON object in this shape:
{
  "observations": {
    "objective": {},
    "populations": [],
    "predicates": {"root": null, "atoms": []},
    "expressions": [],
    "aggregations": [],
    "groupings": [],
    "selections": [],
    "time_constraints": [],
    "comparisons": [],
    "outputs": [],
    "scopes": [],
    "deferred_grounding": []
  },
  "ambiguity_hints": [
    {
      "hint_id": "unique-id",
      "family": "ONE_ALLOWED_FAMILY",
      "source_span": "exact phrase from question",
      "description": "what user meaning is undecided and why alternatives are material",
      "target_path": "/exact/existing/placeholder/path"
    }
  ]
}
'''


REPAIR_SYSTEM = SYSTEM + r'''

The previous answer was structurally incomplete. Repair it using the validation_errors provided by the caller.
Do not remove explicit question semantics merely to satisfy validation. Return the complete JSON object again.
'''


_REQUIRED_SECTIONS = {
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
}


def _normalize_observations(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        result = dict(raw)
    elif isinstance(raw, list):
        # Defensive compatibility with occasional LLM list-shaped outputs.
        result: dict[str, Any] = {}
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            section = item.get("section") or item.get("key")
            if section in _REQUIRED_SECTIONS and "value" in item:
                result[section] = item["value"]
                continue
            for key, value in item.items():
                if key in _REQUIRED_SECTIONS:
                    result[key] = value
    else:
        result = {}

    for key, default in _REQUIRED_SECTIONS.items():
        if key not in result:
            # Missing optional sections get canonical empty containers, but the
            # semantic minimum below still rejects an empty analysis.
            result[key] = default.copy() if isinstance(default, dict) else list(default)
    if not isinstance(result.get("predicates"), Mapping):
        result["predicates"] = {"root": None, "atoms": []}
    result["predicates"].setdefault("root", None)
    result["predicates"].setdefault("atoms", [])
    return result




def _resolve_pointer(document: Any, pointer: str):
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node = document
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, Mapping):
            node = node[part]
        else:
            raise KeyError(pointer)
    return node

def _analysis_errors(observations: dict[str, Any], hints: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not isinstance(observations.get("objective"), Mapping) or not observations["objective"]:
        errors.append("objective must be a non-empty object")
    if not isinstance(observations.get("populations"), list) or not observations["populations"]:
        errors.append("populations must contain at least one item")
    if not isinstance(observations.get("outputs"), list) or not observations["outputs"]:
        errors.append("outputs must contain at least one item")
    predicates = observations.get("predicates", {})
    if not isinstance(predicates, Mapping):
        errors.append("predicates must be an object")
    else:
        if not isinstance(predicates.get("atoms", []), list):
            errors.append("predicates.atoms must be an array")
    for i, hint in enumerate(hints):
        if not isinstance(hint, Mapping):
            errors.append(f"ambiguity_hints[{i}] must be an object")
            continue
        for field in ("hint_id", "family", "source_span", "description", "target_path"):
            if not str(hint.get(field, "")).strip():
                errors.append(f"ambiguity_hints[{i}].{field} is required")
        if hint.get("family") not in {
            "SEMANTIC_DIMENSION", "ENTITY_BOUNDARY", "BOOLEAN_ATTACHMENT", "METRIC_DEFINITION",
            "CALCULATION_FORMULA", "AGGREGATE_TARGET", "GROUPING_GRAIN", "SELECTION_SCOPE",
            "TIME_ATTACHMENT", "COMPARISON_DIRECTION", "OUTPUT_EXPECTATION", "SET_RELATION", "TIE_POLICY",
        }:
            errors.append(f"ambiguity_hints[{i}].family is not allowed")
        target_path = str(hint.get("target_path", ""))
        if not target_path.startswith("/"):
            errors.append(f"ambiguity_hints[{i}].target_path must be a JSON pointer")
        else:
            try:
                target = _resolve_pointer(observations, target_path)
            except (KeyError, IndexError, ValueError, TypeError):
                errors.append(f"ambiguity_hints[{i}].target_path does not point to an existing CQD object")
            else:
                if not isinstance(target, Mapping):
                    errors.append(f"ambiguity_hints[{i}].target_path must point to an object")
    return errors


def _normalize_hints(
    observations: dict[str, Any], raw_hints: Any,
) -> list[dict[str, Any]]:
    """Repair representation-only pointer variants without semantic rewriting."""
    if not isinstance(raw_hints, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in raw_hints:
        if not isinstance(raw, Mapping):
            result.append(raw)
            continue
        hint = dict(raw)
        pointer = str(hint.get("target_path") or "")
        if pointer == "/observations":
            candidate = ""
        elif pointer.startswith("/observations/"):
            candidate = pointer[len("/observations"):]
        else:
            candidate = pointer
        if candidate:
            try:
                _resolve_pointer(observations, candidate)
            except (KeyError, IndexError, ValueError, TypeError):
                pass
            else:
                hint["target_path"] = candidate
        result.append(hint)
    return result


class SourceAnalyzer:
    def __init__(self, *, model, model_name: str, max_total_attempts: int = 5) -> None:
        self.model = model
        self.model_name = model_name
        self.max_total_attempts = max(1, int(max_total_attempts))

    def analyze(self, *, question_id: str, original_question: str) -> SourceAnalysis:
        payload = {"question_id": question_id, "original_question": original_question}
        out = self.model.complete_json(
            agent="source_analyzer",
            system=SYSTEM,
            payload=payload,
            model=self.model_name,
        )

        observations = _normalize_observations(out.get("observations"))
        raw_hints = _normalize_hints(observations, out.get("ambiguity_hints", []))
        errors = _analysis_errors(observations, raw_hints)

        repair_attempt = 0
        while errors and repair_attempt + 1 < self.max_total_attempts:
            repair_attempt += 1
            repair_payload = {
                **payload,
                "previous_output": out,
                "validation_errors": errors,
                "repair_attempt": repair_attempt,
            }
            out = self.model.complete_json(
                agent="source_analyzer",
                system=REPAIR_SYSTEM,
                payload=repair_payload,
                model=self.model_name,
            )
            observations = _normalize_observations(out.get("observations"))
            raw_hints = _normalize_hints(
                observations, out.get("ambiguity_hints", [])
            )
            errors = _analysis_errors(observations, raw_hints)

        if errors:
            # The attempt limit stops repeated model calls, not the pipeline.
            # Preserve all usable model content and add only explicit unresolved
            # placeholders for structurally required sections.  Invalid hints are
            # omitted rather than allowed to mutate CQD through a broken pointer.
            if not isinstance(observations.get("objective"), Mapping) or not observations["objective"]:
                observations["objective"] = {
                    "description": original_question,
                    "resolution_status": "unresolved_system",
                }
            if not isinstance(observations.get("populations"), list) or not observations["populations"]:
                observations["populations"] = [{
                    "entity": "unspecified subject",
                    "source_text": original_question,
                    "resolution_status": "unresolved_system",
                }]
            if not isinstance(observations.get("outputs"), list) or not observations["outputs"]:
                observations["outputs"] = [{
                    "description": "requested result",
                    "source_text": original_question,
                    "resolution_status": "unresolved_system",
                }]
            valid_hints = []
            for hint in raw_hints:
                hint_errors = [
                    error for error in _analysis_errors(observations, [hint])
                    if error.startswith("ambiguity_hints")
                ]
                if not hint_errors:
                    valid_hints.append(hint)
            raw_hints = valid_hints

        hints = [AmbiguityHint(**x) for x in raw_hints]
        return SourceAnalysis(
            question_id=question_id,
            original_question=original_question,
            observations=observations,
            ambiguity_hints=hints,
        )
