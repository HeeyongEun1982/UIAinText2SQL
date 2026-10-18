import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.agents import AgentSuite


class CaptureClient:
    def __init__(self) -> None:
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return {}

    def complete_text(self, **kwargs):
        self.calls.append(kwargs)
        return "SELECT 1"


class SchemaCallPolicyTest(unittest.TestCase):
    def test_full_db_schema_is_limited_to_a4_one_shot_a6_and_system_grounding(self) -> None:
        client = CaptureClient()
        suite = AgentSuite(client, bif_schema={}, sif_schema={})
        case = {"question_id": 1, "db_id": "db", "question": "question"}
        schema = {"tables": [{"name": "items"}]}

        suite.a4_sif_draft(case, {}, schema)
        suite.a5_sif_discover(case, {}, {}, [], [])
        suite.a6_sif_assemble({}, {}, [], schema, [])
        suite.a7_generate_sql({}, schema)
        suite.a8_verify_or_repair(sif={}, schema_catalog=schema, sql="SELECT 1")
        suite.a6_grounding_patch({}, {}, schema, [])

        payloads = {call["agent_id"] + f"_{index}": call["payload"] for index, call in enumerate(client.calls)}
        self.assertIn("schema_catalog", payloads["A4_0"])
        self.assertNotIn("schema_catalog", payloads["A5_DISCOVERY_1"])
        self.assertIn("schema_catalog", payloads["A6_2"])
        self.assertNotIn("schema_catalog", payloads["A7_3"])
        self.assertNotIn("schema_catalog", payloads["A8_4"])
        self.assertIn("schema_catalog", payloads["A6_5"])


if __name__ == "__main__":
    unittest.main()
