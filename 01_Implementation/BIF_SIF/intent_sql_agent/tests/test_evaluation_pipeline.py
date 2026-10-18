import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.agents import AGENT_SPECS
from intent_sql_agent.pipeline import EvaluationRunner
from intent_sql_agent.services import ArtifactLogger, ReadOnlySQLiteExecutor


class EvaluationPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "test.sqlite"
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO items(value) VALUES (?)", [("b",), ("a",), ("a",)])
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_registry_has_exactly_nine_agents(self) -> None:
        self.assertEqual(9, len(AGENT_SPECS))

    def test_multiset_comparison_and_logs(self) -> None:
        logger = ArtifactLogger(self.root / "runs", "run-1", "q-1")
        runner = EvaluationRunner(ReadOnlySQLiteExecutor())
        comparison = runner.run(
            database=self.database,
            gold_sql="SELECT value FROM items ORDER BY id",
            generated_sql="SELECT value FROM items ORDER BY value",
            logger=logger,
            comparison_policy="multiset",
        )
        self.assertTrue(comparison.equivalent)
        self.assertTrue(
            (logger.case_dir / "P12/execution/result_comparison.json").is_file()
        )
        self.assertTrue(
            (logger.case_dir / "P11/execution/gold_result.canonical.json").is_file()
        )
        self.assertTrue(
            (logger.case_dir / "P11/execution/generated_result.canonical.json").is_file()
        )
        self.assertTrue((logger.case_dir / "manifest.json").is_file())
        self.assertTrue((logger.case_dir / "run_complete.json").is_file())
        self.assertTrue(logger.verify_hash_manifest())
        events = (logger.case_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(6, len(events))
        self.assertEqual("RUN_COMMITTED", json.loads(events[-1])["payload"]["to"])
        self.assertEqual("P14", json.loads(events[-1])["payload"]["step"])

    def test_ordered_comparison_detects_difference(self) -> None:
        logger = ArtifactLogger(self.root / "runs", "run-2", "q-2")
        runner = EvaluationRunner(ReadOnlySQLiteExecutor())
        comparison = runner.run(
            database=self.database,
            gold_sql="SELECT value FROM items ORDER BY id",
            generated_sql="SELECT value FROM items ORDER BY value",
            logger=logger,
            comparison_policy="ordered",
        )
        self.assertFalse(comparison.equivalent)


if __name__ == "__main__":
    unittest.main()
