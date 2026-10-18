from __future__ import annotations
from clarification_v2.models.decision import Resolution

SYSTEM = """Map a user's free-text clarification answer to the registered candidates.
Do not modify the CQD and do not invent implementation details.
Return one mapping: selected | novel | uncertain | contradictory.
If selected, selected_candidate_id must be exactly one registered candidate id.
confidence must be a JSON number from 0.0 to 1.0.
Return JSON: {mapping, selected_candidate_id, evidence_text, confidence}.
"""

class AnswerInterpreter:
    def __init__(self, *, model, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    def interpret(self, *, decision, question_text: str, answer_text: str) -> Resolution:
        payload = {
            "question_text": question_text,
            "answer_text": answer_text,
            "candidates": [{"candidate_id": c.candidate_id, "user_summary": c.user_summary} for c in decision.candidates],
        }
        out = self.model.complete_json(agent="answer_interpreter", system=SYSTEM, payload=payload, model=self.model_name)
        repair_attempt = 0
        while True:
            try:
                return self._resolution(
                    out=out, decision=decision,
                    question_text=question_text, answer_text=answer_text,
                )
            except (KeyError, TypeError, ValueError) as exc:
                repair_attempt += 1
                out = self.model.complete_json(
                    agent="answer_interpreter",
                    system=SYSTEM + "\nCorrect the previous output according to validation_error.",
                    payload={
                        **payload, "previous_output": out,
                        "validation_error": str(exc), "repair_attempt": repair_attempt,
                    },
                    model=self.model_name,
                )

    @staticmethod
    def _resolution(*, out, decision, question_text: str, answer_text: str) -> Resolution:
        selected = out.get("selected_candidate_id")
        valid_ids = {c.candidate_id for c in decision.candidates}
        mapping = out.get("mapping")
        if mapping not in {"selected", "novel", "uncertain", "contradictory"}:
            raise ValueError(f"AnswerInterpreter returned invalid mapping: {mapping!r}")
        if mapping == "selected" and selected not in valid_ids:
            raise ValueError(f"AnswerInterpreter selected unknown candidate: {selected}")
        confidence = float(out.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"AnswerInterpreter confidence is outside [0, 1]: {confidence}")
        return Resolution(
            decision_id=decision.decision_id,
            mapping=mapping,
            selected_candidate_id=selected,
            evidence_text=out.get("evidence_text", answer_text),
            confidence=confidence,
            question_text=question_text,
            answer_text=answer_text,
        )
