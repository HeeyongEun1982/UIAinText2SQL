from __future__ import annotations

class DependencyReconciler:
    """First implementation: deterministic dependency hook owned by Orchestrator.
    Contract-specific invalidation/supersession rules can be added here without changing the agents.
    """
    def reconcile(self, *, decisions, resolved_ids: set[str]):
        for d in decisions:
            if d.status == "pending" and d.dependencies and not all(x in resolved_ids for x in d.dependencies):
                d.status = "blocked"
            elif d.status == "blocked" and all(x in resolved_ids for x in d.dependencies):
                d.status = "pending"
        return decisions
