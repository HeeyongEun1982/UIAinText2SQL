from __future__ import annotations

from typing import Any
from intent_sql_agent.model_client import OpenAIChatModelClient
from intent_sql_agent.services.unified_sql_synthesizer import _clean_sql


SELF_CORRECTION_SYSTEM = r"""You are an expert SQLite SQL repair engineer.
A generated SQL query produced a SQLite runtime error.
Your task is to fix the SQL query so that it executes successfully on SQLite and correctly answers the question.

Analyze the SQLite error message, the failed SQL, and the database schema.
Common issues to correct:
1. "no such column: X" or "no such table: Y": Check table and column spelling in schema.
2. "ambiguous column name: Z": Add table alias prefix (e.g. `T1.col`).
3. "syntax error": Check SQLite SQL syntax (e.g., quotes around identifiers with spaces or special characters).
4. Table join conditions: Ensure join columns match primary/foreign keys.

Return ONLY the corrected SQL query ending with a semicolon. Do NOT include markdown explanations.
"""


class SQLSelfCorrection:
    def __init__(self, client: OpenAIChatModelClient) -> None:
        self.client = client

    def correct(
        self,
        *,
        question: str,
        evidence: str | None,
        failed_sql: str,
        error_message: str,
        schema_catalog: dict[str, Any],
        db_id: str,
    ) -> str:
        prompt_payload = {
            "db_id": db_id,
            "question": question,
            "evidence": evidence or "None",
            "failed_sql": failed_sql,
            "sqlite_error": error_message,
        }
        response = self.client.complete_text(
            agent_id="SQL_Self_Correction",
            system_prompt=SELF_CORRECTION_SYSTEM,
            payload=prompt_payload,
        )
        return _clean_sql(response)
