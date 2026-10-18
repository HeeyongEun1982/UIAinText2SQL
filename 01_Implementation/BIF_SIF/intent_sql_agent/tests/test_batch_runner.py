import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.cli import build_parser
from intent_sql_agent.pipeline import BatchRunner


class BatchRunnerTest(unittest.TestCase):
    def test_cli_exposes_one_command_batch(self) -> None:
        args = build_parser().parse_args(["run-batch", "--run-id", "smoke"])
        self.assertEqual("run-batch", args.command)
        self.assertEqual("smoke", args.run_id)
        self.assertEqual("BIRD_DEV_STRATIFIED_SMOKE_30.json", args.dataset.name)
        self.assertFalse(args.resume)
        self.assertFalse(args.retry_failed)
        self.assertIsNone(args.question_ids)

    def test_cli_accepts_batch_question_id_filter(self) -> None:
        args = build_parser().parse_args(
            ["run-batch", "--question-ids", "2", "113", "433"]
        )
        self.assertEqual(["2", "113", "433"], args.question_ids)

    def test_cli_has_no_bif_ablation_policy(self) -> None:
        args = build_parser().parse_args(["run-case", "--question-id", "2"])
        self.assertFalse(hasattr(args, "bif_policy"))

    def test_cli_uses_cost_efficient_models_by_default_and_allows_override(self) -> None:
        defaults = build_parser().parse_args(["run-case", "--question-id", "2"])
        self.assertEqual("gpt-5-mini", defaults.pipeline_model)
        self.assertEqual("gpt-5-mini", defaults.simulated_user_model)

        overridden = build_parser().parse_args(
            [
                "run-case",
                "--question-id",
                "2",
                "--pipeline-model",
                "gpt-4-turbo",
                "--simulated-user-model",
                "gpt-4o",
            ]
        )
        self.assertEqual("gpt-4-turbo", overridden.pipeline_model)
        self.assertEqual("gpt-4o", overridden.simulated_user_model)

    def test_batch_continues_after_failure_and_checkpoints_summary(self) -> None:
        received_cases = []

        def run_case(case, database, logger):
            received_cases.append(case)
            if case["question_id"] == 2:
                raise RuntimeError("synthetic failure")
            logger.set_step("P14")
            logger.write_json(
                "evaluation/metrics.json",
                {
                    "bif_clarification_rounds": 1,
                    "sif_clarification_rounds": 2,
                    "total_clarification_rounds": 3,
                    "system_grounding_rounds": 0,
                },
            )
            comparison = SimpleNamespace(execution_accuracy=1, equivalent=True)
            return SimpleNamespace(comparison=comparison)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                {
                    "question_id": 1,
                    "db_id": "db1",
                    "difficulty": "simple",
                    "question": "q1",
                    "SQL": "SELECT 1",
                    "evidence": "must not reach agents",
                },
                {
                    "question_id": 2,
                    "db_id": "db2",
                    "difficulty": "challenging",
                    "question": "q2",
                    "SQL": "SELECT 2",
                },
            ]
            summary = BatchRunner(run_case).run(
                cases=cases,
                databases_root=root / "databases",
                runs_root=root / "runs",
                run_id="smoke",
            )

            self.assertEqual("complete_with_failures", summary["status"])
            self.assertEqual(2, summary["processed"])
            self.assertEqual(1, summary["completed"])
            self.assertEqual(1, summary["pipeline_failed"])
            self.assertEqual(1, summary["correct"])
            self.assertEqual(0.5, summary["execution_accuracy_all_requested"])
            self.assertIsNone(summary["cases"][1]["execution_accuracy"])
            self.assertEqual("must not reach agents", received_cases[0]["evidence"])

            batch_dir = root / "runs" / "smoke"
            saved = json.loads((batch_dir / "batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(saved["cases"]))
            with (batch_dir / "batch_summary.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(["1", "2"], [row["question_id"] for row in rows])
            self.assertTrue((batch_dir / "batch_run_complete.json").is_file())

    def test_keyboard_interrupt_marks_batch_aborted(self) -> None:
        calls = 0

        def run_case(case, database, logger):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            logger.set_step("P14")
            logger.write_json("evaluation/metrics.json", {})
            return SimpleNamespace(
                comparison=SimpleNamespace(execution_accuracy=1, equivalent=True)
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                {"question_id": 1, "db_id": "db", "difficulty": "simple"},
                {"question_id": 2, "db_id": "db", "difficulty": "simple"},
            ]
            with self.assertRaises(KeyboardInterrupt):
                BatchRunner(run_case).run(
                    cases=cases,
                    databases_root=root / "databases",
                    runs_root=root / "runs",
                    run_id="interrupted",
                )
            summary = json.loads(
                (root / "runs/interrupted/batch_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual("aborted", summary["status"])
            self.assertEqual(1, summary["processed"])
            self.assertIsNotNone(summary["finished_at"])

            resumed_ids = []

            def resumed_run(case, database, logger):
                resumed_ids.append(str(case["question_id"]))
                logger.set_step("P14")
                logger.write_json("evaluation/metrics.json", {})
                return SimpleNamespace(
                    comparison=SimpleNamespace(execution_accuracy=1, equivalent=True)
                )

            resumed = BatchRunner(resumed_run).run(
                cases=cases,
                databases_root=root / "databases",
                runs_root=root / "runs",
                run_id="interrupted",
                resume=True,
            )
            self.assertEqual(["2"], resumed_ids)
            self.assertEqual("complete", resumed["status"])
            self.assertEqual(2, resumed["processed"])
            self.assertTrue((root / "runs/interrupted/interrupted_attempts").is_dir())

    def test_resume_can_retry_failed_cases_without_rerunning_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                {"question_id": 1, "db_id": "db", "difficulty": "simple"},
                {"question_id": 2, "db_id": "db", "difficulty": "simple"},
            ]

            def initial_run(case, database, logger):
                if case["question_id"] == 1:
                    raise RuntimeError("temporary failure")
                logger.set_step("P14")
                logger.write_json("evaluation/metrics.json", {})
                return SimpleNamespace(
                    comparison=SimpleNamespace(execution_accuracy=1, equivalent=True)
                )

            first = BatchRunner(initial_run).run(
                cases=cases,
                databases_root=root / "databases",
                runs_root=root / "runs",
                run_id="retry",
            )
            self.assertEqual(1, first["pipeline_failed"])
            retried = []

            def retry_run(case, database, logger):
                retried.append(str(case["question_id"]))
                logger.set_step("P14")
                logger.write_json("evaluation/metrics.json", {})
                return SimpleNamespace(
                    comparison=SimpleNamespace(execution_accuracy=1, equivalent=True)
                )

            second = BatchRunner(retry_run).run(
                cases=cases,
                databases_root=root / "databases",
                runs_root=root / "runs",
                run_id="retry",
                resume=True,
                retry_failed=True,
            )
            self.assertEqual(["1"], retried)
            self.assertEqual(0, second["pipeline_failed"])
            self.assertEqual(2, second["completed"])


if __name__ == "__main__":
    unittest.main()
