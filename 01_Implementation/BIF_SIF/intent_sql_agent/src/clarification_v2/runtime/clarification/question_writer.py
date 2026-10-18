from __future__ import annotations

SYSTEM = """Write one concise, nontechnical clarification question for the user.
Prefer a contrastive choice using the provided candidate summaries.
Do not mention candidate IDs, CQD, SQL, schema, tables, columns, or database implementation.
Do not lead the user toward a preferred candidate.
Return JSON: {\"question_text\": \"...\"}.
"""

class QuestionWriter:
    def __init__(self, *, model, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    def write(self, decision) -> str:
        payload = {
            "source_span": decision.source_span,
            "description": decision.description,
            "candidates": [{"user_summary": c.user_summary} for c in decision.candidates],
        }
        attempt = 0
        while True:
            attempt += 1
            out = self.model.complete_json(
                agent="question_writer", system=SYSTEM,
                payload={**payload, "attempt": attempt}, model=self.model_name,
            )
            question_text = str(out.get("question_text", "")).strip()
            if question_text:
                return question_text
