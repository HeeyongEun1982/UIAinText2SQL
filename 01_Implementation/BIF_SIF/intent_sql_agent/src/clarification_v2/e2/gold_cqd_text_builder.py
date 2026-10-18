from __future__ import annotations

from typing import Any


SYSTEM = """You are the independent Gold CQD Text Builder for a Text-to-SQL clarification experiment.

Your job is to reconstruct the intended query semantics from the ORIGINAL QUESTION and the GOLD SQL, then render that meaning as one schema-free, query-complete CQD-style natural-language description.

This component belongs to the oracle/E2 side of the experiment. It is NOT part of the runtime clarification agent.

Rules:
1. Use only original_question and gold_sql. No benchmark evidence is available or allowed.
2. Preserve every material query-semantic choice expressed by the Gold SQL when it can be mapped to the user's wording:
   - requested population/entity
   - predicates and AND/OR/NOT structure
   - metric and calculation formula
   - aggregation target
   - grouping grain
   - selection/ranking scope, ordering, limit
   - time condition and what it applies to
   - comparison operands and direction
   - set relation
   - requested outputs and result multiplicity
3. Express database implementation details in business/user language whenever the original question supports that mapping.
4. Never expose SQL text, table names, column names, aliases, join paths, database codes, or implementation-specific identifiers in gold_cqd_text.
5. Do not invent business meanings that cannot be supported by the original question plus Gold SQL structure. If a physical predicate cannot be safely translated to business language, describe only the user-level distinction supported by the question.
6. The text must describe the intended query, not explain how SQL works and not mention that it came from Gold SQL.
7. Be explicit enough that a simulated user can answer later clarification questions consistently.
8. If parallel phrases in the original question are implemented by separate Gold SQL predicates,
   preserve that distinction explicitly as separate user-level semantic conditions. Do not flatten
   them into one broad category merely because their physical column meanings are unavailable.

Return JSON only:
{
  "gold_cqd_text": "..."
}
"""


class GoldCQDTextBuilder:
    """Builds the hidden E2 intent text from Gold SQL without BIRD evidence."""

    def __init__(self, *, model, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    def build(self, *, original_question: str, gold_sql: str) -> str:
        payload: dict[str, Any] = {
            "original_question": original_question,
            "gold_sql": gold_sql,
        }
        attempt = 0
        while True:
            attempt += 1
            out = self.model.complete_json(
                agent="gold_cqd_text_builder",
                system=SYSTEM,
                payload={**payload, "attempt": attempt},
                model=self.model_name,
            )
            text = str(out.get("gold_cqd_text", "")).strip()
            if text:
                return text
