from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVISED_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from intent_sql_agent.services.bif_semantic_validator import validate_bif_semantics
from intent_sql_agent.services.contract_validator import validate_contract


class BIFSemanticValidatorTests(unittest.TestCase):
    def test_q87_compressed_bif_is_not_ready_for_sif(self) -> None:
        question = (
            "What are the valid e-mail addresses of the administrator of the school located "
            "in the San Bernardino county, City of San Bernardino City Unified that opened "
            "between 1/1/2009 to 12/31/2010 whose school types are public "
            "Intermediate/Middle Schools and Unified Schools?"
        )
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    {
                        "condition_id": "location",
                        "semantic_dimension": "location",
                        "subject": "location",
                        "operator": "equals",
                        "value_mode": "scalar",
                        "set_relation": "not_applicable",
                        "value": "San Bernardino county, City of San Bernardino City Unified",
                        "description": "County and city location",
                    },
                    {
                        "condition_id": "type",
                        "semantic_dimension": "school.type",
                        "subject": "school type",
                        "operator": "in",
                        "value_mode": "set",
                        "set_relation": "any_of",
                        "value": ["public Intermediate/Middle Schools", "Unified Schools"],
                        "description": "Either school type",
                    },
                ],
                "inclusion_logic": {
                    "operator": "all_of",
                    "condition_refs": ["location", "type"],
                    "groups": [],
                },
                "exclusion_conditions": [],
                "exclusion_logic": {"operator": "all_of", "condition_refs": [], "groups": []},
            },
            "output_expectation": {
                "fields": [{
                    "label": "Valid e-mail addresses",
                    "value_multiplicity": "single",
                    "coverage": "one",
                    "presentation": "single_column",
                }]
            },
        }
        issues = {item["issue"] for item in validate_bif_semantics(question, bif)}
        self.assertIn("compound_condition_not_atomic", issues)
        self.assertIn("question_logic_mismatch", issues)
        self.assertIn("output_multiplicity_mismatch", issues)

    def test_q87_atomic_bif_has_no_semantic_findings(self) -> None:
        question = "List all administrator e-mail addresses for matching schools."
        conditions = [
            _condition("county", "geography.county", "County", "San Bernardino"),
            _condition("city", "geography.city", "City", "San Bernardino"),
            _condition("level", "school.educational_level", "School level", "Public Intermediate/Middle School"),
            _condition("authority", "organization.authority_type", "Authority type", "Unified School District"),
        ]
        bif = {
            "target_population": {
                "inclusion_conditions": conditions,
                "inclusion_logic": {
                    "operator": "all_of",
                    "condition_refs": [item["condition_id"] for item in conditions],
                    "groups": [],
                },
                "exclusion_conditions": [],
                "exclusion_logic": {"operator": "all_of", "condition_refs": [], "groups": []},
            },
            "output_expectation": {"fields": [{
                "label": "Administrator e-mail addresses",
                "value_multiplicity": "multiple",
                "coverage": "all_available",
                "presentation": "separate_columns",
            }]},
        }
        self.assertEqual([], validate_bif_semantics(question, bif))

    def test_v11_example_satisfies_json_schema(self) -> None:
        schema = json.loads((REVISED_ROOT / "spec/business_intent_frame.schema.json").read_text(encoding="utf-8"))
        example = json.loads((REVISED_ROOT / "spec/examples/bird_0041.business_intent.json").read_text(encoding="utf-8"))
        validate_contract(example, schema, "BIF v1.1 example")

    def test_city_dimension_with_district_subject_requires_role_clarification(self) -> None:
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    _condition(
                        "place", "geography.city", "school_district",
                        "San Bernardino City Unified",
                    )
                ],
                "inclusion_logic": {"operator": "all_of", "condition_refs": ["place"], "groups": []},
                "exclusion_conditions": [],
                "exclusion_logic": {"operator": "none_of", "condition_refs": [], "groups": []},
            },
            "output_expectation": {"fields": []},
        }
        issues = {item["issue"] for item in validate_bif_semantics("schools in the city", bif)}
        self.assertIn("location_role_mismatch", issues)

    def test_user_confirmed_city_role_is_not_asked_again(self) -> None:
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    _condition(
                        "place", "geography.city", "city_name",
                        "San Bernardino City Unified",
                    )
                ],
                "inclusion_logic": {"operator": "all_of", "condition_refs": ["place"], "groups": []},
                "exclusion_conditions": [],
                "exclusion_logic": {"operator": "none_of", "condition_refs": [], "groups": []},
            },
            "output_expectation": {"fields": []},
            "field_states": [{
                "path": "/target_population/inclusion_conditions/0",
                "state": "user_confirmed",
                "material": True,
                "note": "The user confirmed this is the city name.",
            }],
        }
        issues = {item["issue"] for item in validate_bif_semantics("schools in the city", bif)}
        self.assertNotIn("location_role_mismatch", issues)


def _condition(condition_id: str, dimension: str, subject: str, value: str) -> dict:
    return {
        "condition_id": condition_id,
        "semantic_dimension": dimension,
        "subject": subject,
        "operator": "equals",
        "value_mode": "scalar",
        "set_relation": "not_applicable",
        "value": value,
        "description": f"{subject} equals {value}",
    }


if __name__ == "__main__":
    unittest.main()
