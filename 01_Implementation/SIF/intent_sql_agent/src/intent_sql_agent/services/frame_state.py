from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def create_frame_envelope(
    frame: dict[str, Any], *, layer: str, revision: int | None = None
) -> dict[str, Any]:
    """Create immutable-frame metadata; clarification decisions live in a separate ledger."""
    current = copy.deepcopy(frame)
    current_hash = frame_hash(current)
    current_revision = int(
        revision
        if revision is not None
        else (current.get("lifecycle", {}) or {}).get("revision") or 1
    )
    return {
        "layer": layer,
        "version": 1,
        "frame_id": f"{layer.upper()}-{current_hash[:16]}",
        "revision": current_revision,
        "state": _frame_state(current),
        "frame": current,
        "locked_decisions": [],
        "frame_hash": current_hash,
    }


def frame_hash(frame: dict[str, Any]) -> str:
    payload = json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _frame_state(frame: dict[str, Any]) -> str:
    return str(
        frame.get("readiness", {}).get("state")
        or frame.get("status")
        or "draft"
    )
