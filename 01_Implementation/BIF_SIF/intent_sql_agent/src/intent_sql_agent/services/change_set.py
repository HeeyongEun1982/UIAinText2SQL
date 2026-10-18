from __future__ import annotations

import copy
import re
from typing import Any

from intent_sql_agent.services.frame_state import frame_hash


SYSTEM_SIF_OPERATION_TYPES = (
    "add_query_block",
    "replace_query_block",
    "remove_query_block",
    "replace_unsupported_representation",
    "replace_traceability",
    "replace_unresolved_grounding",
    "set_main_block",
)


SIF_SECTION_CHANGE_SET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["request_id", "gap_id", "base_frame_hash", "operations"],
    "properties": {
        "request_id": {"type": "string"},
        "gap_id": {"type": "string"},
        "base_frame_hash": {"type": "string"},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation_type", "path", "value"],
                "properties": {
                    "operation_type": {"enum": list(SYSTEM_SIF_OPERATION_TYPES)},
                    "path": {"type": "string", "pattern": "^/"},
                    "value": {},
                },
            },
        },
    },
}


_SIF_ADD_OPERATIONS = {"add_query_block"}
_SIF_REMOVE_OPERATIONS = {"remove_query_block"}


def apply_sif_system_change_set(
    frame: dict[str, Any],
    change_set: dict[str, Any],
    *,
    affected_paths: list[str] | None = None,
    expected_request_id: str | None = None,
    expected_gap_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply only a system-owned post-clarification SIF grounding transaction."""
    normalized = copy.deepcopy(change_set)
    for index, operation in enumerate(normalized.get("operations", [])):
        kind = operation.get("operation_type")
        path = str(operation.get("path") or "")
        if not _sif_operation_targets_valid_path(str(kind or ""), path):
            return copy.deepcopy(frame), [{
                "operation": "reject_change_set",
                "layer": "sif",
                "operation_index": index,
                "path": path,
                "reason": "operation_target_mismatch",
                "operation_type": kind,
            }]
        operation["op"] = (
            "add" if kind in _SIF_ADD_OPERATIONS
            else "remove" if kind in _SIF_REMOVE_OPERATIONS
            else "replace"
        )
    return _apply_change_set(
        frame,
        normalized,
        affected_paths=affected_paths or ["/"],
        protected_paths=(
            "/schema_version", "/sql_intent_id", "/source_frame_id", "/database",
            "/status",
        ),
        layer="sif",
        expected_request_id=expected_request_id,
        expected_gap_id=expected_gap_id,
    )


def _sif_operation_targets_valid_path(operation_type: str, path: str) -> bool:
    exact_rules = {
        "add_query_block": r"^/query_blocks(?:/-)?$",
        "replace_query_block": r"^/query_blocks/\d+$",
        "remove_query_block": r"^/query_blocks/\d+$",
        "set_main_block": r"^/main_block_id$",
        "replace_traceability": r"^/traceability$",
        "replace_unresolved_grounding": r"^/unresolved_grounding$",
        "replace_unsupported_representation": r"^/query_blocks/\d+$",
    }
    rule = exact_rules.get(operation_type)
    if rule is not None:
        return re.fullmatch(rule, path) is not None
    path_rules = {
        "source": ("/sources",),
        "join": ("/joins",),
        "predicate": ("/where", "/having"),
        "literal_mapping": ("/where", "/having"),
        "projection": ("/projection",),
        "query_block": ("/query_blocks", "/main_block_id"),
        "main_block": ("/main_block_id",),
        "aggregation": ("/projection", "/derived_fields", "/having"),
        "grouping": ("/group_by",),
        "ordering": ("/order_by",),
        "limit": ("/limit",),
        "tie_policy": ("/limit",),
        "population_source": ("/sources",),
        "query_block_output": ("/projection",),
        "expression": (
            "/where", "/having", "/projection", "/derived_fields", "/group_by",
            "/order_by", "/windows", "/joins",
        ),
        "unsupported_representation": ("/query_blocks", "/main_block_id"),
        "traceability": ("/traceability",),
        "unresolved_grounding": ("/unresolved_grounding",),
    }
    suffix = operation_type
    for prefix in ("add_", "replace_", "remove_", "set_", "select_", "expose_"):
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix):]
            break
    fragments = path_rules.get(suffix)
    if fragments is None:
        return False
    return any(fragment in path for fragment in fragments)


def _apply_change_set(
    frame: dict[str, Any],
    change_set: dict[str, Any],
    *,
    affected_paths: list[str],
    protected_paths: tuple[str, ...],
    layer: str,
    expected_request_id: str | None,
    expected_gap_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    for key, expected in (
        ("request_id", expected_request_id),
        ("gap_id", expected_gap_id),
    ):
        if expected is not None and str(change_set.get(key) or "") != expected:
            return copy.deepcopy(frame), [{
                "operation": "reject_change_set",
                "layer": layer,
                "reason": f"{key}_mismatch",
                "expected": expected,
                "actual": change_set.get(key),
            }]
    expected_hash = frame_hash(frame)
    supplied_hash = str(change_set.get("base_frame_hash") or "")
    if supplied_hash != expected_hash:
        return copy.deepcopy(frame), [{
            "operation": "reject_change_set",
            "layer": layer,
            "reason": "stale_base_frame_hash",
            "expected": expected_hash,
            "actual": supplied_hash,
        }]

    allowed = _normalize_paths(affected_paths)
    candidate = copy.deepcopy(frame)
    events: list[dict[str, Any]] = []
    for index, operation in enumerate(change_set.get("operations", [])):
        path = str(operation.get("path") or "")
        op = str(operation.get("op") or "")
        rejection = _operation_rejection(path, op, allowed, protected_paths)
        if rejection is not None:
            return copy.deepcopy(frame), [*events, {
                "operation": "reject_change_set",
                "layer": layer,
                "operation_index": index,
                "path": path,
                "reason": rejection,
            }]
        before = _get_pointer(candidate, path, missing=_MISSING)
        try:
            _apply_pointer(candidate, op, path, operation.get("value"))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return copy.deepcopy(frame), [*events, {
                "operation": "reject_change_set",
                "layer": layer,
                "operation_index": index,
                "path": path,
                "reason": "invalid_patch_target",
                "error": str(exc),
            }]
        events.append({
            "operation": "stage_patch",
            "layer": layer,
            "operation_index": index,
            "path": path,
            "op": op,
            "before": None if before is _MISSING else before,
            "after": _get_pointer(candidate, path, missing=None),
        })
    return candidate, [*events, {
        "operation": "commit_change_set",
        "layer": layer,
        "operation_count": len(change_set.get("operations", [])),
    }]


def _operation_rejection(
    path: str,
    op: str,
    allowed: list[str],
    protected: tuple[str, ...],
) -> str | None:
    if not path.startswith("/") or path == "/":
        return "root_or_invalid_path"
    if op not in {"add", "replace", "remove"}:
        return "unsupported_operation"
    if any(_overlap(path, item) for item in protected):
        return "system_owned_path"
    if not any(_within(path, parent) for parent in allowed):
        return "outside_affected_paths"
    return None


def _apply_pointer(document: dict[str, Any], op: str, path: str, value: Any) -> None:
    parts = _parts(path)
    cursor: Any = document
    for part in parts[:-1]:
        cursor = cursor[_list_index(cursor, part)] if isinstance(cursor, list) else cursor[part]
    final = parts[-1]
    if isinstance(cursor, list):
        if op == "add":
            if final == "-":
                cursor.append(copy.deepcopy(value))
            else:
                cursor.insert(int(final), copy.deepcopy(value))
        elif op == "replace":
            cursor[_list_index(cursor, final)] = copy.deepcopy(value)
        else:
            cursor.pop(_list_index(cursor, final))
        return
    if op == "remove":
        if final not in cursor:
            raise KeyError(final)
        del cursor[final]
    elif op == "replace":
        if final not in cursor:
            raise KeyError(final)
        cursor[final] = copy.deepcopy(value)
    else:
        cursor[final] = copy.deepcopy(value)


class _Missing:
    pass


_MISSING = _Missing()


def _get_pointer(document: Any, path: str, *, missing: Any) -> Any:
    cursor = document
    try:
        for part in _parts(path):
            cursor = cursor[_list_index(cursor, part)] if isinstance(cursor, list) else cursor[part]
    except (KeyError, IndexError, TypeError, ValueError):
        return missing
    return cursor


def _parts(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError(f"Invalid JSON Pointer: {path}")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _list_index(items: list[Any], token: str) -> int:
    try:
        return int(token)
    except ValueError:
        id_fields = (
            "block_id", "source_id", "join_id", "output_id", "field_id", "window_id",
        )
        matches = [
            index
            for index, item in enumerate(items)
            if isinstance(item, dict)
            and any(str(item.get(field) or "") == token for field in id_fields)
        ]
        if len(matches) != 1:
            raise ValueError(f"List selector {token!r} matched {len(matches)} nodes")
        return matches[0]


def _normalize_paths(paths: list[str]) -> list[str]:
    return sorted({str(path).rstrip("/") or "/" for path in paths if str(path).startswith("/")})


def _within(path: str, parent: str) -> bool:
    return parent == "/" or path == parent or path.startswith(parent + "/")


def _overlap(left: str, right: str) -> bool:
    return _within(left, right) or _within(right, left)
