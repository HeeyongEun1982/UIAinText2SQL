from __future__ import annotations
from clarification_v2.models.source_analysis import AmbiguityHint
from clarification_v2.runtime.decision_discovery.contract_detector import ContractDetector

SYSTEM = r'''You are the Clarification Discovery Auditor.
You may be called before the first clarification turn or after a user answer.

You receive the original question, source observations, the CURRENT schema-free CQD state, and already resolved user decisions.
Find only NEW unresolved USER-OWNED ambiguities that could materially change query semantics.
Do not rewrite the CQD and do not choose an interpretation.
Use supplied benchmark_evidence as supporting context: do not report an ambiguity that the evidence already resolves unless it conflicts with the question. Never use or request Gold CQD or Gold SQL, and do not invent schema facts absent from the supplied evidence.

Allowed families:
SEMANTIC_DIMENSION, ENTITY_BOUNDARY, BOOLEAN_ATTACHMENT, METRIC_DEFINITION,
CALCULATION_FORMULA, AGGREGATE_TARGET, GROUPING_GRAIN, SELECTION_SCOPE,
TIME_ATTACHMENT, COMPARISON_DIRECTION, OUTPUT_EXPECTATION, SET_RELATION, TIE_POLICY.

Audit especially for:
- a phrase that could be one named entity versus multiple semantic parts,
- parallel labels that may belong to one dimension versus independent dimensions,
- AND/OR/NOT attachment,
- metric/formula/aggregate target,
- global versus per-group grain or selection scope,
- time attachment,
- comparison operand direction,
- result multiplicity/tie policy/set relation.

If the current CQD has already guessed one plausible meaning for a materially ambiguous phrase, you MAY still emit a hint targeting the existing CQD object so registered candidates can replace it. This is not a CQD rewrite; it is an audit finding.
Do not reopen an already resolved choice unless the current state creates a genuine contradiction.

Each target_path must be selected verbatim from allowed_target_paths. Paths are relative to the
CQD root; never prefix them with current_cqd. Array indexes are zero-based.
Return JSON only:
{"ambiguity_hints": [{"hint_id":"...","family":"...","source_span":"...","description":"...","target_path":"/..."}, ...]}
Return an empty list only when no additional material ambiguity is visible.
'''


class DiscoveryAuditor:
    def __init__(self, *, model, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    def audit(self, *, analysis, cqd: dict, decision_events: list) -> list[AmbiguityHint]:
        allowed_target_paths = ContractDetector._object_paths(cqd)
        payload = {
            "original_question": analysis.original_question,
            "source_observations": analysis.observations,
            "current_cqd": cqd,
            "allowed_target_paths": allowed_target_paths,
            "resolved_decisions": [
                {"decision_id": e.decision_id, "candidate_id": e.candidate_id, "answer_text": e.answer_text}
                for e in decision_events
            ],
        }
        out = self.model.complete_json(
            agent="discovery_auditor",
            system=SYSTEM,
            payload=payload,
            model=self.model_name,
        )
        raw = out.get("ambiguity_hints", [])
        try:
            return self._validated_hints(raw, allowed_target_paths)
        except (TypeError, ValueError) as exc:
            repaired = self.model.complete_json(
                agent="discovery_auditor",
                system=SYSTEM + "\nCorrect the previous output according to validation_error.",
                payload={**payload, "previous_output": out, "validation_error": str(exc)},
                model=self.model_name,
            )
            return self._validated_hints(
                repaired.get("ambiguity_hints", []), allowed_target_paths, strict=False
            )

    @staticmethod
    def _validated_hints(
        raw, allowed_target_paths: list[str], *, strict: bool = True
    ) -> list[AmbiguityHint]:
        if not isinstance(raw, list):
            raise TypeError("DiscoveryAuditor ambiguity_hints must be a list")
        hints = []
        for item in raw:
            if not isinstance(item, dict):
                if strict:
                    raise TypeError("DiscoveryAuditor hints must be objects")
                continue
            try:
                hint = AmbiguityHint(**item)
            except (TypeError, KeyError) as exc:
                if strict:
                    raise ValueError("DiscoveryAuditor hint fields are incomplete") from exc
                continue
            if hint.target_path not in allowed_target_paths:
                if strict:
                    raise ValueError(
                        f"target_path {hint.target_path!r} is not an allowed CQD semantic object"
                    )
                continue
            hints.append(hint)
        return hints
