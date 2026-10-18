from __future__ import annotations

import json
import logging
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient

logger = logging.getLogger(__name__)


SIF_13_CONTRACT_ASSEMBLER_SYSTEM = r"""You are an expert System Intent Framework (SIF) 13-Contract Architect.
Your task is to assemble the confirmed natural-language business intent (BIF) and the physical relational database schema into a complete 13-Contract SIF Draft and initial relational SQL clauses.

The 13 Contract Families:
1. "1_objective": Exact technical query purpose.
2. "2_population": Primary anchor table(s) and base entity cohort.
3. "3_scope": Filtering boundaries or CTE subquery scopes.
4. "4_predicates": List of WHERE conditions (table, column, operator, value).
5. "5_deferred_grounding": Matched database literal codes or constants.
6. "6_selections_joins": Explicit FROM and JOIN clause connecting anchor tables via minimal foreign keys.
7. "7_expressions": Calculations, ratios (100.0 * a / b), differences, or temporal formulas.
8. "8_time_constraints": SQLite date/time functions (e.g. strftime('%Y', col)).
9. "9_aggregations": Aggregate functions (SUM, AVG, COUNT, MAX, MIN).
10. "10_groupings": GROUP BY column expressions ensuring proper entity granularity.
11. "11_comparisons": HAVING filters, ORDER BY expressions, and LIMIT/OFFSET clauses.
12. "12_outputs": Exact SELECT projection expressions and aliases.
13. "13_traceability": End-to-end verification statement connecting BIF to SIF.

Clause Bindings:
Provide initial draft SQL clauses in "clauses":
- "SELECT": SQL projection string
- "FROM_JOIN": SQL FROM and JOIN clause
- "WHERE": SQL WHERE clause (or null if no filters)
- "GROUP_BY": SQL GROUP BY clause (or null)
- "HAVING": SQL HAVING clause (or null)
- "ORDER_BY": SQL ORDER BY clause (or null)
- "LIMIT": SQL LIMIT clause (or null)

Return ONLY a JSON object:
{
  "contracts": {
    "1_objective": "...",
    "2_population": "...",
    "3_scope": "...",
    "4_predicates": [...],
    "5_deferred_grounding": [...],
    "6_selections_joins": "...",
    "7_expressions": "...",
    "8_time_constraints": null,
    "9_aggregations": "...",
    "10_groupings": null,
    "11_comparisons": null,
    "12_outputs": [...],
    "13_traceability": "..."
  },
  "clauses": {
    "SELECT": "...",
    "FROM_JOIN": "...",
    "WHERE": "...",
    "GROUP_BY": null,
    "HAVING": null,
    "ORDER_BY": null,
    "LIMIT": null
  },
  "reverse_business_explanation": "A natural language business summary explaining the query plan in non-technical terms."
}
"""


SIF_4_AXIS_ACTIVE_INSPECTOR_SYSTEM = r"""You are an expert SIF 4-Axis Active Business Clarification Inspector.
Your task is to inspect the Draft SIF Plan and generate 4 active confirmation questions (one for each of the 4 Essential Relational Query Axes) to confirm or clarify the business intent with a non-technical Human Business User (E2).

CRITICAL RULE - STRICT NON-TECHNICAL NATURAL BUSINESS LANGUAGE ONLY:
- You are speaking directly to a non-technical Business User.
- You MUST NEVER use SQL syntax, SQL keywords (SELECT, FROM, JOIN, ON, GROUP BY, WHERE, HAVING, LIMIT, DISTINCT, COUNT, SUM), database table names, or physical column names in question_text or options.
- Translate all technical database concepts into clear, domain-agnostic business concepts:
  - Table / Join -> The real-world entities and conceptual relationships being analyzed.
  - Calculation / Formula -> The conceptual business calculation goal in plain language.
  - Grouping / Granularity -> Whether records sharing identical business attributes should be consolidated into a single entry or preserved individually.
  - Output Columns -> The desired report fields and contextual attributes to present to the user.

The 4 Mandatory Business Inspection Axes:

1. [Axis 1: Business Entity & Target Scope (Contracts 2 & 6)]:
   - Question: "Are you interested in analyzing [Primary Target Concept] or [Alternative Conceptual Entity]?"
   - Options:
     - opt_A: "Focus on [Primary Target Concept]."
     - opt_B: "Focus on [Alternative Conceptual Entity]."

2. [Axis 2: Calculation & Metric Definition (Contracts 7 & 8)]:
   - For direct retrieval: "Should we retrieve the value directly as recorded without modification?"
   - For temporal / duration / elapsed calculations: "Should the time duration be calculated in whole calendar units or exact elapsed fractional units with decimals?"
   - For ratio / percentage calculations: "Should the percentage be calculated based on all matching entries in the relationship or based on distinct master entities?"
   - For difference calculations: "Should the report display only the final difference value or also include the individual source values?"
   - Options:
     - opt_A: "[First Conceptual Calculation Convention]"
     - opt_B: "[Second Conceptual Calculation Convention]"

3. [Axis 3: Entity Granularity & Deduplication (Contracts 10 & 9)]:
   - Question: "When presenting items, if multiple records share the same name or identifier, should they be consolidated into a single unique entry or listed as separate individual records?"
   - Options:
     - opt_A: "Consolidate items sharing the same name or identifier into a single unique entry."
     - opt_B: "Keep each database record separate."

4. [Axis 4: Report Output Scope & Details (Contract 12)]:
   - Question: "In the final report, would you like to see only the primary requested item or also include related contextual information?"
   - Options:
     - opt_A: "Display only the primary requested item."
     - opt_B: "Display both the primary item and related contextual information."

Output format:
Return ONLY a JSON array containing the 4 inspection items:
[
  {
    "axis_key": "1_entity_join",
    "question_text": "...",
    "options": [
      {"option_id": "opt_A", "text": "..."},
      {"option_id": "opt_B", "text": "..."}
    ]
  },
  {
    "axis_key": "2_calculation_metric",
    "question_text": "...",
    "options": [
      {"option_id": "opt_A", "text": "..."},
      {"option_id": "opt_B", "text": "..."}
    ]
  },
  {
    "axis_key": "3_grouping_granularity",
    "question_text": "...",
    "options": [
      {"option_id": "opt_A", "text": "..."},
      {"option_id": "opt_B", "text": "..."}
    ]
  },
  {
    "axis_key": "4_output_projection",
    "question_text": "...",
    "options": [
      {"option_id": "opt_A", "text": "..."},
      {"option_id": "opt_B", "text": "..."}
    ]
  }
]
"""


SIF_ORACLE_E2_SYSTEM = r"""You are an Oracle User Simulator (E2) representing a non-technical Human Business User who asked the original question.
Your task is to review the assistant's business clarification questions and answer based on your true business intent (reflected in the Gold SQL reference).

CRITICAL RULE - NATURAL BUSINESS LANGUAGE ONLY:
- You are a non-technical business user. You DO NOT know SQL syntax, table names, or column names.
- NEVER write SQL syntax, SQL keywords (SELECT, FROM, JOIN, WHERE, ON, GROUP BY, HAVING), table names, or physical column names in your reasoning, answer_text, or custom_correction.
- Always communicate purely in natural, conversational business English explaining what you conceptually want.

Decision Rules:
1. If one of the provided options accurately matches your true business intent (as reflected in Gold SQL), select that option (e.g. "opt_A" or "opt_B").
2. If NONE of the provided options accurately match your true business intent, select "none_of_the_above" and describe your desired business requirements in plain natural conversational English in "custom_correction".

Return ONLY a JSON object:
{
  "selected_option_id": "opt_A" / "opt_B" / "none_of_the_above",
  "reasoning": "Brief explanation of why this matches the business intent (in natural language, no SQL code).",
  "answer_text": "Exact text of the selected option or summary of custom correction.",
  "custom_correction": "Plain natural language business requirement if none_of_the_above, else null"
}
"""


SIF_CONTRACT_REFINE_SYSTEM = r"""You are an expert SIF Contract Refinement Agent and Database Engineer.
Your task is to bridge the semantic gap: translate the User's Natural Business Clarification Feedback into rigorous 13-Contract SIF physical database specifications and exact SQL clause bindings.

Given:
1. Draft SIF Plan (13 Contracts and Clauses)
2. User's Natural Business Decisions and Custom Corrections (Entity Scope, Calculation Metric, Grouping/Deduplication, Output Scope)
3. Target Database Schema (DDL, Foreign Keys, Columns, Documentation)

Instructions:
- Analyze the user's natural business intent and ground it directly into the target database schema:
  - Map the clarified business entity and relationship to the appropriate primary table and foreign key join path in the schema.
  - Map the clarified calculation convention to the appropriate expressions in 7_expressions, 12_outputs, and clauses["SELECT"].
  - Map the clarified deduplication preference to DISTINCT or GROUP BY in 10_groupings and clauses["GROUP_BY"].
  - Map the clarified output scope to 12_outputs and clauses["SELECT"].
- Ensure all clauses in clauses dictionary (SELECT, FROM_JOIN, WHERE, GROUP_BY, HAVING, ORDER_BY, LIMIT) are syntactically valid SQLite clauses.

Return ONLY the updated JSON object matching the SIF plan schema:
{
  "contracts": {
    "1_objective": "...",
    "2_population": "...",
    "3_scope": "...",
    "4_predicates": [...],
    "5_deferred_grounding": [...],
    "6_selections_joins": "...",
    "7_expressions": "...",
    "8_time_constraints": null,
    "9_aggregations": "...",
    "10_groupings": null,
    "11_comparisons": null,
    "12_outputs": [...],
    "13_traceability": "..."
  },
  "clauses": {
    "SELECT": "...",
    "FROM_JOIN": "...",
    "WHERE": "...",
    "GROUP_BY": null,
    "HAVING": null,
    "ORDER_BY": null,
    "LIMIT": null
  },
  "reverse_business_explanation": "..."
}
"""


class SIFClarificationService:
    """Orchestrates 13-Contract SIF Drafting, 4-Axis Mandatory Active Inspection, and Contract Freezing."""

    def __init__(self, client: OpenAIChatModelClient) -> None:
        self.client = client

    def run(
        self,
        *,
        case: dict[str, Any],
        bif: dict[str, Any],
        schema_catalog: dict[str, Any],
        db_id: str,
    ) -> dict[str, Any]:
        """Runs SIF Drafting, 4-Axis Active Inspection with E2, and Contract Freezing."""
        question = str(case.get("question", ""))
        evidence = str(case.get("evidence") or "")
        gold_sql = str(case.get("SQL") or case.get("sql") or "")

        # Format schema context
        schema_text = self._format_schema(schema_catalog, db_id)

        # Step 1: Assemble 13-Contract SIF Draft & Draft Clauses
        draft_sif = self._assemble_13_contracts(
            question=question,
            evidence=evidence,
            bif=bif,
            schema_text=schema_text,
        )

        # Step 2: Generate 4-Axis Mandatory Active Inspection Questions
        inspection_questions = self._active_inspect_4_axes(
            question=question,
            evidence=evidence,
            draft_sif=draft_sif,
            schema_text=schema_text,
        )

        # Step 3: Evaluate Active Inspection Questions with E2 Oracle (allows option selection or custom correction)
        resolved_decisions = self._evaluate_with_e2(
            question=question,
            evidence=evidence,
            gold_sql=gold_sql,
            questions=inspection_questions,
        )

        # Step 4: Refine & Freeze 13-Contract SIF Plan and SQL Clauses
        confirmed_sif = self._refine_contracts(
            question=question,
            evidence=evidence,
            draft_sif=draft_sif,
            resolved_decisions=resolved_decisions,
            schema_text=schema_text,
        )

        return confirmed_sif

    def _assemble_13_contracts(
        self,
        *,
        question: str,
        evidence: str,
        bif: dict[str, Any],
        schema_text: str,
    ) -> dict[str, Any]:
        """Assembles initial 13-Contract SIF Draft from BIF and physical schema."""
        bif_text = bif.get("rendered_text") if isinstance(bif, dict) else str(bif)
        payload = {
            "database_schema": schema_text,
            "question": question,
            "evidence": evidence or "None",
            "confirmed_business_intent": bif_text,
        }

        response = self.client.complete_text(
            agent_id="SIF_13_Contract_Assembler",
            system_prompt=SIF_13_CONTRACT_ASSEMBLER_SYSTEM,
            payload=payload,
        )
        try:
            return json.loads(response.strip())
        except Exception:
            return {
                "contracts": {
                    "1_objective": question,
                    "2_population": "Primary table from schema",
                    "12_outputs": [question],
                    "13_traceability": "Default SIF 13-contract fallback",
                },
                "clauses": {
                    "SELECT": None,
                    "FROM_JOIN": None,
                    "WHERE": None,
                    "GROUP_BY": None,
                    "HAVING": None,
                    "ORDER_BY": None,
                    "LIMIT": None,
                },
                "reverse_business_explanation": f"Draft query analyzing {question}.",
            }

    def _active_inspect_4_axes(
        self,
        *,
        question: str,
        evidence: str,
        draft_sif: dict[str, Any],
        schema_text: str,
    ) -> list[dict[str, Any]]:
        """Generates the 4 mandatory active inspection questions across Entity/Join, Calculation, Grouping, and Output."""
        payload = {
            "database_schema": schema_text,
            "question": question,
            "evidence": evidence or "None",
            "draft_sif_plan": draft_sif,
        }

        response = self.client.complete_text(
            agent_id="SIF_4_Axis_Active_Inspector",
            system_prompt=SIF_4_AXIS_ACTIVE_INSPECTOR_SYSTEM,
            payload=payload,
        )
        try:
            parsed = json.loads(response.strip())
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict) and "questions" in parsed:
                return parsed["questions"]
        except Exception:
            pass

        # Fallback inspection questions in pure natural business language
        return [
            {
                "axis_key": "1_entity_join",
                "question_text": "Is the query focused on the primary business entity and its standard associated relationship?",
                "options": [
                    {"option_id": "opt_A", "text": "Yes, proceed with the primary business entity."},
                    {"option_id": "opt_B", "text": "No, switch to an alternative conceptual entity."},
                ],
            },
            {
                "axis_key": "2_calculation_metric",
                "question_text": "Is the business calculation or metric definition correctly specified?",
                "options": [
                    {"option_id": "opt_A", "text": "Yes, proceed with standard calculation convention."},
                    {"option_id": "opt_B", "text": "No, apply an alternative conceptual calculation convention."},
                ],
            },
            {
                "axis_key": "3_grouping_granularity",
                "question_text": "When presenting items, if multiple records share the same name or identifier, should they be consolidated into a single unique entry or listed separately?",
                "options": [
                    {"option_id": "opt_A", "text": "Consolidate items sharing the same name or identifier into a single unique entry."},
                    {"option_id": "opt_B", "text": "Keep each record separate."},
                ],
            },
            {
                "axis_key": "4_output_projection",
                "question_text": "In the final report, should we display only the primary requested item or also include related contextual information?",
                "options": [
                    {"option_id": "opt_A", "text": "Display only the primary requested item."},
                    {"option_id": "opt_B", "text": "Display both the primary item and related contextual information."},
                ],
            },
        ]

    def _evaluate_with_e2(
        self,
        *,
        question: str,
        evidence: str,
        gold_sql: str,
        questions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Evaluates the 4 active inspection questions with E2 Oracle, allowing option selection or custom correction."""
        if not questions or not gold_sql:
            return []

        resolved_decisions = []
        for q in questions:
            q_text = str(q.get("question_text") or "")
            options = list(q.get("options") or [])
            if not q_text or not options:
                continue

            e2_payload = {
                "original_question": question,
                "evidence": evidence or "None",
                "gold_sql": gold_sql,
                "clarification_question": q_text,
                "options": options,
            }
            e2_res = self.client.complete_json(
                agent_id="SIF_Oracle_Simulated_E2",
                system_prompt=SIF_ORACLE_E2_SYSTEM,
                payload=e2_payload,
                simulated_user=True,
                restricted_request=True,
            )
            selected_opt = str(e2_res.get("selected_option_id") or "")
            answer_text = str(e2_res.get("answer_text") or "")
            custom_corr = e2_res.get("custom_correction")
            resolved_decisions.append({
                "axis_key": q.get("axis_key"),
                "question": q_text,
                "selected_option": selected_opt,
                "answer_text": answer_text,
                "custom_correction": custom_corr,
            })

        return resolved_decisions

    def _refine_contracts(
        self,
        *,
        question: str,
        evidence: str,
        draft_sif: dict[str, Any],
        resolved_decisions: list[dict[str, Any]],
        schema_text: str,
    ) -> dict[str, Any]:
        """Refines and freezes the 13-Contract SIF plan and SQL clauses based on E2 answers and custom corrections."""
        if not resolved_decisions:
            return draft_sif

        refine_payload = {
            "database_schema": schema_text,
            "question": question,
            "evidence": evidence or "None",
            "current_sif_plan": draft_sif,
            "resolved_clarification_decisions": resolved_decisions,
        }
        refine_response = self.client.complete_text(
            agent_id="SIF_Contract_Refiner",
            system_prompt=SIF_CONTRACT_REFINE_SYSTEM,
            payload=refine_payload,
        )
        try:
            refined_plan = json.loads(refine_response.strip())
            refined_plan["active_inspection_decisions"] = resolved_decisions
            return refined_plan
        except Exception:
            draft_sif["active_inspection_decisions"] = resolved_decisions
            return draft_sif

    def _format_schema(self, schema_catalog: dict[str, Any], db_id: str) -> str:
        """Formats tables, columns, foreign keys, descriptions, and matched database values."""
        lines = [f"=== SQLite Database Schema: {db_id} ==="]
        tables = schema_catalog.get("tables", [])
        for t in tables:
            name = t.get("name", "")
            ddl = t.get("ddl", "")
            lines.append(f"\nTable: {name}")
            if ddl:
                lines.append(f"  DDL: {ddl.strip()}")
            fks = t.get("foreign_keys", [])
            if fks:
                lines.append("  Foreign Keys:")
                for fk in fks:
                    lines.append(
                        f"    - {name}.{fk.get('from_column')} -> {fk.get('target_table')}.{fk.get('to_column')}"
                    )
            cols = t.get("columns", [])
            desc_lines = []
            for c in cols:
                desc = c.get("description")
                val_desc = c.get("value_description")
                if desc or val_desc:
                    desc_lines.append(f"    - {c.get('name')}: {desc or ''} (Values: {val_desc or 'N/A'})")
            if desc_lines:
                lines.append("  Documented Columns:")
                lines.extend(desc_lines)
            matches = t.get("question_value_matches", [])
            if matches:
                lines.append("  Matched Database Values:")
                for m in matches:
                    lines.append(f"    - Column '{m.get('column')}': {m.get('matches')}")
        return "\n".join(lines)
