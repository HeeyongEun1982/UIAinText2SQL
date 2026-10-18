from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from intent_sql_agent.agents import AgentSuite
from intent_sql_agent.model_client import ModelProfile, OpenAIChatModelClient
from intent_sql_agent.pipeline import (
    BatchRunner,
    EvaluationRunner,
    FullCaseRunner,
    PipelineRunError,
)
from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.contract_validator import load_schema
from intent_sql_agent.services.sql_executor import ReadOnlySQLiteExecutor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVISED_ROOT = PROJECT_ROOT.parent

DEFAULT_DATASET = REVISED_ROOT / "data" / "bird_dev.json"
DEFAULT_DATABASES = REVISED_ROOT / "data" / "bird_dev" / "dev_databases"
DEFAULT_RUNS = REVISED_ROOT / "runs"
DEFAULT_SPEC = REVISED_ROOT / "spec"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intent-sql-agent",
        description="Unified 8-agent text-to-SQL pipeline with BIF / SIF contracts and grounded execution.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    evaluate = commands.add_parser(
        "evaluate-case",
        help="Evaluate a pre-generated SQL file against gold SQL.",
    )
    _add_case_arguments(evaluate)
    evaluate.add_argument(
        "--generated-sql-file",
        type=Path,
        required=True,
        help="Path to SQL file to evaluate.",
    )

    self_check = commands.add_parser(
        "self-check",
        help="Run local self-check verification.",
    )
    _add_case_arguments(self_check)

    run_case = commands.add_parser(
        "run-case",
        help="Run full agent pipeline for one case (A1->A8 + E2).",
    )
    _add_case_arguments(run_case)
    _add_agent_arguments(run_case)

    run_batch = commands.add_parser(
        "run-batch",
        help="Run batch evaluation over a dataset and save checkpoint summaries.",
    )
    _add_batch_arguments(run_batch)
    _add_agent_arguments(run_batch)

    return parser


def _add_agent_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--spec-root", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--pipeline-model", default="gpt-5-mini")
    parser.add_argument("--simulated-user-model", default="gpt-5-mini")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument(
        "--sif-layout",
        choices=("v3",),
        default="v3",
        help=(
            "SIF execution layout. v3 preserves the existing business clarification, "
            "then decomposes the Confirmed CQD directly into the provided query-"
            "understanding, grounding, table-linking and SQL-section SIF formats."
        ),
    )
    parser.add_argument(
        "--model-timeout-seconds",
        type=float,
        default=120.0,
        help="개별 모델 API 요청 timeout(초). 기본값 120.",
    )
    parser.add_argument(
        "--model-max-retries",
        type=int,
        default=2,
        help="모델 SDK의 일시적 오류 재시도 횟수. 기본값 2.",
    )
    parser.add_argument(
        "--max-bif-rounds",
        type=int,
        default=7,
        help="BIF clarification 안전 상한. 기본값 7.",
    )
    parser.add_argument(
        "--max-sif-rounds",
        type=int,
        default=0,
        help="SIF clarification 안전 상한. 0은 상한 없음(기본값).",
    )


def _add_case_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--question-id", required=True, help="BIRD question_id")
    _add_execution_arguments(parser)


def _add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    _add_execution_arguments(parser)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="처리할 최대 케이스 수 제한.",
    )
    parser.add_argument(
        "--question-ids",
        nargs="+",
        help="Run only these question_id values from the supplied dataset, preserving dataset order.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="기존 batch checkpoint의 처리 완료 case를 건너뛰고 미처리 case부터 재개합니다.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --resume, preserve completed cases and rerun failed cases.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="동시 병렬 처리할 워커(스레드) 수. 기본값 1.",
    )


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--databases-root", type=Path, default=DEFAULT_DATABASES)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--run-id", default="manual_evaluation")
    parser.add_argument("--comparison-policy", choices=("ordered", "multiset"), default="multiset")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-rows", type=int, default=100_000)


def _load_case(dataset: Path, question_id: str) -> dict[str, Any]:
    cases = json.loads(dataset.resolve().read_text(encoding="utf-8"))
    for case in cases:
        if str(case.get("question_id")) == str(question_id):
            return dict(case)
    raise ValueError(f"question_id={question_id} not found in {dataset}")


def _load_cases(dataset: Path) -> list[dict[str, Any]]:
    cases = json.loads(dataset.resolve().read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError(f"Dataset must contain a JSON array: {dataset}")
    return cases


def _gold_sql(case: dict[str, Any]) -> str:
    sql = case.get("SQL") or case.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Selected case does not contain Gold SQL")
    return sql


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run-batch":
            return _run_batch(args)
        case = _load_case(args.dataset, args.question_id)
        gold_sql = _gold_sql(case)
        if args.command == "self-check":
            generated_sql = gold_sql
        elif args.command == "evaluate-case":
            generated_sql = args.generated_sql_file.resolve().read_text(encoding="utf-8").strip()
            if not generated_sql:
                raise ValueError("Generated SQL file is empty")
        else:
            generated_sql = None

        db_id = case.get("db_id")
        if not isinstance(db_id, str) or not db_id:
            raise ValueError("Selected case does not contain db_id")
        database = args.databases_root.resolve() / db_id / f"{db_id}.sqlite"
        logger = ArtifactLogger(args.runs_root, args.run_id, str(args.question_id))
        if args.command == "run-case":
            result = _build_full_case_runner(args, logger).run(
                case=case,
                database=database,
                logger=logger,
                comparison_policy=args.comparison_policy,
            )
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "question_id": str(args.question_id),
                        "db_id": db_id,
                        "mode": "run-case",
                        "agents_executed": [f"A{i}" for i in range(1, 9)] + ["E2"],
                        "equivalent": result.comparison.equivalent,
                        "execution_accuracy": result.comparison.execution_accuracy,
                        "relaxed_equivalent": getattr(result.comparison, "relaxed_equivalent", result.comparison.equivalent),
                        "relaxed_execution_accuracy": getattr(result.comparison, "relaxed_execution_accuracy", int(result.comparison.equivalent)),
                        "artifacts": str(result.case_dir),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        comparison = EvaluationRunner(
            ReadOnlySQLiteExecutor(
                timeout_seconds=args.timeout_seconds,
                max_rows=args.max_rows,
            )
        ).run(
            database=database,
            gold_sql=gold_sql,
            generated_sql=generated_sql or "",
            logger=logger,
            comparison_policy=args.comparison_policy,
        )
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "question_id": str(args.question_id),
                    "db_id": db_id,
                    "mode": args.command,
                    "equivalent": comparison.equivalent,
                    "execution_accuracy": comparison.execution_accuracy,
                    "relaxed_equivalent": getattr(comparison, "relaxed_equivalent", comparison.equivalent),
                    "relaxed_execution_accuracy": getattr(comparison, "relaxed_execution_accuracy", int(comparison.equivalent)),
                    "reason": comparison.reason,
                    "artifacts": str(logger.case_dir),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if comparison.equivalent else 1
    except (OSError, ValueError, json.JSONDecodeError, PipelineRunError) as exc:
        print(json.dumps({"status": "configuration_error", "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"status": "pipeline_error", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 3


def _build_full_case_runner(args: argparse.Namespace, logger: ArtifactLogger) -> FullCaseRunner:
    profile = ModelProfile(
        pipeline_model=args.pipeline_model,
        simulated_user_model=args.simulated_user_model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        request_timeout_seconds=args.model_timeout_seconds,
        max_retries=args.model_max_retries,
    )
    client = OpenAIChatModelClient(profile, logger)
    suite = AgentSuite(
        client,
        bif_schema=load_schema(args.spec_root / "business_intent_frame.schema.json"),
        sif_schema=load_schema(args.spec_root / "sql_intent_frame.schema.json"),
        sif_v2_schema=load_schema(args.spec_root / "sql_intent_frame_v2.schema.json"),
        query_layout_schema=load_schema(
            args.spec_root / "query_intent_layout.schema.json"
        ),
    )
    return FullCaseRunner(
        suite,
        ReadOnlySQLiteExecutor(
            timeout_seconds=args.timeout_seconds,
            max_rows=args.max_rows,
        ),
        max_bif_rounds=args.max_bif_rounds,
        max_sif_rounds=args.max_sif_rounds,
        sif_layout=args.sif_layout,
    )


def _run_batch(args: argparse.Namespace) -> int:
    cases = _load_cases(args.dataset)
    if args.retry_failed and not args.resume:
        raise ValueError("--retry-failed requires --resume")
    if args.question_ids:
        requested = {str(value) for value in args.question_ids}
        available = {str(case.get("question_id")) for case in cases}
        missing = sorted(requested - available)
        if missing:
            raise ValueError(
                f"question_id values not found in {args.dataset}: {missing}"
            )
        cases = [
            case for case in cases
            if str(case.get("question_id")) in requested
        ]
    if args.limit is not None and args.limit > 0:
        cases = cases[:args.limit]

    def run_case(
        case: dict[str, Any], database: Path, logger: ArtifactLogger
    ):
        return _build_full_case_runner(args, logger).run(
            case=case,
            database=database,
            logger=logger,
            comparison_policy=args.comparison_policy,
        )

    def report(event: dict[str, Any]) -> None:
        if event["event"] == "case_started":
            print(
                f"[{event['index']}/{event['total']}] START "
                f"question_id={event['question_id']} difficulty={event['difficulty']} "
                f"db_id={event['db_id']}",
                flush=True,
            )
        else:
            print(
                f"[{event['index']}/{event['total']}] END "
                f"question_id={event['question_id']} status={event['pipeline_status']} "
                f"execution_accuracy={event['execution_accuracy']}",
                flush=True,
            )

    summary = BatchRunner(run_case).run(
        cases=cases,
        databases_root=args.databases_root,
        runs_root=args.runs_root,
        run_id=args.run_id,
        progress=report,
        resume=args.resume,
        retry_failed=args.retry_failed,
        workers=args.workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pipeline_failed"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
