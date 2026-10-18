from __future__ import annotations
from typing import Protocol

class E2Interface(Protocol):
    def answer(self, *, question_text: str, decision) -> dict: ...
