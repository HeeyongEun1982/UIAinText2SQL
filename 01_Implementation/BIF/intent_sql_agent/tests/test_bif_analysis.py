import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.agents.suite import _bif_analysis_schema
from intent_sql_agent.services.contract_validator import ContractError, validate_contract


class BifAnalysisTest(unittest.TestCase):
    def test_a1_analysis_accepts_empty_issue_observations(self) -> None:
        schema = _bif_analysis_schema({"type": "object", "additionalProperties": True})
        analysis = {
            "draft_bif": {},
            "issue_observations": {"candidates": []},
        }
        validate_contract(analysis, schema, "A1 observation bundle")

    def test_a1_analysis_rejects_clarification_control_fields(self) -> None:
        schema = _bif_analysis_schema({"type": "object", "additionalProperties": True})
        analysis = {
            "draft_bif": {},
            "issue_observations": {"candidates": []},
            "gap_analysis": {"gaps": [], "clarification_request": {}},
        }
        with self.assertRaises(ContractError):
            validate_contract(analysis, schema, "A1 observation bundle")

    def test_issue_observation_cannot_contain_decision_control_fields(self) -> None:
        schema = _bif_analysis_schema({"type": "object", "additionalProperties": True})
        analysis = {
            "draft_bif": {},
            "issue_observations": {"candidates": [{
                "issue_type": "OUTPUT_SHAPE",
                "subject_ref": "/output_expectation",
                "description": "Choose the output shape.",
                "alternatives": ["columns", "rows"],
                "material": True,
                "ownership": "USER",
            }]},
        }
        with self.assertRaises(ContractError):
            validate_contract(analysis, schema, "A1 observation bundle")


if __name__ == "__main__":
    unittest.main()
