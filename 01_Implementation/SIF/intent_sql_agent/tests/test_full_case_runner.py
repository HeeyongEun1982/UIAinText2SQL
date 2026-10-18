import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.pipeline import FullCaseRunner, PipelineRunError
from intent_sql_agent.services import ArtifactLogger, ReadOnlySQLiteExecutor
from intent_sql_agent.services.frame_state import frame_hash


class FakeAgentSuite:
    def __init__(self) -> None:
        self.calls = []
        self.bif = {
            "decision_objective": {"summary": "values", "intent_types": ["retrieve"]},
            "target_population": {},
            "metrics": [],
            "scope": {},
            "time_basis": {},
            "comparisons": [],
            "output_expectation": {},
            "readiness": {"state": "ready"},
        }
        self.sif = {"status": "ready", "unresolved_grounding": [], "traceability": []}
        self.clarification = {
            "clarification_request": {
                "request_id": "CQ1",
                "question": "Confirm?",
                "options": [{"option_id": "YES", "label": "Yes"}],
            }
        }

    def a1_bif_analyze(self, case):
        self.calls.append("A1:ANALYZE")
        return {
            "draft_bif": self.bif,
            "issue_observations": {"candidates": []},
        }

    def a2_bif_contract_detect(
        self, *, original_question, bif, contract, allowed_paths
    ):
        return {
            "family": contract["family"],
            "status": "not_applicable",
            "disposition": "none",
            "source_spans": [],
            "reason": "The synthetic case contains no construct for this contract.",
            "findings": [],
        }

    def a2_bif_discover(self, case, bif, decision_ledger):
        self.calls.append("A2_DISCOVERY")
        return {"candidates": []}

    def a2_bif_question(self, case, bif, decision, decision_ledger):
        self.calls.append("A2_QUESTION")
        return self._question_draft(decision)

    def e2_user(self, **kwargs):
        self.calls.append(f"E2:{kwargs['mode']}")
        request = kwargs["clarification"]["clarification_request"]
        options = request["options"]
        return {
            "request_id": request["request_id"],
            "action": "select",
            "selected_option_ids": [options[0]["option_id"]],
        }

    def a3_bif_assemble(self, case, draft_bif, decision_ledger):
        self.calls.append("A3")
        return {
            "confirmed_bif": self.bif,
            "applied_decision_ids": [item["decision_id"] for item in decision_ledger],
        }

    def a4_sif_draft(self, *args):
        self.calls.append("A4")
        return self.sif

    def a5_sif_discover(self, case, bif, sif, decision_ledger, source_ambiguities=None):
        self.calls.append("A5_DISCOVERY")
        return {"candidates": []}

    def a5_sif_question(self, case, bif, sif, decision, decision_ledger):
        self.calls.append("A5_QUESTION")
        return self._question_draft(decision)

    @staticmethod
    def _question_draft(decision):
        alternatives = list(decision.get("choice_domain", []))
        if len(alternatives) < 2:
            alternatives = ["First meaning", "Second meaning"]
        return {
            "question": "Which meaning do you intend?",
            "why_it_matters": "The choice changes the result.",
            "options": [{"label": item} for item in alternatives],
            "allow_free_text": False,
        }

    def a6_sif_assemble(
        self, bif, draft_sif, decision_ledger, schema_catalog, source_ambiguities=None
    ):
        self.calls.append("A6")
        return {
            "confirmed_sif": self.sif,
            "applied_decision_ids": [item["decision_id"] for item in decision_ledger],
        }

    def a7_generate_sql(self, *args):
        self.calls.append("A7")
        return "SELECT value FROM items ORDER BY id"

    def a8_verify_or_repair(self, **kwargs):
        self.calls.append("A8")
        return {"status": "accept", "sql": None, "reason": "valid", "affected_intent_paths": []}


class MultiRoundFakeAgentSuite(FakeAgentSuite):
    def __init__(self) -> None:
        super().__init__()
        self.bif["target_population"] = {
            f"decision_{index}": "draft" for index in range(5)
        }
        self.sif["scope"] = {
            f"decision_{index}": "draft" for index in range(3)
        }

    def a1_bif_analyze(self, case):
        bundle = super().a1_bif_analyze(case)
        bundle["issue_observations"] = {"candidates": [{
            "issue_type": "SCOPE_SELECTION",
            "subject_ref": "/target_population/decision_0",
            "description": "Choose scope 0.",
            "alternatives": ["First scope", "Second scope"],
        }]}
        return bundle

    def a2_bif_discover(self, case, bif, decision_ledger):
        self.calls.append("A2_DISCOVERY")
        index = len(decision_ledger or [])
        if index >= 5:
            return {"candidates": []}
        return {"candidates": [{
            "issue_type": "SCOPE_SELECTION",
            "subject_ref": f"/target_population/decision_{index}",
            "description": f"Choose scope {index}.",
            "alternatives": ["First scope", "Second scope"],
        }]}

    def a2_bif_question(self, case, bif, decision, decision_ledger):
        self.calls.append("A2_QUESTION")
        return self._question_draft(decision)

    def a6_grounding_patch(self, *args):
        self.calls.append("A6:SYSTEM_REGROUND")
        sif = args[1]
        return {
            "request_id": "system_grounding",
            "gap_id": "system_grounding",
            "base_frame_hash": frame_hash(sif),
            "operations": [],
        }

    def a5_sif_discover(
        self, case, bif, sif, decision_ledger, source_ambiguities=None,
    ):
        self.calls.append("A5_DISCOVERY")
        decision_count = len(decision_ledger or [])
        if decision_count >= 3:
            return {"candidates": []}
        return {"candidates": [{
            "issue_type": "QUERY_SELECTION_SCOPE",
            "subject_ref": f"/scope/decision_{decision_count}",
            "description": f"Choose query scope {decision_count}.",
            "alternatives": ["First query scope", "Second query scope"],
        }]}

    def a5_sif_question(self, case, bif, sif, decision, decision_ledger):
        self.calls.append("A5_QUESTION")
        return self._question_draft(decision)


class OptimizedBifFakeAgentSuite(FakeAgentSuite):
    def a1_bif_analyze(self, case):
        self.calls.append("A1:ANALYZE")
        return {
            "draft_bif": self.bif,
            "issue_observations": {"candidates": []},
        }

class CompilableSifFakeAgentSuite(FakeAgentSuite):
    def __init__(self) -> None:
        super().__init__()
        self.sif = {
            "status": "ready",
            "unresolved_grounding": [],
            "traceability": [],
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [{
                    "source_id": "items", "kind": "table", "name": "items", "alias": "i"
                }],
                "joins": [], "where": [], "group_by": [], "having": [], "windows": [],
                "projection": [{
                    "label": "value",
                    "expression": {"kind": "column", "source": "i", "name": "value"},
                }],
                "distinct": False,
                "order_by": [{
                    "expression": {"kind": "column", "source": "i", "name": "id"},
                    "direction": "asc", "nulls": "dialect_default",
                }],
                "limit": None, "set_operations": [],
            }],
        }


class ReadyAfterSifResponseFakeAgentSuite(FakeAgentSuite):
    def __init__(self) -> None:
        super().__init__()
        self.sif = {
            "status": "needs_clarification",
            "unresolved_grounding": ["scope"],
            "traceability": [],
        }

    def a5_sif_discover(
        self, case, bif, sif, decision_ledger, source_ambiguities=None,
    ):
        self.calls.append("A5_DISCOVERY")
        if decision_ledger:
            return {"candidates": []}
        return {"candidates": [{
            "issue_type": "QUERY_SELECTION_SCOPE",
            "subject_ref": "/decision_objective",
            "description": "Choose query scope.",
            "alternatives": ["First scope", "Second scope"],
        }]}

    def a5_sif_question(self, case, bif, sif, decision, decision_ledger):
        self.calls.append("A5_QUESTION")
        return self._question_draft(decision)

    def a6_sif_assemble(
        self, bif, draft_sif, decision_ledger, schema_catalog, source_ambiguities=None
    ):
        self.calls.append("A6")
        return {
            "confirmed_sif": {
                "status": "ready",
                "unresolved_grounding": [],
                "traceability": [{
                    "intent_path": "/decision_objective",
                    "sql_intent_nodes": ["main"],
                }],
            },
            "applied_decision_ids": [item["decision_id"] for item in decision_ledger],
        }


class BifReentryFakeAgentSuite(FakeAgentSuite):
    def __init__(self) -> None:
        super().__init__()
        self.reentry_emitted = False

    def a5_sif_discover(
        self, case, bif, sif, decision_ledger, source_ambiguities=None,
    ):
        self.calls.append("A5_DISCOVERY")
        if self.reentry_emitted:
            return {"candidates": []}
        self.reentry_emitted = True
        return {"candidates": [{
            "issue_type": "LOCATION_ROLE",
            "subject_ref": "/target_population",
            "description": "Clarify whether Fresno is a city or district.",
            "alternatives": ["City", "District"],
        }]}

    def a3_bif_assemble(self, case, draft_bif, decision_ledger):
        self.calls.append("A3")
        assembled = json.loads(json.dumps(self.bif))
        if any(item.get("issue") == "LOCATION_ROLE" for item in decision_ledger):
            assembled["target_population"] = {"description": "Schools in Fresno city"}
        return {
            "confirmed_bif": assembled,
            "applied_decision_ids": [item["decision_id"] for item in decision_ledger],
        }


class BifMutatingSifFakeAgentSuite(FakeAgentSuite):
    def a4_sif_draft(self, case, bif, schema_catalog):
        self.calls.append("A4")
        bif["target_population"]["illicit_sif_mutation"] = True
        return self.sif


class FinalRevisionFakeAgentSuite(CompilableSifFakeAgentSuite):
    def __init__(self) -> None:
        super().__init__()
        self.revised_stages = set()

    def e2_user(self, **kwargs):
        request = kwargs["clarification"]["clarification_request"]
        stage = kwargs["clarification"]["stage"]
        self.calls.append(f"E2:{kwargs['mode']}")
        if request.get("gap_id") == "final_confirmation" and stage not in self.revised_stages:
            self.revised_stages.add(stage)
            return {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["REVISE"],
                "free_text": f"Apply the final {stage} correction in business terms.",
            }
        return {
            "request_id": request["request_id"],
            "action": "select",
            "selected_option_ids": [request["options"][0]["option_id"]],
        }


class FullCaseRunnerTest(unittest.TestCase):
    def test_decision_coverage_survives_output_array_reindexing(self) -> None:
        frame = {
            "output_expectation": {
                "fields": [{"label": "Email", "presentation": "separate_columns"}]
            }
        }
        ledger = [{
            "decision_id": "D-OUTPUT",
            "issue": "OUTPUT_SHAPE",
            "affected_paths": ["/output_expectation/fields/1/presentation"],
            "canonical_effect": {"value": {"canonical_values": ["separate_fields"]}},
        }]
        report = FullCaseRunner._validate_decision_path_coverage(
            frame, ledger, layer="BIF"
        )
        self.assertEqual("semantic_container", report["covered_paths"][0]["coverage"])

    def test_final_revise_reenters_bif_and_sif_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO items(value) VALUES ('a')")
            connection.commit()
            connection.close()
            agents = FinalRevisionFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "final_revise", "1")
            result = FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1,
                    "db_id": "case",
                    "question": "List values",
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )
            self.assertTrue(result.comparison.equivalent)
            self.assertGreaterEqual(agents.calls.count("A3"), 2)
            self.assertGreaterEqual(agents.calls.count("A6"), 2)
            self.assertEqual({"bif", "sif"}, agents.revised_stages)

    def test_sif_processing_cannot_mutate_confirmed_bif(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO items(value) VALUES ('a')")
            connection.commit()
            connection.close()
            agents = BifMutatingSifFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "bif_immutable", "1")
            with self.assertRaisesRegex(
                PipelineRunError, "SIF_BIF_IMMUTABILITY_VIOLATION"
            ):
                FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                    case={
                        "question_id": 1,
                        "db_id": "case",
                        "question": "List values",
                        "difficulty": "simple",
                        "SQL": "SELECT value FROM items ORDER BY id",
                    },
                    database=database,
                    logger=logger,
                    comparison_policy="ordered",
                )

    def test_sif_bif_level_gap_reenters_bif_and_regenerates_sif(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO items(value) VALUES ('a')")
            connection.commit()
            connection.close()
            agents = BifReentryFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "bif_reentry", "1")
            FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1,
                    "db_id": "case",
                    "question": "List values for schools in Fresno city",
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )
            self.assertGreaterEqual(agents.calls.count("A4"), 2)
            events = (logger.case_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("BIF_REENTRY_COMPLETED_AND_SIF_REGENERATED", events)

    def test_answered_sif_decision_does_not_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO items(value) VALUES ('a')")
            connection.commit()
            connection.close()
            agents = ReadyAfterSifResponseFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "sif_once", "1")
            FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1,
                    "db_id": "case",
                    "question": "List values",
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )
            metrics = json.loads(
                (logger.case_dir / "P14/evaluation/metrics.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(2, metrics["sif_clarification_rounds"])
            self.assertEqual(1, agents.calls.count("A6"))

    def test_run_case_executes_all_agent_roles_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.executemany("INSERT INTO items(value) VALUES (?)", [("a",), ("b",)])
            connection.commit()
            connection.close()

            agents = FakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "full", "1")
            result = FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1,
                    "db_id": "case",
                    "question": "List values",
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )

            self.assertTrue(result.comparison.equivalent)
            self.assertEqual(
                [
                    "A1:ANALYZE", "A2_DISCOVERY", "A3", "E2:BIF_CLARIFICATION", "A4", "A5_DISCOVERY",
                    "A6", "E2:SIF_CLARIFICATION", "A7", "A8",
                ],
                agents.calls,
            )
            complete = json.loads(
                (logger.case_dir / "run_complete.json").read_text(encoding="utf-8")
            )
            self.assertEqual("RUN_COMMITTED", complete["final_state"])
            self.assertTrue(complete["manifest_valid"])
            self.assertEqual(9, len(complete["agent_roles_executed"]))

    def test_unbounded_clarification_can_exceed_old_round_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO items(value) VALUES ('a')")
            connection.commit()
            connection.close()

            agents = MultiRoundFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "multi", "1")
            FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1,
                    "db_id": "case",
                    "question": "List values",
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )
            metrics = json.loads(
                (logger.case_dir / "P14/evaluation/metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(6, metrics["bif_clarification_rounds"])
            self.assertEqual(4, metrics["sif_clarification_rounds"])
            self.assertEqual(10, metrics["total_clarification_rounds"])
            self.assertEqual(1, agents.calls.count("A3"))
            self.assertEqual(1, agents.calls.count("A6"))
            self.assertFalse(metrics["iterative_frame_update_enabled"])
            self.assertTrue(metrics["one_shot_frame_assembly_enabled"])
            self.assertFalse(metrics["canonical_frame_compaction_enabled"])
            self.assertIsNone(metrics["bif_round_limit"])
            self.assertIsNone(metrics["sif_round_limit"])
            transaction_files = list(
                logger.case_dir.glob("P02/frames/bif_rounds/round_*/transaction_result.json")
            )
            self.assertGreaterEqual(len(transaction_files), 5)
            first_round = transaction_files[0].parent
            for filename in (
                "queue_before.json",
                "canonical_effect.json",
                "intent_state_before.json",
                "intent_state_after.json",
                "reconciliation_events.json",
                "queue_after.json",
            ):
                self.assertTrue((first_round / filename).exists(), filename)

    def test_combined_bif_analysis_uses_one_shot_a3_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO items(value) VALUES ('a')")
            connection.commit()
            connection.close()

            agents = OptimizedBifFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "optimized", "1")
            FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1,
                    "db_id": "case",
                    "question": "List values",
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )

            self.assertEqual(
                [
                    "A1:ANALYZE",
                    "A2_DISCOVERY",
                    "A3",
                    "E2:BIF_CLARIFICATION",
                    "A4",
                    "A5_DISCOVERY",
                    "A6",
                    "E2:SIF_CLARIFICATION",
                    "A7",
                    "A8",
                ],
                agents.calls,
            )
            events = (logger.case_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("BIF_QUEUE_STABLE_FOR_MATERIALIZATION", events)
            self.assertIn("BIF_CONFIRMED", events)

    def test_compilable_sif_skips_a7_and_a8_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "case.sqlite"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
            connection.executemany("INSERT INTO items(value) VALUES (?)", [("a",), ("b",)])
            connection.commit()
            connection.close()

            agents = CompilableSifFakeAgentSuite()
            logger = ArtifactLogger(root / "runs", "compiled", "1")
            result = FullCaseRunner(agents, ReadOnlySQLiteExecutor()).run(
                case={
                    "question_id": 1, "db_id": "case", "question": "List values",
                    "difficulty": "simple", "SQL": "SELECT value FROM items ORDER BY id",
                },
                database=database,
                logger=logger,
                comparison_policy="ordered",
            )
            self.assertTrue(result.comparison.equivalent)
            self.assertNotIn("A7", agents.calls)
            self.assertNotIn("A8", agents.calls)
            metrics = json.loads(
                (logger.case_dir / "P14/evaluation/metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual("deterministic_sif_compiler", metrics["sql_generation_strategy"])
            self.assertEqual("static_accept_before_preflight", metrics["sql_validation_strategy"])
            self.assertEqual(2, metrics["full_db_schema_model_calls"])


if __name__ == "__main__":
    unittest.main()
