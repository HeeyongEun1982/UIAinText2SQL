from __future__ import annotations

import copy
import re
from typing import Any


def repair_missing_column_joins(
    sif: dict[str, Any],
    schema_catalog: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair a column sourced from the wrong table by adding a declared-FK join."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    tables = {str(table.get("name")): table for table in schema_catalog.get("tables", [])}
    for warning in warnings:
        if warning.get("issue") != "column_not_in_source":
            continue
        block = next(
            (
                item
                for item in repaired.get("query_blocks", [])
                if item.get("block_id") == warning.get("block_id")
            ),
            None,
        )
        if block is None:
            continue
        source_alias = str(warning.get("source") or "")
        source = _source_by_alias(block, source_alias)
        if source is None:
            continue
        source_table = str(source.get("name") or "")
        column_name = str(warning.get("column") or "")
        candidates = [
            table_name
            for table_name, table in tables.items()
            if table_name != source_table and _has_column(table, column_name)
        ]
        reachable = [
            candidate
            for candidate in candidates
            if _direct_fk(tables, source_table, candidate) is not None
        ]
        if len(reachable) != 1:
            continue
        target_table = reachable[0]
        fk = _direct_fk(tables, source_table, target_table)
        if fk is None:
            continue
        target_source = next(
            (item for item in block.get("sources", []) if item.get("name") == target_table),
            None,
        )
        if target_source is None:
            target_alias = _unique_alias(block, target_table)
            target_source = {
                "source_id": target_table,
                "kind": "table",
                "name": target_table,
                "block_ref": None,
                "alias": target_alias,
                "intent_paths": list(warning.get("intent_paths", [])),
            }
            block.setdefault("sources", []).append(target_source)
        target_alias = str(target_source.get("alias") or target_source.get("source_id"))
        _ensure_join(block, source_alias, target_alias, source_table, target_table, fk)
        changed = _replace_column_source(
            block,
            source_alias=source_alias,
            column_name=column_name,
            target_alias=target_alias,
        )
        if changed:
            repairs.append(
                {
                    "issue": "column_not_in_source",
                    "column": column_name,
                    "preserved_source": source_table,
                    "joined_source": target_table,
                    "join": fk,
                }
            )
    return repaired, repairs


def repair_missing_query_block_outputs(
    sif: dict[str, Any],
    schema_catalog: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Expose a requested column through a referenced query-block boundary."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    for warning in warnings:
        if (
            warning.get("issue") != "column_not_in_source"
            or warning.get("source_kind") != "query_block"
        ):
            continue
        consumer = next(
            (
                block
                for block in repaired.get("query_blocks", [])
                if block.get("block_id") == warning.get("block_id")
            ),
            None,
        )
        if consumer is None:
            continue
        source = _source_by_alias(consumer, str(warning.get("source") or ""))
        block_ref = str((source or {}).get("block_ref") or warning.get("source_ref") or "")
        column_name = str(warning.get("column") or "")
        if not block_ref or not column_name:
            continue
        trial = copy.deepcopy(repaired)
        changes: list[dict[str, Any]] = []
        if not _ensure_query_block_output(
            trial,
            schema_catalog,
            block_ref,
            column_name,
            list(warning.get("intent_paths", [])),
            changes,
            set(),
        ):
            continue
        repaired = trial
        repairs.extend(changes)
    return repaired, repairs


def repair_missing_output_traceability(
    sif: dict[str, Any],
    bif: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Attach a missing output BIF path to final projections without changing SQL semantics."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    fields = bif.get("output_expectation", {}).get("fields", []) or []
    blocks = {
        str(block.get("block_id")): block
        for block in repaired.get("query_blocks", [])
        if block.get("block_id")
    }
    main_id = str(repaired.get("main_block_id") or "")
    output_blocks = _set_output_blocks(blocks, main_id)
    for warning in warnings:
        if warning.get("issue") != "missing_bif_intent_path" or warning.get("role") != "output":
            continue
        path = str(warning.get("intent_path") or "")
        match = re.fullmatch(r"/output_expectation/fields/(\d+)", path)
        if not match or int(match.group(1)) >= len(fields):
            continue
        field = fields[int(match.group(1))]
        field_tokens = _semantic_tokens(
            f"{field.get('label') or ''} {field.get('semantic_ref') or ''}"
        )
        candidates: list[dict[str, Any]] = []
        for block in output_blocks:
            for projection in block.get("projection", []) or []:
                expression = projection.get("expression", {})
                projection_tokens = _semantic_tokens(
                    f"{projection.get('label') or ''} {expression.get('name') or ''}"
                )
                if len(fields) == 1 or field_tokens & projection_tokens:
                    candidates.append(projection)
        if not candidates:
            continue
        for projection in candidates:
            projection.setdefault("intent_paths", [])
            if path not in projection["intent_paths"]:
                projection["intent_paths"].append(path)
            expression = projection.get("expression")
            if isinstance(expression, dict):
                expression.setdefault("intent_paths", [])
                if path not in expression["intent_paths"]:
                    expression["intent_paths"].append(path)
        repairs.append(
            {
                "operation": "attach_output_intent_path",
                "intent_path": path,
                "projection_count": len(candidates),
            }
        )
    return repaired, repairs


def repair_missing_population_traceability(
    sif: dict[str, Any],
    warnings: list[dict[str, Any]],
    preferred_tables: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Attach missing population provenance to an already selected physical source."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    preferred_tables = preferred_tables or set()
    for warning in warnings:
        if (
            warning.get("issue") != "missing_bif_intent_path"
            or warning.get("role") != "population"
        ):
            continue
        path = str(warning.get("intent_path") or "")
        sources = [
            source
            for block in repaired.get("query_blocks", [])
            for source in block.get("sources", []) or []
            if source.get("kind") == "table" and source.get("name")
        ]
        preferred = [
            source
            for source in sources
            if str(source.get("name") or "") in preferred_tables
        ]
        candidates = preferred
        if not candidates:
            distinct_tables = {
                str(source.get("name") or "")
                for source in sources
            }
            if len(distinct_tables) == 1:
                candidates = sources
        if not candidates:
            continue
        for source in candidates:
            source.setdefault("intent_paths", [])
            if path not in source["intent_paths"]:
                source["intent_paths"].append(path)
        repairs.append(
            {
                "operation": "attach_population_intent_path",
                "intent_path": path,
                "tables": sorted(
                    {
                        str(source.get("name") or "")
                        for source in candidates
                    }
                ),
                "source_count": len(candidates),
            }
        )
    return repaired, repairs


def repair_unrequested_supporting_projections(
    sif: dict[str, Any],
    bif: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove final supporting identifiers when the BIF requests output fields only."""
    repaired = copy.deepcopy(sif)
    output = bif.get("output_expectation", {})
    if output.get("include_supporting_values") is not False:
        return repaired, []
    fields = output.get("fields", []) or []
    requested_tokens = [
        _semantic_tokens(
            f"{field.get('label') or ''} {field.get('semantic_ref') or ''}"
        )
        for field in fields
    ]
    if not requested_tokens:
        return repaired, []
    main_id = str(repaired.get("main_block_id") or "")
    block = next(
        (
            item
            for item in repaired.get("query_blocks", [])
            if str(item.get("block_id") or "") == main_id
        ),
        None,
    )
    if block is None or block.get("set_operations"):
        return repaired, []
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for projection in block.get("projection", []) or []:
        expression = projection.get("expression", {})
        paths = set(_collect_intent_paths(projection))
        tokens = _semantic_tokens(
            f"{projection.get('label') or ''} {expression.get('name') or ''}"
        )
        is_population_support = (
            any(
                path.startswith(prefix)
                for path in paths
                for prefix in ("/target_population", "/scope", "/granularity")
            )
            and not any(path.startswith("/metrics/") for path in paths)
        )
        matches_requested_field = any(tokens & expected for expected in requested_tokens)
        if is_population_support and not matches_requested_field:
            removed.append(
                {
                    "output_id": projection.get("output_id"),
                    "label": projection.get("label"),
                    "column": expression.get("name"),
                }
            )
        else:
            kept.append(projection)
    if not removed or not kept:
        return repaired, []
    block["projection"] = kept
    return repaired, [
        {
            "operation": "remove_unrequested_supporting_projections",
            "removed": removed,
        }
    ]


def _set_output_blocks(
    blocks: dict[str, dict[str, Any]], block_id: str, seen: set[str] | None = None
) -> list[dict[str, Any]]:
    seen = set() if seen is None else seen
    if block_id in seen or block_id not in blocks:
        return []
    seen.add(block_id)
    block = blocks[block_id]
    result = [block]
    for operation in block.get("set_operations", []) or []:
        result.extend(
            _set_output_blocks(blocks, str(operation.get("query_block_ref") or ""), seen)
        )
    return result


def _ensure_query_block_output(
    sif: dict[str, Any],
    schema_catalog: dict[str, Any],
    block_id: str,
    column_name: str,
    intent_paths: list[str],
    changes: list[dict[str, Any]],
    stack: set[str],
) -> bool:
    if block_id in stack:
        return False
    blocks = {
        str(block.get("block_id")): block
        for block in sif.get("query_blocks", [])
        if block.get("block_id")
    }
    block = blocks.get(block_id)
    if block is None:
        return False
    if _projection_exposes(block, column_name):
        return True
    # An extra ungrouped attribute could change an aggregate block's grain.
    if block.get("group_by") or block.get("having") or block.get("windows"):
        return False

    stack.add(block_id)
    try:
        tables = {
            str(table.get("name")): table
            for table in schema_catalog.get("tables", [])
        }
        candidates: list[tuple[str, str | None]] = []
        for source in block.get("sources", []) or []:
            kind = source.get("kind")
            alias = str(source.get("alias") or source.get("source_id") or "")
            if kind in {"table", "view"}:
                table_name = str(source.get("name") or "")
                if alias and _has_column(tables.get(table_name, {}), column_name):
                    candidates.append((alias, _column_type(tables[table_name], column_name)))
            elif kind == "query_block" and source.get("block_ref"):
                if _ensure_query_block_output(
                    sif,
                    schema_catalog,
                    str(source["block_ref"]),
                    column_name,
                    intent_paths,
                    changes,
                    stack,
                ):
                    candidates.append((alias, None))
        if len(candidates) != 1:
            return False

        # Compound queries use positional output contracts, so every set operand
        # must expose the same appended column in the same position.
        for operation in block.get("set_operations", []) or []:
            operand_ref = str(operation.get("query_block_ref") or "")
            if not operand_ref or not _ensure_query_block_output(
                sif,
                schema_catalog,
                operand_ref,
                column_name,
                intent_paths,
                changes,
                stack,
            ):
                return False

        alias, data_type = candidates[0]
        block.setdefault("projection", []).append(
            {
                "output_id": _unique_output_id(block, column_name),
                "label": column_name,
                "expression": {
                    "kind": "column",
                    "source": alias,
                    "name": column_name,
                    "data_type": data_type,
                    "intent_paths": list(intent_paths),
                },
                "intent_paths": list(intent_paths),
            }
        )
        changes.append(
            {
                "operation": "expose_query_block_output",
                "block_id": block_id,
                "column": column_name,
                "source": alias,
            }
        )
        return True
    finally:
        stack.remove(block_id)


def _projection_exposes(block: dict[str, Any], column_name: str) -> bool:
    expected = column_name.casefold()
    for projection in block.get("projection", []) or []:
        expression = projection.get("expression", {})
        exposed = [projection.get("output_id"), projection.get("label")]
        if expression.get("kind") in {"column", "reference"}:
            exposed.append(expression.get("name"))
        for name in exposed:
            if str(name or "").casefold() == expected:
                return True
    return False


def _column_type(table: dict[str, Any], column_name: str) -> str | None:
    for column in table.get("columns", []) or []:
        if str(column.get("name") or "").casefold() == column_name.casefold():
            return column.get("type") or column.get("data_type")
    return None


def _unique_output_id(block: dict[str, Any], column_name: str) -> str:
    used = {str(item.get("output_id") or "") for item in block.get("projection", [])}
    base = "proj_" + re.sub(r"[^A-Za-z0-9_]+", "_", column_name).strip("_")
    candidate = base or "proj_output"
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def repair_bif_logic_operators(
    sif: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Restore an explicit BIF all_of relation that became OR in the SIF."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    for warning in warnings:
        if warning.get("issue") != "bif_logic_operator_mismatch":
            continue
        block = next(
            (
                item
                for item in repaired.get("query_blocks", [])
                if item.get("block_id") == warning.get("block_id")
            ),
            None,
        )
        if block is None:
            continue
        expected_pairs = {
            frozenset((int(pair["left_index"]), int(pair["right_index"])))
            for pair in warning.get("condition_pairs", [])
        }
        changed = _replace_or_for_condition_pairs(block.get("where", []), expected_pairs)
        if changed:
            repairs.append(
                {
                    "operation": "restore_bif_all_of_operator",
                    "block_id": warning.get("block_id"),
                    "from": "or",
                    "to": "and",
                    "changed_nodes": changed,
                }
            )
    return repaired, repairs


def repair_unrequested_with_ties(
    sif: dict[str, Any],
    bif: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Disable WITH TIES when the confirmed BIF requests exactly one value."""
    if not any(
        item.get("issue") == "unsupported_sif_feature"
        and "WITH TIES" in str(item.get("error") or "")
        for item in warnings
    ):
        return copy.deepcopy(sif), []
    output = bif.get("output_expectation", {})
    if output.get("limit") != 1 and output.get("shape") != "scalar":
        return copy.deepcopy(sif), []
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    for block in repaired.get("query_blocks", []) or []:
        limit = block.get("limit")
        if isinstance(limit, dict) and limit.get("with_ties"):
            limit["with_ties"] = False
            repairs.append({
                "operation": "disable_unrequested_with_ties",
                "block_id": block.get("block_id"),
                "intent_paths": limit.get("intent_paths", []),
            })
    return repaired, repairs


def repair_malformed_boolean_expressions(
    sif: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Collapse an AND/OR wrapper that contains exactly one expression operand."""
    if not any(item.get("issue") == "malformed_boolean_expression" for item in warnings):
        return copy.deepcopy(sif), []
    repaired = copy.deepcopy(sif)
    changed = _collapse_incomplete_boolean_nodes(repaired)
    return repaired, ([{
        "operation": "collapse_incomplete_boolean_wrapper",
        "changed_nodes": changed,
    }] if changed else [])


def repair_non_exact_question_values(
    sif: dict[str, Any], warnings: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replace a less-specific literal when one maximal question value is known."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    for warning in warnings:
        expected = warning.get("expected_question_values", [])
        if warning.get("issue") != "non_exact_question_value_mapping" or len(expected) != 1:
            continue
        block = next(
            (
                item
                for item in repaired.get("query_blocks", [])
                if item.get("block_id") == warning.get("block_id")
            ),
            None,
        )
        if block is None:
            continue
        for predicate in _walk_dicts(block.get("where", [])):
            pair = _column_literal_nodes(predicate)
            if pair is None:
                continue
            column, literal = pair
            if (
                str(column.get("name") or "") == str(warning.get("column") or "")
                and literal.get("value") == warning.get("literal")
                and set(warning.get("intent_paths", []))
                <= set(_collect_intent_paths(predicate))
            ):
                previous = literal.get("value")
                literal["value"] = expected[0]
                repairs.append(
                    {
                        "operation": "restore_maximal_question_value",
                        "column": warning.get("column"),
                        "from": previous,
                        "to": expected[0],
                        "intent_paths": warning.get("intent_paths", []),
                    }
                )
                break
    return repaired, repairs


def repair_merged_independent_conditions(
    sif: dict[str, Any],
    bif: dict[str, Any],
    schema_catalog: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split a merged all-of predicate when each business dimension has one exact column."""
    repaired = copy.deepcopy(sif)
    repairs: list[dict[str, Any]] = []
    conditions = bif.get("target_population", {}).get("inclusion_conditions", [])
    tables = {str(table.get("name")): table for table in schema_catalog.get("tables", [])}
    for warning in warnings:
        if warning.get("issue") != "merged_independent_bif_conditions":
            continue
        block = next(
            (
                item
                for item in repaired.get("query_blocks", [])
                if item.get("block_id") == warning.get("block_id")
            ),
            None,
        )
        if block is None:
            continue
        aliases = {
            str(source.get("alias") or source.get("source_id")): str(source.get("name"))
            for source in block.get("sources", [])
            if source.get("kind") == "table"
        }
        replacements: list[dict[str, Any]] = []
        mappings: list[dict[str, Any]] = []
        for index in warning.get("condition_indexes", []):
            if not isinstance(index, int) or index >= len(conditions):
                replacements = []
                break
            condition = conditions[index]
            if str(condition.get("operator") or "") != "equals":
                replacements = []
                break
            candidates = _exact_semantic_columns(condition, aliases, tables)
            if len(candidates) != 1:
                replacements = []
                break
            alias, column_name = candidates[0]
            intent_path = f"/target_population/inclusion_conditions/{index}"
            value = condition.get("value")
            replacements.append(
                {
                    "kind": "binary",
                    "operator": "=",
                    "left": {
                        "kind": "column",
                        "source": alias,
                        "name": column_name,
                        "intent_paths": [intent_path],
                    },
                    "right": {
                        "kind": "literal",
                        "value": value,
                        "data_type": "number" if isinstance(value, (int, float)) else "text",
                        "intent_paths": [intent_path],
                    },
                    "intent_paths": [intent_path],
                }
            )
            mappings.append(
                {
                    "intent_path": intent_path,
                    "table": aliases[alias],
                    "column": column_name,
                    "value": value,
                }
            )
        if len(replacements) < 2 or len({(m["table"], m["column"]) for m in mappings}) != len(mappings):
            continue
        if _replace_predicate(block.get("where", []), warning.get("predicate"), replacements):
            repairs.append(
                {
                    "operation": "split_merged_independent_conditions",
                    "mappings": mappings,
                }
            )
    return repaired, repairs


def selected_population_tables(
    response: dict[str, Any], source_ambiguities: list[dict[str, Any]]
) -> set[str]:
    selected = {str(value) for value in response.get("selected_option_ids", [])}
    return {
        str(candidate["table"])
        for ambiguity in source_ambiguities
        for candidate in ambiguity.get("candidates", [])
        if str(candidate.get("candidate_id")) in selected and candidate.get("table")
    }


def canonicalize_source_population_selection(
    response: dict[str, Any],
    request: dict[str, Any],
    source_ambiguities: list[dict[str, Any]],
    *,
    ambiguity_id: str | None = None,
) -> dict[str, Any]:
    """Translate user-facing OPTION_n choices back to stable source candidate IDs."""
    canonical = copy.deepcopy(response)
    if response.get("action") != "select":
        return canonical
    selected = {str(value) for value in response.get("selected_option_ids", [])}
    selected_labels = {
        str(option.get("label") or "").strip()
        for option in request.get("options", [])
        if str(option.get("option_id")) in selected
    }
    ambiguities = [
        ambiguity
        for ambiguity in source_ambiguities
        if not ambiguity_id or str(ambiguity.get("ambiguity_id")) == ambiguity_id
    ]
    candidate_ids = {
        str(candidate.get("candidate_id"))
        for ambiguity in ambiguities
        for candidate in ambiguity.get("candidates", [])
        if str(candidate.get("candidate_id")) in selected
        or str(candidate.get("user_facing_definition") or "").strip()
        in selected_labels
    }
    if candidate_ids:
        canonical["selected_option_ids"] = sorted(candidate_ids)
    return canonical


def apply_source_population_selection(
    sif: dict[str, Any],
    response: dict[str, Any],
    source_ambiguities: list[dict[str, Any]],
    schema_catalog: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Apply a population choice while preserving sources used by other roles."""
    selected = {str(value) for value in response.get("selected_option_ids", [])}
    matches = [
        candidate
        for ambiguity in source_ambiguities
        for candidate in ambiguity.get("candidates", [])
        if str(candidate.get("candidate_id")) in selected
    ]
    if response.get("action") != "select" or len(matches) != 1:
        return None, []
    candidate = matches[0]
    target_table = str(candidate.get("table") or "")
    if not target_table:
        return None, []
    updated = copy.deepcopy(sif)
    mapping_paths = {
        str(mapping.get("intent_path")): str(mapping.get("column"))
        for mapping in candidate.get("condition_mappings", [])
        if mapping.get("intent_path") and mapping.get("column")
    }
    target_block = next(
        (
            block
            for block in updated.get("query_blocks", [])
            if any(
                _contains_intent_path(item, set(mapping_paths))
                for item in block.get("where", [])
            )
        ),
        None,
    )
    if target_block is None:
        return None, []
    mapped_columns = _mapped_population_columns(
        target_block.get("where", []), mapping_paths
    )
    aliases = {str(column.get("source")) for column in mapped_columns}
    if len(aliases) != 1:
        return None, []
    population_alias = next(iter(aliases))
    source = _source_by_alias(target_block, population_alias)
    if source is None:
        return None, []
    previous_table = str(source.get("name") or "")
    previous_alias = str(source.get("alias") or source.get("source_id") or "")
    current_alias = previous_alias
    preserved_role_source = False
    join: dict[str, str] | None = None
    if previous_table != target_table and schema_catalog is not None and _source_has_non_population_role(
        target_block, previous_alias
    ):
        tables = {
            str(table.get("name")): table
            for table in schema_catalog.get("tables", [])
        }
        join = _direct_fk(tables, previous_table, target_table)
        if join is not None:
            current_alias = _unique_alias(target_block, target_table)
            target_block.setdefault("sources", []).append(
                {
                    "source_id": target_table,
                    "kind": "table",
                    "name": target_table,
                    "block_ref": None,
                    "alias": current_alias,
                    "intent_paths": sorted(mapping_paths),
                }
            )
            _ensure_join(
                target_block,
                previous_alias,
                current_alias,
                previous_table,
                target_table,
                join,
            )
            preserved_role_source = True
    if not preserved_role_source:
        source["name"] = target_table
        source["source_id"] = target_table
        source["kind"] = "table"
        source["block_ref"] = None
        current_alias = str(source.get("alias") or source.get("source_id") or "")
        if previous_alias != current_alias:
            _replace_source_alias(target_block, previous_alias, current_alias)
            population_alias = current_alias
    changed = _replace_condition_columns(
        target_block.get("where", []), population_alias, mapping_paths, current_alias
    )
    if changed != len(mapping_paths):
        return None, []
    updated["status"] = "ready"
    updated["unresolved_grounding"] = []
    return updated, [
        {
            "operation": "apply_source_population_selection",
            "from_table": previous_table,
            "to_table": target_table,
            "updated_conditions": changed,
            "preserved_role_source": preserved_role_source,
            "join": join,
        }
    ]


def missing_required_sources(sif: dict[str, Any], required_tables: set[str]) -> set[str]:
    present = {
        str(source.get("name"))
        for block in sif.get("query_blocks", [])
        for source in block.get("sources", [])
        if source.get("kind") == "table" and source.get("name")
    }
    return required_tables - present


def enforce_locked_population_sources(
    previous: dict[str, Any],
    candidate: dict[str, Any],
    required_tables: set[str],
    schema_catalog: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Restore model-deleted population sources, predicates, and declared-FK joins."""
    restored = copy.deepcopy(candidate)
    missing = missing_required_sources(restored, required_tables)
    if not missing:
        return restored, []
    tables = {
        str(table.get("name")): table
        for table in schema_catalog.get("tables", [])
    }
    previous_blocks = {
        str(block.get("block_id") or ""): block
        for block in previous.get("query_blocks", [])
    }
    restored_blocks = {
        str(block.get("block_id") or ""): block
        for block in restored.get("query_blocks", [])
    }
    main_id = str(restored.get("main_block_id") or "")
    repairs: list[dict[str, Any]] = []
    for table_name in sorted(missing):
        occurrences = [
            (block_id, block, source)
            for block_id, block in previous_blocks.items()
            for source in block.get("sources", []) or []
            if source.get("kind") == "table"
            and str(source.get("name") or "") == table_name
        ]
        if not occurrences:
            return copy.deepcopy(previous), [{
                "operation": "reject_destructive_grounding_candidate",
                "reason": "locked_source_snapshot_missing",
                "table": table_name,
            }]
        for block_id, old_block, old_source in occurrences:
            target_block = restored_blocks.get(block_id) or restored_blocks.get(main_id)
            if target_block is None:
                return copy.deepcopy(previous), [{
                    "operation": "reject_destructive_grounding_candidate",
                    "reason": "locked_source_target_block_missing",
                    "table": table_name,
                }]
            if any(
                source.get("kind") == "table"
                and str(source.get("name") or "") == table_name
                for source in target_block.get("sources", []) or []
            ):
                continue
            old_alias = str(old_source.get("alias") or old_source.get("source_id") or table_name)
            new_alias = _unique_alias(target_block, table_name)
            source_copy = copy.deepcopy(old_source)
            source_copy["source_id"] = _unique_source_id(target_block, table_name)
            source_copy["alias"] = new_alias
            target_block.setdefault("sources", []).append(source_copy)

            physical_sources = [
                source
                for source in target_block.get("sources", []) or []
                if source is not source_copy
                and source.get("kind") == "table"
                and source.get("name")
            ]
            join_target = next(
                (
                    source
                    for source in physical_sources
                    if _direct_fk(
                        tables,
                        table_name,
                        str(source.get("name") or ""),
                    )
                    is not None
                ),
                None,
            )
            join_spec = None
            if physical_sources and join_target is None:
                return copy.deepcopy(previous), [{
                    "operation": "reject_destructive_grounding_candidate",
                    "reason": "no_declared_fk_for_locked_source",
                    "table": table_name,
                }]
            if join_target is not None:
                other_table = str(join_target.get("name") or "")
                other_alias = str(join_target.get("alias") or join_target.get("source_id") or "")
                join_spec = _direct_fk(tables, table_name, other_table)
                if join_spec is not None:
                    _ensure_join(
                        target_block,
                        new_alias,
                        other_alias,
                        table_name,
                        other_table,
                        join_spec,
                    )

            restored_predicates = 0
            for predicate in old_block.get("where", []) or []:
                if not any(
                    str(column.get("source") or "") == old_alias
                    for column in _columns(predicate)
                ):
                    continue
                predicate_copy = copy.deepcopy(predicate)
                _replace_source_alias(predicate_copy, old_alias, new_alias)
                signature = json_signature(predicate_copy)
                if any(json_signature(item) == signature for item in target_block.get("where", []) or []):
                    continue
                target_block.setdefault("where", []).append(predicate_copy)
                restored_predicates += 1
            repairs.append(
                {
                    "operation": "restore_locked_population_source",
                    "table": table_name,
                    "block_id": target_block.get("block_id"),
                    "alias": new_alias,
                    "restored_predicates": restored_predicates,
                    "join": join_spec,
                }
            )
    if missing_required_sources(restored, required_tables):
        return copy.deepcopy(previous), [{
            "operation": "reject_destructive_grounding_candidate",
            "reason": "locked_source_restore_incomplete",
            "tables": sorted(missing),
        }]
    return restored, repairs


def json_signature(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_by_alias(block: dict[str, Any], alias: str) -> dict[str, Any] | None:
    return next(
        (
            source
            for source in block.get("sources", [])
            if alias in {str(source.get("alias") or ""), str(source.get("source_id") or "")}
        ),
        None,
    )


def _has_column(table: dict[str, Any], column_name: str) -> bool:
    return any(
        str(column.get("name") or "").lower() == column_name.lower()
        for column in table.get("columns", [])
    )


def _foreign_keys(table: dict[str, Any]) -> list[dict[str, Any]]:
    value = table.get("foreign_keys", [])
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)]


def _direct_fk(
    tables: dict[str, dict[str, Any]], left: str, right: str
) -> dict[str, str] | None:
    for fk in _foreign_keys(tables.get(left, {})):
        if str(fk.get("target_table")) == right:
            return {
                "left_column": str(fk.get("from_column")),
                "right_column": str(fk.get("to_column")),
            }
    for fk in _foreign_keys(tables.get(right, {})):
        if str(fk.get("target_table")) == left:
            return {
                "left_column": str(fk.get("to_column")),
                "right_column": str(fk.get("from_column")),
            }
    return None


def _unique_alias(block: dict[str, Any], table_name: str) -> str:
    used = {
        str(source.get("alias") or source.get("source_id") or "")
        for source in block.get("sources", [])
    }
    base = table_name[:1].lower() or "t"
    if base not in used:
        return base
    index = 2
    while f"{base}{index}" in used:
        index += 1
    return f"{base}{index}"


def _unique_source_id(block: dict[str, Any], table_name: str) -> str:
    used = {
        str(source.get("source_id") or "")
        for source in block.get("sources", []) or []
    }
    candidate = table_name
    index = 2
    while candidate in used:
        candidate = f"{table_name}_{index}"
        index += 1
    return candidate


def _ensure_join(
    block: dict[str, Any],
    left_alias: str,
    right_alias: str,
    left_table: str,
    right_table: str,
    fk: dict[str, str],
) -> None:
    if any(
        {str(join.get("left_source")), str(join.get("right_source"))}
        == {left_alias, right_alias}
        for join in block.get("joins", [])
    ):
        return
    block.setdefault("joins", []).append(
        {
            "join_id": f"join_{left_table}_{right_table}",
            "type": "inner",
            "left_source": left_alias,
            "right_source": right_alias,
            "predicates": [
                {
                    "kind": "binary",
                    "operator": "=",
                    "left": {
                        "kind": "column",
                        "source": left_alias,
                        "name": fk["left_column"],
                        "intent_paths": [],
                    },
                    "right": {
                        "kind": "column",
                        "source": right_alias,
                        "name": fk["right_column"],
                        "intent_paths": [],
                    },
                }
            ],
            "intent_paths": [],
        }
    )


def _replace_column_source(
    value: Any, *, source_alias: str, column_name: str, target_alias: str
) -> int:
    changed = 0
    if isinstance(value, dict):
        if (
            value.get("kind") == "column"
            and str(value.get("source")) == source_alias
            and str(value.get("name") or "").lower() == column_name.lower()
        ):
            value["source"] = target_alias
            changed += 1
        for child in value.values():
            changed += _replace_column_source(
                child,
                source_alias=source_alias,
                column_name=column_name,
                target_alias=target_alias,
            )
    elif isinstance(value, list):
        for child in value:
            changed += _replace_column_source(
                child,
                source_alias=source_alias,
                column_name=column_name,
                target_alias=target_alias,
            )
    return changed


def _replace_source_alias(value: Any, old_alias: str, new_alias: str) -> int:
    """Keep expression and join references valid when an implicit alias changes."""
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"source", "left_source", "right_source"} and str(child) == old_alias:
                value[key] = new_alias
                changed += 1
            else:
                changed += _replace_source_alias(child, old_alias, new_alias)
    elif isinstance(value, list):
        for child in value:
            changed += _replace_source_alias(child, old_alias, new_alias)
    return changed


def _replace_or_for_condition_pairs(
    value: Any, expected_pairs: set[frozenset[int]]
) -> int:
    changed = 0
    if isinstance(value, dict):
        if value.get("kind") == "binary" and str(value.get("operator", "")).lower() == "or":
            left = _intent_condition_indexes(value.get("left"))
            right = _intent_condition_indexes(value.get("right"))
            if any(
                frozenset((left_index, right_index)) in expected_pairs
                for left_index in left
                for right_index in right
            ):
                value["operator"] = "and"
                changed += 1
        for child in value.values():
            changed += _replace_or_for_condition_pairs(child, expected_pairs)
    elif isinstance(value, list):
        for child in value:
            changed += _replace_or_for_condition_pairs(child, expected_pairs)
    return changed


def _collapse_incomplete_boolean_nodes(value: Any) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if isinstance(child, dict) and child.get("kind") == "binary" and str(child.get("operator", "")).lower() in {"and", "or"}:
                operands = [child.get(side) for side in ("left", "right") if isinstance(child.get(side), dict)]
                if len(operands) == 1:
                    value[key] = operands[0]
                    child = value[key]
                    changed += 1
            changed += _collapse_incomplete_boolean_nodes(child)
    elif isinstance(value, list):
        for index, child in enumerate(list(value)):
            if isinstance(child, dict) and child.get("kind") == "binary" and str(child.get("operator", "")).lower() in {"and", "or"}:
                operands = [child.get(side) for side in ("left", "right") if isinstance(child.get(side), dict)]
                if len(operands) == 1:
                    value[index] = operands[0]
                    child = value[index]
                    changed += 1
            changed += _collapse_incomplete_boolean_nodes(child)
    return changed


def _intent_condition_indexes(value: Any) -> set[int]:
    indexes: set[int] = set()
    if isinstance(value, dict):
        for path in value.get("intent_paths", []) or []:
            match = re.match(
                r"^/target_population/inclusion_conditions/(\d+)(?:/|$)", str(path)
            )
            if match:
                indexes.add(int(match.group(1)))
        for child in value.values():
            indexes.update(_intent_condition_indexes(child))
    elif isinstance(value, list):
        for child in value:
            indexes.update(_intent_condition_indexes(child))
    return indexes


def _contains_intent_path(value: Any, paths: set[str]) -> bool:
    if isinstance(value, dict):
        if set(value.get("intent_paths", [])) & paths:
            return True
        return any(_contains_intent_path(child, paths) for child in value.values())
    if isinstance(value, list):
        return any(_contains_intent_path(child, paths) for child in value)
    return False


def _columns(value: Any):
    if isinstance(value, dict):
        if value.get("kind") == "column":
            yield value
        for child in value.values():
            yield from _columns(child)
    elif isinstance(value, list):
        for child in value:
            yield from _columns(child)


def _replace_condition_columns(
    value: Any,
    source_alias: str,
    mapping_paths: dict[str, str],
    target_alias: str,
) -> int:
    changed_paths: set[str] = set()
    for mapping_path, column_name in mapping_paths.items():
        candidates = [
            column
            for column in _mapped_population_columns(value, {mapping_path: column_name})
            if str(column.get("source")) == source_alias
        ]
        for column in candidates:
            column["name"] = column_name
            column["source"] = target_alias
            changed_paths.add(mapping_path)
    return len(changed_paths)


def _mapped_population_columns(
    value: Any, mapping_paths: dict[str, str]
) -> list[dict[str, Any]]:
    """Prefer value-level intent references over broader condition references."""
    columns = list(_columns(value))
    selected: list[dict[str, Any]] = []
    for mapping_path in mapping_paths:
        descendants = [
            column
            for column in columns
            if any(
                str(path).startswith(mapping_path + "/")
                for path in column.get("intent_paths", []) or []
            )
        ]
        exact = [
            column
            for column in columns
            if mapping_path in {
                str(path) for path in column.get("intent_paths", []) or []
            }
        ]
        selected.extend(descendants or exact)
    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for column in selected:
        identity = id(column)
        if identity not in seen:
            seen.add(identity)
            unique.append(column)
    return unique


def _source_has_non_population_role(block: dict[str, Any], source_alias: str) -> bool:
    """Return whether an alias is needed outside population filtering."""
    role_nodes = [
        block.get("projection", []),
        block.get("derived_fields", []),
        block.get("group_by", []),
        block.get("having", []),
        block.get("windows", []),
        block.get("order_by", []),
        block.get("set_operations", []),
    ]
    return any(
        str(column.get("source") or "") == source_alias
        for node in role_nodes
        for column in _columns(node)
    )


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _collect_intent_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        paths.update(str(path) for path in value.get("intent_paths", []) or [])
        for child in value.values():
            paths.update(_collect_intent_paths(child))
    elif isinstance(value, list):
        for child in value:
            paths.update(_collect_intent_paths(child))
    return paths


def _column_literal_nodes(
    predicate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if predicate.get("kind") != "binary" or str(predicate.get("operator", "")).lower() not in {
        "=", "==", "equals"
    }:
        return None
    left, right = predicate.get("left"), predicate.get("right")
    if isinstance(left, dict) and isinstance(right, dict):
        if left.get("kind") == "column" and right.get("kind") == "literal":
            return left, right
        if right.get("kind") == "column" and left.get("kind") == "literal":
            return right, left
    return None


def _semantic_tokens(value: str) -> set[str]:
    synonyms = {"lowest": "low", "highest": "high"}
    return {
        synonyms.get(token, token)
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in {"entity", "attribute"}
    }


def _exact_semantic_columns(
    condition: dict[str, Any],
    aliases: dict[str, str],
    tables: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    target = _semantic_tokens(
        " ".join(
            [
                str(condition.get("subject") or ""),
                str(condition.get("semantic_dimension") or "").split(".")[-1],
            ]
        )
    )
    candidates: set[tuple[str, str]] = set()
    if not target:
        return []
    for alias, table_name in aliases.items():
        for column in tables.get(table_name, {}).get("columns", []):
            labels = [
                str(column.get("name") or ""),
                str(column.get("semantic_name") or ""),
            ]
            if any(_semantic_tokens(label) == target for label in labels if label):
                candidates.add((alias, str(column.get("name"))))
    return sorted(candidates)


def _replace_predicate(value: Any, target: Any, replacements: list[dict[str, Any]]) -> bool:
    if isinstance(value, list):
        for index, child in enumerate(list(value)):
            if child == target:
                value[index:index + 1] = copy.deepcopy(replacements)
                return True
            if _replace_predicate(child, target, replacements):
                return True
    elif isinstance(value, dict):
        for key, child in list(value.items()):
            if child == target:
                replacement = copy.deepcopy(replacements[0])
                for extra in replacements[1:]:
                    replacement = {
                        "kind": "binary",
                        "operator": "and",
                        "left": replacement,
                        "right": copy.deepcopy(extra),
                        "intent_paths": sorted(
                            _collect_intent_paths(replacement)
                            | _collect_intent_paths(extra)
                        ),
                    }
                value[key] = replacement
                return True
            if _replace_predicate(child, target, replacements):
                return True
    return False
