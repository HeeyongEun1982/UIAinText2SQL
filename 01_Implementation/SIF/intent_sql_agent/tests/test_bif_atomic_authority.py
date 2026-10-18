from __future__ import annotations

import copy
import unittest

from intent_sql_agent.services.bif_atomic_authority import (
    apply_bif_atomic_authority,
    normalize_bif_atomic_authority,
    route_bif_issue_observations,
    validate_bif_atomic_admission,
)


QUESTION = (
    "What are the valid e-mail addresses of the administrator of the school "
    "located in San Bernardino county, City of San Bernardino City Unified "
    "that opened between 1/1/2009 to 12/31/2010 whose school types are public "
    "Intermediate/Middle Schools and Unified Schools?"
)


def _bif() -> dict:
    return {
        "target_population": {
            "primary_entity": "school",
            "entity_description": "Requested schools",
            "inclusion_conditions": [{
                "condition_id": "school_type",
                "semantic_dimension": "school.type",
                "subject": "school type", "operator": "in",
                "value_mode": "set", "set_relation": "any_of",
                "value": ["public Intermediate/Middle Schools", "Unified Schools"],
                "description": "Either school type",
            }],
            "inclusion_logic": {
                "operator": "all_of", "condition_refs": ["school_type"], "groups": [],
            },
            "exclusion_conditions": [],
            "exclusion_logic": {"operator": "none_of", "condition_refs": [], "groups": []},
        },
        "metrics": [{
            "metric_id": "admin_email", "name": "Valid administrator e-mail address",
            "definition": "Non-null and properly formatted administrator e-mail address",
            "measure_kind": "attribute", "unit": None,
        }],
        "calculation": {
            "steps": [{
                "step_id": "retrieve", "operation": "retrieve",
                "description": "Retrieve email", "input_refs": ["admin_email"],
                "output_ref": "admin_email", "method": "direct retrieval",
            }],
            "null_handling": None, "duplicate_handling": "distinct",
        },
        "granularity": {"input_unit": "school", "output_unit": "school", "dimensions": ["school"]},
        "scope": {"grouping_dimensions": [], "selections": []},
        "time_basis": {"mode": "not_applicable", "description": None, "periods": [],
                       "temporal_semantics": "not_applicable", "cumulative": None},
        "comparisons": [],
        "output_expectation": {
            "shape": "detail_list",
            "fields": [{
                "field_id": "school_name", "label": "School Name",
                "semantic_ref": "school.name", "value_multiplicity": "single",
                "coverage": "one", "presentation": "single_column",
            }, {
                "field_id": "email", "label": "Administrator E-mail Addresses",
                "semantic_ref": "admin_email", "value_multiplicity": "multiple",
                "coverage": "all_requested", "presentation": "separate_columns",
            }],
            "ordering": [{"semantic_ref": "school.name", "direction": "ascending"}],
            "limit": None, "include_supporting_values": False,
        },
    }


class BIFAtomicAuthorityTests(unittest.TestCase):
    def test_q87_preserves_atoms_and_removes_unrequested_enrichment(self) -> None:
        normalized, repairs = normalize_bif_atomic_authority(QUESTION, _bif())
        conditions = normalized["target_population"]["inclusion_conditions"]
        self.assertEqual(2, len(conditions))
        self.assertEqual(
            ["public Intermediate/Middle Schools", "Unified Schools"],
            [item["value"] for item in conditions],
        )
        self.assertTrue(all(item["semantic_dimension"] is None for item in conditions))
        self.assertTrue(all(
            item["semantic_dimension_status"] == "deferred_to_sif"
            for item in conditions
        ))
        self.assertEqual(
            ["school_type_1", "school_type_2"],
            normalized["target_population"]["inclusion_logic"]["condition_refs"],
        )
        self.assertEqual(["email"], [
            item["field_id"] for item in normalized["output_expectation"]["fields"]
        ])
        self.assertEqual([], normalized["output_expectation"]["ordering"])
        self.assertEqual([], normalized["calculation"]["steps"])
        self.assertIsNone(normalized["calculation"]["duplicate_handling"])
        self.assertEqual(
            "deferred_to_sif",
            normalized["metrics"][0]["validation_semantics"]["status"],
        )
        self.assertIn(
            "preserve_coordinated_atomic_conditions",
            {item["operation"] for item in repairs},
        )

    def test_a3_cannot_remerge_atoms_or_add_outputs(self) -> None:
        draft, _ = normalize_bif_atomic_authority(QUESTION, _bif())
        candidate = copy.deepcopy(draft)
        candidate["target_population"] = copy.deepcopy(_bif()["target_population"])
        candidate["output_expectation"]["fields"].insert(0, {
            "field_id": "cds", "label": "CDSCode", "semantic_ref": "school.code",
            "value_multiplicity": "single", "coverage": "one",
            "presentation": "single_column",
        })
        admitted, events = apply_bif_atomic_authority(QUESTION, draft, candidate)
        self.assertEqual([], validate_bif_atomic_admission(QUESTION, draft, admitted))
        self.assertEqual(2, len(admitted["target_population"]["inclusion_conditions"]))
        self.assertEqual(["email"], [
            item["field_id"] for item in admitted["output_expectation"]["fields"]
        ])
        self.assertTrue(
            {"restore_authoritative_bif_section", "remove_unrequested_output_field"}
            & {item["operation"] for item in events}
        )

    def test_admission_rejects_direct_inventory_mutation(self) -> None:
        draft, _ = normalize_bif_atomic_authority(QUESTION, _bif())
        malicious = copy.deepcopy(draft)
        malicious["target_population"]["inclusion_conditions"].pop()
        findings = validate_bif_atomic_admission(QUESTION, draft, malicious)
        self.assertIn(
            "atomic_condition_inventory_changed",
            {item["issue"] for item in findings},
        )

    def test_only_condition_scoped_schema_question_is_deferred(self) -> None:
        draft, _ = normalize_bif_atomic_authority(QUESTION, _bif())
        candidates = [{
            "issue_type": "TARGET_POPULATION",
            "subject_ref": "/target_population/inclusion_conditions/0/semantic_dimension",
            "description": "Which database classification dimension is this?",
            "alternatives": ["school type", "district type"],
        }, {
            "issue_type": "LOCATION_ROLE",
            "subject_ref": "/target_population",
            "description": "Does City Unified name a city or an organisation?",
            "alternatives": ["city", "organisation"],
        }]
        accepted, deferred = route_bif_issue_observations(draft, candidates, [])
        self.assertEqual(["LOCATION_ROLE"], [item["issue_type"] for item in accepted])
        self.assertEqual("sif", deferred[0]["target_stage"])
        self.assertEqual(
            "schema_owned_deferred_dimension", deferred[0]["reason"]
        )


if __name__ == "__main__":
    unittest.main()
