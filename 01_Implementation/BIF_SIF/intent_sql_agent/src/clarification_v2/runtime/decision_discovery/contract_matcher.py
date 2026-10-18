from __future__ import annotations
from clarification_v2.models.source_analysis import SourceAnalysis

class ContractMatcher:
    def __init__(self, registry) -> None:
        self.registry = registry

    def match(self, analysis: SourceAnalysis):
        matches = []
        for hint in analysis.ambiguity_hints:
            contract = self.registry.get(hint.family)
            matches.append((hint, contract))
        return matches
