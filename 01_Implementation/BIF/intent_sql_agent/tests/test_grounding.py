import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVISED_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from intent_sql_agent.services.grounding_validator import validate_literal_domains
from intent_sql_agent.services.schema_catalog import load_sqlite_schema
from intent_sql_agent.services.source_ambiguity import detect_source_population_ambiguities
from intent_sql_agent.services.sif_repair import (
    apply_source_population_selection,
    canonicalize_source_population_selection,
    enforce_locked_population_sources,
    missing_required_sources,
    repair_missing_column_joins,
    repair_missing_output_traceability,
    repair_missing_population_traceability,
    repair_unrequested_supporting_projections,
    repair_unrequested_with_ties,
    repair_missing_query_block_outputs,
)


class GroundingTest(unittest.TestCase):
    def test_user_facing_source_option_is_canonicalized_to_candidate_id(self) -> None:
        response = {"action": "select", "selected_option_ids": ["OPTION_1"]}
        request = {"options": [{
            "option_id": "OPTION_1",
            "label": "the annual program and eligibility reporting records",
        }]}
        ambiguities = [{
            "ambiguity_id": "source_1",
            "candidates": [{
                "candidate_id": "candidate_1",
                "table": "frpm",
                "user_facing_definition": (
                    "the annual program and eligibility reporting records"
                ),
            }],
        }]
        canonical = canonicalize_source_population_selection(
            response, request, ambiguities, ambiguity_id="source_1"
        )
        self.assertEqual(["candidate_1"], canonical["selected_option_ids"])

    def test_scalar_limit_disables_unrequested_with_ties(self) -> None:
        sif = {
            "query_blocks": [{
                "block_id": "main",
                "limit": {"count": 1, "offset": 0, "with_ties": True, "intent_paths": []},
            }]
        }
        bif = {"output_expectation": {"shape": "scalar", "limit": 1}}
        repaired, repairs = repair_unrequested_with_ties(
            sif,
            bif,
            [{"issue": "unsupported_sif_feature", "error": "WITH TIES requires model fallback"}],
        )
        self.assertFalse(repaired["query_blocks"][0]["limit"]["with_ties"])
        self.assertEqual("disable_unrequested_with_ties", repairs[0]["operation"])

    @classmethod
    def setUpClass(cls) -> None:
        cls.database = (
            REVISED_ROOT
            / "data/bird_dev/dev_databases/california_schools/california_schools.sqlite"
        )

    def test_catalog_includes_descriptions_and_question_value_matches(self) -> None:
        question = (
            "Please list the zip code of all the charter schools in "
            "Fresno County Office of Education."
        )
        catalog = load_sqlite_schema(self.database, question=question)
        tables = {table["name"]: table for table in catalog["tables"]}
        school_columns = {column["name"]: column for column in tables["schools"]["columns"]}
        self.assertIn("00 - County Office of Education", school_columns["DOC"]["value_description"])
        self.assertIn(
            {"column": "District Name", "value": "Fresno County Office of Education", "match": "value_in_question"},
            tables["frpm"]["question_value_matches"],
        )

    def test_catalog_prefers_longest_overlapping_question_value(self) -> None:
        catalog = load_sqlite_schema(
            self.database,
            question="Find schools with Lunch Provision 2.",
        )
        frpm = next(table for table in catalog["tables"] if table["name"] == "frpm")
        values = {
            item["value"]
            for item in frpm["question_value_matches"]
            if item["column"] == "NSLP Provision Status"
        }
        self.assertIn("Lunch Provision 2", values)
        self.assertNotIn("Provision 2", values)

    def test_literal_domain_validator_rejects_natural_language_in_doc_code(self) -> None:
        sif = {
            "query_blocks": [
                {
                    "block_id": "main",
                    "sources": [
                        {"source_id": "schools", "kind": "table", "name": "schools", "alias": "s"}
                    ],
                    "joins": [],
                    "having": [],
                    "where": [
                        {
                            "kind": "binary",
                            "operator": "equals",
                            "left": {"kind": "column", "source": "s", "name": "DOC"},
                            "right": {
                                "kind": "literal",
                                "value": "Fresno County Office of Education",
                            },
                        }
                    ],
                }
            ]
        }
        warnings = validate_literal_domains(self.database, sif)
        self.assertEqual(1, len(warnings))
        self.assertEqual("literal_not_in_column_domain", warnings[0]["issue"])

    def test_detects_competing_school_record_populations(self) -> None:
        question = (
            "Please list the zip code of all the charter schools in "
            "Fresno County Office of Education."
        )
        catalog = load_sqlite_schema(self.database, question=question)
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    {
                        "condition_id": "1",
                        "subject": "school_type",
                        "value": "charter",
                        "description": "School must be a charter school",
                    },
                    {
                        "condition_id": "2",
                        "subject": "administrative_unit",
                        "value": "Fresno County Office of Education",
                        "description": "School must be administered by Fresno County Office of Education",
                    },
                ]
            }
        }
        sif = {
            "query_blocks": [
                {
                    "sources": [
                        {"kind": "table", "name": "schools"}
                    ]
                }
            ]
        }
        ambiguities = detect_source_population_ambiguities(bif, sif, catalog)
        self.assertEqual(1, len(ambiguities))
        candidate_tables = {item["table"] for item in ambiguities[0]["candidates"]}
        self.assertIn("schools", candidate_tables)
        self.assertIn("frpm", candidate_tables)
        candidates = {item["table"]: item for item in ambiguities[0]["candidates"]}
        self.assertEqual(
            "Charter School (Y/N)",
            candidates["frpm"]["condition_mappings"][0]["column"],
        )
        self.assertEqual(
            "Charter",
            candidates["schools"]["condition_mappings"][0]["column"],
        )
        self.assertIn("annual program", candidates["frpm"]["user_facing_definition"])
        self.assertIn("master school registry", candidates["schools"]["user_facing_definition"])

    def test_rejects_false_source_population_when_one_table_cannot_map_lunch_provision(self) -> None:
        question = (
            "What is the city location of the high school level school with Lunch Provision 2 "
            "whose lowest grade is 9 and the highest grade is 12 in the county of Merced?"
        )
        catalog = load_sqlite_schema(self.database, question=question)
        bif = {
            "target_population": {
                "inclusion_conditions": [
                    {"condition_id": "1", "subject": "lowest_grade", "value": 9, "description": "lowest grade is 9"},
                    {"condition_id": "2", "subject": "highest_grade", "value": 12, "description": "highest grade is 12"},
                    {"condition_id": "3", "subject": "lunch_provision", "value": "Lunch Provision 2", "description": "Lunch Provision 2"},
                    {"condition_id": "4", "subject": "county", "value": "Merced", "description": "located in Merced county"},
                ]
            }
        }
        sif = {"query_blocks": [{"sources": [{"kind": "table", "name": "frpm"}]}]}
        self.assertEqual([], detect_source_population_ambiguities(bif, sif, catalog))

    def test_validator_rejects_projection_column_missing_from_source(self) -> None:
        sif = {
            "query_blocks": [
                {
                    "block_id": "main",
                    "sources": [
                        {"source_id": "frpm", "kind": "table", "name": "frpm"}
                    ],
                    "joins": [],
                    "where": [],
                    "having": [],
                    "group_by": [],
                    "derived_fields": [],
                    "order_by": [],
                    "windows": [],
                    "projection": [
                        {
                            "expression": {
                                "kind": "column",
                                "source": "frpm",
                                "name": "Zip",
                                "intent_paths": ["/metrics/0"],
                            }
                        }
                    ],
                }
            ]
        }
        warnings = validate_literal_domains(self.database, sif)
        self.assertIn("column_not_in_source", {item["issue"] for item in warnings})

    def test_missing_output_column_adds_fk_join_without_replacing_population_source(self) -> None:
        catalog = load_sqlite_schema(
            self.database,
            question="List charter school zip codes in Fresno County Office of Education",
        )
        sif = {
            "query_blocks": [{
                "block_id": "main",
                "sources": [{
                    "source_id": "frpm", "kind": "table", "name": "frpm",
                    "block_ref": None, "alias": "f", "intent_paths": ["/target_population/primary_entity"],
                }],
                "joins": [],
                "projection": [{
                    "expression": {
                        "kind": "column", "source": "f", "name": "Zip",
                        "intent_paths": ["/metrics/0"],
                    }
                }],
            }]
        }
        warnings = [{
            "block_id": "main", "source": "f", "table": "frpm", "column": "Zip",
            "issue": "column_not_in_source", "intent_paths": ["/metrics/0"],
        }]
        repaired, repairs = repair_missing_column_joins(sif, catalog, warnings)
        block = repaired["query_blocks"][0]
        self.assertEqual({"frpm", "schools"}, {item["name"] for item in block["sources"]})
        self.assertEqual(1, len(block["joins"]))
        self.assertEqual("s", block["projection"][0]["expression"]["source"])
        self.assertEqual(1, len(repairs))
        self.assertEqual(set(), missing_required_sources(repaired, {"frpm"}))

    def test_query_block_output_is_propagated_across_set_operands(self) -> None:
        catalog = load_sqlite_schema(self.database, question="administrator email addresses")
        base_projection = [{
            "output_id": "cds", "label": "CDSCode", "intent_paths": ["/target_population"],
            "expression": {"kind": "column", "source": "s", "name": "CDSCode", "intent_paths": ["/target_population"]},
        }]
        operand_projection = [{
            "output_id": "cds", "label": "CDSCode", "intent_paths": ["/target_population"],
            "expression": {"kind": "column", "source": "u", "name": "CDSCode", "intent_paths": ["/target_population"]},
        }]
        sif = {"query_blocks": [
            {
                "block_id": "filtered", "sources": [{"source_id": "schools", "kind": "table", "name": "schools", "alias": "s"}],
                "joins": [], "where": [], "having": [], "group_by": [], "derived_fields": [],
                "order_by": [], "windows": [], "projection": base_projection,
                "set_operations": [{"operator": "union", "query_block_ref": "unified"}],
            },
            {
                "block_id": "unified", "sources": [{"source_id": "schools_u", "kind": "table", "name": "schools", "alias": "u"}],
                "joins": [], "where": [], "having": [], "group_by": [], "derived_fields": [],
                "order_by": [], "windows": [], "projection": operand_projection, "set_operations": [],
            },
            {
                "block_id": "main", "sources": [{"source_id": "filtered", "kind": "query_block", "block_ref": "filtered", "alias": "fs"}],
                "joins": [], "where": [], "having": [], "group_by": [], "derived_fields": [],
                "order_by": [], "windows": [], "projection": [{
                    "expression": {"kind": "column", "source": "fs", "name": "AdmEmail1", "intent_paths": ["/metrics/0"]},
                }],
            },
        ]}
        warnings = validate_literal_domains(self.database, sif)
        warning = next(item for item in warnings if item.get("column") == "AdmEmail1")
        self.assertEqual("column_not_in_source", warning["issue"])
        self.assertEqual("query_block", warning["source_kind"])

        repaired, repairs = repair_missing_query_block_outputs(sif, catalog, warnings)
        by_id = {block["block_id"]: block for block in repaired["query_blocks"]}
        for block_id in ("filtered", "unified"):
            self.assertEqual(
                ["CDSCode", "AdmEmail1"],
                [item.get("label") for item in by_id[block_id]["projection"]],
            )
        self.assertEqual([], validate_literal_domains(self.database, repaired))
        self.assertEqual(2, len(repairs))

    def test_missing_output_path_is_attached_without_model_regrounding(self) -> None:
        bif = {"output_expectation": {"fields": [{
            "label": "Administrator e-mail address", "semantic_ref": "administrator.email",
        }]}}
        sif = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main", "projection": [{
                    "label": "Administrator Email 1", "intent_paths": ["/metrics/0"],
                    "expression": {"kind": "column", "source": "s", "name": "AdmEmail1", "intent_paths": ["/metrics/0"]},
                }],
                "set_operations": [{"query_block_ref": "email2"}],
            }, {
                "block_id": "email2", "projection": [{
                    "label": "Administrator Email 2", "intent_paths": ["/metrics/0"],
                    "expression": {"kind": "column", "source": "s", "name": "AdmEmail2", "intent_paths": ["/metrics/0"]},
                }], "set_operations": [],
            }],
        }
        repaired, repairs = repair_missing_output_traceability(
            sif, bif, [{
                "issue": "missing_bif_intent_path", "role": "output",
                "intent_path": "/output_expectation/fields/0",
            }]
        )
        for block in repaired["query_blocks"]:
            self.assertIn(
                "/output_expectation/fields/0", block["projection"][0]["intent_paths"]
            )
        self.assertEqual(1, len(repairs))

    def test_missing_population_path_attaches_to_locked_source(self) -> None:
        sif = {"query_blocks": [{
            "block_id": "main",
            "sources": [
                {"source_id": "sets", "kind": "table", "name": "sets", "alias": "s"},
                {"source_id": "translations", "kind": "table", "name": "set_translations", "alias": "t"},
            ],
        }]}
        repaired, repairs = repair_missing_population_traceability(
            sif,
            [{
                "issue": "missing_bif_intent_path",
                "intent_path": "/target_population/primary_entity",
                "role": "population",
            }],
            {"set_translations"},
        )
        source = repaired["query_blocks"][0]["sources"][1]
        self.assertIn("/target_population/primary_entity", source["intent_paths"])
        self.assertEqual(1, len(repairs))

    def test_unrequested_supporting_identifier_is_removed_from_final_projection(self) -> None:
        bif = {"output_expectation": {
            "include_supporting_values": False,
            "fields": [{
                "label": "Administrator E-mail Address",
                "semantic_ref": "administrator.email",
            }],
        }}
        sif = {"main_block_id": "main", "query_blocks": [{
            "block_id": "main", "set_operations": [], "projection": [
                {
                    "output_id": "cds", "label": "CDSCode",
                    "intent_paths": ["/target_population", "/output_expectation/fields/0"],
                    "expression": {
                        "kind": "column", "source": "s", "name": "CDSCode",
                        "intent_paths": ["/target_population", "/output_expectation/fields/0"],
                    },
                },
                {
                    "output_id": "email1", "label": "Administrator Email 1",
                    "intent_paths": ["/metrics/0", "/output_expectation/fields/0"],
                    "expression": {
                        "kind": "column", "source": "s", "name": "AdmEmail1",
                        "intent_paths": ["/metrics/0", "/output_expectation/fields/0"],
                    },
                },
                {
                    "output_id": "email2", "label": "Administrator Email 2",
                    "intent_paths": ["/metrics/0", "/output_expectation/fields/0"],
                    "expression": {
                        "kind": "column", "source": "s", "name": "AdmEmail2",
                        "intent_paths": ["/metrics/0", "/output_expectation/fields/0"],
                    },
                },
            ],
        }]}
        repaired, repairs = repair_unrequested_supporting_projections(sif, bif)
        labels = [item["label"] for item in repaired["query_blocks"][0]["projection"]]
        self.assertEqual(["Administrator Email 1", "Administrator Email 2"], labels)
        self.assertEqual(1, len(repairs))

    def test_unrequested_scope_grouping_identifier_is_not_an_output_column(self) -> None:
        bif = {"output_expectation": {
            "include_supporting_values": False,
            "fields": [{"label": "Administrator E-mail Address", "semantic_ref": "administrator.email"}],
        }}
        sif = {"main_block_id": "main", "query_blocks": [{
            "block_id": "main", "set_operations": [], "projection": [{
                "output_id": "school_id", "label": "School CDSCode",
                "intent_paths": ["/scope/grouping_dimensions/0"],
                "expression": {
                    "kind": "column", "source": "s", "name": "CDSCode",
                    "intent_paths": ["/scope/grouping_dimensions/0"],
                },
            }, {
                "output_id": "email", "label": "Administrator Email",
                "intent_paths": ["/metrics/metric_valid_admin_email"],
                "expression": {
                    "kind": "column", "source": "s", "name": "AdmEmail1",
                    "intent_paths": ["/metrics/metric_valid_admin_email"],
                },
            }],
        }]}
        repaired, repairs = repair_unrequested_supporting_projections(sif, bif)
        self.assertEqual(
            ["AdmEmail1"],
            [item["expression"]["name"] for item in repaired["query_blocks"][0]["projection"]],
        )
        self.assertEqual(1, len(repairs))

    def test_structured_source_choice_is_applied_without_a6_model(self) -> None:
        question = "Please list the zip code of all the charter schools in Fresno County Office of Education."
        catalog = load_sqlite_schema(self.database, question=question)
        bif = {
            "target_population": {"inclusion_conditions": [
                {"condition_id": "1", "subject": "school_type", "value": "charter", "description": "charter school"},
                {"condition_id": "2", "subject": "administrative_unit", "value": "Fresno County Office of Education", "description": "administered by Fresno County Office of Education"},
            ]}
        }
        sif = {"status": "ready", "unresolved_grounding": [], "query_blocks": [{
            "block_id": "main",
            "sources": [{"source_id": "schools", "kind": "table", "name": "schools", "block_ref": None, "alias": "s", "intent_paths": ["/target_population/primary_entity"]}],
            "where": [{
                "kind": "binary", "operator": "and",
                "left": {"kind": "binary", "operator": "=", "left": {"kind": "column", "source": "s", "name": "Charter", "intent_paths": ["/target_population/inclusion_conditions/0"]}, "right": {"kind": "literal", "value": 1}},
                "right": {"kind": "binary", "operator": "=", "left": {"kind": "column", "source": "s", "name": "District", "intent_paths": ["/target_population/inclusion_conditions/1"]}, "right": {"kind": "literal", "value": "Fresno County Office of Education"}},
            }],
            "projection": [{"expression": {"kind": "column", "source": "s", "name": "Zip", "intent_paths": ["/metrics/0"]}}],
        }]}
        ambiguities = detect_source_population_ambiguities(bif, sif, catalog)
        selected = next(
            candidate["candidate_id"]
            for candidate in ambiguities[0]["candidates"]
            if candidate["table"] == "frpm"
        )
        updated, repairs = apply_source_population_selection(
            sif,
            {"action": "select", "selected_option_ids": [selected]},
            ambiguities,
        )
        self.assertIsNotNone(updated)
        block = updated["query_blocks"][0]
        self.assertEqual("frpm", block["sources"][0]["name"])
        columns = [item["left"]["name"] for item in [block["where"][0]["left"], block["where"][0]["right"]]]
        self.assertEqual(["Charter School (Y/N)", "District Name"], columns)
        self.assertEqual(1, len(repairs))

    def test_structured_source_choice_updates_implicit_alias_references(self) -> None:
        sif = {"status": "ready", "unresolved_grounding": [], "query_blocks": [{
            "block_id": "main",
            "sources": [{"source_id": "schools", "kind": "table", "name": "schools", "alias": None}],
            "where": [{"kind": "binary", "operator": "=", "left": {
                "kind": "column", "source": "schools", "name": "Charter",
                "intent_paths": ["/target_population/inclusion_conditions/0"],
            }, "right": {"kind": "literal", "value": 1}}],
            "projection": [{"expression": {"kind": "column", "source": "schools", "name": "Zip"}}],
        }]}
        ambiguities = [{
            "ambiguity_id": "source_1",
            "candidates": [{
                "candidate_id": "use_frpm", "table": "frpm",
                "condition_mappings": [{
                    "intent_path": "/target_population/inclusion_conditions/0",
                    "column": "Charter School (Y/N)",
                }],
            }],
        }]
        updated, repairs = apply_source_population_selection(
            sif,
            {"action": "select", "selected_option_ids": ["use_frpm"]},
            ambiguities,
        )
        self.assertIsNotNone(updated)
        block = updated["query_blocks"][0]
        self.assertEqual("frpm", block["where"][0]["left"]["source"])
        self.assertEqual("frpm", block["projection"][0]["expression"]["source"])
        self.assertEqual("Charter School (Y/N)", block["where"][0]["left"]["name"])
        self.assertEqual(1, len(repairs))

    def test_population_choice_preserves_output_source_and_adds_fk_join(self) -> None:
        question = "Please list the zip code of all the charter schools in Fresno County Office of Education."
        catalog = load_sqlite_schema(self.database, question=question)
        sif = {"status": "ready", "unresolved_grounding": [], "query_blocks": [{
            "block_id": "main",
            "sources": [{
                "source_id": "schools", "kind": "table", "name": "schools",
                "block_ref": None, "alias": "s",
            }],
            "joins": [],
            "where": [
                {"kind": "binary", "operator": "=", "left": {
                    "kind": "column", "source": "s", "name": "Charter",
                    "intent_paths": ["/target_population/inclusion_conditions/0"],
                }, "right": {"kind": "literal", "value": 1}},
                {"kind": "binary", "operator": "=", "left": {
                    "kind": "column", "source": "s", "name": "District",
                    "intent_paths": ["/target_population/inclusion_conditions/1"],
                }, "right": {"kind": "literal", "value": "Fresno County Office of Education"}},
            ],
            "projection": [{"expression": {
                "kind": "column", "source": "s", "name": "Zip",
                "intent_paths": ["/metrics/0"],
            }}],
            "order_by": [], "derived_fields": [], "group_by": [], "having": [],
            "windows": [], "set_operations": [],
        }]}
        ambiguities = [{"ambiguity_id": "source", "candidates": [{
            "candidate_id": "use_frpm", "table": "frpm", "condition_mappings": [
                {"intent_path": "/target_population/inclusion_conditions/0", "column": "Charter School (Y/N)"},
                {"intent_path": "/target_population/inclusion_conditions/1", "column": "District Name"},
            ],
        }]}]
        updated, repairs = apply_source_population_selection(
            sif,
            {"action": "select", "selected_option_ids": ["use_frpm"]},
            ambiguities,
            catalog,
        )
        block = updated["query_blocks"][0]
        self.assertEqual({"schools", "frpm"}, {source["name"] for source in block["sources"]})
        self.assertEqual("s", block["projection"][0]["expression"]["source"])
        population_alias = block["where"][0]["left"]["source"]
        self.assertNotEqual("s", population_alias)
        self.assertEqual(population_alias, block["where"][1]["left"]["source"])
        self.assertEqual(1, len(block["joins"]))
        self.assertTrue(repairs[0]["preserved_role_source"])

    def test_locked_population_source_is_restored_after_model_removes_it(self) -> None:
        catalog = load_sqlite_schema(
            self.database,
            question="List charter school zip codes in Fresno County Office of Education",
        )
        previous = {"main_block_id": "main", "query_blocks": [{
            "block_id": "main",
            "sources": [{
                "source_id": "frpm", "kind": "table", "name": "frpm",
                "block_ref": None, "alias": "f",
            }],
            "joins": [],
            "where": [{
                "kind": "binary", "operator": "=",
                "left": {
                    "kind": "column", "source": "f", "name": "Charter School (Y/N)",
                    "intent_paths": ["/target_population/inclusion_conditions/0"],
                },
                "right": {"kind": "literal", "value": 1},
                "intent_paths": ["/target_population/inclusion_conditions/0"],
            }],
            "projection": [],
        }]}
        candidate = {"main_block_id": "main", "query_blocks": [{
            "block_id": "main",
            "sources": [{
                "source_id": "schools", "kind": "table", "name": "schools",
                "block_ref": None, "alias": "s",
            }],
            "joins": [], "where": [],
            "projection": [{"expression": {
                "kind": "column", "source": "s", "name": "Zip",
                "intent_paths": ["/metrics/0"],
            }}],
        }]}
        restored, repairs = enforce_locked_population_sources(
            previous, candidate, {"frpm"}, catalog
        )
        block = restored["query_blocks"][0]
        self.assertEqual({"frpm", "schools"}, {s["name"] for s in block["sources"]})
        self.assertEqual(1, len(block["joins"]))
        self.assertEqual(1, len(block["where"]))
        self.assertEqual(set(), missing_required_sources(restored, {"frpm"}))
        self.assertEqual("restore_locked_population_source", repairs[0]["operation"])

    def test_query_block_output_id_counts_as_exposed_column(self) -> None:
        sif = {"query_blocks": [
            {
                "block_id": "producer", "sources": [], "joins": [], "where": [],
                "projection": [{
                    "output_id": "balance_start", "label": "account_balance_start",
                    "expression": {"kind": "function", "name": "sum", "args": []},
                }],
            },
            {
                "block_id": "consumer", "sources": [{
                    "source_id": "producer", "kind": "query_block",
                    "block_ref": "producer", "alias": "p",
                }],
                "joins": [], "where": [],
                "projection": [{"expression": {
                    "kind": "column", "source": "p", "name": "balance_start",
                    "intent_paths": ["/metrics/0"],
                }}],
            },
        ]}
        self.assertEqual([], validate_literal_domains(self.database, sif))


if __name__ == "__main__":
    unittest.main()
