from __future__ import annotations

from dataclasses import dataclass, field


ASSESSMENT_STATUSES = frozenset({"detected", "safe", "not_applicable"})


@dataclass(frozen=True)
class ContractFinding:
    source_span: str
    reason: str
    target_path: str


@dataclass(frozen=True)
class ContractAssessment:
    family: str
    status: str
    source_spans: list[str]
    reason: str
    findings: list[ContractFinding] = field(default_factory=list)
