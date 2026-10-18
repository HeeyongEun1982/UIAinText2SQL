from __future__ import annotations
import json
import re


_COORDINATION_STOP_WORDS = {
    "and", "are", "the", "this", "that", "these", "those", "only",
    "select", "selected", "use", "using", "with", "from", "into", "for",
    "one", "choice",
}

class CandidateCompletenessGate:
    """Structural gate before a decision is allowed into the user-facing queue."""
    def check(self, decision) -> tuple[bool, str]:
        if len(decision.candidates) < 2:
            return False, "fewer_than_two_candidates"
        summaries = [c.user_summary.strip().lower() for c in decision.candidates]
        if len(set(summaries)) != len(summaries):
            return False, "duplicate_candidate_meanings"
        effects = []
        for c in decision.candidates:
            effect = c.typed_effect
            if effect.get("operation") not in {"add", "replace", "remove"}:
                return False, "invalid_effect_operation"
            target = effect.get("target_ref", "")
            if not target.startswith("/"):
                return False, "invalid_effect_target"
            effects.append(json.dumps(effect, ensure_ascii=False, sort_keys=True))
        if len(set(effects)) < 2:
            return False, "candidate_effects_not_distinct"
        if self._narrows_explicit_conjunction(decision):
            return False, "explicit_conjunction_narrowed_to_exclusive_choices"
        return True, "passed"

    @staticmethod
    def _narrows_explicit_conjunction(decision) -> bool:
        """Reject A-vs-B choices when the source explicitly requires A and B.

        This is intentionally limited to selection-scope decisions. Other uses of
        ``and`` (ranges, formulae, prose) belong to their respective contracts.
        Distinct source tokens are used instead of domain names or schema values.
        """
        if decision.family != "SELECTION_SCOPE":
            return False
        source = str(decision.source_span or "")
        parts = re.split(r"\band\b", source, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) != 2 or re.search(r"\bbetween\b", source, re.IGNORECASE):
            return False

        def tokens(text: str) -> set[str]:
            return {
                token.casefold()
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)
                if token.casefold() not in _COORDINATION_STOP_WORDS
            }

        left, right = tokens(parts[0]), tokens(parts[1])
        common = left & right
        left -= common
        right -= common
        if not left or not right:
            return False

        coverage: list[tuple[bool, bool]] = []
        for candidate in decision.candidates:
            candidate_text = " ".join((
                candidate.user_summary,
                json.dumps(candidate.semantic_fragment, ensure_ascii=False),
                json.dumps(candidate.typed_effect.get("value"), ensure_ascii=False),
            )).casefold()
            candidate_tokens = tokens(candidate_text)
            coverage.append((bool(left & candidate_tokens), bool(right & candidate_tokens)))

        has_left_only = any(has_left and not has_right for has_left, has_right in coverage)
        has_right_only = any(has_right and not has_left for has_left, has_right in coverage)
        has_combined = any(has_left and has_right for has_left, has_right in coverage)
        return has_left_only and has_right_only and not has_combined
