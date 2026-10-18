from __future__ import annotations

import hashlib
import inspect
import json
import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from intent_sql_agent.agents import AgentSuite
from intent_sql_agent.contracts import ComparisonRecord, ExecutionRecord
from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.bif_reference import normalize_bif_semantic_references
from intent_sql_agent.services.bif_logic_repair import (
    apply_structured_location_role_response,
    apply_structured_logic_response,
    apply_structured_output_presentation_response,
)
from intent_sql_agent.services.bif_semantic_validator import validate_bif_semantics
from intent_sql_agent.services.bif_normalization import normalize_initial_bif
from intent_sql_agent.services.bif_atomic_authority import (
    apply_bif_atomic_authority,
    normalize_bif_atomic_authority,
    route_bif_issue_observations,
    validate_bif_atomic_admission,
)
from intent_sql_agent.services.bif_contract_coverage import BIFContractCoverageService
from intent_sql_agent.services.bif_slot_layout import attach_bif_slot_layout
from intent_sql_agent.services.cqd_bif_layout import validate_cqd_bif_layout_coverage
from intent_sql_agent.services.change_set import apply_sif_system_change_set
from intent_sql_agent.services.clarification_orchestrator import (
    ClarificationOrchestrator,
    ClarificationStateError,
)
from intent_sql_agent.services.contract_validator import ContractError, validate_contract
from intent_sql_agent.services.decision_compliance import (
    remove_unrequested_bif_outputs,
    validate_decision_compliance,
    validate_unrequested_bif_outputs,
)
from intent_sql_agent.services.grounding_validator import validate_literal_domains
from intent_sql_agent.services.frame_state import (
    create_frame_envelope,
)
from intent_sql_agent.services.schema_catalog import load_sqlite_schema
from intent_sql_agent.services.schema_documented_repair import repair_schema_documented_grounding
from intent_sql_agent.services.sif_coverage import validate_sif_coverage
from intent_sql_agent.services.sif_repair import (
    apply_source_population_selection,
    canonicalize_source_population_selection,
    missing_required_sources,
    repair_bif_logic_operators,
    repair_merged_independent_conditions,
    repair_malformed_boolean_expressions,
    repair_missing_column_joins,
    repair_missing_output_traceability,
    repair_missing_population_traceability,
    repair_missing_query_block_outputs,
    repair_non_exact_question_values,
    repair_unrequested_supporting_projections,
    repair_unrequested_with_ties,
    selected_population_tables,
)
from intent_sql_agent.services.sif_sql_compiler import UnsupportedSIFError, compile_sif
from intent_sql_agent.services.sif_v2_clarification import (
    SIFV2ClarificationError,
    SIFV2ClarificationSession,
)
from intent_sql_agent.services.sif_v2_lowerer import lower_sif_v2_to_query_plan
from intent_sql_agent.services.sif_transaction import reduce_sif_candidate
from intent_sql_agent.services.query_intent_layout import (
    apply_sif_links,
    create_sif_overlay,
    iter_query_nodes,
)
from intent_sql_agent.services.query_intent_compiler import (
    UnsupportedQueryIntentLayout,
    compile_query_intent_layout,
)
from intent_sql_agent.services.sif_contract_normalization import normalize_sif_contract_shape
from intent_sql_agent.services.sif_reference import normalize_sif_intent_references
from intent_sql_agent.services.source_ambiguity import detect_source_population_ambiguities
from intent_sql_agent.services.e2_oracle import build_e2_oracle_context
from intent_sql_agent.services.sql_executor import ReadOnlySQLiteExecutor
from intent_sql_agent.services.direct_sif_compiler import (
    DirectSIFCompilationError,
    apply_cqd_sql_semantics,
    compile_direct_sif,
    normalize_direct_sif_bundle,
    validate_direct_sif_bundle,
)
from intent_sql_agent.services.direct_sif_candidate_resolver import (
    resolve_column_candidates,
)
from intent_sql_agent.services.direct_sif_binding_intents import (
    expand_plural_output_bindings,
)
from intent_sql_agent.services.cqd_sql_direct_compiler import (
    compile_cqd_directly_to_sql,
)
from intent_sql_agent.services.unified_sql_synthesizer import UnifiedSQLSynthesizer
from intent_sql_agent.services.sif_clarification_service import SIFClarificationService
from intent_sql_agent.services.sql_self_correction import SQLSelfCorrection

from .evaluation_runner import EvaluationRunner


class PipelineRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class FullCaseResult:
    comparison: ComparisonRecord
    final_sql: str
    preflight: ExecutionRecord
    case_dir: Path


class FullCaseRunner:
    def __init__(
        self,
        agents: AgentSuite,
        executor: ReadOnlySQLiteExecutor,
        *,
        max_bif_rounds: int | None = None,
        max_sif_rounds: int | None = None,
        sif_layout: Literal["v1", "v2", "v3"] = "v1",
    ) -> None:
        self.agents = agents
        self.executor = executor
        self.max_bif_rounds = self._normalize_round_limit(max_bif_rounds)
        self.max_sif_rounds = self._normalize_round_limit(max_sif_rounds)
        if sif_layout not in {"v1", "v2", "v3"}:
            raise ValueError("sif_layout must be v1, v2, or v3")
        self.sif_layout = sif_layout

    def run(
        self,
        *,
        case: dict[str, Any],
        database: Path,
        logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"] = "multiset",
    ) -> FullCaseResult:
        logger.set_step("P00")
        self._step(logger, "P00", "CASE_LOADED")
        public_input = {
            "question_id": case["question_id"],
            "db_id": case["db_id"],
            "question": case["question"],
            "difficulty": case.get("difficulty"),
        }
        logger.write_json("inputs/input_without_evidence.json", public_input)
        schema_catalog = load_sqlite_schema(
            database, question=case["question"], evidence=case.get("evidence")
        )
        logger.write_json("inputs/schema_snapshot.json", schema_catalog)

        try:
            logger.set_step("P01")
            bif_initial_analysis_strategy = "combined_draft_and_observations"
            a1_signature = inspect.signature(self.agents.a1_bif_analyze)
            if "schema_catalog" in a1_signature.parameters:
                bif_analysis = self.agents.a1_bif_analyze(
                    case, schema_catalog=schema_catalog
                )
            else:
                bif_analysis = self.agents.a1_bif_analyze(case)
            semantic_frontend_result = getattr(
                self.agents, "semantic_frontend_result", None
            )
            semantic_frontend_active = bool(
                getattr(self.agents, "semantic_frontend_active", False)
                and semantic_frontend_result
            )
            bif_draft = bif_analysis["draft_bif"]
            direct_sif_understanding = getattr(
                self.agents, "direct_sif_understanding", None
            )
            if (
                self.sif_layout == "v3" and semantic_frontend_active
                and isinstance(self.agents, AgentSuite)
            ):
                return self._run_cqd_direct_sql(
                    case=case,
                    database=database,
                    logger=logger,
                    comparison_policy=comparison_policy,
                    schema_catalog=schema_catalog,
                    semantic_frontend_result=semantic_frontend_result,
                )
            # Compatibility only for injected legacy test doubles. Production
            # AgentSuite always uses the natural-CQD direct SQL path above.
            if (
                self.sif_layout == "v3" and semantic_frontend_active
                and direct_sif_understanding
            ):
                return self._run_direct_sif(
                    case=case, database=database, logger=logger,
                    comparison_policy=comparison_policy,
                    understanding=direct_sif_understanding,
                    schema_catalog=schema_catalog,
                    semantic_frontend_result=semantic_frontend_result,
                )
            if (
                self.sif_layout == "v3"
                and semantic_frontend_active
                and bif_draft.get("schema_version") == "4.0"
            ):
                return self._run_query_layout_v3(
                    case=case,
                    database=database,
                    logger=logger,
                    comparison_policy=comparison_policy,
                    bif_layout=bif_draft,
                    schema_catalog=schema_catalog,
                    semantic_frontend_result=semantic_frontend_result,
                )
            bif_draft, initial_bif_normalization_repairs = normalize_initial_bif(
                case["question"], bif_draft
            )
            bif_draft, atomic_authority_repairs = normalize_bif_atomic_authority(
                case["question"], bif_draft
            )
            if semantic_frontend_active:
                bif_draft, slot_layout_report = attach_bif_slot_layout(
                    semantic_frontend_result["confirmed_cqd"], bif_draft
                )
                semantic_frontend_result["bif_slot_layout_report"] = slot_layout_report
            bif_analysis["draft_bif"] = bif_draft
            initial_bif_observations = bif_analysis["issue_observations"]
            if semantic_frontend_active:
                contract_assessments = list(
                    semantic_frontend_result.get("contract_coverage", [])
                )
                contract_observations = []
                expected_contracts = len(contract_assessments)
                layout_coverage = validate_cqd_bif_layout_coverage(
                    semantic_frontend_result["confirmed_cqd"], bif_draft
                )
                logger.write_json(
                    "frames/semantic_frontend_result.json", semantic_frontend_result
                )
                logger.write_json("frames/cqd_to_bif_coverage.json", layout_coverage)
                logger.write_json(
                    "frames/bif_slot_layout.json", bif_draft.get("slot_layout", {})
                )
                logger.write_json(
                    "frames/bif_slot_layout_report.json",
                    semantic_frontend_result.get("bif_slot_layout_report", {}),
                )
            else:
                bif_contract_service = BIFContractCoverageService()
                contract_assessments = bif_contract_service.inspect(
                    original_question=case["question"],
                    bif=bif_draft,
                    detector=self.agents.a2_bif_contract_detect,
                )
                contract_observations = bif_contract_service.detected_observations(
                    contract_assessments
                )
                expected_contracts = len(bif_contract_service.contracts)
            bif_contract_metrics = {
                "expected": expected_contracts,
                "actual": len(contract_assessments),
                "detected": sum(
                    item["status"] == "detected" for item in contract_assessments
                ),
                "safe": sum(item["status"] == "safe" for item in contract_assessments),
                "not_applicable": sum(
                    item["status"] == "not_applicable" for item in contract_assessments
                ),
                "deferred_to_sif": sum(
                    item.get("disposition") == "deferred_to_sif"
                    for item in contract_assessments
                ),
            }
            logger.write_json(
                "frames/bif_contract_coverage.json", contract_assessments
            )
            logger.write_json(
                "frames/bif_contract_observations.json", contract_observations
            )
            self._step(
                logger,
                "P01",
                "BIF_CONTRACT_COVERAGE_COMPLETED",
                bif_contract_metrics,
            )
            logger.write_json(
                "frames/bif_initial_issue_observations.json",
                initial_bif_observations,
            )
            logger.write_json("frames/bif_analysis_bundle.json", bif_analysis)
            if initial_bif_normalization_repairs:
                logger.write_json(
                    "frames/bif_initial_normalization_repairs.json",
                    initial_bif_normalization_repairs,
                )
                self._step(
                    logger,
                    "P01",
                    "BIF_INITIAL_DEFAULTS_REMOVED_BY_CODE",
                    {"repairs": len(initial_bif_normalization_repairs)},
                )
            if atomic_authority_repairs:
                logger.write_json(
                    "frames/bif_initial_atomic_authority_repairs.json",
                    atomic_authority_repairs,
                )
                self._step(
                    logger, "P01", "BIF_ATOMIC_AUTHORITY_APPLIED",
                    {"repairs": len(atomic_authority_repairs)},
                )
            self._step(logger, "P01", "A1_BIF_AND_OBSERVATIONS_ANALYZED")
            logger.write_json("frames/bif_draft.json", bif_draft)

            bif_current = bif_draft
            bif_envelope = create_frame_envelope(bif_current, layer="bif")
            logger.write_json("frames/bif_envelope_initial.json", bif_envelope)
            bif_semantic_findings = (
                []
                if semantic_frontend_active
                else validate_bif_semantics(case["question"], bif_current)
            )
            initial_bif_semantic_finding_count = len(bif_semantic_findings)
            logger.write_json(
                "frames/bif_semantic_findings_initial.json",
                bif_semantic_findings,
            )
            if bif_semantic_findings:
                self._step(
                    logger,
                    "P01",
                    "BIF_SEMANTIC_FINDINGS_DETECTED",
                    {
                        "count": len(bif_semantic_findings),
                        "issues": sorted(
                            {str(item.get("issue")) for item in bif_semantic_findings}
                        ),
                    },
                )
            bif_clarifier = ClarificationOrchestrator(stage="bif")
            bif_clarifier.register_findings(
                bif_semantic_findings,
                source="bif_semantic_validator",
                allowed_paths=self._frame_paths(bif_draft),
            )
            initial_candidates, initial_deferred_observations = route_bif_issue_observations(
                bif_draft,
                [
                    *self._discovery_candidates(initial_bif_observations),
                    *contract_observations,
                ],
                bif_semantic_findings,
            )
            if initial_deferred_observations:
                logger.write_json(
                    "frames/bif_observations_deferred_from_user.json",
                    initial_deferred_observations,
                )
            bif_clarifier.register_candidates(
                initial_candidates,
                source="a1_initial_observations",
                round_number=0,
                allowed_paths=self._frame_paths(bif_draft),
            )
            bif_rounds = 0
            bif_decision_ledger: list[dict[str, Any]] = []
            while True:
                logger.set_step("P02")
                if self.max_bif_rounds is not None and bif_rounds >= self.max_bif_rounds:
                    self._step(logger, "P02", "BIF_ROUND_BUDGET_CONTINUED", {
                        "configured_limit": self.max_bif_rounds,
                        "round": bif_rounds,
                    })
                    self.max_bif_rounds = None
                bif_rounds += 1
                round_number = bif_rounds
                required_decision = bif_clarifier.next_pending()
                discovery = None
                if required_decision is None and bif_clarifier.needs_discovery_audit():
                    discovery = (
                        {"candidates": []}
                        if semantic_frontend_active
                        else self._a2_discover(
                            case, bif_draft, bif_clarifier.decision_ledger()
                        )
                    )
                    candidates, deferred_observations = route_bif_issue_observations(
                        bif_draft,
                        self._discovery_candidates(discovery),
                        validate_bif_semantics(case["question"], bif_draft),
                    )
                    if deferred_observations:
                        logger.write_json(
                            f"frames/bif_rounds/round_{round_number:02d}/deferred_observations.json",
                            deferred_observations,
                        )
                    bif_clarifier.record_discovery_audit(
                        round_number=round_number,
                        candidate_count=len(candidates),
                    )
                    bif_clarifier.register_candidates(
                        candidates,
                        source="a2_discovery",
                        round_number=round_number,
                        allowed_paths=self._frame_paths(bif_draft),
                    )
                    required_decision = bif_clarifier.next_pending()

                if required_decision is None and bif_clarifier.ready_for_materialization():
                    self._step(
                        logger,
                        "P02",
                        "BIF_QUEUE_STABLE_FOR_MATERIALIZATION",
                        {"round": round_number},
                    )
                    break
                if required_decision is not None:
                    question_draft = self._a2_question(
                        case,
                        bif_draft,
                        required_decision,
                        bif_clarifier.decision_ledger(),
                    )
                    request = bif_clarifier.build_request(
                        str(required_decision["decision_id"]), question_draft
                    )
                    bif_interaction = self._interaction_envelope(required_decision, request)
                    decision_id = str(required_decision["decision_id"])
                else:
                    self._step(logger, "P02", "BIF_NO_NEXT_ACTION_CONTINUED", {
                        "round": round_number,
                    })
                    break

                round_root = f"frames/bif_rounds/round_{round_number:02d}"
                logger.write_json(f"{round_root}/interaction.json", bif_interaction)
                if discovery is not None:
                    logger.write_json(
                        f"{round_root}/issue_observations.json", discovery
                    )
                logger.write_json(
                    f"{round_root}/policy_decision_events.json",
                    bif_clarifier.events,
                )
                logger.write_json(
                    f"{round_root}/clarification_request.json",
                    bif_interaction["clarification_request"],
                )
                logger.write_json("frames/bif_interaction.json", bif_interaction)
                self._step(
                    logger,
                    "P02",
                    "BIF_ORCHESTRATOR_ACTION_SELECTED",
                    {"round": round_number, "decision_id": decision_id},
                )

                bif_oracle_context = build_e2_oracle_context(
                    case["SQL"],
                    bif_interaction,
                    original_question=case["question"],
                )
                bif_response = self.agents.e2_user(
                    mode="BIF_CLARIFICATION",
                    case=case,
                    clarification=bif_interaction,
                    oracle_context=bif_oracle_context,
                )
                logger.write_json(f"{round_root}/user_response.json", bif_response)
                logger.write_json("frames/bif_user_response.json", bif_response)
                self._step(
                    logger,
                    "P02",
                    "E2_BIF_RESPONDED",
                    {"round": round_number},
                )
                try:
                    if required_decision is not None:
                        bif_clarifier.bind_request(
                            decision_id, request, round_number=round_number
                        )
                        bif_clarifier.resolve(
                            decision_id,
                            bif_response,
                            round_number=round_number,
                            context={},
                        )
                        self._write_clarification_transaction(
                            logger, round_root, bif_clarifier
                        )
                except ClarificationStateError as exc:
                    raise PipelineRunError(f"BIF_CLARIFICATION_STATE_ERROR: {exc}") from exc
                bif_decision_ledger = bif_clarifier.decision_ledger()
                logger.write_json(
                    f"{round_root}/decision_record.json",
                    (
                        bif_decision_ledger[-1]
                        if required_decision is not None and bif_decision_ledger
                        else bif_clarifier.final_confirmation
                    ),
                )
                logger.write_json("frames/bif_decision_ledger.json", bif_decision_ledger)
                logger.write_json(
                    "frames/bif_clarification_state.json", bif_clarifier.snapshot()
                )
                logger.write_json(
                    f"{round_root}/orchestrator_state.json",
                    bif_clarifier.snapshot(),
                )
                self._step(
                    logger,
                    "P02",
                    "BIF_DECISION_RECORDED",
                    {"round": round_number, "decision_id": decision_id},
                )
            if semantic_frontend_active:
                # Confirmed CQD is the sole semantic authority.  The compatibility
                # BIF sections and canonical slot layout must not be reopened by
                # the legacy BIF clarification/materialization loop.
                bif_confirmed, bif_reference_repairs = normalize_bif_semantic_references(
                    copy.deepcopy(bif_draft)
                )
                bif_confirmed.setdefault("readiness", {}).update({
                    "state": "ready", "blocking_paths": [], "reason": None,
                })
                bif_assembly = {
                    "confirmed_bif": copy.deepcopy(bif_confirmed),
                    "applied_decision_ids": [],
                    "strategy": "confirmed_cqd_slot_layout_projection",
                }
                bif_invariant_report = {
                    "status": "complete",
                    "authority": "confirmed_cqd",
                    "slot_layout": semantic_frontend_result.get(
                        "bif_slot_layout_report", {}
                    ),
                }
                logger.write_json("frames/bif_assembly_bundle.json", bif_assembly)
                logger.write_json("frames/bif_decision_invariants.json", bif_invariant_report)
                logger.write_json("frames/bif_semantic_findings_final.json", [])
            else:
                (
                    bif_confirmed,
                    bif_assembly,
                    bif_reference_repairs,
                    bif_invariant_report,
                    bif_rounds,
                ) = self._materialize_bif_until_compliant(
                    case=case,
                    bif_draft=bif_draft,
                    clarifier=bif_clarifier,
                    logger=logger,
                    rounds_completed=bif_rounds,
                )
            bif_confirmed, bif_rounds, bif_decision_ledger = (
                self._finalize_bif_with_revision_loop(
                    case=case,
                    bif_draft=bif_draft,
                    bif=bif_confirmed,
                    clarifier=bif_clarifier,
                    logger=logger,
                    rounds_completed=bif_rounds,
                )
            )
            if semantic_frontend_active:
                bif_rounds = int(semantic_frontend_result.get("rounds", 0))
            logger.write_json(
                "frames/bif_final_orchestrator_state.json",
                bif_clarifier.snapshot(),
            )
            logger.write_json(
                "frames/bif_clarification_state.json", bif_clarifier.snapshot()
            )
            logger.write_json(
                "frames/bif_canonical_intent_state.json",
                bif_clarifier.snapshot()["canonical_intent_state"],
            )
            bif_envelope = create_frame_envelope(
                bif_confirmed,
                layer="bif",
                revision=max(1, bif_clarifier.snapshot()["control"]["resolution_revision"]),
            )
            bif_envelope["locked_decisions"] = [
                {
                    "decision_id": item["decision_id"],
                    "paths": item.get("affected_paths", []),
                    "confirmed_by": "E2",
                    "confirmed_at_round": item.get("resolved_round"),
                }
                for item in bif_decision_ledger
            ]
            logger.write_json("frames/bif_envelope_confirmed.json", bif_envelope)
            if bif_reference_repairs:
                logger.write_json(
                    "frames/bif_semantic_reference_repairs.json",
                    bif_reference_repairs,
                )
                self._step(
                    logger,
                    "P02",
                    "BIF_SEMANTIC_REFERENCES_NORMALIZED",
                    {"repairs": len(bif_reference_repairs)},
                )
            logger.write_json("frames/bif_confirmed.json", bif_confirmed)
            locked_bif_hash = hashlib.sha256(
                json.dumps(
                    bif_confirmed,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if bif_confirmed.get("readiness", {}).get("state") != "ready":
                self._step(logger, "P02", "BIF_NOT_READY_CONTINUED", {
                    "readiness": bif_confirmed.get("readiness", {}),
                })
            self._step(logger, "P02", "BIF_CONFIRMED", {"rounds": bif_rounds})

            if self.sif_layout == "v2":
                return self._run_sif_v2_after_bif(
                    case=case, database=database, logger=logger,
                    comparison_policy=comparison_policy,
                    bif_confirmed=bif_confirmed,
                    bif_rounds=bif_rounds,
                    schema_catalog=schema_catalog,
                    locked_bif_hash=locked_bif_hash,
                    bif_contract_metrics=bif_contract_metrics,
                )

            logger.set_step("P03")
            sif_draft = self._draft_sif(
                case, bif_confirmed, schema_catalog, bif_envelope
            )
            full_db_schema_model_calls = 1
            self._step(
                logger,
                "P03",
                "FULL_DB_SCHEMA_MODEL_INPUT",
                {"agent": "A4", "ordinal": full_db_schema_model_calls},
            )
            logger.write_json("frames/sif_draft.json", sif_draft)
            sif_draft, sif_reference_repairs = normalize_sif_intent_references(
                sif_draft, bif_confirmed
            )
            sif_draft, schema_documented_repairs = repair_schema_documented_grounding(
                sif_draft, bif_confirmed, schema_catalog
            )
            sif_draft, output_projection_repairs = repair_unrequested_supporting_projections(
                sif_draft, bif_confirmed
            )
            if sif_reference_repairs or schema_documented_repairs or output_projection_repairs:
                logger.write_json(
                    "frames/sif_deterministic_grounding_repairs.json",
                    sif_reference_repairs + schema_documented_repairs + output_projection_repairs,
                )
                logger.write_json("frames/sif_draft_normalized.json", sif_draft)
                self._step(
                    logger,
                    "P03",
                    "SIF_DRAFT_NORMALIZED_BY_CODE",
                    {
                        "repairs": len(sif_reference_repairs)
                        + len(schema_documented_repairs)
                        + len(output_projection_repairs)
                    },
                )
            self._step(logger, "P03", "A4_SIF_DRAFTED")

            sif_current = sif_draft
            sif_rounds = 0
            reviewed_source_ambiguities: set[str] = set()
            reviewed_source_ambiguity_records: list[dict[str, Any]] = []
            locked_population_sources: set[str] = set()
            deterministically_applied_sif_decisions: set[str] = set()
            sif_transaction_log: list[dict[str, Any]] = []
            sif_decision_ledger: list[dict[str, Any]] = []
            sif_clarifier = ClarificationOrchestrator(stage="sif")
            deferred_sif_candidates = bif_clarifier.consume_handoffs(
                target_stage="sif"
            )
            if deferred_sif_candidates:
                sif_clarifier.register_candidates(
                    [
                        {
                            "issue_type": item["issue_type"],
                            "subject_ref": item["subject_ref"],
                            "description": item["description"],
                            "alternatives": item["alternatives"],
                        }
                        for item in deferred_sif_candidates
                    ],
                    source="bif_deferred_to_sif",
                    round_number=0,
                    allowed_paths=(
                        self._frame_paths(bif_confirmed)
                        | self._frame_paths(sif_draft)
                    ),
                )
                logger.write_json(
                    "frames/bif_to_sif_handoffs.json", deferred_sif_candidates
                )
            sif_system_transaction_count = 0
            sif_system_operation_count = 0
            bif_reentry_count = 0
            while True:
                logger.set_step("P04")
                if self.max_sif_rounds is not None and sif_rounds >= self.max_sif_rounds:
                    self._step(logger, "P04", "SIF_ROUND_BUDGET_CONTINUED", {
                        "configured_limit": self.max_sif_rounds,
                        "round": sif_rounds,
                    })
                    self.max_sif_rounds = None
                sif_rounds += 1
                round_number = sif_rounds
                source_ambiguities = [
                    item
                    for item in detect_source_population_ambiguities(
                        bif_confirmed, sif_draft, schema_catalog
                    )
                    if item["ambiguity_id"] not in reviewed_source_ambiguities
                ]
                sif_clarifier.register_source_ambiguities(
                    source_ambiguities, round_number=round_number
                )
                required_decision = sif_clarifier.next_pending()
                discovery = None
                if required_decision is None and sif_clarifier.needs_discovery_audit():
                    discovery = self._a5_discover(
                        case,
                        bif_confirmed,
                        sif_draft,
                        sif_clarifier.decision_ledger(),
                        source_ambiguities,
                    )
                    candidates = self._discovery_candidates(discovery)
                    if semantic_frontend_active:
                        candidates = self._route_post_bif_candidates(candidates)
                    sif_clarifier.record_discovery_audit(
                        round_number=round_number,
                        candidate_count=len(candidates),
                    )
                    sif_clarifier.register_candidates(
                        candidates,
                        source="a5_discovery",
                        round_number=round_number,
                        allowed_paths=(
                            self._frame_paths(bif_confirmed)
                            | self._frame_paths(sif_draft)
                        ),
                    )
                    bif_reentry = sif_clarifier.consume_handoffs(target_stage="bif")
                    system_handoffs = sif_clarifier.consume_handoffs(
                        target_stage="system"
                    )
                    if system_handoffs:
                        logger.write_json(
                            f"frames/sif_rounds/round_{round_number:02d}/system_owned_observations.json",
                            system_handoffs,
                        )
                    if bif_reentry:
                        bif_reentry_count += 1
                        logger.write_json(
                            f"frames/sif_rounds/round_{round_number:02d}/bif_reentry_request.json",
                            bif_reentry,
                        )
                        logger.write_json(
                            f"frames/bif_reentries/revision_{bif_reentry_count:02d}/abandoned_sif.json",
                            {
                                "status": "abandoned",
                                "reason": "BIF_REENTRY_REQUIRED",
                                "sif_draft": sif_draft,
                                "sif_decision_ledger": sif_clarifier.decision_ledger(),
                            },
                        )
                        reentry_candidates = [
                            {
                                "issue_type": item["issue_type"],
                                "subject_ref": item["subject_ref"],
                                "description": item["description"],
                                "alternatives": item["alternatives"],
                            }
                            for item in bif_reentry
                        ]
                        registered_reentry = bif_clarifier.register_candidates(
                            reentry_candidates,
                            source="sif_bif_reentry",
                            round_number=bif_rounds + 1,
                            allowed_paths=self._frame_paths(bif_confirmed),
                        )
                        if not registered_reentry or bif_clarifier.next_pending() is None:
                            raise PipelineRunError(
                                "BIF_REENTRY_ROUTING_FAILED: no BIF decision was activated"
                            )
                        bif_rounds = self._continue_bif_clarification(
                            case=case,
                            bif_draft=bif_confirmed,
                            clarifier=bif_clarifier,
                            logger=logger,
                            rounds_completed=bif_rounds,
                        )
                        (
                            bif_confirmed,
                            bif_assembly,
                            bif_reference_repairs,
                            bif_invariant_report,
                            bif_rounds,
                        ) = self._materialize_bif_until_compliant(
                            case=case,
                            bif_draft=bif_confirmed,
                            clarifier=bif_clarifier,
                            logger=logger,
                            rounds_completed=bif_rounds,
                        )
                        bif_confirmed, bif_rounds, bif_decision_ledger = (
                            self._finalize_bif_with_revision_loop(
                                case=case,
                                bif_draft=bif_confirmed,
                                bif=bif_confirmed,
                                clarifier=bif_clarifier,
                                logger=logger,
                                rounds_completed=bif_rounds,
                            )
                        )
                        logger.write_json(
                            f"frames/bif_reentries/revision_{bif_reentry_count:02d}/confirmed_bif.json",
                            bif_confirmed,
                        )
                        bif_envelope = create_frame_envelope(
                            bif_confirmed,
                            layer="bif",
                            revision=max(
                                1,
                                bif_clarifier.snapshot()["control"]["resolution_revision"],
                            ),
                        )
                        bif_envelope["locked_decisions"] = [
                            {
                                "decision_id": item["decision_id"],
                                "paths": item.get("affected_paths", []),
                                "confirmed_by": "E2",
                                "confirmed_at_round": item.get("resolved_round"),
                            }
                            for item in bif_decision_ledger
                        ]
                        logger.write_json("frames/bif_confirmed.json", bif_confirmed)
                        logger.write_json("frames/bif_envelope_confirmed.json", bif_envelope)
                        logger.write_json(
                            "frames/bif_decision_ledger.json", bif_decision_ledger
                        )
                        logger.write_json(
                            "frames/bif_clarification_state.json",
                            bif_clarifier.snapshot(),
                        )
                        locked_bif_hash = hashlib.sha256(
                            json.dumps(
                                bif_confirmed,
                                sort_keys=True,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()
                        full_db_schema_model_calls += 1
                        sif_draft = self._draft_sif(
                            case,
                            bif_confirmed,
                            schema_catalog,
                            bif_envelope,
                        )
                        sif_draft, _ = normalize_sif_intent_references(
                            sif_draft, bif_confirmed
                        )
                        sif_draft, _ = repair_schema_documented_grounding(
                            sif_draft, bif_confirmed, schema_catalog
                        )
                        sif_draft, _ = repair_unrequested_supporting_projections(
                            sif_draft, bif_confirmed
                        )
                        logger.write_json(
                            f"frames/bif_reentries/revision_{bif_reentry_count:02d}/regenerated_sif_draft.json",
                            sif_draft,
                        )
                        sif_clarifier = ClarificationOrchestrator(stage="sif")
                        deferred_after_reentry = bif_clarifier.consume_handoffs(
                            target_stage="sif"
                        )
                        if deferred_after_reentry:
                            sif_clarifier.register_candidates(
                                [
                                    {
                                        "issue_type": item["issue_type"],
                                        "subject_ref": item["subject_ref"],
                                        "description": item["description"],
                                        "alternatives": item["alternatives"],
                                    }
                                    for item in deferred_after_reentry
                                ],
                                source="bif_reentry_deferred_to_sif",
                                round_number=round_number,
                                allowed_paths=(
                                    self._frame_paths(bif_confirmed)
                                    | self._frame_paths(sif_draft)
                                ),
                            )
                        sif_decision_ledger = []
                        sif_transaction_log = []
                        reviewed_source_ambiguities.clear()
                        reviewed_source_ambiguity_records.clear()
                        locked_population_sources.clear()
                        deterministically_applied_sif_decisions.clear()
                        self._step(
                            logger,
                            "P04",
                            "BIF_REENTRY_COMPLETED_AND_SIF_REGENERATED",
                            {"round": round_number, "bif_revision": bif_rounds},
                        )
                        continue
                    required_decision = sif_clarifier.next_pending()

                if required_decision is None and sif_clarifier.ready_for_materialization():
                    self._step(
                        logger,
                        "P04",
                        "SIF_QUEUE_STABLE_FOR_MATERIALIZATION",
                        {"round": round_number},
                    )
                    break
                if required_decision is not None:
                    question_draft = self._a5_question(
                        case,
                        bif_confirmed,
                        sif_draft,
                        required_decision,
                        sif_clarifier.decision_ledger(),
                        schema_catalog,
                        source_ambiguities,
                    )
                    request = sif_clarifier.build_request(
                        str(required_decision["decision_id"]), question_draft
                    )
                    sif_interaction = self._interaction_envelope(required_decision, request)
                    decision_id = str(required_decision["decision_id"])
                else:
                    self._step(logger, "P04", "SIF_NO_NEXT_ACTION_CONTINUED", {
                        "round": round_number,
                    })
                    break

                round_root = f"frames/sif_rounds/round_{round_number:02d}"
                logger.write_json(f"{round_root}/interaction.json", sif_interaction)
                if discovery is not None:
                    logger.write_json(f"{round_root}/issue_observations.json", discovery)
                logger.write_json(
                    f"{round_root}/policy_decision_events.json",
                    sif_clarifier.events,
                )
                logger.write_json(f"{round_root}/source_ambiguities.json", source_ambiguities)
                logger.write_json(
                    f"{round_root}/clarification_request.json",
                    sif_interaction["clarification_request"],
                )
                logger.write_json("frames/sif_interaction.json", sif_interaction)
                self._step(
                    logger,
                    "P04",
                    "SIF_ORCHESTRATOR_ACTION_SELECTED",
                    {"round": round_number, "decision_id": decision_id},
                )
                sif_oracle_context = build_e2_oracle_context(
                    case["SQL"], sif_interaction, source_ambiguities
                )
                sif_response = self.agents.e2_user(
                    mode="SIF_CLARIFICATION",
                    case=case,
                    clarification=sif_interaction,
                    confirmed_bif_summary={
                        "decision_objective": bif_confirmed["decision_objective"],
                        "target_population": bif_confirmed["target_population"],
                        "metrics": bif_confirmed["metrics"],
                        "scope": bif_confirmed["scope"],
                        "time_basis": bif_confirmed["time_basis"],
                        "comparisons": bif_confirmed["comparisons"],
                        "output_expectation": bif_confirmed["output_expectation"],
                    },
                    oracle_context=sif_oracle_context,
                )
                logger.write_json(f"{round_root}/user_response.json", sif_response)
                logger.write_json("frames/sif_user_response.json", sif_response)
                self._step(
                    logger,
                    "P04",
                    "E2_SIF_RESPONDED",
                    {"round": round_number},
                )

                source_payload = (
                    (required_decision.get("source_payload", {}) or {})
                    if required_decision is not None
                    else {}
                )
                canonical_source_response = canonicalize_source_population_selection(
                    sif_response,
                    request,
                    source_ambiguities,
                    ambiguity_id=str(source_payload.get("ambiguity_id") or "") or None,
                )
                locked_population_sources.update(
                    selected_population_tables(
                        canonical_source_response, source_ambiguities
                    )
                )
                selected_source_tables = selected_population_tables(
                    canonical_source_response, source_ambiguities
                )
                if selected_source_tables:
                    selected_sif, source_selection_events = (
                        apply_source_population_selection(
                            sif_draft,
                            canonical_source_response,
                            source_ambiguities,
                            schema_catalog,
                        )
                    )
                    if selected_sif is None:
                        raise PipelineRunError(
                            "SIF_SOURCE_SELECTION_APPLICATION_FAILED: confirmed source "
                            "choice could not be applied to the draft SIF"
                        )
                    sif_draft = selected_sif
                    deterministically_applied_sif_decisions.add(decision_id)
                    sif_transaction_log.append({
                        "stage": "structured_source_selection",
                        "round": round_number,
                        "events": source_selection_events,
                    })
                    logger.write_json(
                        f"{round_root}/source_selection_transaction.json",
                        source_selection_events,
                    )
                    logger.write_json(
                        f"{round_root}/sif_after_source_selection.json", sif_draft
                    )
                    self._step(
                        logger,
                        "P04",
                        "STRUCTURED_SOURCE_SELECTION_APPLIED",
                        {
                            "round": round_number,
                            "tables": sorted(locked_population_sources),
                        },
                    )
                try:
                    if required_decision is not None:
                        sif_clarifier.bind_request(
                            decision_id, request, round_number=round_number
                        )
                        sif_clarifier.resolve(
                            decision_id,
                            sif_response,
                            round_number=round_number,
                            context={"source_ambiguities": source_ambiguities},
                        )
                        self._write_clarification_transaction(
                            logger, round_root, sif_clarifier
                        )
                except ClarificationStateError as exc:
                    raise PipelineRunError(f"SIF_CLARIFICATION_STATE_ERROR: {exc}") from exc
                sif_decision_ledger = sif_clarifier.decision_ledger()
                logger.write_json(
                    f"{round_root}/decision_record.json",
                    (
                        sif_decision_ledger[-1]
                        if required_decision is not None and sif_decision_ledger
                        else sif_clarifier.final_confirmation
                    ),
                )
                logger.write_json("frames/sif_decision_ledger.json", sif_decision_ledger)
                logger.write_json(
                    "frames/sif_clarification_state.json", sif_clarifier.snapshot()
                )
                logger.write_json(
                    f"{round_root}/orchestrator_state.json",
                    sif_clarifier.snapshot(),
                )
                if required_decision is not None:
                    resolved_ambiguity_id = str(
                        source_payload.get("ambiguity_id") or ""
                    )
                    if resolved_ambiguity_id:
                        reviewed_source_ambiguities.add(resolved_ambiguity_id)
                        reviewed_source_ambiguity_records.append(source_payload)
                self._step(
                    logger,
                    "P04",
                    "SIF_DECISION_RECORDED",
                    {"round": round_number, "decision_id": decision_id},
                )
            resolved_sif_decision_ids = {
                str(item["decision_id"]) for item in sif_decision_ledger
            }
            structured_decisions_complete = bool(resolved_sif_decision_ids) and (
                resolved_sif_decision_ids == deterministically_applied_sif_decisions
            )
            if structured_decisions_complete:
                sif_assembly = {
                    "confirmed_sif": copy.deepcopy(sif_draft),
                    "applied_decision_ids": sorted(resolved_sif_decision_ids),
                    "assembly_strategy": "deterministic_structured_decisions",
                }
                self._step(
                    logger,
                    "P04",
                    "SIF_STRUCTURED_DECISIONS_MATERIALIZED_BY_CODE",
                    {"decision_ids": sorted(resolved_sif_decision_ids)},
                )
            else:
                full_db_schema_model_calls += 1
                self._step(
                    logger,
                    "P04",
                    "FULL_DB_SCHEMA_MODEL_INPUT",
                    {"agent": "A6", "ordinal": full_db_schema_model_calls},
                )
                sif_assembly = self._assemble_sif(
                    bif_confirmed,
                    sif_draft,
                    sif_decision_ledger,
                    schema_catalog,
                    reviewed_source_ambiguity_records,
                    sif_clarifier,
                )
            sif_assembly["applied_decision_ids"] = [
                str(item["decision_id"]) for item in sif_decision_ledger
            ]
            logger.write_json("frames/sif_assembly_bundle.json", sif_assembly)
            sif_candidate = sif_assembly["confirmed_sif"]
            sif_candidate, sif_reference_repairs = normalize_sif_intent_references(
                sif_candidate, bif_confirmed
            )
            sif_candidate, schema_documented_repairs = repair_schema_documented_grounding(
                sif_candidate, bif_confirmed, schema_catalog
            )
            sif_candidate, output_projection_repairs = repair_unrequested_supporting_projections(
                sif_candidate, bif_confirmed
            )
            assembly_repairs = (
                sif_reference_repairs
                + schema_documented_repairs
                + output_projection_repairs
            )
            sif_invariant_report = self._validate_decision_path_coverage(
                sif_candidate,
                sif_decision_ledger,
                layer="SIF",
                allow_intent_references=True,
            )
            logger.write_json(
                "frames/sif_decision_invariants.json", sif_invariant_report
            )
            if assembly_repairs:
                logger.write_json(
                    "frames/sif_post_assembly_repairs.json", assembly_repairs
                )
            sif_confirmed, sif_transaction_events = reduce_sif_candidate(
                sif_draft,
                sif_candidate,
                bif=bif_confirmed,
                locked_tables=locked_population_sources,
                schema_catalog=schema_catalog,
            )
            sif_transaction_log.append(
                {
                    "stage": "one_shot_assembly",
                    "round": 1,
                    "events": sif_transaction_events,
                }
            )
            logger.write_json(
                "frames/sif_assembly_transaction.json", sif_transaction_events
            )
            assembly_rejected = any(
                item.get("operation") in {
                    "reject_sif_candidate", "reject_destructive_grounding_candidate"
                }
                for item in sif_transaction_events
            )
            if assembly_rejected:
                baseline_report = self._validate_decision_path_coverage(
                    sif_confirmed,
                    sif_decision_ledger,
                    layer="SIF_BASELINE",
                    allow_intent_references=True,
                )
                logger.write_json(
                    "frames/sif_assembly_fallback_invariants.json", baseline_report
                )
                self._step(
                    logger,
                    "P04",
                    "SIF_ASSEMBLY_CANDIDATE_REJECTED_BASELINE_RETAINED",
                    {
                        "reasons": [
                            item.get("reason")
                            for item in sif_transaction_events
                            if item.get("operation") in {
                                "reject_sif_candidate",
                                "reject_destructive_grounding_candidate",
                            }
                        ]
                    },
                )
            missing_locked = missing_required_sources(
                sif_confirmed, locked_population_sources
            )
            if missing_locked:
                raise PipelineRunError(
                    "SIF_ASSEMBLY_DECISION_MISSING: confirmed population source(s) absent: "
                    f"{sorted(missing_locked)}"
                )
            sif_contract = getattr(self.agents, "sif_schema", None)
            if sif_contract:
                try:
                    validate_contract(
                        sif_confirmed, sif_contract, "One-shot confirmed SIF"
                    )
                except ContractError as exc:
                    raise PipelineRunError(
                        f"SIF_ASSEMBLY_INVALID: {exc}"
                    ) from exc

            sif_confirmed, sif_rounds = self._rematerialize_sif_until_compliant(
                case=case,
                bif=bif_confirmed,
                sif_draft=sif_draft,
                current_sif=sif_confirmed,
                clarifier=sif_clarifier,
                schema_catalog=schema_catalog,
                logger=logger,
                locked_tables=locked_population_sources,
                reviewed_ambiguities=reviewed_source_ambiguity_records,
                rounds_completed=sif_rounds,
            )
            sif_decision_ledger = sif_clarifier.decision_ledger()

            system_grounding_rounds = 0
            deterministic_grounding_repairs = 0
            grounding_warnings = self._grounding_warnings(
                database, bif_confirmed, sif_confirmed, schema_catalog
            )
            grounding_seen: set[str] = set()
            while grounding_warnings:
                logger.set_step("P04")
                system_grounding_rounds += 1
                warning_root = f"frames/system_grounding/round_{system_grounding_rounds:02d}"
                logger.write_json(f"{warning_root}/warnings.json", grounding_warnings)
                current_hash = hashlib.sha256(
                    json.dumps(sif_confirmed, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                if current_hash in grounding_seen:
                    raise PipelineRunError(
                        "SYSTEM_GROUNDING_STALLED: grounding warnings remain with unchanged SIF"
                    )
                grounding_seen.add(current_hash)
                code_repaired, code_repairs = repair_schema_documented_grounding(
                    sif_confirmed, bif_confirmed, schema_catalog
                )
                code_repaired, output_repairs = repair_missing_query_block_outputs(
                    code_repaired, schema_catalog, grounding_warnings
                )
                code_repairs += output_repairs
                code_repaired, with_ties_repairs = repair_unrequested_with_ties(
                    code_repaired, bif_confirmed, grounding_warnings
                )
                code_repairs += with_ties_repairs
                code_repaired, trace_repairs = repair_missing_output_traceability(
                    code_repaired, bif_confirmed, grounding_warnings
                )
                code_repairs += trace_repairs
                code_repaired, population_trace_repairs = repair_missing_population_traceability(
                    code_repaired, grounding_warnings, locked_population_sources
                )
                code_repairs += population_trace_repairs
                code_repaired, join_repairs = repair_missing_column_joins(
                    code_repaired, schema_catalog, grounding_warnings
                )
                code_repairs += join_repairs
                code_repaired, boolean_repairs = repair_malformed_boolean_expressions(
                    code_repaired, grounding_warnings
                )
                code_repairs += boolean_repairs
                code_repaired, logic_repairs = repair_bif_logic_operators(
                    code_repaired, grounding_warnings
                )
                code_repairs += logic_repairs
                code_repaired, value_repairs = repair_non_exact_question_values(
                    code_repaired, grounding_warnings
                )
                code_repairs += value_repairs
                code_repaired, merge_repairs = repair_merged_independent_conditions(
                    code_repaired,
                    bif_confirmed,
                    schema_catalog,
                    grounding_warnings,
                )
                code_repairs += merge_repairs
                if code_repairs:
                    deterministic_grounding_repairs += len(code_repairs)
                    sif_confirmed = code_repaired
                    logger.write_json(f"{warning_root}/deterministic_repairs.json", code_repairs)
                    logger.write_json(
                        f"{warning_root}/sif_after_deterministic_repair.json",
                        sif_confirmed,
                    )
                    self._step(
                        logger,
                        "P04",
                        "DETERMINISTIC_GROUNDING_REPAIR_APPLIED",
                        {"round": system_grounding_rounds, "repairs": len(code_repairs)},
                    )
                    grounding_warnings = self._grounding_warnings(
                        database, bif_confirmed, sif_confirmed, schema_catalog
                    )
                    if not grounding_warnings:
                        continue
                full_db_schema_model_calls += 1
                self._step(
                    logger,
                    "P04",
                    "FULL_DB_SCHEMA_MODEL_INPUT",
                    {"agent": "A6", "ordinal": full_db_schema_model_calls},
                )
                pre_reground_sif = sif_confirmed
                sif_change_set = self.agents.a6_grounding_patch(
                    bif_confirmed,
                    sif_confirmed,
                    schema_catalog,
                    grounding_warnings,
                )
                logger.write_json(
                    f"{warning_root}/sif_grounding_change_set.json", sif_change_set
                )
                sif_candidate, sif_change_set_events = apply_sif_system_change_set(
                    sif_confirmed,
                    sif_change_set,
                    expected_request_id="system_grounding",
                    expected_gap_id="system_grounding",
                )
                logger.write_json(
                    f"{warning_root}/sif_grounding_change_set_transaction.json",
                    sif_change_set_events,
                )
                sif_system_transaction_count += 1
                sif_system_operation_count += len(
                    sif_change_set.get("operations", [])
                )
                if any(
                    item.get("operation") == "reject_change_set"
                    for item in sif_change_set_events
                ):
                    raise PipelineRunError(
                        "SIF_GROUNDING_CHANGE_SET_REJECTED: A6 attempted an invalid patch"
                    )
                sif_candidate, post_model_codebook_repairs = (
                    repair_schema_documented_grounding(
                        sif_candidate, bif_confirmed, schema_catalog
                    )
                )
                if post_model_codebook_repairs:
                    logger.write_json(
                        f"{warning_root}/post_model_codebook_repairs.json",
                        post_model_codebook_repairs,
                    )
                    self._step(
                        logger,
                        "P04",
                        "A6_CODEBOOK_DECISIONS_REAPPLIED",
                        {
                            "round": system_grounding_rounds,
                            "repairs": len(post_model_codebook_repairs),
                        },
                    )
                sif_candidate, contract_shape_repairs = normalize_sif_contract_shape(
                    sif_candidate
                )
                if contract_shape_repairs:
                    logger.write_json(
                        f"{warning_root}/sif_contract_shape_repairs.json",
                        contract_shape_repairs,
                    )
                sif_contract = getattr(self.agents, "sif_schema", None)
                if sif_contract:
                    try:
                        validate_contract(
                            sif_candidate, sif_contract, "SIF after grounding change set"
                        )
                    except ContractError as exc:
                        raise PipelineRunError(
                            f"SIF_GROUNDING_CHANGE_SET_REJECTED: {exc}"
                        ) from exc
                candidate_grounding_warnings = self._grounding_warnings(
                    database, bif_confirmed, sif_candidate, schema_catalog
                )
                sif_confirmed, sif_transaction_events = reduce_sif_candidate(
                    pre_reground_sif,
                    sif_candidate,
                    bif=bif_confirmed,
                    locked_tables=locked_population_sources,
                    schema_catalog=schema_catalog,
                    previous_grounding_warnings=grounding_warnings,
                    candidate_grounding_warnings=candidate_grounding_warnings,
                )
                if sif_transaction_events:
                    sif_transaction_log.append(
                        {
                            "stage": "system_grounding",
                            "round": system_grounding_rounds,
                            "events": sif_transaction_events,
                        }
                    )
                    logger.write_json(
                        f"{warning_root}/sif_transaction.json",
                        sif_transaction_events,
                    )
                    self._step(
                        logger,
                        "P04",
                        "SIF_TRANSACTION_APPLIED",
                        {
                            "round": system_grounding_rounds,
                            "events": len(sif_transaction_events),
                        },
                    )
                missing_locked = missing_required_sources(
                    sif_confirmed, locked_population_sources
                )
                if missing_locked:
                    raise PipelineRunError(
                        "SIF_LOCKED_SOURCE_VIOLATION: system grounding removed confirmed "
                        f"population source(s): {sorted(missing_locked)}"
                    )
                logger.write_json(f"{warning_root}/sif_after_regrounding.json", sif_confirmed)
                self._step(
                    logger,
                    "P04",
                    "A6_SYSTEM_REGROUNDING_APPLIED",
                    {"round": system_grounding_rounds, "warnings_before": len(grounding_warnings)},
                )
                grounding_warnings = self._grounding_warnings(
                    database, bif_confirmed, sif_confirmed, schema_catalog
                )
            sif_confirmed["status"] = "ready"
            sif_confirmed, final_output_repairs = repair_unrequested_supporting_projections(
                sif_confirmed, bif_confirmed
            )
            if final_output_repairs:
                deterministic_grounding_repairs += len(final_output_repairs)
                logger.write_json(
                    "frames/final_output_projection_repairs.json",
                    final_output_repairs,
                )
                self._step(
                    logger,
                    "P04",
                    "UNREQUESTED_SUPPORTING_PROJECTIONS_REMOVED",
                    {"repairs": len(final_output_repairs)},
                )
            sif_confirmed, sif_rounds, sif_decision_ledger = (
                self._finalize_sif_with_revision_loop(
                    case=case,
                    bif=bif_confirmed,
                    sif_draft=sif_draft,
                    sif=sif_confirmed,
                    clarifier=sif_clarifier,
                    schema_catalog=schema_catalog,
                    logger=logger,
                    locked_tables=locked_population_sources,
                    reviewed_ambiguities=reviewed_source_ambiguity_records,
                    rounds_completed=sif_rounds,
                )
            )
            logger.write_json(
                "frames/sif_clarification_state.json", sif_clarifier.snapshot()
            )
            logger.write_json(
                "frames/sif_canonical_intent_state.json",
                sif_clarifier.snapshot()["canonical_intent_state"],
            )
            logger.write_json("frames/sif_confirmed.json", sif_confirmed)
            logger.write_json(
                "frames/sif_state.json",
                {
                    "state": "ready",
                    "frame_hash": hashlib.sha256(
                        json.dumps(
                            sif_confirmed,
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                    "locked_population_sources": sorted(locked_population_sources),
                    "transactions": sif_transaction_log,
                },
            )
            logger.write_json("frames/traceability.json", sif_confirmed.get("traceability", []))
            logger.write_json("frames/grounding_validation.json", {"warnings": grounding_warnings})
            self._step(
                logger,
                "P04",
                "SIF_CONFIRMED",
                {"rounds": sif_rounds, "system_grounding_rounds": system_grounding_rounds},
            )

            current_bif_hash = hashlib.sha256(
                json.dumps(
                    bif_confirmed,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if current_bif_hash != locked_bif_hash:
                raise PipelineRunError(
                    "SIF_BIF_IMMUTABILITY_VIOLATION: SIF processing mutated Confirmed BIF"
                )

            logger.set_step("P05")
            try:
                sql_draft = compile_sif(sif_confirmed)
                sql_generation_strategy = "deterministic_sif_compiler"
                self._step(logger, "P05", "A7_SQL_COMPILED_BY_CODE")
            except UnsupportedSIFError as compiler_error:
                logger.write_json(
                    "sql/deterministic_compiler_fallback.json",
                    {
                        "error_type": type(compiler_error).__name__,
                        "error": str(compiler_error),
                    },
                )
                sql_draft = self.agents.a7_generate_sql(sif_confirmed, schema_catalog)
                sql_generation_strategy = "a7_model_fallback"
                self._step(logger, "P05", "A7_SQL_MODEL_FALLBACK")
            sif_compiler_ready = sql_generation_strategy == "deterministic_sif_compiler"
            logger.write_text("sql/sql_draft.sql", sql_draft)
            self._step(logger, "P05", "A7_SQL_DRAFTED")

            logger.set_step("P06")
            if sql_generation_strategy == "deterministic_sif_compiler":
                verdict = {
                    "status": "accept",
                    "sql": None,
                    "reason": "Confirmed SIF compiled deterministically after schema and coverage validation.",
                    "affected_intent_paths": [],
                }
                sql_validation_strategy = "static_accept_before_preflight"
            else:
                verdict = self.agents.a8_verify_or_repair(
                    sif=sif_confirmed,
                    schema_catalog=schema_catalog,
                    sql=sql_draft,
                )
                sql_validation_strategy = "a8_model_for_model_generated_sql"
            logger.write_json("sql/sql_validation.json", verdict)
            self._step(logger, "P06", "A8_SQL_VALIDATED")
            if verdict["status"] == "return_to_sif":
                self._step(logger, "P06", "A8_SIF_REENTRY_DEFERRED", {
                    "action": "continue_with_current_sql",
                })
            candidate = verdict["sql"] if verdict["status"] == "repair" else sql_draft
            if not candidate:
                candidate = sql_draft
                self._step(logger, "P06", "A8_EMPTY_CANDIDATE_CONTINUED", {
                    "action": "use_sql_draft",
                })
            self._assert_read_only(candidate)

            logger.set_step("P07")
            preflight_records: list[dict[str, Any]] = []
            preflight = self.executor.execute(database, candidate)
            preflight_records.append(preflight.to_dict(include_rows=False))
            self._step(logger, "P07", "A8_SQL_PREFLIGHT_EXECUTED", {"status": preflight.status})
            if preflight.status != "success":
                sql_validation_strategy += "+a8_runtime_repair"
                repair = self.agents.a8_verify_or_repair(
                    sif=sif_confirmed,
                    schema_catalog=schema_catalog,
                    sql=candidate,
                    execution_error=preflight.to_dict(include_rows=False),
                )
                logger.write_json("sql/sql_runtime_repair.json", repair)
                if repair["status"] != "repair" or not repair["sql"]:
                    self._step(logger, "P07", "A8_PREFLIGHT_REPAIR_DEFERRED", {
                        "error_code": preflight.error_code,
                        "action": "continue_with_current_sql",
                    })
                else:
                    candidate = repair["sql"]
                    self._assert_read_only(candidate)
                    preflight = self.executor.execute(database, candidate)
                    preflight_records.append(preflight.to_dict(include_rows=False))
                    self._step(
                        logger,
                        "P07",
                        "A8_REPAIRED_SQL_PREFLIGHT_EXECUTED",
                        {"status": preflight.status},
                    )
            logger.write_json("sql/sql_preflight_execution.json", preflight_records)
            if preflight.status != "success":
                self._step(logger, "P07", "SQL_PREFLIGHT_FAILURE_CONTINUED", {
                    "error_code": preflight.error_code,
                })

            logger.set_step("P08")
            final_sql = candidate
            logger.write_text("sql/sql_final.sql", final_sql)
            self._step(logger, "P08", "FINAL_SQL_FROZEN")

            comparison = EvaluationRunner(self.executor).run(
                database=database,
                gold_sql=case["SQL"],
                generated_sql=final_sql,
                logger=logger,
                comparison_policy=comparison_policy,
                commit=False,
                embedded=True,
            )

            logger.set_step("P13")
            attribution = self._attribute(comparison)
            logger.write_json("evaluation/stage_attribution.json", attribution)
            self._step(logger, "P13", "PROCESS_TRACE_ANALYZED", attribution)
            logger.set_step("P14")
            metrics = {
                "execution_accuracy": comparison.execution_accuracy,
                "equivalent": comparison.equivalent,
                "bif_clarification_rounds": bif_rounds,
                "sif_clarification_rounds": sif_rounds,
                "total_clarification_rounds": bif_rounds + sif_rounds,
                "preflight_attempts": len(preflight_records),
                "bif_round_limit": self.max_bif_rounds,
                "sif_round_limit": self.max_sif_rounds,
                "clarification_termination": "ready",
                "system_grounding_rounds": system_grounding_rounds,
                "deterministic_grounding_repairs": deterministic_grounding_repairs,
                "locked_population_sources": sorted(locked_population_sources),
                "locked_bif_decision_count": len(
                    bif_envelope.get("locked_decisions", [])
                ),
                "bif_decision_ledger_count": len(bif_decision_ledger),
                "bif_one_shot_assembly_count": max(
                    1, self._model_calls_by_agent().get("A3", 0)
                ),
                "bif_clarification_quality": bif_clarifier.snapshot()["quality"],
                "sif_transaction_count": len(sif_transaction_log),
                "sif_candidate_rejection_count": sum(
                    event.get("operation") == "reject_sif_candidate"
                    for transaction in sif_transaction_log
                    for event in transaction.get("events", [])
                ),
                "sif_system_grounding_transaction_count": sif_system_transaction_count,
                "sif_system_grounding_operation_count": sif_system_operation_count,
                "sif_decision_ledger_count": len(sif_decision_ledger),
                "sif_one_shot_assembly_count": max(
                    1, self._model_calls_by_agent().get("A6", 0)
                ),
                "sif_clarification_quality": sif_clarifier.snapshot()["quality"],
                "clarification_valid_termination": (
                    bif_clarifier.can_terminate() and sif_clarifier.can_terminate()
                ),
                "model_whole_frame_rewrite_enabled": False,
                "iterative_frame_update_enabled": False,
                "one_shot_frame_assembly_enabled": True,
                "canonical_frame_compaction_enabled": False,
                "literal_domain_warnings_final": len(grounding_warnings),
                "agent_roles_executed": [f"A{i}" for i in range(1, 9)] + ["E2"],
                "model_call_count_total": self._model_call_count_total(),
                "model_calls_by_agent": self._model_calls_by_agent(),
                "model_backed_roles_called": sorted(self._model_calls_by_agent()),
                "bif_initial_analysis_strategy": bif_initial_analysis_strategy,
                "bif_policy": "combined_fixed",
                "bif_schema_version": bif_confirmed.get("schema_version"),
                "bif_semantic_findings_initial": initial_bif_semantic_finding_count,
                "bif_semantic_findings_final": len(bif_semantic_findings),
                "bif_contract_coverage": bif_contract_metrics,
                "sql_generation_strategy": sql_generation_strategy,
                "sif_compiler_ready": sif_compiler_ready,
                "sql_validation_strategy": sql_validation_strategy,
                "full_db_schema_model_calls": full_db_schema_model_calls,
            }
            logger.write_json("evaluation/metrics.json", metrics)
            self._step(logger, "P14", "METRICS_WRITTEN")
            logger.append_event("RUN_COMMITTED", {"status": "success" if comparison.equivalent else "failed"})
            manifest = logger.build_hash_manifest()
            manifest_valid = logger.verify_hash_manifest()
            logger.write_json(
                "run_complete.json",
                {
                    "status": "success" if comparison.equivalent else "failed",
                    "final_state": "RUN_COMMITTED",
                    "artifact_count": len(manifest),
                    "manifest_valid": manifest_valid,
                    "agent_roles_executed": metrics["agent_roles_executed"],
                    "model_call_count_total": metrics["model_call_count_total"],
                },
            )
            return FullCaseResult(comparison, final_sql, preflight, logger.case_dir)
        except Exception as exc:
            diagnostic = getattr(exc, "diagnostic", None)
            if isinstance(diagnostic, dict):
                logger.write_json(
                    "errors/bif_layout_no_progress.json", diagnostic
                )
            logger.append_event(
                "RUN_FAILED", {
                    "error_type": type(exc).__name__, "error": str(exc),
                    "diagnostic": diagnostic if isinstance(diagnostic, dict) else None,
                }
            )
            manifest = logger.build_hash_manifest()
            logger.write_json(
                "run_complete.json",
                {
                    "status": "pipeline_failed",
                    "final_state": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "diagnostic": diagnostic if isinstance(diagnostic, dict) else None,
                    "artifact_count": len(manifest),
                    "manifest_valid": logger.verify_hash_manifest(),
                    "model_call_count_total": self._model_call_count_total(),
                    "model_calls_by_agent": self._model_calls_by_agent(),
                },
            )
            raise

    @staticmethod
    def _step(
        logger: ArtifactLogger,
        step: str,
        event: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        logger.set_step(step)
        payload = {"step": step}
        if extra:
            payload.update(extra)
        logger.append_event(event, payload)

    def _run_sif_v2_after_bif(
        self, *, case: dict[str, Any], database: Path, logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"],
        bif_confirmed: dict[str, Any], bif_rounds: int,
        schema_catalog: dict[str, Any], locked_bif_hash: str,
        bif_contract_metrics: dict[str, int],
    ) -> FullCaseResult:
        """Execute the BIF-aligned SIF 2.0 path without a model-owned query AST."""
        sif_schema = getattr(self.agents, "sif_v2_schema", None)
        if not sif_schema:
            raise PipelineRunError("SIF_V2_SCHEMA_NOT_CONFIGURED")
        logger.set_step("P03")
        self._step(logger, "P03", "SIF_V2_GROUNDING_STARTED")
        interaction_index = 0
        trace_index = 0

        def e2_responder(
            request: dict[str, Any], decision: dict[str, Any] | None
        ) -> dict[str, Any]:
            nonlocal interaction_index
            interaction_index += 1
            interaction = (
                self._interaction_envelope(decision, request)
                if decision is not None
                else {"stage": "sif", "decision": None,
                      "clarification_request": request}
            )
            root = f"frames/sif_v2_rounds/round_{interaction_index:02d}"
            logger.write_json(f"{root}/interaction.json", interaction)
            logger.write_json(f"{root}/clarification_request.json", request)
            response = self.agents.e2_user(
                mode="SIF_CLARIFICATION", case=case, clarification=interaction,
                confirmed_bif_summary={
                    "decision_objective": bif_confirmed.get("decision_objective"),
                    "target_population": bif_confirmed.get("target_population"),
                    "metrics": bif_confirmed.get("metrics"),
                    "scope": bif_confirmed.get("scope"),
                    "time_basis": bif_confirmed.get("time_basis"),
                    "comparisons": bif_confirmed.get("comparisons"),
                    "output_expectation": bif_confirmed.get("output_expectation"),
                },
                oracle_context=build_e2_oracle_context(case["SQL"], interaction),
            )
            logger.write_json(f"{root}/user_response.json", response)
            return response

        def trace_sink(trace: dict[str, Any]) -> None:
            nonlocal trace_index
            trace_index += 1
            logger.write_json(
                f"frames/sif_v2_events/event_{trace_index:03d}.json", trace
            )

        try:
            result = SIFV2ClarificationSession(
                self.agents, sif_schema=sif_schema,
                max_rounds=self.max_sif_rounds,
            ).run(
                case=case, bif=bif_confirmed, schema_catalog=schema_catalog,
                e2_responder=e2_responder, database=database,
                trace_sink=trace_sink,
            )
        except SIFV2ClarificationError as exc:
            raise PipelineRunError(str(exc)) from exc
        sif_confirmed = result.sif
        logger.write_json("frames/sif_v2_confirmed.json", sif_confirmed)
        logger.write_json("frames/sif_v2_decision_ledger.json", result.decision_ledger)
        self._step(logger, "P04", "SIF_V2_CONFIRMED", {"rounds": result.rounds})

        current_bif_hash = hashlib.sha256(
            json.dumps(bif_confirmed, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if current_bif_hash != locked_bif_hash:
            raise PipelineRunError("SIF_BIF_IMMUTABILITY_VIOLATION")

        logger.set_step("P05")
        query_plan = lower_sif_v2_to_query_plan(bif_confirmed, sif_confirmed)
        logger.write_json("frames/sif_v2_lowered_query_plan.json", query_plan)
        final_sql = compile_sif(query_plan)
        logger.write_text("sql/sql_draft.sql", final_sql)
        self._step(logger, "P05", "SIF_V2_SQL_COMPILED_BY_CODE")
        self._assert_read_only(final_sql)

        logger.set_step("P07")
        preflight = self.executor.execute(database, final_sql)
        logger.write_json(
            "sql/sql_preflight_execution.json",
            [preflight.to_dict(include_rows=False)],
        )
        self._step(logger, "P07", "SIF_V2_SQL_PREFLIGHT_EXECUTED",
                   {"status": preflight.status})
        if preflight.status != "success":
            self._step(logger, "P07", "SIF_V2_PREFLIGHT_FAILURE_CONTINUED", {
                "error_code": preflight.error_code,
            })
        logger.set_step("P08")
        logger.write_text("sql/sql_final.sql", final_sql)
        self._step(logger, "P08", "FINAL_SQL_FROZEN")
        comparison = EvaluationRunner(self.executor).run(
            database=database, gold_sql=case["SQL"], generated_sql=final_sql,
            logger=logger, comparison_policy=comparison_policy,
            commit=False, embedded=True,
        )
        logger.set_step("P13")
        attribution = self._attribute(comparison)
        logger.write_json("evaluation/stage_attribution.json", attribution)
        self._step(logger, "P13", "PROCESS_TRACE_ANALYZED", attribution)
        metrics = {
            "execution_accuracy": comparison.execution_accuracy,
            "equivalent": comparison.equivalent,
            "bif_clarification_rounds": bif_rounds,
            "sif_clarification_rounds": result.rounds,
            "total_clarification_rounds": bif_rounds + result.rounds,
            "clarification_termination": "ready",
            "clarification_valid_termination": True,
            "sif_layout": "2.0-bif-aligned-grounding",
            "sif_slot_patch_only": True,
            "sif_admission_gate_enabled": True,
            "sql_generation_strategy": "deterministic_sif_v2_lowerer",
            "sif_compiler_ready": True,
            "preflight_attempts": 1,
            "model_call_count_total": self._model_call_count_total(),
            "model_calls_by_agent": self._model_calls_by_agent(),
            "model_backed_roles_called": sorted(self._model_calls_by_agent()),
            "bif_contract_coverage": bif_contract_metrics,
        }
        logger.set_step("P14")
        logger.write_json("evaluation/metrics.json", metrics)
        self._step(logger, "P14", "METRICS_WRITTEN")
        logger.append_event(
            "RUN_COMMITTED",
            {"status": "success" if comparison.equivalent else "failed"},
        )
        manifest = logger.build_hash_manifest()
        logger.write_json("run_complete.json", {
            "status": "success" if comparison.equivalent else "failed",
            "final_state": "RUN_COMMITTED", "artifact_count": len(manifest),
            "manifest_valid": logger.verify_hash_manifest(),
            "model_call_count_total": metrics["model_call_count_total"],
            "sif_layout": "v2",
        })
        return FullCaseResult(comparison, final_sql, preflight, logger.case_dir)

    def _run_direct_sif(
        self, *, case: dict[str, Any], database: Path, logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"],
        understanding: dict[str, Any], schema_catalog: dict[str, Any],
        semantic_frontend_result: dict[str, Any],
    ) -> FullCaseResult:
        """Ground the provided SIF stages directly after business clarification."""
        confirmed_cqd = semantic_frontend_result["confirmed_cqd"]
        coverage = semantic_frontend_result.get(
            "direct_sif_understanding_coverage", {}
        ) or {}
        understanding_continued = coverage.get("status") != "complete"
        logger.write_json("frames/semantic_frontend_result.json", semantic_frontend_result)
        logger.write_json("frames/direct_sif_query_understanding.json", understanding)
        logger.write_json("frames/direct_sif_understanding_coverage.json", coverage)
        self._step(logger, "P02", "DIRECT_SIF_UNDERSTANDING_CONFIRMED", {
            "obligations": coverage.get("obligation_count", 0),
            "missing": coverage.get("missing_count", 0),
            "continued_with_warning": understanding_continued,
        })

        logger.set_step("P03")
        technical_feedback: list[dict[str, Any]] = []
        previous_signature: str | None = None
        repeated = 0
        grounding_attempt = 0
        technical_clarification_rounds = 0
        grounding_continued_with_warnings = False
        technical_resolution_cache: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        accepted_technical_feedback: list[dict[str, Any]] = []
        best_issue_count: int | None = None
        stalled_rounds = 0
        best_executable: tuple[int, dict[str, Any], str, ExecutionRecord] | None = None
        while True:
            grounding_attempt += 1
            bundle = self.agents.a4_direct_sif_grounding_bundle(
                case=case,
                confirmed_cqd=confirmed_cqd,
                understanding=understanding,
                schema_catalog=schema_catalog,
                technical_feedback=technical_feedback,
            )
            bundle = normalize_direct_sif_bundle(
                bundle=bundle, schema_catalog=schema_catalog
            )
            bundle, candidate_report = resolve_column_candidates(
                bundle=bundle,
                schema_catalog=schema_catalog,
                original_question=str(case["question"]),
                benchmark_evidence=case.get("evidence"),
                understanding=understanding,
                confirmed_cqd=confirmed_cqd,
                technical_feedback=technical_feedback,
            )
            bundle, output_report = expand_plural_output_bindings(
                bundle=bundle,
                understanding=understanding,
                confirmed_cqd=confirmed_cqd,
                schema_catalog=schema_catalog,
                technical_feedback=technical_feedback,
            )
            bundle = apply_cqd_sql_semantics(
                bundle=bundle, confirmed_cqd=confirmed_cqd,
            )
            candidate_report["decisions"].extend(output_report["decisions"])
            candidate_report["issues"].extend(output_report["issues"])
            report = validate_direct_sif_bundle(
                understanding=understanding,
                bundle=bundle,
                schema_catalog=schema_catalog,
                confirmed_cqd=confirmed_cqd,
            )
            report["candidate_resolution"] = candidate_report
            report["issues"].extend(candidate_report["issues"])
            if candidate_report["issues"]:
                report["status"] = "needs_grounding"
            issue_count = len(report["issues"])
            if best_issue_count is None or issue_count < best_issue_count:
                best_issue_count = issue_count
                stalled_rounds = 0
            else:
                stalled_rounds += 1
            try:
                executable_sql = compile_direct_sif(
                    understanding=understanding, bundle=bundle
                )
                self._assert_read_only(executable_sql)
            except (DirectSIFCompilationError, PipelineRunError):
                pass
            else:
                executable_preflight = self.executor.execute(database, executable_sql)
                executable_rank = issue_count + (
                    0 if executable_preflight.status == "success" else 1000
                )
                if best_executable is None or executable_rank < best_executable[0]:
                    best_executable = (
                        executable_rank, copy.deepcopy(bundle), executable_sql,
                        executable_preflight,
                    )
            logger.write_json(
                f"frames/direct_sif_grounding_round_{grounding_attempt:02d}.json",
                {"bundle": bundle, "coverage": report},
            )
            if report["status"] == "complete":
                try:
                    candidate_sql = compile_direct_sif(
                        understanding=understanding, bundle=bundle
                    )
                    self._assert_read_only(candidate_sql)
                except (DirectSIFCompilationError, PipelineRunError) as exc:
                    report["issues"].append({
                        "type": "DETERMINISTIC_COMPILATION",
                        "reason": str(exc),
                    })
                else:
                    preflight = self.executor.execute(database, candidate_sql)
                    if preflight.status == "success":
                        final_sql = candidate_sql
                        break
                    report["issues"].append({
                        "type": "SQL_PREFLIGHT",
                        "reason": preflight.error_message or preflight.error_code,
                    })

            deterministic_clarification_issues = [
                item for item in candidate_report["issues"]
                if item.get("type") == "CLARIFICATION_REQUIRED"
                and len(item.get("candidates", []) or []) >= 2
            ]
            deterministic_surfaces = {
                str(item.get("surface") or "").casefold()
                for item in deterministic_clarification_issues
            }
            clarification_issues = deterministic_clarification_issues + [
                item for item in report["issues"]
                if item.get("type") == "CLARIFICATION_REQUIRED"
                and len(item.get("candidates", []) or []) >= 2
                and str(item.get("surface") or "").casefold()
                not in deterministic_surfaces
            ]
            round_feedback = copy.deepcopy(report["issues"])
            round_feedback.extend(copy.deepcopy(accepted_technical_feedback))
            seen_clarification_keys: set[tuple[str, tuple[str, ...]]] = set()
            for index, issue in enumerate(clarification_issues):
                clarification_key = (
                    str(issue.get("surface") or "").casefold(),
                    tuple(sorted(str(item) for item in issue.get("candidates", []) or [])),
                )
                if clarification_key in seen_clarification_keys:
                    continue
                seen_clarification_keys.add(clarification_key)
                if clarification_key in technical_resolution_cache:
                    round_feedback.append({
                        "type": "TECHNICAL_CLARIFICATION_RESPONSE",
                        "surface": issue.get("surface"),
                        "candidates": issue.get("candidates", []),
                        "response": copy.deepcopy(
                            technical_resolution_cache[clarification_key]
                        ),
                        "reused": True,
                    })
                    continue
                technical_clarification_rounds += 1
                request_id = (
                    f"direct-sif-{case['question_id']}-{grounding_attempt:02d}-{index + 1:02d}"
                )
                options = [
                    {"option_id": f"option_{number + 1}", "label": str(label)}
                    for number, label in enumerate(issue["candidates"])
                ]
                interaction = {
                    "stage": "sif",
                    "decision": {
                        "decision_id": request_id,
                        "issue_type": "TECHNICAL_GROUNDING",
                        "description": str(issue.get("reason") or issue.get("surface")),
                        "affected_paths": [str(issue.get("surface") or "")],
                    },
                    "clarification_request": {
                        "request_id": request_id,
                        "stage": "sif",
                        "gap_id": str(issue.get("surface") or request_id),
                        "question": (
                            f"'{issue.get('surface')}'에는 어떤 의미를 적용해야 하나요?"
                        ),
                        "why_it_matters": str(issue.get("reason") or "물리 연결 결과가 달라집니다."),
                        "options": options,
                        "allow_free_text": True,
                        "affected_paths": [str(issue.get("surface") or "")],
                    },
                }
                response = self.agents.e2_user(
                    mode="SIF_CLARIFICATION",
                    case=case,
                    clarification=interaction,
                    confirmed_bif_summary={
                        "query_understanding": understanding,
                        "confirmed_cqd": confirmed_cqd,
                    },
                )
                logger.write_json(
                    f"frames/direct_sif_technical_clarification_{technical_clarification_rounds:02d}.json",
                    {"interaction": interaction, "response": response},
                )
                technical_resolution_cache[clarification_key] = copy.deepcopy(response)
                accepted = {
                    "type": "TECHNICAL_CLARIFICATION_RESPONSE",
                    "surface": issue.get("surface"),
                    "candidates": issue.get("candidates", []),
                    "response": response,
                }
                round_feedback.append(copy.deepcopy(accepted))
                accepted_technical_feedback.append(copy.deepcopy(accepted))

            signature = hashlib.sha256(json.dumps(
                sorted(
                    (
                        str(item.get("type") or ""),
                        str(item.get("surface") or "").rsplit(".", 1)[-1].casefold(),
                    )
                    for item in report["issues"]
                ),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                default=str,
            ).encode("utf-8")).hexdigest()
            repeated = repeated + 1 if signature == previous_signature else 1
            previous_signature = signature
            technical_feedback = round_feedback
            if stalled_rounds >= 5 or grounding_attempt >= 5:
                if best_executable is None:
                    grounding_continued_with_warnings = True
                    final_sql = 'SELECT NULL AS "unresolved_result" WHERE 0;'
                    preflight = self.executor.execute(database, final_sql)
                    logger.write_json(
                        "frames/direct_sif_grounding_no_progress_continued.json",
                        {
                            "repetitions": stalled_rounds,
                            "attempts": grounding_attempt,
                            "issues": report["issues"],
                            "action": "continue_with_explicit_unresolved_result",
                            "reason": (
                                "No catalog-grounded executable projection was supported; "
                                "do not invent a column and continue to final evaluation."
                            ),
                        },
                    )
                    break
                grounding_continued_with_warnings = True
                logger.write_json(
                    "frames/direct_sif_grounding_no_progress_continued.json",
                    {
                        "repetitions": stalled_rounds,
                        "attempts": grounding_attempt,
                        "issues": report["issues"],
                        "action": "continue_with_best_executable_bundle",
                    },
                )
                _, bundle, final_sql, preflight = best_executable
                break

        logger.write_json(
            "frames/direct_sif_confirmed_grounding.json",
            bundle["confirmed_grounding"],
        )
        logger.write_json("frames/direct_sif_table_linking.json", bundle["table_linking"])
        logger.write_json("frames/direct_sif_sql_plan.json", bundle["sql_plan"])
        self._step(logger, "P04", "DIRECT_SIF_GROUNDED", {
            "grounding_attempts": grounding_attempt,
            "understanding_continued_with_warning": understanding_continued,
            "grounding_continued_with_warnings": grounding_continued_with_warnings,
            "technical_clarification_rounds": technical_clarification_rounds,
        })

        logger.set_step("P05")
        logger.write_text("sql/sql_draft.sql", final_sql)
        self._step(logger, "P05", "DIRECT_SIF_SQL_COMPILED_BY_CODE")
        logger.set_step("P07")
        logger.write_json(
            "sql/sql_preflight_execution.json", [preflight.to_dict(include_rows=False)]
        )
        logger.write_text("sql/sql_final.sql", final_sql)
        self._step(logger, "P07", "DIRECT_SIF_SQL_PREFLIGHT_EXECUTED")
        comparison = EvaluationRunner(self.executor).run(
            database=database, gold_sql=case["SQL"], generated_sql=final_sql,
            logger=logger, comparison_policy=comparison_policy,
            commit=False, embedded=True,
        )
        logger.set_step("P13")
        attribution = self._attribute(comparison)
        logger.write_json("evaluation/stage_attribution.json", attribution)
        metrics = {
            "execution_accuracy": comparison.execution_accuracy,
            "equivalent": comparison.equivalent,
            "sif_layout": "direct-provided-format-v1",
            "business_clarification_rounds": semantic_frontend_result.get("rounds", 0),
            "technical_clarification_rounds": technical_clarification_rounds,
            "grounding_attempts": grounding_attempt,
            "understanding_continued_with_warning": understanding_continued,
            "grounding_continued_with_warnings": grounding_continued_with_warnings,
            "sql_generation_strategy": "deterministic_direct_sif_compiler",
            "model_call_count_total": self._model_call_count_total(),
            "model_calls_by_agent": self._model_calls_by_agent(),
        }
        logger.set_step("P14")
        logger.write_json("evaluation/metrics.json", metrics)
        self._step(logger, "P14", "METRICS_WRITTEN")
        logger.append_event(
            "RUN_COMMITTED",
            {"status": "success" if comparison.equivalent else "failed"},
        )
        manifest = logger.build_hash_manifest()
        logger.write_json("run_complete.json", {
            "status": "success" if comparison.equivalent else "failed",
            "final_state": "RUN_COMMITTED",
            "artifact_count": len(manifest),
            "manifest_valid": logger.verify_hash_manifest(),
            "model_call_count_total": metrics["model_call_count_total"],
            "sif_layout": "direct-provided-format-v1",
        })
        return FullCaseResult(comparison, final_sql, preflight, logger.case_dir)

    def _run_cqd_direct_sql(
        self, *, case: dict[str, Any], database: Path, logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"],
        schema_catalog: dict[str, Any], semantic_frontend_result: dict[str, Any],
    ) -> FullCaseResult:
        """Link the confirmed natural-language CQD directly into executable SQL."""
        confirmed_cqd = semantic_frontend_result["confirmed_cqd"]
        logger.write_json("frames/semantic_frontend_result.json", semantic_frontend_result)
        self._step(logger, "P02", "NATURAL_CQD_CONFIRMED")

        logger.set_step("P03")
        sif_service = SIFClarificationService(self.agents.client)
        confirmed_sif = sif_service.run(
            case=case,
            bif=confirmed_cqd,
            schema_catalog=schema_catalog,
            db_id=str(case.get("db_id", "")),
        )
        logger.write_json("frames/confirmed_sif.json", confirmed_sif)
        self._step(logger, "P03", "SIF_GROUNDING_CONFIRMED")

        logger.set_step("P04")
        synthesizer = UnifiedSQLSynthesizer(self.agents.client)
        synthesized_sql = synthesizer.synthesize(
            question=str(case.get("question", "")),
            evidence=case.get("evidence"),
            confirmed_cqd=confirmed_cqd,
            confirmed_sif=confirmed_sif,
            schema_catalog=schema_catalog,
            db_id=str(case.get("db_id", "")),
        )
        self._assert_read_only(synthesized_sql)
        preflight = self.executor.execute(database, synthesized_sql)
        final_sql = synthesized_sql

        if preflight.status != "success":
            corrector = SQLSelfCorrection(self.agents.client)
            corrected_sql = corrector.correct(
                question=str(case.get("question", "")),
                evidence=case.get("evidence"),
                failed_sql=synthesized_sql,
                error_message=str(preflight.error_message or preflight.error_code or ""),
                schema_catalog=schema_catalog,
                db_id=str(case.get("db_id", "")),
            )
            self._assert_read_only(corrected_sql)
            corrected_preflight = self.executor.execute(database, corrected_sql)
            if corrected_preflight.status == "success":
                final_sql = corrected_sql
                preflight = corrected_preflight

        logger.write_json("frames/cqd_sql_final_linking.json", {
            "synthesized_sql": synthesized_sql,
            "final_sql": final_sql,
            "preflight_status": preflight.status,
        })
        self._step(logger, "P04", "CQD_LINKED_DIRECTLY_TO_SQL", {
            "technical_clarification_rounds": 0,
            "continued_with_warnings": False,
        })
        logger.set_step("P05")
        logger.write_text("sql/sql_draft.sql", final_sql)
        self._step(logger, "P05", "SQL_COMPILED_DIRECTLY_FROM_CQD")
        logger.set_step("P07")
        logger.write_json("sql/sql_preflight_execution.json", [preflight.to_dict(include_rows=False)])
        logger.write_text("sql/sql_final.sql", final_sql)
        self._step(logger, "P07", "DIRECT_CQD_SQL_PREFLIGHT_EXECUTED")
        comparison = EvaluationRunner(self.executor).run(
            database=database, gold_sql=case["SQL"], generated_sql=final_sql,
            logger=logger, comparison_policy=comparison_policy,
            commit=False, embedded=True,
        )
        logger.set_step("P13")
        logger.write_json("evaluation/stage_attribution.json", self._attribute(comparison))
        metrics = {
            "execution_accuracy": comparison.execution_accuracy,
            "equivalent": comparison.equivalent,
            "business_clarification_rounds": semantic_frontend_result.get("rounds", 0),
            "technical_clarification_rounds": 0,
            "sql_generation_strategy": "natural_cqd_direct_sql",
            "intermediate_layouts_created": 0,
            "continued_with_warnings": False,
            "model_call_count_total": self._model_call_count_total(),
            "model_calls_by_agent": self._model_calls_by_agent(),
        }
        logger.set_step("P14")
        logger.write_json("evaluation/metrics.json", metrics)
        self._step(logger, "P14", "METRICS_WRITTEN")
        logger.append_event("RUN_COMMITTED", {
            "status": "success" if comparison.equivalent else "failed",
        })
        manifest = logger.build_hash_manifest()
        logger.write_json("run_complete.json", {
            "status": "success" if comparison.equivalent else "failed",
            "final_state": "RUN_COMMITTED", "artifact_count": len(manifest),
            "manifest_valid": logger.verify_hash_manifest(),
            "model_call_count_total": metrics["model_call_count_total"],
            "sql_generation_strategy": "natural_cqd_direct_sql",
        })
        return FullCaseResult(comparison, final_sql, preflight, logger.case_dir)

    def _run_query_layout_v3(
        self, *, case: dict[str, Any], database: Path, logger: ArtifactLogger,
        comparison_policy: Literal["ordered", "multiset"],
        bif_layout: dict[str, Any], schema_catalog: dict[str, Any],
        semantic_frontend_result: dict[str, Any],
    ) -> FullCaseResult:
        """Run the unified BIF/SIF layout; no semantic layout conversion is allowed."""
        bif_report = {
            "status": "validation_bypassed",
            "semantic_node_count": len(iter_query_nodes(bif_layout)),
            "reason": "BIF layout is advisory input to SIF grounding",
        }
        completion = semantic_frontend_result.get("query_layout_obligations", {}) or {}
        while (
            completion.get("authority") != "BIFLayoutCompletionOrchestrator"
            or completion.get("finalized_once") is not True
        ):
            bif_layout = self.agents.a3_query_layout_from_cqd(
                case=case,
                confirmed_cqd=semantic_frontend_result["confirmed_cqd"],
            )
            semantic_frontend_result = (
                getattr(self.agents, "semantic_frontend_result", None)
                or semantic_frontend_result
            )
            completion = semantic_frontend_result.get(
                "query_layout_obligations", {}
            ) or {}
        history = completion.get("history", []) or []
        if history:
            logger.write_json(
                "frames/bif_layout_ir_baseline.json", history[0].get("layout", {})
            )
            logger.write_json(
                "frames/bif_layout_coverage_00.json", history[0].get("coverage", {})
            )
        for item in history:
            round_number = int(item.get("round", 0))
            cycle = int(item.get("cycle", 0))
            if item.get("source") == "deterministic_ir":
                logger.write_json(
                    f"frames/bif_layout_ir_baseline_cycle_{cycle:02d}.json",
                    item.get("layout", {}),
                )
                logger.write_json(
                    f"frames/bif_layout_coverage_cycle_{cycle:02d}.json",
                    item.get("coverage", {}),
                )
                continue
            logger.write_json(
                f"frames/bif_layout_gap_patch_c{cycle:02d}_r{round_number:02d}.json",
                {
                    "proposal": item.get("proposal", {}),
                    "admitted": item.get("admitted", {}),
                },
            )
        logger.write_json(
            "frames/bif_layout_coverage_final.json", completion.get("coverage", {})
        )
        contract_assessments = list(
            semantic_frontend_result.get("contract_coverage", []) or []
        )
        while len(contract_assessments) != 13:
            refreshed = self.agents.a1_bif_analyze(case, schema_catalog=schema_catalog)
            bif_layout = refreshed["draft_bif"]
            semantic_frontend_result = (
                getattr(self.agents, "semantic_frontend_result", None)
                or semantic_frontend_result
            )
            contract_assessments = list(
                semantic_frontend_result.get("contract_coverage", []) or []
            )
        logger.write_json("frames/semantic_frontend_result.json", semantic_frontend_result)
        logger.write_json("frames/bif_contract_coverage.json", contract_assessments)
        logger.write_json("frames/bif_query_intent_layout.json", bif_layout)
        logger.write_json("frames/bif_query_intent_layout_validation.json", bif_report)
        self._step(logger, "P02", "BIF_QUERY_INTENT_LAYOUT_CONFIRMED", {
            "semantic_nodes": bif_report["semantic_node_count"],
            "contract_checks": len(contract_assessments),
        })

        logger.set_step("P03")
        sif_overlay = create_sif_overlay(bif_layout)
        logger.write_json("frames/sif_query_intent_overlay.json", sif_overlay)
        grounding_feedback: list[dict[str, Any]] = []
        grounding_attempt = 0
        while True:
            grounding_attempt += 1
            request_id = f"link-{case['question_id']}-{grounding_attempt:02d}"
            try:
                linked_layout = self.agents.a4_query_layout_grounding_proposal(
                    case, bif_layout, sif_overlay, schema_catalog,
                    request_id=request_id, technical_feedback=grounding_feedback,
                )
            except ContractError as exc:
                grounding_feedback.append({
                    "attempt": grounding_attempt,
                    "stage": "grounding_contract",
                    "error": str(exc),
                })
                logger.write_json(
                    f"frames/sif_grounding_retry_{grounding_attempt:02d}.json",
                    grounding_feedback[-1],
                )
                continue
            sif_layout = apply_sif_links(bif_layout, linked_layout)
            try:
                candidate_sql = compile_query_intent_layout(bif_layout, sif_layout)
                self._assert_read_only(candidate_sql)
            except (UnsupportedQueryIntentLayout, PipelineRunError) as exc:
                grounding_feedback.append({
                    "attempt": grounding_attempt,
                    "stage": "deterministic_compilation",
                    "error": str(exc),
                    "rejected_link_proposal": linked_layout,
                })
                logger.write_json(
                    f"frames/sif_grounding_retry_{grounding_attempt:02d}.json",
                    grounding_feedback[-1],
                )
                continue
            candidate_preflight = self.executor.execute(database, candidate_sql)
            if candidate_preflight.status != "success":
                grounding_feedback.append({
                    "attempt": grounding_attempt,
                    "stage": "sql_preflight",
                    "error_code": candidate_preflight.error_code,
                    "error_message": candidate_preflight.error_message,
                    "rejected_link_proposal": linked_layout,
                })
                logger.write_json(
                    f"frames/sif_grounding_retry_{grounding_attempt:02d}.json",
                    grounding_feedback[-1],
                )
                continue
            final_sql = candidate_sql
            preflight = candidate_preflight
            break

        logger.write_json("frames/sif_linking_result.json", linked_layout)
        sif_nodes = iter_query_nodes(sif_layout)
        transition_report = {
            "status": "validation_bypassed",
            "semantic_node_count": len(sif_nodes),
            "technical_node_count": 0,
            "reason": "Simple layout is checked only when compiled to SQL",
        }
        logger.write_json("frames/sif_query_intent_layout.json", sif_layout)
        logger.write_json("frames/bif_sif_transition_validation.json", transition_report)
        self._step(logger, "P04", "SIF_QUERY_INTENT_LAYOUT_GROUNDED", {
            "semantic_nodes": transition_report["semantic_node_count"],
            "technical_nodes": transition_report["technical_node_count"],
        })

        logger.set_step("P05")
        logger.write_text("sql/sql_draft.sql", final_sql)
        self._step(logger, "P05", "QUERY_INTENT_LAYOUT_SQL_COMPILED_BY_CODE")

        logger.set_step("P07")
        logger.write_json(
            "sql/sql_preflight_execution.json", [preflight.to_dict(include_rows=False)]
        )
        self._step(logger, "P07", "QUERY_INTENT_LAYOUT_SQL_PREFLIGHT_EXECUTED")
        logger.write_text("sql/sql_final.sql", final_sql)
        comparison = EvaluationRunner(self.executor).run(
            database=database, gold_sql=case["SQL"], generated_sql=final_sql,
            logger=logger, comparison_policy=comparison_policy,
            commit=False, embedded=True,
        )
        logger.set_step("P13")
        attribution = self._attribute(comparison)
        logger.write_json("evaluation/stage_attribution.json", attribution)
        metrics = {
            "execution_accuracy": comparison.execution_accuracy,
            "equivalent": comparison.equivalent,
            "bif_contract_checks": len(contract_assessments),
            "bif_semantic_nodes": transition_report["semantic_node_count"],
            "sif_technical_nodes": transition_report["technical_node_count"],
            "sif_layout": "4.0-simple-unified-query-layout",
            "semantic_layout_conversion_count": 0,
            "sql_generation_strategy": "deterministic_query_intent_compiler",
            "model_call_count_total": self._model_call_count_total(),
            "model_calls_by_agent": self._model_calls_by_agent(),
        }
        logger.set_step("P14")
        logger.write_json("evaluation/metrics.json", metrics)
        logger.append_event("RUN_COMMITTED", {
            "status": "success" if comparison.equivalent else "failed"
        })
        manifest = logger.build_hash_manifest()
        logger.write_json("run_complete.json", {
            "status": "success" if comparison.equivalent else "failed",
            "final_state": "RUN_COMMITTED", "artifact_count": len(manifest),
            "manifest_valid": logger.verify_hash_manifest(),
            "sif_layout": "4.0-simple-unified-query-layout",
        })
        return FullCaseResult(comparison, final_sql, preflight, logger.case_dir)

    @staticmethod
    def _assert_read_only(sql: str) -> None:
        first = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if first not in {"SELECT", "WITH"}:
            raise PipelineRunError("Generated SQL is not a SELECT/CTE query")

    @staticmethod
    def _normalize_round_limit(value: int | None) -> int | None:
        if value is None or value == 0:
            return None
        if value < 0:
            raise ValueError("Clarification round limits must be zero/unlimited or positive")
        return value

    @staticmethod
    def _apply_bif_decision_provenance(
        bif: dict[str, Any], ledger: list[dict[str, Any]]
    ) -> None:
        states = bif.setdefault("field_states", [])
        by_path = {
            str(item.get("path") or ""): item
            for item in states
            if item.get("path")
        }
        for decision in ledger:
            for path in decision.get("affected_paths", []) or []:
                path = str(path)
                state = by_path.get(path)
                if state is None:
                    state = {
                        "path": path,
                        "state": "user_confirmed",
                        "material": True,
                        "note": f"Confirmed by clarification decision {decision.get('decision_id')}",
                    }
                    states.append(state)
                    by_path[path] = state
                else:
                    state["state"] = "user_confirmed"
                    state["material"] = True
                    state["note"] = (
                        f"Confirmed by clarification decision {decision.get('decision_id')}"
                    )

    def _a2_discover(
        self,
        case: dict[str, Any],
        draft_bif: dict[str, Any],
        ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.agents.a2_bif_discover(case, draft_bif, ledger)

    def _assemble_bif(
        self,
        case: dict[str, Any],
        draft_bif: dict[str, Any],
        ledger: list[dict[str, Any]],
        clarifier: ClarificationOrchestrator,
        materialization_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        method = self.agents.a3_bif_assemble
        if "canonical_intent_state" in inspect.signature(method).parameters:
            kwargs: dict[str, Any] = {
                "canonical_intent_state": clarifier.intent_state.snapshot()
            }
            if "materialization_feedback" in inspect.signature(method).parameters:
                kwargs["materialization_feedback"] = materialization_feedback or []
            return method(case, draft_bif, ledger, **kwargs)
        return method(case, draft_bif, ledger)

    def _draft_sif(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        schema_catalog: dict[str, Any],
        bif_envelope: dict[str, Any],
    ) -> dict[str, Any]:
        method = self.agents.a4_sif_draft
        if "bif_source_metadata" in inspect.signature(method).parameters:
            return method(
                case,
                bif,
                schema_catalog,
                bif_source_metadata={
                    "frame_id": bif_envelope["frame_id"],
                    "revision": bif_envelope["revision"],
                    "frame_hash": bif_envelope["frame_hash"],
                },
            )
        return method(case, bif, schema_catalog)

    def _assemble_sif(
        self,
        bif: dict[str, Any],
        draft_sif: dict[str, Any],
        ledger: list[dict[str, Any]],
        schema_catalog: dict[str, Any],
        source_ambiguities: list[dict[str, Any]],
        clarifier: ClarificationOrchestrator,
        materialization_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        method = self.agents.a6_sif_assemble
        if "canonical_intent_state" in inspect.signature(method).parameters:
            kwargs: dict[str, Any] = {
                "canonical_intent_state": clarifier.intent_state.snapshot()
            }
            if "materialization_feedback" in inspect.signature(method).parameters:
                kwargs["materialization_feedback"] = materialization_feedback or []
            return method(
                bif,
                draft_sif,
                ledger,
                schema_catalog,
                source_ambiguities,
                **kwargs,
            )
        return method(
            bif, draft_sif, ledger, schema_catalog, source_ambiguities
        )

    def _continue_bif_clarification(
        self,
        *,
        case: dict[str, Any],
        bif_draft: dict[str, Any],
        clarifier: ClarificationOrchestrator,
        logger: ArtifactLogger,
        rounds_completed: int,
    ) -> int:
        """Resume BIF clarification after a materialization compliance failure."""
        rounds = rounds_completed
        while True:
            if self.max_bif_rounds is not None and rounds >= self.max_bif_rounds:
                self._step(logger, "P02", "BIF_ROUND_BUDGET_CONTINUED", {
                    "configured_limit": self.max_bif_rounds,
                    "round": rounds,
                })
                self.max_bif_rounds = None
            rounds += 1
            required = clarifier.next_pending()
            discovery = None
            if required is None and clarifier.needs_discovery_audit():
                discovery = self._a2_discover(
                    case, bif_draft, clarifier.decision_ledger()
                )
                candidates, deferred_observations = route_bif_issue_observations(
                    bif_draft,
                    self._discovery_candidates(discovery),
                    validate_bif_semantics(case["question"], bif_draft),
                )
                if deferred_observations:
                    logger.write_json(
                        f"frames/bif_rounds/round_{rounds:02d}/deferred_observations.json",
                        deferred_observations,
                    )
                clarifier.record_discovery_audit(
                    round_number=rounds, candidate_count=len(candidates)
                )
                clarifier.register_candidates(
                    candidates,
                    source="a2_compliance_discovery",
                    round_number=rounds,
                    allowed_paths=self._frame_paths(bif_draft),
                )
                required = clarifier.next_pending()
            if required is None and clarifier.ready_for_materialization():
                return rounds
            if required is None:
                self._step(logger, "P02", "BIF_NO_NEXT_ACTION_CONTINUED", {
                    "round": rounds,
                })
                return rounds
            question_draft = self._a2_question(
                case,
                bif_draft,
                required,
                clarifier.decision_ledger(),
            )
            request = clarifier.build_request(
                str(required["decision_id"]), question_draft
            )
            interaction = self._interaction_envelope(required, request)
            round_root = f"frames/bif_rounds/round_{rounds:02d}"
            logger.write_json(f"{round_root}/interaction.json", interaction)
            if discovery is not None:
                logger.write_json(f"{round_root}/issue_observations.json", discovery)
            logger.write_json(f"{round_root}/clarification_request.json", request)
            clarifier.bind_request(
                str(required["decision_id"]), request, round_number=rounds
            )
            response = self.agents.e2_user(
                mode="BIF_CLARIFICATION",
                case=case,
                clarification=interaction,
                oracle_context=build_e2_oracle_context(
                    case["SQL"], interaction, original_question=case["question"]
                ),
            )
            logger.write_json(f"{round_root}/user_response.json", response)
            try:
                clarifier.resolve(
                    str(required["decision_id"]),
                    response,
                    round_number=rounds,
                    context={"trigger": "materialization_compliance"},
                )
                self._write_clarification_transaction(
                    logger, round_root, clarifier
                )
            except ClarificationStateError as exc:
                raise PipelineRunError(
                    f"BIF_CLARIFICATION_STATE_ERROR: {exc}"
                ) from exc
            logger.write_json(
                f"{round_root}/orchestrator_state.json", clarifier.snapshot()
            )

    def _materialize_bif_until_compliant(
        self,
        *,
        case: dict[str, Any],
        bif_draft: dict[str, Any],
        clarifier: ClarificationOrchestrator,
        logger: ArtifactLogger,
        rounds_completed: int,
    ) -> tuple[
        dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any], int
    ]:
        revision = 0
        rounds = rounds_completed
        materialization_feedback: list[dict[str, Any]] = []
        feedback_attempts: dict[str, int] = {}
        semantic_attempts: dict[str, int] = {}
        while True:
            revision += 1
            ledger = clarifier.decision_ledger()
            assembly = self._assemble_bif(
                case,
                bif_draft,
                ledger,
                clarifier,
                materialization_feedback=materialization_feedback,
            )
            assembly["applied_decision_ids"] = [
                str(item["decision_id"]) for item in ledger
            ]
            logger.write_json(
                f"frames/bif_materializations/revision_{revision:02d}/assembly_bundle.json",
                assembly,
            )
            bif, reference_repairs = normalize_bif_semantic_references(
                assembly["confirmed_bif"]
            )
            bif, authority_events = apply_bif_atomic_authority(
                case["question"], bif_draft, bif
            )
            if authority_events:
                logger.write_json(
                    f"frames/bif_materializations/revision_{revision:02d}/atomic_authority_events.json",
                    authority_events,
                )
            admission_findings = validate_bif_atomic_admission(
                case["question"], bif_draft, bif
            )
            logger.write_json(
                f"frames/bif_materializations/revision_{revision:02d}/atomic_admission_findings.json",
                admission_findings,
            )
            if admission_findings:
                raise PipelineRunError(
                    "BIF_ATOMIC_ADMISSION_REJECTED: A3 changed immutable business atoms"
                )
            structured_repairs: list[dict[str, Any]] = []
            for decision in ledger:
                interaction = {
                    "clarification_request": decision.get("request") or {}
                }
                response = decision.get("user_response") or {}
                repair_function = {
                    "LOCATION_ROLE": apply_structured_location_role_response,
                    "LOGIC_RELATION": apply_structured_logic_response,
                    "POPULATION_CONDITION_STRUCTURE": apply_structured_logic_response,
                    "OUTPUT_SHAPE": apply_structured_output_presentation_response,
                }.get(str(decision.get("issue") or ""))
                if repair_function is None:
                    continue
                repaired, repairs = repair_function(bif, interaction, response)
                if repaired is not None:
                    bif = repaired
                    structured_repairs.extend(repairs)
            if structured_repairs:
                logger.write_json(
                    f"frames/bif_materializations/revision_{revision:02d}/confirmed_decision_repairs.json",
                    structured_repairs,
                )
            bif, post_decision_atomic_repairs = normalize_bif_atomic_authority(
                case["question"], bif
            )
            if post_decision_atomic_repairs:
                logger.write_json(
                    f"frames/bif_materializations/revision_{revision:02d}/post_decision_atomic_repairs.json",
                    post_decision_atomic_repairs,
                )
            bif, output_repairs = remove_unrequested_bif_outputs(
                case["question"], bif
            )
            if output_repairs:
                logger.write_json(
                    f"frames/bif_materializations/revision_{revision:02d}/unrequested_output_repairs.json",
                    output_repairs,
                )
            invariant_report = self._validate_decision_path_coverage(
                bif, ledger, layer="BIF"
            )
            self._apply_bif_decision_provenance(bif, ledger)
            readiness = bif.setdefault("readiness", {})
            readiness.update({"state": "ready", "blocking_paths": [], "reason": None})
            contract = getattr(self.agents, "bif_schema", None)
            if contract:
                try:
                    validate_contract(bif, contract, "Provisional BIF")
                except ContractError as exc:
                    raise PipelineRunError(f"BIF_ASSEMBLY_INVALID: {exc}") from exc
            semantic_findings = validate_bif_semantics(case["question"], bif)
            compliance_findings = validate_decision_compliance(
                bif, ledger, layer="BIF"
            )
            logger.write_json(
                f"frames/bif_materializations/revision_{revision:02d}/semantic_findings.json",
                semantic_findings,
            )
            logger.write_json(
                f"frames/bif_materializations/revision_{revision:02d}/decision_compliance.json",
                compliance_findings,
            )
            if not semantic_findings and not compliance_findings:
                logger.write_json("frames/bif_assembly_bundle.json", assembly)
                logger.write_json("frames/bif_decision_invariants.json", invariant_report)
                logger.write_json("frames/bif_semantic_findings_final.json", [])
                return bif, assembly, reference_repairs, invariant_report, rounds
            if compliance_findings:
                materialization_feedback = [
                    {
                        "kind": item.get("kind"),
                        "issue_type": item.get("issue_type"),
                        "subject_ref": item.get("subject_ref"),
                        "description": item.get("description"),
                        "required_canonical_value": item.get("canonical_value"),
                    }
                    for item in compliance_findings
                ] + [
                    {
                        "kind": "semantic_structure_mismatch",
                        "issue_type": item.get("issue"),
                        "subject_ref": item.get("path"),
                        "description": item.get("description"),
                    }
                    for item in semantic_findings
                ]
                feedback_signature = hashlib.sha256(
                    json.dumps(
                        materialization_feedback,
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
                feedback_attempts[feedback_signature] = (
                    feedback_attempts.get(feedback_signature, 0) + 1
                )
                logger.write_json(
                    f"frames/bif_materializations/revision_{revision:02d}/system_correction_feedback.json",
                    {
                        "attempt": feedback_attempts[feedback_signature],
                        "feedback": materialization_feedback,
                        "routing": "A3_REMATERIALIZATION_WITHOUT_E2",
                    },
                )
                if feedback_attempts[feedback_signature] >= 3:
                    raise PipelineRunError(
                        "BIF_MATERIALIZATION_FAILED: identical confirmed-decision "
                        "compliance mismatch remained after three A3 attempts"
                    )
                continue
            materialization_feedback = []
            semantic_signature = hashlib.sha256(
                json.dumps(
                    semantic_findings,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            semantic_attempts[semantic_signature] = (
                semantic_attempts.get(semantic_signature, 0) + 1
            )
            if semantic_attempts[semantic_signature] >= 3:
                raise PipelineRunError(
                    "BIF_SEMANTIC_CLARIFICATION_STALLED: identical semantic "
                    "finding remained after three clarification/materialization cycles"
                )
            registered = clarifier.register_findings(
                semantic_findings,
                source="bif_materialization_semantic_compliance",
                round_number=rounds + 1,
                allowed_paths=self._frame_paths(bif_draft) | self._frame_paths(bif),
            )
            registered += clarifier.register_candidates(
                compliance_findings,
                source="bif_materialization_decision_compliance",
                round_number=rounds + 1,
                allowed_paths=self._frame_paths(bif_draft) | self._frame_paths(bif),
            )
            if not registered or clarifier.next_pending() is None:
                raise PipelineRunError(
                    "BIF_ASSEMBLY_INVALID: compliance findings could not be routed to clarification"
                )
            logger.write_json(
                f"frames/bif_materializations/revision_{revision:02d}/reentry.json",
                {"registered_decision_ids": registered},
            )
            rounds = self._continue_bif_clarification(
                case=case,
                bif_draft=bif_draft,
                clarifier=clarifier,
                logger=logger,
                rounds_completed=rounds,
            )

    def _finalize_bif_with_revision_loop(
        self,
        *,
        case: dict[str, Any],
        bif_draft: dict[str, Any],
        bif: dict[str, Any],
        clarifier: ClarificationOrchestrator,
        logger: ArtifactLogger,
        rounds_completed: int,
    ) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
        if bool(getattr(self.agents, "semantic_frontend_active", False)):
            return bif, rounds_completed, clarifier.decision_ledger()
        rounds = rounds_completed
        current = bif
        while True:
            summary = self._frame_user_summary(current, layer="bif")
            clarifier.mark_provisional_frame(summary)
            final_round = rounds + 1
            request = clarifier.build_final_confirmation_request()
            interaction = {
                "stage": "bif",
                "decision": None,
                "clarification_request": request,
            }
            round_root = f"frames/bif_rounds/round_{final_round:02d}"
            logger.write_json(f"{round_root}/interaction.json", interaction)
            logger.write_json(f"{round_root}/clarification_request.json", request)
            logger.write_json(
                f"frames/bif_materializations/final_{final_round:02d}/user_facing_summary.json",
                {"summary": summary},
            )
            response = self.agents.e2_user(
                mode="BIF_CLARIFICATION",
                case=case,
                clarification=interaction,
                oracle_context=build_e2_oracle_context(
                    case["SQL"], interaction, original_question=case["question"]
                ),
            )
            logger.write_json(f"{round_root}/user_response.json", response)
            try:
                outcome = clarifier.record_final_confirmation(
                    request, response, round_number=final_round
                )
            except ClarificationStateError as exc:
                raise PipelineRunError(f"BIF_FINAL_CONFIRMATION_ERROR: {exc}") from exc
            logger.write_json(
                f"frames/bif_materializations/final_{final_round:02d}/final_confirmation.json",
                {"request": request, "response": response, "outcome": outcome},
            )
            if outcome == "confirmed":
                if not clarifier.can_terminate():
                    raise PipelineRunError(
                        "BIF clarification did not terminate after confirmation"
                    )
                return current, rounds, clarifier.decision_ledger()
            correction = str(response.get("free_text") or "").strip()
            clarifier.record_final_revision_correction(
                correction, round_number=final_round
            )
            rounds = self._continue_bif_clarification(
                case=case,
                bif_draft=current,
                clarifier=clarifier,
                logger=logger,
                rounds_completed=final_round,
            )
            current, _, _, _, rounds = self._materialize_bif_until_compliant(
                case=case,
                bif_draft=current,
                clarifier=clarifier,
                logger=logger,
                rounds_completed=rounds,
            )

    @staticmethod
    def _route_post_bif_candidates(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep post-BIF physical gaps in SIF and reject new semantic ambiguity."""
        bif_issue_types = {
            "DECISION_OBJECTIVE",
            "TARGET_POPULATION",
            "POPULATION_CONDITION_STRUCTURE",
            "LOCATION_ROLE",
            "METRIC_DEFINITION",
            "CALCULATION_METHOD",
            "LOGIC_RELATION",
            "GRANULARITY",
            "SCOPE_SELECTION",
            "TIME_BASIS",
            "COMPARISON",
            "OUTPUT_SHAPE",
        }
        physical_terms = re.compile(
            r"\b(schema|table|column|field|code|coded|mapping|mapped|stored|"
            r"join|foreign key|operator|predicate|attribute|equals?|in\s*\(|like)\b",
            re.IGNORECASE,
        )
        routed: list[dict[str, Any]] = []
        for candidate in candidates:
            issue_type = str(candidate.get("issue_type") or "")
            if issue_type not in bif_issue_types:
                routed.append(candidate)
                continue
            evidence = " ".join(
                [
                    str(candidate.get("description") or ""),
                    *[str(item) for item in candidate.get("alternatives", [])],
                ]
            )
            if not physical_terms.search(evidence):
                raise PipelineRunError(
                    "SEMANTIC_FRONTEND_INCOMPLETE: SIF discovered a new business "
                    f"decision after Confirmed CQD ({issue_type})"
                )
            revised = copy.deepcopy(candidate)
            revised["issue_type"] = "TECHNICAL_GROUNDING"
            routed.append(revised)
        return routed

    @staticmethod
    def _frame_user_summary(frame: dict[str, Any], *, layer: str) -> str:
        if layer == "bif":
            population = frame.get("target_population", {}) or {}
            population_text = (
                population.get("description")
                or population.get("summary")
                or "the population described in the request"
            )
            metric_labels = [
                str(item.get("label") or item.get("name") or item.get("description") or "metric")
                for item in frame.get("metrics", []) or []
                if isinstance(item, dict)
            ]
            output_labels = [
                str(item.get("label") or item.get("name") or "result value")
                for item in (frame.get("output_expectation", {}) or {}).get("fields", []) or []
                if isinstance(item, dict)
            ]
            parts = [
                f"Target population: {population_text}",
                f"Measures: {', '.join(metric_labels) if metric_labels else 'no separate calculated measure'}",
                f"Time basis: {FullCaseRunner._plain_business_value(frame.get('time_basis'))}",
                f"Output: {', '.join(output_labels) if output_labels else 'the requested result'}",
            ]
        else:
            blocks = frame.get("query_blocks", []) or []
            filter_count = sum(
                len(block.get("where", []) or [])
                for block in blocks if isinstance(block, dict)
            )
            grouped = any(
                block.get("group_by")
                for block in blocks if isinstance(block, dict)
            )
            output_labels = [
                str(item.get("label") or "result value")
                for block in blocks if isinstance(block, dict)
                for item in block.get("projection", []) or [] if isinstance(item, dict)
            ]
            parts = [
                f"Query organization: {len(blocks)} logical stage(s)",
                f"Filtering: {filter_count} confirmed condition(s)",
                f"Grouping: {'grouped results' if grouped else 'no additional grouping'}",
                f"Output: {', '.join(output_labels) if output_labels else 'the requested result'}",
            ]
        return "; ".join(parts)

    @staticmethod
    def _plain_business_value(value: Any) -> str:
        if value in (None, {}, []):
            return "as stated in the request"
        if isinstance(value, dict):
            for key in ("summary", "description", "label", "value", "period"):
                if value.get(key) not in (None, "", [], {}):
                    return str(value[key])
            return ", ".join(
                f"{key}: {FullCaseRunner._plain_business_value(child)}"
                for key, child in value.items()
                if child not in (None, "", [], {})
            ) or "as stated in the request"
        if isinstance(value, list):
            return ", ".join(FullCaseRunner._plain_business_value(item) for item in value)
        return str(value)

    def _a2_question(
        self,
        case: dict[str, Any],
        draft_bif: dict[str, Any],
        decision: dict[str, Any],
        ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.agents.a2_bif_question(case, draft_bif, decision, ledger)

    def _a5_discover(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif: dict[str, Any],
        ledger: list[dict[str, Any]],
        source_ambiguities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.agents.a5_sif_discover(
            case, bif, sif, ledger, source_ambiguities
        )

    def _continue_sif_clarification(
        self,
        *,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif_draft: dict[str, Any],
        clarifier: ClarificationOrchestrator,
        schema_catalog: dict[str, Any],
        logger: ArtifactLogger,
        rounds_completed: int,
    ) -> int:
        rounds = rounds_completed
        while True:
            if self.max_sif_rounds is not None and rounds >= self.max_sif_rounds:
                self._step(logger, "P04", "SIF_ROUND_BUDGET_CONTINUED", {
                    "configured_limit": self.max_sif_rounds,
                    "round": rounds,
                })
                self.max_sif_rounds = None
            rounds += 1
            required = clarifier.next_pending()
            discovery = None
            if required is None and clarifier.needs_discovery_audit():
                # A compliance retry reasserts an already discovered SIF slot. It cannot
                # introduce a new business meaning, so a deterministic zero-candidate
                # audit closes this revision without another model discovery call.
                clarifier.record_discovery_audit(
                    round_number=rounds, candidate_count=0
                )
                required = clarifier.next_pending()
            if required is None and clarifier.ready_for_materialization():
                return rounds
            if required is None:
                self._step(logger, "P04", "SIF_NO_NEXT_ACTION_CONTINUED", {
                    "round": rounds,
                })
                return rounds
            question_draft = self._a5_question(
                case,
                bif,
                sif_draft,
                required,
                clarifier.decision_ledger(),
                schema_catalog,
                [],
            )
            request = clarifier.build_request(
                str(required["decision_id"]), question_draft
            )
            interaction = self._interaction_envelope(required, request)
            round_root = f"frames/sif_rounds/round_{rounds:02d}"
            logger.write_json(f"{round_root}/interaction.json", interaction)
            if discovery is not None:
                logger.write_json(f"{round_root}/issue_observations.json", discovery)
            logger.write_json(f"{round_root}/clarification_request.json", request)
            clarifier.bind_request(
                str(required["decision_id"]), request, round_number=rounds
            )
            response = self.agents.e2_user(
                mode="SIF_CLARIFICATION",
                case=case,
                clarification=interaction,
                confirmed_bif_summary={
                    "decision_objective": bif.get("decision_objective"),
                    "target_population": bif.get("target_population"),
                    "metrics": bif.get("metrics"),
                    "output_expectation": bif.get("output_expectation"),
                },
                oracle_context=build_e2_oracle_context(case["SQL"], interaction),
            )
            logger.write_json(f"{round_root}/user_response.json", response)
            try:
                clarifier.resolve(
                    str(required["decision_id"]),
                    response,
                    round_number=rounds,
                    context={"trigger": "materialization_compliance"},
                )
                self._write_clarification_transaction(
                    logger, round_root, clarifier
                )
            except ClarificationStateError as exc:
                raise PipelineRunError(
                    f"SIF_CLARIFICATION_STATE_ERROR: {exc}"
                ) from exc

    def _rematerialize_sif_until_compliant(
        self,
        *,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif_draft: dict[str, Any],
        current_sif: dict[str, Any],
        clarifier: ClarificationOrchestrator,
        schema_catalog: dict[str, Any],
        logger: ArtifactLogger,
        locked_tables: set[str],
        reviewed_ambiguities: list[dict[str, Any]],
        rounds_completed: int,
    ) -> tuple[dict[str, Any], int]:
        candidate = current_sif
        rounds = rounds_completed
        revision = 0
        feedback_attempts: dict[str, int] = {}
        while True:
            findings = validate_decision_compliance(
                candidate, clarifier.decision_ledger(), layer="SIF"
            )
            logger.write_json(
                f"frames/sif_materializations/revision_{revision + 1:02d}/decision_compliance.json",
                findings,
            )
            if not findings:
                return candidate, rounds
            materialization_feedback = [
                {
                    "kind": item.get("kind"),
                    "issue_type": item.get("issue_type"),
                    "subject_ref": item.get("subject_ref"),
                    "description": item.get("description"),
                    "required_canonical_value": item.get("canonical_value"),
                }
                for item in findings
            ]
            signature = hashlib.sha256(
                json.dumps(
                    materialization_feedback,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            feedback_attempts[signature] = feedback_attempts.get(signature, 0) + 1
            if feedback_attempts[signature] >= 3:
                raise PipelineRunError(
                    "SIF_MATERIALIZATION_FAILED: identical confirmed-decision "
                    "compliance mismatch remained after three A6 attempts"
                )
            revision += 1
            ledger = clarifier.decision_ledger()
            assembly = self._assemble_sif(
                bif,
                sif_draft,
                ledger,
                schema_catalog,
                reviewed_ambiguities,
                clarifier,
                materialization_feedback=materialization_feedback,
            )
            logger.write_json(
                f"frames/sif_materializations/revision_{revision:02d}/system_correction_feedback.json",
                {
                    "attempt": feedback_attempts[signature],
                    "feedback": materialization_feedback,
                    "routing": "A6_REMATERIALIZATION_WITHOUT_E2",
                },
            )
            rematerialized = assembly["confirmed_sif"]
            rematerialized, _ = normalize_sif_intent_references(rematerialized, bif)
            rematerialized, _ = repair_schema_documented_grounding(
                rematerialized, bif, schema_catalog
            )
            rematerialized, _ = repair_unrequested_supporting_projections(
                rematerialized, bif
            )
            self._validate_decision_path_coverage(
                rematerialized,
                ledger,
                layer="SIF",
                allow_intent_references=True,
            )
            candidate, events = reduce_sif_candidate(
                sif_draft,
                rematerialized,
                bif=bif,
                locked_tables=locked_tables,
                schema_catalog=schema_catalog,
            )
            if any(
                item.get("operation") in {
                    "reject_sif_candidate", "reject_destructive_grounding_candidate"
                }
                for item in events
            ):
                raise PipelineRunError(
                    "SIF_COMPLIANCE_REMATERIALIZATION_REJECTED"
                )
            logger.write_json(
                f"frames/sif_materializations/revision_{revision:02d}/assembly_bundle.json",
                assembly,
            )

    def _finalize_sif_with_revision_loop(
        self,
        *,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif_draft: dict[str, Any],
        sif: dict[str, Any],
        clarifier: ClarificationOrchestrator,
        schema_catalog: dict[str, Any],
        logger: ArtifactLogger,
        locked_tables: set[str],
        reviewed_ambiguities: list[dict[str, Any]],
        rounds_completed: int,
    ) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
        current = sif
        rounds = rounds_completed
        while True:
            summary = self._frame_user_summary(current, layer="sif")
            clarifier.mark_provisional_frame(summary)
            final_round = rounds + 1
            request = clarifier.build_final_confirmation_request()
            interaction = {
                "stage": "sif",
                "decision": None,
                "clarification_request": request,
            }
            round_root = f"frames/sif_rounds/round_{final_round:02d}"
            logger.write_json(f"{round_root}/interaction.json", interaction)
            logger.write_json(f"{round_root}/clarification_request.json", request)
            logger.write_json(
                f"frames/sif_materializations/final_{final_round:02d}/user_facing_summary.json",
                {"summary": summary},
            )
            response = self.agents.e2_user(
                mode="SIF_CLARIFICATION",
                case=case,
                clarification=interaction,
                confirmed_bif_summary={
                    "decision_objective": bif.get("decision_objective"),
                    "target_population": bif.get("target_population"),
                    "metrics": bif.get("metrics"),
                    "output_expectation": bif.get("output_expectation"),
                },
                oracle_context=build_e2_oracle_context(case["SQL"], interaction),
            )
            logger.write_json(f"{round_root}/user_response.json", response)
            try:
                outcome = clarifier.record_final_confirmation(
                    request, response, round_number=final_round
                )
            except ClarificationStateError as exc:
                raise PipelineRunError(f"SIF_FINAL_CONFIRMATION_ERROR: {exc}") from exc
            logger.write_json(
                f"frames/sif_materializations/final_{final_round:02d}/final_confirmation.json",
                {"request": request, "response": response, "outcome": outcome},
            )
            if outcome == "confirmed":
                if not clarifier.can_terminate():
                    raise PipelineRunError(
                        "SIF clarification did not terminate after confirmation"
                    )
                return current, rounds, clarifier.decision_ledger()
            clarifier.record_final_revision_correction(
                str(response.get("free_text") or "").strip(),
                round_number=final_round,
            )
            rounds = self._continue_sif_clarification(
                case=case,
                bif=bif,
                sif_draft=sif_draft,
                clarifier=clarifier,
                schema_catalog=schema_catalog,
                logger=logger,
                rounds_completed=final_round,
            )
            revision_assembly = self._assemble_sif(
                bif,
                sif_draft,
                clarifier.decision_ledger(),
                schema_catalog,
                reviewed_ambiguities,
                clarifier,
            )
            revision_candidate = revision_assembly["confirmed_sif"]
            revision_candidate, _ = normalize_sif_intent_references(
                revision_candidate, bif
            )
            revision_candidate, _ = repair_schema_documented_grounding(
                revision_candidate, bif, schema_catalog
            )
            revision_candidate, _ = repair_unrequested_supporting_projections(
                revision_candidate, bif
            )
            self._validate_decision_path_coverage(
                revision_candidate,
                clarifier.decision_ledger(),
                layer="SIF",
                allow_intent_references=True,
            )
            current, revision_events = reduce_sif_candidate(
                sif_draft,
                revision_candidate,
                bif=bif,
                locked_tables=locked_tables,
                schema_catalog=schema_catalog,
            )
            if any(
                item.get("operation") in {
                    "reject_sif_candidate", "reject_destructive_grounding_candidate"
                }
                for item in revision_events
            ):
                raise PipelineRunError("SIF_FINAL_REVISION_REMATERIALIZATION_REJECTED")
            current, rounds = self._rematerialize_sif_until_compliant(
                case=case,
                bif=bif,
                sif_draft=sif_draft,
                current_sif=current,
                clarifier=clarifier,
                schema_catalog=schema_catalog,
                logger=logger,
                locked_tables=locked_tables,
                reviewed_ambiguities=reviewed_ambiguities,
                rounds_completed=rounds,
            )

    def _a5_question(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif: dict[str, Any],
        decision: dict[str, Any],
        ledger: list[dict[str, Any]],
        schema_catalog: dict[str, Any],
        source_ambiguities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.agents.a5_sif_question(case, bif, sif, decision, ledger)

    @staticmethod
    def _discovery_candidates(discovery: dict[str, Any]) -> list[dict[str, Any]]:
        if set(discovery) != {"candidates"}:
            raise PipelineRunError(
                "DISCOVERY_CONTRACT_VIOLATION: expected only the candidates field"
            )
        return list(discovery.get("candidates", []) or [])

    @staticmethod
    def _interaction_envelope(
        decision: dict[str, Any], request: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "stage": str(decision["stage"]),
            "decision": {
                "decision_id": str(decision["decision_id"]),
                "issue_type": str(decision["issue"]),
                "description": str(decision["description"]),
                "affected_paths": list(decision.get("affected_paths", [])),
            },
            "clarification_request": request,
        }

    @staticmethod
    def _write_clarification_transaction(
        logger: ArtifactLogger,
        round_root: str,
        clarifier: ClarificationOrchestrator,
    ) -> None:
        transaction = clarifier.last_transaction()
        if not transaction:
            return
        mapping = {
            "queue_before": "queue_before.json",
            "selected_decision": "selected_decision.json",
            "canonical_request": "canonical_request.json",
            "user_response": "user_response.json",
            "canonical_effect": "canonical_effect.json",
            "intent_state_before": "intent_state_before.json",
            "intent_state_after": "intent_state_after.json",
            "reconciliation_events": "reconciliation_events.json",
            "queue_after": "queue_after.json",
        }
        for key, filename in mapping.items():
            if key in transaction:
                logger.write_json(f"{round_root}/{filename}", transaction[key])
        logger.write_json(
            f"{round_root}/transaction_result.json",
            {
                "status": transaction.get("transaction_result"),
                "decision_id": transaction.get("decision_id"),
                "round": transaction.get("round"),
                "reason": transaction.get("reason"),
            },
        )

    @staticmethod
    def _frame_paths(frame: Any) -> set[str]:
        paths: set[str] = set()

        def visit(value: Any, path: str) -> None:
            if path:
                paths.add(path)
            if isinstance(value, dict):
                for key, child in value.items():
                    token = str(key).replace("~", "~0").replace("/", "~1")
                    visit(child, f"{path}/{token}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}/{index}")

        visit(frame, "")
        return paths

    @classmethod
    def _validate_decision_path_coverage(
        cls,
        frame: dict[str, Any],
        ledger: list[dict[str, Any]],
        *,
        layer: str,
        allow_intent_references: bool = False,
    ) -> dict[str, Any]:
        frame_paths = cls._frame_paths(frame)
        intent_references: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "intent_path" and isinstance(child, str):
                        intent_references.add(child)
                    elif key == "intent_paths" and isinstance(child, list):
                        intent_references.update(
                            str(item) for item in child if isinstance(item, str)
                        )
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        if allow_intent_references:
            collect(frame)

        covered: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        for decision in ledger:
            decision_id = str(decision.get("decision_id") or "")
            semantic_container = {
                "LOCATION_ROLE": "/target_population",
                "TARGET_POPULATION": "/target_population",
                "POPULATION_CONDITION_STRUCTURE": "/target_population",
                "LOGIC_RELATION": "/target_population",
                "METRIC_DEFINITION": "/metrics",
                "CALCULATION_METHOD": "/calculation",
                "OUTPUT_SHAPE": "/output_expectation",
                "QUERY_GRAIN": "/query_blocks",
                "AGGREGATION_PLACEMENT": "/query_blocks",
                "QUERY_SELECTION_SCOPE": "/query_blocks",
                "QUERY_TIME_SEMANTICS": "/query_blocks",
                "QUERY_COMPARISON_SEMANTICS": "/query_blocks",
                "QUERY_OUTPUT_SEMANTICS": "/query_blocks",
                "SOURCE_POPULATION": "/query_blocks",
            }.get(str(decision.get("issue") or ""))
            for path in decision.get("affected_paths", []) or []:
                path = str(path)
                direct = path in frame_paths
                traced = allow_intent_references and any(
                    reference == path
                    or reference.startswith(path + "/")
                    or path.startswith(reference + "/")
                    for reference in intent_references
                )
                semantic_covered = bool(
                    decision.get("canonical_effect")
                    and semantic_container
                    and semantic_container in frame_paths
                )
                if direct or traced or semantic_covered:
                    covered.append({
                        "decision_id": decision_id,
                        "path": path,
                        "coverage": (
                            "frame_path"
                            if direct
                            else "intent_reference"
                            if traced
                            else "semantic_container"
                        ),
                    })
                else:
                    missing.append({"decision_id": decision_id, "path": path})
        if missing:
            raise PipelineRunError(
                f"{layer}_DECISION_INVARIANT_FAILED: missing_paths={missing}"
            )
        return {
            "layer": layer,
            "status": "valid",
            "decision_count": len(ledger),
            "covered_paths": covered,
            "missing_paths": [],
        }

    @staticmethod
    def _attribute(comparison: ComparisonRecord) -> dict[str, Any]:
        if comparison.equivalent:
            return {"primary_stage": "SUCCESS", "reason": "Execution results are equivalent"}
        if comparison.reason == "REFERENCE_EXECUTION_FAILED":
            return {"primary_stage": "REFERENCE", "reason": comparison.reason}
        if comparison.reason == "GENERATED_EXECUTION_FAILED":
            return {"primary_stage": "SQL", "reason": comparison.reason}
        return {
            "primary_stage": "UNRESOLVED",
            "reason": "Semantic result mismatch requires BIF/SIF/SQL trace review",
        }

    def _model_calls_by_agent(self) -> dict[str, int]:
        client = getattr(self.agents, "client", None)
        counts = getattr(client, "call_counts", {})
        return dict(counts) if isinstance(counts, dict) else {}

    def _model_call_count_total(self) -> int:
        return sum(self._model_calls_by_agent().values())

    @staticmethod
    def _grounding_warnings(
        database: Path,
        bif: dict[str, Any],
        sif: dict[str, Any],
        schema_catalog: dict[str, Any],
    ) -> list[dict[str, Any]]:
        warnings = validate_literal_domains(database, sif) + validate_sif_coverage(
            bif, sif, schema_catalog
        )
        if sif.get("query_blocks"):
            try:
                compile_sif(sif)
            except UnsupportedSIFError as error:
                warnings.append(
                    {
                        "issue": "unsupported_sif_feature",
                        "role": "system",
                        "error": str(error),
                    }
                )
        return warnings
