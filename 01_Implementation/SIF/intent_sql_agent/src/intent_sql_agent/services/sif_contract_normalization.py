from __future__ import annotations

import copy
from typing import Any


def normalize_sif_contract_shape(
    sif: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize representation-only model variations before schema validation."""
    normalized = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    for block_index, block in enumerate(normalized.get("query_blocks", [])):
        for order_index, item in enumerate(block.get("order_by", []) or []):
            path = f"/query_blocks/{block_index}/order_by/{order_index}"
            if "kind" in item:
                removed = item.pop("kind")
                repairs.append({
                    "operation": "remove_order_by_kind",
                    "path": f"{path}/kind",
                    "removed": removed,
                })
            if "nulls" not in item:
                item["nulls"] = "dialect_default"
                repairs.append({
                    "operation": "add_order_by_nulls_default",
                    "path": f"{path}/nulls",
                    "value": "dialect_default",
                })
    return normalized, repairs
