from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient
from intent_sql_agent.services.artifact_logger import ArtifactLogger


# =========================================================================
# 1. Direct SIF Grounder (스키마 직접 접지 및 물리 스키마 모호성 감지)
# =========================================================================
DIRECT_SIF_GROUNDER_SYSTEM = r"""You are an expert SQL Schema Grounding and Intent Frame (SIF) engineer.
Your task is to analyze the user's natural language question, domain evidence, and the database schema to produce a complete, grounded SQL Intent Frame (SIF).

PRINCIPLES:
1. Target Entities & Relations:
   - Identify the minimal set of tables and valid foreign key join paths necessary to answer the question.
2. Projections (SELECT):
   - Map requested output attributes directly to exact schema columns and necessary aggregations.
   - Project only what is requested by the question.
3. Filters (WHERE / HAVING):
   - Map conditions and qualifications directly to exact column predicates and schema-matched literals.
4. Physical Schema Conflict Detection:
   - Identify if any requested concept has multiple distinct candidate columns or relationships in the schema that represent different semantic attributes and cannot be uniquely determined from the question alone.
   - If a genuine schema conflict exists, list it in `schema_conflicts`.
   - If the mapping is uniquely determined by the question and schema, `schema_conflicts` must be an empty list `[]`.
   - Do not invent hypothetical ambiguities that have no basis in the schema or question.

Return ONLY a JSON object:
{
  "target_tables": ["table_name"],
  "join_paths": ["table1.col = table2.col"],
  "projections": [
    {"column": "table.column", "expression": "table.column", "alias": "alias_name"}
  ],
  "where_predicates": [
    "table.column = 'value'"
  ],
  "group_by": [],
  "order_by": [
    {"expression": "table.column", "direction": "ASC|DESC"}
  ],
  "limit": null,
  "schema_conflicts": [
    {
      "attribute_name": "concept_name",
      "candidate_columns": ["table.col1", "table.col2"],
      "conflict_description": "Description of why multiple candidate columns exist for this concept."
    }
  ]
}
"""

# =========================================================================
# 2. SIF Clarification Question Writer (비기술적 명확화 질문 작성기)
# =========================================================================
SIF_CLARIFICATION_WRITER_SYSTEM = r"""You are a clarification question writer for a database system.
When a schema candidate conflict is identified, formulate a clear, non-technical multiple-choice question offering distinct options so the user can clarify their intended real-world meaning.

RULES:
- Use clear, non-technical natural language.
- Do not expose raw table names, column names, or SQL syntax to the user.
- Present options in terms of real-world business meaning and intent.

Return ONLY a JSON object:
{
  "question_text": "Clarification question in plain domain language",
  "options": [
    {"option_id": "A", "description": "Description of first alternative in plain language"},
    {"option_id": "B", "description": "Description of second alternative in plain language"}
  ]
}
"""

# =========================================================================
# 3. Oracle E2 Step 1: Technical Selector (Gold SQL 기반 객관적 정답 선택)
# =========================================================================
ORACLE_E2_TECHNICAL_SELECTOR_SYSTEM = r"""You are an SQL benchmark oracle analyst.
Your task is to analyze the provided `gold_sql` and identify which of the given `options` accurately reflects the exact semantic logic executed in Gold SQL.

EVALUATION PROCESS:
1. Examine Gold SQL projections, calculations, tables, joins, filters, grouping, and ordering.
2. Compare the semantic meaning of each option against the true logic of Gold SQL.
3. Select the option that precisely corresponds to Gold SQL's execution intent.

Return ONLY a JSON object:
{
  "gold_sql_analysis": "Detailed step-by-step breakdown of Gold SQL execution semantics.",
  "selected_option_id": "Option identifier (e.g. A, B)",
  "technical_reason": "Explanation of why this option matches Gold SQL."
}
"""

# =========================================================================
# 4. Oracle E2 Step 2: Natural Translator (비개발자 사용자 발화로 순화)
# =========================================================================
ORACLE_E2_NATURAL_TRANSLATOR_SYSTEM = r"""You are a non-technical domain user responding to a clarification question.
Given a selected option, articulate your requirement in natural, conversational business language.

RULES:
1. Express what you want using plain domain concepts.
2. Do not use technical database terminology, table/column names, data types, or SQL keywords.
3. Keep the answer clear, natural, and direct.

Return ONLY a JSON object:
{
  "selected_option_id": "Option identifier",
  "answer_text": "Natural conversational statement explaining your requirement."
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


class DirectSIFService:
    """Direct SIF (SQL Intent Frame) Service: Grounds question directly to schema without BIF."""

    def __init__(
        self,
        client: OpenAIChatModelClient,
        logger: ArtifactLogger,
        *,
        max_rounds: int = 2,
    ) -> None:
        self.client = client
        self.logger = logger
        self.max_rounds = max_rounds

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

        # 1. Direct SIF Schema Grounding
        schema_summary = []
        tables = schema_catalog.get("tables", [])
        if isinstance(tables, list):
            for t in tables:
                tbl = t.get("name", "")
                cols = [f"{c['name']} ({c.get('type', 'TEXT')})" for c in t.get("columns", [])]
                schema_summary.append(f"TABLE {tbl}:\n  COLUMNS: {', '.join(cols)}")
        elif isinstance(tables, dict):
            for tbl, info in tables.items():
                cols = [f"{c['name']} ({c.get('type', 'TEXT')})" for c in info.get("columns", [])]
                schema_summary.append(f"TABLE {tbl}:\n  COLUMNS: {', '.join(cols)}")
        schema_text = "\n\n".join(schema_summary)

        grounding_payload = {
            "question": question,
            "evidence": evidence or "None",
            "database_schema": schema_text,
            "matched_values": schema_catalog.get("matched_values", []),
        }

        sif_grounding = self.client.complete_json(
            agent_id="Direct_SIF_Grounder",
            system_prompt=DIRECT_SIF_GROUNDER_SYSTEM,
            payload=grounding_payload,
        )

        schema_conflicts = sif_grounding.get("schema_conflicts") or []
        if isinstance(schema_conflicts, str):
            schema_conflicts = [{"conflict_description": schema_conflicts}]

        self.logger.write_json(
            "frames/direct_sif_initial_grounding.json",
            {
                "question_id": question_id,
                "sif_grounding": sif_grounding,
                "schema_conflicts": schema_conflicts,
            },
        )

        # 2. SIF Clarification Turns (오직 물리적 스키마 충돌 시에만 실행)
        clarification_turns: list[dict[str, Any]] = []
        turn_count = 0

        for conflict in schema_conflicts:
            if turn_count >= self.max_rounds:
                break
            turn_count += 1
            turn_dir = f"frames/sif_clarification/turn_{turn_count:02d}"

            # Step 2a: 비기술적 질문 작성
            q_payload = {
                "question": question,
                "evidence": evidence or "None",
                "schema_conflict": conflict,
            }
            q_res = self.client.complete_json(
                agent_id="SIF_Clarification_Question_Writer",
                system_prompt=SIF_CLARIFICATION_WRITER_SYSTEM,
                payload=q_payload,
            )
            question_text = str(q_res.get("question_text") or f"Please clarify: {conflict}")
            options = q_res.get("options") or []

            # Step 2b: Oracle E2 Step 1 기술적 선택
            tech_payload = {
                "original_question": question,
                "evidence": evidence or "None",
                "gold_sql": gold_sql,
                "clarification_question": question_text,
                "options": options,
            }
            tech_res = self.client.complete_json(
                agent_id="Oracle_Simulated_E2_Technical_Selector",
                system_prompt=ORACLE_E2_TECHNICAL_SELECTOR_SYSTEM,
                payload=tech_payload,
                simulated_user=True,
                restricted_request=True,
            )
            selected_opt = str(tech_res.get("selected_option_id") or "")
            tech_analysis = str(tech_res.get("gold_sql_analysis") or "")
            tech_reason = str(tech_res.get("technical_reason") or "")

            self.logger.write_json(f"{turn_dir}/e2_technical_decision.json", {
                "turn": turn_count,
                "question_text": question_text,
                "options": options,
                "selected_option_id": selected_opt,
                "gold_sql_analysis": tech_analysis,
                "technical_reason": tech_reason,
            })

            # Step 2c: Oracle E2 Step 2 자연어 순화 발화
            chosen_opt_desc = ""
            for o in options:
                if str(o.get("option_id")) == selected_opt:
                    chosen_opt_desc = str(o.get("description", ""))
                    break

            trans_payload = {
                "original_question": question,
                "clarification_question": question_text,
                "selected_option_id": selected_opt,
                "selected_option_description": chosen_opt_desc,
            }
            trans_res = self.client.complete_json(
                agent_id="Oracle_Simulated_E2_Natural_Translator",
                system_prompt=ORACLE_E2_NATURAL_TRANSLATOR_SYSTEM,
                payload=trans_payload,
                simulated_user=True,
                restricted_request=True,
            )
            answer_text = str(trans_res.get("answer_text") or chosen_opt_desc)

            turn_data = {
                "turn": turn_count,
                "question_text": question_text,
                "options": options,
                "selected_option_id": selected_opt,
                "selected_option_description": chosen_opt_desc,
                "answer_text": answer_text,
                "technical_reason": tech_reason,
                "gold_sql_analysis": tech_analysis,
            }
            self.logger.write_json(f"{turn_dir}/turn_record.json", turn_data)
            clarification_turns.append(turn_data)

        # 3. 확정된 Confirmed SIF Frame 생성
        confirmed_sif = {
            "schema_version": "2.0",
            "question_id": question_id,
            "target_tables": sif_grounding.get("target_tables", []),
            "join_paths": sif_grounding.get("join_paths", []),
            "projections": sif_grounding.get("projections", []),
            "where_predicates": sif_grounding.get("where_predicates", []),
            "group_by": sif_grounding.get("group_by", []),
            "order_by": sif_grounding.get("order_by", []),
            "limit": sif_grounding.get("limit"),
            "clarification_turns": clarification_turns,
            "clarification_rounds": len(clarification_turns),
        }

        self.logger.write_json("frames/confirmed_sif.json", confirmed_sif)

        return {
            "status": "success",
            "confirmed_sif": confirmed_sif,
            "rounds": len(clarification_turns),
            "clarification_turns": clarification_turns,
        }
