import sqlite3
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVISED_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from intent_sql_agent.services.sif_sql_compiler import UnsupportedSIFError, compile_sif


class SifSqlCompilerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = REVISED_ROOT / "data/bird_dev/dev_databases/california_schools/california_schools.sqlite"

    def test_compiles_joined_q2_intent_to_five_rows(self) -> None:
        sif = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [
                    {"source_id": "frpm", "kind": "table", "name": "frpm", "alias": "f"},
                    {"source_id": "schools", "kind": "table", "name": "schools", "alias": "s"},
                ],
                "joins": [{
                    "type": "inner", "left_source": "f", "right_source": "s",
                    "predicates": [{
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "f", "name": "CDSCode"},
                        "right": {"kind": "column", "source": "s", "name": "CDSCode"},
                    }],
                }],
                "where": [{
                    "kind": "binary", "operator": "and",
                    "left": {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "f", "name": "Charter School (Y/N)"},
                        "right": {"kind": "literal", "value": 1},
                    },
                    "right": {
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "f", "name": "District Name"},
                        "right": {"kind": "literal", "value": "Fresno County Office of Education"},
                    },
                }],
                "projection": [{
                    "label": "Zip",
                    "expression": {"kind": "column", "source": "s", "name": "Zip"},
                }],
                "distinct": False,
                "group_by": [], "having": [], "order_by": [], "windows": [],
                "set_operations": [], "limit": None,
            }],
        }
        sql = compile_sif(sif)
        connection = sqlite3.connect(self.database)
        try:
            rows = connection.execute(sql).fetchall()
        finally:
            connection.close()
        self.assertEqual(5, len(rows))

    def test_unsupported_raw_sql_hint_requires_model_fallback(self) -> None:
        sif = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main", "sources": [], "joins": [], "where": [],
                "projection": [{"expression": {"kind": "raw_sql_hint", "value": "x"}}],
                "distinct": False, "group_by": [], "having": [], "order_by": [],
                "windows": [], "set_operations": [], "limit": None,
            }],
        }
        with self.assertRaises(UnsupportedSIFError):
            compile_sif(sif)

    def test_join_references_may_use_source_ids_when_aliases_exist(self) -> None:
        sif = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [
                    {"source_id": "schools_table", "kind": "table", "name": "schools", "alias": "s"},
                    {"source_id": "frpm_table", "kind": "table", "name": "frpm", "alias": "f"},
                ],
                "joins": [{
                    "type": "inner", "left_source": "schools_table", "right_source": "frpm_table",
                    "predicates": [{
                        "kind": "binary", "operator": "=",
                        "left": {"kind": "column", "source": "s", "name": "CDSCode"},
                        "right": {"kind": "column", "source": "f", "name": "CDSCode"},
                    }],
                }],
                "where": [],
                "projection": [{"expression": {"kind": "column", "source": "s", "name": "City"}}],
                "distinct": False, "group_by": [], "having": [], "order_by": [],
                "windows": [], "set_operations": [], "limit": None,
            }],
        }
        sql = compile_sif(sif)
        self.assertIn('INNER JOIN "frpm" AS "f"', sql)

    def test_compiles_object_between_and_is_not_null(self) -> None:
        sif = {
            "main_block_id": "main",
            "query_blocks": [{
                "block_id": "main",
                "sources": [{"source_id": "schools", "kind": "table", "name": "schools", "alias": "s"}],
                "joins": [],
                "where": [
                    {"kind": "binary", "operator": "BETWEEN", "left": {"kind": "column", "source": "s", "name": "OpenDate"}, "right": {"kind": "literal", "value": {"start": "2009-01-01", "end": "2010-12-31"}}},
                    {"kind": "binary", "operator": "IS NOT", "left": {"kind": "column", "source": "s", "name": "AdmEmail1"}, "right": {"kind": "literal", "value": None}},
                ],
                "projection": [{"expression": {"kind": "column", "source": "s", "name": "AdmEmail1"}}],
                "distinct": False, "group_by": [], "having": [], "order_by": [],
                "windows": [], "set_operations": [], "limit": None,
            }],
        }
        sql = compile_sif(sif)
        self.assertIn("BETWEEN '2009-01-01' AND '2010-12-31'", sql)
        self.assertIn("IS NOT NULL", sql)


if __name__ == "__main__":
    unittest.main()
