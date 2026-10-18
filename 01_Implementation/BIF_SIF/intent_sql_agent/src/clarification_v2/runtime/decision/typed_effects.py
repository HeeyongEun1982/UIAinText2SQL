from __future__ import annotations
from copy import deepcopy
from typing import Any

ALLOWED_ROOTS = {
    "objective", "populations", "predicates", "expressions", "aggregations", "groupings",
    "selections", "time_constraints", "comparisons", "outputs", "scopes", "deferred_grounding",
}

def _decode(x: str) -> str:
    return x.replace("~1", "/").replace("~0", "~")

def _parent(document: Any, pointer: str):
    if not pointer.startswith("/"):
        raise ValueError(f"target_ref must be a JSON pointer: {pointer}")
    parts = [_decode(x) for x in pointer.split("/")[1:]]
    if not parts or parts[0] not in ALLOWED_ROOTS:
        raise ValueError(f"typed effect outside CQD semantics: {pointer}")
    node = document
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    return node, parts[-1]

def apply_typed_effect(cqd: dict, effect: dict) -> None:
    parent, key = _parent(cqd, effect["target_ref"])
    op = effect["operation"]
    if op == "replace":
        if isinstance(parent, list):
            parent[int(key)] = deepcopy(effect["value"])
        else:
            parent[key] = deepcopy(effect["value"])
    elif op == "add":
        if isinstance(parent, list):
            if key == "-":
                parent.append(deepcopy(effect["value"]))
            else:
                parent.insert(int(key), deepcopy(effect["value"]))
        else:
            parent[key] = deepcopy(effect["value"])
    elif op == "remove":
        if isinstance(parent, list):
            parent.pop(int(key))
        else:
            parent.pop(key, None)
    else:
        raise ValueError(f"unsupported effect operation: {op}")
