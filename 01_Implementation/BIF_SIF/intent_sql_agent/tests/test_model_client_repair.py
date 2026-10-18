import json
import sys
import unittest
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.model_client import ModelProfile, OpenAIChatModelClient, _build_chat_request
from intent_sql_agent.agents.suite import (
    E2_INTERACTION_SCHEMA,
    _assembly_bundle_schema,
    _bif_analysis_schema,
    _bif_analysis_model_schema,
)
from intent_sql_agent.services.contract_validator import ContractError, validate_contract


class StubClient(OpenAIChatModelClient):
    def __init__(self, responses):
        self.responses = iter(responses)

    def _call(self, **kwargs):
        return next(self.responses)


class ModelClientRepairTest(unittest.TestCase):
    def test_json_repair_is_not_limited_to_a_fixed_attempt_count(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        client = StubClient([
            json.dumps({"wrong": 1}),
            json.dumps({"wrong": 2}),
            json.dumps({"wrong": 3}),
            json.dumps({"answer": "recovered"}),
        ])
        result = client.complete_json(
            agent_id="A1",
            system_prompt="test",
            payload={},
            output_schema=schema,
            contract_name="test",
        )
        self.assertEqual("recovered", result["answer"])

    def test_text_completion_retries_empty_responses(self) -> None:
        client = StubClient(["", "```text\n\n```", "usable"])
        result = client.complete_text(
            agent_id="A2", system_prompt="test", payload={}
        )
        self.assertEqual("usable", result)

    def test_assembly_model_cannot_claim_decision_receipt(self) -> None:
        schema = _assembly_bundle_schema(
            {"type": "object", "additionalProperties": True},
            frame_key="confirmed_sif",
            decision_ids=["sif_choice_1"],
        )
        validate_contract({"confirmed_sif": {}}, schema, "model assembly")
        with self.assertRaises(ContractError):
            validate_contract(
                {
                    "confirmed_sif": {},
                    "applied_decision_ids": ["bif_inherited_choice"],
                },
                schema,
                "stage-local receipt",
            )

    def test_empty_stage_assembly_also_excludes_model_receipt(self) -> None:
        schema = _assembly_bundle_schema(
            {"type": "object", "additionalProperties": True},
            frame_key="confirmed_sif",
            decision_ids=[],
        )
        validate_contract({"confirmed_sif": {}}, schema, "empty assembly")
        with self.assertRaises(ContractError):
            validate_contract(
                {"confirmed_sif": {}, "applied_decision_ids": ["bif_choice"]},
                schema,
                "empty receipt",
            )

    def test_a1_schema_does_not_include_or_mutate_clarification_control(self) -> None:
        required_before = list(
            E2_INTERACTION_SCHEMA["properties"]["clarification_request"]["required"]
        )
        model_schema = _bif_analysis_model_schema({"type": "object", "properties": {}})
        required_after = E2_INTERACTION_SCHEMA["properties"][
            "clarification_request"
        ]["required"]
        self.assertEqual(required_before, required_after)
        self.assertIn("request_id", required_after)
        self.assertNotIn("gap_analysis", model_schema["properties"])

    def test_a1_model_contract_contains_observations_without_control_fields(self) -> None:
        bif_schema = {
            "type": "object",
            "additionalProperties": True,
            "required": ["frame_id", "decision_objective"],
            "properties": {
                "frame_id": {"type": "string"},
                "decision_objective": {"type": "object"},
            },
        }
        model_schema = _bif_analysis_model_schema(bif_schema)
        self.assertIn("issue_observations", model_schema["required"])
        analysis = {
            "draft_bif": {
                "frame_id": "f1",
                "decision_objective": {"summary": "List values"},
            },
            "issue_observations": {"candidates": []},
        }
        validate_contract(analysis, model_schema, "model BIF analysis")

    def test_gpt5_request_uses_compatible_parameters(self) -> None:
        request = _build_chat_request(
            profile=ModelProfile(max_tokens=8192),
            model="gpt-5-mini",
            system_prompt="system",
            user_prompt="user",
        )
        self.assertEqual(8192, request["max_completion_tokens"])
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("temperature", request)
        self.assertNotIn("logprobs", request)

    def test_gpt41_request_preserves_existing_parameters(self) -> None:
        request = _build_chat_request(
            profile=ModelProfile(max_tokens=4096, temperature=0.0, logprobs=True),
            model="gpt-4.1-mini",
            system_prompt="system",
            user_prompt="user",
        )
        self.assertEqual(4096, request["max_tokens"])
        self.assertEqual(0.0, request["temperature"])
        self.assertTrue(request["logprobs"])
        self.assertNotIn("max_completion_tokens", request)

    def test_recovers_corrected_json_wrapped_in_invalid_output(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["options"],
            "properties": {
                "options": {"type": "array", "minItems": 2}
            },
        }
        invalid = json.dumps({"options": []})
        corrected = json.dumps({"options": ["yes", "no"]})
        wrapped = json.dumps(
            {
                "invalid_output": corrected,
                "validation_error": "options too short",
                "required_json_schema": schema,
                "instruction": "Return corrected JSON",
            }
        )
        client = StubClient([invalid, wrapped])
        result = client.complete_json(
            agent_id="A5",
            system_prompt="test",
            payload={},
            output_schema=schema,
            contract_name="test",
        )
        self.assertEqual(["yes", "no"], result["options"])

    def test_recovers_corrected_object_wrapped_in_invalid_output(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        invalid = json.dumps({"wrong": True})
        wrapped = json.dumps({"invalid_output": {"answer": "yes"}})
        client = StubClient([invalid, wrapped])
        result = client.complete_json(
            agent_id="A1",
            system_prompt="test",
            payload={},
            output_schema=schema,
            contract_name="test",
        )
        self.assertEqual("yes", result["answer"])


if __name__ == "__main__":
    unittest.main()
