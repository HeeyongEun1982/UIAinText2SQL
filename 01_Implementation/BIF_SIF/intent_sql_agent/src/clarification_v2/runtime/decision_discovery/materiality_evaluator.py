from __future__ import annotations
from copy import deepcopy
import json
from clarification_v2.runtime.decision.typed_effects import apply_typed_effect

SEMANTIC_KEYS = (
    "populations", "predicates", "expressions", "aggregations", "groupings", "selections",
    "time_constraints", "comparisons", "outputs", "scopes",
)

def semantic_signature(cqd: dict) -> str:
    projection = {k: cqd.get(k) for k in SEMANTIC_KEYS}
    return json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

class MaterialityEvaluator:
    def evaluate(self, *, cqd: dict, decision):
        if len(decision.candidates) < 2:
            decision.materiality_status = "candidate_incomplete"
            decision.materiality_proof = "Fewer than two plausible candidates"
            return decision
        signatures = []
        for candidate in decision.candidates:
            trial = deepcopy(cqd)
            apply_typed_effect(trial, candidate.typed_effect)
            signatures.append(semantic_signature(trial))
        unique = len(set(signatures))
        if unique < 2:
            decision.materiality_status = "non_material"
            decision.materiality_proof = "All candidate effects produce the same CQD semantic signature"
            decision.status = "invalidated"
        else:
            decision.materiality_status = "material"
            decision.materiality_proof = f"{unique} distinct CQD semantic signatures"
        return decision
