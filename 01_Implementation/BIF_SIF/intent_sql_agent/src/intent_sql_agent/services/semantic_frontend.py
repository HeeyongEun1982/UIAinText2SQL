from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient
from intent_sql_agent.services.artifact_logger import ArtifactLogger


INTENT_ANALYZER_SYSTEM = r"""You are an expert natural-language query understanding agent.
Your task is to analyze the user question and domain evidence in PURE NATURAL LANGUAGE without requiring any physical database schema or SQL syntax.

Extract the following in concise natural language:
1. "goal": Clear, concise 1-sentence statement of what the question asks to find or compute.
2. "target_entity": The main entity or subject (e.g. "school campuses", "customer transactions", "hospital clinics").
3. "requested_outputs" (CRITICAL): List of specific attributes, columns, or entity names explicitly asked for in the user question (e.g. ["payment_method"], ["primary_email", "secondary_email"]).
   - For "What is the most common X?" or "Which X has highest frequency?": requested_outputs is strictly ["X"].
   - NEVER include unrequested helper metrics like "count", "frequency", "percentage", or "amount" unless the question explicitly asks "indicate the count", "how many", or "what percentage".
4. "explicit_filters": List of explicit filters or constraints stated directly in the question or evidence (e.g. ["city is Adelanto", "county is San Bernardino", "DOC is 54 and SOC is 62", "registered between 2009 and 2010"]).
5. "ambiguities": List of genuine, material business ambiguities that require clarification from the user. If the question and evidence are clear, return an empty list [].

Return ONLY a JSON object:
{
  "goal": "...",
  "target_entity": "...",
  "requested_outputs": ["..."],
  "explicit_filters": ["..."],
  "ambiguities": []
}
"""

CLARIFICATION_WRITER_SYSTEM = r"""You are a natural language clarification question writer.
An ambiguity was identified in the user's question.
Formulate a concise, clear multiple-choice question in natural language offering 2 to 3 distinct options so the user can easily clarify their intent.

Return ONLY a JSON object:
{
  "question_text": "...",
  "options": [
    {"option_id": "A", "description": "..."},
    {"option_id": "B", "description": "..."}
  ]
}
"""

ORACLE_E2_SYSTEM = r"""You are an oracle user simulator (E2) representing the true user intent for a benchmark question.
You have access to the original question, evidence, and ground truth Gold SQL.
When asked a clarification question, select the option that matches the Gold SQL's semantics and provide a concise, direct natural language answer.

Return ONLY a JSON object:
{
  "selected_option_id": "A",
  "answer_text": "..."
}
"""


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class SemanticFrontendService:
    """Pure Natural Language Intent Understanding & Clarification Pipeline."""

    def __init__(
        self,
        client: OpenAIChatModelClient,
        logger: ArtifactLogger,
        *,
        max_rounds: int = 7,
        fast_path: bool = False,
    ) -> None:
        self.client = client
        self.logger = logger
        self.max_rounds = max_rounds
        self.fast_path = fast_path

    def run(
        self,
        *,
        case: dict[str, Any],
        schema_catalog: dict[str, Any],
    ) -> dict[str, Any]:
        question_id = str(case["question_id"])
        question = str(case["question"])
        evidence = str(case.get("evidence") or "").strip() or None
        gold_sql = str(case.get("SQL") or case.get("sql") or "")

        # 1. Natural Language Intent Analysis
        analysis_payload = {
            "question": question,
            "evidence": evidence or "None",
        }
        raw_intent = self.client.complete_json(
            agent_id="Natural_Language_Intent_Analyzer",
            system_prompt=INTENT_ANALYZER_SYSTEM,
            payload=analysis_payload,
        )

        goal = str(raw_intent.get("goal") or question)
        target_entity = str(raw_intent.get("target_entity") or "entity")
        requested_outputs = raw_intent.get("requested_outputs") or [question]
        if isinstance(requested_outputs, str):
            requested_outputs = [requested_outputs]
        explicit_filters = raw_intent.get("explicit_filters") or []
        if isinstance(explicit_filters, str):
            explicit_filters = [explicit_filters]
        ambiguities = raw_intent.get("ambiguities") or []
        if isinstance(ambiguities, str):
            ambiguities = [ambiguities]

        # Clean unrequested helper words from requested_outputs if question does not ask for counts/percentages
        lower_q = question.lower()
        has_count_kw = any(w in lower_q for w in ["how many", "count", "amount", "number of", "percentage", "ratio", "proportion", "along with", "as well as", "indicate the"])
        if not has_count_kw and len(requested_outputs) > 1:
            filtered_outs = [
                o for o in requested_outputs
                if not any(k in str(o).lower() for k in ["count", "percentage", "frequency", "cnt", "total", "pct", "share"])
            ]
            if filtered_outs:
                requested_outputs = filtered_outs

        self.logger.write_json(
            "semantic_frontend/01_source_analysis/natural_intent.json",
            {
                "question_id": question_id,
                "goal": goal,
                "target_entity": target_entity,
                "requested_outputs": requested_outputs,
                "explicit_filters": explicit_filters,
                "ambiguities": ambiguities,
            },
        )

        # 2. Interactive Clarification Turns in Pure Natural Language
        clarification_turns: list[dict[str, Any]] = []
        turn_count = 0

        for amb in ambiguities:
            if turn_count >= self.max_rounds:
                break
            turn_count += 1
            turn_dir = f"semantic_frontend/05_clarification/turn_{turn_count:02d}"

            # Write Question
            q_payload = {
                "question": question,
                "evidence": evidence or "None",
                "ambiguity_to_clarify": amb,
                "current_goal": goal,
            }
            q_res = self.client.complete_json(
                agent_id="Natural_Clarification_Question_Writer",
                system_prompt=CLARIFICATION_WRITER_SYSTEM,
                payload=q_payload,
            )
            question_text = str(q_res.get("question_text") or f"Please clarify: {amb}")
            options = q_res.get("options") or []

            # Oracle E2 Answer
            e2_payload = {
                "original_question": question,
                "evidence": evidence or "None",
                "gold_sql": gold_sql,
                "clarification_question": question_text,
                "options": options,
            }
            e2_res = self.client.complete_json(
                agent_id="Oracle_Simulated_E2",
                system_prompt=ORACLE_E2_SYSTEM,
                payload=e2_payload,
                simulated_user=True,
                restricted_request=True,
            )
            selected_opt = str(e2_res.get("selected_option_id") or "")
            answer_text = str(e2_res.get("answer_text") or "")

            turn_data = {
                "turn": turn_count,
                "ambiguity": amb,
                "question_text": question_text,
                "options": options,
                "selected_option_id": selected_opt,
                "answer_text": answer_text,
            }
            clarification_turns.append(turn_data)
            self.logger.write_json(f"{turn_dir}/turn_record.json", turn_data)

            # Record resolved clarification as an additional explicit filter/guideline
            if answer_text:
                explicit_filters.append(f"Clarification resolution: {answer_text}")

        # 3. Build Confirmed Natural Language Intent Document
        rendered_lines = [
            f"Goal: {goal}",
            f"Target Entity: {target_entity}",
            f"Requested Outputs: {', '.join(str(o) for o in requested_outputs)}",
        ]
        if explicit_filters:
            rendered_lines.append("Filters and Constraints:")
            for flt in explicit_filters:
                rendered_lines.append(f"  - {flt}")
        if clarification_turns:
            rendered_lines.append("Resolved Clarifications:")
            for turn in clarification_turns:
                rendered_lines.append(f"  - Q: {turn['question_text']} -> A: {turn['answer_text']}")

        rendered_text = "\n".join(rendered_lines)

        canonical_cqd = {
            "cqd_id": f"cqd_{question_id}",
            "contract_version": "2.0",
            "status": "confirmed",
            "objective": {"description": goal, "primary_goal": goal},
            "populations": [{"population_id": "pop_1", "entity": target_entity, "description": target_entity}],
            "predicates": {"root": None, "atoms": []},
            "expressions": [],
            "aggregations": [],
            "groupings": [],
            "selections": [],
            "time_constraints": [],
            "comparisons": [],
            "outputs": [{"output_id": f"out_{i+1}", "attribute": str(attr), "requested": True} for i, attr in enumerate(requested_outputs)],
            "scopes": [],
            "deferred_grounding": [],
            "traceability": {
                "question_id": question_id,
                "state_hash": f"state_hash_{question_id}",
            },
            "rendered_text": rendered_text,
            "natural_language_intent": {
                "goal": goal,
                "target_entity": target_entity,
                "requested_outputs": requested_outputs,
                "explicit_filters": explicit_filters,
                "clarifications": clarification_turns,
            },
        }

        self.logger.write_json("semantic_frontend/04_confirmed_cqd/confirmed_cqd.json", canonical_cqd)
        self.logger.write_text("semantic_frontend/04_confirmed_cqd/confirmed_cqd.txt", rendered_text)
        self.logger.write_json("semantic_frontend/07_ledger/decision_events.json", clarification_turns)

        return {
            "question_id": question_id,
            "rounds": len(clarification_turns),
            "status": "confirmed",
            "confirmed_cqd": canonical_cqd,
            "confirmed_cqd_hash": canonical_cqd["traceability"]["state_hash"],
            "decision_ledger": clarification_turns,
        }

    def resolve_layout_gaps(
        self, *, confirmed_cqd: dict[str, Any], gaps: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Safe passthrough for layout gaps without complex AST rewriting."""
        return confirmed_cqd

