from __future__ import annotations

import unittest

from intent_sql_agent.services.bif_logic_repair import (
    apply_structured_location_role_response,
    apply_structured_logic_response,
    apply_structured_output_presentation_response,
)


class BIFLogicRepairTests(unittest.TestCase):
    def test_splits_confirmed_simultaneous_set_into_atomic_conditions(self) -> None:
        bif = {
            "lifecycle": {"state": "draft", "revision": 1},
            "readiness": {"state": "needs_confirmation"},
            "target_population": {
                "inclusion_conditions": [{
                    "condition_id": "c4", "semantic_dimension": "school.type",
                    "subject": "school type", "operator": "in", "value_mode": "set",
                    "set_relation": "any_of",
                    "value": ["Intermediate/Middle Schools", "Unified Schools"],
                    "description": "Either type",
                }],
                "inclusion_logic": {"operator": "all_of", "condition_refs": ["c4"], "groups": []},
            },
        }
        clarification = {"clarification_request": {
            "affected_paths": ["/target_population/inclusion_conditions/0"],
            "options": [
                {"option_id": "simultaneous", "label": "Simultaneous conditions on separate dimensions"},
                {"option_id": "alternative", "label": "Alternatives"},
            ],
        }}
        repaired, repairs = apply_structured_logic_response(
            bif,
            clarification,
            {"action": "select", "selected_option_ids": ["simultaneous"]},
        )
        self.assertIsNotNone(repaired)
        conditions = repaired["target_population"]["inclusion_conditions"]
        self.assertEqual(["c4_1", "c4_2"], [item["condition_id"] for item in conditions])
        self.assertTrue(all(item["operator"] == "equals" for item in conditions))
        self.assertEqual(
            ["c4_1", "c4_2"],
            repaired["target_population"]["inclusion_logic"]["condition_refs"],
        )
        self.assertEqual("ready", repaired["readiness"]["state"])
        self.assertEqual(1, len(repairs))

    def test_simultaneous_choice_replaces_single_any_of_member_with_all_of(self) -> None:
        bif = {
            "lifecycle": {"state": "draft", "revision": 1},
            "readiness": {"state": "needs_confirmation"},
            "target_population": {
                "inclusion_conditions": [{
                    "condition_id": "c4", "semantic_dimension": "school.type",
                    "subject": "school type", "operator": "in", "value_mode": "set",
                    "set_relation": "any_of", "value": ["Middle", "Unified"],
                    "description": "Either type",
                }],
                "inclusion_logic": {
                    "operator": "all_of", "condition_refs": [], "groups": [{
                        "operator": "any_of", "condition_refs": ["c4"], "groups": [],
                    }],
                },
            },
        }
        clarification = {"clarification_request": {
            "affected_paths": ["/target_population/inclusion_conditions/0"],
            "options": [
                {"option_id": "and", "label": "Both types simultaneously"},
                {"option_id": "or", "label": "Either type"},
            ],
        }}
        repaired, _ = apply_structured_logic_response(
            bif, clarification,
            {"action": "select", "selected_option_ids": ["and"]},
        )
        group = repaired["target_population"]["inclusion_logic"]["groups"][0]
        self.assertEqual("all_of", group["operator"])
        self.assertEqual(["c4_1", "c4_2"], group["condition_refs"])

    def test_applies_separate_columns_output_presentation_without_model(self) -> None:
        bif = {
            "lifecycle": {"state": "draft", "revision": 1},
            "readiness": {"state": "needs_confirmation"},
            "output_expectation": {"fields": [{"presentation": "unresolved"}]},
        }
        clarification = {"clarification_request": {
            "affected_paths": ["/output_expectation/fields/0/presentation"],
            "options": [
                {"option_id": "columns", "label": "Show values side-by-side in separate columns"},
                {"option_id": "rows", "label": "Show one value per repeated row"},
            ],
        }}
        repaired, repairs = apply_structured_output_presentation_response(
            bif, clarification,
            {"action": "select", "selected_option_ids": ["columns"]},
        )
        self.assertEqual(
            "separate_columns",
            repaired["output_expectation"]["fields"][0]["presentation"],
        )
        self.assertEqual(1, len(repairs))

    def test_city_role_removes_district_suffix_from_city_value(self) -> None:
        bif = {
            "lifecycle": {"state": "draft", "revision": 1},
            "readiness": {"state": "needs_confirmation"},
            "target_population": {"inclusion_conditions": [{
                "semantic_dimension": "geography.city", "subject": "school_district",
                "value": "San Bernardino City Unified",
            }]},
        }
        clarification = {"clarification_request": {
            "affected_paths": ["/target_population/inclusion_conditions/0"],
            "options": [
                {"option_id": "opt_city_name_exact", "label": "Yes, use the city interpretation"},
                {"option_id": "opt_city_name_district", "label": "No, use the district interpretation"},
            ],
        }}
        repaired, repairs = apply_structured_location_role_response(
            bif, clarification,
            {"action": "select", "selected_option_ids": ["opt_city_name_exact"]},
        )
        condition = repaired["target_population"]["inclusion_conditions"][0]
        self.assertEqual("city_name", condition["subject"])
        self.assertEqual("San Bernardino", condition["value"])
        self.assertEqual(1, len(repairs))

    def test_generic_yes_confirmation_does_not_change_location_role(self) -> None:
        bif = {
            "target_population": {"inclusion_conditions": [{
                "semantic_dimension": "school.administration",
                "subject": "school_administration",
                "value": "Fresno County Office of Education",
            }]},
        }
        clarification = {"clarification_request": {
            "affected_paths": ["/target_population/inclusion_conditions/0/value"],
            "options": [
                {"option_id": "yes", "label": "Yes, the data labeling is accurate."},
                {"option_id": "no", "label": "No, the data labeling is inaccurate."},
            ],
        }}
        repaired, repairs = apply_structured_location_role_response(
            bif,
            clarification,
            {"action": "select", "selected_option_ids": ["yes"]},
        )
        self.assertIsNone(repaired)
        self.assertEqual([], repairs)


if __name__ == "__main__":
    unittest.main()
