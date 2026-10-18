from __future__ import annotations

from collections.abc import Mapping

REQUIRED = {
    "cqd_id", "contract_version", "status", "objective", "populations", "predicates",
    "expressions", "aggregations", "groupings", "selections", "time_constraints",
    "comparisons", "outputs", "scopes", "deferred_grounding", "traceability", "rendered_text",
}


def _contains_unresolved(value) -> bool:
    if isinstance(value, dict):
        if "unresolved_ref" in value:
            return True
        if value.get("resolution_status") == "unresolved":
            return True
        return any(_contains_unresolved(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_unresolved(v) for v in value)
    if isinstance(value, str) and value.strip().lower() in {"unresolved", "unknown_pending_clarification"}:
        return True
    return False


class CQDValidator:
    def validate_shape(self, cqd: dict) -> None:
        missing = REQUIRED - set(cqd)
        if missing:
            raise ValueError(f"CQD missing fields: {sorted(missing)}")
        if not isinstance(cqd.get("predicates"), Mapping):
            raise ValueError("CQD predicates must be an object")
        if not isinstance(cqd["predicates"].get("atoms", []), list):
            raise ValueError("CQD predicates.atoms must be an array")
        object_arrays = (
            "populations", "expressions", "aggregations", "groupings", "selections",
            "time_constraints", "comparisons", "outputs", "scopes", "deferred_grounding",
        )
        for section in object_arrays:
            value = cqd.get(section)
            if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
                raise ValueError(f"CQD {section} must be an array of objects")
        if not all(isinstance(item, Mapping) for item in cqd["predicates"].get("atoms", [])):
            raise ValueError("CQD predicates.atoms must contain objects")

    def validate_initial_semantics(self, cqd: dict) -> None:
        """Reject the failure mode where an empty CQD is treated as a valid run."""
        self.validate_shape(cqd)
        errors = []
        if not isinstance(cqd.get("objective"), Mapping) or not cqd["objective"]:
            errors.append("objective is empty")
        if not isinstance(cqd.get("populations"), list) or not cqd["populations"]:
            errors.append("populations is empty")
        if not isinstance(cqd.get("outputs"), list) or not cqd["outputs"]:
            errors.append("outputs is empty")
        if errors:
            raise ValueError("Initial CQD is semantically empty/incomplete: " + "; ".join(errors))

    def validate_confirmable(self, cqd: dict) -> None:
        self.validate_initial_semantics(cqd)
        semantic = {
            k: v for k, v in cqd.items()
            if k not in {"traceability", "rendered_text", "status", "cqd_id", "contract_version"}
        }
        if _contains_unresolved(semantic):
            raise ValueError("CQD still contains unresolved material decision placeholders")
