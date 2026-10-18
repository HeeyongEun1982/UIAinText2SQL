from __future__ import annotations

import unittest
import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from clarification_v2.runtime.decision_discovery.contract_registry import ContractRegistry
from clarification_v2.models.decision import Candidate, DecisionSpec
from intent_sql_agent.pipeline import FullCaseRunner, PipelineRunError
from intent_sql_agent.services.benchmark_semantic_e2 import BenchmarkOracleSemanticE2
from intent_sql_agent.services.cqd_bif_layout import (
    complete_bif_layout_structure,
    validate_cqd_bif_layout_coverage,
)
from intent_sql_agent.services.sif_contract_normalization import normalize_sif_contract_shape


class SemanticFrontendIntegrationTests(unittest.TestCase):
    def test_semantic_e2_uses_deterministic_gold_boolean_resolution(self) -> None:
        class Delegate:
            def answer(self, **_kwargs):
                raise AssertionError("deterministic oracle resolution should avoid model fallback")

        decision = DecisionSpec(
            decision_id="logic", contract_id="SEMANTIC_DIMENSION",
            family="SEMANTIC_DIMENSION",
            source_span="Middle Schools and Unified Schools", description="logic",
            candidates=[
                Candidate("or", "Treat the values as alternatives", {
                    "operation": "replace", "target_ref": "/predicates", "value": "or"
                }),
                Candidate("and", "Apply the conditions simultaneously", {
                    "operation": "replace", "target_ref": "/predicates", "value": "and"
                }),
            ],
        )
        e2 = BenchmarkOracleSemanticE2(
            delegate=Delegate(),
            gold_sql="SELECT x FROM t WHERE t.SOC=62 AND t.DOC=54",
            original_question="schools that are Middle Schools and Unified Schools",
        )
        answer = e2.answer(
            question_text="Should both conditions apply simultaneously or be alternatives?",
            decision=decision,
        )
        self.assertEqual("Apply the conditions simultaneously", answer["answer_text"])

    def test_vendored_frontend_keeps_all_thirteen_contracts(self) -> None:
        self.assertEqual(13, len(ContractRegistry.default().families()))

    def test_confirmed_cqd_leaves_require_existing_bif_layout_roots(self) -> None:
        cqd = {
            "objective": {"description": "List schools"},
            "predicates": {
                "root": {"operator": "and", "children": ["p1"]},
                "atoms": [{"predicate_id": "p1", "value": "Adelanto"}],
            },
            "outputs": [{"concept": "grade span"}],
            "traceability": {"state_hash": "abc"},
        }
        bif = {
            "decision_objective": {"summary": "List schools"},
            "target_population": {"inclusion_conditions": [{"value": "Adelanto"}]},
            "output_expectation": {"fields": [{"label": "Grade span"}]},
        }
        report = validate_cqd_bif_layout_coverage(cqd, bif)
        self.assertEqual("complete", report["status"])
        self.assertEqual("abc", report["confirmed_cqd_hash"])

    def test_missing_cqd_output_mapping_is_a_hard_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "BIF_LAYOUT_COVERAGE_FAILED"):
            validate_cqd_bif_layout_coverage(
                {"outputs": [{"concept": "zip"}]},
                {"output_expectation": {}},
            )

    def test_list_output_does_not_invent_a_presentation_choice(self) -> None:
        completed, repairs = complete_bif_layout_structure(
            {"outputs": [{"output_type": "list"}]},
            {"output_expectation": {"fields": [{"presentation": "unresolved"}]}},
        )
        self.assertEqual(
            "unresolved",
            completed["output_expectation"]["fields"][0]["presentation"],
        )
        self.assertEqual([], repairs)

    def test_order_by_model_variation_is_normalized_before_contract_check(self) -> None:
        normalized, repairs = normalize_sif_contract_shape({
            "query_blocks": [{
                "order_by": [{
                    "kind": "order",
                    "expression": {"kind": "column", "source": "s", "name": "x"},
                    "direction": "desc",
                }]
            }]
        })
        order = normalized["query_blocks"][0]["order_by"][0]
        self.assertNotIn("kind", order)
        self.assertEqual("dialect_default", order["nulls"])
        self.assertEqual(2, len(repairs))

    def test_physical_post_bif_gap_stays_in_sif(self) -> None:
        routed = FullCaseRunner._route_post_bif_candidates([{
            "issue_type": "TARGET_POPULATION",
            "subject_ref": "/target_population/inclusion_conditions/0",
            "description": "Choose the documented stored code mapping for this column.",
            "alternatives": ["Use code 1", "Use code 2"],
        }])
        self.assertEqual("TECHNICAL_GROUNDING", routed[0]["issue_type"])

    def test_new_business_gap_after_confirmed_cqd_is_rejected(self) -> None:
        with self.assertRaisesRegex(PipelineRunError, "SEMANTIC_FRONTEND_INCOMPLETE"):
            FullCaseRunner._route_post_bif_candidates([{
                "issue_type": "TARGET_POPULATION",
                "subject_ref": "/target_population",
                "description": "The requested population has two business meanings.",
                "alternatives": ["Customers", "Suppliers"],
            }])


if __name__ == "__main__":
    unittest.main()
