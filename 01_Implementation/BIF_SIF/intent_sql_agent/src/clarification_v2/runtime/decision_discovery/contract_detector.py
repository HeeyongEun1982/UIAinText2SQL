from __future__ import annotations

from dataclasses import asdict
from collections.abc import Mapping
import re

from clarification_v2.models.contract_assessment import (
    ASSESSMENT_STATUSES,
    ContractAssessment,
    ContractFinding,
)


SYSTEM = r'''You are one clarification contract detector in a CQD system.
Evaluate exactly the supplied contract family and its detection_rules against the original
question and Initial CQD. Treat every supplied detection rule as a mandatory check.
The Source Analyzer hints are auxiliary evidence only. Inspect the source and CQD independently;
do not assume that a hint is correct and do not return safe merely because no hint exists.
Use supplied benchmark_evidence as supporting context when deciding whether a meaning is already safe or remains ambiguous. Evidence may resolve terminology or intended database concepts, but cannot override the user's explicit wording. Never use Gold SQL or Gold CQD, and never invent schema facts absent from the supplied evidence.

Return exactly one status:
- detected: an unresolved, user-owned ambiguity in this family could materially change meaning.
- safe: this family is present and its meaning/attachment is explicit.
- not_applicable: the family has no relevant construct in the question.

For detected, return one finding per distinct ambiguity. target_path must be an RFC-6901 JSON
pointer to an existing replaceable semantic object in current_cqd. For safe/not_applicable,
findings must be empty. Always give a concrete reason; never silently return an empty object.

Return JSON only:
{"family":"SUPPLIED_FAMILY","status":"detected|safe|not_applicable",
 "source_spans":["exact text"],"reason":"...",
 "findings":[{"source_span":"exact text","reason":"...","target_path":"/..."}]}
'''


def _resolve_pointer(document, pointer: str):
    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"target_path is not an RFC-6901 JSON pointer: {pointer!r}")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"target_path does not exist: {pointer}") from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ValueError(f"target_path does not exist: {pointer}")
    return current


class ContractDetector:
    def __init__(self, *, model, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    def detect(self, *, contract, analysis, cqd: dict) -> ContractAssessment:
        forced = self._structural_assessment(
            contract=contract, cqd=cqd, original_question=analysis.original_question
        )
        if forced is not None:
            return forced

        allowed_target_paths = self._object_paths(cqd)
        auxiliary_hints = []
        for hint in analysis.ambiguity_hints:
            if hint.family.upper() != contract.family:
                continue
            if hint.target_path in allowed_target_paths:
                auxiliary_hints.append(asdict(hint))
        payload = {
            "contract": asdict(contract),
            "original_question": analysis.original_question,
            "source_observations": analysis.observations,
            "current_cqd": cqd,
            "allowed_target_paths": allowed_target_paths,
            "auxiliary_source_hints": auxiliary_hints,
        }
        out = self.model.complete_json(
            agent="contract_detector", system=SYSTEM, payload=payload, model=self.model_name
        )
        repair_index = 0
        while True:
            try:
                return self._assessment_from_output(
                    contract=contract, cqd=cqd,
                    original_question=analysis.original_question, out=out,
                )
            except ValueError as exc:
                repair_index += 1
                out = self.model.complete_json(
                    agent="contract_detector",
                    system=(
                        SYSTEM
                        + "\nYour previous output failed validation. Every source span must be a "
                          "verbatim substring of original_question and every target must come from "
                          "allowed_target_paths. If you cannot ground a detected finding exactly, "
                          "return safe or not_applicable with empty findings."
                    ),
                    payload={
                        **payload, "previous_output": out,
                        "validation_error": str(exc), "repair_attempt": repair_index,
                    },
                    model=self.model_name,
                )
    @staticmethod
    def _structural_assessment(
        *, contract, cqd: dict, original_question: str
    ) -> ContractAssessment | None:
        family = contract.family
        absent_sections = {
            "METRIC_DEFINITION": ("expressions", "aggregations"),
            "CALCULATION_FORMULA": ("expressions",),
            "AGGREGATE_TARGET": ("aggregations",),
            "GROUPING_GRAIN": ("groupings", "aggregations"),
            "SELECTION_SCOPE": ("selections",),
            "COMPARISON_DIRECTION": ("comparisons",),
            "TIE_POLICY": ("selections",),
        }
        sections = absent_sections.get(family)
        if sections is not None and not any(cqd.get(section) for section in sections):
            return ContractAssessment(
                family=family,
                status="not_applicable",
                source_spans=[],
                reason=(
                    f"Initial CQD contains none of the structures required by {family}: "
                    f"{', '.join(sections)}."
                ),
                findings=[],
            )

        if family == "TIME_ATTACHMENT":
            atoms = cqd.get("predicates", {}).get("atoms", [])
            temporal_atoms = [
                atom for atom in atoms
                if any(
                    token in str(atom.get(field, "")).casefold()
                    for field in ("dimension", "source_text")
                    for token in ("date", "time", "year", "opened", "between")
                )
            ]
            if temporal_atoms and all(atom.get("applies_to") for atom in temporal_atoms):
                spans = [str(atom.get("source_text", "")).strip() for atom in temporal_atoms]
                return ContractAssessment(
                    family=family,
                    status="safe",
                    source_spans=[span for span in spans if span],
                    reason="Every temporal predicate has an explicit event/entity attachment in Initial CQD.",
                    findings=[],
                )
            if not temporal_atoms and not cqd.get("time_constraints"):
                return ContractAssessment(
                    family=family,
                    status="not_applicable",
                    source_spans=[],
                    reason="Initial CQD contains no temporal predicate or time constraint.",
                    findings=[],
                )
        if family in {"SEMANTIC_DIMENSION", "SET_RELATION"}:
            atoms = cqd.get("predicates", {}).get("atoms", [])
            for index, atom in enumerate(atoms):
                values = atom.get("value")
                span = str(atom.get("source_text", "")).strip()
                has_parallel_values = isinstance(values, list) and len(values) > 1
                has_unresolved_parallel = (
                    atom.get("resolution_status") == "unresolved"
                    and " and " in span.casefold()
                )
                if (has_parallel_values or has_unresolved_parallel) and " and " in span.casefold():
                    if family == "SEMANTIC_DIMENSION":
                        reason = (
                            "Parallel labels may be values of one dimension or labels from "
                            "different semantic dimensions."
                        )
                    else:
                        reason = (
                            "The conjunction between multiple labels may denote intersection "
                            "or a list/union relation."
                        )
                    target_path = (
                        "/predicates" if family == "SEMANTIC_DIMENSION"
                        else (
                            f"/predicates/atoms/{index}/value"
                            if has_parallel_values
                            else f"/predicates/atoms/{index}"
                        )
                    )
                    return ContractAssessment(
                        family=family,
                        status="detected",
                        source_spans=[span],
                        reason=reason,
                        findings=[ContractFinding(
                            source_span=span,
                            reason=reason,
                            target_path=target_path,
                        )],
                    )
            return ContractAssessment(
                family=family,
                status="safe",
                source_spans=[],
                reason=f"Initial CQD has no unresolved compound predicates for {family}.",
                findings=[],
            )

        if family == "BOOLEAN_ATTACHMENT":
            predicates = cqd.get("predicates", {})
            atoms = predicates.get("atoms", [])
            root = predicates.get("root")
            if atoms and isinstance(root, str) and root.upper() in {"AND", "OR"}:
                spans = [
                    str(atom.get("source_text", "")).strip()
                    for atom in atoms if atom.get("source_text")
                ]
                return ContractAssessment(
                    family=family,
                    status="safe",
                    source_spans=spans,
                    reason=(
                        f"Initial CQD has an explicit {root.upper()} root and each predicate "
                        "is attached as a separate atom."
                    ),
                    findings=[],
                )
            if not atoms:
                return ContractAssessment(
                    family=family,
                    status="not_applicable",
                    source_spans=[],
                    reason="Initial CQD contains no predicate atoms.",
                    findings=[],
                )

        if family == "OUTPUT_EXPECTATION":
            outputs = cqd.get("outputs", [])
            if outputs:
                return ContractAssessment(
                    family=family,
                    status="safe",
                    source_spans=[],
                    reason="Initial CQD specifies explicit output fields.",
                    findings=[],
                )

        if family == "ENTITY_BOUNDARY":
            entities = cqd.get("entities", [])
            atoms = cqd.get("predicates", {}).get("atoms", [])
            if entities or atoms:
                return ContractAssessment(
                    family=family,
                    status="safe",
                    source_spans=[],
                    reason="Entities and target objects are structurally anchored.",
                    findings=[],
                )

    @staticmethod
    def _object_paths(document) -> list[str]:
        paths = []

        def visit(value, pointer: str) -> None:
            if pointer:
                paths.append(pointer)
            if isinstance(value, Mapping):
                for key, child in value.items():
                    escaped = str(key).replace("~", "~0").replace("/", "~1")
                    visit(child, f"{pointer}/{escaped}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{pointer}/{index}")

        visit(document, "")
        return paths

    @staticmethod
    def _assessment_from_output(
        *, contract, cqd: dict, original_question: str, out
    ) -> ContractAssessment:
        family = str(out.get("family", "")).upper()
        status = out.get("status")
        reason = out.get("reason")
        raw_spans = out.get("source_spans")
        raw_findings = out.get("findings")

        if family != contract.family:
            raise ValueError(
                f"Contract detector returned family {family!r}; expected {contract.family!r}"
            )
        if status not in ASSESSMENT_STATUSES:
            raise ValueError(f"Invalid {family} assessment status: {status!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{family} assessment requires a non-empty reason")
        if not isinstance(raw_spans, list) or not all(
            isinstance(span, str) and span.strip() for span in raw_spans
        ):
            raise ValueError(f"{family} source_spans must be a list of non-empty strings")
        if not isinstance(raw_findings, list):
            raise ValueError(f"{family} findings must be a list")

        findings = []
        for raw in raw_findings:
            if not isinstance(raw, dict):
                raise ValueError(f"{family} findings must contain objects")
            finding = ContractFinding(
                source_span=str(raw.get("source_span", "")).strip(),
                reason=str(raw.get("reason", "")).strip(),
                target_path=str(raw.get("target_path", "")).strip(),
            )
            if not finding.source_span or not finding.reason:
                raise ValueError(f"{family} detected finding is incomplete")
            _resolve_pointer(cqd, finding.target_path)
            findings.append(finding)

        if status == "detected" and not findings:
            raise ValueError(f"{family} detected assessment requires at least one finding")
        if status != "detected" and findings:
            raise ValueError(f"{family} {status} assessment cannot contain findings")
        def normalize_span(value: str) -> str:
            return re.sub(r"\s+", " ", value).strip().casefold()

        normalized_question = normalize_span(original_question)
        invalid_source_spans = [
            span for span in raw_spans
            if normalize_span(span) not in normalized_question
        ]
        invalid_finding_spans = [
            finding.source_span for finding in findings
            if normalize_span(finding.source_span) not in normalized_question
        ]
        if invalid_source_spans or invalid_finding_spans:
            raise ValueError(
                f"{family} source spans must be exact text from original_question: "
                f"assessment={invalid_source_spans}, findings={invalid_finding_spans}"
            )

        normalized_spans = [normalize_span(span) for span in raw_spans]
        uncovered = []
        for finding in findings:
            finding_span = normalize_span(finding.source_span)
            if not any(
                finding_span in assessment_span or assessment_span in finding_span
                for assessment_span in normalized_spans
            ):
                uncovered.append(finding.source_span)
        if status == "detected" and uncovered:
            raise ValueError(
                f"{family} source_spans do not cover finding spans: {uncovered}"
            )

        return ContractAssessment(
            family=family,
            status=status,
            source_spans=list(raw_spans),
            reason=reason.strip(),
            findings=findings,
        )
