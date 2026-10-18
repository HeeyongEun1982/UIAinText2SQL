from dataclasses import dataclass, field
from enum import Enum


class PipelineState(str, Enum):
    CASE_LOADED = "CASE_LOADED"
    FINAL_SQL_FROZEN = "FINAL_SQL_FROZEN"
    GOLD_SQL_EXECUTED = "GOLD_SQL_EXECUTED"
    GENERATED_SQL_EXECUTED = "GENERATED_SQL_EXECUTED"
    RESULTS_NORMALIZED = "RESULTS_NORMALIZED"
    EXECUTION_COMPARED = "EXECUTION_COMPARED"
    RUN_COMMITTED = "RUN_COMMITTED"


_ALLOWED = {
    PipelineState.CASE_LOADED: {PipelineState.FINAL_SQL_FROZEN},
    PipelineState.FINAL_SQL_FROZEN: {PipelineState.GOLD_SQL_EXECUTED},
    PipelineState.GOLD_SQL_EXECUTED: {PipelineState.GENERATED_SQL_EXECUTED},
    PipelineState.GENERATED_SQL_EXECUTED: {PipelineState.RESULTS_NORMALIZED},
    PipelineState.RESULTS_NORMALIZED: {PipelineState.EXECUTION_COMPARED},
    PipelineState.EXECUTION_COMPARED: {PipelineState.RUN_COMMITTED},
    PipelineState.RUN_COMMITTED: set(),
}


@dataclass
class StateMachine:
    state: PipelineState = PipelineState.CASE_LOADED
    history: list[PipelineState] = field(default_factory=lambda: [PipelineState.CASE_LOADED])

    def transition(self, target: PipelineState) -> None:
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"Invalid transition: {self.state} -> {target}")
        self.state = target
        self.history.append(target)
