from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from jsonschema import Draft202012Validator

from intent_sql_agent.pipeline.full_case_runner import FullCaseRunner
from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.query_intent_compiler import compile_query_intent_layout
from intent_sql_agent.services.query_intent_layout import (
    apply_sif_links,
    create_sif_overlay,
    grounding_proposal_schema,
)
from intent_sql_agent.services.sql_executor import ReadOnlySQLiteExecutor


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec"


def _load(name: str) -> dict:
    return json.loads((SPEC / "examples" / name).read_text(encoding="utf-8"))


class SimpleQueryLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bif = _load("q2.bif_query_layout.json")
        self.sif = _load("q2.sif_query_layout.json")

    def test_bif_and_sif_use_one_flat_schema(self) -> None:
        schema = json.loads(
            (SPEC / "query_intent_layout.schema.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)
        validator.validate(self.bif)
        validator.validate(self.sif)
        self.assertEqual(set(self.bif), set(self.sif))

    def test_removed_nested_metadata_is_not_in_layout(self) -> None:
        raw = json.dumps(self.sif)
        for removed in (
            "grounding", "bindings", "constraint_results", "evidence",
            "technical_decision_refs", "origin_node_ids", "source_refs",
            "coverage", "readiness", "node_kind",
        ):
            self.assertNotIn(removed, raw)

    def test_a4_contract_contains_links_not_layout_clauses(self) -> None:
        schema = json.loads(
            (SPEC / "query_intent_layout.schema.json").read_text(encoding="utf-8")
        )
        proposal = grounding_proposal_schema(schema)
        self.assertEqual(
            set(proposal["properties"]),
            {"sources", "fields", "predicates", "having_predicates", "joins"},
        )
        self.assertNotIn("select", proposal["properties"])
        self.assertNotIn("group_by", proposal["properties"])

    def test_field_is_linked_once_and_clauses_reference_id(self) -> None:
        fields = {item["id"]: item for item in self.sif["fields"]}
        self.assertEqual(self.sif["select"], ["zip"])
        sources = {item["id"]: item for item in self.sif["sources"]}
        self.assertEqual(sources[fields["zip"]["source"]]["table"], "schools")
        self.assertEqual(fields["zip"]["column"], "Zip")

    def test_overlay_changes_only_phase(self) -> None:
        overlay = create_sif_overlay(self.bif)
        expected = copy.deepcopy(self.bif)
        expected["phase"] = "sif"
        self.assertEqual(overlay, expected)

    def test_link_application_preserves_bif_sql_structure(self) -> None:
        altered = copy.deepcopy(self.sif)
        altered["select"] = ["organization"]
        altered["where"] = None
        result = apply_sif_links(self.bif, altered)
        self.assertEqual(result["select"], self.bif["select"])
        self.assertEqual(result["where"], self.bif["where"])
        self.assertEqual(result["sources"][1]["table"], "schools")

    def test_q2_compiles_from_simple_layout(self) -> None:
        sql = compile_query_intent_layout(self.bif, self.sif)
        self.assertIn('SELECT "schools"."Zip"', sql)
        self.assertIn('INNER JOIN "schools"', sql)
        self.assertIn('"frpm"."Charter School (Y/N)" = 1', sql)
        self.assertIn("'Fresno County Office of Education'", sql)

    def test_q78_mode_uses_one_field_link(self) -> None:
        bif = {
            "schema_version": "4.0", "phase": "bif",
            "sources": [{"id": "school", "role": "schools", "table": None}],
            "fields": [{"id": "grade_span", "role": "grade span", "source": "school", "column": None, "expression": None, "aggregate": None, "distinct": False},
                       {"id": "city", "role": "city", "source": "school", "column": None, "expression": None, "aggregate": None, "distinct": False}],
            "select": ["grade_span"],
            "predicates": [{"id": "city_filter", "field": "city", "role": "city is Adelanto", "operator": "=", "value": "Adelanto"}],
            "where": "city_filter", "group_by": ["grade_span"],
            "having_predicates": [], "having": None,
            "order_by": [{"field": "grade_span", "aggregate": "count", "direction": "desc"}],
            "joins": [], "distinct": False, "limit": 1,
        }
        sif = copy.deepcopy(bif)
        sif["phase"] = "sif"
        sif["sources"][0]["table"] = "schools"
        sif["fields"][0]["column"] = "GSserved"
        sif["fields"][1]["column"] = "City"
        sql = compile_query_intent_layout(bif, sif)
        self.assertEqual(sql.count('"schools"."GSserved"'), 3)
        self.assertIn('ORDER BY COUNT("schools"."GSserved") DESC', sql)

    def test_runner_uses_simple_sif_and_compares_gold(self) -> None:
        cases = json.loads(
            (ROOT / "data/qa_set/BIRD_DEV_STRATIFIED_SMOKE_30.json")
            .read_text(encoding="utf-8")
        )
        case = next(item for item in cases if str(item["question_id"]) == "2")

        class Client:
            call_counts: dict[str, int] = {}

        class Agents:
            client = Client()
            semantic_frontend_active = True
            semantic_frontend_result = {
                "confirmed_cqd": {},
                "contract_coverage": [
                    {"family": f"contract_{index}", "status": "safe"}
                    for index in range(13)
                ],
                "query_layout_obligations": {
                    "authority": "BIFLayoutCompletionOrchestrator",
                    "finalized_once": True,
                    "history": [],
                    "coverage": {"status": "complete", "requirements": []},
                },
            }

            def a1_bif_analyze(self, selected_case, schema_catalog=None):
                return {"draft_bif": copy.deepcopy(bif), "issue_observations": {"candidates": []}}

            def a4_query_layout_grounding_proposal(
                self, selected_case, input_bif, overlay, schema_catalog, *, request_id,
                technical_feedback=None,
            ):
                return copy.deepcopy(sif)

        bif = self.bif
        sif = self.sif
        database = ROOT / "data/bird_dev/dev_databases" / case["db_id"] / f"{case['db_id']}.sqlite"
        with TemporaryDirectory() as directory:
            logger = ArtifactLogger(Path(directory), "simple", "2")
            result = FullCaseRunner(
                Agents(), ReadOnlySQLiteExecutor(), sif_layout="v3"
            ).run(case=case, database=database, logger=logger)
            self.assertTrue(result.comparison.equivalent)


if __name__ == "__main__":
    unittest.main()
