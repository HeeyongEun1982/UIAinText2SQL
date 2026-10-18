from __future__ import annotations

import unittest

from clarification_v2.models.decision import Candidate, DecisionSpec
from clarification_v2.runtime.decision_discovery.candidate_gate import CandidateCompletenessGate


def _candidate(candidate_id: str, summary: str, value: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        user_summary=summary,
        semantic_fragment={"selection": value},
        typed_effect={"operation": "replace", "target_ref": "/selections", "value": value},
    )


class CandidateCompletenessGateTests(unittest.TestCase):
    def test_rejects_exclusive_choices_that_drop_explicit_conjuncts(self) -> None:
        decision = DecisionSpec(
            decision_id="D1",
            contract_id="SELECTION_SCOPE",
            family="SELECTION_SCOPE",
            source_span="public Intermediate/Middle Schools and Unified Schools",
            description="Choose a school type",
            candidates=[
                _candidate("C1", "Intermediate only", "Intermediate"),
                _candidate("C2", "Unified only", "Unified"),
            ],
        )
        self.assertEqual(
            (False, "explicit_conjunction_narrowed_to_exclusive_choices"),
            CandidateCompletenessGate().check(decision),
        )

    def test_accepts_candidates_when_one_preserves_combined_scope(self) -> None:
        decision = DecisionSpec(
            decision_id="D2",
            contract_id="SELECTION_SCOPE",
            family="SELECTION_SCOPE",
            source_span="retail customers and wholesale customers",
            description="Resolve scope",
            candidates=[
                _candidate("C1", "Both retail and wholesale", "retail and wholesale"),
                _candidate("C2", "Retail only", "retail"),
            ],
        )
        self.assertEqual((True, "passed"), CandidateCompletenessGate().check(decision))

    def test_does_not_apply_selection_rule_to_time_range(self) -> None:
        decision = DecisionSpec(
            decision_id="D3",
            contract_id="TIME_ATTACHMENT",
            family="TIME_ATTACHMENT",
            source_span="opened between 2009 and 2010",
            description="Resolve time",
            candidates=[
                _candidate("C1", "Calendar dates", "calendar"),
                _candidate("C2", "Academic years", "academic"),
            ],
        )
        self.assertEqual((True, "passed"), CandidateCompletenessGate().check(decision))


if __name__ == "__main__":
    unittest.main()
