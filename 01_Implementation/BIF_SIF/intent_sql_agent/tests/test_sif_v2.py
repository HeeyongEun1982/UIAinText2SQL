from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from intent_sql_agent.services.contract_validator import load_schema, validate_contract
from intent_sql_agent.services.sif_v2 import build_sif_v2_scaffold
from intent_sql_agent.services.sif_v2_validator import (
    validate_sif_v2_invariants,
    validate_sif_v2_physical_schema,
)
from intent_sql_agent.services.sif_v2_lowerer import (
    SIFV2LoweringError,
    lower_sif_v2_to_query_plan,
)
from intent_sql_agent.services.sif_sql_compiler import compile_sif
from intent_sql_agent.services.frame_state import frame_hash
from intent_sql_agent.services.sif_v2_patch import (
    admit_sif_v2_grounding_proposal,
    apply_sif_v2_grounding_proposal,
)
from intent_sql_agent.services.sif_v2_semantic_validator import validate_sif_v2_semantics
from intent_sql_agent.services.sif_v2_grounding_coordinator import SIFV2GroundingCoordinator
from intent_sql_agent.services.sif_v2_clarification import SIFV2ClarificationSession


def _bif() -> dict:
    return {
        "schema_version": "1.1",
        "frame_id": "bif_q87",
        "source": {"question_id": 87, "db_id": "california_schools", "question": "emails"},
        "lifecycle": {"state": "confirmed", "revision": 2},
        "target_population": {
            "primary_entity": "school",
            "inclusion_conditions": [{
                "condition_id": "cond_school_type_intermediate",
                "operator": "equals", "value_mode": "scalar",
                "value": "Intermediate/Middle Schools",
            }, {
                "condition_id": "cond_school_type_unified",
                "operator": "equals", "value_mode": "scalar",
                "value": "Unified Schools",
            }],
            "inclusion_logic": {
                "operator": "all_of",
                "condition_refs": [
                    "cond_school_type_intermediate", "cond_school_type_unified",
                ],
                "groups": [],
            },
            "exclusion_conditions": [],
            "exclusion_logic": {"operator": "none_of", "condition_refs": [], "groups": []},
        },
        "metrics": [{"metric_id": "metric_admin_email"}],
        "calculation": {"steps": []},
        "granularity": {"dimensions": []},
        "scope": {"selections": []},
        "time_basis": {"periods": []},
        "comparisons": [],
        "output_expectation": {
            "fields": [{
                "field_id": "field_admin_email",
                "semantic_ref": "metric_admin_email",
            }],
            "ordering": [], "include_supporting_values": False,
        },
    }


class SIFV2Tests(unittest.TestCase):
    def test_scaffold_has_one_slot_per_bif_node_and_valid_contract(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif, schema_fingerprint="schema-hash")
        slots = sif["target_population"]["inclusion_condition_groundings"]
        self.assertEqual(
            ["cond_school_type_intermediate", "cond_school_type_unified"],
            [item["condition_ref"] for item in slots],
        )
        self.assertEqual("/target_population/inclusion_logic", sif["target_population"]["inclusion_logic_ref"])
        schema = load_schema(
            Path(__file__).resolve().parents[2] / "spec" / "sql_intent_frame_v2.schema.json"
        )
        validate_contract(sif, schema, "SIF 2.0 scaffold")
        self.assertEqual([], validate_sif_v2_invariants(bif, sif))

    def test_missing_slot_cannot_be_hidden_by_merging_two_conditions(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        sif["target_population"]["inclusion_condition_groundings"] = [
            sif["target_population"]["inclusion_condition_groundings"][0]
        ]
        findings = validate_sif_v2_invariants(bif, sif)
        self.assertIn("missing_grounding_slot", {item["issue"] for item in findings})

    def test_logic_reference_and_source_bif_are_immutable_contracts(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        sif["target_population"]["inclusion_logic_ref"] = "/query/where/or"
        sif["source_bif"]["frame_hash"] = "changed"
        issues = {item["issue"] for item in validate_sif_v2_invariants(bif, sif)}
        self.assertIn("bif_logic_reference_changed", issues)
        self.assertIn("source_bif_mismatch", issues)

    def test_ready_frame_requires_every_independent_binding(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        sif["status"] = "ready"
        sif["readiness"] = {"state": "ready", "blocking_refs": []}
        sif["unresolved_groundings"] = []
        findings = validate_sif_v2_invariants(bif, sif)
        self.assertIn("ready_with_unresolved_primary_entity", {item["issue"] for item in findings})
        self.assertEqual(4, sum(item["issue"] == "ready_with_unresolved_slot" for item in findings))

    def test_duplicate_condition_slot_is_rejected(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        slot = copy.deepcopy(sif["target_population"]["inclusion_condition_groundings"][0])
        slot["grounding_id"] += "_duplicate"
        sif["target_population"]["inclusion_condition_groundings"].append(slot)
        findings = validate_sif_v2_invariants(bif, sif)
        self.assertIn("duplicate_grounding_slot", {item["issue"] for item in findings})

    def test_q87_independent_conditions_lower_to_soc_and_doc_without_extra_output(self) -> None:
        bif = _bif()
        for condition in bif["target_population"]["inclusion_conditions"]:
            condition.update({
                "semantic_dimension": None,
                "semantic_dimension_status": "deferred_to_sif",
                "grounding_requirement": "schema",
            })
        bif["output_expectation"]["fields"][0]["label"] = "Administrator E-mail Address"
        sif = build_sif_v2_scaffold(bif)
        sif["physical_support"]["sources"] = [{
            "source_id": "schools_source", "kind": "table", "name": "schools", "alias": "s",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        primary = sif["target_population"]["primary_entity_grounding"]
        primary.update({"source_ref": "schools_source", "status": "system_resolved"})
        slots = sif["target_population"]["inclusion_condition_groundings"]
        slots[0]["bindings"] = [{
            "source_ref": "schools_source", "column": "SOC", "sql_operator": "equals",
            "physical_value": "62", "data_type": "text", "transformation": "code_mapping",
        }]
        slots[1]["bindings"] = [{
            "source_ref": "schools_source", "column": "DOC", "sql_operator": "equals",
            "physical_value": "54", "data_type": "text", "transformation": "code_mapping",
        }]
        for slot in slots:
            slot["status"] = "system_resolved"
            slot["binding_mode"] = "code_mapping"
        metric = sif["metrics"]["metric_groundings"][0]
        output = sif["output_expectation"]["field_groundings"][0]
        email_bindings = [{
            "source_ref": "schools_source", "column": column, "sql_operator": None,
            "physical_value": None, "data_type": "text", "transformation": "direct",
        } for column in ("AdmEmail1", "AdmEmail2")]
        metric.update({"bindings": copy.deepcopy(email_bindings), "status": "system_resolved"})
        output.update({"bindings": copy.deepcopy(email_bindings), "status": "system_resolved"})
        sif["status"] = "ready"
        sif["readiness"] = {"state": "ready", "blocking_refs": []}
        sif["unresolved_groundings"] = []

        self.assertEqual([], validate_sif_v2_invariants(bif, sif))
        plan = lower_sif_v2_to_query_plan(bif, sif)
        sql = compile_sif(plan)
        self.assertIn('"s"."SOC" = \'62\'', sql)
        self.assertIn('"s"."DOC" = \'54\'', sql)
        self.assertIn(" AND ", sql)
        self.assertNotIn(" IN ", sql)
        self.assertNotIn("CDSCode", sql)
        self.assertIn('FROM "schools" AS "s"', sql)
        self.assertEqual(2, len(plan["query_blocks"][0]["projection"]))

    def test_slot_proposal_cannot_merge_or_rename_bif_conditions(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        proposal = {
            "request_id": "grounding-1",
            "base_frame_hash": frame_hash(sif),
            "source_additions": [{
                "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
                "origin_refs": ["/target_population/primary_entity"],
            }],
            "join_additions": [],
            "primary_entity_source_ref": "s",
            "slot_proposals": [{
                "grounding_id": "ground_inc_cond_school_type_intermediate",
                "binding_mode": "code_mapping", "binding_composition": "all_of",
                "bindings": [{
                    "source_ref": "s", "column": "SOC", "sql_operator": "equals",
                    "physical_value": "62", "data_type": "text", "transformation": "code_mapping",
                }],
                "confidence": "high", "unresolved_reason": None,
            }],
        }
        staged, events = apply_sif_v2_grounding_proposal(
            sif, proposal, expected_request_id="grounding-1",
            allowed_grounding_ids={
                "ground_primary_entity", "ground_inc_cond_school_type_intermediate",
            },
        )
        slots = staged["target_population"]["inclusion_condition_groundings"]
        self.assertEqual(
            ["cond_school_type_intermediate", "cond_school_type_unified"],
            [item["condition_ref"] for item in slots],
        )
        self.assertEqual("proposed", slots[0]["status"])
        self.assertEqual("unresolved", slots[1]["status"])
        self.assertIn("proposal_staged", {item["operation"] for item in events})

        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOC", "value_description": "62 - Intermediate/Middle Schools (Public)"},
        ]}]}
        committed, commit_events = admit_sif_v2_grounding_proposal(
            bif, sif, proposal, catalog,
            expected_request_id="grounding-1",
            allowed_grounding_ids={
                "ground_primary_entity", "ground_inc_cond_school_type_intermediate",
            },
            resolution="system_resolved",
        )
        self.assertEqual("system_resolved", committed["target_population"]["primary_entity_grounding"]["status"])
        self.assertEqual("system_resolved", committed["target_population"]["inclusion_condition_groundings"][0]["status"])
        self.assertIn("admission_committed", {item["operation"] for item in commit_events})

    def test_locked_slot_rejects_later_model_rewrite(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        slot = sif["target_population"]["inclusion_condition_groundings"][0]
        slot["bindings"] = [{
            "source_ref": "s", "column": "SOC", "sql_operator": "equals",
            "physical_value": "62", "data_type": "text", "transformation": "code_mapping",
        }]
        slot["status"] = "system_resolved"
        sif["physical_support"]["sources"] = [{
            "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        proposal = {
            "request_id": "rewrite", "base_frame_hash": frame_hash(sif),
            "source_additions": [], "join_additions": [], "primary_entity_source_ref": None,
            "slot_proposals": [{
                "grounding_id": slot["grounding_id"], "binding_mode": "direct",
                "binding_composition": "any_of",
                "bindings": [{
                    "source_ref": "s", "column": "SOC", "sql_operator": "in",
                    "physical_value": ["62", "54"], "data_type": "text",
                    "transformation": "direct",
                }],
                "confidence": "high", "unresolved_reason": None,
            }],
        }
        result, events = apply_sif_v2_grounding_proposal(
            sif, proposal, expected_request_id="rewrite",
            allowed_grounding_ids={slot["grounding_id"]},
        )
        self.assertEqual(sif, result)
        self.assertIn("locked_grounding_slot", {item.get("reason") for item in events})

    def test_physical_validator_rejects_unknown_binding_column(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        sif["physical_support"]["sources"] = [{
            "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        slot = sif["target_population"]["inclusion_condition_groundings"][0]
        slot["bindings"] = [{
            "source_ref": "s", "column": "NotAColumn", "sql_operator": "equals",
            "physical_value": "62", "data_type": "text", "transformation": "direct",
        }]
        catalog = {"tables": [{"name": "schools", "columns": [{"name": "SOC"}]}]}
        findings = validate_sif_v2_physical_schema(sif, catalog)
        self.assertIn("binding_column_not_in_source", {item["issue"] for item in findings})

    def test_full_q87_example_preserves_bif_logic_through_sql_compilation(self) -> None:
        bif = {
            "schema_version": "1.1", "frame_id": "bif_bird_0087",
            "source": {"question_id": 87, "db_id": "california_schools", "question": "emails"},
            "lifecycle": {"state": "confirmed", "revision": 1},
            "target_population": {
                "primary_entity": "school",
                "inclusion_conditions": [
                    {"condition_id": "cond_county_san_bernardino", "operator": "equals", "value": "San Bernardino"},
                    {"condition_id": "cond_city_san_bernardino", "operator": "equals", "value": "San Bernardino"},
                    {"condition_id": "cond_open_date_range", "operator": "between", "value": {"start": "2009-01-01", "end": "2010-12-31"}},
                    {"condition_id": "cond_school_type_intermediate", "operator": "equals", "value": "Intermediate/Middle Schools"},
                    {"condition_id": "cond_school_type_unified", "operator": "equals", "value": "Unified Schools"},
                ],
                "inclusion_logic": {
                    "operator": "all_of",
                    "condition_refs": [
                        "cond_county_san_bernardino", "cond_city_san_bernardino",
                        "cond_open_date_range", "cond_school_type_intermediate",
                        "cond_school_type_unified",
                    ],
                    "groups": [],
                },
                "exclusion_conditions": [],
                "exclusion_logic": {"operator": "none_of", "condition_refs": [], "groups": []},
            },
            "metrics": [{"metric_id": "metric_valid_admin_email"}],
            "calculation": {"steps": []}, "granularity": {"dimensions": []},
            "scope": {"selections": []}, "time_basis": {"periods": []},
            "comparisons": [],
            "output_expectation": {
                "fields": [{
                    "field_id": "field_admin_email", "label": "Administrator E-mail Address",
                    "semantic_ref": "metric_valid_admin_email",
                }],
                "ordering": [], "limit": None, "include_supporting_values": False,
            },
        }
        example_path = (
            Path(__file__).resolve().parents[2] / "spec" / "examples" /
            "bird_0087.sql_intent_v2.json"
        )
        sif = json.loads(example_path.read_text(encoding="utf-8"))
        sif["source_bif"]["frame_hash"] = frame_hash(bif)
        self.assertEqual([], validate_sif_v2_invariants(bif, sif))
        sql = compile_sif(lower_sif_v2_to_query_plan(bif, sif))
        self.assertIn('"s"."City" = \'San Bernardino\'', sql)
        self.assertNotIn('"s"."District"', sql)
        self.assertIn('"s"."SOC" = \'62\'', sql)
        self.assertIn('"s"."DOC" = \'54\'', sql)
        self.assertNotIn(" IN ", sql)
        self.assertNotIn("CDSCode", sql)

    def test_semantic_validator_rejects_operator_change_and_wrong_output(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        sif["physical_support"]["sources"] = [{
            "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        slots = sif["target_population"]["inclusion_condition_groundings"]
        slots[0]["bindings"] = [{
            "source_ref": "s", "column": "SOC", "sql_operator": "in",
            "physical_value": ["62", "54"], "data_type": "text", "transformation": "direct",
        }]
        metric = sif["metrics"]["metric_groundings"][0]
        metric["bindings"] = [{
            "source_ref": "s", "column": "AdmEmail1", "sql_operator": None,
            "physical_value": None, "data_type": "text", "transformation": "direct",
        }]
        output = sif["output_expectation"]["field_groundings"][0]
        output["bindings"] = [{
            "source_ref": "s", "column": "CDSCode", "sql_operator": None,
            "physical_value": None, "data_type": "text", "transformation": "direct",
        }]
        issues = {
            item["issue"]
            for item in validate_sif_v2_semantics(bif, sif, {"tables": []})
        }
        self.assertIn("condition_operator_changed", issues)
        self.assertIn("output_metric_binding_mismatch", issues)

    def test_documented_code_mapping_is_semantically_accepted(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        sif["physical_support"]["sources"] = [{
            "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        slots = sif["target_population"]["inclusion_condition_groundings"]
        slots[0]["bindings"] = [{
            "source_ref": "s", "column": "SOC", "sql_operator": "equals",
            "physical_value": "62", "data_type": "text", "transformation": "code_mapping",
        }]
        slots[1]["bindings"] = [{
            "source_ref": "s", "column": "DOC", "sql_operator": "equals",
            "physical_value": "54", "data_type": "text", "transformation": "code_mapping",
        }]
        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOC", "value_description": "62 - Intermediate/Middle Schools (Public)"},
            {"name": "DOC", "value_description": "54 - Unified School District"},
        ]}]}
        self.assertEqual([], validate_sif_v2_semantics(bif, sif, catalog))

    def test_admission_gate_rejects_goal_change_and_wrong_output_binding(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        proposal = {
            "request_id": "malicious", "base_frame_hash": frame_hash(sif),
            "source_additions": [{
                "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
                "origin_refs": ["/target_population/primary_entity"],
            }],
            "join_additions": [], "primary_entity_source_ref": "s",
            "slot_proposals": [{
                "grounding_id": "ground_inc_cond_school_type_intermediate",
                "binding_mode": "direct", "binding_composition": "any_of",
                "bindings": [{
                    "source_ref": "s", "column": "SOC", "sql_operator": "in",
                    "physical_value": ["62", "54"], "data_type": "text",
                    "transformation": "direct",
                }],
                "confidence": "high", "unresolved_reason": None,
            }, {
                "grounding_id": "ground_metric_metric_admin_email",
                "binding_mode": "direct", "binding_composition": None,
                "bindings": [{
                    "source_ref": "s", "column": "AdmEmail1", "sql_operator": None,
                    "physical_value": None, "data_type": "text", "transformation": "direct",
                }],
                "confidence": "high", "unresolved_reason": None,
            }, {
                "grounding_id": "ground_output_field_admin_email",
                "binding_mode": "direct", "binding_composition": None,
                "bindings": [{
                    "source_ref": "s", "column": "CDSCode", "sql_operator": None,
                    "physical_value": None, "data_type": "text", "transformation": "direct",
                }],
                "confidence": "high", "unresolved_reason": None,
            }],
        }
        allowed = {
            "ground_primary_entity", "ground_inc_cond_school_type_intermediate",
            "ground_metric_metric_admin_email", "ground_output_field_admin_email",
        }
        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOC"}, {"name": "AdmEmail1"}, {"name": "CDSCode"},
        ]}]}
        result, events = admit_sif_v2_grounding_proposal(
            bif, sif, proposal, catalog,
            expected_request_id="malicious", allowed_grounding_ids=allowed,
            resolution="system_resolved",
        )
        self.assertEqual(sif, result)
        rejection = next(item for item in events if item["operation"] == "reject_admission")
        issues = {item["issue"] for item in rejection["findings"]}
        self.assertIn("condition_operator_changed", issues)
        self.assertIn("output_metric_binding_mismatch", issues)

    def test_proposal_cannot_touch_slot_outside_orchestrator_scope(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)
        proposal = {
            "request_id": "scope", "base_frame_hash": frame_hash(sif),
            "source_additions": [], "join_additions": [], "primary_entity_source_ref": None,
            "slot_proposals": [{
                "grounding_id": "ground_inc_cond_school_type_unified",
                "binding_mode": "direct", "binding_composition": "all_of",
                "bindings": [], "confidence": "low", "unresolved_reason": "unknown",
            }],
        }
        result, events = apply_sif_v2_grounding_proposal(
            sif, proposal, expected_request_id="scope",
            allowed_grounding_ids={"ground_inc_cond_school_type_intermediate"},
        )
        self.assertEqual(sif, result)
        self.assertIn("slot_outside_allowed_scope", {item.get("reason") for item in events})

    def test_coordinator_routes_a6_through_selected_slot_scope(self) -> None:
        bif = _bif()
        sif = build_sif_v2_scaffold(bif)

        class OutsideScopeAgent:
            def a6_sif_v2_grounding_proposal(self, bif, sif, schema, **kwargs):
                return {
                    "request_id": kwargs["request_id"],
                    "base_frame_hash": frame_hash(sif),
                    "source_additions": [], "join_additions": [],
                    "primary_entity_source_ref": None,
                    "slot_proposals": [{
                        "grounding_id": "ground_inc_cond_school_type_unified",
                        "binding_mode": "direct", "binding_composition": "all_of",
                        "bindings": [], "confidence": "low", "unresolved_reason": "unknown",
                    }],
                }

        schema = load_schema(
            Path(__file__).resolve().parents[2] / "spec" / "sql_intent_frame_v2.schema.json"
        )
        coordinator = SIFV2GroundingCoordinator(OutsideScopeAgent(), sif_schema=schema)
        result = coordinator.ground_selected_slots(
            bif=bif, sif=sif, schema_catalog={"tables": []},
            target_grounding_ids=["ground_inc_cond_school_type_intermediate"],
            decision_ledger=[], resolution="system_resolved", request_id="selected",
        )
        self.assertFalse(result.admitted)
        self.assertEqual(sif, result.sif)
        self.assertIn("slot_outside_allowed_scope", {item.get("reason") for item in result.events})

    def test_orchestrator_clarifies_one_slot_and_owns_termination(self) -> None:
        bif = _bif()
        schema = load_schema(
            Path(__file__).resolve().parents[2] / "spec" / "sql_intent_frame_v2.schema.json"
        )
        catalog = {"tables": [{"name": "schools", "columns": [
            {"name": "SOC", "value_description": "62 - Intermediate/Middle Schools (Public)"},
            {"name": "DOC", "value_description": "54 - Unified School District"},
            {"name": "AdmEmail1", "value_description": ""},
        ]}]}

        def binding(column, value=None, transformation="direct"):
            return {
                "source_ref": "s", "column": column,
                "sql_operator": "equals" if value is not None else None,
                "physical_value": value, "data_type": "text",
                "transformation": transformation,
            }

        class Agents:
            def a4_sif_v2_grounding_proposal(self, case, bif, sif, schema, **kwargs):
                return {
                    "request_id": kwargs["request_id"], "base_frame_hash": frame_hash(sif),
                    "source_additions": [{
                        "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
                        "origin_refs": ["/target_population/primary_entity"],
                    }],
                    "join_additions": [], "primary_entity_source_ref": "s",
                    "slot_proposals": [
                        {"grounding_id": "ground_metric_metric_admin_email",
                         "binding_mode": "direct", "binding_composition": None,
                         "bindings": [binding("AdmEmail1")], "confidence": "high",
                         "unresolved_reason": None},
                        {"grounding_id": "ground_output_field_admin_email",
                         "binding_mode": "direct", "binding_composition": None,
                         "bindings": [binding("AdmEmail1")], "confidence": "high",
                         "unresolved_reason": None},
                    ],
                }

            def a5_sif_v2_discover(self, case, bif, sif, unresolved, ledger):
                candidates = []
                for grounding_id, label in (
                    ("ground_inc_cond_school_type_intermediate", "intermediate-school"),
                    ("ground_inc_cond_school_type_unified", "unified-school"),
                ):
                    if grounding_id in unresolved:
                        candidates.append({
                            "issue_type": (
                                "QUERY_GRAIN" if grounding_id.endswith("intermediate")
                                else "SOURCE_POPULATION"
                            ),
                            "subject_ref": grounding_id,
                            "description": f"Which {label} classification is intended?",
                            "alternatives": [f"{label} classification", "A similarly named category"],
                        })
                return {"candidates": candidates}

            def a5_sif_question(self, case, bif, sif, decision, ledger):
                return {
                    "question": "Which school classification did you mean?",
                    "why_it_matters": "It changes which schools are included.",
                    "options": [{"label": item} for item in decision["choice_domain"]],
                    "allow_free_text": False,
                }

            def a6_sif_v2_grounding_proposal(self, bif, sif, schema, **kwargs):
                proposals = []
                for grounding_id in kwargs["target_grounding_ids"]:
                    column, value = (
                        ("SOC", "62")
                        if grounding_id.endswith("intermediate") else ("DOC", "54")
                    )
                    proposals.append({
                        "grounding_id": grounding_id,
                        "binding_mode": "code_mapping", "binding_composition": "all_of",
                        "bindings": [binding(column, value, "code_mapping")],
                        "confidence": "high", "unresolved_reason": None,
                    })
                return {
                    "request_id": kwargs["request_id"], "base_frame_hash": frame_hash(sif),
                    "source_additions": [], "join_additions": [],
                    "primary_entity_source_ref": None,
                    "slot_proposals": proposals,
                }

        def e2(request, decision):
            selected = "CONFIRM" if decision is None else "OPTION_1"
            return {
                "request_id": request["request_id"], "mode": "SIF_CLARIFICATION",
                "action": "select", "selected_option_ids": [selected],
                "free_text": None, "confidence": "high",
            }

        result = SIFV2ClarificationSession(Agents(), sif_schema=schema).run(
            case={"question_id": 87, "db_id": "california_schools", "question": "emails"},
            bif=bif, schema_catalog=catalog, e2_responder=e2,
        )
        self.assertEqual("ready", result.sif["status"])
        self.assertEqual(2, result.rounds)
        self.assertEqual(2, len(result.decision_ledger))
        self.assertEqual([], validate_sif_v2_invariants(bif, result.sif))

    def test_lowerer_fails_closed_for_unimplemented_complex_semantics(self) -> None:
        bif = _bif()
        bif["calculation"]["steps"] = [{
            "step_id": "calc_rank", "operation": "rank",
            "method": "rank", "input_refs": ["metric_admin_email"],
            "output_ref": "email_rank", "description": "test",
        }]
        sif = build_sif_v2_scaffold(bif)
        # The capability check runs only after structural readiness, so mark the
        # synthetic frame resolved without pretending the calculation is lowerable.
        sif["physical_support"]["sources"] = [{
            "source_id": "s", "kind": "table", "name": "schools", "alias": "s",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        sif["target_population"]["primary_entity_grounding"].update(
            {"source_ref": "s", "status": "system_resolved"}
        )
        for section in (
            sif["target_population"]["inclusion_condition_groundings"],
            sif["metrics"]["metric_groundings"],
            sif["calculation"]["step_groundings"],
            sif["output_expectation"]["field_groundings"],
        ):
            for slot in section:
                slot["bindings"] = [{
                    "source_ref": "s", "column": "AdmEmail1", "sql_operator": None,
                    "physical_value": None, "data_type": "text", "transformation": "direct",
                }]
                slot["status"] = "system_resolved"
        sif["unresolved_groundings"] = []
        sif["status"] = "ready"
        sif["readiness"] = {"state": "ready", "blocking_refs": []}
        with self.assertRaisesRegex(SIFV2LoweringError, "implementation:ground_calculation_calc_rank"):
            lower_sif_v2_to_query_plan(bif, sif)

    def test_aggregate_calculation_is_derived_from_bif_not_rewritten_by_model(self) -> None:
        bif = _bif()
        bif["target_population"]["inclusion_conditions"] = []
        bif["target_population"]["inclusion_logic"]["condition_refs"] = []
        bif["metrics"] = [{
            "metric_id": "consumption", "name": "Consumption",
            "definition": "Customer consumption", "measure_kind": "attribute",
        }]
        bif["calculation"]["steps"] = [{
            "step_id": "average_step", "operation": "aggregate",
            "method": "average", "input_refs": ["consumption"],
            "output_ref": "average_consumption", "description": "Annual average",
        }]
        bif["output_expectation"]["fields"] = [{
            "field_id": "average_output", "label": "Average Consumption",
            "semantic_ref": "average_consumption",
        }]
        sif = build_sif_v2_scaffold(bif)
        sif["physical_support"]["sources"] = [{
            "source_id": "ym", "kind": "table", "name": "yearmonth", "alias": "ym",
            "origin_refs": ["/target_population/primary_entity"],
        }]
        sif["target_population"]["primary_entity_grounding"].update(
            {"source_ref": "ym", "status": "system_resolved"}
        )
        metric = sif["metrics"]["metric_groundings"][0]
        metric.update({
            "bindings": [{
                "source_ref": "ym", "column": "Consumption", "sql_operator": None,
                "physical_value": None, "data_type": "real", "transformation": "direct",
            }],
            "status": "system_resolved",
        })
        sif["unresolved_groundings"] = []
        sif["status"] = "ready"
        sif["readiness"] = {"state": "ready", "blocking_refs": []}
        sql = compile_sif(lower_sif_v2_to_query_plan(bif, sif))
        self.assertIn('AVG("ym"."Consumption")', sql)
        self.assertNotIn("average_step", sql)

        sif["calculation"]["step_groundings"][0]["implementation"]["operator"] = "sum"
        issues = {item["issue"] for item in validate_sif_v2_invariants(bif, sif)}
        self.assertIn("bif_derived_implementation_changed", issues)


if __name__ == "__main__":
    unittest.main()
