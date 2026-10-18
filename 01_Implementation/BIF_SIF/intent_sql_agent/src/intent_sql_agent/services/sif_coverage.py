from __future__ import annotations

import re
from typing import Any, Iterator


def validate_sif_coverage(
    bif: dict[str, Any],
    sif: dict[str, Any],
    schema_catalog: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    warnings.extend(_output_presentation_warnings(bif, sif))
    present_paths = _all_intent_paths(sif)

    required: list[tuple[str, str]] = []
    if bif.get("target_population", {}).get("primary_entity"):
        required.append(("/target_population/primary_entity", "population"))
    required_aliases: list[tuple[list[str], str]] = [([path], role) for path, role in required]
    required_aliases.extend(
        ([
            f"/target_population/inclusion_conditions/{index}",
            f"/target_population/inclusion_conditions/{condition.get('condition_id')}",
        ], "population_condition")
        for index, condition in enumerate(
            bif.get("target_population", {}).get("inclusion_conditions", [])
        )
    )
    required_aliases.extend(
        ([f"/metrics/{index}", f"/metrics/{metric.get('metric_id')}"], "metric")
        for index, metric in enumerate(bif.get("metrics", []))
    )
    for aliases, role in required_aliases:
        aliases = [path for path in aliases if not path.endswith("/None")]
        if not any(_path_covered(path, present_paths) for path in aliases):
            warnings.append(
                {
                    "issue": "missing_bif_intent_path",
                    "intent_path": aliases[0],
                    "role": role,
                }
            )

    for index, field in enumerate(
        bif.get("output_expectation", {}).get("fields", [])
    ):
        output_path = f"/output_expectation/fields/{index}"
        semantic_ref = str(field.get("semantic_ref") or "")
        if semantic_ref and not semantic_ref.startswith("/"):
            semantic_ref = next(
                (
                    f"/metrics/{metric_index}"
                    for metric_index, metric in enumerate(bif.get("metrics", []))
                    if semantic_ref
                    in {
                        str(metric.get("metric_id") or ""),
                        str(metric.get("name") or ""),
                    }
                ),
                semantic_ref,
            )
        if not _path_covered(output_path, present_paths) and not (
            semantic_ref and _path_covered(semantic_ref, present_paths)
        ):
            warnings.append(
                {
                    "issue": "missing_bif_intent_path",
                    "intent_path": output_path,
                    "semantic_ref": semantic_ref,
                    "role": "output",
                }
            )

    for block in sif.get("query_blocks", []):
        warnings.extend(_malformed_boolean_warnings(block))
        warnings.extend(_bif_logic_warnings(bif, block))
        warnings.extend(_merged_condition_warnings(bif, block))
        for predicate in _atomic_predicates(block.get("where", [])):
            if not _all_intent_paths(predicate):
                warnings.append(
                    {
                        "issue": "untraced_predicate",
                        "block_id": block.get("block_id"),
                        "predicate": predicate,
                        "intent_paths": [],
                    }
                )
        if schema_catalog is not None:
            warnings.extend(_question_value_warnings(bif, block, schema_catalog))
    return warnings


def _output_presentation_warnings(
    bif: dict[str, Any], sif: dict[str, Any]
) -> list[dict[str, Any]]:
    fields = bif.get("output_expectation", {}).get("fields", []) or []
    if not any(field.get("presentation") == "separate_columns" for field in fields):
        return []
    blocks = {
        str(block.get("block_id")): block
        for block in sif.get("query_blocks", [])
        if block.get("block_id")
    }
    main = blocks.get(str(sif.get("main_block_id") or ""))
    if not main:
        return []
    warnings: list[dict[str, Any]] = []
    if main.get("set_operations"):
        warnings.append({
            "issue": "output_presentation_mismatch",
            "block_id": main.get("block_id"),
            "expected_presentation": "separate_columns",
            "actual_structure": "set_operation_rows",
            "intent_paths": [
                f"/output_expectation/fields/{index}"
                for index, field in enumerate(fields)
                if field.get("presentation") == "separate_columns"
            ],
        })
    return warnings


def _merged_condition_warnings(
    bif: dict[str, Any], block: dict[str, Any]
) -> list[dict[str, Any]]:
    """Reject one physical predicate standing in for independent all-of conditions."""
    conditions = bif.get("target_population", {}).get("inclusion_conditions", [])
    logic = bif.get("target_population", {}).get("inclusion_logic")
    if not isinstance(logic, dict):
        return []
    warnings: list[dict[str, Any]] = []
    for predicate in _atomic_predicates(block.get("where", [])):
        indexes = sorted(_condition_indexes(predicate))
        if len(indexes) < 2:
            continue
        independent: list[int] = []
        for index in indexes:
            if index >= len(conditions):
                continue
            condition_id = str(conditions[index].get("condition_id") or "")
            peers = [
                peer
                for peer in indexes
                if peer != index
                and peer < len(conditions)
                and _logic_relation(
                    logic,
                    condition_id,
                    str(conditions[peer].get("condition_id") or ""),
                )
                == "all_of"
                and str(conditions[index].get("semantic_dimension") or "")
                != str(conditions[peer].get("semantic_dimension") or "")
            ]
            if peers:
                independent.append(index)
        independent = sorted(set(independent))
        if len(independent) >= 2:
            warnings.append(
                {
                    "issue": "merged_independent_bif_conditions",
                    "block_id": block.get("block_id"),
                    "condition_indexes": independent,
                    "intent_paths": [
                        f"/target_population/inclusion_conditions/{index}"
                        for index in independent
                    ],
                    "predicate": predicate,
                }
            )
    return warnings


def _malformed_boolean_warnings(block: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for expression in _walk_dicts(block.get("where", [])):
        operator = str(expression.get("operator", "")).lower()
        if expression.get("kind") == "binary" and operator in {"and", "or"}:
            present = [side for side in ("left", "right") if isinstance(expression.get(side), dict)]
            if len(present) != 2:
                warnings.append(
                    {
                        "issue": "malformed_boolean_expression",
                        "block_id": block.get("block_id"),
                        "operator": operator,
                        "present_operands": present,
                    }
                )
    return warnings


def _bif_logic_warnings(
    bif: dict[str, Any], block: dict[str, Any]
) -> list[dict[str, Any]]:
    conditions = bif.get("target_population", {}).get("inclusion_conditions", [])
    logic = bif.get("target_population", {}).get("inclusion_logic")
    if not isinstance(logic, dict):
        return []
    warnings: list[dict[str, Any]] = []
    for expression in _walk_dicts(block.get("where", [])):
        if expression.get("kind") != "binary" or str(expression.get("operator", "")).lower() != "or":
            continue
        mismatches = []
        for left_index in _condition_indexes(expression.get("left")):
            for right_index in _condition_indexes(expression.get("right")):
                if left_index == right_index or left_index >= len(conditions) or right_index >= len(conditions):
                    continue
                left_id = str(conditions[left_index].get("condition_id"))
                right_id = str(conditions[right_index].get("condition_id"))
                if _logic_relation(logic, left_id, right_id) == "all_of":
                    mismatches.append((left_index, right_index, left_id, right_id))
        if mismatches:
            warnings.append(
                {
                    "issue": "bif_logic_operator_mismatch",
                    "block_id": block.get("block_id"),
                    "sif_operator": "or",
                    "bif_operator": "all_of",
                    "condition_pairs": [
                        {
                            "left_index": left_index,
                            "right_index": right_index,
                            "left_condition_id": left_id,
                            "right_condition_id": right_id,
                        }
                        for left_index, right_index, left_id, right_id in mismatches
                    ],
                }
            )
    return warnings


def _logic_relation(logic: dict[str, Any], left_id: str, right_id: str) -> str | None:
    for group in logic.get("groups", []):
        if isinstance(group, dict) and {left_id, right_id} <= _logic_refs(group):
            return _logic_relation(group, left_id, right_id)
    if {left_id, right_id} <= _logic_refs(logic):
        return str(logic.get("operator") or "")
    return None


def _logic_refs(logic: dict[str, Any]) -> set[str]:
    refs = {str(value) for value in logic.get("condition_refs", [])}
    for group in logic.get("groups", []):
        if isinstance(group, dict):
            refs.update(_logic_refs(group))
    return refs


def _condition_indexes(value: Any) -> set[int]:
    indexes: set[int] = set()
    for path in _all_intent_paths(value):
        match = re.match(r"^/target_population/inclusion_conditions/(\d+)(?:/|$)", path)
        if match:
            indexes.add(int(match.group(1)))
    return indexes


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _all_intent_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for path in value.get("intent_paths", []) or []:
            if isinstance(path, str):
                paths.add(path)
        for child in value.values():
            paths.update(_all_intent_paths(child))
    elif isinstance(value, list):
        for child in value:
            paths.update(_all_intent_paths(child))
    return paths


def _path_covered(required: str, present: set[str]) -> bool:
    return any(
        path == required
        or path.startswith(required.rstrip("/") + "/")
        or required.startswith(path.rstrip("/") + "/")
        for path in present
    )


def _atomic_predicates(values: Any) -> Iterator[dict[str, Any]]:
    if isinstance(values, list):
        for value in values:
            yield from _atomic_predicates(value)
        return
    if not isinstance(values, dict):
        return
    if values.get("kind") == "binary" and str(values.get("operator", "")).lower() in {
        "and", "or"
    }:
        yield from _atomic_predicates(values.get("left"))
        yield from _atomic_predicates(values.get("right"))
        return
    yield values


def _question_value_warnings(
    bif: dict[str, Any], block: dict[str, Any], schema_catalog: dict[str, Any]
) -> list[dict[str, Any]]:
    aliases = {
        str(source.get("alias") or source.get("source_id")): str(source.get("name"))
        for source in block.get("sources", [])
        if source.get("kind") == "table"
    }
    tables = {str(table.get("name")): table for table in schema_catalog.get("tables", [])}
    conditions = bif.get("target_population", {}).get("inclusion_conditions", [])
    warnings: list[dict[str, Any]] = []
    for predicate in _atomic_predicates(block.get("where", [])):
        pair = _column_literal_pair(predicate)
        if pair is None:
            continue
        column, literal = pair
        table = tables.get(aliases.get(str(column.get("source")), ""), {})
        column_name = str(column.get("name") or "")
        paths = _all_intent_paths(predicate)
        condition_text = " ".join(
            str(condition.get(key) or "")
            for index, condition in enumerate(conditions)
            if f"/target_population/inclusion_conditions/{index}" in paths
            for key in ("value", "description")
        ).lower()
        expected = sorted(
            {
                str(match.get("value"))
                for match in table.get("question_value_matches", [])
                if str(match.get("column")) == column_name
                and str(match.get("value") or "").lower() in condition_text
            }
        )
        if expected and str(literal) not in expected:
            warnings.append(
                {
                    "issue": "non_exact_question_value_mapping",
                    "block_id": block.get("block_id"),
                    "table": table.get("name"),
                    "column": column_name,
                    "literal": literal,
                    "expected_question_values": expected,
                    "intent_paths": sorted(paths),
                }
            )
    return warnings


def _column_literal_pair(
    predicate: dict[str, Any],
) -> tuple[dict[str, Any], Any] | None:
    if predicate.get("kind") != "binary" or str(predicate.get("operator", "")).lower() not in {
        "=", "==", "equals"
    }:
        return None
    left, right = predicate.get("left"), predicate.get("right")
    if isinstance(left, dict) and isinstance(right, dict):
        if left.get("kind") == "column" and right.get("kind") == "literal":
            return left, right.get("value")
        if right.get("kind") == "column" and left.get("kind") == "literal":
            return right, left.get("value")
    return None
