from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    responsibility: str
    model_role: str
    evaluation_only: bool = False


AGENT_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec("A1", "Business Intent Analyzer", "Draft BIF와 최초 issue observation 생성", "pipeline"),
    AgentSpec("A2", "BIF Observation and Question Sub-functions", "BIF issue 관찰 또는 지정 decision 질문 표현", "pipeline"),
    AgentSpec("A3", "One-shot BIF Assembler", "frozen BIF decision ledger 전체로 Confirmed BIF 조립", "pipeline"),
    AgentSpec("A4", "SIF Draft Constructor", "Confirmed BIF를 schema-grounded Draft SIF로 변환", "pipeline"),
    AgentSpec("A5", "SIF Observation and Question Sub-functions", "SIF issue 관찰 또는 지정 decision 질문 표현", "pipeline"),
    AgentSpec("A6", "One-shot SIF Assembler", "frozen SIF decision ledger 전체로 Confirmed SIF 조립", "pipeline"),
    AgentSpec("A7", "SQL Generation", "Confirmed SIF에서 SQL Draft 생성", "pipeline"),
    AgentSpec(
        "A8",
        "SQL Verification, Preflight Execution and Constrained Repair",
        "SQL 정적 검증, read-only preflight 실행, 구현 오류의 제한 수리",
        "pipeline",
    ),
    AgentSpec(
        "E2",
        "Simulated User",
        "현재 canonical BIF/SIF 질문에 비개발자 사용자 언어로 응답",
        "simulated_user",
        evaluation_only=True,
    ),
)


def validate_registry() -> None:
    ids = [spec.agent_id for spec in AGENT_SPECS]
    if len(AGENT_SPECS) != 9 or len(set(ids)) != 9:
        raise RuntimeError("Agent registry must contain exactly A1-A8 and E2")
    if set(ids) != {f"A{i}" for i in range(1, 9)} | {"E2"}:
        raise RuntimeError("Unexpected Agent IDs")


validate_registry()
