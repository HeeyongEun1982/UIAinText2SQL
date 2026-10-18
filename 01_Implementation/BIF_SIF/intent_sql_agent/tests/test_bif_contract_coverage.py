import sys
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PROJECT_SRC))

from intent_sql_agent.services.bif_contract_coverage import (
    BIF_CONTRACTS,
    BIFContractCoverageService,
)


class BIFContractCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bif = {
            "decision_objective": {"summary": "Return identifiers"},
            "target_population": {
                "primary_entity": "records",
                "inclusion_conditions": [],
            },
            "output_expectation": {"fields": [{"label": "identifier"}]},
        }

    def test_all_thirteen_contracts_execute(self) -> None:
        calls = []

        def detector(**kwargs):
            calls.append(kwargs["contract"]["family"])
            return {
                "family": kwargs["contract"]["family"],
                "status": "not_applicable",
                "disposition": "none",
                "source_spans": [],
                "reason": "No relevant construct in this synthetic request.",
                "findings": [],
            }

        assessments = BIFContractCoverageService().inspect(
            original_question="Return record identifiers.",
            bif=self.bif,
            detector=detector,
        )
        self.assertEqual(len(BIF_CONTRACTS), 13)
        self.assertEqual(len(assessments), 13)
        self.assertEqual(set(calls), {item.family for item in BIF_CONTRACTS})

    def test_incomplete_coverage_is_program_error(self) -> None:
        service = BIFContractCoverageService()
        assessments = [
            {
                "family": item.family,
                "status": "safe",
                "disposition": "none",
                "reason": "Checked.",
            }
            for item in BIF_CONTRACTS[:-1]
        ]
        with self.assertRaisesRegex(RuntimeError, "Incomplete BIF contract coverage"):
            service.validate_complete(assessments)

    def test_detected_finding_becomes_existing_path_observation(self) -> None:
        def detector(**kwargs):
            family = kwargs["contract"]["family"]
            if family == "ENTITY_BOUNDARY":
                return {
                    "family": family,
                    "status": "detected",
                    "disposition": "bif_clarification",
                    "source_spans": ["records"],
                    "reason": "Two business entity readings remain.",
                    "findings": [{
                        "source_span": "records",
                        "reason": "Choose the intended business entity boundary.",
                        "subject_ref": "/target_population/primary_entity",
                        "alternatives": ["One collective population", "Individual records"],
                    }],
                }
            return {
                "family": family,
                "status": "not_applicable",
                "disposition": "none",
                "source_spans": [],
                "reason": "No relevant construct.",
                "findings": [],
            }

        service = BIFContractCoverageService()
        assessments = service.inspect(
            original_question="Return records.", bif=self.bif, detector=detector
        )
        observations = service.detected_observations(assessments)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["issue_type"], "TARGET_POPULATION")
        self.assertEqual(
            observations[0]["subject_ref"], "/target_population/primary_entity"
        )

    def test_unknown_bif_path_is_rejected(self) -> None:
        calls = []

        def detector(**kwargs):
            family = kwargs["contract"]["family"]
            calls.append(family)
            return {
                "family": family,
                "status": "detected",
                "disposition": "bif_clarification",
                "source_spans": ["records"],
                "reason": "Synthetic ambiguity.",
                "findings": [{
                    "source_span": "records",
                    "reason": "Synthetic ambiguity.",
                    "subject_ref": "/missing/path",
                    "alternatives": ["First", "Second"],
                }],
            }

        with self.assertRaisesRegex(RuntimeError, "unknown BIF path"):
            BIFContractCoverageService().inspect(
                original_question="Return records.", bif=self.bif, detector=detector
            )
        self.assertEqual(set(calls), {item.family for item in BIF_CONTRACTS})

    def test_invalid_assessment_is_repaired_with_validation_feedback(self) -> None:
        attempts = {}

        def detector(**kwargs):
            family = kwargs["contract"]["family"]
            attempts[family] = attempts.get(family, 0) + 1
            if family == "ENTITY_BOUNDARY" and attempts[family] == 1:
                return {
                    "family": family,
                    "status": "detected",
                    "disposition": "bif_clarification",
                    "source_spans": ["records"],
                    "reason": "Synthetic ambiguity.",
                    "findings": [{
                        "source_span": "records", "reason": "Synthetic ambiguity.",
                        "subject_ref": "/missing/path",
                        "alternatives": ["First", "Second"],
                    }],
                }
            if family == "ENTITY_BOUNDARY":
                self.assertIn("unknown BIF path", kwargs["validation_error"])
            return {
                "family": family,
                "status": "not_applicable",
                "disposition": "none",
                "source_spans": [],
                "reason": "No relevant construct.",
                "findings": [],
            }

        assessments = BIFContractCoverageService().inspect(
            original_question="Return records.", bif=self.bif, detector=detector
        )
        self.assertEqual(len(assessments), 13)
        self.assertEqual(attempts["ENTITY_BOUNDARY"], 2)


if __name__ == "__main__":
    unittest.main()
