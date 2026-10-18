from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient


SQL_SYNTHESIS_SYSTEM = r"""You are an expert SQLite Text-to-SQL engineer.
Your task is to generate a precise, syntactically valid SQLite SQL query to answer the user question based on the Confirmed Business Intent and database schema.

Inputs provided:
- User Question
- Domain Evidence (hints, formulas, code value definitions, or mappings)
- Confirmed Business Intent (CQD / BIF)
- SQLite Database Schema (CREATE TABLE DDLs, column descriptions, foreign keys, and matched database values)

Core SQLite Generation Guidelines:
1. Business Intent Realization:
   - Faithfully realize the confirmed business intent, filters, groupings, and output expectations.
   - Use only the tables and columns defined in the provided schema.

2. Column Selection / Projection:
   - Select only the columns and aggregates requested by the business intent.
   - Do not include unrequested auxiliary columns in the SELECT clause.

3. Predicates & Evidence Grounding:
   - Use matched database values provided in the schema context for exact literal matching.
   - Apply any domain formulas or code mappings given in the evidence strictly in the WHERE or SELECT clauses.

4. Joins & Keys:
   - When multiple tables are required, connect them using the minimal foreign key relationships in the schema.

5. SQLite Arithmetic & Formatting:
   - When computing ratios or percentages, multiply by 1.0 or 100.0 to prevent integer division truncation.
   - Return raw numeric and text values directly without unnecessary string formatting unless requested.

6. Output Formatting:
   - Return only the raw SQLite SQL statement ending with a semicolon.
   - Do not include markdown formatting or explanations.
"""


def _clean_sql(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql|json|text)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    if cleaned.startswith("{") and "sql" in cleaned.lower():
        try:
            import json
            data = json.loads(cleaned)
            if isinstance(data, dict) and "sql" in data:
                cleaned = str(data["sql"]).strip()
        except Exception:
            pass
    cleaned = cleaned.rstrip(";") + ";"
    return cleaned


class UnifiedSQLSynthesizer:
    def __init__(self, client: OpenAIChatModelClient) -> None:
        self.client = client

    def synthesize(
        self,
        *,
        question: str,
        evidence: str | None,
        confirmed_cqd: dict[str, Any] | None = None,
        confirmed_sif: dict[str, Any] | None = None,
        schema_catalog: dict[str, Any],
        db_id: str,
    ) -> str:
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
            "database_schema": schema_text,
        }

        response = self.client.complete_text(
            agent_id="Unified_SQL_Synthesizer",
            system_prompt=SQL_SYNTHESIS_SYSTEM,
            payload=prompt_payload,
        )
        return _clean_sql(response)

    def _format_schema(self, schema_catalog: dict[str, Any], db_id: str) -> str:
        lines: list[str] = [f"Database: {db_id}\n"]
        for table in schema_catalog.get("tables", []):
            name = table.get("name")
            ddl = table.get("ddl")
            if ddl:
                lines.append(f"{ddl};\n")

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

            fks = table.get("foreign_keys", [])
            if fks:
                fk_strs = [
                    f"FOREIGN KEY ({fk['from_column']}) REFERENCES {fk['target_table']}({fk['to_column']})"
                    for fk in fks if fk.get("target_table") and fk.get("from_column") and fk.get("to_column")
                ]
                if fk_strs:
                    lines.append(f"Foreign Keys for `{name}`:\n  " + "\n  ".join(fk_strs) + "\n")

            val_matches = table.get("question_value_matches", [])
            if val_matches:
                match_strs = [f"  - `{name}`.{m['column']} = '{m['value']}'" for m in val_matches]
                lines.append(f"Matched Values for `{name}`:\n" + "\n".join(match_strs) + "\n")
        return "\n".join(lines)
