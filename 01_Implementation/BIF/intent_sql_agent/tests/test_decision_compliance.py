from __future__ import annotations

import unittest

from intent_sql_agent.services.decision_compliance import (
    validate_decision_compliance,
    validate_unrequested_bif_outputs,
)


class DecisionComplianceTests(unittest.TestCase):
    def test_selected_location_effect_must_match_materialized_bif(self) -> None:
        ledger = [{
            "decision_id": "D1",
            "issue": "LOCATION_ROLE",
            "affected_paths": ["/target_population"],
            "choice_domain": ["City", "District"],
            "canonical_effect": {
                "value": {"canonical_values": ["city"]},
            },
        }]
        frame = {"target_population": {"description": "Fresno district schools"}}
        findings = validate_decision_compliance(frame, ledger, layer="BIF")
        self.assertEqual("decision_effect_mismatch", findings[0]["kind"])

    def test_selected_location_effect_accepts_matching_materialized_bif(self) -> None:
        ledger = [{
            "decision_id": "D1",
            "issue": "LOCATION_ROLE",
            "affected_paths": ["/target_population"],
            "choice_domain": ["City", "District"],
            "canonical_effect": {
                "value": {"canonical_values": ["city"]},
            },
        }]
        frame = {"target_population": {"description": "Schools in Fresno city"}}
        self.assertEqual([], validate_decision_compliance(frame, ledger, layer="BIF"))

    def test_unrequested_identifier_output_is_rejected(self) -> None:
        bif = {
            "output_expectation": {
                "fields": [{"label": "School ID"}, {"label": "School name"}]
            }
        }
        findings = validate_unrequested_bif_outputs(
            "Show the school names", bif
        )
        self.assertEqual(["/output_expectation/fields/0"], [f["path"] for f in findings])

    def test_logic_effect_is_checked_against_population_logic_container(self) -> None:
        ledger = [{
            "decision_id": "D2",
            "issue": "LOGIC_RELATION",
            "affected_paths": ["/target_population/inclusion_conditions/3"],
            "choice_domain": ["Simultaneous", "Alternatives"],
            "canonical_effect": {
                "value": {"canonical_values": ["all_of"]},
            },
        }]
        frame = {
            "target_population": {
                "inclusion_conditions": [
                    {"condition_id": "c1", "value": "A"},
                    {"condition_id": "c2", "value": "B"},
                ],
                "inclusion_logic": {
                    "operator": "all_of",
                    "condition_refs": ["c1", "c2"],
                    "groups": [],
                },
            }
        }
        self.assertEqual([], validate_decision_compliance(frame, ledger, layer="BIF"))


if __name__ == "__main__":
    unittest.main()
