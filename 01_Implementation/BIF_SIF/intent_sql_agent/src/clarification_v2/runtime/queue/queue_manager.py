from __future__ import annotations

class QueueManager:
    """Data structure only. Orchestrator is the only caller allowed to mutate the queue."""
    def __init__(self) -> None:
        self._items = []

    def replace(self, decisions) -> None:
        self._items = sorted(
            [d for d in decisions if d.status == "pending" and d.materiality_status == "material"],
            key=lambda d: (-d.impact, d.decision_id),
        )

    def next(self):
        return self._items[0] if self._items else None

    def snapshot(self):
        return [d.to_dict() for d in self._items]

    def __len__(self) -> int:
        return len(self._items)
