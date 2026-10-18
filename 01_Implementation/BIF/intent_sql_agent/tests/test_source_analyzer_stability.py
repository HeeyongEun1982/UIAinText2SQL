from __future__ import annotations

import copy
import unittest

from clarification_v2.runtime.source_analysis.source_analyzer import SourceAnalyzer


def _valid_output() -> dict:
    return {
        "observations": {
            "objective": {"description": "return a value"},
            "populations": [{"entity": "record"}],
            "predicates": {
                "root": None,
                "atoms": [{
                    "resolution_status": "unresolved",
                    "source_text": "first record",
                    "semantic_role": "selection",
                    "stable_id": "atom-1",
                }],
            },
            "expressions": [], "aggregations": [], "groupings": [],
            "selections": [], "time_constraints": [], "comparisons": [],
            "outputs": [{"attribute": "value"}], "scopes": [],
            "deferred_grounding": [],
        },
        "ambiguity_hints": [{
            "hint_id": "hint-1", "family": "SELECTION_SCOPE",
            "source_span": "first record", "description": "which first record",
            "target_path": "/observations/predicates/atoms/0",
        }],
    }


class SourceAnalyzerStabilityTests(unittest.TestCase):
    def test_observations_prefixed_pointer_is_normalized_without_retry(self) -> None:
        class Model:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                return copy.deepcopy(_valid_output())

        model = Model()
        result = SourceAnalyzer(model=model, model_name="test").analyze(
            question_id="1", original_question="first record value",
        )
        self.assertEqual(1, model.calls)
        self.assertEqual(
            "/predicates/atoms/0", result.ambiguity_hints[0].target_path,
        )

    def test_structurally_invalid_outputs_continue_after_five_total_attempts(self) -> None:
        class Model:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                return {"observations": {}, "ambiguity_hints": []}

        model = Model()
        result = SourceAnalyzer(model=model, model_name="test").analyze(
            question_id="1", original_question="value",
        )
        self.assertEqual(5, model.calls)
        self.assertEqual(
            "unresolved_system", result.observations["objective"]["resolution_status"]
        )
        self.assertTrue(result.observations["populations"])
        self.assertTrue(result.observations["outputs"])


if __name__ == "__main__":
    unittest.main()
