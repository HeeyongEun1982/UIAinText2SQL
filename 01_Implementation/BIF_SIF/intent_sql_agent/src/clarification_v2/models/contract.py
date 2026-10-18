from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ClarificationContract:
    contract_id: str
    family: str
    decision_slot_type: str
    description: str
    detection_rules: tuple[str, ...]
    question_strategy: str = "contrastive_choice"
    materiality_dimensions: tuple[str, ...] = field(default_factory=tuple)

    def detect(self, *, detector, analysis, cqd: dict):
        """Run this contract's independent applicability/ambiguity check."""
        return detector.detect(contract=self, analysis=analysis, cqd=cqd)
