from __future__ import annotations

from copy import deepcopy
import re

from clarification_v2.models.link_assessment import (
    LINK_STATUSES,
    LinkAssessment,
    LinkCandidate,
)


LINKABLE_FAMILIES = frozenset({"SEMANTIC_DIMENSION", "ENTITY_BOUNDARY", "SET_RELATION"})
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(value: str) -> set[str]:
    tokens = set()
    for token in _TOKEN.findall(value.casefold()):
        if len(token) <= 1:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _slug(value: str) -> str:
    return "_".join(_TOKEN.findall(value.casefold()))


def _overlaps(left: str, right: str) -> bool:
    a = " ".join(left.casefold().split())
    b = " ".join(right.casefold().split())
    return bool(a and b and (a in b or b in a))


class SchemaLinkService:
    def __init__(self, schema_catalog: dict | None = None, *, threshold: float = 0.65,
                 uniqueness_margin: float = 0.15) -> None:
        self.schema_catalog = schema_catalog
        self.threshold = threshold
        self.uniqueness_margin = uniqueness_margin

    def inspect(self, *, contract_assessments, cqd: dict) -> list[LinkAssessment]:
        results = []
        grounded_spans: list[str] = []
        for assessment in contract_assessments:
            if assessment.status != "detected":
                continue
            for finding in assessment.findings:
                if (
                    assessment.family == "SET_RELATION"
                    and any(_overlaps(finding.source_span, span) for span in grounded_spans)
                ):
                    results.append(LinkAssessment(
                        family=assessment.family,
                        source_span=finding.source_span,
                        target_path=finding.target_path,
                        status="grounded",
                        reason="Resolved by the grounded semantic-dimension split for the same source phrase.",
                    ))
                    continue
                result = self._inspect_finding(
                    family=assessment.family,
                    finding=finding,
                    cqd=cqd,
                )
                results.append(result)
                if result.status == "grounded":
                    grounded_spans.append(result.source_span)
        self.validate_complete(contract_assessments, results)
        return results

    def _inspect_finding(self, *, family: str, finding, cqd: dict) -> LinkAssessment:
        if family not in LINKABLE_FAMILIES:
            return LinkAssessment(
                family=family,
                source_span=finding.source_span,
                target_path=finding.target_path,
                status="not_linkable",
                reason=f"{family} requires user meaning rather than database grounding.",
            )
        if not self.schema_catalog:
            return LinkAssessment(
                family=family,
                source_span=finding.source_span,
                target_path=finding.target_path,
                status="no_match",
                reason="No schema catalog was supplied to the runtime.",
            )
        if family != "SEMANTIC_DIMENSION":
            return LinkAssessment(
                family=family,
                source_span=finding.source_span,
                target_path=finding.target_path,
                status="no_match",
                reason="No unique deterministic schema link was found for this family.",
            )
        return self._link_semantic_dimension(finding=finding, cqd=cqd)

    def _link_semantic_dimension(self, *, finding, cqd: dict) -> LinkAssessment:
        predicates = cqd.get("predicates", {})
        atoms = predicates.get("atoms", [])
        source_atom = next(
            (
                (index, atom) for index, atom in enumerate(atoms)
                if _overlaps(finding.source_span, str(atom.get("source_text", "")))
                and (
                    (isinstance(atom.get("value"), list) and len(atom["value"]) > 1)
                    or atom.get("resolution_status") == "unresolved"
                )
            ),
            None,
        )
        if source_atom is None:
            return LinkAssessment(
                family="SEMANTIC_DIMENSION",
                source_span=finding.source_span,
                target_path=finding.target_path,
                status="no_match",
                reason="No parallel-label predicate atom corresponds to the finding.",
            )

        atom_index, atom = source_atom
        labels = self._parallel_labels(atom, finding.source_span)
        if len(labels) < 2:
            return LinkAssessment(
                family="SEMANTIC_DIMENSION",
                source_span=finding.source_span,
                target_path=finding.target_path,
                status="no_match",
                reason="The parallel source phrase could not be separated into linkable labels.",
            )
        selected = []
        all_candidates = []
        for label in labels:
            ranked = self._rank_columns(str(label))
            all_candidates.extend(ranked[:3])
            if not ranked or ranked[0].score < self.threshold:
                return LinkAssessment(
                    family="SEMANTIC_DIMENSION",
                    source_span=finding.source_span,
                    target_path=finding.target_path,
                    status="no_match",
                    reason=f"No schema metadata match passed threshold for {label!r}.",
                    candidates=all_candidates,
                )
            if len(ranked) > 1 and ranked[0].score - ranked[1].score < self.uniqueness_margin:
                return LinkAssessment(
                    family="SEMANTIC_DIMENSION",
                    source_span=finding.source_span,
                    target_path=finding.target_path,
                    status="ambiguous",
                    reason=f"Multiple schema dimensions match {label!r} without a unique margin.",
                    candidates=all_candidates,
                )
            selected.append((str(label), ranked[0]))

        physical_dimensions = {(item.table, item.column) for _, item in selected}
        if len(physical_dimensions) != len(selected):
            return LinkAssessment(
                family="SEMANTIC_DIMENSION",
                source_span=finding.source_span,
                target_path=finding.target_path,
                status="ambiguous",
                reason="Parallel labels uniquely link to the same database dimension; user relation remains unresolved.",
                candidates=[item for _, item in selected],
            )

        new_predicates = deepcopy(predicates)
        retained = [item for index, item in enumerate(new_predicates.get("atoms", [])) if index != atom_index]
        split_atoms = []
        for offset, (label, candidate) in enumerate(selected, start=1):
            split_atom = deepcopy(atom)
            split_atom["condition_id"] = f"{atom.get('condition_id', atom_index + 1)}.{offset}"
            split_atom["dimension"] = _slug(candidate.business_dimension)
            split_atom["operator"] = "equals"
            split_atom["value"] = label
            split_atom.pop("resolution_status", None)
            split_atom.pop("semantic_role", None)
            split_atom.pop("stable_id", None)
            split_atom["grounding"] = {
                "method": "schema_link",
                "business_dimension": candidate.business_dimension,
                "confidence": candidate.score,
                "table": candidate.table,
                "column": candidate.column,
                "physical_value": candidate.physical_value,
            }
            split_atoms.append(split_atom)
        new_predicates["atoms"] = retained + split_atoms
        new_predicates["root"] = "AND"
        return LinkAssessment(
            family="SEMANTIC_DIMENSION",
            source_span=finding.source_span,
            target_path=finding.target_path,
            status="grounded",
            reason="Each parallel label uniquely links to a different documented schema dimension.",
            candidates=[item for _, item in selected],
            typed_effect={"operation": "replace", "target_ref": "/predicates", "value": new_predicates},
        )

    @staticmethod
    def _parallel_labels(atom: dict, source_span: str) -> list[str]:
        values = atom.get("value")
        if isinstance(values, list):
            return [str(value).strip() for value in values if str(value).strip()]
        phrase = str(atom.get("source_text") or source_span).strip()
        match = re.search(r"\b(?:are|is)\s+(.+)$", phrase, flags=re.IGNORECASE)
        if match:
            phrase = match.group(1)
        return [part.strip(" ,.;?") for part in re.split(r"\s+and\s+", phrase, flags=re.IGNORECASE) if part.strip()]

    def _rank_columns(self, label: str) -> list[LinkCandidate]:
        label_tokens = _tokens(label)
        if not label_tokens:
            return []
        ranked = []
        for table in self.schema_catalog.get("tables", []):
            for column in table.get("columns", []):
                business_dimension = str(
                    column.get("semantic_name") or column.get("description") or column.get("name") or ""
                ).strip()
                metadata = " ".join(str(column.get(key) or "") for key in (
                    "name", "semantic_name", "description", "value_description"
                ))
                score = len(label_tokens & _tokens(metadata)) / len(label_tokens)
                if score:
                    physical_value = self._documented_code(
                        label, str(column.get("value_description") or "")
                    )
                    ranked.append(LinkCandidate(
                        table=str(table.get("name", "")),
                        column=str(column.get("name", "")),
                        business_dimension=business_dimension,
                        matched_value=label,
                        score=round(score, 6),
                        physical_value=physical_value,
                    ))
        return sorted(ranked, key=lambda item: (-item.score, item.table, item.column))

    @staticmethod
    def _documented_code(label: str, value_description: str):
        label_tokens = _tokens(label)
        matches = []
        for line in value_description.splitlines():
            match = re.search(r"\b([A-Za-z0-9]+)\s*[-–—]\s*(.+)$", line.strip())
            if not match:
                continue
            description_tokens = _tokens(match.group(2))
            if not description_tokens:
                continue
            score = len(label_tokens & description_tokens) / max(len(label_tokens), 1)
            if score >= 0.6:
                matches.append((score, match.group(1)))
        if not matches:
            return None
        best_score = max(score for score, _ in matches)
        best_codes = {code for score, code in matches if score == best_score}
        return next(iter(best_codes)) if len(best_codes) == 1 else None

    @staticmethod
    def validate_complete(contract_assessments, link_assessments) -> None:
        expected = [
            (assessment.family, finding.source_span, finding.target_path)
            for assessment in contract_assessments if assessment.status == "detected"
            for finding in assessment.findings
        ]
        actual = [
            (assessment.family, assessment.source_span, assessment.target_path)
            for assessment in link_assessments
        ]
        invalid = [item for item in link_assessments if item.status not in LINK_STATUSES or not item.reason]
        if sorted(expected) != sorted(actual) or invalid:
            raise RuntimeError(
                "Incomplete schema-link coverage: "
                f"expected={len(expected)}, actual={len(actual)}, invalid={len(invalid)}"
            )

    @staticmethod
    def validate_grounded_state(link_assessments, cqd: dict) -> None:
        atoms = cqd.get("predicates", {}).get("atoms", [])
        for assessment in link_assessments:
            if assessment.status != "grounded" or not assessment.typed_effect:
                continue
            for candidate in assessment.candidates:
                dimension = _slug(candidate.business_dimension)
                if not any(
                    atom.get("dimension") == dimension
                    and str(atom.get("value")) == candidate.matched_value
                    for atom in atoms
                ):
                    raise RuntimeError(
                        "Grounded schema link was modified after resolution: "
                        f"family={assessment.family}, dimension={dimension}, "
                        f"value={candidate.matched_value!r}"
                    )
