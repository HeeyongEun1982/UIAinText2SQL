from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from intent_sql_agent.contracts import ComparisonRecord
from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.execution_evaluator import (
    canonicalize_execution_rows,
    compare_execution_results,
)
from intent_sql_agent.services.sql_executor import ReadOnlySQLiteExecutor

from .state_machine import PipelineState, StateMachine


@dataclass
class EvaluationRunner:
    executor: ReadOnlySQLiteExecutor

    def run(
        self,
        *,
        database: Path,
        gold_sql: str,
        generated_sql: str,
        logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"] = "multiset",
        commit: bool = True,
        embedded: bool = False,
    ) -> ComparisonRecord:
        state = StateMachine()
        logger.set_step("P08")
        logger.write_text("sql/sql_final.sql", generated_sql)
        if not embedded:
            self._transition(state, PipelineState.FINAL_SQL_FROZEN, logger)

        logger.set_step("P09")
        logger.write_text("sql/gold.sql", gold_sql)
        gold = self.executor.execute(database, gold_sql)
        logger.write_json("execution/gold_execution.json", gold.to_dict(include_rows=False))
        logger.write_json("execution/gold_result.raw.json", gold.to_dict())
        if embedded:
            logger.append_event("GOLD_SQL_EXECUTED", {"step": "P09", "status": gold.status})
        else:
            self._transition(state, PipelineState.GOLD_SQL_EXECUTED, logger)

        logger.set_step("P10")
        generated = self.executor.execute(database, generated_sql)
        logger.write_json("execution/generated_execution.json", generated.to_dict(include_rows=False))
        logger.write_json("execution/generated_result.raw.json", generated.to_dict())
        if embedded:
            logger.append_event(
                "GENERATED_SQL_EXECUTED", {"step": "P10", "status": generated.status}
            )
        else:
            self._transition(state, PipelineState.GENERATED_SQL_EXECUTED, logger)

        logger.set_step("P11")
        logger.write_json(
            "execution/gold_result.canonical.json",
            {"rows": canonicalize_execution_rows(gold)},
        )
        logger.write_json(
            "execution/generated_result.canonical.json",
            {"rows": canonicalize_execution_rows(generated)},
        )
        if embedded:
            logger.append_event("RESULTS_NORMALIZED", {"step": "P11"})
        else:
            self._transition(state, PipelineState.RESULTS_NORMALIZED, logger)

        logger.set_step("P12")
        comparison = compare_execution_results(gold, generated, policy=comparison_policy)
        logger.write_json("execution/result_comparison.json", comparison.to_dict())
        if embedded:
            logger.append_event(
                "EXECUTION_COMPARED",
                {"step": "P12", "equivalent": comparison.equivalent},
            )
        else:
            self._transition(state, PipelineState.EXECUTION_COMPARED, logger)

        if commit and not embedded:
            self.commit(logger, state, gold.status, comparison)
        return comparison

    def commit(
        self,
        logger: ArtifactLogger,
        state: StateMachine,
        gold_status: str,
        comparison: ComparisonRecord,
        extra: dict | None = None,
    ) -> None:
        logger.set_step("P14")
        self._transition(state, PipelineState.RUN_COMMITTED, logger)
        manifest = logger.build_hash_manifest()
        manifest_valid = logger.verify_hash_manifest()
        if gold_status != "success":
            run_status = "reference_failed"
        elif comparison.equivalent:
            run_status = "success"
        else:
            run_status = "failed"
        payload = {
            "status": run_status,
            "final_state": PipelineState.RUN_COMMITTED,
            "artifact_count": len(manifest),
            "manifest_valid": manifest_valid,
        }
        if extra:
            payload.update(extra)
        logger.write_json("run_complete.json", payload)

    @staticmethod
    def _transition(state: StateMachine, target: PipelineState, logger: ArtifactLogger) -> None:
        source = state.state
        state.transition(target)
        logger.append_event("STATE_TRANSITION", {"from": source, "to": target})
