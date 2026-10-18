from __future__ import annotations

import re

from clarification_v2.models.contract_assessment import ASSESSMENT_STATUSES
from clarification_v2.models.source_analysis import AmbiguityHint


def semantic_key(family: str, source_span: str) -> tuple[str, str]:
    normalized_span = re.sub(r"\s+", " ", source_span).strip().casefold()
    return family.upper(), normalized_span


class ContractCoverageService:
    def __init__(self, *, registry, detector) -> None:
        self.registry = registry
        self.detector = detector

    def inspect(self, *, analysis, cqd: dict):
        assessments = [
            contract.detect(detector=self.detector, analysis=analysis, cqd=cqd)
            for contract in self.registry.contracts()
        ]
        self.validate_complete(assessments)
        return assessments

    def validate_complete(self, assessments) -> None:
        expected = set(self.registry.families())
        actual = [assessment.family for assessment in assessments]
        duplicates = sorted({family for family in actual if actual.count(family) > 1})
        missing = sorted(expected - set(actual))
        unexpected = sorted(set(actual) - expected)
        invalid = [
            assessment.family for assessment in assessments
            if assessment.status not in ASSESSMENT_STATUSES or not assessment.reason.strip()
        ]
        if missing or duplicates or unexpected or invalid or len(actual) != len(expected):
            raise RuntimeError(
                "Incomplete clarification contract coverage: "
                f"missing={missing}, duplicates={duplicates}, unexpected={unexpected}, "
                f"invalid={invalid}, expected={len(expected)}, actual={len(actual)}"
            )

    def detected_hints(self, assessments) -> list[AmbiguityHint]:
        hints = []
        seen: set[tuple[str, str]] = set()
        seen_targets: set[str] = set()
        for assessment in assessments:
            if assessment.status != "detected":
                continue
            for index, finding in enumerate(assessment.findings, start=1):
                key = semantic_key(assessment.family, finding.source_span)
                if key in seen or finding.target_path in seen_targets:
                    continue
                seen.add(key)
                seen_targets.add(finding.target_path)
                hints.append(AmbiguityHint(
                    hint_id=f"contract-{assessment.family.lower()}-{index}",
                    family=assessment.family,
                    source_span=finding.source_span,
                    description=finding.reason,
                    target_path=finding.target_path,
                ))
        return hints
