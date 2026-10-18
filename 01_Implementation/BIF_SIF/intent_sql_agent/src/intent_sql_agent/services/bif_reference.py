from __future__ import annotations

import copy
import re
from typing import Any


def normalize_bif_semantic_references(
    bif: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair only unambiguous, invalid output-to-metric JSON pointers."""
    normalized = copy.deepcopy(bif)
    metrics = normalized.get("metrics", [])
    repairs: list[dict[str, Any]] = []
    if not isinstance(metrics, list) or not metrics:
        return normalized, repairs

    fields = normalized.get("output_expectation", {}).get("fields", [])
    for field_index, field in enumerate(fields if isinstance(fields, list) else []):
        reference = str(field.get("semantic_ref") or "")
        match = re.fullmatch(r"/metrics/(\d+)", reference)
        if not match or int(match.group(1)) < len(metrics):
            continue

        target_index = _unique_metric_match(field, metrics)
        if target_index is None:
            continue
        replacement = f"/metrics/{target_index}"
        field["semantic_ref"] = replacement
        repairs.append(
            {
                "operation": "normalize_bif_semantic_reference",
                "field_path": f"/output_expectation/fields/{field_index}/semantic_ref",
                "from": reference,
                "to": replacement,
                "reason": "invalid_metric_array_pointer_with_unique_target",
            }
        )
    return normalized, repairs


def _unique_metric_match(
    field: dict[str, Any], metrics: list[dict[str, Any]]
) -> int | None:
    if len(metrics) == 1:
        return 0
    field_terms = {
        _key(field.get("label")),
        _key(field.get("field_id")),
    } - {""}
    matches = [
        index
        for index, metric in enumerate(metrics)
        if field_terms
        & {
            _key(metric.get("name")),
            _key(metric.get("metric_id")),
        }
    ]
    return matches[0] if len(matches) == 1 else None


def _key(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())
