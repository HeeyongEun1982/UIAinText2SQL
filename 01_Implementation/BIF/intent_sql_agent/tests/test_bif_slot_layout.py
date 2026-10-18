from __future__ import annotations

import unittest
import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.services.bif_slot_layout import (
    attach_bif_slot_layout,
    find_semantic_slot_id,
)
from intent_sql_agent.services.sif_v2 import build_sif_v2_scaffold
from intent_sql_agent.services.frame_state import frame_hash
from intent_sql_agent.services.sif_v2_patch import apply_sif_v2_grounding_proposal


def _bif() -> dict:
    return {
        "schema_version": "1.1", "frame_id": "bif_test",
        "source": {"question_id": 87, "db_id": "db", "question": "emails"},
        "lifecycle": {"state": "confirmed", "revision": 1},
        "target_population": {
            "primary_entity": "school",
            "inclusion_conditions": [
                {"condition_id": "cond_school_type_intermediate", "operator": "equals", "value": "Intermediate/Middle Schools"},
                {"condition_id": "cond_school_type_unified", "operator": "equals", "value": "Unified Schools"},
            ],
            "exclusion_conditions": [],
        },
        "metrics": [{"metric_id": "metric_admin_email"}],
        "calculation": {"steps": []}, "granularity": {"dimensions": []},
        "scope": {"selections": []}, "time_basis": {"periods": []},
        "comparisons": [],
        "output_expectation": {
            "fields": [{"field_id": "field_admin_email", "semantic_ref": "metric_admin_email"}],
            "ordering": [],
        },
    }


class BIFSlotLayoutTests(unittest.TestCase):
    def test_unknown_future_cqd_root_is_preserved_not_dropped(self) -> None:
        cqd = {
            "objective": {"description": "List schools"},
            "outputs": [{"content": "school name"}],
            "future_requirement": {"meaning": "apply a future semantic rule"},
        }
        bif, report = attach_bif_slot_layout(cqd, _bif())
        future = next(
            item for item in bif["slot_layout"]["slots"]
            if item["source_refs"] == ["/future_requirement"]
        )
        self.assertEqual("grounding", future["role"])
        self.assertEqual("complete", report["status"])

    def test_predicate_boolean_tree_is_preserved_as_logical_slot(self) -> None:
        cqd = {
            "objective": {"description": "List schools"},
            "predicates": {
                "root": {"operator": "and", "children": ["p1", "p2"]},
                "atoms": [{"value": "A"}, {"value": "B"}],
            },
            "outputs": [{"content": "school name"}],
        }
        bif, _ = attach_bif_slot_layout(cqd, _bif())
        logic = next(
            item for item in bif["slot_layout"]["slots"]
            if item["source_refs"] == ["/predicates/root"]
        )
        self.assertEqual("logic", logic["role"])
        sif = build_sif_v2_scaffold(bif)
        coverage = next(
            item for item in sif["semantic_slot_coverage"]
            if item["semantic_slot_id"] == logic["slot_id"]
        )
        self.assertEqual("not_applicable", coverage["status"])

    def test_cqd_payload_is_preserved_with_typed_slot_and_constraint(self) -> None:
        cqd = {
            "objective": {"description": "List administrator emails"},
            "outputs": [{
                "content": "valid administrator email addresses",
                "output_type": "list",
                "presentation": "separate_fields",
                "source_text": "valid e-mail addresses",
            }],
            "traceability": {"state_hash": "cqd-hash"},
        }
        bif = _bif()
        attached, report = attach_bif_slot_layout(cqd, bif)
        self.assertEqual("complete", report["status"])
        output = next(
            item for item in attached["slot_layout"]["slots"]
            if item["role"] == "output"
        )
        self.assertEqual(cqd["outputs"][0], output["semantic_payload"])
        types = {item["type"] for item in output["constraints"]}
        self.assertIn("multiplicity", types)
        self.assertIn("presentation", types)
        self.assertEqual(["field_admin_email"], output["legacy_refs"])

    def test_sif_grounding_retains_same_semantic_slot_id_and_constraint_coverage(self) -> None:
        cqd = {
            "populations": [{"content": "schools"}],
            "predicates": {"atoms": [
                {"content": "Intermediate schools", "operator": "equals", "value": "Intermediate/Middle Schools"},
                {"content": "Unified schools", "operator": "equals", "value": "Unified Schools"},
            ]},
            "outputs": [{"content": "administrator email", "output_type": "list"}],
        }
        bif, _ = attach_bif_slot_layout(cqd, _bif())
        sif = build_sif_v2_scaffold(bif)
        condition = sif["target_population"]["inclusion_condition_groundings"][0]
        expected = find_semantic_slot_id(bif, "cond_school_type_intermediate")
        self.assertEqual(expected, condition["semantic_slot_id"])
        self.assertTrue(condition["constraint_coverage"])
        coverage_ids = {
            item["semantic_slot_id"] for item in sif["semantic_slot_coverage"]
        }
        self.assertEqual(
            {item["slot_id"] for item in bif["slot_layout"]["slots"]},
            coverage_ids,
        )

    def test_grounding_rejects_missing_constraint_result(self) -> None:
        cqd = {
            "objective": {"description": "List schools"},
            "predicates": {"atoms": [{"content": "Intermediate schools", "operator": "equals", "value": "Intermediate/Middle Schools"}]},
            "outputs": [{"content": "administrator email"}],
        }
        bif, _ = attach_bif_slot_layout(cqd, _bif())
        sif = build_sif_v2_scaffold(bif)
        slot = sif["target_population"]["inclusion_condition_groundings"][0]
        proposal = {
            "request_id": "missing-constraint",
            "base_frame_hash": frame_hash(sif),
            "source_additions": [{
                "source_id": "s", "kind": "table", "name": "schools",
                "alias": "s", "origin_refs": [slot["condition_ref"]],
            }],
            "join_additions": [], "primary_entity_source_ref": None,
            "primary_constraint_results": [],
            "slot_proposals": [{
                "grounding_id": slot["grounding_id"], "binding_mode": "direct",
                "binding_composition": "all_of",
                "bindings": [{
                    "source_ref": "s", "column": "SOC", "sql_operator": "equals",
                    "physical_value": "62", "data_type": "text", "transformation": "direct",
                }],
                "constraint_results": [], "confidence": "high", "unresolved_reason": None,
            }],
        }
        result, events = apply_sif_v2_grounding_proposal(
            sif, proposal, expected_request_id="missing-constraint",
            allowed_grounding_ids={slot["grounding_id"]},
        )
        self.assertEqual(sif, result)
        self.assertIn("constraint_coverage_mismatch", {item.get("reason") for item in events})

    def test_grounded_constraint_requires_concrete_evidence(self) -> None:
        cqd = {
            "objective": {"description": "List schools"},
            "predicates": {"atoms": [{"content": "Intermediate schools", "operator": "equals", "value": "Intermediate/Middle Schools"}]},
            "outputs": [{"content": "administrator email"}],
        }
        bif, _ = attach_bif_slot_layout(cqd, _bif())
        sif = build_sif_v2_scaffold(bif)
        slot = sif["target_population"]["inclusion_condition_groundings"][0]
        constraint_results = [{
            "constraint_id": item["constraint_id"], "status": "grounded",
            "evidence": [], "reason": None,
        } for item in slot["constraint_coverage"]]
        proposal = {
            "request_id": "no-evidence", "base_frame_hash": frame_hash(sif),
            "source_additions": [{"source_id": "s", "kind": "table", "name": "schools", "alias": "s", "origin_refs": [slot["condition_ref"]]}],
            "join_additions": [], "primary_entity_source_ref": None,
            "primary_constraint_results": [],
            "slot_proposals": [{
                "grounding_id": slot["grounding_id"], "binding_mode": "direct",
                "binding_composition": "all_of",
                "bindings": [{"source_ref": "s", "column": "SOC", "sql_operator": "equals", "physical_value": "62", "data_type": "text", "transformation": "direct"}],
                "constraint_results": constraint_results,
                "confidence": "high", "unresolved_reason": None,
            }],
        }
        result, events = apply_sif_v2_grounding_proposal(
            sif, proposal, expected_request_id="no-evidence",
            allowed_grounding_ids={slot["grounding_id"]},
        )
        self.assertEqual(sif, result)
        self.assertIn("grounded_constraint_without_evidence", {item.get("reason") for item in events})


if __name__ == "__main__":
    unittest.main()
