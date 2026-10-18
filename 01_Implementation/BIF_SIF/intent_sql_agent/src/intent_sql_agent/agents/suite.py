from __future__ import annotations

import copy
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient
from intent_sql_agent.services.change_set import (
    SIF_SECTION_CHANGE_SET_SCHEMA,
)
from intent_sql_agent.services.bif_contract_coverage import (
    BIF_CONTRACT_ASSESSMENT_SCHEMA,
)
from intent_sql_agent.services.contract_validator import validate_contract
from intent_sql_agent.services.e2_oracle import enforce_oracle_response
from intent_sql_agent.services.frame_state import frame_hash
from intent_sql_agent.services.semantic_frontend import SemanticFrontendService
from intent_sql_agent.services.sif_v2_patch import SIF_V2_GROUNDING_PROPOSAL_SCHEMA
from intent_sql_agent.services.query_intent_layout import (
    grounding_proposal_schema,
)
from intent_sql_agent.services.cqd_layout_obligations import (
    compile_layout_obligations,
)
from intent_sql_agent.services.bif_layout_completion_orchestrator import (
    BIF_LAYOUT_GAP_PATCH_SCHEMA,
    BIFLayoutCompletionOrchestrator,
)
from intent_sql_agent.services.direct_sif import (
    QUERY_UNDERSTANDING_SCHEMA,
    validate_understanding_coverage,
)
from intent_sql_agent.services.direct_sif_materializer import (
    compile_understanding_from_cqd,
    materialize_grounding_bundle,
)


E2_INTERACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stage", "decision", "clarification_request"],
    "properties": {
        "stage": {"enum": ["bif", "sif"]},
        "decision": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["decision_id", "issue_type", "description", "affected_paths"],
            "properties": {
                "decision_id": {"type": "string"},
                "issue_type": {"type": "string"},
                "description": {"type": "string"},
                "affected_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
        "clarification_request": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "request_id", "stage", "gap_id", "question", "why_it_matters",
                "options", "allow_free_text", "affected_paths",
            ],
            "properties": {
                "request_id": {"type": "string"},
                "stage": {"enum": ["bif", "sif"]},
                "gap_id": {"type": "string"},
                "question": {"type": "string", "minLength": 1},
                "why_it_matters": {"type": "string", "minLength": 1},
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["option_id", "label"],
                        "properties": {
                            "option_id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
                "allow_free_text": {"type": "boolean"},
                "affected_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}

USER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "request_id", "mode", "action", "selected_option_ids", "free_text", "confidence"
    ],
    "properties": {
        "request_id": {"type": "string"},
        "mode": {"enum": ["BIF_CLARIFICATION", "SIF_CLARIFICATION"]},
        "action": {"enum": ["select", "free_text", "cannot_answer"]},
        "selected_option_ids": {"type": "array", "items": {"type": "string"}},
        "free_text": {"type": ["string", "null"]},
        "confidence": {"enum": ["high", "medium", "low"]},
    },
}

ISSUE_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "issue_type", "subject_ref", "description", "alternatives",
                ],
                "properties": {
                    "issue_type": {"enum": [
                        "DECISION_OBJECTIVE", "TARGET_POPULATION",
                        "POPULATION_CONDITION_STRUCTURE", "LOCATION_ROLE",
                        "METRIC_DEFINITION", "CALCULATION_METHOD", "LOGIC_RELATION",
                        "GRANULARITY", "SCOPE_SELECTION", "TIME_BASIS", "COMPARISON",
                        "OUTPUT_SHAPE", "QUERY_GRAIN", "AGGREGATION_PLACEMENT",
                        "QUERY_SELECTION_SCOPE", "QUERY_TIME_SEMANTICS",
                        "QUERY_COMPARISON_SEMANTICS", "QUERY_OUTPUT_SEMANTICS",
                        "SOURCE_POPULATION", "PHYSICAL_GROUNDING", "DATA_AVAILABILITY",
                        "DATA_QUALITY", "SQL_IMPLEMENTATION", "TECHNICAL_GROUNDING",
                    ]},
                    "subject_ref": {"type": "string", "pattern": "^/"},
                    "description": {"type": "string", "minLength": 1},
                    "alternatives": {
                        "type": "array", "minItems": 2,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}

QUESTION_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "why_it_matters", "options", "allow_free_text"],
    "properties": {
        "question": {"type": "string", "minLength": 1},
        "why_it_matters": {"type": "string", "minLength": 1},
        "options": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label"],
                "properties": {"label": {"type": "string", "minLength": 1}},
            },
        },
        "allow_free_text": {"type": "boolean"},
    },
}

SIF_V2_ISSUE_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["issue_type", "subject_ref", "description", "alternatives"],
                "properties": {
                    "issue_type": {"enum": [
                        "QUERY_GRAIN", "AGGREGATION_PLACEMENT",
                        "QUERY_SELECTION_SCOPE", "QUERY_TIME_SEMANTICS",
                        "QUERY_COMPARISON_SEMANTICS", "QUERY_OUTPUT_SEMANTICS",
                        "SOURCE_POPULATION",
                    ]},
                    "subject_ref": {"type": "string", "pattern": "^ground_"},
                    "description": {"type": "string", "minLength": 1},
                    "alternatives": {
                        "type": "array", "minItems": 2,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}

A8_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "sql", "reason", "affected_intent_paths"],
    "properties": {
        "status": {"enum": ["accept", "repair", "return_to_sif"]},
        "sql": {"type": ["string", "null"]},
        "reason": {"type": "string"},
        "affected_intent_paths": {"type": "array", "items": {"type": "string"}},
    },
}


def _bif_analysis_schema(bif_schema: dict[str, Any]) -> dict[str, Any]:
    """Compose BIF and observation contracts without exposing control fields."""
    embedded_bif = copy.deepcopy(bif_schema)
    definitions = embedded_bif.pop("$defs", {})
    for metadata_key in ("$schema", "$id", "title", "description"):
        embedded_bif.pop(metadata_key, None)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["draft_bif", "issue_observations"],
        "properties": {
            "draft_bif": embedded_bif,
            "issue_observations": copy.deepcopy(ISSUE_DISCOVERY_SCHEMA),
        },
        "$defs": definitions,
    }


def _assembly_bundle_schema(
    frame_schema: dict[str, Any], *, frame_key: str, decision_ids: list[str]
) -> dict[str, Any]:
    """Embed a canonical frame; orchestration code owns the decision receipt."""
    embedded = copy.deepcopy(frame_schema)
    definitions = embedded.pop("$defs", {})
    for metadata_key in ("$schema", "$id", "title", "description"):
        embedded.pop(metadata_key, None)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [frame_key],
        "properties": {
            frame_key: embedded,
        },
        "$defs": definitions,
    }


def _bif_analysis_model_schema(bif_schema: dict[str, Any]) -> dict[str, Any]:
    """Relax only non-control uniqueness constraints for model compatibility."""
    schema = _bif_analysis_schema(bif_schema)
    _relax_model_unique_items(schema)
    return schema


def _relax_model_unique_items(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("uniqueItems", None)
        for value in node.values():
            _relax_model_unique_items(value)
    elif isinstance(node, list):
        for value in node:
            _relax_model_unique_items(value)


class AgentSuite:
    def __init__(
        self,
        client: OpenAIChatModelClient,
        *,
        bif_schema: dict[str, Any],
        sif_schema: dict[str, Any],
        sif_v2_schema: dict[str, Any] | None = None,
        query_layout_schema: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.bif_schema = bif_schema
        self.sif_schema = sif_schema
        self.sif_v2_schema = sif_v2_schema or {}
        self.query_layout_schema = query_layout_schema or {}
        self.semantic_frontend_result: dict[str, Any] | None = None
        self.semantic_frontend_service: SemanticFrontendService | None = None
        self.semantic_frontend_active = False
        self.query_layout_obligation_report: dict[str, Any] | None = None
        self.direct_sif_understanding: dict[str, Any] | None = None
        self.direct_sif_understanding_coverage: dict[str, Any] | None = None

    def a1_bif_analyze(
        self, case: dict[str, Any], schema_catalog: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Create the BIF and possible issue observations in one semantic pass."""
        if schema_catalog is not None:
            self.semantic_frontend_service = SemanticFrontendService(
                self.client, self.client.logger
            )
            self.semantic_frontend_result = self.semantic_frontend_service.run(
                case=case, schema_catalog=schema_catalog
            )
            if self.query_layout_schema:
                bif = self.a3_direct_sif_understanding(
                    case=case,
                    confirmed_cqd=self.semantic_frontend_result["confirmed_cqd"],
                )
            else:
                bif = self.a3_bif_layout_from_cqd(
                    case=case,
                    confirmed_cqd=self.semantic_frontend_result["confirmed_cqd"],
                )
            self.semantic_frontend_result["layout_structural_repairs"] = []
            if self.direct_sif_understanding_coverage is not None:
                self.semantic_frontend_result["direct_sif_understanding_coverage"] = (
                    copy.deepcopy(self.direct_sif_understanding_coverage)
                )
            if self.query_layout_obligation_report is not None:
                self.semantic_frontend_result["query_layout_obligations"] = (
                    self.query_layout_obligation_report
                )
            self.semantic_frontend_active = True
            return {"draft_bif": bif, "issue_observations": {"candidates": []}}
        output_schema = _bif_analysis_schema(self.bif_schema)
        analysis = self.client.complete_json(
            agent_id="A1",
            system_prompt=(
                "You are the Business Intent Analysis Agent. In one coherent semantic pass, create the "
                "draft Business Intent Frame and inspect objective, population, metric, calculation, grain, "
                "scope, time, comparison, and output for possible semantic choices. Use only the user's question; "
                "never use database schema, SQL, evidence, or hidden answers. Return IssueObservations only: "
                "closed issue_type, an existing BIF subject_ref, description, and at least two alternatives. "
                "Do not create IDs, materiality, ownership, category, questions, options, state, patches, ordering, "
                "or termination decisions. The Orchestrator makes every final clarification decision. Return an "
                "empty candidates array if there is no observation. Preserve uncertainty in assumptions and "
                "field_states. For a multi-valued output whose row/column presentation is not stated, set "
                "presentation=unresolved and ask whether values should be side-by-side in separate fields or "
                "one value per repeated row. Preserve every restrictive entity qualifier as an inclusion condition even "
                "when it also describes the primary entity. Preserve plural requested values as a complete "
                "collection; never silently reduce them to the first value. Preserve a complete user-stated "
                "business category in condition.value; do not replace a complete label with a substring. "
                "Each condition must represent "
                "one atomic business dimension. Separate distinct business concepts instead of combining "
                "them in one condition. Never place values from different "
                "business dimensions into one IN condition. When coordinated terms are explicitly joined by AND "
                "but their exact dimensions require schema knowledge, emit separate atomic conditions, set "
                "semantic_dimension=null, semantic_dimension_status=deferred_to_sif, grounding_requirement=schema, "
                "and preserve all_of logic. Do not ask a BIF user to choose database-owned dimensions. "
                "Preserve AND/OR explicitly in inclusion_logic and "
                "exclusion_logic. Mark plural multi-valued outputs with value_multiplicity=multiple and "
                "coverage=all_requested or all_available. Return JSON only against the "
                "supplied contract."
            ),
            payload={
                "question_id": case["question_id"],
                "db_id": case["db_id"],
                "question": case["question"],
                "required_output_schema": output_schema,
            },
            output_schema=_bif_analysis_model_schema(self.bif_schema),
            contract_name="BIF analysis bundle",
        )
        validate_contract(analysis, output_schema, "BIF analysis bundle")
        return analysis

    def a3_direct_sif_understanding(
        self, *, case: dict[str, Any], confirmed_cqd: dict[str, Any]
    ) -> dict[str, Any]:
        """Project immutable CQD meaning into SIF understanding by code."""
        understanding = compile_understanding_from_cqd(
            original_question=str(case["question"]), confirmed_cqd=confirmed_cqd,
        )
        validate_contract(
            understanding, QUERY_UNDERSTANDING_SCHEMA,
            "Deterministic Direct SIF query understanding",
        )
        coverage = validate_understanding_coverage(confirmed_cqd, understanding)
        self.direct_sif_understanding = copy.deepcopy(understanding)
        self.direct_sif_understanding_coverage = copy.deepcopy(coverage)
        return understanding

    def a4_direct_sif_grounding_bundle(
        self,
        *,
        case: dict[str, Any],
        confirmed_cqd: dict[str, Any],
        understanding: dict[str, Any],
        schema_catalog: dict[str, Any],
        technical_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Materialize catalog-bounded grounding and SQL roles by code."""
        return materialize_grounding_bundle(
            original_question=str(case["question"]),
            benchmark_evidence=case.get("evidence"),
            confirmed_cqd=confirmed_cqd,
            understanding=understanding,
            schema_catalog=schema_catalog,
            technical_feedback=technical_feedback,
        )

    def a3_bif_layout_from_cqd(
        self, *, case: dict[str, Any], confirmed_cqd: dict[str, Any]
    ) -> dict[str, Any]:
        bif = self.client.complete_json(
            agent_id="A3_LAYOUT",
            system_prompt=(
                "You are the existing revised BIF Layout constructor. Convert the supplied "
                "Confirmed CQD into exactly the supplied Business Intent Frame schema. The CQD "
                "is immutable and already contains every resolved business meaning. Preserve every "
                "population, predicate atom and boolean relation, expression, aggregation, grouping, "
                "selection, time constraint, comparison, scope, and requested output. Do not ask a "
                "question, reinterpret the request, add a filter/output, or introduce any table, "
                "column, join, stored code, or SQL implementation detail. Add only structural IDs, "
                "required empty containers, and explicit not_applicable values needed by the existing "
                "BIF compatibility view. Omit the optional slot_layout property: orchestration code builds "
                "that canonical, lossless view directly from Confirmed CQD after this call. Never infer a "
                "missing output presentation from list/scalar cardinality. Set readiness.state to ready only "
                "when the full CQD has been "
                "represented. Return only the BIF JSON."
            ),
            payload={
                "original_question": case["question"],
                "confirmed_cqd": confirmed_cqd,
                "confirmed_cqd_hash": confirmed_cqd.get("traceability", {}).get(
                    "state_hash"
                ),
                "required_bif_schema": self.bif_schema,
            },
            output_schema=self.bif_schema,
            contract_name="Confirmed CQD to existing BIF Layout",
            max_tokens_override=8192,
        )
        validate_contract(bif, self.bif_schema, "Confirmed CQD BIF Layout")
        return bif

    def a3_query_layout_from_cqd(
        self, *, case: dict[str, Any], confirmed_cqd: dict[str, Any]
    ) -> dict[str, Any]:
        """Finalize BIF through deterministic IR plus constrained monotonic patches."""
        def propose(
            working_layout: dict[str, Any], gaps: list[dict[str, Any]], round_number: int
        ) -> dict[str, Any]:
            return self.client.complete_json(
                agent_id="A3_LAYOUT_GAP",
                system_prompt=(
                    "Complete only the supplied open CQD coverage gaps. The Orchestrator owns and "
                    "finalizes the layout; never return or rewrite the whole layout. Return exactly one "
                    "patch per supplied cqd_ref and cite only evidence_ids listed on that gap. You may "
                    "generate the minimum SQL-semantic fragments needed to preserve that evidence, but "
                    "you must not invent, broaden, narrow, or reinterpret meaning. Use add when the cited "
                    "CQD or question evidence directly entails the fragments. Reuse evidence wording for "
                    "semantic roles and literal values; do not introduce synonyms or unstated categories. "
                    "Use keep only when the gap explicitly has keep_allowed=true and the "
                    "existing target slots preserve the complete CQD payload; a source label alone does "
                    "not preserve a restrictive population qualifier. Use clarification_required when "
                    "the evidence permits multiple user meanings or does not determine a fragment. Use "
                    "replace only to request CQD clarification; layout replacement is never applied. The "
                    "Orchestrator sends that change through E2 and recompiles the revised CQD. "
                    "Do not add physical tables, columns, joins, stored codes, or other schema facts. "
                    "Every generated field and predicate must cite an exact semantic_ref at or below its "
                    "cqd_ref. A predicate semantic_ref must equal its field semantic_ref. "
                    "Add only fragments that are actually missing. Never repeat an existing source, field, "
                    "predicate, select, group_by, order_by, or limit. target_slot_ids identify existing "
                    "slots being referenced; they are not instructions to recreate those slots. If a gap "
                    "contains last_rejection_reason and last_rejected_patch, correct that exact violation "
                    "in this round while preserving the same CQD meaning. "
                    "If a gap contains completed_components and remaining_components, preserve every "
                    "completed component exactly and correct only the remaining components. A predicate "
                    "may be partially complete: its field, operator, value, semantic_ref, and role are "
                    "tracked independently. Return the containing object required by the schema, but do "
                    "not change components that are already complete. "
                    "Added fields must reference an existing or added source; predicates and clauses must "
                    "reference an existing or added field. Return only the gap patch JSON."
                ),
                payload={
                    "original_question": case["question"],
                    "confirmed_cqd": confirmed_cqd,
                    "authoritative_working_layout_read_only": working_layout,
                    "open_coverage_gaps": gaps,
                    "round": round_number,
                    "required_patch_schema": BIF_LAYOUT_GAP_PATCH_SCHEMA,
                },
                output_schema=BIF_LAYOUT_GAP_PATCH_SCHEMA,
                contract_name="CQD to BIF monotonic gap patch",
                max_tokens_override=4096,
            )

        def clarify_layout_gaps(
            current_cqd: dict[str, Any], gaps: list[dict[str, Any]]
        ) -> dict[str, Any]:
            if self.semantic_frontend_service is None:
                return current_cqd
            revised = self.semantic_frontend_service.resolve_layout_gaps(
                confirmed_cqd=current_cqd, gaps=gaps
            )
            confirmed_cqd.clear()
            confirmed_cqd.update(copy.deepcopy(revised))
            return revised

        layout, report = BIFLayoutCompletionOrchestrator().run(
            original_question=str(case["question"]),
            confirmed_cqd=confirmed_cqd,
            propose=propose,
            clarify=clarify_layout_gaps if self.semantic_frontend_service else None,
        )
        report["obligations"] = compile_layout_obligations(
            report.get("confirmed_cqd", confirmed_cqd)
        )
        self.query_layout_obligation_report = report
        if self.semantic_frontend_result is not None:
            revised_cqd = report.get("confirmed_cqd", confirmed_cqd)
            self.semantic_frontend_result["confirmed_cqd"] = copy.deepcopy(revised_cqd)
            self.semantic_frontend_result["confirmed_cqd_hash"] = (
                revised_cqd.get("traceability", {}).get("state_hash")
            )
            self.semantic_frontend_result["confirmed_cqd_text"] = str(
                revised_cqd.get("rendered_text") or ""
            )
            self.semantic_frontend_result["query_layout_obligations"] = copy.deepcopy(
                report
            )
        return layout

    def a4_query_layout_grounding_proposal(
        self,
        case: dict[str, Any],
        bif_layout: dict[str, Any],
        sif_overlay: dict[str, Any],
        schema_catalog: dict[str, Any],
        *,
        request_id: str,
        technical_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Fill the same flat layout with physical table and column links."""
        proposal_schema = grounding_proposal_schema(self.query_layout_schema)
        proposal = self.client.complete_json(
            agent_id="A4",
            system_prompt=(
                "Link the supplied Simple Query Layout 4.0 to the SQLite catalog. Do not return a BIF or "
                "SIF layout. Return only flat physical links for sources, fields, predicates and joins. "
                "Return a source link for every existing source ID and a field link for every existing "
                "field ID. Fill each source's table once, then link each field to a source ID and column. "
                "Return predicate operator/value links for every existing predicate ID. "
                "Use expression only for a real derived calculation. Apply stored-code conversion by "
                "updating the predicate value. Add only physically required joins. Do not introduce any "
                "grounding wrapper, binding list, evidence, validation metadata, technical node or new "
                "business filter/output. The Orchestrator applies these links to the authoritative layout."
                " If technical_feedback is present, correct those grounding or compilation failures; "
                "do not repeat the rejected physical links."
            ),
            payload={
                "question_provenance": {
                    "question_id": case["question_id"], "db_id": case["db_id"]
                },
                "bif_query_intent_layout": bif_layout,
                "sif_grounding_overlay": sif_overlay,
                "schema_catalog": schema_catalog,
                "request_id": request_id,
                "technical_feedback": technical_feedback or [],
                "required_sif_layout_schema": proposal_schema,
            },
            output_schema=proposal_schema,
            contract_name="Simple Query Layout 4.0 physical link proposal",
            max_tokens_override=8192,
        )
        validate_contract(
            proposal, proposal_schema, "Simple Query Layout 4.0 physical link proposal"
        )
        return proposal

    def a2_bif_contract_detect(
        self,
        *,
        original_question: str,
        bif: dict[str, Any],
        contract: dict[str, Any],
        allowed_paths: list[str],
        previous_output: dict[str, Any] | None = None,
        validation_error: str | None = None,
        repair_attempt: int = 0,
    ) -> dict[str, Any]:
        """Evaluate exactly one registered BIF clarification contract."""
        return self.client.complete_json(
            agent_id=f"A2_CONTRACT_{contract['family']}",
            system_prompt=(
                "You are one schema-free BIF clarification contract detector. Evaluate exactly "
                "the supplied contract and every detection rule against the original question and "
                "Draft BIF. Do not rely on another detector. Use no database schema, table, column, "
                "SQL, evidence, Gold SQL, or hidden answer. Return detected only when at least two "
                "plausible user-owned business meanings remain and they materially change the request. "
                "Do not invent ambiguity from ordinary plurality, words such as list/all/each, or an "
                "explicitly stated output. A physical dimension, code, column, join, or stored-value "
                "choice is not a user ambiguity: mark it safe with disposition deferred_to_sif. "
                "For detected findings, source_span must be text from the original question, "
                "subject_ref must be copied exactly from allowed_paths, and alternatives must be "
                "mutually exclusive business meanings. For safe or not_applicable, findings must be "
                "empty. Always provide a concrete reason. Return JSON only against the contract."
            ),
            payload={
                "original_question": original_question,
                "draft_bif": bif,
                "contract": contract,
                "allowed_paths": allowed_paths,
                "previous_output": previous_output,
                "validation_error": validation_error,
                "repair_attempt": repair_attempt,
                "required_output_schema": BIF_CONTRACT_ASSESSMENT_SCHEMA,
            },
            output_schema=BIF_CONTRACT_ASSESSMENT_SCHEMA,
            contract_name=f"BIF contract assessment {contract['family']}",
        )

    def a2_bif_discover(
        self,
        case: dict[str, Any],
        draft_bif: dict[str, Any],
        decision_ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A2_DISCOVERY",
            system_prompt=(
                "You are a BIF issue observation sub-function. Report possible unresolved semantic choices "
                "needed to understand the user's business objective, population, calculation, or output. "
                "Choose only an issue_type from the supplied closed contract, point to a subject_ref already "
                "present in the draft, and describe at least two distinct semantic alternatives. Do not create "
                "IDs, materiality, ownership, category, questions, state, ordering, paths beyond subject_ref, or "
                "termination decisions. The orchestrator validates and decides everything. Treat answered_decisions "
                "as final. A condition marked semantic_dimension_status=deferred_to_sif is intentionally resolved "
                "later from schema and must not be reported as a BIF ambiguity. Use system issue types for database availability, stored value quality, schema, table, "
                "column, join, and SQL concerns. Return an empty candidates array when there is no observation."
            ),
            payload={
                "question": case["question"],
                "draft_bif": draft_bif,
                "answered_decisions": decision_ledger,
                "required_output_schema": ISSUE_DISCOVERY_SCHEMA,
            },
            output_schema=ISSUE_DISCOVERY_SCHEMA,
            contract_name="BIF issue discovery",
        )

    def a2_bif_question(
        self,
        case: dict[str, Any],
        draft_bif: dict[str, Any],
        decision: dict[str, Any],
        decision_ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A2_QUESTION",
            system_prompt=(
                "You are a BIF question-writing sub-function. Express exactly the supplied DecisionSpec as "
                "one concise question a non-technical user can answer. Do not discover another issue, create "
                "IDs, change paths, update state, or decide whether clarification is complete. Do not mention "
                "SQL, schema, tables, columns, or hidden evidence. Provide mutually exclusive business-language "
                "option labels and allow free text only when the listed choices may be incomplete. JSON only."
            ),
            payload={
                "question": case["question"],
                "draft_bif": draft_bif,
                "decision_spec": decision,
                "answered_decisions": decision_ledger,
                "required_output_schema": QUESTION_DRAFT_SCHEMA,
            },
            output_schema=QUESTION_DRAFT_SCHEMA,
            contract_name="BIF question draft",
        )

    def a3_bif_assemble(
        self,
        case: dict[str, Any],
        draft_bif: dict[str, Any],
        decision_ledger: list[dict[str, Any]],
        canonical_intent_state: dict[str, Any] | None = None,
        materialization_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        decision_ids = [str(item["decision_id"]) for item in decision_ledger]
        if self.semantic_frontend_active:
            return {
                "confirmed_bif": copy.deepcopy(draft_bif),
                "applied_decision_ids": decision_ids,
            }
        output_schema = _assembly_bundle_schema(
            self.bif_schema,
            frame_key="confirmed_bif",
            decision_ids=decision_ids,
        )
        return self.client.complete_json(
            agent_id="A3",
            system_prompt=(
                "You are A3, the one-shot Canonical BIF Assembler. Clarification is complete. Build one "
                "complete BIF from the original question, the untouched draft, explicit CanonicalIntentState "
                "assertions (including absence/no-additional-constraint effects), and the entire ordered decision "
                "ledger. This is one immutable materialization checkpoint; a later revision may create another "
                "checkpoint, but you must never patch an earlier Frame. Apply every answered decision together so dependent "
                "population conditions, logic groups, metrics, grain, scope, time, comparisons, and output shape "
                "remain internally consistent. Never use database schema, SQL, evidence, or hidden answers. Do not "
                "invent operational validity criteria. Atomic conditions, their IDs, AND/OR topology, requested "
                "output inventory, and deferred_to_sif states in the draft are authoritative: never merge, delete, "
                "rename, or expand them. Do not add outputs, ordering, DISTINCT, validation filters, or retrieval "
                "calculation steps absent from the draft. Preserve all decisions even when several affect the same "
                "area. Do not decide or report which decisions were applied; orchestration code owns that "
                "receipt. When materialization_feedback is supplied, the previous provisional Frame failed a deterministic "
                "compliance check: correct every listed structural mismatch without asking the user again or changing the "
                "confirmed choice. Return the assembly bundle only."
            ),
            payload={
                "question": case["question"],
                "draft_bif": draft_bif,
                "decision_ledger": decision_ledger,
                "canonical_intent_state": canonical_intent_state or {},
                "materialization_feedback": materialization_feedback or [],
                "required_output_schema": output_schema,
            },
            output_schema=output_schema,
            contract_name="One-shot BIF assembly",
            max_tokens_override=8192,
        )

    def e2_user(
        self,
        *,
        mode: str,
        case: dict[str, Any],
        clarification: dict[str, Any],
        confirmed_bif_summary: dict[str, Any] | None = None,
        oracle_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_contract(
            clarification, E2_INTERACTION_SCHEMA, "E2 interaction envelope"
        )
        oracle_context = oracle_context or {}
        response = self.client.complete_json(
            agent_id="E2",
            system_prompt=(
                "You are E2_SPEC_V1: a cooperative non-technical business user who does not know SQL or "
                "database structure. Use hidden gold SQL only to infer the user's intended answer to the "
                "current clarification. If restricted oracle_context contains oracle_target_option_ids, they "
                "are authoritative: select exactly those option IDs. Answer only the current question. Select a matching option; if none "
                "matches, answer briefly in business language; if only a physical database expert could "
                "answer, use cannot_answer. Never output SQL, table/column/join/function names, JSON Patch, "
                "frames, explanations, or unsolicited gold facts. Return UserResponse JSON only."
            ),
            payload={
                "mode": mode,
                "original_question": case["question"],
                "clarification_request": clarification["clarification_request"],
                "confirmed_bif_user_summary": confirmed_bif_summary,
                "hidden_gold_sql": case["SQL"],
                "oracle_context": oracle_context,
                "output_schema": USER_RESPONSE_SCHEMA,
            },
            output_schema=USER_RESPONSE_SCHEMA,
            contract_name="E2 UserResponse",
            simulated_user=True,
            restricted_request=True,
        )
        request_id = str(clarification["clarification_request"]["request_id"])
        corrected, changed = enforce_oracle_response(
            response,
            mode=mode,
            request_id=request_id,
            oracle_context=oracle_context,
        )
        if changed:
            safe_request_id = "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in request_id
            )
            self.client.logger.write_json(
                f"restricted/e2_oracle/corrections/{mode.lower()}_{safe_request_id}.json",
                {
                    "raw_model_response": response,
                    "normalized_oracle_response": corrected,
                    "oracle_context": oracle_context,
                },
            )
            self.client.logger.append_event(
                "E2_ORACLE_RESPONSE_NORMALIZED",
                {
                    "mode": mode,
                    "request_id": request_id,
                    "raw_selected_option_ids": response.get("selected_option_ids", []),
                    "oracle_target_option_ids": oracle_context.get(
                        "oracle_target_option_ids", []
                    ),
                },
            )
        return corrected

    def a4_sif_draft(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        schema_catalog: dict[str, Any],
        bif_source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A4",
            system_prompt=(
                "You are A4, the SIF Draft Constructor. Ground the Confirmed BIF in the supplied SQLite "
                "schema. Represent sources, joins, predicates, aggregations, grain, scope, ranking, output, "
                "and query-block dependencies. Every material node must include BIF intent_paths. Do not "
                "omit intent_paths on parent projection objects even when their nested expression already has "
                "intent_paths. JSON Pointer array indexes are zero-based: the first metric is /metrics/0 and "
                "the first inclusion condition is /target_population/inclusion_conditions/0. Do not "
                "see or infer from gold SQL. Use column descriptions, value descriptions, foreign keys, and "
                "question_value_matches as grounding evidence. Prefer an exact database value match over a "
                "superficially similar column name. If matching stored values overlap, preserve the longest "
                "complete business phrase from the question. Never put a natural-language literal into a coded column "
                "unless its value description maps that literal to the stored code. Distinct business "
                "classifications must remain separate predicates when their documented codes belong to "
                "different columns. Every distinct all_of BIF condition_id must remain a separate predicate, "
                "including conditions whose semantic_dimension is deferred_to_sif; never merge them merely "
                "because their dimensions are null or similarly worded. Never compress low/high boundary "
                "conditions into one span field. "
                "For a plural requested attribute, project every useful schema field that "
                "represents a distinct requested value, excluding fields documented as not useful. When its BIF "
                "presentation is separate_columns, keep those sibling fields side-by-side in the same projection; "
                "do not reshape them with UNION. Never add "
                "an unrequested status or activity filter. Return SIF JSON only."
            ),
            payload={
                "question_provenance": {"question_id": case["question_id"], "db_id": case["db_id"]},
                "confirmed_bif": bif,
                "confirmed_bif_source": bif_source_metadata or {},
                "schema_catalog": schema_catalog,
                "required_sif_schema": self.sif_schema,
            },
            output_schema=self.sif_schema,
            contract_name="Draft SIF",
        )

    def a4_sif_v2_grounding_proposal(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif_scaffold: dict[str, Any],
        schema_catalog: dict[str, Any],
        *,
        request_id: str,
    ) -> dict[str, Any]:
        """Propose physical bindings without constructing SQL topology."""
        return self.client.complete_json(
            agent_id="A4",
            system_prompt=(
                "You are A4, a SIF 2.0 physical grounding proposer. The Confirmed BIF owns every "
                "business condition, logical relation, metric, calculation, grain, comparison, and output. "
                "Do not reinterpret or restate that logic and do not generate query blocks, WHERE trees, "
                "projections, GROUP BY, SQL, or a complete SIF. Propose table/column/code/datatype bindings "
                "only for unresolved grounding_id values already present in the supplied scaffold. Never "
                "propose or alter an implementation descriptor; those are derived deterministically from "
                "the Confirmed BIF. Keep independent "
                "condition slots independent even when their labels look similar or their BIF semantic_dimension "
                "is deferred_to_sif. Resolve each deferred condition from schema descriptions and code mappings. "
                "One batch call may propose "
                "many slots. Leave bindings empty and explain unresolved_reason when the schema does not "
                "support a confident mapping. For every constraint_id exposed by a grounding slot, return "
                "exactly one constraint result. Mark it grounded only when the proposed physical binding "
                "actually implements that specific constraint and cite concrete evidence. Otherwise use "
                "clarification_required or unsupported with a reason. Do not treat finding any column as "
                "proof that presentation, multiplicity, validity, tie, null, or set constraints are satisfied. "
                "Source and join additions must carry BIF origin refs. Echo "
                "request_id and base_frame_hash exactly. JSON only."
            ),
            payload={
                "question_provenance": {
                    "question_id": case["question_id"], "db_id": case["db_id"],
                },
                "confirmed_bif": bif,
                "sif_v2_scaffold": sif_scaffold,
                "request_id": request_id,
                "base_frame_hash": frame_hash(sif_scaffold),
                "schema_catalog": schema_catalog,
                "required_proposal_schema": SIF_V2_GROUNDING_PROPOSAL_SCHEMA,
            },
            output_schema=SIF_V2_GROUNDING_PROPOSAL_SCHEMA,
            contract_name="SIF 2.0 grounding proposal",
        )

    def a5_sif_discover(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif: dict[str, Any],
        decision_ledger: list[dict[str, Any]],
        source_ambiguities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A5_DISCOVERY",
            system_prompt=(
                "You are a SIF issue observation sub-function. Report possible new semantic choices needed "
                "to bridge the confirmed business meaning to query semantics, especially record population or "
                "aggregation/grain meaning. Choose only an issue_type from the closed contract, a subject_ref "
                "already present in the supplied frames, and at least two distinct semantic alternatives. Do not "
                "create IDs, materiality, ownership, category, questions, state, ordering, or termination decisions. "
                "The orchestrator validates and decides everything. Physical mapping, joins, casts, dialect, data "
                "availability, and data quality must use system issue types. When schema inspection exposes "
                "multiple executable column, stored-code, operator, or predicate-structure mappings that require "
                "a technical clarification, use TECHNICAL_GROUNDING; never relabel such a gap as a BIF issue. "
                "Deterministic source ambiguities are supplied separately and must not be duplicated. Return "
                "an empty candidates array when no new user decision exists. JSON only."
            ),
            payload={
                "question": case["question"],
                "confirmed_bif": bif,
                "draft_sif": sif,
                "answered_decisions": decision_ledger,
                "deterministic_source_ambiguities": source_ambiguities or [],
                "required_output_schema": ISSUE_DISCOVERY_SCHEMA,
            },
            output_schema=ISSUE_DISCOVERY_SCHEMA,
            contract_name="SIF issue discovery",
        )

    def a5_sif_v2_discover(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif: dict[str, Any],
        unresolved_grounding_ids: list[str],
        decision_ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Observe only user-answerable choices blocking unresolved SIF v2 slots."""
        return self.client.complete_json(
            agent_id="A5_DISCOVERY",
            system_prompt=(
                "You are a SIF 2.0 issue-observation sub-function. Inspect only the supplied "
                "unresolved grounding IDs. Report a candidate only when a non-technical user can "
                "choose between two or more materially different business meanings that affect "
                "physical grounding. subject_ref must be exactly one supplied grounding ID. Do not "
                "ask users to choose tables, columns, joins, SQL syntax, casts, or database codes. "
                "Do not create questions, decision IDs, queue state, priorities, or termination "
                "decisions. Purely technical or unavailable mappings are system-owned and must not "
                "be returned. Return an empty candidates array when no user-answerable choice exists."
            ),
            payload={
                "question": case["question"],
                "confirmed_bif": bif,
                "current_sif_v2": sif,
                "unresolved_grounding_ids": unresolved_grounding_ids,
                "answered_decisions": decision_ledger,
                "required_output_schema": SIF_V2_ISSUE_DISCOVERY_SCHEMA,
            },
            output_schema=SIF_V2_ISSUE_DISCOVERY_SCHEMA,
            contract_name="SIF 2.0 issue discovery",
        )

    def a5_sif_question(
        self,
        case: dict[str, Any],
        bif: dict[str, Any],
        sif: dict[str, Any],
        decision: dict[str, Any],
        decision_ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A5_QUESTION",
            system_prompt=(
                "You are a SIF question-writing sub-function. Express exactly the supplied DecisionSpec as "
                "one question a non-technical user can answer. Do not discover issues, create IDs, change "
                "paths, update state, or decide termination. Describe record definitions and query meaning in "
                "business language; never expose table, column, SQL, schema, or physical identifiers. Provide "
                "mutually exclusive option labels. JSON only."
            ),
            payload={
                "question": case["question"],
                "confirmed_bif": bif,
                "draft_sif": sif,
                "decision_spec": decision,
                "answered_decisions": decision_ledger,
                "required_output_schema": QUESTION_DRAFT_SCHEMA,
            },
            output_schema=QUESTION_DRAFT_SCHEMA,
            contract_name="SIF question draft",
        )

    def a6_sif_assemble(
        self,
        bif: dict[str, Any],
        draft_sif: dict[str, Any],
        decision_ledger: list[dict[str, Any]],
        schema_catalog: dict[str, Any],
        source_ambiguities: list[dict[str, Any]] | None = None,
        canonical_intent_state: dict[str, Any] | None = None,
        materialization_feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        decision_ids = [str(item["decision_id"]) for item in decision_ledger]
        output_schema = _assembly_bundle_schema(
            self.sif_schema,
            frame_key="confirmed_sif",
            decision_ids=decision_ids,
        )
        return self.client.complete_json(
            agent_id="A6",
            system_prompt=(
                "You are A6, the one-shot Canonical SIF Assembler. SIF clarification is complete. Build one "
                "complete schema-grounded SIF from the Confirmed BIF, untouched SIF draft, explicit canonical "
                "query-intent assertions, entire ordered decision "
                "ledger, reviewed source candidates, and schema catalog. This is one immutable SIF materialization "
                "checkpoint, never an incremental patch. Apply every user decision together and keep sources, joins, predicates, query "
                "blocks, aggregation, grain, ranking, and output mutually consistent. Use only documented tables, "
                "columns, values, and foreign keys. Never change BIF meaning or invent an unrequested filter. "
                "Do not decide or report which decisions were applied; orchestration code owns that receipt. "
                "Physical grounding warnings may remain explicit for the later system repair phase. "
                "When materialization_feedback is supplied, correct every listed confirmed-decision mismatch "
                "without asking the user again or changing the confirmed business/query choice. "
                "Return the assembly bundle only."
            ),
            payload={
                "confirmed_bif": bif,
                "draft_sif": draft_sif,
                "decision_ledger": decision_ledger,
                "canonical_query_intent_state": canonical_intent_state or {},
                "materialization_feedback": materialization_feedback or [],
                "reviewed_source_ambiguities": source_ambiguities or [],
                "schema_catalog": schema_catalog,
                "required_output_schema": output_schema,
            },
            output_schema=output_schema,
            contract_name="One-shot SIF assembly",
            max_tokens_override=8192,
        )

    def a6_sif_v2_grounding_proposal(
        self,
        bif: dict[str, Any],
        current_sif: dict[str, Any],
        schema_catalog: dict[str, Any],
        *,
        request_id: str,
        target_grounding_ids: list[str],
        decision_ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Propose bindings only for Orchestrator-selected unresolved slots."""
        return self.client.complete_json(
            agent_id="A6",
            system_prompt=(
                "You are A6, a constrained SIF 2.0 grounding sub-function. The Orchestrator has selected "
                "specific unresolved grounding IDs after clarification. Propose physical bindings only for "
                "those IDs. Never return or rewrite the whole SIF; never change semantic_ref, condition_ref, "
                "BIF logic, output fields, or an already resolved slot. Use the decision ledger only to choose "
                "among physical candidates for the selected slots. Return a batch slot proposal and echo the "
                "request and frame hash exactly. Return exactly one result for every constraint_id in each "
                "selected slot, with concrete evidence; never mark all constraints grounded merely because "
                "one column was found. JSON only."
            ),
            payload={
                "confirmed_bif": bif,
                "current_sif_v2": current_sif,
                "target_grounding_ids": target_grounding_ids,
                "decision_ledger": decision_ledger,
                "request_id": request_id,
                "base_frame_hash": frame_hash(current_sif),
                "schema_catalog": schema_catalog,
                "required_proposal_schema": SIF_V2_GROUNDING_PROPOSAL_SCHEMA,
            },
            output_schema=SIF_V2_GROUNDING_PROPOSAL_SCHEMA,
            contract_name="SIF 2.0 constrained grounding proposal",
        )

    def a7_generate_sql(self, sif: dict[str, Any], schema_catalog: dict[str, Any]) -> str:
        return self.client.complete_text(
            agent_id="A7",
            system_prompt=(
                "You are A7, the SQL Generation Agent. Compile the Confirmed SIF into one executable, "
                "read-only SQLite SELECT statement. The SIF is the sole semantic source. Use only supplied "
                "identifiers already present in the SIF. Return SQL only with no markdown or explanation."
            ),
            payload={"confirmed_sif": sif},
        )

    def a6_grounding_patch(
        self,
        bif: dict[str, Any],
        sif: dict[str, Any],
        schema_catalog: dict[str, Any],
        grounding_warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A6",
            system_prompt=(
                "You are A6 planning a system-owned physical grounding patch. The user intent is already "
                "confirmed, so do not ask the user and do not change business meaning. Correct every supplied "
                "grounding warning, including predicates whose literals do not exist and projected/filter columns "
                "that do not exist in their selected source. Use column/value descriptions, "
                "question_value_matches and foreign keys. Prefer a source where related filter concepts are "
                "jointly and explicitly represented, then join through declared keys when a requested output "
                "attribute exists in a related source. Never treat an unknown double-quoted identifier as a "
                "SQLite string literal. "
                "An output_presentation_mismatch warning requires preserving separate_columns as sibling "
                "projections in the same result row and removing a UNION that turns them into repeated rows. "
                "Do not put natural-language text into coded fields. A missing_bif_intent_path warning requires "
                "adding the omitted BIF condition or output without changing its meaning. An untraced_predicate "
                "warning requires removing the unsupported predicate unless an existing BIF path explicitly "
                "justifies it. A non_exact_question_value_mapping warning requires the longest documented exact value "
                "present in the user's wording. A merged_independent_bif_conditions warning requires one separate "
                "predicate per supplied BIF condition and forbids replacing low/high boundary conditions with one "
                "span field. Never merge distinct documented classification dimensions into one IN predicate. "
                "An unsupported_sif_feature warning means the current SIF cannot be compiled by the deterministic "
                "compiler. Rewrite only the unsupported representation into equivalent supported query blocks and "
                "expressions; do not change its business meaning. "
                "Return only coherent section replacement operations. Replace the smallest complete query block "
                "that owns the warning instead of emitting leaf-level source, predicate, projection, expression, "
                "or limit patches. Traceability and unresolved-grounding arrays may be replaced as complete "
                "sections. Never return the complete SIF and never patch status, "
                "frame identifiers, source_frame_id, or database metadata. Echo the supplied request_id, gap_id, "
                "and base_frame_hash exactly."
            ),
            payload={
                "confirmed_bif": bif,
                "current_sif": sif,
                "request_id": "system_grounding",
                "gap_id": "system_grounding",
                "base_frame_hash": frame_hash(sif),
                "grounding_warnings": grounding_warnings,
                "schema_catalog": schema_catalog,
                "required_change_set_schema": SIF_SECTION_CHANGE_SET_SCHEMA,
            },
            output_schema=SIF_SECTION_CHANGE_SET_SCHEMA,
            contract_name="SIF grounding section change set",
        )

    def a8_verify_or_repair(
        self,
        *,
        sif: dict[str, Any],
        schema_catalog: dict[str, Any],
        sql: str,
        execution_error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.client.complete_json(
            agent_id="A8",
            system_prompt=(
                "You are A8, the SQL Verification, Preflight Execution and Constrained Repair Agent. Check "
                "the SQL against the Confirmed SIF and the supplied execution error. You may repair syntax, aliases, quoting, casts, "
                "dialect functions, or a SIF node accidentally omitted by generation. You must not add a new "
                "business filter, metric, scope, grain, or comparison. status=accept means sql=null; "
                "status=repair requires a complete repaired read-only SQLite SQL; status=return_to_sif is only "
                "for a missing material business decision. Return JSON only."
            ),
            payload={
                "confirmed_sif": sif,
                "candidate_sql": sql,
                "preflight_execution_error": execution_error,
                "output_schema": A8_VERDICT_SCHEMA,
            },
            output_schema=A8_VERDICT_SCHEMA,
            contract_name="A8 verdict",
        )
