from __future__ import annotations

import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from intent_sql_agent.services.artifact_logger import ArtifactLogger

from .full_case_runner import FullCaseResult


CaseRunner = Callable[[dict[str, Any], Path, ArtifactLogger], FullCaseResult]
ProgressReporter = Callable[[dict[str, Any]], None]


class BatchRunner:
    """Run independent cases to completion while checkpointing aggregate results."""

    def __init__(self, run_case: CaseRunner) -> None:
        self.run_case = run_case

    def run(
        self,
        *,
        cases: list[dict[str, Any]],
        databases_root: Path,
        runs_root: Path,
        run_id: str,
        progress: ProgressReporter | None = None,
        resume: bool = False,
        retry_failed: bool = False,
        workers: int = 1,
    ) -> dict[str, Any]:
        if not cases:
            raise ValueError("Batch dataset is empty")
        question_ids = [str(case.get("question_id")) for case in cases]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Batch dataset contains duplicate question_id values")

        batch_dir = (runs_root / run_id).resolve()
        batch_dir.mkdir(parents=True, exist_ok=True)
        existing = _read_json(batch_dir / "batch_summary.json") if resume else None
        if resume and existing is None:
            raise ValueError(f"No batch checkpoint exists for run_id={run_id}")
        outcomes: list[dict[str, Any]] = list((existing or {}).get("cases", []))
        if resume and retry_failed:
            outcomes = [
                item for item in outcomes
                if item.get("pipeline_status") == "completed"
            ]
        completed_ids = {str(item.get("question_id")) for item in outcomes}
        if not completed_ids <= set(question_ids):
            raise ValueError("Batch checkpoint does not match the supplied dataset")
        started_at = str((existing or {}).get("started_at") or datetime.now(timezone.utc).isoformat())

        pending_cases = [
            (idx, dict(c))
            for idx, c in enumerate(cases, start=1)
            if str(c.get("question_id")) not in completed_ids
        ]

        if not pending_cases:
            return _build_summary(run_id, started_at, cases, outcomes, finished=True)

        lock = threading.Lock()

        def _process_single_case(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
            index, case = item
            question_id = str(case.get("question_id"))
            db_id = str(case.get("db_id") or "")
            difficulty = case.get("difficulty") or "unknown"
            if progress:
                with lock:
                    progress(
                        {
                            "event": "case_started",
                            "index": index,
                            "total": len(cases),
                            "question_id": question_id,
                            "db_id": db_id,
                            "difficulty": difficulty,
                        }
                    )

            partial_case_dir = batch_dir / question_id
            if resume and partial_case_dir.is_dir():
                interrupted_root = batch_dir / "interrupted_attempts"
                interrupted_root.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                partial_case_dir.replace(interrupted_root / f"{question_id}_{timestamp}")
            logger = ArtifactLogger(runs_root, run_id, question_id)
            database = databases_root.resolve() / db_id / f"{db_id}.sqlite"
            try:
                result = self.run_case(case, database, logger)
                metrics = _read_json(
                    logger.case_dir / "P14" / "evaluation" / "metrics.json"
                ) or {}
                outcome = {
                    "question_id": question_id,
                    "db_id": db_id,
                    "difficulty": difficulty,
                    "pipeline_status": "completed",
                    "execution_accuracy": result.comparison.execution_accuracy,
                    "equivalent": result.comparison.equivalent,
                    "relaxed_execution_accuracy": getattr(result.comparison, "relaxed_execution_accuracy", int(result.comparison.equivalent)),
                    "relaxed_equivalent": getattr(result.comparison, "relaxed_equivalent", result.comparison.equivalent),
                    "bif_clarification_rounds": metrics.get("bif_clarification_rounds"),
                    "sif_clarification_rounds": metrics.get("sif_clarification_rounds"),
                    "total_clarification_rounds": metrics.get("total_clarification_rounds"),
                    "system_grounding_rounds": metrics.get("system_grounding_rounds"),
                    "model_call_count_total": metrics.get("model_call_count_total"),
                    "locked_bif_decision_count": metrics.get("locked_bif_decision_count"),
                    "bif_decision_ledger_count": metrics.get("bif_decision_ledger_count"),
                    "sif_decision_ledger_count": metrics.get("sif_decision_ledger_count"),
                    "sif_system_grounding_transaction_count": metrics.get(
                        "sif_system_grounding_transaction_count"
                    ),
                    "sif_candidate_rejection_count": metrics.get("sif_candidate_rejection_count"),
                    "sif_compiler_ready": metrics.get("sif_compiler_ready"),
                    "error_type": None,
                    "error": None,
                    "artifact_dir": str(logger.case_dir),
                }
            except Exception as exc:
                outcome = {
                    "question_id": question_id,
                    "db_id": db_id,
                    "difficulty": difficulty,
                    "pipeline_status": "failed",
                    "execution_accuracy": None,
                    "equivalent": False,
                    "relaxed_execution_accuracy": 0,
                    "relaxed_equivalent": False,
                    "bif_clarification_rounds": None,
                    "sif_clarification_rounds": None,
                    "total_clarification_rounds": None,
                    "system_grounding_rounds": None,
                    "model_call_count_total": None,
                    "locked_bif_decision_count": None,
                    "bif_decision_ledger_count": None,
                    "sif_decision_ledger_count": None,
                    "sif_system_grounding_transaction_count": None,
                    "sif_candidate_rejection_count": None,
                    "sif_compiler_ready": None,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "artifact_dir": str(logger.case_dir),
                }

            with lock:
                outcomes.append(outcome)
                summary = _build_summary(run_id, started_at, cases, outcomes)
                _write_summary(batch_dir, summary)
                if progress:
                    progress({"event": "case_finished", "index": index, "total": len(cases), **outcome})

            return outcome

        try:
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(_process_single_case, item) for item in pending_cases]
                    for future in as_completed(futures):
                        future.result()
            else:
                for item in pending_cases:
                    _process_single_case(item)

        except KeyboardInterrupt:
            summary = _build_summary(
                run_id,
                started_at,
                cases,
                outcomes,
                finished=True,
                status_override="aborted",
            )
            _write_summary(batch_dir, summary)
            raise

        return _build_summary(run_id, started_at, cases, outcomes, finished=True)


def _build_summary(
    run_id: str,
    started_at: str,
    cases: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    finished: bool = False,
    status_override: str | None = None,
) -> dict[str, Any]:
    completed = sum(1 for item in outcomes if item.get("pipeline_status") == "completed")
    failed = sum(1 for item in outcomes if item.get("pipeline_status") == "failed")
    correct = sum(int(item.get("execution_accuracy") or 0) for item in outcomes)
    relaxed_correct = sum(int(item.get("relaxed_execution_accuracy") or 0) for item in outcomes)
    requested = len(cases)
    by_difficulty = []
    difficulties = sorted({str(case.get("difficulty") or "unknown") for case in cases})
    for difficulty in difficulties:
        scoped = [item for item in outcomes if item.get("difficulty") == difficulty]
        scoped_completed = sum(1 for item in scoped if item.get("pipeline_status") == "completed")
        scoped_failed = sum(1 for item in scoped if item.get("pipeline_status") == "failed")
        scoped_correct = sum(int(item.get("execution_accuracy") or 0) for item in scoped)
        scoped_relaxed = sum(int(item.get("relaxed_execution_accuracy") or 0) for item in scoped)
        scoped_requested = sum(
            1 for case in cases if str(case.get("difficulty") or "unknown") == difficulty
        )
        by_difficulty.append(
            {
                "difficulty": difficulty,
                "requested": scoped_requested,
                "processed": len(scoped),
                "completed": scoped_completed,
                "pipeline_failed": scoped_failed,
                "correct": scoped_correct,
                "execution_accuracy_all_requested": (
                    scoped_correct / scoped_requested if scoped_requested else None
                ),
                "execution_accuracy_completed": (
                    scoped_correct / scoped_completed if scoped_completed else None
                ),
                "relaxed_correct": scoped_relaxed,
                "relaxed_execution_accuracy_all_requested": (
                    scoped_relaxed / scoped_requested if scoped_requested else None
                ),
                "relaxed_execution_accuracy_completed": (
                    scoped_relaxed / scoped_completed if scoped_completed else None
                ),
                "average_bif_clarification_rounds": _average(
                    scoped, "bif_clarification_rounds"
                ),
                "average_sif_clarification_rounds": _average(
                    scoped, "sif_clarification_rounds"
                ),
                "average_total_clarification_rounds": _average(
                    scoped, "total_clarification_rounds"
                ),
            }
        )

    finished_at = datetime.now(timezone.utc).isoformat() if finished else None
    status = status_override or ("complete" if len(outcomes) == requested else "in_progress")
    return {
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "requested": requested,
        "processed": len(outcomes),
        "completed": completed,
        "pipeline_failed": failed,
        "correct": correct,
        "execution_accuracy_all_requested": correct / requested if requested else 0.0,
        "execution_accuracy_completed": correct / completed if completed else None,
        "relaxed_correct": relaxed_correct,
        "relaxed_execution_accuracy_all_requested": relaxed_correct / requested if requested else 0.0,
        "relaxed_execution_accuracy_completed": relaxed_correct / completed if completed else None,
        "average_bif_clarification_rounds": _average(
            outcomes, "bif_clarification_rounds"
        ),
        "average_sif_clarification_rounds": _average(
            outcomes, "sif_clarification_rounds"
        ),
        "average_total_clarification_rounds": _average(
            outcomes, "total_clarification_rounds"
        ),
        "average_model_call_count": _average(outcomes, "model_call_count_total"),
        "sif_compiler_ready_count": sum(
            item.get("sif_compiler_ready") is True for item in outcomes
        ),
        "bif_decision_total": sum(
            int(item.get("bif_decision_ledger_count") or 0) for item in outcomes
        ),
        "sif_decision_total": sum(
            int(item.get("sif_decision_ledger_count") or 0) for item in outcomes
        ),
        "sif_candidate_rejection_total": sum(
            int(item.get("sif_candidate_rejection_count") or 0) for item in outcomes
        ),
        "by_difficulty": by_difficulty,
        "cases": outcomes,
    }


def _safe_replace(src: Path, dst: Path, retries: int = 10, delay: float = 0.05) -> None:
    """Safely replace a file on Windows handling momentary file locking/antivirus delays."""
    for i in range(retries):
        try:
            os.replace(src, dst)
            return
        except OSError:
            if i == retries - 1:
                try:
                    if src.is_file():
                        dst.write_bytes(src.read_bytes())
                        src.unlink(missing_ok=True)
                except Exception:
                    pass
                return
            time.sleep(delay)


def _write_summary(batch_dir: Path, summary: dict[str, Any]) -> None:
    try:
        _atomic_text(
            batch_dir / "batch_summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        )
        csv_path = batch_dir / "batch_summary.csv"
        temporary = csv_path.with_suffix(".csv.tmp")
        fields = [
            "question_id", "db_id", "difficulty", "pipeline_status", "execution_accuracy",
            "equivalent", "relaxed_execution_accuracy", "relaxed_equivalent", "bif_clarification_rounds", "sif_clarification_rounds",
            "total_clarification_rounds", "system_grounding_rounds", "error_type", "error",
            "model_call_count_total", "locked_bif_decision_count", "bif_decision_ledger_count",
            "sif_decision_ledger_count", "sif_system_grounding_transaction_count",
            "sif_candidate_rejection_count", "sif_compiler_ready",
            "artifact_dir",
        ]
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(summary["cases"])
        _safe_replace(temporary, csv_path)
        _atomic_text(
            batch_dir / "batch_run_complete.json",
            json.dumps(
                {
                    key: summary[key]
                    for key in (
                        "run_id", "status", "started_at", "finished_at", "requested", "processed",
                        "completed", "pipeline_failed", "correct", "execution_accuracy_all_requested",
                        "execution_accuracy_completed", "average_bif_clarification_rounds",
                        "average_sif_clarification_rounds", "average_total_clarification_rounds",
                        "average_model_call_count", "sif_compiler_ready_count",
                        "bif_decision_total", "sif_decision_total",
                        "sif_candidate_rejection_total",
                        "by_difficulty",
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    except Exception:
        pass


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    _safe_replace(temporary, path)


def _average(items: list[dict[str, Any]], key: str) -> float | None:
    values = [item[key] for item in items if item.get(key) is not None]
    return sum(values) / len(values) if values else None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
