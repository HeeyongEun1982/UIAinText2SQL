from __future__ import annotations

from clarification_v2.runtime.decision_discovery.contract_coverage import semantic_key

class DecisionDiscoveryService:
    def __init__(self, *, matcher, candidate_builder, candidate_gate, materiality) -> None:
        self.matcher = matcher
        self.candidate_builder = candidate_builder
        self.candidate_gate = candidate_gate
        self.materiality = materiality

    def discover(self, *, analysis, cqd: dict, known_keys: set[tuple[str, str]]):
        decisions = []
        for hint, contract in self.matcher.match(analysis):
            key = semantic_key(contract.family, hint.source_span)
            if key in known_keys:
                continue
            decision = self.candidate_builder.build(analysis=analysis, cqd=cqd, hint=hint, contract=contract)
            passed, reason = self.candidate_gate.check(decision)
            if not passed:
                decision.materiality_status = "candidate_incomplete"
                decision.materiality_proof = reason
                decision.status = "invalidated"
                continue
            decision = self.materiality.evaluate(cqd=cqd, decision=decision)
            if decision.materiality_status == "material":
                decisions.append(decision)
        return decisions

    def reassess_pending(self, *, cqd: dict, decisions):
        for decision in decisions:
            if decision.status == "pending":
                self.materiality.evaluate(cqd=cqd, decision=decision)
        return decisions
