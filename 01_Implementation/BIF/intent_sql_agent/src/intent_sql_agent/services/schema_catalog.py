from __future__ import annotations

import hashlib
import csv
import sqlite3
from pathlib import Path
from typing import Any


def load_sqlite_schema(
    database: Path, *, question: str | None = None, evidence: str | None = None
) -> dict[str, Any]:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        objects = connection.execute(
            "SELECT name, type, sql FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        descriptions = _load_column_descriptions(database.parent / "database_description")
        tables: list[dict[str, Any]] = []
        combined_text = " ".join(filter(None, [question, evidence]))
        for name, object_type, ddl in objects:
            escaped = str(name).replace("'", "''")
            table_descriptions = descriptions.get(str(name).lower(), {})
            columns = []
            for row in connection.execute(f"PRAGMA table_info('{escaped}')"):
                metadata = table_descriptions.get(str(row[1]).strip().lower(), {})
                column = {
                    "cid": row[0],
                    "name": row[1],
                    "type": row[2],
                    "not_null": bool(row[3]),
                    "default": row[4],
                    "primary_key": bool(row[5]),
                }
                if metadata:
                    column["semantic_name"] = metadata.get("column_name") or None
                    column["description"] = metadata.get("column_description") or None
                    column["value_description"] = metadata.get("value_description") or None
                    column["documented_format"] = metadata.get("data_format") or None
                columns.append(column)
            foreign_keys = [
                {
                    "id": row[0],
                    "sequence": row[1],
                    "target_table": row[2],
                    "from_column": row[3],
                    "to_column": row[4],
                }
                for row in connection.execute(f"PRAGMA foreign_key_list('{escaped}')")
            ]
            value_matches = _find_question_value_matches(
                connection,
                str(name),
                columns,
                combined_text,
            )
            tables.append(
                {
                    "name": name,
                    "type": object_type,
                    "ddl": ddl,
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                    "question_value_matches": value_matches,
                }
            )
        fingerprint_source = "\n".join(str(item[2] or "") for item in objects)
        return {
            "dialect": "sqlite",
            "database_path": str(database),
            "schema_fingerprint": hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest(),
            "tables": tables,
        }
    finally:
        connection.close()


def _load_column_descriptions(description_root: Path) -> dict[str, dict[str, dict[str, str]]]:
    result: dict[str, dict[str, dict[str, str]]] = {}
    if not description_root.is_dir():
        return result
    for path in description_root.glob("*.csv"):
        table = result.setdefault(path.stem.lower(), {})
        content = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
            try:
                content = path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if not content:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        try:
            reader = csv.DictReader(content.splitlines())
            for row in reader:
                original = (row.get("original_column_name") or "").strip()
                if original:
                    table[original.lower()] = {
                        key: (row.get(key) or "").strip()
                        for key in (
                            "column_name",
                            "column_description",
                            "data_format",
                            "value_description",
                        )
                    }
        except Exception:
            pass
    return result


def _find_question_value_matches(
    connection: sqlite3.Connection,
    table_name: str,
    columns: list[dict[str, Any]],
    question: str | None,
) -> list[dict[str, Any]]:
    if not question:
        return []
    matches: list[dict[str, Any]] = []
    table_sql = _quote_identifier(table_name)
    for column in columns:
        declared_type = str(column.get("type") or "").upper()
        if not any(kind in declared_type for kind in ("CHAR", "CLOB", "TEXT", "DATE")):
            continue
        column_name = str(column["name"])
        column_sql = _quote_identifier(column_name)
        sql = (
            f"SELECT DISTINCT CAST({column_sql} AS TEXT) FROM {table_sql} "
            f"WHERE {column_sql} IS NOT NULL "
            f"AND length(trim(CAST({column_sql} AS TEXT))) >= 5 "
            f"AND instr(lower(?), lower(trim(CAST({column_sql} AS TEXT)))) > 0 LIMIT 20"
        )
        for (value,) in connection.execute(sql, (question,)):
            matches.append({"column": column_name, "value": value, "match": "value_in_question"})
    # A shorter stored value can be a textual substring of a more specific value
    # from the same column. Keeping both makes the shorter value look like an
    # equally exact grounding. Retain only maximal matches per column.
    maximal: list[dict[str, Any]] = []
    for match in matches:
        value = str(match.get("value") or "").strip().casefold()
        column = str(match.get("column") or "")
        if any(
            column == str(other.get("column") or "")
            and value != str(other.get("value") or "").strip().casefold()
            and value in str(other.get("value") or "").strip().casefold()
            for other in matches
        ):
            continue
        maximal.append(match)
    return maximal


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
