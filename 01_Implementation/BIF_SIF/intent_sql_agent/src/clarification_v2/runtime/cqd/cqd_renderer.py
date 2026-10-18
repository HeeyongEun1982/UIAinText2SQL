from __future__ import annotations

import json


def _short(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _predicate_text(atom: dict) -> str:
    if atom.get("resolution_status") == "unresolved":
        role = atom.get("semantic_role", "meaning")
        return f"UNRESOLVED[{role}]: {atom.get('source_text', '')}"
    dimension = atom.get("dimension") or atom.get("semantic_label") or atom.get("source_text") or "condition"
    operator = atom.get("operator", "")
    value = atom.get("value", "")
    if value == "" and atom.get("description"):
        return str(atom["description"])
    return " ".join(x for x in [str(dimension), str(operator), _short(value)] if x).strip()


class CQDRenderer:
    """Deterministic, human-readable rendering. CQD State remains source of truth."""

    def render(self, cqd: dict) -> str:
        lines: list[str] = []
        objective = cqd.get("objective", {})
        if objective:
            lines.append(f"Objective: {objective.get('decision_purpose') or objective.get('operation')}")

        populations = cqd.get("populations", [])
        if populations:
            lines.append(
                "Population: " + "; ".join(
                    str(x.get("description") or x.get("entity_type") or x) for x in populations
                )
            )

        atoms = cqd.get("predicates", {}).get("atoms", [])
        if atoms:
            lines.append("Conditions:")
            for atom in atoms:
                lines.append(f"  - {_predicate_text(atom)}")
        root = cqd.get("predicates", {}).get("root")
        if root:
            lines.append("Condition logic: " + _short(root))

        if cqd.get("expressions"):
            lines.append("Expressions: " + "; ".join(_short(x) for x in cqd["expressions"]))
        if cqd.get("aggregations"):
            lines.append("Aggregations: " + "; ".join(_short(x) for x in cqd["aggregations"]))
        if cqd.get("groupings"):
            lines.append("Groupings: " + "; ".join(_short(x) for x in cqd["groupings"]))
        if cqd.get("selections"):
            lines.append("Selections: " + "; ".join(_short(x) for x in cqd["selections"]))
        if cqd.get("time_constraints"):
            lines.append("Time: " + "; ".join(_short(x) for x in cqd["time_constraints"]))
        if cqd.get("comparisons"):
            lines.append("Comparisons: " + "; ".join(_short(x) for x in cqd["comparisons"]))
        if cqd.get("outputs"):
            lines.append(
                "Outputs: " + "; ".join(
                    str(x.get("meaning") or x.get("description") or x) for x in cqd["outputs"]
                )
            )
        return "\n".join(lines)
