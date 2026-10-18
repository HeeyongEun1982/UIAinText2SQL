from __future__ import annotations

import unittest

from intent_sql_agent.services.bif_normalization import normalize_initial_bif


class BIFNormalizationTests(unittest.TestCase):
    def test_deduplicates_repeated_scalar_enum_values(self) -> None:
        bif = {
            "decision_objective": {
                "summary": "Calculate a percentage.",
                "intent_types": ["derive", "filter", "derive"],
            },
            "metrics": [], "calculation": {"steps": []}, "assumptions": [],
            "output_expectation": {"fields": []},
        }
        repaired, repairs = normalize_initial_bif("Calculate a percentage.", bif)
        self.assertEqual(
            ["derive", "filter"], repaired["decision_objective"]["intent_types"]
        )
        self.assertIn("dedupe_scalar_list", {item["operation"] for item in repairs})

    def test_defers_unstated_presentation_and_removes_invented_validation(self) -> None:
        bif = {
            "metrics": [{
                "name": "Valid administrator e-mail address",
                "definition": "Must use standard e-mail format and be active and verified",
            }],
            "calculation": {"steps": [{
                "step_id": "validate", "description": "Verify active e-mail format", "method": "regex",
            }]},
            "assumptions": [{"description": "Addresses are active and verified"}],
            "output_expectation": {"fields": [{
                "value_multiplicity": "multiple", "presentation": "repeated_rows",
            }]},
        }
        repaired, repairs = normalize_initial_bif(
            "What are the valid e-mail addresses of the administrators?", bif
        )
        self.assertEqual(
            "unresolved", repaired["output_expectation"]["fields"][0]["presentation"]
        )
        self.assertEqual([], repaired["calculation"]["steps"])
        self.assertEqual([], repaired["assumptions"])
        self.assertGreaterEqual(len(repairs), 3)

    def test_preserves_user_stated_set_entity_phrase(self) -> None:
        bif = {
            "target_population": {
                "primary_entity": "card",
                "entity_description": "All cards",
            },
            "metrics": [],
            "calculation": {"steps": []},
            "assumptions": [],
            "output_expectation": {"fields": []},
        }
        repaired, repairs = normalize_initial_bif(
            "What percentage of the set of cards that use Chinese Simplified are online only?",
            bif,
        )
        self.assertEqual(
            "set of cards", repaired["target_population"]["primary_entity"]
        )
        self.assertIn(
            "preserve_user_entity_phrase",
            {item["operation"] for item in repairs},
        )

if __name__ == "__main__":
    unittest.main()
