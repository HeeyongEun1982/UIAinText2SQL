from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from intent_sql_agent.services.intent_state import CanonicalEffect, CanonicalIntentState


_ISSUE_POLICIES: dict[str, dict[str, Any]] = {
    "DECISION_OBJECTIVE": {"stage": "bif", "category": "BUSINESS_INTENT", "path": "/decision_objective"},
    "TARGET_POPULATION": {"stage": "bif", "category": "BUSINESS_INTENT", "path": "/target_population"},
    "POPULATION_CONDITION_STRUCTURE": {
        "stage": "bif", "category": "BUSINESS_INTENT", "path": "/target_population",
        "alternatives": [
            "Treat the phrase as one combined business condition",
            "Treat its distinct concepts as separate simultaneous conditions",
        ],
    },
    "LOCATION_ROLE": {
        "stage": "bif", "category": "BUSINESS_INTENT", "path": "/target_population",
        "alternatives": ["Interpret it as a city", "Interpret it as an organization or district"],
    },
    "METRIC_DEFINITION": {"stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/metrics"},
    "CALCULATION_METHOD": {"stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/calculation"},
    "LOGIC_RELATION": {
        "stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/calculation",
        "alternatives": ["Apply the conditions simultaneously", "Treat the values as alternatives"],
    },
    "GRANULARITY": {"stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/granularity"},
    "SCOPE_SELECTION": {"stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/scope"},
    "TIME_BASIS": {"stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/time_basis"},
    "COMPARISON": {"stage": "bif", "category": "BUSINESS_CALCULATION", "path": "/comparisons"},
    "OUTPUT_SHAPE": {
        "stage": "bif", "category": "BUSINESS_OUTPUT", "path": "/output_expectation",
        "alternatives": ["Show values in separate fields", "Show one value per repeated row"],
    },
    "QUERY_GRAIN": {"stage": "sif", "category": "QUERY_SEMANTICS", "path": "/query_blocks"},
    "AGGREGATION_PLACEMENT": {"stage": "sif", "category": "QUERY_SEMANTICS", "path": "/query_blocks"},
    "QUERY_SELECTION_SCOPE": {"stage": "sif", "category": "QUERY_SEMANTICS", "path": "/query_blocks"},
    "QUERY_TIME_SEMANTICS": {"stage": "sif", "category": "QUERY_SEMANTICS", "path": "/query_blocks"},
    "QUERY_COMPARISON_SEMANTICS": {"stage": "sif", "category": "QUERY_SEMANTICS", "path": "/query_blocks"},
    "QUERY_OUTPUT_SEMANTICS": {"stage": "sif", "category": "QUERY_SEMANTICS", "path": "/query_blocks"},
    "SOURCE_POPULATION": {"stage": "sif", "category": "SOURCE_POPULATION", "path": "/target_population"},
    "TECHNICAL_GROUNDING": {"stage": "sif", "category": "TECHNICAL_GROUNDING", "path": "/query_blocks"},
    "PHYSICAL_GROUNDING": {"stage": "system", "category": "PHYSICAL_GROUNDING"},
    "DATA_AVAILABILITY": {"stage": "system", "category": "DATA_AVAILABILITY"},
    "DATA_QUALITY": {"stage": "system", "category": "DATA_QUALITY"},
    "SQL_IMPLEMENTATION": {"stage": "system", "category": "SQL_IMPLEMENTATION"},
}

_ISSUE_POLICY_VERSION = "2.0-dynamic-queue"

_TERMINAL_DECISION_STATES = {
    "resolved",
    "resolved_by_implication",
    "merged",
    "invalidated",
    "superseded",
    "deferred_to_sif",
    "system_owned",
}

_ISSUE_PRIORITY = {
    "DECISION_OBJECTIVE": 10,
    "LOCATION_ROLE": 20,
    "POPULATION_CONDITION_STRUCTURE": 30,
    "TARGET_POPULATION": 40,
    "METRIC_DEFINITION": 50,
    "CALCULATION_METHOD": 60,
    "LOGIC_RELATION": 70,
    "GRANULARITY": 80,
    "SCOPE_SELECTION": 90,
    "TIME_BASIS": 100,
    "COMPARISON": 110,
    "OUTPUT_SHAPE": 120,
    "SOURCE_POPULATION": 20,
    "TECHNICAL_GROUNDING": 25,
    "QUERY_GRAIN": 30,
    "AGGREGATION_PLACEMENT": 40,
    "QUERY_SELECTION_SCOPE": 50,
    "QUERY_TIME_SEMANTICS": 60,
    "QUERY_COMPARISON_SEMANTICS": 70,
    "QUERY_OUTPUT_SEMANTICS": 80,
}

_ISSUE_ALIASES = {
    "location_role_mismatch": "LOCATION_ROLE",
    "question_logic_mismatch": "LOGIC_RELATION",
    "compound_condition_not_atomic": "POPULATION_CONDITION_STRUCTURE",
    "duplicate_condition_id": "SQL_IMPLEMENTATION",
    "missing_condition_logic": "LOGIC_RELATION",
    "condition_logic_reference_mismatch": "SQL_IMPLEMENTATION",
    "invalid_set_semantics": "LOGIC_RELATION",
    "output_presentation_unresolved": "OUTPUT_SHAPE",
    "output_multiplicity_mismatch": "OUTPUT_SHAPE",
    "scope": "SCOPE_SELECTION",
    "time": "TIME_BASIS",
    "metric": "METRIC_DEFINITION",
    "calculation": "CALCULATION_METHOD",
    "presentation": "OUTPUT_SHAPE",
    "location": "LOCATION_ROLE",
    "logic": "LOGIC_RELATION",
    "semantic_source_population": "SOURCE_POPULATION",
    "source_population": "SOURCE_POPULATION",
}


class ClarificationStateError(RuntimeError):
    pass


class ClarificationOrchestrator:
    """Deterministically owns decision state, question order, and termination."""

    def __init__(self, *, stage: str) -> None:
        if stage not in {"bif", "sif"}:
            raise ValueError(f"Unsupported clarification stage: {stage}")
        self.stage = stage
        self._decisions: list[dict[str, Any]] = []
        self.final_confirmation: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self._handoffs: list[dict[str, Any]] = []
        self._last_transaction: dict[str, Any] | None = None
        self.intent_state = CanonicalIntentState(stage=stage)
        self._resolution_revision = 0
        self._audited_revision = -1
        self._materialized_revision = -1
        self._provisional_summary: str | None = None

    def register_findings(
        self,
        findings: list[dict[str, Any]],
        *,
        source: str,
        round_number: int = 0,
        allowed_paths: set[str] | None = None,
    ) -> list[str]:
        observations = []
        for finding in findings:
            if not finding.get("material", True) or not finding.get("user_answerable", True):
                continue
            issue = str(finding.get("issue") or finding.get("gap_type") or "")
            observations.append({
                "issue_type": self._canonical_issue_type(issue),
                "subject_ref": str(finding.get("path") or ""),
                "description": str(finding.get("description") or issue),
                "alternatives": list(finding.get("alternatives", []) or []),
            })
        return self.register_candidates(
            observations,
            source=source,
            round_number=round_number,
            allowed_paths=allowed_paths,
        )

    def register_source_ambiguities(
        self,
        ambiguities: list[dict[str, Any]],
        *,
        round_number: int,
    ) -> list[str]:
        registered: list[str] = []
        policy = _ISSUE_POLICIES["SOURCE_POPULATION"]
        if self.stage != policy["stage"]:
            self.events.append({
                "event": "candidate_deferred",
                "source": "source_ambiguity_detector",
                "issue_type": "SOURCE_POPULATION",
                "owner_stage": policy["stage"],
                "round": round_number,
            })
            return registered
        for ambiguity in ambiguities:
            if not ambiguity.get("material", True):
                continue
            if not str(ambiguity.get("ambiguity_id") or ""):
                continue
            choice_domain = self._canonical_alternatives(
                [
                    str(item.get("user_facing_definition") or "")
                    for item in ambiguity.get("candidates", []) or []
                ],
                [],
            )
            if len(choice_domain) < 2:
                self.events.append({
                    "event": "candidate_rejected",
                    "source": "source_ambiguity_detector",
                    "reason": "no_material_choice",
                    "issue_type": "SOURCE_POPULATION",
                    "round": round_number,
                })
                continue
            affected_paths = self._canonical_candidate_paths(
                [str(path) for path in ambiguity.get("intent_paths", [])],
                str(policy["path"]),
                None,
            )
            self.events.append({
                "event": "candidate_accepted_by_policy",
                "source": "source_ambiguity_detector",
                "policy_version": _ISSUE_POLICY_VERSION,
                "issue_type": "SOURCE_POPULATION",
                "category": policy["category"],
                "stage": self.stage,
                "round": round_number,
            })
            registered.append(
                self._register(
                    issue="SOURCE_POPULATION",
                    description=str(ambiguity.get("reason") or "Choose the intended record population"),
                    affected_paths=affected_paths,
                    category=str(policy["category"]),
                    decision_subject=affected_paths[0],
                    choice_domain=choice_domain,
                    source="source_ambiguity_detector",
                    round_number=round_number,
                    payload=ambiguity,
                )
            )
        return registered

    def register_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        source: str,
        round_number: int,
        allowed_paths: set[str] | None = None,
    ) -> list[str]:
        """Apply the closed policy registry to untrusted issue observations."""
        registered: list[str] = []
        for candidate in candidates:
            issue_type = self._canonical_issue_type(
                str(candidate.get("issue_type") or "")
            )
            policy = _ISSUE_POLICIES.get(issue_type)
            if policy is None:
                self.events.append({
                    "event": "candidate_rejected",
                    "source": source,
                    "reason": "unknown_issue_type",
                    "proposed_issue_type": str(candidate.get("issue_type") or ""),
                    "round": round_number,
                })
                continue
            if policy["stage"] != self.stage:
                target_stage = str(policy["stage"])
                status = (
                    "BIF_REENTRY_REQUIRED"
                    if self.stage == "sif" and target_stage == "bif"
                    else "deferred_to_sif"
                    if self.stage == "bif" and target_stage == "sif"
                    else "system_owned"
                )
                handoff = {
                    "status": status,
                    "source_stage": self.stage,
                    "target_stage": target_stage,
                    "issue_type": issue_type,
                    "subject_ref": str(candidate.get("subject_ref") or policy.get("path") or ""),
                    "description": str(candidate.get("description") or "Material clarification decision"),
                    "alternatives": list(candidate.get("alternatives", []) or policy.get("alternatives", []) or []),
                    "source": source,
                    "round": round_number,
                }
                self._handoffs.append(handoff)
                self.events.append({
                    "event": "candidate_routed",
                    "source": source,
                    "issue_type": issue_type,
                    "owner_stage": target_stage,
                    "status": status,
                    "round": round_number,
                })
                continue
            proposed_subject = str(
                candidate.get("subject_ref") or ""
            )
            raw_paths = [proposed_subject] if proposed_subject else []
            affected_paths = self._canonical_candidate_paths(
                raw_paths,
                str(policy["path"]),
                allowed_paths,
            )
            if raw_paths != affected_paths:
                self.events.append({
                    "event": "candidate_paths_canonicalized",
                    "source": source,
                    "round": round_number,
                    "proposed_paths": raw_paths,
                    "accepted_paths": affected_paths,
                })
            alternatives = self._canonical_alternatives(
                list(candidate.get("alternatives", []) or []),
                list(policy.get("alternatives", []) or []),
            )
            alternatives = self._ensure_option_completeness(
                issue_type,
                str(candidate.get("description") or ""),
                alternatives,
            )
            if len(alternatives) < 2:
                self.events.append({
                    "event": "candidate_rejected",
                    "source": source,
                    "reason": "no_material_choice",
                    "issue_type": issue_type,
                    "round": round_number,
                })
                continue
            self.events.append({
                "event": "candidate_accepted_by_policy",
                "source": source,
                "policy_version": _ISSUE_POLICY_VERSION,
                "issue_type": issue_type,
                "category": policy["category"],
                "stage": self.stage,
                "round": round_number,
            })
            registered.append(self._register(
                issue=issue_type,
                description=str(candidate.get("description") or "Material clarification decision"),
                affected_paths=affected_paths,
                category=str(policy["category"]),
                decision_subject=affected_paths[0] if affected_paths else str(policy["path"]),
                choice_domain=alternatives,
                source=source,
                round_number=round_number,
                payload=candidate,
            ))
        return registered

    def _canonical_candidate_paths(
        self,
        paths: list[str],
        policy_path: str,
        allowed_paths: set[str] | None,
    ) -> list[str]:
        if allowed_paths is None:
            accepted = sorted(set(path for path in paths if path.startswith("/")))
            return accepted or [policy_path]
        accepted = sorted(set(path for path in paths if path in allowed_paths))
        if accepted:
            return accepted
        if policy_path in allowed_paths:
            return [policy_path]
        return [min(allowed_paths, key=len)] if allowed_paths else []

    def next_pending(self) -> dict[str, Any] | None:
        candidates = [item for item in self._decisions if item["status"] == "pending"]
        if not candidates:
            return None
        selected = min(
            candidates,
            key=lambda item: (
                int(item.get("priority", 1000)),
                int(item.get("discovered_round", 0)),
                str(item.get("decision_id") or ""),
            ),
        )
        return copy.deepcopy(selected)

    def pending(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self._decisions if item["status"] == "pending"]

    def resolved(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self._decisions if item["status"] == "resolved"]

    def get(self, decision_id: str) -> dict[str, Any] | None:
        return next((item for item in self._decisions if item["decision_id"] == decision_id), None)

    def bind_request(
        self,
        decision_id: str,
        request: dict[str, Any],
        *,
        round_number: int,
    ) -> None:
        decision = self.get(decision_id)
        if decision is None:
            raise ClarificationStateError(f"Unknown decision: {decision_id}")
        if decision["status"] != "pending":
            raise ClarificationStateError(
                f"Decision {decision_id} cannot be asked from state {decision['status']}"
            )
        if str(request.get("gap_id") or "") != decision_id:
            raise ClarificationStateError(
                f"Request is not owned by decision {decision_id}"
            )
        decision["status"] = "asked"
        decision["question_attempts"] += 1
        decision["request"] = copy.deepcopy(request)
        decision["asked_round"] = round_number
        self.events.append({
            "event": "decision_asked",
            "decision_id": decision_id,
            "round": round_number,
            "reason": "highest_priority_pending_decision",
        })

    def resolve(
        self,
        decision_id: str,
        response: dict[str, Any],
        *,
        round_number: int,
        context: dict[str, Any] | None = None,
    ) -> None:
        decision = self.get(decision_id)
        if decision is None:
            raise ClarificationStateError(f"Unknown decision: {decision_id}")
        if decision["status"] != "asked":
            raise ClarificationStateError(
                f"Decision {decision_id} cannot be resolved from state {decision['status']}"
            )
        expected_request_id = str((decision.get("request") or {}).get("request_id") or "")
        if str(response.get("request_id") or "") != expected_request_id:
            raise ClarificationStateError(
                f"Decision {decision_id} response request_id does not match the active request"
            )
        if response.get("action") == "cannot_answer":
            decision["status"] = "blocked"
            decision["response"] = copy.deepcopy(response)
            raise ClarificationStateError(f"Decision {decision_id} could not be answered")
        if response.get("action") == "select":
            allowed_options = {
                str(item.get("option_id") or "")
                for item in (decision.get("request") or {}).get("options", [])
            }
            selected_options = {
                str(item) for item in response.get("selected_option_ids", []) or []
            }
            if not selected_options or not selected_options <= allowed_options:
                raise ClarificationStateError(
                    f"Decision {decision_id} response selected unknown options"
                )
        elif response.get("action") == "free_text" and not str(
            response.get("free_text") or ""
        ).strip():
            raise ClarificationStateError(
                f"Decision {decision_id} free-text response is empty"
            )
        selected_value = self._selected_value(decision, response)
        effect = CanonicalEffect(
            operation="set",
            semantic_slot=str(decision["semantic_slot"]),
            value=selected_value,
            source_decision_id=decision_id,
        )
        decisions_before = copy.deepcopy(self._decisions)
        state_before = self.intent_state.snapshot()
        revision_before = self._resolution_revision
        event_count_before = len(self.events)
        try:
            decision["status"] = "resolved"
            decision["response"] = copy.deepcopy(response)
            decision["resolved_round"] = round_number
            decision["context"] = copy.deepcopy(context or {})
            decision["canonical_effect"] = {
                "operation": effect.operation,
                "semantic_slot": effect.semantic_slot,
                "value": copy.deepcopy(effect.value),
                "source_decision_id": effect.source_decision_id,
            }
            self._resolution_revision += 1
            state_change = self.intent_state.apply_effect(
                effect, round_number=round_number
            )
            self.events.append({
                "event": "decision_resolved",
                "decision_id": decision_id,
                "round": round_number,
                "semantic_slot": decision["semantic_slot"],
                "selected_value": copy.deepcopy(selected_value),
                "reason": "canonical_E2_response_committed",
            })
            self.events.append({
                "event": "canonical_intent_state_updated",
                "decision_id": decision_id,
                "round": round_number,
                **state_change,
            })
            self._reconcile_queue(decision, selected_value, round_number=round_number)
            self._reprioritize_queue(round_number=round_number)
            self._last_transaction = {
                "decision_id": decision_id,
                "round": round_number,
                "queue_before": decisions_before,
                "selected_decision": copy.deepcopy(
                    next(
                        item for item in decisions_before
                        if item.get("decision_id") == decision_id
                    )
                ),
                "canonical_request": copy.deepcopy(decision.get("request")),
                "user_response": copy.deepcopy(response),
                "canonical_effect": copy.deepcopy(decision.get("canonical_effect")),
                "intent_state_before": state_before,
                "intent_state_after": self.intent_state.snapshot(),
                "reconciliation_events": copy.deepcopy(
                    self.events[event_count_before:]
                ),
                "queue_after": copy.deepcopy(self._decisions),
                "transaction_result": "committed",
            }
        except Exception as exc:
            self._decisions = decisions_before
            self.intent_state.restore(state_before)
            self._resolution_revision = revision_before
            del self.events[event_count_before:]
            self.events.append({
                "event": "response_transaction_rolled_back",
                "decision_id": decision_id,
                "round": round_number,
                "reason": type(exc).__name__,
            })
            self._last_transaction = {
                "decision_id": decision_id,
                "round": round_number,
                "queue_before": decisions_before,
                "canonical_request": copy.deepcopy(decision.get("request")),
                "user_response": copy.deepcopy(response),
                "intent_state_before": state_before,
                "intent_state_after": self.intent_state.snapshot(),
                "queue_after": copy.deepcopy(self._decisions),
                "transaction_result": "rolled_back",
                "reason": type(exc).__name__,
            }
            raise ClarificationStateError(
                f"Decision {decision_id} response transaction rolled back: {exc}"
            ) from exc

    def record_final_confirmation(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        round_number: int,
    ) -> str:
        if not self.ready_for_final_confirmation():
            raise ClarificationStateError(
                "Final confirmation attempted before pending decisions and discovery audit completed"
            )
        expected_request = self.build_final_confirmation_request()
        for key in ("request_id", "stage", "gap_id", "options", "affected_paths"):
            if request.get(key) != expected_request.get(key):
                raise ClarificationStateError(
                    f"Final confirmation request is not orchestrator-owned: {key}"
                )
        if str(response.get("request_id") or "") != expected_request["request_id"]:
            raise ClarificationStateError(
                "Final confirmation response does not match the active request"
            )
        selected = {
            str(item) for item in response.get("selected_option_ids", []) or []
        }
        if response.get("action") == "select" and selected == {"REVISE"}:
            correction = str(response.get("free_text") or "").strip()
            if not correction:
                raise ClarificationStateError(
                    "Final revision requires a business-language correction description"
                )
            self.final_confirmation = None
            self._materialized_revision = -1
            self._provisional_summary = None
            self.events.append({
                "event": "final_confirmation_revision_requested",
                "round": round_number,
                "correction": correction,
            })
            return "revise"
        accepted = response.get("action") == "select" and selected == {"CONFIRM"}
        if not accepted:
            raise ClarificationStateError("Final confirmation was not accepted")
        self.final_confirmation = {
            "round": round_number,
            "request": copy.deepcopy(request),
            "response": copy.deepcopy(response),
        }
        self.events.append({
            "event": "final_confirmation_accepted",
            "round": round_number,
            "reason": "exact_orchestrator_request_confirmed",
        })
        return "confirmed"

    def can_terminate(self) -> bool:
        return (
            self.final_confirmation is not None
            and not self.pending()
            and all(item["status"] in _TERMINAL_DECISION_STATES for item in self._decisions)
        )

    def needs_discovery_audit(self) -> bool:
        return (
            not self.pending()
            and not any(item["status"] == "asked" for item in self._decisions)
            and self._audited_revision != self._resolution_revision
        )

    def record_discovery_audit(self, *, round_number: int, candidate_count: int) -> None:
        if self.pending() or any(item["status"] == "asked" for item in self._decisions):
            raise ClarificationStateError("Discovery audit requires no active decision")
        self._audited_revision = self._resolution_revision
        self.events.append({
            "event": "discovery_audit_completed",
            "round": round_number,
            "revision": self._resolution_revision,
            "candidate_count": candidate_count,
        })

    def ready_for_final_confirmation(self) -> bool:
        return (
            self.ready_for_materialization()
            and self._materialized_revision == self._resolution_revision
            and bool(self._provisional_summary)
        )

    def ready_for_materialization(self) -> bool:
        return (
            not self.pending()
            and not any(item["status"] == "asked" for item in self._decisions)
            and self._audited_revision == self._resolution_revision
        )

    def mark_provisional_frame(self, summary: str) -> None:
        if not self.ready_for_materialization():
            raise ClarificationStateError(
                "Provisional frame cannot be recorded before the queue and audit are stable"
            )
        normalized = " ".join(str(summary).split())
        if not normalized:
            raise ClarificationStateError("Provisional frame summary must not be empty")
        self._materialized_revision = self._resolution_revision
        self._provisional_summary = normalized
        self.events.append({
            "event": "provisional_frame_materialized",
            "revision": self._materialized_revision,
        })

    def build_request(
        self,
        decision_id: str,
        question_draft: dict[str, Any],
    ) -> dict[str, Any]:
        decision = self.get(decision_id)
        if decision is None or decision["status"] != "pending":
            raise ClarificationStateError(f"Decision {decision_id} is not pending")
        raw_options = (
            [{"label": label} for label in decision.get("choice_domain", [])]
            if len(decision.get("choice_domain", [])) >= 2
            else list(question_draft.get("options", []) or [])
        )
        if len(raw_options) < 2:
            raise ClarificationStateError(
                f"Decision {decision_id} question requires at least two options"
            )
        options = [
            {"option_id": f"OPTION_{index}", "label": str(item.get("label") or "")}
            for index, item in enumerate(raw_options, start=1)
        ]
        if any(not item["label"].strip() for item in options):
            raise ClarificationStateError("Question option labels must not be empty")
        question = str(question_draft.get("question") or "").strip()
        why_it_matters = str(question_draft.get("why_it_matters") or "").strip()
        if not question or not why_it_matters:
            raise ClarificationStateError("Question text and rationale must not be empty")
        return {
            "request_id": f"{decision_id}-Q{int(decision['question_attempts']) + 1}",
            "stage": self.stage,
            "gap_id": decision_id,
            "question": question,
            "why_it_matters": why_it_matters,
            "options": options,
            "allow_free_text": bool(question_draft.get("allow_free_text", False)),
            "affected_paths": copy.deepcopy(decision["affected_paths"]),
        }

    def build_final_confirmation_request(self) -> dict[str, Any]:
        if not self.ready_for_final_confirmation():
            raise ClarificationStateError("Clarification is not ready for final confirmation")
        return {
            "request_id": f"{self.stage.upper()}-FINAL-CONFIRMATION",
            "stage": self.stage,
            "gap_id": "final_confirmation",
            "question": (
                "The clarified request has been assembled as follows: "
                f"{self._provisional_summary} "
                "Does this complete interpretation match what you want?"
            ),
            "why_it_matters": (
                "This confirmation approves the assembled frame before it is used by the next layer."
            ),
            "options": [
                {"option_id": "CONFIRM", "label": "Yes, this matches my request"},
                {"option_id": "REVISE", "label": "No, something still needs correction"},
            ],
            "allow_free_text": True,
            "affected_paths": [],
        }

    def decision_ledger(self) -> list[dict[str, Any]]:
        return [
            {
                "decision_id": item["decision_id"],
                "stage": self.stage,
                "area": item["area"],
                "issue": item["issue"],
                "category": item["category"],
                "semantic_key": item["semantic_key"],
                "semantic_slot": item["semantic_slot"],
                "decision_subject": item["decision_subject"],
                "choice_domain": copy.deepcopy(item["choice_domain"]),
                "description": item["description"],
                "affected_paths": copy.deepcopy(item["affected_paths"]),
                "request": copy.deepcopy(item.get("request")),
                "user_response": copy.deepcopy(item.get("response")),
                "canonical_effect": copy.deepcopy(item.get("canonical_effect")),
                "context": copy.deepcopy(item.get("context", {})),
                "status": item["status"],
                "resolved_round": item.get("resolved_round"),
            }
            for item in self._decisions
            if item["status"] == "resolved"
        ]

    def handoffs(self, *, target_stage: str | None = None) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(item)
            for item in self._handoffs
            if target_stage is None or item.get("target_stage") == target_stage
        ]

    def consume_handoffs(self, *, target_stage: str) -> list[dict[str, Any]]:
        selected = [
            item for item in self._handoffs if item.get("target_stage") == target_stage
        ]
        self._handoffs = [
            item for item in self._handoffs if item.get("target_stage") != target_stage
        ]
        if selected:
            self.events.append({
                "event": "handoffs_consumed",
                "target_stage": target_stage,
                "count": len(selected),
            })
        return copy.deepcopy(selected)

    def last_transaction(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._last_transaction)

    def record_final_revision_correction(
        self, correction: str, *, round_number: int
    ) -> str:
        normalized = " ".join(str(correction).split())
        if not normalized:
            raise ClarificationStateError("Final revision correction must not be empty")
        issue = "DECISION_OBJECTIVE" if self.stage == "bif" else "QUERY_OUTPUT_SEMANTICS"
        policy = _ISSUE_POLICIES[issue]
        semantic_slot = f"{self.stage}.final_revision.{self._resolution_revision + 1}"
        semantic_key = f"final_revision::{self._resolution_revision + 1}::{normalized.casefold()}"
        decision_id = self._stable_id(str(policy["category"]), semantic_key)
        effect_value = {"kind": "free_text", "text": normalized}
        effect = CanonicalEffect(
            operation="set",
            semantic_slot=semantic_slot,
            value=effect_value,
            source_decision_id=decision_id,
        )
        self._resolution_revision += 1
        self.intent_state.apply_effect(effect, round_number=round_number)
        self._decisions.append({
            "decision_id": decision_id,
            "stage": self.stage,
            "area": "general",
            "issue": issue,
            "category": str(policy["category"]),
            "semantic_key": semantic_key,
            "semantic_slot": semantic_slot,
            "decision_subject": str(policy["path"]),
            "choice_domain": [],
            "description": f"Final user correction: {normalized}",
            "affected_paths": [str(policy["path"])],
            "source": "final_confirmation_revision",
            "policy_version": _ISSUE_POLICY_VERSION,
            "priority": 0,
            "source_payload": {"correction": normalized},
            "status": "resolved",
            "discovered_round": round_number,
            "resolved_round": round_number,
            "question_attempts": 1,
            "request": None,
            "response": {"action": "free_text", "free_text": normalized},
            "canonical_effect": {
                "operation": effect.operation,
                "semantic_slot": effect.semantic_slot,
                "value": effect_value,
                "source_decision_id": effect.source_decision_id,
            },
        })
        self._audited_revision = -1
        self.events.append({
            "event": "final_revision_correction_recorded",
            "decision_id": decision_id,
            "round": round_number,
            "reason": "E2_selected_REVISE",
        })
        return decision_id

    def snapshot(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": "complete" if self.can_terminate() else "clarifying",
            "decisions": copy.deepcopy(self._decisions),
            "final_confirmation": copy.deepcopy(self.final_confirmation),
            "events": copy.deepcopy(self.events),
            "canonical_intent_state": self.intent_state.snapshot(),
            "handoffs": copy.deepcopy(self._handoffs),
            "control": {
                "resolution_revision": self._resolution_revision,
                "audited_revision": self._audited_revision,
                "needs_discovery_audit": self.needs_discovery_audit(),
                "ready_for_final_confirmation": self.ready_for_final_confirmation(),
                "ready_for_materialization": self.ready_for_materialization(),
                "materialized_revision": self._materialized_revision,
            },
            "quality": {
                "detected_material_decisions": len(self._decisions),
                "resolved_material_decisions": sum(
                    item["status"] == "resolved" for item in self._decisions
                ),
                "pending_material_decisions": sum(
                    item["status"] in {"pending", "asked"} for item in self._decisions
                ),
                "redundant_question_count": sum(
                    max(0, int(item["question_attempts"]) - 1) for item in self._decisions
                ) + sum(
                    item.get("event") in {
                        "redundant_request_suppressed",
                        "candidate_deduplicated",
                    }
                    for item in self.events
                ),
                "premature_termination_prevented": sum(
                    item.get("event") == "termination_prevented" for item in self.events
                ),
            },
        }

    def note_termination_prevented(self, reason: str) -> None:
        self.events.append({"event": "termination_prevented", "reason": reason})

    def _register(
        self,
        *,
        issue: str,
        description: str,
        affected_paths: list[str],
        category: str,
        decision_subject: str,
        choice_domain: list[str],
        source: str,
        round_number: int,
        payload: dict[str, Any],
    ) -> str:
        semantic_slot = self._semantic_slot(issue, affected_paths)
        choice_keys = {self._choice_key(item) for item in choice_domain}
        same_slot = next(
            (
                item
                for item in self._decisions
                if item.get("semantic_slot") == semantic_slot
                and item.get("issue") == issue
                and item.get("status") in {"pending", "asked"}
            ),
            None,
        )
        if same_slot is not None:
            existing_keys = {
                self._choice_key(item) for item in same_slot.get("choice_domain", [])
            }
            if choice_keys == existing_keys or choice_keys & existing_keys:
                same_slot["affected_paths"] = sorted(set(
                    same_slot.get("affected_paths", []) + affected_paths
                ))
                same_slot["choice_domain"] = list(dict.fromkeys(
                    same_slot.get("choice_domain", []) + choice_domain
                ))
                same_slot.setdefault("merged_sources", []).append({
                    "source": source,
                    "round": round_number,
                    "payload": copy.deepcopy(payload),
                })
                self.events.append({
                    "event": "candidate_merged",
                    "decision_id": same_slot["decision_id"],
                    "semantic_slot": semantic_slot,
                    "round": round_number,
                    "source": source,
                    "reason": "same_semantic_slot_with_compatible_choices",
                })
                return str(same_slot["decision_id"])
            if same_slot["status"] == "pending":
                same_slot["status"] = "superseded"
                same_slot["superseded_round"] = round_number
                same_slot["reconciliation_reason"] = (
                    "new_candidate_redefines_same_semantic_slot"
                )
                self.events.append({
                    "event": "queue_candidate_reconciled",
                    "decision_id": same_slot["decision_id"],
                    "status": "superseded",
                    "reason": "new_candidate_redefines_same_semantic_slot",
                    "round": round_number,
                    "source": source,
                })
        semantic_key = self._semantic_key(
            category,
            issue,
            affected_paths,
            decision_subject,
            choice_domain,
            semantic_slot=semantic_slot,
        )
        existing = next(
            (item for item in self._decisions if item["semantic_key"] == semantic_key),
            None,
        )
        if existing is not None:
            if existing.get("status") in _TERMINAL_DECISION_STATES:
                revision = 2 + sum(
                    str(item.get("semantic_key") or "").startswith(semantic_key + "::revision=")
                    for item in self._decisions
                )
                semantic_key = f"{semantic_key}::revision={revision}"
                existing = None
            else:
                self.events.append({
                    "event": "candidate_deduplicated",
                    "decision_id": existing["decision_id"],
                    "round": round_number,
                    "source": source,
                    "reason": "canonical_signature_already_active",
                })
                return str(existing["decision_id"])
        decision_id = self._stable_id(category, semantic_key)
        item = {
            "decision_id": decision_id,
            "stage": self.stage,
            "area": self._area(affected_paths),
            "issue": issue,
            "category": category,
            "semantic_key": semantic_key,
            "semantic_slot": semantic_slot,
            "decision_subject": decision_subject,
            "choice_domain": list(dict.fromkeys(choice_domain)),
            "description": description,
            "affected_paths": sorted(set(affected_paths)),
            "source": source,
            "policy_version": _ISSUE_POLICY_VERSION,
            "priority": _ISSUE_PRIORITY.get(issue, 1000),
            "source_payload": copy.deepcopy(payload),
            "status": "pending",
            "discovered_round": round_number,
            "question_attempts": 0,
            "request": None,
            "response": None,
        }
        self.final_confirmation = None
        self._materialized_revision = -1
        self._provisional_summary = None
        self._decisions.append(item)
        self.events.append({
            "event": "decision_detected",
            "decision_id": decision_id,
            "round": round_number,
            "source": source,
            "reason": "accepted_by_closed_issue_policy",
        })
        return decision_id

    def _selected_value(
        self, decision: dict[str, Any], response: dict[str, Any]
    ) -> dict[str, Any]:
        if response.get("action") == "free_text":
            return {"kind": "free_text", "text": str(response.get("free_text") or "").strip()}
        request_options = {
            str(item.get("option_id") or ""): str(item.get("label") or "")
            for item in (decision.get("request") or {}).get("options", [])
        }
        selected_ids = [str(item) for item in response.get("selected_option_ids", []) or []]
        labels = [request_options[item] for item in selected_ids if item in request_options]
        return {
            "kind": "selection",
            "option_ids": selected_ids,
            "labels": labels,
            "canonical_values": [
                self._canonical_effect_value(str(decision.get("issue") or ""), label)
                for label in labels
            ],
        }

    @staticmethod
    def _canonical_effect_value(issue: str, label: str) -> str:
        text = " ".join(re.findall(r"[a-z0-9]+", label.casefold()))
        if issue == "LOCATION_ROLE":
            if "city" in text:
                return "city"
            if "district" in text or "organization" in text:
                return "organization_or_district"
        if issue in {"LOGIC_RELATION", "POPULATION_CONDITION_STRUCTURE"}:
            if any(term in text for term in ("simultaneous", "separate simultaneous", "all of")):
                return "all_of"
            if any(term in text for term in ("alternative", "any of")):
                return "any_of"
        if issue in {"OUTPUT_SHAPE", "QUERY_OUTPUT_SEMANTICS"}:
            if "separate field" in text or "side by side" in text:
                return "separate_fields"
            if "repeated row" in text or "one value per" in text:
                return "repeated_rows"
        if "as recorded" in text or "no additional validation" in text:
            return "as_recorded_no_additional_validation"
        return text.replace(" ", "_")

    def _reconcile_queue(
        self,
        resolved: dict[str, Any],
        selected_value: dict[str, Any],
        *,
        round_number: int,
    ) -> None:
        """Re-evaluate every pending candidate after an atomic intent-state change."""
        selected_keys = {
            self._choice_key(item) for item in selected_value.get("labels", [])
        }
        for candidate in self._decisions:
            if candidate["status"] != "pending" or candidate is resolved:
                continue
            status: str | None = None
            reason: str | None = None
            if candidate["semantic_slot"] == resolved["semantic_slot"]:
                candidate_keys = {
                    self._choice_key(item) for item in candidate.get("choice_domain", [])
                }
                if selected_keys & candidate_keys:
                    removed_options = [
                        item
                        for item in candidate.get("choice_domain", [])
                        if self._choice_key(item) not in selected_keys
                    ]
                    if removed_options:
                        candidate["choice_domain"] = [
                            item
                            for item in candidate.get("choice_domain", [])
                            if self._choice_key(item) in selected_keys
                        ]
                        self.events.append({
                            "event": "candidate_options_pruned",
                            "decision_id": candidate["decision_id"],
                            "removed_options": removed_options,
                            "reason": "options_conflict_with_canonical_slot_value",
                            "round": round_number,
                        })
                    status = "resolved_by_implication"
                    reason = "semantic_slot_already_resolved"
                else:
                    status = "invalidated"
                    reason = "candidate_conflicts_with_canonical_slot_value"
            elif resolved["issue"] == "LOCATION_ROLE" and candidate["issue"] == "TARGET_POPULATION":
                selected_text = " ".join(selected_value.get("labels", [])).casefold()
                candidate_text = " ".join([
                    str(candidate.get("description") or ""),
                    *[str(item) for item in candidate.get("choice_domain", [])],
                ]).casefold()
                location_terms = ("city", "district", "organization")
                if any(term in selected_text for term in location_terms) and sum(
                    term in candidate_text for term in location_terms
                ) >= 2:
                    status = "resolved_by_implication"
                    reason = "location_role_resolves_population_location_choice"
            if status is None:
                continue
            candidate["status"] = status
            candidate["resolved_round"] = round_number
            candidate["resolved_by_decision_id"] = resolved["decision_id"]
            candidate["reconciliation_reason"] = reason
            self.events.append({
                "event": "queue_candidate_reconciled",
                "decision_id": candidate["decision_id"],
                "status": status,
                "reason": reason,
                "resolved_by_decision_id": resolved["decision_id"],
                "round": round_number,
            })

    def _reprioritize_queue(self, *, round_number: int) -> None:
        pending = [item for item in self._decisions if item.get("status") == "pending"]
        for item in pending:
            base = _ISSUE_PRIORITY.get(str(item.get("issue") or ""), 1000)
            downstream_bonus = 20 if item.get("category") == "BUSINESS_INTENT" else 0
            reconciliation_bonus = 10 if item.get("issue") in {
                "LOCATION_ROLE", "POPULATION_CONDITION_STRUCTURE", "SOURCE_POPULATION"
            } else 0
            item["priority"] = max(0, base - downstream_bonus - reconciliation_bonus)
        self.events.append({
            "event": "pending_queue_reprioritized",
            "round": round_number,
            "order": [
                item["decision_id"]
                for item in sorted(
                    pending,
                    key=lambda value: (
                        int(value.get("priority", 1000)),
                        str(value.get("decision_id") or ""),
                    ),
                )
            ],
            "reason": "canonical_state_revision_changed",
        })

    @staticmethod
    def _choice_key(value: Any) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))

    @staticmethod
    def _semantic_slot(issue: str, paths: list[str]) -> str:
        issue_slots = {
            "DECISION_OBJECTIVE": "business.objective",
            "TARGET_POPULATION": "business.population",
            "POPULATION_CONDITION_STRUCTURE": "business.population.condition_structure",
            "LOCATION_ROLE": "business.population.location_role",
            "METRIC_DEFINITION": "business.metric.definition",
            "CALCULATION_METHOD": "business.calculation.method",
            "LOGIC_RELATION": "business.calculation.logic",
            "GRANULARITY": "business.granularity",
            "SCOPE_SELECTION": "business.scope",
            "TIME_BASIS": "business.time_basis",
            "COMPARISON": "business.comparison",
            "OUTPUT_SHAPE": "business.output.shape",
            "QUERY_GRAIN": "query.grain",
            "AGGREGATION_PLACEMENT": "query.aggregation_placement",
            "QUERY_SELECTION_SCOPE": "query.selection_scope",
            "QUERY_TIME_SEMANTICS": "query.time_semantics",
            "QUERY_COMPARISON_SEMANTICS": "query.comparison_semantics",
            "QUERY_OUTPUT_SEMANTICS": "query.output.shape",
            "SOURCE_POPULATION": "query.source_population",
            "TECHNICAL_GROUNDING": "query.technical_grounding",
        }
        if issue in issue_slots:
            base = issue_slots[issue]
            first = next((path for path in paths if path.startswith("/")), "")
            if issue == "METRIC_DEFINITION":
                match = re.search(r"/metrics/(\d+)", first)
                return f"business.metric.{match.group(1)}.definition" if match else base
            if issue == "COMPARISON":
                match = re.search(r"/comparisons/(\d+)", first)
                return f"business.comparison.{match.group(1)}" if match else base
            if issue.startswith("QUERY_") or issue == "AGGREGATION_PLACEMENT":
                match = re.search(r"/query_blocks/(\d+)", first)
                return f"{base}.block_{match.group(1)}" if match else base
            return base
        root = ClarificationOrchestrator._area(paths)
        return f"{root}.{issue.casefold()}"

    def _stable_id(self, category: str, semantic_key: str) -> str:
        digest = hashlib.sha1(semantic_key.encode("utf-8")).hexdigest()[:10].upper()
        return f"{self.stage.upper()}-{category}-{digest}"

    @staticmethod
    def _canonical_issue_type(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        if normalized.upper() in _ISSUE_POLICIES:
            return normalized.upper()
        return _ISSUE_ALIASES.get(normalized, normalized.upper())

    @staticmethod
    def _canonical_alternatives(
        proposed: list[Any], defaults: list[Any]
    ) -> list[str]:
        source = defaults if len(defaults) >= 2 else proposed
        result: list[str] = []
        seen: set[str] = set()
        for item in source:
            label = " ".join(str(item).split())
            key = label.casefold()
            if label and key not in seen:
                seen.add(key)
                result.append(label)
        return result

    @staticmethod
    def _ensure_option_completeness(
        issue: str, description: str, alternatives: list[str]
    ) -> list[str]:
        """Add the domain baseline when a validation meaning omits it."""
        if issue != "METRIC_DEFINITION":
            return alternatives
        text = " ".join([description, *alternatives]).casefold()
        validation_terms = ("valid", "validation", "verified", "deliverability", "syntax")
        baseline_terms = ("as recorded", "stored value", "no additional")
        if any(term in text for term in validation_terms) and not any(
            term in text for term in baseline_terms
        ):
            return ["Use the values as recorded, with no additional validation", *alternatives]
        return alternatives

    @staticmethod
    def _semantic_key(
        category: str,
        issue: str,
        paths: list[str],
        decision_subject: str,
        choice_domain: list[str],
        *,
        semantic_slot: str | None = None,
    ) -> str:
        normalize = lambda value: " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))
        components = [
            category,
            normalize(semantic_slot or decision_subject),
            "|".join(sorted(normalize(item) for item in choice_domain if item)),
        ]
        return "::".join(components)

    @staticmethod
    def _area(paths: list[str]) -> str:
        first = next((path for path in paths if path.startswith("/")), "")
        return first.split("/")[1] if first.count("/") >= 1 else "general"
