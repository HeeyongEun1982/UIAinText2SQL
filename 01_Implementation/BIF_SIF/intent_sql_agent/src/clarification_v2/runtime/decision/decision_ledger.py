from __future__ import annotations

class DecisionLedger:
    """Append-only ledger. Events are never edited or deleted."""
    def __init__(self) -> None:
        self._events = []

    def append(self, event) -> None:
        self._events.append(event)

    def events(self):
        return list(self._events)

    def resolved_ids(self) -> set[str]:
        return {e.decision_id for e in self._events}
