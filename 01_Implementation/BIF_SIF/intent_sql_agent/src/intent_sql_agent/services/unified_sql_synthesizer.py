from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient


SQL_SYNTHESIS_SYSTEM = r"""You are an expert SQLite Text-to-SQL engineer.
Your task is to generate a precise, syntactically valid SQLite SQL query to answer the user question.

Inputs provided:
- User Question
- Domain Evidence (hints, formulas, code value definitions, or mappings)
- Confirmed Business Intent (BIF)
- Confirmed SIF 13-Contract Grounding Plan (Anchor tables, foreign key joins, WHERE predicates, GROUP BY granularity, SELECT projections)
- SQLite Database Schema (CREATE TABLE DDLs, column descriptions, foreign keys, and matched database values)

Core SQLite Generation Guidelines:
1. SIF 13-Contract Grounding Execution (CRITICAL):
   - When a `confirmed_sif_plan` (or `clauses`) is provided, strictly follow its grounded table anchors, minimal foreign key join paths, and target projection expressions.
   - Do NOT invent ungrounded tables or extraneous joins that contradict the confirmed SIF plan.

2. Column Selection / Projection (CRITICAL):
   - Select ONLY the columns explicitly asked for in the user question or confirmed in SIF.
   - For "What is the most common X?" or "Which X has the highest frequency?":
     * SELECT only the target column X without including unrequested COUNT(*) helper columns in the SELECT list.
   - For entity attributes spanning multiple schema columns:
     * Select the primary schema columns directly without inventing complex regex filters, defensive CTEs, or UNION pivot subqueries.

3. Schema & Evidence Grounding:
   - Use only table and column names that exist in the provided schema.
   - Use matched database values provided in the schema context for exact literal matching.
   - If evidence defines domain codes or value mappings, apply them strictly in the WHERE clause.

4. Table Joins:
   - When the query requires multiple tables, join them using the minimal primary and foreign key paths provided in the schema.

5. Date & Time Expressions:
   - Use SQLite date/time functions (such as `strftime` or `date`) appropriately when filtering or extracting temporal components.

6. Arithmetic, Percentages & Raw Output:
   - In SQLite, integer division truncates. When computing percentages, ratios, or differences, multiply by 1.0 or 100.0 to ensure floating-point precision.
   - Unless explicitly requested by the user question or evidence, return raw numeric values directly without wrapping expressions in `printf` string formatting or arbitrary case transformations.

7. Output Formatting:
   - Return clean, direct SQLite SQL statement ending with a semicolon.
   - Do NOT wrap with markdown fences or extra explanations; return only the SQL statement.
"""


def _clean_sql(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql|json|text)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    # If JSON object was returned, extract sql field
    if cleaned.startswith("{") and "sql" in cleaned.lower():
        try:
            import json
            data = json.loads(cleaned)
            if isinstance(data, dict) and "sql" in data:
                cleaned = str(data["sql"]).strip()
        except Exception:
            pass
    # Remove trailing comments if any, ensure ending semicolon
    cleaned = cleaned.rstrip(";") + ";"
    return cleaned


def _normalize_projection(sql: str, question: str) -> str:
    """Normalize unrequested COUNT(*) or aggregation projections in most-common / frequency queries."""
    lower_q = question.lower()
    multi_indicators = [
        "how many", "count", "amount", "number of", "total number",
        "indicate the", "along with", "as well as", "percentage", "ratio", "proportion",
    ]
    wants_count = any(k in lower_q for k in multi_indicators)
    if wants_count:
        return sql

    # Find the final outermost SELECT clause
    select_matches = list(re.finditer(r"\bSELECT\s+(DISTINCT\s+)?(.+?)\s+FROM\b", sql, flags=re.IGNORECASE | re.DOTALL))
    if not select_matches:
        return sql

    last_match = select_matches[-1]
    start_pos = last_match.start()
    end_pos = last_match.end()
    distinct_str = last_match.group(1) or ""
    proj_str = last_match.group(2).strip()

    # If projection contains multiple columns and some are unrequested count/percentage/amount helpers
    parts = [p.strip() for p in re.split(r",\s*(?![^()]*\))", proj_str)]
    if len(parts) > 1:
        non_helpers = []
        stripped_aliases = []
        for p in parts:
            p_lower = p.lower()
            is_helper = (
                re.match(r"^count\s*\(", p, flags=re.IGNORECASE)
                or "count(*)" in p_lower
                or "tot.total" in p_lower
                or "cnt" in p_lower
                or "percentage" in p_lower
                or "100.0 *" in p_lower
                or "100 *" in p_lower
            )
            if is_helper:
                alias_match = re.search(r"\bAS\s+([a-zA-Z0-9_]+)$", p, flags=re.IGNORECASE)
                if alias_match:
                    stripped_aliases.append(alias_match.group(1))
            else:
                non_helpers.append(p)

        if non_helpers and len(non_helpers) < len(parts):
            new_proj = ", ".join(non_helpers)
            new_select_clause = f"SELECT {distinct_str}{new_proj} FROM"
            modified_sql = sql[:start_pos] + new_select_clause + sql[end_pos:]
            for alias in stripped_aliases:
                modified_sql = re.sub(
                    rf"\bORDER\s+BY\s+{re.escape(alias)}\b",
                    "ORDER BY COUNT(*)",
                    modified_sql,
                    flags=re.IGNORECASE,
                )
            return modified_sql

    return sql


class UnifiedSQLSynthesizer:
    def __init__(self, client: OpenAIChatModelClient) -> None:
        self.client = client

    def synthesize(
        self,
        *,
        question: str,
        evidence: str | None,
        confirmed_cqd: dict[str, Any] | None,
        confirmed_sif: dict[str, Any] | None = None,
        schema_catalog: dict[str, Any],
        db_id: str,
    ) -> str:
        # Build concise, informative schema prompt
        schema_text = self._format_schema(schema_catalog, db_id)
        
        intent_text = ""
        if isinstance(confirmed_cqd, dict):
            intent_text = confirmed_cqd.get("rendered_text") or ""
        if not intent_text and confirmed_cqd:
            intent_text = str(confirmed_cqd)

        prompt_payload = {
            "db_id": db_id,
            "question": question,
            "evidence": evidence or "None",
            "confirmed_business_intent": intent_text or "Direct question intent",
            "confirmed_sif_plan": confirmed_sif or {},
            "database_schema": schema_text,
        }

        response = self.client.complete_text(
            agent_id="Unified_SQL_Synthesizer",
            system_prompt=SQL_SYNTHESIS_SYSTEM,
            payload=prompt_payload,
        )
        cleaned = _clean_sql(response)
        return _normalize_projection(cleaned, question)

    def _format_schema(self, schema_catalog: dict[str, Any], db_id: str) -> str:
        lines: list[str] = [f"Database: {db_id}\n"]
        for table in schema_catalog.get("tables", []):
            name = table.get("name")
            ddl = table.get("ddl")
            if ddl:
                lines.append(f"{ddl};\n")
            
            # Add column description highlights if present
            col_docs = []
            for col in table.get("columns", []):
                col_name = col.get("name")
                desc = col.get("description")
                v_desc = col.get("value_description")
                if desc or v_desc:
                    doc_parts = []
                    if desc:
                        doc_parts.append(f"Description: {desc}")
                    if v_desc:
                        doc_parts.append(f"Values/Codes: {v_desc}")
                    col_docs.append(f"  - {name}.{col_name}: {'; '.join(doc_parts)}")
            if col_docs:
                lines.append(f"Column Documentation for `{name}`:\n" + "\n".join(col_docs) + "\n")
            
            # Foreign keys
            fks = table.get("foreign_keys", [])
            if fks:
                fk_strs = [
                    f"FOREIGN KEY ({fk['from_column']}) REFERENCES {fk['target_table']}({fk['to_column']})"
                    for fk in fks if fk.get("target_table") and fk.get("from_column") and fk.get("to_column")
                ]
                if fk_strs:
                    lines.append(f"Foreign Keys for `{name}`:\n  " + "\n  ".join(fk_strs) + "\n")

            # Question / Evidence value matches
            val_matches = table.get("question_value_matches", [])
            if val_matches:
                match_strs = [f"  - `{name}`.{m['column']} = '{m['value']}'" for m in val_matches]
                lines.append(f"Matched Values for `{name}`:\n" + "\n".join(match_strs) + "\n")
        return "\n".join(lines)
