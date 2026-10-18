from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from intent_sql_agent.agents.suite import AgentSuite
from intent_sql_agent.services.cqd_layout_obligations import compile_layout_obligations


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/query_layout_v3_q78_q87_20260821_001/78"


def _q78_cqd() -> dict:
    return json.loads(
        (RUN / "P01/semantic_frontend/06_final/confirmed_cqd.json")
        .read_text(encoding="utf-8")
    )


def _simple_q78_bif() -> dict:
    return {
        "schema_version": "4.0", "phase": "bif",
        "sources": [{"id": "school", "role": "schools", "table": None}],
        "fields": [
            {"id": "grade_span", "role": "grade span", "source": "school", "column": None, "expression": None, "aggregate": None, "distinct": False},
            {"id": "city", "role": "city", "source": "school", "column": None, "expression": None, "aggregate": None, "distinct": False},
        ],
        "select": ["grade_span"],
        "predicates": [{"id": "city_filter", "field": "city", "role": "city is Adelanto", "operator": "=", "value": "Adelanto"}],
        "where": "city_filter", "group_by": ["grade_span"],
        "having_predicates": [], "having": None,
        "order_by": [{"field": "grade_span", "aggregate": "count", "direction": "desc"}],
        "joins": [], "distinct": False, "limit": 1,
    }


class CQDLayoutObligationTests(unittest.TestCase):
    def test_mode_guidance_still_identifies_sql_roles(self) -> None:
        roles = {
            item["role"] for item in compile_layout_obligations(_q78_cqd())
            if item["obligation_id"].startswith("mode_")
        }
        self.assertEqual(
            roles, {"select", "group_by", "order_by_frequency_desc", "limit"}
        )

    def test_a3_ir_baseline_is_finalized_by_orchestrator(self) -> None:
        class Client:
            def __init__(self):
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                layout = kwargs["payload"]["authoritative_working_layout_read_only"]
                gap = kwargs["payload"]["open_coverage_gaps"][0]
                entity = gap["payload"]["entity"]
                return {"patches": [{
                    "cqd_ref": "/populations/0", "operation": "add",
                    "target_slot_ids": [],
                    "evidence_ids": ["cqd:/populations/0"],
                    "reason": "preserve population membership explicitly",
                    "sources": [],
                    "fields": [{
                        "id": "field_population_membership",
                        "role": f"{entity} status", "source": layout["sources"][0]["id"],
                        "semantic_ref": "/populations/0/entity",
                        "aggregate": None, "distinct": False,
                    }],
                    "predicates": [{
                        "id": "predicate_population_membership",
                        "field": "field_population_membership", "role": entity,
                        "semantic_ref": "/populations/0/entity",
                        "operator": "equals", "value": entity,
                    }],
                    "select": [], "group_by": [], "order_by": [], "limit": None,
                }]}

        client = Client()
        schema = json.loads(
            (ROOT / "spec/query_intent_layout.schema.json").read_text(encoding="utf-8")
        )
        suite = AgentSuite(client, bif_schema={}, sif_schema={}, query_layout_schema=schema)
        result = suite.a3_query_layout_from_cqd(
            case={"question_id": 78, "db_id": "california_schools", "question": "q"},
            confirmed_cqd=_q78_cqd(),
        )
        self.assertEqual(len(result["select"]), 1)
        self.assertEqual(result["group_by"], result["select"])
        self.assertEqual(result["order_by"][0]["aggregate"], "count")
        self.assertEqual(result["limit"], 1)
        self.assertEqual(client.calls, 1)
        self.assertNotIn("grounding", json.dumps(result))
        self.assertTrue(suite.query_layout_obligation_report["finalized_once"])


if __name__ == "__main__":
    unittest.main()
