from __future__ import annotations
from typing import Any, Protocol

class JsonModel(Protocol):
    def complete_json(self, *, agent: str, system: str, payload: dict[str, Any], model: str) -> dict[str, Any]: ...
