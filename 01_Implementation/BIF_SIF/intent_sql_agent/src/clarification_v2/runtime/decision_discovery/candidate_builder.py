from __future__ import annotations

from dataclasses import asdict
from clarification_v2.models.decision import Candidate, DecisionSpec

SYSTEM = r'''You are the Candidate Interpretation Builder in a schema-free CQD clarification system.
You receive the original-question analysis, the CURRENT CQD, exactly one ambiguity hint, its clarification contract, and possibly benchmark_evidence.

Generate 2-4 plausible, mutually distinguishable USER-MEANING candidates for that ambiguity only.
Use supplied benchmark_evidence to avoid proposing a choice that it already resolves. If evidence establishes one candidate without contradicting the question, represent that supported meaning faithfully; do not invent alternatives from physical naming alone. Never use Gold SQL or Gold CQD, and do not invent schema facts absent from the supplied evidence.
Do not guess the correct answer.

EFFECT CONTRACT
- Every candidate MUST contain exactly one typed_effect.
- typed_effect.operation MUST be "replace".
- typed_effect.target_ref MUST be EXACTLY the supplied hint.target_path.
- typed_effect.value MUST be the complete resolved schema-free semantic object that should replace the unresolved placeholder at that path.
- Preserve any stable identity from the unresolved placeholder when useful (for example condition_id/stable_id) so references remain coherent.
- Candidate values must differ materially in user meaning, not merely wording.

SEMANTIC_DIMENSION REQUIREMENT
When family is SEMANTIC_DIMENSION and target_path is /predicates, include at least:
- one candidate treating the parallel labels as values of one semantic dimension, and
- one candidate treating them as separate conditions with distinct semantic dimensions.
The latter typed_effect.value must be a complete predicates object containing separate atoms
with distinct dimension fields. Preserve all unrelated predicate atoms and root logic.

Return JSON only:
{"candidates":[
  {"candidate_id":"...","user_summary":"...","semantic_fragment":{},
   "typed_effect":{"operation":"replace","target_ref":"/exact/path","value":{}}}
]}
'''

IMPACT = {
    "BOOLEAN_ATTACHMENT": 100, "SET_RELATION": 100, "SEMANTIC_DIMENSION": 95,
    "ENTITY_BOUNDARY": 95, "CALCULATION_FORMULA": 90, "METRIC_DEFINITION": 90,
    "AGGREGATE_TARGET": 85, "GROUPING_GRAIN": 85, "SELECTION_SCOPE": 85,
    "COMPARISON_DIRECTION": 80, "TIME_ATTACHMENT": 75, "OUTPUT_EXPECTATION": 70,
    "TIE_POLICY": 50,
}


class CandidateBuilder:
    def __init__(self, *, model, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    def build(self, *, analysis, cqd: dict, hint, contract) -> DecisionSpec:
        payload = {
            "original_question": analysis.original_question,
            "current_cqd": cqd,
            "hint": asdict(hint),
            "contract": asdict(contract),
        }
        out = self.model.complete_json(
            agent="candidate_builder", system=SYSTEM, payload=payload, model=self.model_name
        )
        repair_index = 0
        while True:
            try:
                return self._decision_from_output(
                    out=out, hint=hint, contract=contract, cqd=cqd
                )
            except ValueError as exc:
                repair_index += 1
                out = self.model.complete_json(
                    agent="candidate_builder",
                    system=(
                        SYSTEM + "\nCorrect the previous output according to validation_error. "
                        "Copy hint.target_path verbatim into every typed_effect.target_ref."
                    ),
                    payload={
                        **payload, "previous_output": out,
                        "validation_error": str(exc), "repair_attempt": repair_index,
                    },
                    model=self.model_name,
                )
    @staticmethod
    def _decision_from_output(*, out, hint, contract, cqd: dict) -> DecisionSpec:
        candidates = [Candidate.from_dict(x) for x in out.get("candidates", [])]
        current_value = CandidateBuilder._pointer_value(cqd, hint.target_path)

        # The Source Analyzer owns the target slot. Candidate generation must not
        # redirect a clarification effect somewhere else in the CQD.
        for candidate in candidates:
            effect = candidate.typed_effect
            if effect.get("operation") != "replace":
                raise ValueError(
                    f"Candidate {candidate.candidate_id} must use replace for an unresolved CQD slot"
                )
            if effect.get("target_ref") != hint.target_path:
                raise ValueError(
                    f"Candidate {candidate.candidate_id} target_ref {effect.get('target_ref')} "
                    f"does not match hint target_path {hint.target_path}"
                )
            replacement = effect.get("value")
            if not CandidateBuilder._same_slot_shape(current_value, replacement):
                raise ValueError(
                    f"Candidate {candidate.candidate_id} changes the CQD slot shape at "
                    f"{hint.target_path}: {type(current_value).__name__} -> "
                    f"{type(replacement).__name__}"
                )

        if contract.family == "SEMANTIC_DIMENSION" and hint.target_path == "/predicates":
            has_same_dimension = False
            dimension_counts = []
            for candidate in candidates:
                value = candidate.typed_effect.get("value")
                if not isinstance(value, dict) or not isinstance(value.get("atoms"), list):
                    continue
                relevant_atoms = [
                    atom for atom in value["atoms"]
                    if isinstance(atom, dict) and atom.get("dimension")
                ]
                dimensions = {str(atom["dimension"]).casefold() for atom in relevant_atoms}
                dimension_counts.append(len(dimensions))
                if any(isinstance(atom.get("value"), list) and len(atom["value"]) > 1 for atom in relevant_atoms):
                    has_same_dimension = True
            has_dimension_split = bool(dimension_counts) and max(dimension_counts) > min(dimension_counts)
            if not has_same_dimension or not has_dimension_split:
                raise ValueError(
                    "SEMANTIC_DIMENSION candidates must include both a same-dimension list "
                    "and separate atoms with distinct dimensions"
                )
        return DecisionSpec(
            decision_id=f"decision-{hint.hint_id}",
            contract_id=contract.contract_id,
            family=contract.family,
            source_span=hint.source_span,
            description=hint.description,
            candidates=candidates,
            impact=IMPACT.get(contract.family, 50),
        )

    @staticmethod
    def _pointer_value(document, pointer: str):
        node = document
        for raw in pointer.split("/")[1:]:
            key = raw.replace("~1", "/").replace("~0", "~")
            node = node[int(key)] if isinstance(node, list) else node[key]
        return node

    @staticmethod
    def _same_slot_shape(current, replacement) -> bool:
        if current is None:
            return replacement is None or isinstance(replacement, (str, int, float, bool))
        if isinstance(current, bool):
            return isinstance(replacement, bool)
        if isinstance(current, (str, int, float)):
            return isinstance(replacement, type(current))
        return isinstance(replacement, type(current))
