from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


class SchemaCatalogLoader:
    """Build the metadata subset required by linking; never reads Gold SQL/evidence."""

    def load(self, database_dir: Path) -> dict:
        database_dir = database_dir.resolve()
        database_name = database_dir.name
        sqlite_path = database_dir / f"{database_name}.sqlite"
        if not sqlite_path.exists():
            raise FileNotFoundError(f"Database file not found: {sqlite_path}")

        descriptions = self._descriptions(database_dir / "database_description")
        tables = []
        connection = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
        try:
            table_names = [
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table_name in table_names:
                columns = []
                escaped = table_name.replace("'", "''")
                for row in connection.execute(f"PRAGMA table_info('{escaped}')"):
                    column_name = str(row[1])
                    metadata = descriptions.get((table_name.casefold(), column_name.casefold()), {})
                    columns.append({
                        "name": column_name,
                        "type": str(row[2] or ""),
                        "semantic_name": metadata.get("column_name") or None,
                        "description": metadata.get("column_description") or column_name,
                        "value_description": metadata.get("value_description") or None,
                    })
                foreign_keys = []
                for row in connection.execute(f"PRAGMA foreign_key_list('{escaped}')"):
                    foreign_keys.append({
                        "from_column": str(row[3]),
                        "to_table": str(row[2]),
                        "to_column": str(row[4]),
                    })
                tables.append({
                    "name": table_name,
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                })
        finally:
            connection.close()
        return {
            "database_id": database_name,
            "database_path": str(sqlite_path),
            "source": "sqlite_schema+database_description",
            "tables": tables,
        }

    @staticmethod
    def _descriptions(description_dir: Path) -> dict[tuple[str, str], dict]:
        result = {}
        if not description_dir.exists():
            return result
        for csv_path in description_dir.glob("*.csv"):
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    original = str(row.get("original_column_name", "")).strip()
                    if original:
                        result[(csv_path.stem.casefold(), original.casefold())] = row
        return result
