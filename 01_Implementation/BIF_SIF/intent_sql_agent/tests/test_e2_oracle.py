import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.services.e2_oracle import (
    build_e2_oracle_context,
    enforce_oracle_response,
)


class E2OracleTest(unittest.TestCase):
    def test_authoritative_selection_removes_unsolicited_free_text(self) -> None:
        response, changed = enforce_oracle_response(
            {
                "request_id": "r1",
                "mode": "BIF_CLARIFICATION",
                "action": "select",
                "selected_option_ids": ["CONFIRM"],
                "free_text": "Yes, that is what I meant.",
                "confidence": "high",
            },
            mode="BIF_CLARIFICATION",
            request_id="r1",
            oracle_context={"oracle_target_option_ids": ["CONFIRM"]},
        )
        self.assertTrue(changed)
        self.assertIsNone(response["free_text"])

    def test_non_gold_table_cannot_win_from_same_named_columns(self) -> None:
        clarification = {
            "decision": {"decision_id": "source"},
            "clarification_request": {
                "options": [
                    {"option_id": "candidate_1", "label": "Loan application record"},
                    {"option_id": "candidate_2", "label": "Transaction record"},
                ]
            },
        }
        ambiguities = [{
            "candidates": [
                {
                    "candidate_id": "candidate_1",
                    "table": "loan",
                    "condition_mappings": [
                        {"column": "loan_id"}, {"column": "date"}
                    ],
                },
                {
                    "candidate_id": "candidate_2",
                    "table": "trans",
                    "condition_mappings": [
                        {"column": "amount"}, {"column": "date"}
                    ],
                },
            ]
        }]
        context = build_e2_oracle_context(
            "SELECT c.birth_date FROM loan l JOIN client c ON 1=1 "
            "WHERE l.amount = 98832 AND l.date = '1996-01-03'",
            clarification,
            ambiguities,
        )
        self.assertEqual(["candidate_1"], context["oracle_target_option_ids"])

    def test_gold_identifiers_resolve_source_population_option(self) -> None:
        gold = (
            "SELECT T2.Zip FROM frpm AS T1 INNER JOIN schools AS T2 "
            "ON T1.CDSCode = T2.CDSCode WHERE "
            "T1.`District Name` = 'Fresno County Office of Education' "
            "AND T1.`Charter School (Y/N)` = 1"
        )
        clarification = {
            "decision": {"decision_id": "source_population"},
            "clarification_request": {
                "options": [
                    {"option_id": "candidate_1", "label": "annual reporting records"},
                    {"option_id": "candidate_2", "label": "master registry"},
                ]
            },
        }
        ambiguities = [
            {
                "candidates": [
                    {
                        "candidate_id": "candidate_1",
                        "table": "frpm",
                        "condition_mappings": [
                            {"column": "Charter School (Y/N)"},
                            {"column": "District Name"},
                        ],
                    },
                    {
                        "candidate_id": "candidate_2",
                        "table": "schools",
                        "condition_mappings": [
                            {"column": "Charter"},
                            {"column": "District"},
                        ],
                    },
                ]
            }
        ]
        context = build_e2_oracle_context(gold, clarification, ambiguities)
        self.assertEqual(["candidate_1"], context["oracle_target_option_ids"])
        scores = {
            item["option_id"]: item["score"] for item in context["option_resolution_map"]
        }
        self.assertGreater(scores["candidate_1"], scores["candidate_2"])

    def test_source_population_maps_canonical_orchestrator_option_ids_by_order(self) -> None:
        clarification = {
            "decision": {"decision_id": "SIF-SOURCE_POPULATION-1"},
            "clarification_request": {
                "options": [
                    {"option_id": "OPTION_1", "label": "annual reporting records"},
                    {"option_id": "OPTION_2", "label": "master registry"},
                ]
            },
        }
        ambiguities = [{
            "candidates": [
                {"candidate_id": "candidate_1", "table": "annual", "condition_mappings": []},
                {"candidate_id": "candidate_2", "table": "master", "condition_mappings": []},
            ]
        }]
        context = build_e2_oracle_context(
            "SELECT * FROM master", clarification, ambiguities
        )
        self.assertEqual(["OPTION_2"], context["oracle_target_option_ids"])

    def test_wrong_model_selection_is_normalized_to_oracle_target(self) -> None:
        raw = {
            "request_id": "r1",
            "mode": "SIF_CLARIFICATION",
            "action": "select",
            "selected_option_ids": ["candidate_2"],
            "free_text": None,
            "confidence": "high",
        }
        corrected, changed = enforce_oracle_response(
            raw,
            mode="SIF_CLARIFICATION",
            request_id="r1",
            oracle_context={"oracle_target_option_ids": ["candidate_1"]},
        )
        self.assertTrue(changed)
        self.assertEqual(["candidate_1"], corrected["selected_option_ids"])

    def test_no_gap_confirmation_has_deterministic_affirmative_target(self) -> None:
        clarification = {
            "decision": None,
            "clarification_request": {
                "options": [
                    {"option_id": "CONFIRM", "label": "Yes"},
                    {"option_id": "REVISE", "label": "No"},
                ]
            },
        }
        context = build_e2_oracle_context("SELECT 1", clarification)
        self.assertEqual(["CONFIRM"], context["oracle_target_option_ids"])

    def test_explicit_and_connector_selects_simultaneous_business_meaning(self) -> None:
        clarification = {
            "decision": {"decision_id": "logic"},
            "clarification_request": {
                "question": (
                    "Should 'Intermediate/Middle Schools' and 'Unified Schools' be "
                    "simultaneous conditions or alternatives?"
                ),
                "options": [
                    {"option_id": "simultaneous", "label": "Simultaneous conditions on separate dimensions"},
                    {"option_id": "alternative", "label": "Alternatives on a single dimension"},
                ],
            },
        }
        context = build_e2_oracle_context(
            "SELECT 1",
            clarification,
            original_question=(
                "schools whose types are Intermediate/Middle Schools and Unified Schools"
            ),
        )
        self.assertEqual("explicit_question_connector", context["resolution_method"])
        self.assertEqual(["simultaneous"], context["oracle_target_option_ids"])

    def test_explicit_and_connector_understands_both_and_either_labels(self) -> None:
        clarification = {
            "decision": {"decision_id": "logic"},
            "clarification_request": {
                "question": "Should schools be both 'Middle Schools' and 'Unified Schools', or either type?",
                "options": [
                    {"option_id": "option_and", "label": "Schools must be both types simultaneously"},
                    {"option_id": "option_or", "label": "Schools can be either type"},
                ],
            },
        }
        context = build_e2_oracle_context(
            "SELECT 1", clarification,
            original_question="schools whose types are Middle Schools and Unified Schools",
        )
        self.assertEqual("explicit_question_connector", context["resolution_method"])
        self.assertEqual(["option_and"], context["oracle_target_option_ids"])

    def test_q87_gold_boolean_shape_selects_simultaneous_conditions(self) -> None:
        clarification = {
            "decision": {"decision_id": "logic"},
            "clarification_request": {
                "question": (
                    "Should the school type condition require both types "
                    "simultaneously, or allow either type as an alternative?"
                ),
                "options": [
                    {"option_id": "OPTION_1", "label": "Apply the conditions simultaneously"},
                    {"option_id": "OPTION_2", "label": "Treat the values as alternatives"},
                ],
            },
        }
        context = build_e2_oracle_context(
            "SELECT s.AdmEmail1, s.AdmEmail2 FROM schools s "
            "WHERE s.County='San Bernardino' AND s.DOC=54 AND s.SOC=62",
            clarification,
            original_question=(
                "schools whose school types are public Intermediate/Middle Schools "
                "and Unified Schools"
            ),
        )
        self.assertEqual("gold_boolean_shape", context["resolution_method"])
        self.assertEqual(["OPTION_1"], context["oracle_target_option_ids"])

    def test_gold_projection_count_resolves_output_presentation(self) -> None:
        clarification = {
            "decision": {"decision_id": "output_shape"},
            "clarification_request": {
                "question": "How should multiple e-mail values be displayed?",
                "options": [
                    {"option_id": "columns", "label": "Side-by-side in separate columns"},
                    {"option_id": "rows", "label": "One value per repeated row in a single column"},
                ],
            },
        }
        context = build_e2_oracle_context(
            "SELECT s.AdmEmail1, s.AdmEmail2 FROM schools AS s", clarification
        )
        self.assertEqual("gold_output_shape", context["resolution_method"])
        self.assertEqual(["columns"], context["oracle_target_option_ids"])

    def test_gold_location_identifier_resolves_city_vs_district(self) -> None:
        clarification = {
            "decision": {"decision_id": "location"},
            "clarification_request": {
                "options": [
                    {"option_id": "city", "label": "City named San Bernardino"},
                    {"option_id": "district", "label": "District named San Bernardino City Unified"},
                ],
            },
        }
        context = build_e2_oracle_context(
            "SELECT s.Name FROM schools s WHERE s.City = 'San Bernardino'", clarification
        )
        self.assertEqual("gold_column_option_overlap", context["resolution_method"])
        self.assertEqual(["city"], context["oracle_target_option_ids"])


if __name__ == "__main__":
    unittest.main()
