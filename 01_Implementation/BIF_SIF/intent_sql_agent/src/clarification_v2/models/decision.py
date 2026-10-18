from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class Candidate:
    candidate_id: str
    user_summary: str
    typed_effect: dict[str, Any]
    semantic_fragment: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Candidate":
        return cls(
            candidate_id=value["candidate_id"],
            user_summary=value["user_summary"],
            typed_effect=value["typed_effect"],
            semantic_fragment=value.get("semantic_fragment", {}),
        )

@dataclass
class DecisionSpec:
    decision_id: str
    contract_id: str
    family: str
    source_span: str
    description: str
    candidates: list[Candidate]
    materiality_status: str = "material"
    materiality_proof: str = ""
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"
    impact: int = 50

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class Resolution:
    decision_id: str
    mapping: str
    selected_candidate_id: str | None
    evidence_text: str
    confidence: float
    question_text: str
    answer_text: str

@dataclass
class DecisionEvent:
    decision_id: str
    candidate_id: str
    question_text: str
    answer_text: str
    evidence_text: str

@dataclass
class RunResult:
    question_id: str
    original_question: str
    initial_cqd: dict[str, Any]
    confirmed_cqd: dict[str, Any]
    decision_events: list[DecisionEvent]
    rounds: int
    log_id: str = ""
    run_id: str = ""
    source_analysis: dict[str, Any] = field(default_factory=dict)
    contract_coverage: list[dict[str, Any]] = field(default_factory=list)
    link_assessments: list[dict[str, Any]] = field(default_factory=list)
    grounding_events: list[dict[str, Any]] = field(default_factory=list)
    physical_plan: dict[str, Any] = field(default_factory=dict)
    technical_clarification_turns: list[dict[str, Any]] = field(default_factory=list)
    generated_sql: str = ""
    execution_evaluation: dict[str, Any] = field(default_factory=dict)
    initial_decisions: list[dict[str, Any]] = field(default_factory=list)
    clarification_turns: list[dict[str, Any]] = field(default_factory=list)
    initial_cqd_text: str = ""
    confirmed_cqd_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "original_question": self.original_question,
            "log_id": self.log_id,
            "run_id": self.run_id,
            "source_analysis": self.source_analysis,
            "contract_coverage": self.contract_coverage,
            "link_assessments": self.link_assessments,
            "grounding_events": self.grounding_events,
            "physical_plan": self.physical_plan,
            "technical_clarification_turns": self.technical_clarification_turns,
            "generated_sql": self.generated_sql,
            "execution_evaluation": self.execution_evaluation,
            "initial_cqd": self.initial_cqd,
            "initial_cqd_text": self.initial_cqd_text,
            "initial_decisions": self.initial_decisions,
            "clarification_turns": self.clarification_turns,
            "confirmed_cqd": self.confirmed_cqd,
            "confirmed_cqd_text": self.confirmed_cqd_text,
            "decision_events": [asdict(x) for x in self.decision_events],
            "rounds": self.rounds,
        }
