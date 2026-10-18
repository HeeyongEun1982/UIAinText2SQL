from __future__ import annotations

import unittest
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from jsonschema import Draft202012Validator

from intent_sql_agent.services.direct_sif import (
    CONFIRMED_GROUNDING_SCHEMA,
    QUERY_UNDERSTANDING_SCHEMA,
    SQL_SECTION_PLAN_SCHEMA,
    TABLE_LINKING_SCHEMA,
    compile_understanding_obligations,
    normalize_understanding_roles,
    validate_grounded_condition_coverage,
    validate_understanding_coverage,
)
from intent_sql_agent.services.direct_sif_compiler import (
    apply_cqd_sql_semantics,
    compile_direct_sif,
    normalize_direct_sif_bundle,
    validate_direct_sif_bundle,
)
from intent_sql_agent.services.direct_sif_candidate_resolver import (
    resolve_column_candidates,
)
from intent_sql_agent.services.direct_sif_binding_intents import (
    build_binding_intents,
    expand_plural_output_bindings,
)
from intent_sql_agent.services.direct_sif_materializer import (
    compile_understanding_from_cqd,
    materialize_grounding_bundle,
)
from intent_sql_agent.services.semantic_frontend import _ModelAdapter
from intent_sql_agent.pipeline.full_case_runner import FullCaseRunner
from intent_sql_agent.agents.suite import AgentSuite
from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.sql_executor import ReadOnlySQLiteExecutor


ROOT = Path(__file__).resolve().parents[2]


Q2_CQD = {
    "objective": {"action": "list"},
    "populations": [{
        "entity": "charter schools",
        "location": "Fresno County Office of Education",
    }],
    "predicates": {"root": None, "atoms": []},
    "expressions": [], "aggregations": [], "groupings": [],
    "selections": [{"selection_type": "attribute", "attribute": "zip code"}],
    "time_constraints": [], "comparisons": [],
    "outputs": [{"output_type": "list", "content": "zip code"}],
    "scopes": [],
}


Q2_UNDERSTANDING = {
    "normalized_query": (
        "Return the zip code for schools that are charter schools and belong "
        "to Fresno County Office of Education."
    ),
    "primary_subject_anchor": "schools",
    "anchor_reasoning": "Schools are the row-level subject.",
    "targets": ["zip code"],
    "field_phrases": ["zip code", "charter status", "district"],
    "value_phrases": ["charter", "Fresno County Office of Education"],
    "conditions_text": [
        "schools are charter schools",
        "schools belong to Fresno County Office of Education",
    ],
    "scope_text": [],
    "ambiguous_phrases": [],
}


class DirectSIFTests(unittest.TestCase):
    @staticmethod
    def _q2_bundle():
        return {
            "confirmed_grounding": {"confirmed": {
                "terms": [
                    {"physicalName": "Zip", "termId": None, "surface": "zip code", "selectionRule": "SCHEMA_EVIDENCE"},
                    {"physicalName": "Charter School (Y/N)", "termId": None, "surface": "charter status", "selectionRule": "DOCUMENTED_VALUE"},
                    {"physicalName": "District Name", "termId": None, "surface": "district", "selectionRule": "QUESTION_VALUE_MATCH"},
                ],
                "codes": [{
                    "physicalName": "Charter School (Y/N)", "termId": None, "code": 1,
                    "label": "charter", "sourceText": "schools are charter schools",
                }],
                "unresolved": [],
            }},
            "table_linking": {
                "root_anchor": "frpm",
                "selected_tables": [
                    {"table": "frpm", "tier": 1, "role": "root"},
                    {"table": "schools", "tier": 2, "role": "join"},
                ],
                "joins": [{"from": "frpm", "to": "schools", "from_col": "CDSCode", "to_col": "CDSCode"}],
                "column_bindings": {"Zip": "schools", "Charter School (Y/N)": "frpm", "District Name": "frpm"},
                "reasoning": ["Condition columns use frpm and Zip is joined from schools."],
                "diagnostics": {"missing_columns": []},
            },
            "sql_plan": {
                "where_sections": [
                    {"column": "frpm.Charter School (Y/N)", "operator": "EQ", "values": [1], "is_negation": False, "matched_snippet": "schools are charter schools"},
                    {"column": "frpm.District Name", "operator": "EQ", "values": ["Fresno County Office of Education"], "is_negation": False, "matched_snippet": "schools belong to Fresno County Office of Education"},
                ],
                "order_by_section": [],
                "limit_section": {"count": None, "matched_snippet": ""},
                "dedupe_section": {"key_column": None, "policy": None, "matched_snippet": ""},
            },
        }

    def test_provided_stage_formats_are_strict_json_contracts(self) -> None:
        for schema in (
            QUERY_UNDERSTANDING_SCHEMA, CONFIRMED_GROUNDING_SCHEMA,
            TABLE_LINKING_SCHEMA, SQL_SECTION_PLAN_SCHEMA,
        ):
            Draft202012Validator.check_schema(schema)

    def test_understanding_is_projected_without_model_generation(self) -> None:
        class Client:
            def __init__(self):
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                raise AssertionError("CQD projection must not call a model")

        client = Client()
        suite = AgentSuite(client, bif_schema={}, sif_schema={})
        result = suite.a3_direct_sif_understanding(
            case={"question": "q", "evidence": None},
            confirmed_cqd=Q2_CQD,
        )
        self.assertEqual(client.calls, 0)
        self.assertEqual(result["targets"], ["zip code"])
        self.assertIn("charter schools", result["conditions_text"])
        self.assertEqual(suite.direct_sif_understanding_coverage["status"], "complete")

    def test_understanding_projection_is_stable_for_identical_cqd(self) -> None:
        first = compile_understanding_from_cqd(
            original_question="q", confirmed_cqd=Q2_CQD,
        )
        second = compile_understanding_from_cqd(
            original_question="q", confirmed_cqd=copy.deepcopy(Q2_CQD),
        )
        self.assertEqual(first, second)

    def test_grounding_materializer_uses_catalog_join_path_not_sql_text(self) -> None:
        cqd = {
            "populations": [{"entity": "client"}],
            "predicates": {"atoms": [
                {"dimension": "loan amount", "operator": "=", "value": 98832,
                 "applies_to": "loan", "source_text": "98832 USD loan"},
                {"dimension": "loan date", "operator": "=", "value": "1996-01-03",
                 "applies_to": "loan", "source_text": "loan on 1996/1/3"},
            ]},
            "outputs": [{"attribute": "birthday", "entity": "client"}],
        }
        catalog = {"tables": [
            {"name": "client", "columns": [
                {"name": "client_id", "primary_key": True},
                {"name": "birth_date", "description": "birth date"},
            ], "foreign_keys": []},
            {"name": "disp", "columns": [
                {"name": "client_id"}, {"name": "account_id"},
            ], "foreign_keys": [
                {"target_table": "client", "from_column": "client_id", "to_column": "client_id"},
                {"target_table": "account", "from_column": "account_id", "to_column": "account_id"},
            ]},
            {"name": "account", "columns": [{"name": "account_id"}], "foreign_keys": []},
            {"name": "loan", "columns": [
                {"name": "account_id"}, {"name": "amount", "description": "loan amount"},
                {"name": "date", "description": "loan date"},
            ], "foreign_keys": [
                {"target_table": "account", "from_column": "account_id", "to_column": "account_id"},
            ]},
        ]}
        understanding = compile_understanding_from_cqd(
            original_question="birthday for the client with a loan", confirmed_cqd=cqd,
        )
        bundle = materialize_grounding_bundle(
            original_question="birthday for the client with a loan",
            benchmark_evidence=None, confirmed_cqd=cqd,
            understanding=understanding, schema_catalog=catalog,
        )
        sql = compile_direct_sif(understanding=understanding, bundle=bundle)
        self.assertIn('"client"."birth_date"', sql)
        self.assertIn('INNER JOIN "disp"', sql)
        self.assertIn('"loan"."amount" = 98832', sql)
        self.assertNotIn("SELECT disp.client_id", sql)

    def test_structured_field_aliases_materialize_the_same_condition(self) -> None:
        """CQD producer aliases must not change the physical SQL obligation."""
        catalog = {"tables": [{
            "name": "frpm",
            "columns": [
                {"name": "Charter School (Y/N)", "description": "charter school flag"},
            ],
            "foreign_keys": [],
        }]}
        plans = []
        for alias in ("dimension", "attribute", "field"):
            cqd = {
                "populations": [{
                    "entity": "schools",
                    "filter": {
                        alias: "Charter School (Y/N)",
                        "operator": "=", "value": 1,
                        "source_text": "charter schools",
                    },
                }],
                "outputs": [{alias: "Charter School (Y/N)"}],
            }
            understanding = compile_understanding_from_cqd(
                original_question="List the charter school flag for charter schools.",
                confirmed_cqd=cqd,
            )
            bundle = materialize_grounding_bundle(
                original_question="List the charter school flag for charter schools.",
                benchmark_evidence=None,
                confirmed_cqd=cqd,
                understanding=understanding,
                schema_catalog=catalog,
            )
            plans.append(bundle["sql_plan"])

        self.assertEqual(plans[0], plans[1])
        self.assertEqual(plans[1], plans[2])
        where = plans[0]["where_sections"]
        self.assertEqual(len(where), 1)
        self.assertEqual(where[0]["column"], "frpm.Charter School (Y/N)")
        self.assertEqual(where[0]["values"], [1])

    def test_count_then_max_cqd_compiles_as_mode_without_population_synonym_filter(self) -> None:
        cqd = {
            "populations": [{"entity": "schools or educational institutions"}],
            "predicates": {"atoms": [{
                "dimension": "city", "operator": "=", "value": "Adelanto",
                "source_text": "in the city of Adelanto",
            }]},
            "aggregations": [
                {"aggregation_type": "count", "target": "grade span type"},
                {"aggregation_type": "max", "target": "count of grade span type"},
            ],
            "groupings": [{"group_by": "grade span type"}],
            "selections": [{"selection_type": "top", "count": 1,
                            "tie_policy": "return_one"}],
            "outputs": [{"output_type": "grade span type",
                         "description": "the most common grade span type"}],
        }
        catalog = {"tables": [{
            "name": "schools",
            "columns": [{"name": "School"}, {"name": "City"},
                        {"name": "GSserved", "description": "grade span served"}],
            "question_value_matches": [{"column": "City", "value": "Adelanto"}],
            "foreign_keys": [],
        }]}
        understanding = compile_understanding_from_cqd(
            original_question="Most common grade span in Adelanto", confirmed_cqd=cqd,
        )
        bundle = materialize_grounding_bundle(
            original_question="Most common grade span in Adelanto",
            benchmark_evidence=None, confirmed_cqd=cqd,
            understanding=understanding, schema_catalog=catalog,
        )
        bundle = apply_cqd_sql_semantics(bundle=bundle, confirmed_cqd=cqd)
        sql = compile_direct_sif(understanding=understanding, bundle=bundle)
        self.assertNotIn('"schools"."School" = 1', sql)
        self.assertIn('WHERE ("schools"."City" = \'Adelanto\')', sql)
        self.assertIn('GROUP BY "schools"."GSserved"', sql)
        self.assertIn('ORDER BY COUNT("schools"."GSserved") DESC', sql)
        self.assertIn("LIMIT 1", sql)

    def test_semantic_output_type_is_selected_and_output_owner_role_is_scope(self) -> None:
        cqd = {
            "populations": [{"entity": "school"}],
            "predicates": {"atoms": [{
                "dimension": "administrator_role", "operator": "=",
                "value": "administrator", "source_text": "administrator of the school",
            }]},
            "outputs": [{
                "output_type": "valid e-mail addresses", "entity": "administrator",
            }],
        }
        catalog = {"tables": [{
            "name": "schools",
            "columns": [{"name": "AdmEmail1"}, {"name": "AdmEmail2"},
                        {"name": "AdmLName2"}],
            "foreign_keys": [],
        }]}
        understanding = compile_understanding_from_cqd(
            original_question="valid e-mail addresses of the administrator",
            confirmed_cqd=cqd,
        )
        bundle = materialize_grounding_bundle(
            original_question="valid e-mail addresses of the administrator",
            benchmark_evidence=None, confirmed_cqd=cqd,
            understanding=understanding, schema_catalog=catalog,
        )
        selected = bundle["sql_plan"]["select_section"]
        self.assertTrue(selected)
        self.assertIn("AdmEmail", selected[0]["column"])
        self.assertEqual(bundle["sql_plan"]["where_sections"], [])

    def test_population_entity_is_always_an_executable_condition_obligation(self) -> None:
        obligations = compile_understanding_obligations(Q2_CQD)
        charter = next(
            item for item in obligations
            if item["cqd_ref"] == "/populations/0/entity"
        )
        self.assertEqual(charter["kind"], "population_membership")
        self.assertEqual(charter["required_sections"], ["conditions_text"])

    def test_output_owner_relationship_is_not_invented_as_where_predicate(self) -> None:
        cqd = {
            "populations": [{"population": "administrator", "of_entity": "school"}],
            "selections": [{"selection": "email addresses", "of_entity": "administrator"}],
            "outputs": [{"output": "email addresses", "of_entity": "administrator"}],
        }
        obligations = compile_understanding_obligations(cqd)
        by_ref = {item["cqd_ref"]: item for item in obligations}
        self.assertEqual(
            ["scope_text", "conditions_text"],
            by_ref["/populations/0/population"]["required_sections"],
        )
        self.assertEqual(
            ["scope_text", "conditions_text"],
            by_ref["/populations/0/of_entity"]["required_sections"],
        )

    def test_output_owner_population_entity_can_be_scope(self) -> None:
        cqd = {
            "populations": [
                {"entity": "account administrator", "role": "record owner"},
                {"entity": "account", "role": "account"},
            ],
            "outputs": [{"content": "email address of the account administrator"}],
        }
        obligations = compile_understanding_obligations(cqd)
        owner = next(
            item for item in obligations
            if item["cqd_ref"] == "/populations/0/entity"
        )
        self.assertEqual("population_descriptor", owner["kind"])
        self.assertEqual(["scope_text", "conditions_text"], owner["required_sections"])

    def test_composite_population_constraint_can_span_conditions(self) -> None:
        cqd = {"populations": [{
            "entity": "account",
            "categories": [
                {"type_name": "retail", "code": "R"},
                {"type_name": "commercial", "code": "C"},
            ],
        }]}
        understanding = {
            "conditions_text": [
                "account membership", "category retail code R",
                "category commercial code C",
            ],
            "scope_text": [], "targets": [], "field_phrases": [],
        }
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual("complete", report["status"], report)

    def test_structural_population_descriptors_can_be_covered_by_scope(self) -> None:
        cqd = {"populations": [{
            "population": "account owner", "entity_type": "person",
            "role": "owner",
        }]}
        understanding = {
            "targets": [], "field_phrases": [], "conditions_text": [],
            "scope_text": ["account owner is a person with owner role"],
        }
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual("complete", report["status"], report)

    def test_output_owner_condition_is_moved_to_scope_before_grounding(self) -> None:
        cqd = {
            "populations": [{"entity": "administrator", "of_entity": "school"}],
            "predicates": {"atoms": [{"source_text": "school is in London"}]},
        }
        understanding = {
            "conditions_text": ["administrator role exists for the school", "school is in London"],
            "scope_text": [],
        }
        normalized = normalize_understanding_roles(understanding, cqd)
        self.assertEqual(["school is in London"], normalized["conditions_text"])
        self.assertEqual(
            ["administrator role exists for the school"], normalized["scope_text"]
        )

    def test_tautological_output_role_atom_is_not_a_where_obligation(self) -> None:
        cqd = {
            "predicates": {"atoms": [{
                "dimension": "account_owner_role", "operator": "=",
                "value": "account owner", "source_text": "account owner of the record",
            }]},
            "outputs": [{"content": "contact address of the account owner"}],
        }
        obligations = compile_understanding_obligations(cqd)
        self.assertEqual([], [
            item for item in obligations if item["kind"] == "predicate"
        ])
        normalized = normalize_understanding_roles({
            "conditions_text": ["account owner of the record"], "scope_text": [],
        }, cqd)
        self.assertEqual([], normalized["conditions_text"])
        self.assertEqual(["account owner of the record"], normalized["scope_text"])

    def test_restrictive_role_value_remains_a_where_obligation(self) -> None:
        cqd = {
            "predicates": {"atoms": [{
                "dimension": "employee_role", "operator": "=", "value": "manager",
                "source_text": "employees whose role is manager",
            }]},
            "outputs": [{"content": "employee email address"}],
        }
        obligations = compile_understanding_obligations(cqd)
        self.assertEqual(1, len([
            item for item in obligations if item["kind"] == "predicate"
        ]))

    def test_population_role_identity_is_scope_not_where(self) -> None:
        cqd = {
            "populations": [{
                "population": "account administrator",
                "entity_type": "person",
                "role": "account administrator",
            }],
            "predicates": {"atoms": [{
                "dimension": "person.role", "operator": "=",
                "value": "account administrator",
                "source_text": "account administrator",
            }]},
            "outputs": [{"content": "valid contact addresses of account administrators"}],
        }
        obligations = compile_understanding_obligations(cqd)
        self.assertEqual([], [
            item for item in obligations if item["kind"] == "predicate"
        ])
        normalized = normalize_understanding_roles({
            "conditions_text": ["person.role = account administrator"],
            "scope_text": [],
        }, cqd)
        self.assertEqual([], normalized["conditions_text"])

    def test_range_condition_coverage_combines_sections_for_same_column(self) -> None:
        report = validate_grounded_condition_coverage({
            "conditions_text": ["account.opening_date between 2009-01-01 and 2010-12-31"],
        }, {
            "where_sections": [
                {"column": "accounts.OpenDate", "operator": "GTE",
                 "values": ["2009-01-01"], "matched_snippet": "opened after 1/1/2009"},
                {"column": "accounts.OpenDate", "operator": "LTE",
                 "values": ["2010-12-31"], "matched_snippet": "opened before 12/31/2010"},
            ],
        })
        self.assertEqual("complete", report["status"], report)

    def test_normalization_discards_logical_binding_aliases(self) -> None:
        bundle = self._q2_bundle()
        bundle["table_linking"]["column_bindings"]["location_county"] = "schools"
        catalog = {
            "tables": [
                {"name": "frpm", "columns": [
                    {"name": "Charter School (Y/N)"}, {"name": "District Name"},
                    {"name": "CDSCode"},
                ]},
                {"name": "schools", "columns": [
                    {"name": "Zip"}, {"name": "CDSCode"},
                ]},
            ]
        }
        normalized = normalize_direct_sif_bundle(bundle=bundle, schema_catalog=catalog)
        self.assertNotIn("location_county", normalized["table_linking"]["column_bindings"])
        self.assertEqual(
            "schools", normalized["table_linking"]["column_bindings"]["Zip"]
        )

    def test_q2_understanding_preserves_charter_and_location_conditions(self) -> None:
        report = validate_understanding_coverage(Q2_CQD, Q2_UNDERSTANDING)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["missing_count"], 0)

    def test_output_owner_may_be_split_between_target_and_field_sections(self) -> None:
        cqd = {
            "selections": [{
                "selection": "valid e-mail addresses",
                "of_entity": "administrator",
            }],
            "outputs": [{
                "output": "valid e-mail addresses",
                "of_entity": "administrator",
            }],
        }
        understanding = {
            "targets": ["valid e-mail addresses"],
            "field_phrases": ["administrator"],
            "conditions_text": [],
            "scope_text": [],
        }
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual("complete", report["status"], report)

    def test_output_entity_may_be_represented_in_scope_section(self) -> None:
        cqd = {"outputs": [{
            "output_type": "attribute",
            "attribute": "valid e-mail addresses",
            "entity": "administrator",
        }]}
        understanding = {
            "targets": ["valid e-mail addresses"],
            "field_phrases": ["valid e-mail addresses"],
            "conditions_text": [],
            "scope_text": ["administrator of the school"],
        }
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual("complete", report["status"], report)

    def test_coverage_ignores_cqd_trace_metadata_in_predicate_objects(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["populations"][0]["filter"] = {
            "dimension": "Charter School (Y/N)", "operator": "=", "value": 1,
            "source_text": "Charter schools refers to the benchmark evidence table",
        }
        cqd["predicates"] = {"root": "and", "atoms": [{
            "condition_id": 1, "dimension": "Charter School (Y/N)",
            "operator": "=", "value": 1, "applies_to": "population",
            "source_text": "Charter schools refers to the benchmark evidence table",
        }, {
            "condition_id": 2, "dimension": "County Office of Education",
            "operator": "=", "value": "Fresno", "applies_to": "population",
            "source_text": "in Fresno County Office of Education",
        }]}
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["conditions_text"] = [
            "schools are charter schools (Charter School (Y/N) = 1)",
            "schools are in Fresno County Office of Education",
        ]
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual(report["status"], "complete", report)

    def test_source_text_can_cover_equivalent_natural_language_condition(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["populations"][0]["filter"] = {
            "attribute": "Charter School (Y/N)", "value": 1,
            "source_text": "charter schools",
        }
        cqd["predicates"] = {"root": "and", "atoms": [{
            "condition_id": "1", "dimension": "Charter School (Y/N)",
            "operator": "=", "value": 1, "applies_to": "schools",
            "source_text": "charter schools",
        }]}
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["conditions_text"] = [
            "schools are charter schools",
            "schools belong to Fresno County Office of Education",
        ]
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual(report["status"], "complete", report)

    def test_description_and_equivalent_date_formats_cover_cqd_meaning(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["populations"] = [{"entity": "schools"}]
        cqd["outputs"] = [{
            "output_type": "value",
            "description": "the most common type of grade span served",
        }]
        cqd["selections"] = []
        cqd["aggregations"] = [{
            "aggregation_type": "mode",
            "description": "most common type of grade span served",
            "grain_scope": "global",
        }]
        cqd["predicates"] = {"root": None, "atoms": [{
            "dimension": "school_open_date", "operator": "between",
            "value": ["2009-01-01", "2010-12-31"],
            "source_text": "opened between 1/1/2009 to 12/31/2010",
        }]}
        cqd["time_constraints"] = [{
            "time_dimension": "school_open_date",
            "start_date": "2009-01-01", "end_date": "2010-12-31",
            "source_text": "opened between 1/1/2009 to 12/31/2010",
        }]
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["most common type of grade span served"]
        understanding["field_phrases"] = ["grade span served"]
        understanding["conditions_text"] = [
            "schools are included",
            "school opened between 2009-01-01 and 2010-12-31",
        ]
        report = validate_understanding_coverage(cqd, understanding)
        self.assertEqual(report["status"], "complete", report)

    def test_q2_cannot_complete_when_charter_is_only_the_subject_label(self) -> None:
        understanding = dict(Q2_UNDERSTANDING)
        understanding["primary_subject_anchor"] = "charter schools"
        understanding["conditions_text"] = [
            "schools belong to Fresno County Office of Education"
        ]
        report = validate_understanding_coverage(Q2_CQD, understanding)
        missing = {
            item["cqd_ref"] for item in report["assessments"]
            if item["status"] == "missing"
        }
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("/populations/0/entity", missing)

    def test_every_condition_requires_a_where_section(self) -> None:
        sql_plan = {
            "where_sections": [{
                "column": "schools.District", "operator": "EQ",
                "values": ["Fresno County Office of Education"],
                "is_negation": False,
                "matched_snippet": (
                    "schools belong to Fresno County Office of Education"
                ),
            }],
            "order_by_section": [],
            "limit_section": {"count": None, "matched_snippet": ""},
            "dedupe_section": {
                "key_column": None, "policy": None, "matched_snippet": ""
            },
        }
        report = validate_grounded_condition_coverage(Q2_UNDERSTANDING, sql_plan)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(
            report["assessments"][0]["condition"], "schools are charter schools"
        )

    def test_q2_direct_sif_compiles_all_conditions_and_required_join(self) -> None:
        bundle = self._q2_bundle()
        schema_catalog = {"tables": [{
            "name": "schools", "columns": [{"name": "CDSCode"}, {"name": "Zip"}],
        }, {
            "name": "frpm", "columns": [
                {"name": "CDSCode"}, {"name": "Charter School (Y/N)"},
                {"name": "District Name"},
            ],
        }]}
        report = validate_direct_sif_bundle(
            understanding=Q2_UNDERSTANDING,
            bundle=bundle,
            schema_catalog=schema_catalog,
        )
        self.assertEqual(report["status"], "complete")
        sql = compile_direct_sif(
            understanding=Q2_UNDERSTANDING, bundle=bundle
        )
        self.assertIn('SELECT "schools"."Zip"', sql)
        self.assertIn('"frpm"."Charter School (Y/N)" = 1', sql)
        self.assertIn(
            '"frpm"."District Name" = \'Fresno County Office of Education\'', sql
        )
        self.assertIn('INNER JOIN "schools"', sql)

    def test_plural_target_compiles_all_matching_physical_columns(self) -> None:
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [{
            "physicalName": "AdmEmail1", "termId": None,
            "surface": "valid email addresses", "selectionRule": "schema",
        }, {
            "physicalName": "AdmEmail2", "termId": None,
            "surface": "valid email addresses", "selectionRule": "schema",
        }]
        bundle["table_linking"].update({
            "root_anchor": "schools",
            "selected_tables": [{"table": "schools", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {"AdmEmail1": "schools", "AdmEmail2": "schools"},
        })
        bundle["sql_plan"]["where_sections"] = []
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["valid email addresses"]
        understanding["conditions_text"] = []
        sql = compile_direct_sif(understanding=understanding, bundle=bundle)
        self.assertIn('SELECT "schools"."AdmEmail1", "schools"."AdmEmail2"', sql)

    def test_binding_intents_keep_select_and_where_roles_separate(self) -> None:
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        intents = build_binding_intents(understanding, Q2_CQD)
        self.assertEqual("SELECT", intents[0]["role"])
        self.assertEqual("ambiguous", intents[0]["cardinality"])
        self.assertTrue(all(item["role"] == "WHERE" for item in intents[1:]))

    def test_literal_candidate_resolution_skips_is_not_null(self) -> None:
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["support contact addresses"]
        understanding["conditions_text"] = ["account manager exists"]
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [{
            "physicalName": "ContactAddress1", "termId": "target",
            "surface": "support contact addresses", "selectionRule": "schema",
        }]
        bundle["table_linking"].update({
            "root_anchor": "accounts",
            "selected_tables": [{"table": "accounts", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {"ContactAddress1": "accounts"},
        })
        bundle["sql_plan"]["where_sections"] = [{
            "column": "accounts.ContactAddress1", "operator": "IS_NOT_NULL",
            "values": [None], "is_negation": False,
            "matched_snippet": "account manager exists",
        }]
        catalog = {"tables": [{
            "name": "accounts",
            "columns": [{"name": "ContactAddress1"}, {"name": "ManagerLastName2"}],
            "question_value_matches": [{"column": "ManagerLastName2", "value": "Manager"}],
        }]}
        corrected, report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="Return support contact addresses for the account manager",
            benchmark_evidence=None, understanding=understanding,
        )
        self.assertEqual(
            "accounts.ContactAddress1",
            corrected["sql_plan"]["where_sections"][0]["column"],
        )
        self.assertEqual(
            "ContactAddress1",
            corrected["confirmed_grounding"]["confirmed"]["terms"][0]["physicalName"],
        )
        self.assertEqual("not_applicable", report["decisions"][0]["status"])

    def test_multiple_output_expands_usable_numbered_schema_family(self) -> None:
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["support contact addresses"]
        understanding["conditions_text"] = []
        cqd = copy.deepcopy(Q2_CQD)
        cqd["outputs"] = [{
            "output_type": "list", "value_multiplicity": "multiple",
            "content": "support contact addresses",
        }]
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [{
            "physicalName": "ContactAddress1", "termId": "target",
            "surface": "support contact addresses", "selectionRule": "schema",
        }]
        bundle["table_linking"].update({
            "root_anchor": "accounts",
            "selected_tables": [{"table": "accounts", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {"ContactAddress1": "accounts"},
        })
        bundle["sql_plan"]["where_sections"] = []
        catalog = {"tables": [{
            "name": "accounts", "columns": [
                {"name": "ContactAddress1", "description": "support contact address"},
                {"name": "ContactAddress2"},
                {"name": "ContactAddress3", "status": "deprecated"},
            ],
        }]}
        expanded, report = expand_plural_output_bindings(
            bundle=bundle, understanding=understanding, confirmed_cqd=cqd,
            schema_catalog=catalog,
        )
        sql = compile_direct_sif(understanding=understanding, bundle=expanded)
        self.assertIn(
            'SELECT "accounts"."ContactAddress1", "accounts"."ContactAddress2"', sql
        )
        self.assertNotIn("ContactAddress3", sql)
        self.assertEqual("expanded", report["decisions"][0]["status"])

    def test_collection_does_not_imply_multiple_physical_columns(self) -> None:
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["support contact addresses"]
        understanding["conditions_text"] = []
        cqd = copy.deepcopy(Q2_CQD)
        cqd["outputs"] = [{
            "output_type": "list", "content": "support contact addresses",
        }]
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [{
            "physicalName": "ContactAddress1", "termId": "target",
            "surface": "support contact addresses", "selectionRule": "schema",
        }]
        bundle["table_linking"].update({
            "root_anchor": "accounts",
            "selected_tables": [{"table": "accounts", "tier": 1, "role": "root"}],
            "joins": [], "column_bindings": {"ContactAddress1": "accounts"},
        })
        bundle["sql_plan"]["where_sections"] = []
        catalog = {"tables": [{
            "name": "accounts", "columns": [
                {"name": "ContactAddress1"}, {"name": "ContactAddress2"},
            ],
        }]}
        unchanged, report = expand_plural_output_bindings(
            bundle=bundle, understanding=understanding, confirmed_cqd=cqd,
            schema_catalog=catalog,
        )
        self.assertNotIn(
            "ContactAddress2", unchanged["table_linking"]["column_bindings"]
        )
        self.assertEqual("clarification_required", report["status"])
        self.assertEqual("CLARIFICATION_REQUIRED", report["issues"][0]["type"])

        options = report["issues"][0]["candidates"]
        resolved, resolved_report = expand_plural_output_bindings(
            bundle=bundle, understanding=understanding, confirmed_cqd=cqd,
            schema_catalog=catalog,
            technical_feedback=[{
                "type": "TECHNICAL_CLARIFICATION_RESPONSE",
                "surface": "support contact addresses", "candidates": options,
                "response": {"selected_option_ids": ["option_2"]},
            }],
        )
        self.assertEqual("resolved", resolved_report["status"])
        self.assertIn(
            "ContactAddress2", resolved["table_linking"]["column_bindings"]
        )

    def test_unspecified_output_cardinality_clarifies_numbered_schema_family(self) -> None:
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["support contact addresses"]
        understanding["conditions_text"] = []
        cqd = copy.deepcopy(Q2_CQD)
        cqd["outputs"] = [{
            "output_type": "contact addresses",
            "source_text": "support contact addresses",
        }]
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [{
            "physicalName": "ContactAddress1", "termId": "target",
            "surface": "support contact addresses", "selectionRule": "schema",
        }]
        bundle["table_linking"].update({
            "root_anchor": "accounts",
            "selected_tables": [{"table": "accounts", "tier": 1, "role": "root"}],
            "joins": [], "column_bindings": {"ContactAddress1": "accounts"},
        })
        bundle["sql_plan"]["where_sections"] = []
        catalog = {"tables": [{"name": "accounts", "columns": [
            {"name": "ContactAddress1"}, {"name": "ContactAddress2"},
        ]}]}
        _, report = expand_plural_output_bindings(
            bundle=bundle, understanding=understanding, confirmed_cqd=cqd,
            schema_catalog=catalog,
        )
        self.assertEqual("clarification_required", report["status"])

    def test_schema_marked_unusable_output_is_removed_without_name_hardcoding(self) -> None:
        understanding = copy.deepcopy(Q2_UNDERSTANDING)
        understanding["targets"] = ["support contact addresses"]
        understanding["conditions_text"] = []
        cqd = copy.deepcopy(Q2_CQD)
        cqd["outputs"] = [{
            "content": "support contact addresses",
            "value_multiplicity": "multiple",
        }]
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [
            {"physicalName": name, "termId": "target",
             "surface": "support contact addresses", "selectionRule": "schema"}
            for name in ("ContactAddress1", "ContactAddress2", "ContactAddress3")
        ]
        bundle["table_linking"].update({
            "root_anchor": "accounts",
            "selected_tables": [{"table": "accounts", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {
                name: "accounts" for name in (
                    "ContactAddress1", "ContactAddress2", "ContactAddress3"
                )
            },
        })
        bundle["sql_plan"]["where_sections"] = []
        catalog = {"tables": [{
            "name": "accounts", "columns": [
                {"name": "ContactAddress1"}, {"name": "ContactAddress2"},
                {"name": "ContactAddress3", "status": "deprecated"},
            ],
        }]}
        normalized, report = expand_plural_output_bindings(
            bundle=bundle, understanding=understanding, confirmed_cqd=cqd,
            schema_catalog=catalog,
        )
        physical = {
            item["physicalName"]
            for item in normalized["confirmed_grounding"]["confirmed"]["terms"]
        }
        self.assertEqual({"ContactAddress1", "ContactAddress2"}, physical)
        self.assertTrue(any(
            item["status"] == "discarded_unusable_schema_members"
            for item in report["decisions"]
        ))

    def test_model_adapter_supplies_evidence_to_early_semantic_agents(self) -> None:
        class Client:
            def __init__(self):
                self.payload = None

            def complete_json(self, **kwargs):
                self.payload = kwargs["payload"]
                return {}

        client = Client()
        _ModelAdapter(client, benchmark_evidence="Charter means physical code 1").complete_json(
            agent="source_analyzer", system="test", payload={"question": "q"}, model="m"
        )
        self.assertEqual(
            client.payload["benchmark_evidence"], "Charter means physical code 1"
        )

    def test_exact_catalog_normalization_repairs_inverse_column_bindings(self) -> None:
        bundle = self._q2_bundle()
        bundle["table_linking"]["column_bindings"] = {
            "zip code": "schools.Zip",
            "charter status": "frpm.Charter School (Y/N)",
            "district": "frpm.District Name",
        }
        bundle["confirmed_grounding"]["confirmed"]["terms"][0]["physicalName"] = "schools.Zip"
        catalog = {"tables": [
            {"name": "schools", "columns": [{"name": "CDSCode"}, {"name": "Zip"}]},
            {"name": "frpm", "columns": [
                {"name": "CDSCode"}, {"name": "Charter School (Y/N)"},
                {"name": "District Name"},
            ]},
        ]}
        normalized = normalize_direct_sif_bundle(
            bundle=bundle, schema_catalog=catalog
        )
        self.assertEqual(normalized["table_linking"]["column_bindings"], {
            "Zip": "schools",
            "Charter School (Y/N)": "frpm",
            "District Name": "frpm",
        })
        self.assertEqual(
            normalized["confirmed_grounding"]["confirmed"]["terms"][0]["physicalName"],
            "Zip",
        )

    def test_candidate_resolver_clarifies_conflicting_value_and_dimension_signals(self) -> None:
        bundle = self._q2_bundle()
        bundle["sql_plan"]["where_sections"][1].update({
            "column": "schools.County", "values": ["Fresno"],
            "matched_snippet": "schools belong to Fresno County Office of Education",
        })
        bundle["table_linking"]["column_bindings"]["County"] = "schools"
        catalog = {"tables": [{
            "name": "frpm",
            "columns": [
                {"name": "CDSCode"}, {"name": "Charter School (Y/N)"},
                {"name": "District Name"}, {"name": "County Name"},
            ],
            "question_value_matches": [
                {"column": "County Name", "value": "Fresno", "match": "value_in_question"},
                {"column": "District Name", "value": "Fresno County Office of Education", "match": "value_in_question"},
            ],
        }, {
            "name": "schools",
            "columns": [{"name": "CDSCode"}, {"name": "Zip"}, {"name": "County"}, {"name": "District"}],
            "question_value_matches": [
                {"column": "County", "value": "Fresno", "match": "value_in_question"},
                {"column": "District", "value": "Fresno County Office of Education", "match": "value_in_question"},
            ],
        }]}
        corrected, report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="List charter schools in Fresno County Office of Education",
            benchmark_evidence="Charter schools refers to Charter School (Y/N) = 1 in fprm",
        )
        location = corrected["sql_plan"]["where_sections"][1]
        self.assertEqual(location["column"], "schools.County")
        self.assertEqual(location["values"], ["Fresno"])
        self.assertEqual(report["status"], "clarification_required")
        self.assertEqual(report["decisions"][-1]["status"], "clarification_required")

    def test_candidate_resolver_recovers_full_value_from_cqd_source_text(self) -> None:
        bundle = self._q2_bundle()
        bundle["sql_plan"]["where_sections"][1].update({
            "column": "frpm.County Name", "values": ["Fresno"],
            "matched_snippet": "County Office of Education = Fresno",
        })
        bundle["table_linking"]["column_bindings"]["County Name"] = "frpm"
        cqd = copy.deepcopy(Q2_CQD)
        cqd["predicates"]["atoms"].append({
            "dimension": "County Office of Education", "operator": "=",
            "value": "Fresno", "source_text": "Fresno County Office of Education",
        })
        catalog = {"tables": [{
            "name": "frpm", "columns": [
                {"name": "County Name"}, {"name": "District Name"},
            ],
            "question_value_matches": [
                {"column": "County Name", "value": "Fresno"},
                {"column": "District Name", "value": "Fresno County Office of Education"},
            ],
        }]}
        _, report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="List schools in Fresno County Office of Education",
            benchmark_evidence=None, confirmed_cqd=cqd,
        )
        self.assertEqual("clarification_required", report["status"])
        self.assertIn(
            "frpm.District Name = 'Fresno County Office of Education'",
            report["issues"][0]["candidates"],
        )

    def test_confirmed_cqd_physical_grounding_cannot_be_overwritten(self) -> None:
        bundle = self._q2_bundle()
        bundle["table_linking"].update({
            "root_anchor": "records",
            "selected_tables": [{"table": "records", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {"CategoryCode": "records", "Region": "records"},
        })
        bundle["sql_plan"]["where_sections"] = [{
            "column": "records.Region", "operator": "EQ", "values": ["North"],
            "is_negation": False,
            "matched_snippet": "category code equals CAT=7",
        }]
        cqd = {"predicates": {"atoms": [{
            "dimension": "category_code", "operator": "equals", "value": "CAT=7",
            "source_text": "category code seven",
            "grounding": {"table": "records", "column": "CategoryCode",
                          "physical_value": None},
        }]}}
        catalog = {"tables": [{
            "name": "records",
            "columns": [{"name": "CategoryCode"}, {"name": "Region"}],
            "question_value_matches": [{"column": "Region", "value": "North"}],
        }]}
        corrected, report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="records in North with category code seven",
            benchmark_evidence=None, confirmed_cqd=cqd,
        )
        section = corrected["sql_plan"]["where_sections"][0]
        self.assertEqual("records.CategoryCode", section["column"])
        self.assertEqual([7], section["values"])
        self.assertEqual("resolved", report["status"])
        self.assertEqual("cqd_grounding_locked", report["decisions"][0]["status"])

    def test_mode_cqd_is_compiled_to_frequency_query_without_extra_select(self) -> None:
        understanding = {
            **copy.deepcopy(Q2_UNDERSTANDING),
            "targets": ["most common grade span served in a city"],
            "conditions_text": ["city equals Adelanto"],
        }
        bundle = self._q2_bundle()
        bundle["confirmed_grounding"]["confirmed"]["terms"] = [
            {"physicalName": "GradeSpan", "termId": None,
             "surface": "grade span served", "selectionRule": "schema"},
            {"physicalName": "City", "termId": None,
             "surface": "city", "selectionRule": "schema"},
        ]
        bundle["table_linking"].update({
            "root_anchor": "entities",
            "selected_tables": [{"table": "entities", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {"GradeSpan": "entities", "City": "entities"},
        })
        bundle["sql_plan"]["where_sections"] = [{
            "column": "entities.City", "operator": "EQ", "values": ["Adelanto"],
            "is_negation": False, "matched_snippet": "city equals Adelanto",
        }]
        cqd = {
            "aggregations": [{
                "aggregation_type": "mode",
                "expression": {"type": "attribute", "name": "grade span served"},
                "description": "most common grade span served",
            }],
            "outputs": [{"output_type": "single value"}],
        }
        enriched = apply_cqd_sql_semantics(bundle=bundle, confirmed_cqd=cqd)
        sql = compile_direct_sif(understanding=understanding, bundle=enriched)
        self.assertIn('SELECT "entities"."GradeSpan"', sql)
        self.assertNotIn('"entities"."City",', sql)
        self.assertIn('GROUP BY "entities"."GradeSpan"', sql)
        self.assertIn('ORDER BY COUNT("entities"."GradeSpan") DESC', sql)
        self.assertIn("LIMIT 1", sql)

    def test_candidate_resolver_requests_clarification_when_exact_candidates_tie(self) -> None:
        bundle = self._q2_bundle()
        bundle["sql_plan"]["where_sections"] = [bundle["sql_plan"]["where_sections"][1]]
        catalog = {"tables": [{
            "name": "frpm", "columns": [{"name": "District Name"}],
            "question_value_matches": [{
                "column": "District Name", "value": "Fresno County Office of Education",
            }],
        }, {
            "name": "schools", "columns": [{"name": "District"}],
            "question_value_matches": [{
                "column": "District", "value": "Fresno County Office of Education",
            }],
        }]}
        _, report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="in Fresno County Office of Education",
            benchmark_evidence=None,
        )
        self.assertEqual(report["status"], "clarification_required")
        self.assertEqual(report["issues"][0]["type"], "CLARIFICATION_REQUIRED")
        self.assertEqual(len(report["issues"][0]["candidates"]), 2)

    def test_column_and_value_dimension_conflict_requires_clarification(self) -> None:
        bundle = self._q2_bundle()
        bundle["table_linking"].update({
            "root_anchor": "schools",
            "selected_tables": [{"table": "schools", "tier": 1, "role": "root"}],
            "joins": [],
            "column_bindings": {"SOC": "schools", "District": "schools"},
        })
        bundle["sql_plan"]["where_sections"] = [{
            "column": "schools.SOC", "operator": "EQ", "values": [62],
            "is_negation": False,
            "matched_snippet": "school ownership code equals SOC 62",
        }, {
            "column": "schools.District", "operator": "EQ",
            "values": ["San Bernardino City Unified"], "is_negation": False,
            "matched_snippet": "school located in the City of San Bernardino City Unified",
        }]
        catalog = {"tables": [{
            "name": "schools",
            "columns": [
                {"name": "SOC"}, {"name": "City"}, {"name": "District"},
            ],
            "question_value_matches": [
                {"column": "City", "value": "San Bernardino"},
                {"column": "District", "value": "San Bernardino City Unified"},
            ],
        }]}
        corrected, report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="school located in the City of San Bernardino City Unified",
            benchmark_evidence="Intermediate/Middle Schools refers to SOC = 62",
        )
        location = corrected["sql_plan"]["where_sections"][1]
        self.assertEqual(location["column"], "schools.District")
        self.assertEqual(location["values"], ["San Bernardino City Unified"])
        self.assertEqual(report["status"], "clarification_required")

        options = report["issues"][0]["candidates"]
        city_option = options.index("schools.City = 'San Bernardino'") + 1
        clarified, clarified_report = resolve_column_candidates(
            bundle=bundle, schema_catalog=catalog,
            original_question="school located in the City of San Bernardino City Unified",
            benchmark_evidence="Intermediate/Middle Schools refers to SOC = 62",
            technical_feedback=[{
                "type": "TECHNICAL_CLARIFICATION_RESPONSE",
                "surface": "school located in the City of San Bernardino City Unified",
                "candidates": options,
                "response": {"selected_option_ids": [f"option_{city_option}"]},
            }],
        )
        self.assertEqual("resolved", clarified_report["status"])
        self.assertEqual(
            "schools.City", clarified["sql_plan"]["where_sections"][1]["column"]
        )

    def test_runner_bypasses_bif_and_executes_direct_sif(self) -> None:
        cases = json.loads(
            (ROOT / "data/qa_set/BIRD_DEV_STRATIFIED_SMOKE_30.json")
            .read_text(encoding="utf-8")
        )
        case = copy.deepcopy(next(item for item in cases if str(item["question_id"]) == "2"))
        case["evidence"] = (
            str(case.get("evidence") or "")
            + "; Fresno County Office of Education refers to District Name"
        )
        bundle = self._q2_bundle()

        class Client:
            call_counts: dict[str, int] = {}

        class Agents:
            client = Client()
            semantic_frontend_active = True
            direct_sif_understanding = copy.deepcopy(Q2_UNDERSTANDING)
            semantic_frontend_result = {
                "confirmed_cqd": copy.deepcopy(Q2_CQD),
                "rounds": 0,
                "direct_sif_understanding_coverage": (
                    validate_understanding_coverage(Q2_CQD, Q2_UNDERSTANDING)
                ),
            }

            def a1_bif_analyze(self, selected_case, schema_catalog=None):
                return {
                    "draft_bif": copy.deepcopy(Q2_UNDERSTANDING),
                    "issue_observations": {"candidates": []},
                }

            def a4_direct_sif_grounding_bundle(self, **kwargs):
                return copy.deepcopy(bundle)

        database = (
            ROOT / "data/bird_dev/dev_databases" / case["db_id"]
            / f"{case['db_id']}.sqlite"
        )
        with TemporaryDirectory() as directory:
            logger = ArtifactLogger(Path(directory), "direct", "2")
            result = FullCaseRunner(
                Agents(), ReadOnlySQLiteExecutor(), sif_layout="v3"
            ).run(case=case, database=database, logger=logger)
            self.assertTrue(result.comparison.equivalent)
            self.assertIn('"frpm"."Charter School (Y/N)" = 1', result.final_sql)
            self.assertTrue(
                (logger.case_dir / "P03/frames/direct_sif_sql_plan.json").is_file()
                or (logger.case_dir / "P04/frames/direct_sif_sql_plan.json").is_file()
            )

    def test_runner_asks_technical_clarification_then_regrounds(self) -> None:
        cases = json.loads(
            (ROOT / "data/qa_set/BIRD_DEV_STRATIFIED_SMOKE_30.json")
            .read_text(encoding="utf-8")
        )
        case = copy.deepcopy(next(item for item in cases if str(item["question_id"]) == "2"))
        case["evidence"] = (
            str(case.get("evidence") or "")
            + "; Fresno County Office of Education refers to District Name"
        )
        complete_bundle = self._q2_bundle()

        class Client:
            call_counts: dict[str, int] = {}

        class Agents:
            client = Client()
            semantic_frontend_active = True
            direct_sif_understanding = copy.deepcopy(Q2_UNDERSTANDING)
            semantic_frontend_result = {
                "confirmed_cqd": copy.deepcopy(Q2_CQD), "rounds": 0,
                "direct_sif_understanding_coverage": (
                    validate_understanding_coverage(Q2_CQD, Q2_UNDERSTANDING)
                ),
            }

            def __init__(self):
                self.grounding_calls = 0
                self.e2_calls = 0

            def a1_bif_analyze(self, selected_case, schema_catalog=None):
                return {"draft_bif": copy.deepcopy(Q2_UNDERSTANDING), "issue_observations": {"candidates": []}}

            def a4_direct_sif_grounding_bundle(self, **kwargs):
                self.grounding_calls += 1
                result = copy.deepcopy(complete_bundle)
                if self.grounding_calls == 1:
                    result["confirmed_grounding"]["confirmed"]["unresolved"] = [{
                        "surface": "zip code",
                        "status": "CLARIFICATION_REQUIRED",
                        "reason": "postal and mailing ZIP have different meanings",
                        "candidates": ["physical location ZIP", "mailing ZIP"],
                    }]
                return result

            def e2_user(self, **kwargs):
                self.e2_calls += 1
                request = kwargs["clarification"]["clarification_request"]
                return {
                    "request_id": request["request_id"],
                    "mode": "SIF_CLARIFICATION", "action": "select",
                    "selected_option_ids": ["option_1"],
                    "free_text": None, "confidence": "high",
                }

        agents = Agents()
        database = ROOT / "data/bird_dev/dev_databases" / case["db_id"] / f"{case['db_id']}.sqlite"
        with TemporaryDirectory() as directory:
            result = FullCaseRunner(
                agents, ReadOnlySQLiteExecutor(), sif_layout="v3"
            ).run(
                case=case, database=database,
                logger=ArtifactLogger(Path(directory), "clarify", "2"),
            )
        self.assertTrue(result.comparison.equivalent)
        self.assertEqual(agents.grounding_calls, 2)
        self.assertEqual(agents.e2_calls, 1)

    def test_runner_continues_to_evaluation_when_grounding_is_unresolved(self) -> None:
        cases = json.loads(
            (ROOT / "data/qa_set/BIRD_DEV_STRATIFIED_SMOKE_30.json")
            .read_text(encoding="utf-8")
        )
        case = copy.deepcopy(next(item for item in cases if str(item["question_id"]) == "2"))
        unresolved_bundle = {
            "confirmed_grounding": {"confirmed": {
                "terms": [], "codes": [], "unresolved": [{
                    "surface": "zip code", "status": "UNRESOLVED_SYSTEM",
                    "reason": "no supported catalog candidate", "candidates": [],
                }],
            }},
            "table_linking": {
                "root_anchor": None, "selected_tables": [], "joins": [],
                "column_bindings": {}, "reasoning": [],
                "diagnostics": {"missing_columns": []},
            },
            "sql_plan": {
                "select_section": [], "where_sections": [],
                "group_by_section": [], "order_by_section": [],
                "limit_section": {"count": None, "matched_snippet": ""},
                "dedupe_section": {"key_column": None, "policy": None,
                                   "matched_snippet": ""},
            },
        }

        class Client:
            call_counts: dict[str, int] = {}

        class Agents:
            client = Client()
            semantic_frontend_active = True
            direct_sif_understanding = copy.deepcopy(Q2_UNDERSTANDING)
            semantic_frontend_result = {
                "confirmed_cqd": copy.deepcopy(Q2_CQD), "rounds": 0,
                "direct_sif_understanding_coverage": (
                    validate_understanding_coverage(Q2_CQD, Q2_UNDERSTANDING)
                ),
            }

            def __init__(self):
                self.grounding_calls = 0

            def a1_bif_analyze(self, selected_case, schema_catalog=None):
                return {"draft_bif": {}, "issue_observations": {"candidates": []}}

            def a4_direct_sif_grounding_bundle(self, **kwargs):
                self.grounding_calls += 1
                return copy.deepcopy(unresolved_bundle)

        agents = Agents()
        database = (
            ROOT / "data/bird_dev/dev_databases" / case["db_id"]
            / f"{case['db_id']}.sqlite"
        )
        with TemporaryDirectory() as directory:
            result = FullCaseRunner(
                agents, ReadOnlySQLiteExecutor(), sif_layout="v3"
            ).run(
                case=case, database=database,
                logger=ArtifactLogger(Path(directory), "unresolved", "2"),
            )
        self.assertEqual(5, agents.grounding_calls)
        self.assertEqual(
            'SELECT NULL AS "unresolved_result" WHERE 0;', result.final_sql
        )
        self.assertFalse(result.comparison.equivalent)


if __name__ == "__main__":
    unittest.main()
