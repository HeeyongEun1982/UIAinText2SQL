from __future__ import annotations

import copy
import unittest

from intent_sql_agent.services.bif_layout_completion_orchestrator import (
    BIFLayoutCompletionOrchestrator,
)
from intent_sql_agent.services.cqd_query_layout_compiler import (
    compile_cqd_query_layout,
)


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
    "scopes": [], "deferred_grounding": [],
}


class BIFLayoutCompletionOrchestratorTests(unittest.TestCase):
    @staticmethod
    def _equivalent_population_patch(layout, gap):
        source = layout["sources"][0]
        location_field = next(
            item for item in layout["fields"] if item["role"] == "location"
        )
        location_predicate = next(
            item for item in layout["predicates"]
            if item["field"] == location_field["id"]
        )
        refs = gap["slot_semantic_refs"]
        return {
            "cqd_ref": gap["cqd_ref"], "operation": "add",
            "target_slot_ids": gap["slot_ids"],
            "evidence_ids": ["cqd:/populations/0"],
            "reason": "confirm the already represented population",
            "sources": [{"id": source["id"], "role": source["role"]}],
            "fields": [{
                "id": location_field["id"], "role": location_field["role"],
                "source": location_field["source"],
                "semantic_ref": refs[location_field["id"]],
                "aggregate": location_field["aggregate"],
                "distinct": location_field["distinct"],
            }],
            "predicates": [{
                "id": location_predicate["id"],
                "field": location_predicate["field"],
                "role": location_predicate["role"],
                "semantic_ref": refs[location_predicate["id"]],
                "operator": location_predicate["operator"],
                "value": location_predicate["value"],
            }],
            "select": list(layout["select"]),
            "group_by": [], "order_by": [], "limit": None,
        }

    def test_equivalent_existing_population_is_completed_not_retried(self) -> None:
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            return {"patches": [self._equivalent_population_patch(layout, gaps[0])]}

        _, report = BIFLayoutCompletionOrchestrator().run(
            original_question=(
                "Please list the zip code of all the charter schools in "
                "Fresno County Office of Education."
            ),
            confirmed_cqd=Q2_CQD,
            propose=propose,
        )
        self.assertEqual(calls, 1)
        self.assertTrue(report["finalized_once"])
        result = report["history"][1]["admitted"]["results"][0]
        self.assertEqual(result["outcome"], "ALREADY_PRESENT_EQUIVALENT")
        self.assertEqual(result["action"], "complete")

    def test_completed_population_is_not_sent_with_next_retry(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["aggregations"] = [{"type": "mode", "target": "zip code"}]
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            refs = [item["cqd_ref"] for item in gaps]
            if calls == 1:
                population = next(
                    item for item in gaps if item["cqd_ref"] == "/populations/0"
                )
                return {"patches": [
                    self._equivalent_population_patch(layout, population),
                    {
                        "cqd_ref": "/aggregations/0", "operation": "add",
                        "target_slot_ids": [],
                        "evidence_ids": ["cqd:/aggregations/0"],
                        "reason": "unsupported limit", "sources": [], "fields": [],
                        "predicates": [], "select": [], "group_by": [],
                        "order_by": [], "limit": 1,
                    },
                ]}
            self.assertEqual(refs, ["/aggregations/0"])
            return {"patches": [{
                "cqd_ref": "/aggregations/0",
                "operation": "clarification_required",
                "target_slot_ids": [],
                "evidence_ids": ["cqd:/aggregations/0"],
                "reason": "tie policy is not specified",
                "sources": [], "fields": [], "predicates": [],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator().run(
            original_question="Return the most common zip code for charter schools.",
            confirmed_cqd=cqd,
            propose=propose,
        )
        self.assertEqual(calls, 2)
        population = next(
            item for item in report["coverage"]["requirements"]
            if item["cqd_ref"] == "/populations/0"
        )
        self.assertEqual(population["status"], "mapped")
        self.assertEqual(
            population["completion_result"], "ALREADY_PRESENT_EQUIVALENT"
        )

    def test_five_identical_retries_are_deferred_without_raising(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["populations"] = []
        cqd["aggregations"] = [{"type": "mode", "target": "zip code"}]
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            return {"patches": [{
                "cqd_ref": "/aggregations/0", "operation": "add",
                "target_slot_ids": [],
                "evidence_ids": ["cqd:/aggregations/0"],
                "reason": "unsupported limit", "sources": [], "fields": [],
                "predicates": [], "select": [], "group_by": [],
                "order_by": [], "limit": 1,
            }]}

        _, report = BIFLayoutCompletionOrchestrator().run(
            original_question="Return the most common zip code.",
            confirmed_cqd=cqd,
            propose=propose,
        )
        self.assertEqual(calls, 5)
        warning = next(item for item in report["history"] if item["source"] == "no_progress_warning")
        self.assertEqual(warning["diagnostic"]["repeat_count"], 5)
        self.assertEqual(warning["diagnostic"]["classification"], "INVALID_PROPOSAL")
        self.assertFalse(report["finalized_once"])

    def test_five_empty_patch_responses_are_deferred_without_raising(self) -> None:
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            return {"patches": []}

        _, report = BIFLayoutCompletionOrchestrator().run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD,
            propose=propose,
        )
        self.assertEqual(calls, 5)
        warning = next(item for item in report["history"] if item["source"] == "no_progress_warning")
        self.assertEqual(
            warning["diagnostic"]["classification"], "MISSING_PATCH"
        )
        self.assertEqual(
            warning["diagnostic"]["last_rejection_reason"],
            "missing_patch_for_open_gap",
        )

    def test_valid_field_is_kept_and_only_invalid_predicate_operator_is_retried(self) -> None:
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            gap = gaps[0]
            if calls == 2:
                self.assertEqual(gap["processing_state"], "PARTIAL")
                self.assertIn(
                    "field:field_charter_status.source",
                    gap["completed_components"],
                )
                self.assertIn(
                    "predicate:predicate_charter.field",
                    gap["completed_components"],
                )
                self.assertEqual(
                    [item["component"] for item in gap["remaining_components"]],
                    ["predicate:predicate_charter.operator"],
                )
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": gap["slot_ids"],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "complete the charter population condition",
                "sources": [],
                "fields": ([{
                    "id": "field_charter_status", "role": "charter status",
                    "source": layout["sources"][0]["id"],
                    "semantic_ref": "/populations/0/entity",
                    "aggregate": None, "distinct": False,
                }] if calls == 1 else []),
                "predicates": [{
                    "id": "predicate_charter", "field": "field_charter_status",
                    "role": "charter schools",
                    "semantic_ref": "/populations/0/entity",
                    "operator": "NOT_EQUALS" if calls == 1 else "equals",
                    "value": "charter schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        layout, report = BIFLayoutCompletionOrchestrator().run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD,
            propose=propose,
        )
        self.assertEqual(calls, 2)
        self.assertTrue(report["finalized_once"])
        self.assertEqual(
            len([
                item for item in layout["fields"]
                if item["id"] == "field_charter_status"
            ]),
            1,
        )
        predicate = next(
            item for item in layout["predicates"]
            if item["id"] == "predicate_charter"
        )
        self.assertEqual(predicate["operator"], "equals")

    def test_existing_predicate_conflict_identifies_only_operator_component(self) -> None:
        def propose(layout, gaps, round_number):
            predicate = layout["predicates"][0]
            gap = gaps[0]
            return {"patches": [{
                "cqd_ref": gap["cqd_ref"], "operation": "add",
                "target_slot_ids": gap["slot_ids"],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "conflicting operator",
                "sources": [], "fields": [],
                "predicates": [{
                    "id": predicate["id"], "field": predicate["field"],
                    "role": predicate["role"],
                    "semantic_ref": gap["slot_semantic_refs"][predicate["id"]],
                    "operator": "LIKE", "value": predicate["value"],
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD,
            propose=propose,
        )
        result = report["history"][1]["admitted"]["results"][0]
        self.assertEqual(result["outcome"], "ID_CONFLICT")
        self.assertEqual(
            [item["component"] for item in result["remaining_components"]],
            ["predicate:predicate_population_1_location.operator"],
        )
        self.assertIn(
            "predicate:predicate_population_1_location.field",
            result["completed_components"],
        )

    def test_existing_predicate_conflict_identifies_only_value_component(self) -> None:
        def propose(layout, gaps, round_number):
            predicate = layout["predicates"][0]
            gap = gaps[0]
            return {"patches": [{
                "cqd_ref": gap["cqd_ref"], "operation": "add",
                "target_slot_ids": gap["slot_ids"],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "conflicting predicate value",
                "sources": [], "fields": [],
                "predicates": [{
                    "id": predicate["id"], "field": predicate["field"],
                    "role": predicate["role"],
                    "semantic_ref": gap["slot_semantic_refs"][predicate["id"]],
                    "operator": predicate["operator"],
                    "value": "a different office",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD,
            propose=propose,
        )
        result = report["history"][1]["admitted"]["results"][0]
        self.assertEqual(result["outcome"], "ID_CONFLICT")
        self.assertEqual(
            [item["component"] for item in result["remaining_components"]],
            ["predicate:predicate_population_1_location.value"],
        )
        self.assertIn(
            "predicate:predicate_population_1_location.field",
            result["completed_components"],
        )
        self.assertIn(
            "predicate:predicate_population_1_location.operator",
            result["completed_components"],
        )

    def test_partial_admission_resets_five_repeat_counter(self) -> None:
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            gap = gaps[0]
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": gap["slot_ids"],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "repeat an invalid predicate after adding its field",
                "sources": [],
                "fields": [{
                    "id": "field_charter_status", "role": "charter status",
                    "source": layout["sources"][0]["id"],
                    "semantic_ref": "/populations/0/entity",
                    "aggregate": None, "distinct": False,
                }],
                "predicates": [{
                    "id": "predicate_charter", "field": "field_charter_status",
                    "role": "charter schools",
                    "semantic_ref": "/populations/0/entity",
                    "operator": "NOT_EQUALS", "value": "charter schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator().run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD,
            propose=propose,
        )
        self.assertEqual(calls, 6)
        warning = next(item for item in report["history"] if item["source"] == "no_progress_warning")
        self.assertEqual(warning["diagnostic"]["repeat_count"], 5)
        self.assertEqual(
            warning["diagnostic"]["last_rejection_reason"],
            "generated_operator_not_supported_by_evidence",
        )

    def test_ir_compiler_preserves_q2_and_cannot_invent_mode(self) -> None:
        layout, coverage = compile_cqd_query_layout(
            original_question="Please list the zip code of all charter schools.",
            confirmed_cqd=Q2_CQD,
        )
        selected = next(item for item in layout["fields"] if item["id"] in layout["select"])
        self.assertEqual(selected["role"], "zip code")
        self.assertEqual(layout["group_by"], [])
        self.assertEqual(layout["order_by"], [])
        self.assertIsNone(layout["limit"])
        self.assertIn("Fresno County Office of Education", {
            item["value"] for item in layout["predicates"]
        })
        self.assertEqual(coverage["status"], "needs_completion")

    def test_orchestrator_admits_only_missing_population_patch(self) -> None:
        def propose(layout, gaps, round_number):
            source_id = layout["sources"][0]["id"]
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "charter is a restrictive population qualifier",
                "sources": [],
                "fields": [{
                    "id": "field_charter_status", "role": "charter status",
                    "source": source_id, "semantic_ref": "/populations/0/entity",
                    "aggregate": None, "distinct": False,
                }],
                "predicates": [{
                    "id": "predicate_charter", "field": "field_charter_status",
                    "role": "charter schools", "semantic_ref": "/populations/0/entity",
                    "operator": "equals",
                    "value": "charter schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        layout, report = BIFLayoutCompletionOrchestrator().run(
            original_question="Please list the zip code of all charter schools.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertIn("predicate_charter", {item["id"] for item in layout["predicates"]})
        self.assertEqual(layout["group_by"], [])
        self.assertEqual(report["authority"], "BIFLayoutCompletionOrchestrator")
        self.assertTrue(report["finalized_once"])

    def test_rejected_patch_receives_feedback_and_can_retry(self) -> None:
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            if calls == 1:
                patch = {
                    "cqd_ref": "/populations/0", "operation": "add",
                    "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                    "reason": "invalidly touch select", "sources": [], "fields": [],
                    "predicates": [], "select": ["field_not_owned_by_population"],
                    "group_by": [], "order_by": [], "limit": None,
                }
            else:
                self.assertEqual(gaps[0]["last_rejection_reason"], "select_not_allowed_for_cqd_root")
                patch = {
                    "cqd_ref": "/populations/0", "operation": "add",
                    "target_slot_ids": [layout["sources"][0]["id"]],
                    "evidence_ids": ["cqd:/populations/0"],
                    "reason": "add only missing entity constraint", "sources": [],
                    "fields": [{
                        "id": "field_charter_status", "role": "charter status",
                        "source": layout["sources"][0]["id"],
                        "semantic_ref": "/populations/0/entity",
                        "aggregate": None, "distinct": False,
                    }],
                    "predicates": [{
                        "id": "predicate_charter", "field": "field_charter_status",
                        "role": "charter schools", "semantic_ref": "/populations/0/entity",
                        "operator": "equals", "value": "charter schools",
                    }],
                    "select": [], "group_by": [], "order_by": [], "limit": None,
                }
            return {"patches": [patch]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=2).run(
            original_question="Please list charter school zip codes.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertEqual(calls, 2)
        self.assertTrue(report["finalized_once"])

    def test_rejected_patch_cannot_overwrite_existing_slot(self) -> None:
        def propose(layout, gaps, round_number):
            existing = layout["fields"][0]
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "attempted overwrite",
                "sources": [],
                "fields": [{
                    "id": existing["id"], "role": "wrong", "source": layout["sources"][0]["id"],
                    "semantic_ref": "/populations/0/entity", "aggregate": None, "distinct": False,
                }],
                "predicates": [],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        layout, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="q", confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertNotIn("wrong", {item["role"] for item in layout["fields"]})
        admitted = report["history"][1]["admitted"]
        self.assertEqual(admitted["applied_count"], 0)
        self.assertEqual(admitted["results"][0]["outcome"], "ID_CONFLICT")
        self.assertEqual(admitted["results"][0]["action"], "retry")

    def test_keep_cannot_close_an_unproven_population_gap(self) -> None:
        def propose(layout, gaps, round_number):
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "keep",
                "target_slot_ids": gaps[0]["slot_ids"],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "claim source is sufficient",
                "sources": [], "fields": [], "predicates": [],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertFalse(report["finalized_once"])
        self.assertEqual(
            report["history"][1]["admitted"]["rejected"][0]["reason"],
            "invalid_keep",
        )

    def test_unsupported_operator_is_rejected_even_with_valid_citation(self) -> None:
        def propose(layout, gaps, round_number):
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                "reason": "operator attack", "sources": [],
                "fields": [{
                    "id": "field_charter_status", "role": "charter status",
                    "source": layout["sources"][0]["id"],
                    "semantic_ref": "/populations/0/entity",
                    "aggregate": None, "distinct": False,
                }],
                "predicates": [{
                    "id": "predicate_charter", "field": "field_charter_status",
                    "role": "charter schools", "semantic_ref": "/populations/0/entity",
                    "operator": "NOT_EQUALS",
                    "value": "charter schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter schools.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertFalse(report["finalized_once"])
        self.assertEqual(
            report["history"][1]["admitted"]["rejected"][0]["reason"],
            "generated_operator_not_supported_by_evidence",
        )
        self.assertEqual(
            report["history"][1]["admitted"]["results"][0]["outcome"],
            "PARTIALLY_MAPPED",
        )

    def test_population_predicate_cannot_bind_an_unowned_existing_field(self) -> None:
        def propose(layout, gaps, round_number):
            zip_field = next(
                item["id"] for item in layout["fields"] if item["role"] == "zip code"
            )
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                "reason": "attach population meaning to output field",
                "sources": [], "fields": [],
                "predicates": [{
                    "id": "predicate_charter", "field": zip_field,
                    "role": "charter schools", "semantic_ref": "/populations/0/entity",
                    "operator": "equals",
                    "value": "charter schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter school zip codes.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertFalse(report["finalized_once"])
        self.assertEqual(
            report["history"][1]["admitted"]["rejected"][0]["reason"],
            "predicate_uses_field_owned_by_another_cqd_requirement",
        )

    def test_population_predicate_semantic_ref_must_match_owned_field(self) -> None:
        def propose(layout, gaps, round_number):
            location_field = next(
                item["id"] for item in layout["fields"] if item["role"] == "location"
            )
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                "reason": "attach entity classification to location",
                "sources": [], "fields": [],
                "predicates": [{
                    "id": "predicate_charter", "field": location_field,
                    "role": "charter schools", "semantic_ref": "/populations/0/entity",
                    "operator": "equals",
                    "value": "charter schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter school zip codes.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        self.assertEqual(
            report["history"][1]["admitted"]["rejected"][0]["reason"],
            "predicate_semantic_ref_does_not_match_field",
        )

    def test_aggregation_clause_cannot_reference_an_unowned_field(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["aggregations"] = [{"type": "mode", "target": "zip code"}]

        def propose(layout, gaps, round_number):
            location_field = next(
                item["id"] for item in layout["fields"] if item["role"] == "location"
            )
            return {"patches": [{
                "cqd_ref": "/aggregations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/aggregations/0"],
                "reason": "attach mode to location", "sources": [], "fields": [],
                "predicates": [], "select": [], "group_by": [location_field],
                "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Return the most common zip code.",
            confirmed_cqd=cqd, propose=propose,
        )
        self.assertEqual(
            report["history"][1]["admitted"]["rejected"][0]["reason"],
            "clause_uses_field_owned_by_another_cqd_requirement",
        )

    def test_time_predicate_is_preserved_with_existing_boolean_atoms(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["predicates"] = {
            "root": "AND",
            "atoms": [{
                "condition_id": "location", "dimension": "location",
                "operator": "equals", "value": "Fresno",
                "applies_to": "population",
            }],
        }
        cqd["time_constraints"] = [{
            "time_dimension": "school year",
            "start_date": "2020-01-01", "end_date": "2020-12-31",
        }]
        layout, _ = compile_cqd_query_layout(
            original_question="List Fresno charter schools during 2020.",
            confirmed_cqd=cqd,
        )
        time_id = next(
            item["id"] for item in layout["predicates"]
            if item["role"] == "school year"
        )
        self.assertIn(time_id, layout["where"])
        self.assertIn("predicate_location", layout["where"])

    def test_clarification_revises_cqd_then_recompiles(self) -> None:
        calls = 0

        def propose(layout, gaps, round_number):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"patches": [{
                    "cqd_ref": "/populations/0", "operation": "clarification_required",
                    "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                    "reason": "population meaning needs E2", "sources": [], "fields": [],
                    "predicates": [], "select": [], "group_by": [], "order_by": [],
                    "limit": None,
                }]}
            entity = gaps[0]["payload"]["entity"]
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                "reason": "use revised CQD", "sources": [],
                "fields": [{
                    "id": "field_confirmed_status", "role": f"{entity} status",
                    "source": layout["sources"][0]["id"],
                    "semantic_ref": "/populations/0/entity",
                    "aggregate": None, "distinct": False,
                }],
                "predicates": [{
                    "id": "predicate_confirmed", "field": "field_confirmed_status",
                    "role": entity, "semantic_ref": "/populations/0/entity",
                    "operator": "equals", "value": entity,
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        def clarify(cqd, gaps):
            revised = copy.deepcopy(cqd)
            revised["populations"][0]["entity"] = "confirmed charter schools"
            revised.setdefault("traceability", {})["layout_decisions"] = [{
                "decision_id": "layout-e2-1", "cqd_ref": "/populations/0",
                "evidence": "confirmed charter schools",
            }]
            return revised

        layout, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list charter schools.", confirmed_cqd=Q2_CQD,
            propose=propose, clarify=clarify,
        )
        self.assertTrue(report["finalized_once"])
        self.assertEqual(report["clarification_cycles"], 1)
        self.assertEqual(report["confirmed_cqd"]["populations"][0]["entity"], "confirmed charter schools")
        self.assertIn("predicate_confirmed", {item["id"] for item in layout["predicates"]})

    def test_mode_without_tie_policy_does_not_invent_limit(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["aggregations"] = [{"type": "mode", "target": "zip code"}]
        layout, coverage = compile_cqd_query_layout(
            original_question="Return the most common zip code.", confirmed_cqd=cqd
        )
        requirement = next(
            item for item in coverage["requirements"]
            if item["cqd_ref"] == "/aggregations/0"
        )
        self.assertIsNone(layout["limit"])
        self.assertEqual(requirement["status"], "needs_review")
        self.assertEqual(requirement["clarification_family"], "TIE_POLICY")

    def test_limit_without_tie_policy_evidence_is_rejected(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["aggregations"] = [{"type": "mode", "target": "zip code"}]

        def propose(layout, gaps, round_number):
            return {"patches": [{
                "cqd_ref": "/aggregations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/aggregations/0"],
                "reason": "invent a top-one policy", "sources": [], "fields": [],
                "predicates": [], "select": [], "group_by": [], "order_by": [],
                "limit": 1,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Return the most common zip code.",
            confirmed_cqd=cqd, propose=propose,
        )
        admitted = report["history"][1]["admitted"]
        self.assertEqual(admitted["applied_count"], 0)
        self.assertEqual(
            admitted["rejected"][0]["reason"],
            "generated_limit_not_supported_by_evidence",
        )

    def test_aggregate_distinct_and_order_direction_require_evidence(self) -> None:
        def population_attack(*, aggregate=None, distinct=False):
            def propose(layout, gaps, round_number):
                return {"patches": [{
                    "cqd_ref": "/populations/0", "operation": "add",
                    "target_slot_ids": [], "evidence_ids": ["cqd:/populations/0"],
                    "reason": "field semantics attack", "sources": [],
                    "fields": [{
                        "id": "field_charter", "role": "charter status",
                        "source": layout["sources"][0]["id"],
                        "semantic_ref": "/populations/0/entity",
                        "aggregate": aggregate, "distinct": distinct,
                    }],
                    "predicates": [{
                        "id": "predicate_charter", "field": "field_charter",
                        "role": "charter schools", "semantic_ref": "/populations/0/entity",
                        "operator": "equals",
                        "value": "charter schools",
                    }],
                    "select": [], "group_by": [], "order_by": [], "limit": None,
                }]}
            return propose

        attacks = [
            (population_attack(aggregate="sum"), "generated_aggregate_not_supported_by_evidence"),
            (population_attack(distinct=True), "generated_distinct_not_supported_by_evidence"),
        ]
        for propose, expected in attacks:
            with self.subTest(expected=expected):
                _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
                    original_question="Please list charter schools.",
                    confirmed_cqd=Q2_CQD, propose=propose,
                )
                self.assertEqual(
                    report["history"][1]["admitted"]["rejected"][0]["reason"],
                    expected,
                )

        mode_cqd = copy.deepcopy(Q2_CQD)
        mode_cqd["aggregations"] = [{"type": "mode", "target": "zip code"}]

        def bad_order(layout, gaps, round_number):
            aggregate_gap = next(item for item in gaps if item["cqd_ref"] == "/aggregations/0")
            field_id = aggregate_gap["slot_ids"][0]
            return {"patches": [{
                "cqd_ref": "/aggregations/0", "operation": "add",
                "target_slot_ids": [], "evidence_ids": ["cqd:/aggregations/0"],
                "reason": "direction attack", "sources": [], "fields": [],
                "predicates": [], "select": [], "group_by": [],
                "order_by": [{"field": field_id, "aggregate": "count", "direction": "asc"}],
                "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Return the most common zip code.",
            confirmed_cqd=mode_cqd, propose=bad_order,
        )
        self.assertEqual(
            report["history"][1]["admitted"]["rejected"][0]["reason"],
            "generated_order_direction_not_supported_by_evidence",
        )

    def test_e2_evidence_is_bound_to_its_cqd_path(self) -> None:
        cqd = copy.deepcopy(Q2_CQD)
        cqd["traceability"] = {
            "active_decision_ids": ["unrelated"],
            "layout_decisions": [{
                "decision_id": "population-e2", "cqd_ref": "/populations/0",
                "evidence": "charter schools",
            }],
        }
        _, coverage = compile_cqd_query_layout(
            original_question="Please list charter schools.", confirmed_cqd=cqd
        )
        population = next(
            item for item in coverage["requirements"]
            if item["cqd_ref"] == "/populations/0"
        )
        self.assertIn("decision:population-e2", population["evidence_ids"])
        self.assertNotIn("decision:unrelated", population["evidence_ids"])

    def test_patch_with_citation_but_invented_semantics_is_rejected(self) -> None:
        def propose(layout, gaps, round_number):
            return {"patches": [{
                "cqd_ref": "/populations/0", "operation": "add",
                "target_slot_ids": [],
                "evidence_ids": ["cqd:/populations/0"],
                "reason": "invent a school type",
                "sources": [],
                "fields": [{
                    "id": "field_private_status", "role": "private status",
                    "source": layout["sources"][0]["id"],
                    "semantic_ref": "/populations/0/entity",
                    "aggregate": None, "distinct": False,
                }],
                "predicates": [{
                    "id": "predicate_private", "field": "field_private_status",
                    "role": "private schools", "semantic_ref": "/populations/0/entity",
                    "operator": "equals",
                    "value": "private schools",
                }],
                "select": [], "group_by": [], "order_by": [], "limit": None,
            }]}

        _, report = BIFLayoutCompletionOrchestrator(max_rounds=1).run(
            original_question="Please list the zip code of all charter schools.",
            confirmed_cqd=Q2_CQD, propose=propose,
        )
        admitted = report["history"][1]["admitted"]
        self.assertEqual(admitted["applied_count"], 0)
        self.assertEqual(
            admitted["rejected"][0]["reason"],
            "generated_semantics_not_supported_by_evidence",
        )

    def test_all_cqd_roots_receive_explicit_coverage(self) -> None:
        cqd = {
            **Q2_CQD,
            "expressions": [{"name": "rate", "formula": "{a} / {b}"}],
            "comparisons": [{"dimension": "rate", "operator": ">", "value": 1}],
            "scopes": [{"entity": "active charter schools"}],
            "deferred_grounding": [{"concept": "status code"}],
        }
        _, coverage = compile_cqd_query_layout(
            original_question="List rates above one for active charter schools.",
            confirmed_cqd=cqd,
        )
        refs = {item["cqd_ref"]: item["status"] for item in coverage["requirements"]}
        self.assertIn("/expressions/0", refs)
        self.assertIn("/comparisons/0", refs)
        self.assertIn("/scopes/0", refs)
        self.assertEqual(refs["/deferred_grounding/0"], "deferred_to_sif")
        self.assertEqual(
            coverage["original_question"],
            "List rates above one for active charter schools.",
        )


if __name__ == "__main__":
    unittest.main()
