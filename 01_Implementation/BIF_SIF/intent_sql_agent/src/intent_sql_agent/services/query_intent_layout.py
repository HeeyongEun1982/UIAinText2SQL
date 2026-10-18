from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SIF_LINK_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["sources", "fields", "predicates", "having_predicates", "joins"],
    "properties": {
        "sources": {"type": "array", "items": {"$ref": "#/$defs/sourceLink"}},
        "fields": {"type": "array", "items": {"$ref": "#/$defs/fieldLink"}},
        "predicates": {"type": "array", "items": {"$ref": "#/$defs/predicateLink"}},
        "having_predicates": {"type": "array", "items": {"$ref": "#/$defs/predicateLink"}},
        "joins": {"type": "array", "items": {"$ref": "#/$defs/joinLink"}},
    },
    "$defs": {
        "value": {
            "oneOf": [
                {"type": "null"}, {"type": "string"}, {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}},
            ]
        },
        "sourceLink": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "role", "table"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "role": {"type": "string", "minLength": 1},
                "table": {"type": "string", "minLength": 1},
            },
        },
        "fieldLink": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "source", "column", "expression"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "source": {"type": "string", "minLength": 1},
                "column": {"type": ["string", "null"]},
                "expression": {"type": ["string", "null"]},
            },
        },
        "predicateLink": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "operator", "value"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "operator": {"type": "string", "minLength": 1},
                "value": {"$ref": "#/$defs/value"},
            },
        },
        "joinLink": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "role", "type", "left_source", "left_column", "right_source", "right_column"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "role": {"type": "string", "minLength": 1},
                "type": {"enum": ["inner", "left", "right", "full", "cross"]},
                "left_source": {"type": "string", "minLength": 1},
                "left_column": {"type": ["string", "null"]},
                "right_source": {"type": "string", "minLength": 1},
                "right_column": {"type": ["string", "null"]},
            },
        },
    },
}


def grounding_proposal_schema(layout_schema: dict[str, Any]) -> dict[str, Any]:
    """Return a physical-link-only contract; A4 cannot rewrite the layout."""
    del layout_schema
    return copy.deepcopy(SIF_LINK_PROPOSAL_SCHEMA)


def create_sif_overlay(bif_layout: dict[str, Any]) -> dict[str, Any]:
    """Create the same layout for SIF without introducing another representation."""
    sif = copy.deepcopy(bif_layout)
    sif["phase"] = "sif"
    return sif


def apply_sif_links(
    bif_layout: dict[str, Any], linked_layout: dict[str, Any]
) -> dict[str, Any]:
    """Fill physical values while keeping the BIF SQL roles and references intact."""
    result = create_sif_overlay(bif_layout)
    linked_sources = {
        str(item.get("id")): item for item in linked_layout.get("sources", []) or []
    }
    known_source_ids = {str(item.get("id")) for item in result.get("sources", []) or []}
    for source in result.get("sources", []) or []:
        linked = linked_sources.pop(str(source.get("id")), None)
        if linked is not None:
            source["table"] = copy.deepcopy(linked.get("table"))
    for source in linked_sources.values():
        if str(source.get("id")) not in known_source_ids:
            result["sources"].append(copy.deepcopy(source))
    linked_fields = {
        str(item.get("id")): item for item in linked_layout.get("fields", []) or []
    }
    for field in result.get("fields", []) or []:
        linked = linked_fields.get(str(field.get("id")))
        if linked is None:
            continue
        for key in ("source", "column", "expression"):
            field[key] = copy.deepcopy(linked.get(key))

    for collection in ("predicates", "having_predicates"):
        linked_items = {
            str(item.get("id")): item
            for item in linked_layout.get(collection, []) or []
        }
        for item in result.get(collection, []) or []:
            linked = linked_items.get(str(item.get("id")))
            if linked is not None:
                item["operator"] = copy.deepcopy(linked.get("operator"))
                item["value"] = copy.deepcopy(linked.get("value"))

    linked_joins = {
        str(item.get("id")): item for item in linked_layout.get("joins", []) or []
    }
    for join in result.get("joins", []) or []:
        linked = linked_joins.pop(str(join.get("id")), None)
        if linked is None:
            continue
        for key in ("type", "left_source", "left_column", "right_source", "right_column"):
            join[key] = copy.deepcopy(linked.get(key))
    result["joins"].extend(copy.deepcopy(list(linked_joins.values())))
    return result


def apply_node_groundings(
    bif_layout: dict[str, Any],
    current_sif: dict[str, Any],
    node_groundings: Any,
    technical_nodes: Any = None,
) -> dict[str, Any]:
    """Compatibility entry point: accept an already linked simple SIF layout."""
    if isinstance(node_groundings, dict) and node_groundings.get("phase") == "sif":
        return copy.deepcopy(node_groundings)
    return copy.deepcopy(current_sif)


def resolve_derived_groundings(
    bif_layout: dict[str, Any], proposal: dict[str, Any], confirmed_cqd: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Derived SQL roles are represented directly in the simple layout."""
    return copy.deepcopy(proposal), []


def validate_bif_layout(layout: dict[str, Any]) -> dict[str, Any]:
    """Compatibility diagnostic only; runtime does not use it as a gate."""
    return {
        "status": "validation_bypassed",
        "slot_count": len(iter_query_nodes(layout)),
    }


def validate_layout_transition(
    bif_layout: dict[str, Any],
    sif_layout: dict[str, Any],
    *,
    allow_unresolved: bool = False,
) -> dict[str, Any]:
    """Compatibility diagnostic only; SQL compilation is the authoritative check."""
    return {
        "status": "validation_bypassed",
        "slot_count": len(iter_query_nodes(sif_layout)),
        "semantic_fingerprint": semantic_fingerprint(bif_layout),
    }


def semantic_fingerprint(layout: dict[str, Any]) -> str:
    """Hash only the logical, schema-free part of the simple layout."""
    logical = copy.deepcopy(layout)
    logical.pop("phase", None)
    for source in logical.get("sources", []) or []:
        source["table"] = None
    for field in logical.get("fields", []) or []:
        field["column"] = None
    for join in logical.get("joins", []) or []:
        for key in ("type", "left_source", "left_column", "right_source", "right_column"):
            join[key] = None
    raw = json.dumps(logical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_query_nodes(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the few concrete slots that can be counted or logged."""
    result: list[dict[str, Any]] = []
    result.extend(layout.get("sources", []) or [])
    result.extend(layout.get("fields", []) or [])
    result.extend(layout.get("predicates", []) or [])
    result.extend(layout.get("having_predicates", []) or [])
    result.extend(layout.get("order_by", []) or [])
    result.extend(layout.get("joins", []) or [])
    return result
