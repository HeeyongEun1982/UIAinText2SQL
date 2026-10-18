from __future__ import annotations


SYSTEM = """You are a nontechnical simulated user in a clarification experiment.

You are given:
- the user's original question,
- a hidden Gold CQD Text describing what the user actually intended,
- one clarification question from the system.

Answer the clarification question naturally and consistently using ONLY the meaning in gold_cqd_text.

Rules:
1. Behave like the user who had that intended meaning in mind.
2. Do not reveal or mention Gold CQD, Gold SQL, SQL, database schema, tables, columns, aliases, physical codes, candidate IDs, or internal system structures.
3. Do not copy internal terminology unless it is natural user-facing language.
4. Do not invent information that is absent from gold_cqd_text.
5. If gold_cqd_text does not determine the answer, say that the intended meaning does not specify it.
6. Return only a natural-language answer to the current clarification question.

Return JSON only:
{"answer_text": "...", "confidence": "high|medium|low"}
"""


class SimulatedE2:
    """Oracle-side simulated user grounded only in Gold CQD Text.

    The runtime clarification agent never receives Gold SQL or Gold CQD Text.
    """

    def __init__(
        self,
        *,
        model,
        original_question: str,
        gold_cqd_text: str,
        model_name: str,
    ) -> None:
        self.model = model
        self._original_question = original_question
        self._gold_cqd_text = gold_cqd_text
        self.model_name = model_name

    def answer(self, *, question_text: str, decision) -> dict:
        # `decision` is required by the shared E2 interface but intentionally
        # not exposed to the simulated user. A real user only sees the question.
        payload = {
            "original_question": self._original_question,
            "gold_cqd_text": self._gold_cqd_text,
            "clarification_question": question_text,
        }
        attempt = 0
        while True:
            attempt += 1
            out = self.model.complete_json(
                agent="simulated_e2",
                system=SYSTEM,
                payload={**payload, "attempt": attempt},
                model=self.model_name,
            )
            answer_text = str(out.get("answer_text", "")).strip()
            if answer_text:
                return {
                    "answer_text": answer_text,
                    "confidence": out.get("confidence", "medium"),
                }
