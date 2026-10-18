from __future__ import annotations

import unittest

from intent_sql_agent.services.clarification_orchestrator import (
    ClarificationOrchestrator,
    ClarificationStateError,
)


class ClarificationOrchestratorTests(unittest.TestCase):
    def test_resolving_one_finding_preserves_other_pending_findings(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        orchestrator.register_findings(
            [
                {"issue": "location", "path": "/target_population/location", "description": "location"},
                {"issue": "logic", "path": "/target_population/type", "description": "logic"},
                {"issue": "presentation", "path": "/output_expectation/fields/0", "description": "output"},
            ],
            source="validator",
        )
        first = orchestrator.next_pending()
        self.assertIsNotNone(first)
        request = orchestrator.build_request(
            first["decision_id"],
            {
                "question": "Which location meaning?",
                "why_it_matters": "It changes the included population.",
                "options": [{"label": "City"}, {"label": "District"}],
                "allow_free_text": False,
            },
        )
        orchestrator.bind_request(
            first["decision_id"],
            request,
            round_number=1,
        )
        orchestrator.resolve(
            first["decision_id"],
            {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["OPTION_1"],
            },
            round_number=1,
        )
        self.assertEqual(2, len(orchestrator.pending()))
        self.assertFalse(orchestrator.can_terminate())

    def test_final_confirmation_is_rejected_while_pending_exists(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        orchestrator.register_findings(
            [{"issue": "logic", "path": "/scope", "description": "logic"}],
            source="validator",
        )
        with self.assertRaises(ClarificationStateError):
            orchestrator.record_final_confirmation(
                {"gap_id": "final_confirmation"},
                {"action": "select", "selected_option_ids": ["confirm"]},
                round_number=1,
            )

    def test_unknown_model_issue_cannot_create_a_decision(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        orchestrator.register_candidates(
            [{
                "issue_type": "MODEL_INVENTED_TYPE",
                "subject_ref": "/metrics/0",
                "description": "Invented issue.",
                "alternatives": ["A", "B"],
            }],
            source="a2",
            round_number=1,
            allowed_paths={"/metrics/0"},
        )
        self.assertIsNone(orchestrator.next_pending())
        self.assertFalse(orchestrator.can_terminate())

    def test_semantically_identical_candidates_are_deduplicated(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        candidate = {
            "issue_type": "METRIC_DEFINITION",
            "subject_ref": "/metrics/0",
            "description": "Choose the intended business meaning.",
            "alternatives": ["standard format", "verified deliverability"],
        }
        first = orchestrator.register_candidates([candidate], source="a2", round_number=1)
        paraphrase = {
            **candidate,
            "description": "Select which business definition is intended.",
        }
        second = orchestrator.register_candidates([paraphrase], source="a2", round_number=2)
        self.assertEqual(first, second)
        self.assertEqual(1, len(orchestrator.snapshot()["decisions"]))

    def test_model_supplied_ids_are_not_used_as_decision_ids(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        candidate = {
            "decision_id": "MODEL_CONTROLLED_ID",
            "gap_id": "ANOTHER_MODEL_ID",
            "issue_type": "OUTPUT_SHAPE",
            "subject_ref": "/output_expectation",
            "description": "Choose rows or columns.",
            "alternatives": ["columns", "rows"],
        }
        decision_id = orchestrator.register_candidates(
            [candidate],
            source="a2",
            round_number=1,
            allowed_paths={"/output_expectation"},
        )[0]
        self.assertTrue(decision_id.startswith("BIF-BUSINESS_OUTPUT-"))
        self.assertNotIn("MODEL", decision_id)

    def test_model_control_fields_cannot_change_orchestrator_policy(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        decision_id = orchestrator.register_candidates(
            [{
                "issue_type": "OUTPUT_SHAPE",
                "subject_ref": "/output_expectation",
                "description": "Choose rows or columns.",
                "alternatives": ["model A", "model B"],
                "material": False,
                "ownership": "SYSTEM",
                "category": "DATA_QUALITY",
                "status": "closed",
            }],
            source="a2",
            round_number=1,
            allowed_paths={"/output_expectation"},
        )[0]
        decision = orchestrator.get(decision_id)
        self.assertEqual("BUSINESS_OUTPUT", decision["category"])
        self.assertEqual("pending", decision["status"])
        self.assertEqual(
            ["Show values in separate fields", "Show one value per repeated row"],
            decision["choice_domain"],
        )

    def test_different_meanings_on_same_path_remain_separate_decisions(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        base = {
            "subject_ref": "/target_population",
            "alternatives": ["First", "Second"],
        }
        candidates = [
            {**base, "issue_type": "LOCATION_ROLE", "description": "Location role"},
            {**base, "issue_type": "TARGET_POPULATION", "description": "Population status"},
        ]
        orchestrator.register_candidates(
            candidates,
            source="a2",
            round_number=1,
            allowed_paths={"/target_population"},
        )
        self.assertEqual(2, len(orchestrator.pending()))

    def test_bif_system_candidate_is_deferred(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        registered = orchestrator.register_candidates(
            [{
                "issue_type": "DATA_QUALITY",
                "subject_ref": "/target_population",
                "description": "Are stored labels accurate?",
                "alternatives": ["Accurate", "Not accurate"],
            }],
            source="a2",
            round_number=1,
            allowed_paths={"/target_population"},
        )
        self.assertEqual([], registered)
        self.assertIsNone(orchestrator.next_pending())

    def test_audit_and_orchestrator_owned_question_control_termination(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        self.assertTrue(orchestrator.needs_discovery_audit())
        with self.assertRaises(ClarificationStateError):
            orchestrator.build_final_confirmation_request()
        orchestrator.record_discovery_audit(round_number=1, candidate_count=0)
        self.assertTrue(orchestrator.ready_for_materialization())
        with self.assertRaises(ClarificationStateError):
            orchestrator.build_final_confirmation_request()
        orchestrator.mark_provisional_frame("Return the requested values as separate fields.")
        request = orchestrator.build_final_confirmation_request()
        self.assertEqual("final_confirmation", request["gap_id"])
        self.assertEqual(["CONFIRM", "REVISE"], [item["option_id"] for item in request["options"]])

    def test_only_exact_orchestrator_confirmation_can_terminate(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        orchestrator.record_discovery_audit(round_number=1, candidate_count=0)
        orchestrator.mark_provisional_frame("Return the requested result.")
        request = orchestrator.build_final_confirmation_request()
        with self.assertRaisesRegex(ClarificationStateError, "not accepted"):
            orchestrator.record_final_confirmation(
                request,
                {
                    "request_id": request["request_id"],
                    "action": "select", "selected_option_ids": ["yes"],
                },
                round_number=1,
            )
        forged = {**request, "request_id": "MODEL-FINAL"}
        with self.assertRaisesRegex(ClarificationStateError, "orchestrator-owned"):
            orchestrator.record_final_confirmation(
                forged,
                {
                    "request_id": request["request_id"],
                    "action": "select", "selected_option_ids": ["CONFIRM"],
                },
                round_number=1,
            )
        orchestrator.record_final_confirmation(
            request,
            {
                "request_id": request["request_id"],
                "action": "select", "selected_option_ids": ["CONFIRM"],
            },
            round_number=1,
        )
        self.assertTrue(orchestrator.can_terminate())

    def test_final_revise_reopens_materialization_and_requires_correction(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        orchestrator.record_discovery_audit(round_number=1, candidate_count=0)
        orchestrator.mark_provisional_frame("Schools in the district")
        request = orchestrator.build_final_confirmation_request()
        result = orchestrator.record_final_confirmation(
            request,
            {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["REVISE"],
                "free_text": "Use the city, not the district.",
            },
            round_number=2,
        )
        self.assertEqual("revise", result)
        self.assertFalse(orchestrator.ready_for_final_confirmation())
        self.assertFalse(orchestrator.can_terminate())

    def test_question_writer_cannot_override_decision_control_fields(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        decision_id = orchestrator.register_candidates(
            [{
                "issue_type": "OUTPUT_SHAPE",
                "subject_ref": "/output_expectation",
                "description": "Choose presentation.",
                "alternatives": ["Separate fields", "Repeated rows"],
            }],
            source="a2",
            round_number=1,
            allowed_paths={"/output_expectation"},
        )[0]
        request = orchestrator.build_request(
            decision_id,
            {
                "request_id": "MODEL_ID",
                "gap_id": "MODEL_GAP",
                "affected_paths": ["/wrong"],
                "question": "How should the values be shown?",
                "why_it_matters": "It changes the output shape.",
                "options": [{"label": "Ignored A"}, {"label": "Ignored B"}],
                "allow_free_text": False,
            },
        )
        self.assertEqual(decision_id, request["gap_id"])
        self.assertEqual(["/output_expectation"], request["affected_paths"])
        self.assertEqual(
            ["Show values in separate fields", "Show one value per repeated row"],
            [item["label"] for item in request["options"]],
        )

    def test_response_for_another_request_cannot_resolve_decision(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        decision_id = orchestrator.register_candidates(
            [{
                "issue_type": "SCOPE_SELECTION",
                "subject_ref": "/scope",
                "description": "Choose scope.",
                "alternatives": ["Global", "Per group"],
            }],
            source="a2",
            round_number=1,
            allowed_paths={"/scope"},
        )[0]
        request = orchestrator.build_request(
            decision_id,
            {
                "question": "Which scope?",
                "why_it_matters": "It changes the selected records.",
                "options": [{"label": "Global"}, {"label": "Per group"}],
                "allow_free_text": False,
            },
        )
        orchestrator.bind_request(decision_id, request, round_number=1)
        with self.assertRaisesRegex(ClarificationStateError, "active request"):
            orchestrator.resolve(
                decision_id,
                {
                    "request_id": "FORGED-REQUEST",
                    "action": "select",
                    "selected_option_ids": ["OPTION_1"],
                },
                round_number=1,
            )

    def test_response_updates_canonical_state_and_reconciles_same_slot(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        ids = orchestrator.register_candidates(
            [
                {
                    "issue_type": "OUTPUT_SHAPE",
                    "subject_ref": "/output_expectation",
                    "description": "Choose the output layout.",
                    "alternatives": ["columns", "rows"],
                },
                {
                    "issue_type": "OUTPUT_SHAPE",
                    "subject_ref": "/output_expectation/fields/0",
                    "description": "Choose the output layout again.",
                    "alternatives": ["columns", "rows"],
                },
            ],
            source="a2",
            round_number=1,
        )
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(1, len(orchestrator.pending()))
        self.assertIn("candidate_merged", {item["event"] for item in orchestrator.events})
        decision = orchestrator.next_pending()
        request = orchestrator.build_request(
            decision["decision_id"],
            {
                "question": "How should the results be shown?",
                "why_it_matters": "It determines the result layout.",
            },
        )
        orchestrator.bind_request(decision["decision_id"], request, round_number=2)
        orchestrator.resolve(
            decision["decision_id"],
            {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["OPTION_1"],
            },
            round_number=2,
        )
        state = orchestrator.snapshot()["canonical_intent_state"]
        self.assertEqual(
            ["Show values in separate fields"],
            state["slots"]["business.output.shape"]["value"]["labels"],
        )

    def test_location_answer_removes_redundant_population_location_question(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        ids = orchestrator.register_candidates(
            [
                {
                    "issue_type": "LOCATION_ROLE",
                    "subject_ref": "/target_population/location",
                    "description": "Does Fresno identify a city or district?",
                    "alternatives": ["city", "district"],
                },
                {
                    "issue_type": "TARGET_POPULATION",
                    "subject_ref": "/target_population",
                    "description": "Use the Fresno city population or Fresno district population?",
                    "alternatives": ["Fresno city", "Fresno district"],
                },
            ],
            source="a2",
            round_number=1,
        )
        decision = orchestrator.get(ids[0])
        request = orchestrator.build_request(
            ids[0],
            {"question": "Which meaning?", "why_it_matters": "It changes the population."},
        )
        orchestrator.bind_request(ids[0], request, round_number=2)
        orchestrator.resolve(
            ids[0],
            {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["OPTION_1"],
            },
            round_number=2,
        )
        self.assertEqual("resolved_by_implication", orchestrator.get(ids[1])["status"])
        self.assertEqual([], orchestrator.pending())

    def test_validation_question_includes_as_recorded_baseline(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        decision_id = orchestrator.register_candidates(
            [{
                "issue_type": "METRIC_DEFINITION",
                "subject_ref": "/metrics/0",
                "description": "Should a valid email mean syntax-valid or verified deliverability?",
                "alternatives": ["Syntax-valid", "Verified deliverability"],
            }],
            source="a2",
            round_number=1,
        )[0]
        self.assertEqual(
            "Use the values as recorded, with no additional validation",
            orchestrator.get(decision_id)["choice_domain"][0],
        )

    def test_queue_priority_asks_location_before_broad_population(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        orchestrator.register_candidates(
            [
                {
                    "issue_type": "TARGET_POPULATION",
                    "subject_ref": "/target_population",
                    "description": "Choose a population.",
                    "alternatives": ["City population", "District population"],
                },
                {
                    "issue_type": "LOCATION_ROLE",
                    "subject_ref": "/target_population/location",
                    "description": "Choose the location role.",
                    "alternatives": ["City", "District"],
                },
            ],
            source="a2",
            round_number=1,
        )
        self.assertEqual("LOCATION_ROLE", orchestrator.next_pending()["issue"])

    def test_failed_response_transaction_rolls_back_state_and_queue(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        decision_id = orchestrator.register_candidates(
            [{
                "issue_type": "SCOPE_SELECTION",
                "subject_ref": "/scope",
                "description": "Choose the selection scope.",
                "alternatives": ["Overall", "Per group"],
            }],
            source="a2",
            round_number=1,
        )[0]
        request = orchestrator.build_request(
            decision_id,
            {"question": "Which scope?", "why_it_matters": "It changes selection."},
        )
        orchestrator.bind_request(decision_id, request, round_number=1)
        orchestrator._reconcile_queue = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced reconciliation failure")
        )
        with self.assertRaisesRegex(ClarificationStateError, "rolled back"):
            orchestrator.resolve(
                decision_id,
                {
                    "request_id": request["request_id"],
                    "action": "select",
                    "selected_option_ids": ["OPTION_1"],
                },
                round_number=1,
            )
        self.assertEqual("asked", orchestrator.get(decision_id)["status"])
        self.assertEqual(0, orchestrator.snapshot()["canonical_intent_state"]["revision"])
        self.assertEqual(
            "response_transaction_rolled_back",
            orchestrator.events[-1]["event"],
        )

    def test_conflicting_same_slot_candidate_is_invalidated_after_answer(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        first = orchestrator.register_candidates(
            [{
                "issue_type": "TARGET_POPULATION",
                "subject_ref": "/target_population",
                "description": "Choose active or inactive customers.",
                "alternatives": ["Active customers", "Inactive customers"],
            }],
            source="validator",
            round_number=1,
        )[0]
        decision = orchestrator.get(first)
        request = orchestrator.build_request(
            first,
            {"question": "Which customers?", "why_it_matters": "It changes the population."},
        )
        orchestrator.bind_request(first, request, round_number=1)
        conflicting_id = orchestrator.register_candidates(
            [{
                "issue_type": "TARGET_POPULATION",
                "subject_ref": "/target_population",
                "description": "A concurrent audit proposed a conflicting population basis.",
                "alternatives": ["Closed accounts", "Dormant accounts"],
            }],
            source="concurrent_audit",
            round_number=1,
        )[0]
        orchestrator.resolve(
            first,
            {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["OPTION_1"],
            },
            round_number=1,
        )
        self.assertEqual("invalidated", orchestrator.get(conflicting_id)["status"])

    def test_new_incompatible_candidate_supersedes_pending_same_slot(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        old_id = orchestrator.register_candidates(
            [{
                "issue_type": "METRIC_DEFINITION",
                "subject_ref": "/metrics/0",
                "description": "Choose revenue meaning.",
                "alternatives": ["Gross revenue", "Net revenue"],
            }],
            source="a1",
            round_number=1,
        )[0]
        new_id = orchestrator.register_candidates(
            [{
                "issue_type": "METRIC_DEFINITION",
                "subject_ref": "/metrics/0",
                "description": "Choose account value meaning.",
                "alternatives": ["Book value", "Market value"],
            }],
            source="audit",
            round_number=2,
        )[0]
        self.assertNotEqual(old_id, new_id)
        self.assertEqual("superseded", orchestrator.get(old_id)["status"])
        self.assertEqual("pending", orchestrator.get(new_id)["status"])

    def test_sif_bif_level_candidate_is_routed_to_bif_reentry(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="sif")
        registered = orchestrator.register_candidates(
            [{
                "issue_type": "LOCATION_ROLE",
                "subject_ref": "/target_population",
                "description": "The business location meaning is still unclear.",
                "alternatives": ["City", "District"],
            }],
            source="a5",
            round_number=1,
        )
        self.assertEqual([], registered)
        handoffs = orchestrator.consume_handoffs(target_stage="bif")
        self.assertEqual("BIF_REENTRY_REQUIRED", handoffs[0]["status"])
        self.assertEqual("LOCATION_ROLE", handoffs[0]["issue_type"])
        self.assertEqual([], orchestrator.handoffs(target_stage="bif"))

    def test_discovery_audit_can_add_new_candidate_after_answer(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        first = orchestrator.register_candidates(
            [{
                "issue_type": "LOCATION_ROLE",
                "subject_ref": "/target_population",
                "description": "Choose location meaning.",
                "alternatives": ["City", "District"],
            }],
            source="initial",
            round_number=1,
        )[0]
        request = orchestrator.build_request(
            first,
            {"question": "Which location?", "why_it_matters": "It changes the population."},
        )
        orchestrator.bind_request(first, request, round_number=1)
        orchestrator.resolve(
            first,
            {
                "request_id": request["request_id"],
                "action": "select",
                "selected_option_ids": ["OPTION_1"],
            },
            round_number=1,
        )
        self.assertTrue(orchestrator.needs_discovery_audit())
        orchestrator.record_discovery_audit(round_number=2, candidate_count=1)
        second = orchestrator.register_candidates(
            [{
                "issue_type": "OUTPUT_SHAPE",
                "subject_ref": "/output_expectation",
                "description": "Choose output shape exposed by the location decision.",
                "alternatives": ["Separate fields", "Repeated rows"],
            }],
            source="dependency_audit",
            round_number=2,
        )[0]
        self.assertEqual(second, orchestrator.next_pending()["decision_id"])

    def test_repeated_metric_paths_get_distinct_atomic_semantic_slots(self) -> None:
        orchestrator = ClarificationOrchestrator(stage="bif")
        ids = orchestrator.register_candidates(
            [
                {
                    "issue_type": "METRIC_DEFINITION",
                    "subject_ref": "/metrics/0",
                    "description": "Choose first metric meaning.",
                    "alternatives": ["Gross", "Net"],
                },
                {
                    "issue_type": "METRIC_DEFINITION",
                    "subject_ref": "/metrics/1",
                    "description": "Choose second metric meaning.",
                    "alternatives": ["Gross", "Net"],
                },
            ],
            source="a2",
            round_number=1,
        )
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(
            {"business.metric.0.definition", "business.metric.1.definition"},
            {item["semantic_slot"] for item in orchestrator.pending()},
        )


if __name__ == "__main__":
    unittest.main()
