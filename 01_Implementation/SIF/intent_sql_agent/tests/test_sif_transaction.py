from __future__ import annotations

import unittest

from intent_sql_agent.services.sif_transaction import reduce_sif_candidate


class SIFTransactionTests(unittest.TestCase):
    def test_rejects_candidate_that_introduces_a_new_grounding_warning(self) -> None:
        previous = {"query_blocks": [], "main_block_id": "main"}
        candidate = {"query_blocks": [], "main_block_id": "main", "status": "ready"}
        result, events = reduce_sif_candidate(
            previous,
            candidate,
            bif={"target_population": {}, "metrics": [], "output_expectation": {"fields": []}},
            schema_catalog={"tables": []},
            locked_tables=set(),
            previous_grounding_warnings=[{"issue": "unsupported_sif_feature", "error": "x"}],
            candidate_grounding_warnings=[
                {"issue": "literal_not_in_column_domain", "table": "molecule", "column": "id", "literal": "+"}
            ],
        )
        self.assertEqual(previous, result)
        self.assertIn("grounding_warning_regression", {item.get("reason") for item in events})

    def test_rejects_candidate_that_loses_existing_bif_coverage(self) -> None:
        bif = {
            "target_population": {"primary_entity": "customer", "inclusion_conditions": []},
            "metrics": [],
            "output_expectation": {"fields": []},
        }
        previous = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [{
                    "kind": "table", "name": "customers", "alias": "c",
                    "intent_paths": ["/target_population/primary_entity"],
                }],
                "where": [], "projection": [],
            }],
        }
        candidate = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [{
                    "kind": "table", "name": "customers", "alias": "c",
                    "intent_paths": [],
                }],
                "where": [], "projection": [],
            }],
        }
        result, events = reduce_sif_candidate(
            previous,
            candidate,
            bif=bif,
            schema_catalog={"tables": []},
            locked_tables=set(),
        )
        self.assertEqual(previous, result)
        self.assertIn("bif_coverage_regression", {item.get("reason") for item in events})

    def test_commits_candidate_without_invariant_regression(self) -> None:
        bif = {
            "target_population": {"primary_entity": "customer", "inclusion_conditions": []},
            "metrics": [],
            "output_expectation": {"fields": []},
        }
        previous = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [{
                    "kind": "table", "name": "customers", "alias": "c",
                    "intent_paths": ["/target_population/primary_entity"],
                }],
                "where": [], "projection": [],
            }],
        }
        candidate = {
            **previous,
            "status": "ready",
        }
        result, events = reduce_sif_candidate(
            previous,
            candidate,
            bif=bif,
            schema_catalog={"tables": []},
            locked_tables=set(),
        )
        self.assertEqual("ready", result["status"])
        self.assertIn("commit_sif_candidate", {item.get("operation") for item in events})


if __name__ == "__main__":
    unittest.main()
