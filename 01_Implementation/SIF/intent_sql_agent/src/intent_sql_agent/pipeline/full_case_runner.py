from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from intent_sql_agent.agents import AgentSuite
from intent_sql_agent.contracts import ComparisonRecord, ExecutionRecord
from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.schema_catalog import load_sqlite_schema
from intent_sql_agent.services.sql_executor import ReadOnlySQLiteExecutor
from intent_sql_agent.services.unified_sql_synthesizer import UnifiedSQLSynthesizer
from intent_sql_agent.services.sql_self_correction import SQLSelfCorrection
from .evaluation_runner import EvaluationRunner


class PipelineRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class FullCaseResult:
    comparison: ComparisonRecord
    final_sql: str
    preflight: ExecutionRecord
    case_dir: Path


class FullCaseRunner:
    """Direct SIF Full Case Runner: BIF is completely bypassed; Question + Schema -> SIF -> SQL."""

    def __init__(
        self,
        agents: AgentSuite,
        executor: ReadOnlySQLiteExecutor,
        *,
        max_bif_rounds: int | None = None,
        max_sif_rounds: int | None = None,
        sif_layout: Literal["v1", "v2", "v3"] = "v3",
    ) -> None:
        self.agents = agents
        self.executor = executor
        self.max_sif_rounds = 2 if max_sif_rounds is None else max_sif_rounds

    def run(
        self,
        *,
        case: dict[str, Any],
        database: Path,
        logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"] = "multiset",
    ) -> FullCaseResult:
        logger.set_step("P00")
        self._step(logger, "P00", "CASE_LOADED")
        public_input = {
            "question_id": case["question_id"],
            "db_id": case["db_id"],
            "question": case["question"],
            "difficulty": case.get("difficulty"),
        }
        logger.write_json("inputs/input_without_evidence.json", public_input)
        schema_catalog = load_sqlite_schema(
            database, question=case["question"], evidence=case.get("evidence")
        )
        logger.write_json("inputs/schema_snapshot.json", schema_catalog)

        try:
            logger.set_step("P01")
            sif_result = self.agents.run_direct_sif(
                case=case, schema_catalog=schema_catalog
            )
            return self._run_direct_sif_pipeline(
                case=case,
                database=database,
                logger=logger,
                comparison_policy=comparison_policy,
                schema_catalog=schema_catalog,
                sif_result=sif_result,
            )
        except Exception as exc:
            logger.write_json("execution/error.json", {"error": str(exc)})
            raise

    def _run_direct_sif_pipeline(
        self,
        *,
        case: dict[str, Any],
        database: Path,
        logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"],
        schema_catalog: dict[str, Any],
        sif_result: dict[str, Any],
    ) -> FullCaseResult:
        """Execute Direct SIF pipeline directly from original question and schema without BIF."""
        confirmed_sif = sif_result["confirmed_sif"]
        logger.write_json("frames/sif_result.json", sif_result)
        self._step(logger, "P01", "DIRECT_SIF_GROUNDED")

        logger.set_step("P02")
        # BIF stage is completely bypassed in SIF-Only mode
        logger.write_json("frames/confirmed_bif.json", {"status": "bypassed_sif_only_mode"})
        self._step(logger, "P02", "BIF_BYPASSED_SIF_ONLY_MODE")

        logger.set_step("P03")
        self._step(logger, "P03", "SIF_CONFIRMED")

        logger.set_step("P04")
        synthesizer = UnifiedSQLSynthesizer(self.agents.client)
        synthesized_sql = synthesizer.synthesize(
            question=str(case.get("question", "")),
            evidence=case.get("evidence"),
            confirmed_cqd=None,
            confirmed_sif=confirmed_sif,
            schema_catalog=schema_catalog,
            db_id=str(case.get("db_id", "")),
        )
        self._assert_read_only(synthesized_sql)
        preflight = self.executor.execute(database, synthesized_sql)
        final_sql = synthesized_sql

        if preflight.status != "success":
            corrector = SQLSelfCorrection(self.agents.client)
            corrected_sql = corrector.correct(
                question=str(case.get("question", "")),
                evidence=case.get("evidence"),
                failed_sql=synthesized_sql,
                error_message=str(preflight.error_message or preflight.error_code or ""),
                schema_catalog=schema_catalog,
                db_id=str(case.get("db_id", "")),
            )
            self._assert_read_only(corrected_sql)
            corrected_preflight = self.executor.execute(database, corrected_sql)
            if corrected_preflight.status == "success":
                final_sql = corrected_sql
                preflight = corrected_preflight

        logger.write_json("frames/sif_sql_final_linking.json", {
            "synthesized_sql": synthesized_sql,
            "final_sql": final_sql,
            "preflight_status": preflight.status,
        })
        self._step(logger, "P04", "SIF_LINKED_DIRECTLY_TO_SQL", {
            "technical_clarification_rounds": sif_result.get("rounds", 0),
            "continued_with_warnings": False,
        })
        logger.set_step("P05")
        logger.write_text("sql/sql_draft.sql", final_sql)
        self._step(logger, "P05", "SQL_COMPILED_DIRECTLY_FROM_SIF")
        logger.set_step("P07")
        logger.write_json("sql/sql_preflight_execution.json", [preflight.to_dict(include_rows=False)])
        logger.write_text("sql/sql_final.sql", final_sql)
        self._step(logger, "P07", "DIRECT_SIF_SQL_PREFLIGHT_EXECUTED")
        comparison = EvaluationRunner(self.executor).run(
            database=database, gold_sql=case["SQL"], generated_sql=final_sql,
            logger=logger, comparison_policy=comparison_policy,
            commit=False, embedded=True,
        )
        logger.set_step("P13")
        logger.write_json("evaluation/stage_attribution.json", self._attribute(comparison))
        metrics = {
            "execution_accuracy": comparison.execution_accuracy,
            "equivalent": comparison.equivalent,
            "business_clarification_rounds": 0,
            "technical_clarification_rounds": sif_result.get("rounds", 0),
            "sql_generation_strategy": "sif_only_direct_sql",
            "intermediate_layouts_created": 0,
            "continued_with_warnings": False,
            "model_call_count_total": self._model_call_count_total(),
            "model_calls_by_agent": self._model_calls_by_agent(),
        }
        logger.set_step("P14")
        logger.write_json("evaluation/metrics.json", metrics)
        self._step(logger, "P14", "METRICS_WRITTEN")
        logger.append_event("RUN_COMMITTED", {
            "status": "success" if comparison.equivalent else "failed",
        })
        manifest = logger.build_hash_manifest()
        logger.write_json("run_complete.json", {
            "status": "success" if comparison.equivalent else "failed",
            "final_state": "RUN_COMMITTED", "artifact_count": len(manifest),
            "manifest_valid": logger.verify_hash_manifest(),
            "model_call_count_total": metrics["model_call_count_total"],
            "sql_generation_strategy": "sif_only_direct_sql",
        })
        return FullCaseResult(comparison, final_sql, preflight, logger.case_dir)

    def _step(self, logger: ArtifactLogger, step_id: str, event_name: str, payload: dict[str, Any] | None = None) -> None:
        logger.append_event(event_name, payload or {})

    def _assert_read_only(self, sql: str) -> None:
        forbidden = [r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b", r"\bALTER\b", r"\bCREATE\b"]
        for pattern in forbidden:
            if re.search(pattern, sql, flags=re.IGNORECASE):
                raise PipelineRunError(f"Non read-only SQL detected: {pattern}")

    @staticmethod
    def _attribute(comparison: ComparisonRecord) -> dict[str, Any]:
        if comparison.equivalent:
            return {"primary_stage": "SUCCESS", "reason": "Execution results are equivalent"}
        if comparison.reason == "REFERENCE_EXECUTION_FAILED":
            return {"primary_stage": "REFERENCE", "reason": comparison.reason}
        if comparison.reason == "GENERATED_EXECUTION_FAILED":
            return {"primary_stage": "SQL", "reason": comparison.reason}
        return {
            "primary_stage": "UNRESOLVED",
            "reason": "Semantic result mismatch requires SIF/SQL trace review",
        }

    def _model_calls_by_agent(self) -> dict[str, int]:
        client = getattr(self.agents, "client", None)
        counts = getattr(client, "call_counts", {})
        return dict(counts) if isinstance(counts, dict) else {}

    def _model_call_count_total(self) -> int:
        return sum(self._model_calls_by_agent().values())
