from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AmbiguityHint:
    hint_id: str
    family: str
    source_span: str
    description: str
    target_path: str

@dataclass
class SourceAnalysis:
    question_id: str
    original_question: str
    observations: dict[str, Any]
    ambiguity_hints: list[AmbiguityHint] = field(default_factory=list)
