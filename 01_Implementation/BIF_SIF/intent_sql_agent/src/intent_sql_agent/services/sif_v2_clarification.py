from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from intent_sql_agent.services.clarification_orchestrator import (
    ClarificationOrchestrator,
    ClarificationStateError,
)
from intent_sql_agent.services.sif_v2_grounding_coordinator import (
    SIFV2GroundingCoordinator,
    unresolved_grounding_ids,
)


class SIFV2ClarificationError(RuntimeError):
    pass


E2Responder = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]
TraceSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class SIFV2ClarificationResult:
    sif: dict[str, Any]
    decision_ledger: list[dict[str, Any]]
    rounds: int
    traces: list[dict[str, Any]]


class SIFV2ClarificationSession:
    """Orchestrator-owned dynamic queue for SIF 2.0 grounding gaps.

    A5 may observe a choice and A6 may propose a binding, but only this state
    machine selects the next decision, scopes writable slots, commits answers,
    re-audits the queue, and decides termination.
    """

    def __init__(
        self,
        agents: Any,
        *,
        sif_schema: dict[str, Any],
        max_rounds: int | None = None,
    ) -> None:
        self.agents = agents
        self.coordinator = SIFV2GroundingCoordinator(agents, sif_schema=sif_schema)
        self.max_rounds = max_rounds

    def run(
        self,
        *,
        case: dict[str, Any],
        bif: dict[str, Any],
        schema_catalog: dict[str, Any],
        e2_responder: E2Responder,
        database: Path | None = None,
        trace_sink: TraceSink | None = None,
    ) -> SIFV2ClarificationResult:
        traces: list[dict[str, Any]] = []

        def emit(value: dict[str, Any]) -> None:
            traces.append(value)
            if trace_sink is not None:
                trace_sink(value)

        initial = self.coordinator.initial_grounding(
            case=case, bif=bif, schema_catalog=schema_catalog, database=database,
        )
        sif = initial.sif
        emit({"event": "initial_grounding", "admitted": initial.admitted,
              "admission_events": initial.events})
        orchestrator = ClarificationOrchestrator(stage="sif")
        rounds = 0
        audited_signature: tuple[str, ...] | None = None

        while unresolved_grounding_ids(sif):
            if self.max_rounds is not None and rounds >= self.max_rounds:
                emit({
                    "event": "round_budget_continued",
                    "round": rounds,
                    "configured_max_rounds": self.max_rounds,
                    "action": "continue_until_grounding_is_resolved",
                })
                self.max_rounds = None
            unresolved = sorted(unresolved_grounding_ids(sif))
            signature = tuple(unresolved)
            # Existing pending decisions always have priority.  A discovery
            # audit is legal only after that queue has been exhausted.
            if orchestrator.next_pending() is None and audited_signature != signature:
                discovery = self.agents.a5_sif_v2_discover(
                    case, bif, sif, unresolved, orchestrator.decision_ledger()
                )
                raw_candidates = discovery.get("candidates", []) or []
                candidates = [
                    item for item in raw_candidates
                    if str(item.get("subject_ref")) in set(unresolved)
                ]
                orchestrator.record_discovery_audit(
                    round_number=rounds + 1, candidate_count=len(candidates)
                )
                registered = orchestrator.register_candidates(
                    candidates, source="a5_v2_discovery", round_number=rounds + 1,
                    allowed_paths=set(unresolved),
                )
                audited_signature = signature
                emit({"event": "discovery_audit", "round": rounds + 1,
                      "unresolved_grounding_ids": unresolved,
                      "observations": discovery, "registered_decisions": registered})

            decision = orchestrator.next_pending()
            if decision is None:
                raise SIFV2ClarificationError(
                    "SIF_V2_SYSTEM_GROUNDING_BLOCKED: unresolved slots remain but "
                    "A5 found no non-technical user-answerable choice: " + ", ".join(unresolved)
                )
            rounds += 1
            question_draft = self.agents.a5_sif_question(
                case, bif, sif, decision, orchestrator.decision_ledger()
            )
            request = orchestrator.build_request(
                str(decision["decision_id"]), question_draft
            )
            response = e2_responder(request, decision)
            try:
                orchestrator.bind_request(
                    str(decision["decision_id"]), request, round_number=rounds
                )
                orchestrator.resolve(
                    str(decision["decision_id"]), response, round_number=rounds
                )
            except ClarificationStateError as exc:
                raise SIFV2ClarificationError(str(exc)) from exc
            target_ids = [
                str(item) for item in decision.get("affected_paths", []) or []
                if str(item) in unresolved_grounding_ids(sif)
            ]
            if not target_ids:
                raise SIFV2ClarificationError(
                    "SIF_V2_DECISION_HAS_NO_UNRESOLVED_SLOT"
                )
            grounded = self.coordinator.ground_selected_slots(
                bif=bif, sif=sif, schema_catalog=schema_catalog,
                target_grounding_ids=target_ids,
                decision_ledger=orchestrator.decision_ledger(),
                resolution="confirmed",
                request_id=f"sif-v2-round-{rounds}", database=database,
            )
            emit({"event": "clarification_round", "round": rounds,
                  "decision": decision, "request": request, "response": response,
                  "target_grounding_ids": target_ids,
                  "admitted": grounded.admitted,
                  "admission_events": grounded.events})
            if not grounded.admitted:
                raise SIFV2ClarificationError(
                    "SIF_V2_CLARIFIED_GROUNDING_REJECTED: " + ", ".join(target_ids)
                )
            sif = grounded.sif
            audited_signature = None

        unsupported_semantic_slots = [
            str(item.get("semantic_slot_id"))
            for item in sif.get("semantic_slot_coverage", []) or []
            if item.get("status") == "unsupported"
        ]
        if unsupported_semantic_slots:
            raise SIFV2ClarificationError(
                "SIF_V2_UNREPRESENTED_SEMANTIC_SLOTS: "
                + ", ".join(unsupported_semantic_slots)
            )
        pending_semantic_slots = [
            str(item.get("semantic_slot_id"))
            for item in sif.get("semantic_slot_coverage", []) or []
            if item.get("status") == "pending"
        ]
        if pending_semantic_slots:
            raise SIFV2ClarificationError(
                "SIF_V2_UNRESOLVED_SEMANTIC_CONSTRAINTS: "
                + ", ".join(pending_semantic_slots)
            )

        # Termination is still Orchestrator-owned.  The final user confirmation
        # approves the business-language interpretation, not physical names.
        if orchestrator.needs_discovery_audit():
            orchestrator.record_discovery_audit(
                round_number=rounds + 1, candidate_count=0
            )
        orchestrator.mark_provisional_frame(
            "All requested business concepts and confirmed choices are represented; "
            "no unresolved query-meaning choice remains."
        )
        final_request = orchestrator.build_final_confirmation_request()
        final_response = e2_responder(final_request, None)
        try:
            outcome = orchestrator.record_final_confirmation(
                final_request, final_response, round_number=rounds + 1
            )
        except ClarificationStateError as exc:
            raise SIFV2ClarificationError(str(exc)) from exc
        if outcome != "confirmed" or not orchestrator.can_terminate():
            raise SIFV2ClarificationError("SIF_V2_FINAL_CONFIRMATION_NOT_ACCEPTED")
        emit({"event": "final_confirmation", "request": final_request,
              "response": final_response})
        return SIFV2ClarificationResult(
            sif=sif, decision_ledger=orchestrator.decision_ledger(),
            rounds=rounds, traces=traces,
        )
