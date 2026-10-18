from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ContractError(ValueError):
    pass


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.resolve().read_text(encoding="utf-8"))


def validate_contract(value: Any, schema: dict[str, Any], name: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if not errors:
        return
    messages = []
    for error in errors[:20]:
        location = "/" + "/".join(str(part) for part in error.absolute_path)
        messages.append(f"{location}: {error.message}")
    raise ContractError(f"{name} contract validation failed: " + " | ".join(messages))
