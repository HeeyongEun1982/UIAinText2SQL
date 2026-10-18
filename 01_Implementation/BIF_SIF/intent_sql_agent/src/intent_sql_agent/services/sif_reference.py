from __future__ import annotations

import copy
import re
from typing import Any


def normalize_sif_intent_references(
    sif: dict[str, Any], bif: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair invalid one-based SIF pointers when the zero-based target is unique."""
    normalized = copy.deepcopy(sif)
    sizes = {
        "/metrics": len(bif.get("metrics", [])),
        "/target_population/inclusion_conditions": len(
            bif.get("target_population", {}).get("inclusion_conditions", [])
        ),
        "/target_population/exclusion_conditions": len(
            bif.get("target_population", {}).get("exclusion_conditions", [])
        ),
        "/comparisons": len(bif.get("comparisons", [])),
        "/output_expectation/fields": len(
            bif.get("output_expectation", {}).get("fields", [])
        ),
    }
    repairs: list[dict[str, Any]] = []

    def visit(value: Any, location: str) -> None:
        if isinstance(value, dict):
            paths = value.get("intent_paths")
            if isinstance(paths, list):
                value["intent_paths"] = [
                    repair_path(path, f"{location}/intent_paths/{index}")
                    for index, path in enumerate(paths)
                ]
            if isinstance(value.get("intent_path"), str):
                value["intent_path"] = repair_path(
                    value["intent_path"], f"{location}/intent_path"
                )
            for key, child in value.items():
                if key not in {"intent_paths", "intent_path"}:
                    visit(child, f"{location}/{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{location}/{index}")

    def repair_path(path: Any, location: str) -> Any:
        if not isinstance(path, str):
            return path
        for prefix, size in sizes.items():
            match = re.match(rf"^{re.escape(prefix)}/(\d+)(?P<suffix>/.*)?$", path)
            if match and size > 0 and int(match.group(1)) == size:
                replacement = f"{prefix}/{size - 1}{match.group('suffix') or ''}"
                repairs.append(
                    {
                        "operation": "normalize_sif_intent_reference",
                        "location": location,
                        "from": path,
                        "to": replacement,
                    }
                )
                return replacement
        return path

    visit(normalized, "")
    return normalized, repairs
