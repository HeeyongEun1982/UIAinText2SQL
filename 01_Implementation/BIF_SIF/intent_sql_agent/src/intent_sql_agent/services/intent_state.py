from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CanonicalEffect:
    operation: str
    semantic_slot: str
    value: Any
    source_decision_id: str

    def validate(self) -> None:
        if self.operation != "set":
            raise ValueError(f"Unsupported canonical effect operation: {self.operation}")
        if not self.semantic_slot.strip():
            raise ValueError("Canonical effect requires a semantic slot")
        if not self.source_decision_id.strip():
            raise ValueError("Canonical effect requires a source decision")


class CanonicalIntentState:
    """System-owned, atomic semantic effects accumulated from E2 decisions."""

    def __init__(self, *, stage: str) -> None:
        self.stage = stage
        self._slots: dict[str, dict[str, Any]] = {}
        self.revision = 0

    def apply_effect(
        self, effect: CanonicalEffect, *, round_number: int
    ) -> dict[str, Any]:
        effect.validate()
        previous = copy.deepcopy(self._slots.get(effect.semantic_slot))
        self.revision += 1
        current = {
            "semantic_slot": effect.semantic_slot,
            "value": copy.deepcopy(effect.value),
            "decision_id": effect.source_decision_id,
            "effect": asdict(effect),
            "round": round_number,
            "revision": self.revision,
        }
        self._slots[effect.semantic_slot] = current
        return {"previous": previous, "current": copy.deepcopy(current)}

    def restore(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("stage") != self.stage:
            raise ValueError("Cannot restore intent state from another stage")
        self.revision = int(snapshot.get("revision", 0))
        self._slots = copy.deepcopy(snapshot.get("slots", {}))

    def get(self, semantic_slot: str) -> dict[str, Any] | None:
        value = self._slots.get(semantic_slot)
        return copy.deepcopy(value) if value is not None else None

    def snapshot(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "revision": self.revision,
            "slots": copy.deepcopy(self._slots),
        }
