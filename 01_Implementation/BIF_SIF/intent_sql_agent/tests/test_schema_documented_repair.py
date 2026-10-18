from __future__ import annotations

import unittest

from intent_sql_agent.services.schema_documented_repair import repair_schema_documented_grounding


class SchemaDocumentedRepairTests(unittest.TestCase):
    def test_adds_distinct_codebook_predicates_and_plural_sibling_output(self) -> None:
        bif = {
            "target_population": {"inclusion_conditions": [{
                "value": ["public Intermediate/Middle Schools", "Unified Schools"]
            }]},
            "metrics": [{"name": "contact values"}],
            "output_expectation": {"fields": [{
                "value_multiplicity": "multiple",
                "coverage": "all_available",
            }]},
        }
        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOC", "value_description": "62 - Intermediate/Middle Schools (Public)\n65 - K-12 Schools (Public)"},
            {"name": "DOC", "value_description": "54 - Unified School District"},
            {"name": "AdmEmail1", "value_description": None},
            {"name": "AdmEmail2", "value_description": None},
        ]}]}
        sif = {"query_blocks": [{
            "sources": [{"kind": "table", "name": "schools", "source_id": "schools", "alias": "s"}],
            "where": [{
                "kind": "binary", "operator": "IN",
                "left": {"kind": "column", "source": "s", "name": "SOC", "intent_paths": ["/target_population/inclusion_conditions/0"]},
                "right": {"kind": "literal", "value": ["62", "54"]},
            }],
            "projection": [{
                "output_id": "email", "label": "Email", "intent_paths": ["/metrics/0"],
                "expression": {"kind": "column", "source": "s", "name": "AdmEmail1", "intent_paths": ["/metrics/0"]},
            }],
        }]}
        repaired, repairs = repair_schema_documented_grounding(sif, bif, catalog)
        block = repaired["query_blocks"][0]
        self.assertEqual(["62"], block["where"][0]["right"]["value"])
        self.assertEqual("DOC", block["where"][1]["left"]["name"])
        self.assertEqual("54", block["where"][1]["right"]["value"])
        self.assertEqual(["AdmEmail1", "AdmEmail2"], [item["expression"]["name"] for item in block["projection"]])
        self.assertEqual(3, len(repairs))

    def test_exact_codebook_mapping_removes_superseded_semantic_column(self) -> None:
        bif = {"target_population": {"inclusion_conditions": [{
            "value": "Unified Schools",
        }]}, "metrics": [], "output_expectation": {"fields": []}}
        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOCType", "value_description": None},
            {"name": "DOC", "value_description": "54 - Unified School District"},
        ]}]}
        sif = {"query_blocks": [{
            "sources": [{"kind": "table", "name": "schools", "source_id": "schools", "alias": "s"}],
            "where": [{
                "kind": "binary", "operator": "=",
                "left": {"kind": "column", "source": "s", "name": "SOCType", "intent_paths": ["/target_population/inclusion_conditions/0"]},
                "right": {"kind": "literal", "value": "K-12 Schools (Public)"},
                "intent_paths": ["/target_population/inclusion_conditions/0"],
            }],
            "projection": [],
        }]}
        repaired, repairs = repair_schema_documented_grounding(sif, bif, catalog)
        predicates = repaired["query_blocks"][0]["where"]
        self.assertEqual(["DOC"], [item["left"]["name"] for item in predicates])
        self.assertEqual(["54"], [item["right"]["value"] for item in predicates])
        self.assertIn("remove_superseded_schema_mapping", {item["operation"] for item in repairs})

    def test_stable_condition_ids_preserve_distinct_codebook_dimensions(self) -> None:
        bif = {"target_population": {"inclusion_conditions": [{
            "condition_id": "school_type", "value": "Intermediate/Middle Schools",
        }, {
            "condition_id": "district_type", "value": "Unified Schools",
        }]}, "metrics": [], "output_expectation": {"fields": []}}
        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOC", "value_description": "62 - Intermediate/Middle Schools (Public)"},
            {"name": "DOC", "value_description": "54 - Unified School District"},
        ]}]}
        paths = [
            "/target_population/inclusion_conditions/school_type",
            "/target_population/inclusion_conditions/district_type",
        ]
        sif = {"query_blocks": [{
            "sources": [{"kind": "table", "name": "schools", "alias": "s"}],
            "where": [{
                "kind": "binary", "operator": "in",
                "left": {"kind": "column", "source": "s", "name": "SOC", "intent_paths": paths},
                "right": {"kind": "literal", "value": ["62", "54"]},
                "intent_paths": paths,
            }],
            "projection": [],
        }]}
        repaired, _ = repair_schema_documented_grounding(sif, bif, catalog)
        where = repaired["query_blocks"][0]["where"]
        self.assertEqual(["SOC", "DOC"], [item["left"]["name"] for item in where])
        self.assertEqual([["62"], "54"], [item["right"]["value"] for item in where])


if __name__ == "__main__":
    unittest.main()
