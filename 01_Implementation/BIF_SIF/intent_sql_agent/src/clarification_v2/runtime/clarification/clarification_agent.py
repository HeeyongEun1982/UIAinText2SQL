from __future__ import annotations

class ClarificationAgent:
    """Sub-agent: resolves exactly one DecisionSpec. It never mutates the CQD or queue."""
    def __init__(self, *, writer, e2, interpreter) -> None:
        self.writer = writer
        self.e2 = e2
        self.interpreter = interpreter

    def resolve(self, decision):
        question_text = self.writer.write(decision)
        answer = self.e2.answer(question_text=question_text, decision=decision)
        return self.interpreter.interpret(
            decision=decision,
            question_text=question_text,
            answer_text=answer["answer_text"],
        )
