from __future__ import annotations

import copy
import re
from typing import Any


_CODE_LINE = re.compile(r"^\s*[•*]?\s*(\d+)\s*[-–—]\s*(.+?)\s*$")
_GENERIC = {"a", "an", "and", "are", "of", "or", "the"}


def repair_schema_documented_grounding(
    sif: dict[str, Any], bif: dict[str, Any], schema_catalog: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply exact codebook mappings and explicit plural numbered outputs."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    tables = {str(table.get("name")): table for table in schema_catalog.get("tables", [])}
    conditions = bif.get("target_population", {}).get("inclusion_conditions", [])

    for block in repaired.get("query_blocks", []):
        aliases = {
            str(source.get("alias") or source.get("source_id")): str(source.get("name"))
            for source in block.get("sources", [])
            if source.get("kind") == "table"
        }
        condition_expectations: dict[str, list[tuple[str, str, str]]] = {}
        for index, condition in enumerate(conditions):
            raw_value = condition.get("value")
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            if not values or not all(isinstance(value, str) for value in values):
                continue
            condition_ref = str(condition.get("condition_id") or index)
            intent_path = f"/target_population/inclusion_conditions/{condition_ref}"
            expected = []
            for phrase in values:
                match = _unique_code_match(str(phrase), aliases, tables)
                if match is not None:
                    expected.append(match)
            if len(expected) != len(values) or len({item[:2] for item in expected}) != len(expected):
                continue
            condition_expectations[intent_path] = expected

        repairs.extend(
            _remove_conflicting_condition_predicates(block, condition_expectations)
        )
        existing = _predicate_mappings(block)
        for intent_path, expected in condition_expectations.items():
            repairs.extend(
                _align_existing_code_predicates(block, intent_path, expected)
            )
            existing = _predicate_mappings(block)
            for alias, column, code in expected:
                if (intent_path, alias, column, code) in existing:
                    continue
                block.setdefault("where", []).append(
                    {
                        "kind": "binary",
                        "operator": "=",
                        "left": {
                            "kind": "column",
                            "source": alias,
                            "name": column,
                            "intent_paths": [intent_path],
                        },
                        "right": {
                            "kind": "literal",
                            "value": code,
                            "data_type": "text",
                        },
                        "intent_paths": [intent_path],
                    }
                )
                repairs.append(
                    {
                        "operation": "add_schema_documented_code_mapping",
                        "intent_path": intent_path,
                        "table": aliases[alias],
                        "column": column,
                        "value": code,
                    }
                )
                existing.add((intent_path, alias, column, code))
        repairs.extend(_expand_plural_numbered_outputs(block, bif, aliases, tables))
    return repaired, repairs


def _remove_conflicting_condition_predicates(
    block: dict[str, Any],
    expectations: dict[str, list[tuple[str, str, str]]],
) -> list[dict[str, Any]]:
    """Remove predicates wholly superseded by exact documented code mappings.

    This is evaluated across all referenced conditions at once.  A predicate may
    carry more than one stable BIF condition path, so removing it one condition at
    a time can accidentally delete a mapping already repaired for a peer.
    """
    kept: list[Any] = []
    repairs: list[dict[str, Any]] = []
    for predicate in block.get("where", []) or []:
        nodes = _predicate_mappings({"where": [predicate]})
        paths = {
            path
            for path, _, _, _ in nodes
        }
        referenced = paths & set(expectations)
        mappings = {(alias, column, value) for _, alias, column, value in nodes}
        expected_keys = {
            mapping for path in referenced for mapping in expectations[path]
        }
        if referenced and mappings and mappings.isdisjoint(expected_keys):
            repairs.append(
                {
                    "operation": "remove_superseded_schema_mapping",
                    "intent_paths": sorted(referenced),
                    "removed_mappings": sorted(mappings),
                }
            )
            continue
        kept.append(predicate)
    block["where"] = kept
    return repairs


def _unique_code_match(
    phrase: str,
    aliases: dict[str, str],
    tables: dict[str, dict[str, Any]],
) -> tuple[str, str, str] | None:
    phrase_terms = _terms(phrase)
    if not phrase_terms:
        return None
    scored: list[tuple[float, str, str, str]] = []
    for alias, table_name in aliases.items():
        for column in tables.get(table_name, {}).get("columns", []):
            for line in str(column.get("value_description") or "").splitlines():
                match = _CODE_LINE.match(line)
                if not match:
                    continue
                label_terms = _terms(match.group(2))
                if not label_terms:
                    continue
                overlap = phrase_terms & label_terms
                score = len(overlap) / len(phrase_terms)
                if score >= 0.75:
                    scored.append((score, alias, str(column.get("name")), match.group(1)))
    if not scored:
        return None
    best_score = max(item[0] for item in scored)
    best = {(alias, column, code) for score, alias, column, code in scored if score == best_score}
    return next(iter(best)) if len(best) == 1 else None


def _predicate_mappings(block: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    mappings: set[tuple[str, str, str, str]] = set()
    for value in _walk_dicts(block.get("where", [])):
        operator = str(value.get("operator", "")).lower()
        if value.get("kind") != "binary" or operator not in {"=", "==", "equals", "in"}:
            continue
        left, right = value.get("left"), value.get("right")
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        if left.get("kind") == "column" and right.get("kind") == "literal":
            literal_values = right.get("value")
            if operator != "in" or not isinstance(literal_values, list):
                literal_values = [literal_values]
            for path in set(value.get("intent_paths", [])) | set(left.get("intent_paths", [])):
                for literal in literal_values:
                    mappings.add((str(path), str(left.get("source")), str(left.get("name")), str(literal)))
    return mappings


def _align_existing_code_predicates(
    block: dict[str, Any],
    intent_path: str,
    expected: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    expected_by_column: dict[tuple[str, str], list[str]] = {}
    for alias, column, code in expected:
        expected_by_column.setdefault((alias, column), []).append(code)
    repairs: list[dict[str, Any]] = []
    for predicate in _walk_dicts(block.get("where", [])):
        operator = str(predicate.get("operator", "")).lower()
        left, right = predicate.get("left"), predicate.get("right")
        if predicate.get("kind") != "binary" or operator not in {"=", "==", "equals", "in"}:
            continue
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        if left.get("kind") != "column" or right.get("kind") != "literal":
            continue
        paths = set(predicate.get("intent_paths", [])) | set(left.get("intent_paths", []))
        key = (str(left.get("source")), str(left.get("name")))
        if intent_path not in paths or key not in expected_by_column:
            continue
        expected_values = expected_by_column[key]
        current = right.get("value")
        replacement: Any = expected_values if operator == "in" else expected_values[0]
        if current == replacement:
            continue
        right["value"] = replacement
        repairs.append(
            {
                "operation": "align_schema_documented_code_mapping",
                "intent_path": intent_path,
                "column": key[1],
                "from": current,
                "to": replacement,
            }
        )
    return repairs


def _expand_plural_numbered_outputs(
    block: dict[str, Any],
    bif: dict[str, Any],
    aliases: dict[str, str],
    tables: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    fields = bif.get("output_expectation", {}).get("fields", []) or []
    expects_multiple = any(
        field.get("value_multiplicity") == "multiple"
        and field.get("coverage") in {"all_requested", "all_available"}
        for field in fields
    )
    if not expects_multiple:
        return []
    projections = block.get("projection", [])
    present = {
        (str(item.get("expression", {}).get("source")), str(item.get("expression", {}).get("name")))
        for item in projections
    }
    additions: list[dict[str, Any]] = []
    for item in list(projections):
        expression = item.get("expression", {})
        match = re.fullmatch(r"(.+?)1", str(expression.get("name") or ""))
        alias = str(expression.get("source") or "")
        if expression.get("kind") != "column" or not match or alias not in aliases:
            continue
        columns = {str(column.get("name")) for column in tables.get(aliases[alias], {}).get("columns", [])}
        siblings = sorted(
            column for column in columns
            if re.fullmatch(re.escape(match.group(1)) + r"[2-9][0-9]*", column)
        )
        for sibling in siblings:
            if (alias, sibling) in present:
                continue
            suffix = sibling[len(match.group(1)):]
            addition = copy.deepcopy(item)
            addition["output_id"] = f"{item.get('output_id') or match.group(1)}_{suffix}"
            addition["label"] = sibling
            addition["expression"]["name"] = sibling
            projections.append(addition)
            present.add((alias, sibling))
            additions.append(
                {
                    "operation": "expand_plural_numbered_output",
                    "table": aliases[alias],
                    "from_column": expression.get("name"),
                    "added_column": sibling,
                }
            )
    return additions


def _terms(value: str) -> set[str]:
    return {
        token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in _GENERIC
    }


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)
