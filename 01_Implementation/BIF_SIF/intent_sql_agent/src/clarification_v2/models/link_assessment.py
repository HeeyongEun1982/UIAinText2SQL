from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


LINK_STATUSES = frozenset({"grounded", "ambiguous", "no_match", "not_linkable"})


@dataclass(frozen=True)
class LinkCandidate:
    table: str
    column: str
    business_dimension: str
    matched_value: str
    score: float
    physical_value: Any | None = None


@dataclass(frozen=True)
class LinkAssessment:
    family: str
    source_span: str
    target_path: str
    status: str
    reason: str
    candidates: list[LinkCandidate] = field(default_factory=list)
    typed_effect: dict[str, Any] | None = None
