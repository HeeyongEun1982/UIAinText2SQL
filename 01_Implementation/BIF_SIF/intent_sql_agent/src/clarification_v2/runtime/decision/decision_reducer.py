from __future__ import annotations
from clarification_v2.models.decision import DecisionEvent
from .typed_effects import apply_typed_effect

class DecisionReducer:
    def reduce(self, *, cqd: dict, decision, resolution):
        if resolution.mapping != "selected":
            raise RuntimeError(f"Decision {decision.decision_id} is not resolved: {resolution.mapping}")
        candidate = next(c for c in decision.candidates if c.candidate_id == resolution.selected_candidate_id)
        apply_typed_effect(cqd, candidate.typed_effect)
        decision.status = "resolved"
        cqd["status"] = "clarifying"
        cqd.setdefault("traceability", {}).setdefault("active_decision_ids", []).append(decision.decision_id)
        return DecisionEvent(
            decision_id=decision.decision_id,
            candidate_id=candidate.candidate_id,
            question_text=resolution.question_text,
            answer_text=resolution.answer_text,
            evidence_text=resolution.evidence_text,
        )
