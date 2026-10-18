import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.services.sif_coverage import validate_sif_coverage
from intent_sql_agent.services.sif_repair import (
    repair_bif_logic_operators,
    repair_merged_independent_conditions,
    repair_malformed_boolean_expressions,
    repair_non_exact_question_values,
)


class SifCoverageTest(unittest.TestCase):
    def test_stable_bif_ids_satisfy_metric_and_condition_coverage(self) -> None:
        bif = {
            "target_population": {
                "primary_entity": None,
                "inclusion_conditions": [{"condition_id": "county", "value": "York"}],
            },
            "metrics": [{"metric_id": "school_email", "name": "Email"}],
            "output_expectation": {"fields": []},
        }
        sif = {"query_blocks": [{
            "where": [{"intent_paths": ["/target_population/inclusion_conditions/county"]}],
            "projection": [{"intent_paths": ["/metrics/school_email"]}],
        }]}
        warnings = validate_sif_coverage(bif, sif)
        self.assertNotIn("missing_bif_intent_path", {item["issue"] for item in warnings})

    def test_detects_missing_condition_and_untraced_added_filter(self) -> None:
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    {"condition_id": "1"},
                    {"condition_id": "2"},
                ]
            },
            "metrics": [{"metric_id": "1"}],
            "output_expectation": {
                "fields": [{"semantic_ref": "/metrics/0"}]
            },
        }
        sif = {
            "query_blocks": [{
                "block_id": "main",
                "sources": [{"intent_paths": ["/target_population/primary_entity"]}],
                "where": [{
                    "kind": "binary", "operator": "and",
                    "left": {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "s", "name": "County", "intent_paths": ["/target_population/inclusion_conditions/0"]},
                        "right": {"kind": "literal", "value": "X"},
                    },
                    "right": {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "s", "name": "StatusType", "intent_paths": []},
                        "right": {"kind": "literal", "value": "Active"},
                    },
                }],
                "projection": [{"expression": {"kind": "column", "source": "s", "name": "Email", "intent_paths": ["/metrics/0"]}}],
            }]
        }
        warnings = validate_sif_coverage(bif, sif)
        issues = [item["issue"] for item in warnings]
        self.assertIn("missing_bif_intent_path", issues)
        self.assertIn("untraced_predicate", issues)
        self.assertIn(
            "/target_population/inclusion_conditions/1",
            {item.get("intent_path") for item in warnings},
        )

    def test_detects_and_repairs_bif_all_of_changed_to_sif_or(self) -> None:
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    {"condition_id": "level"},
                    {"condition_id": "authority"},
                ],
                "inclusion_logic": {
                    "operator": "all_of",
                    "condition_refs": ["level", "authority"],
                    "groups": [],
                },
            },
            "metrics": [],
            "output_expectation": {"fields": []},
        }
        sif = {"query_blocks": [{
            "block_id": "main", "sources": [], "projection": [],
            "where": [{
                "kind": "binary", "operator": "or",
                "left": {"kind": "binary", "operator": "=", "left": {
                    "kind": "column", "source": "s", "name": "SOC",
                    "intent_paths": ["/target_population/inclusion_conditions/0"],
                }, "right": {"kind": "literal", "value": "62"}},
                "right": {"kind": "binary", "operator": "=", "left": {
                    "kind": "column", "source": "s", "name": "DOC",
                    "intent_paths": ["/target_population/inclusion_conditions/1"],
                }, "right": {"kind": "literal", "value": "54"}},
            }],
        }]}
        warnings = validate_sif_coverage(bif, sif)
        logic_warnings = [item for item in warnings if item["issue"] == "bif_logic_operator_mismatch"]
        self.assertEqual(1, len(logic_warnings))
        repaired, repairs = repair_bif_logic_operators(sif, logic_warnings)
        self.assertEqual("and", repaired["query_blocks"][0]["where"][0]["operator"])
        self.assertEqual(1, len(repairs))

    def test_detects_and_collapses_single_operand_boolean_wrapper(self) -> None:
        bif = {"target_population": {"inclusion_conditions": []}, "metrics": [], "output_expectation": {"fields": []}}
        sif = {"query_blocks": [{
            "block_id": "main", "sources": [], "projection": [],
            "where": [{"kind": "binary", "operator": "and", "left": {
                "kind": "binary", "operator": "=",
                "left": {"kind": "column", "source": "s", "name": "x"},
                "right": {"kind": "literal", "value": 1},
            }}],
        }]}
        warnings = validate_sif_coverage(bif, sif)
        malformed = [item for item in warnings if item["issue"] == "malformed_boolean_expression"]
        self.assertEqual(1, len(malformed))
        repaired, repairs = repair_malformed_boolean_expressions(sif, malformed)
        self.assertEqual("=", repaired["query_blocks"][0]["where"][0]["operator"])
        self.assertEqual(1, len(repairs))

    def test_q76_repairs_longest_literal_and_splits_grade_boundaries(self) -> None:
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    {
                        "condition_id": "lunch",
                        "semantic_dimension": "school.lunch_provision",
                        "subject": "lunch_provision",
                        "operator": "equals",
                        "value": "2",
                        "description": "School has Lunch Provision 2",
                    },
                    {
                        "condition_id": "low",
                        "semantic_dimension": "school.lowest_grade",
                        "subject": "lowest_grade",
                        "operator": "equals",
                        "value": 9,
                        "description": "Lowest grade is 9",
                    },
                    {
                        "condition_id": "high",
                        "semantic_dimension": "school.highest_grade",
                        "subject": "highest_grade",
                        "operator": "equals",
                        "value": 12,
                        "description": "Highest grade is 12",
                    },
                ],
                "inclusion_logic": {
                    "operator": "all_of",
                    "condition_refs": ["lunch", "low", "high"],
                    "groups": [],
                },
            },
            "metrics": [],
            "output_expectation": {"fields": []},
        }
        schema = {
            "tables": [
                {
                    "name": "frpm",
                    "columns": [
                        {"name": "NSLP Provision Status", "semantic_name": None},
                        {"name": "Low Grade", "semantic_name": None},
                        {"name": "High Grade", "semantic_name": None},
                    ],
                    "question_value_matches": [
                        {"column": "NSLP Provision Status", "value": "Lunch Provision 2"}
                    ],
                },
                {
                    "name": "schools",
                    "columns": [{"name": "GSoffered", "semantic_name": "grade span offered"}],
                    "question_value_matches": [],
                },
            ]
        }
        sif = {
            "query_blocks": [{
                "block_id": "main",
                "sources": [
                    {"kind": "table", "name": "frpm", "alias": "f"},
                    {"kind": "table", "name": "schools", "alias": "s"},
                ],
                "projection": [],
                "where": [
                    {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "f", "name": "NSLP Provision Status"},
                        "right": {"kind": "literal", "value": "Provision 2"},
                        "intent_paths": ["/target_population/inclusion_conditions/0"],
                    },
                    {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "s", "name": "GSoffered"},
                        "right": {"kind": "literal", "value": "9-12"},
                        "intent_paths": [
                            "/target_population/inclusion_conditions/1",
                            "/target_population/inclusion_conditions/2",
                        ],
                    },
                ],
            }]
        }
        warnings = validate_sif_coverage(bif, sif, schema)
        self.assertIn("non_exact_question_value_mapping", {w["issue"] for w in warnings})
        self.assertIn("merged_independent_bif_conditions", {w["issue"] for w in warnings})

        repaired, value_repairs = repair_non_exact_question_values(sif, warnings)
        repaired, merge_repairs = repair_merged_independent_conditions(
            repaired, bif, schema, warnings
        )
        where = repaired["query_blocks"][0]["where"]
        self.assertEqual("Lunch Provision 2", where[0]["right"]["value"])
        self.assertEqual(["Low Grade", "High Grade"], [item["left"]["name"] for item in where[1:]])
        self.assertEqual([9, 12], [item["right"]["value"] for item in where[1:]])
        self.assertEqual(1, len(value_repairs))
        self.assertEqual(1, len(merge_repairs))

    def test_detects_separate_columns_implemented_as_set_operation_rows(self) -> None:
        bif = {
            "target_population": {"inclusion_conditions": []},
            "metrics": [],
            "output_expectation": {"fields": [{
                "semantic_ref": "administrator.email",
                "presentation": "separate_columns",
            }]},
        }
        sif = {
            "main_block_id": "email1",
            "query_blocks": [{
                "block_id": "email1", "sources": [], "where": [],
                "projection": [{"expression": {"kind": "column", "name": "AdmEmail1"}}],
                "set_operations": [{"operator": "union", "query_block_ref": "email2"}],
            }, {
                "block_id": "email2", "sources": [], "where": [],
                "projection": [{"expression": {"kind": "column", "name": "AdmEmail2"}}],
                "set_operations": [],
            }],
        }
        issues = {item["issue"] for item in validate_sif_coverage(bif, sif)}
        self.assertIn("output_presentation_mismatch", issues)


if __name__ == "__main__":
    unittest.main()
