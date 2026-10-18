from __future__ import annotations

from typing import Any

from intent_sql_agent.services.e2_oracle import build_e2_oracle_context


class BenchmarkOracleSemanticE2:
    """Benchmark-only E2 guard; runtime grounding never receives Gold SQL."""

    def __init__(self, *, delegate, gold_sql: str, original_question: str) -> None:
        self.delegate = delegate
        self.gold_sql = gold_sql
        self._original_question = original_question

    def answer(self, *, question_text: str, decision) -> dict[str, Any]:
        options = [
            {"option_id": item.candidate_id, "label": item.user_summary}
            for item in decision.candidates
        ]
        context = build_e2_oracle_context(
            self.gold_sql,
            {
                "decision": {"decision_id": decision.decision_id},
                "clarification_request": {
                    "question": question_text,
                    "options": options,
                },
            },
            original_question=self._original_question,
        )
        targets = list(context.get("oracle_target_option_ids") or [])
        if len(targets) == 1:
            selected = next(
                (item for item in decision.candidates if item.candidate_id == targets[0]),
                None,
            )
            if selected is not None:
                return {"answer_text": selected.user_summary, "confidence": "high"}
        return self.delegate.answer(question_text=question_text, decision=decision)
