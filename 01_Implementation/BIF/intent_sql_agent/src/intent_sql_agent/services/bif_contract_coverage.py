from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable


ASSESSMENT_STATUSES = frozenset({"detected", "safe", "not_applicable"})
DISPOSITIONS = frozenset({"bif_clarification", "deferred_to_sif", "none"})


@dataclass(frozen=True)
class BIFClarificationContract:
    family: str
    description: str
    detection_rules: tuple[str, ...]
    issue_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BIF_CONTRACTS: tuple[BIFClarificationContract, ...] = (
    BIFClarificationContract(
        "SEMANTIC_DIMENSION",
        "Whether parallel terms belong to one business dimension or independent conditions",
        ("Check parallel labels for same-dimension versus cross-dimension readings.",),
        "POPULATION_CONDITION_STRUCTURE",
    ),
    BIFClarificationContract(
        "ENTITY_BOUNDARY",
        "Whether a phrase denotes one entity or multiple semantic parts",
        ("Check compound names and location phrases for competing entity boundaries.",),
        "TARGET_POPULATION",
    ),
    BIFClarificationContract(
        "BOOLEAN_ATTACHMENT",
        "AND, OR, NOT, and parenthesis attachment",
        ("Check every conjunction, disjunction, negation, and implicit grouping.",),
        "LOGIC_RELATION",
    ),
    BIFClarificationContract(
        "METRIC_DEFINITION",
        "The business quantity intended by each requested metric",
        ("Check whether every requested metric has one explicit business meaning.",),
        "METRIC_DEFINITION",
    ),
    BIFClarificationContract(
        "CALCULATION_FORMULA",
        "Formula, operator, and operand semantics",
        ("Check derived quantities for explicit operators, operands, and formula meaning.",),
        "CALCULATION_METHOD",
    ),
    BIFClarificationContract(
        "AGGREGATE_TARGET",
        "The measure or expression to which an aggregation applies",
        ("Check that each aggregation has one intended target.",),
        "CALCULATION_METHOD",
    ),
    BIFClarificationContract(
        "GROUPING_GRAIN",
        "Global, entity-level, or group-level calculation grain",
        ("Check whether the requested grain is explicit.",),
        "GRANULARITY",
    ),
    BIFClarificationContract(
        "SELECTION_SCOPE",
        "Whether rank or extrema apply globally or within each group",
        ("Check the scope of ranking, top/bottom, minimum, and maximum selection.",),
        "SCOPE_SELECTION",
    ),
    BIFClarificationContract(
        "TIME_ATTACHMENT",
        "The event, state, or measurement constrained by a time phrase",
        ("Check each time phrase for an explicit attachment.",),
        "TIME_BASIS",
    ),
    BIFClarificationContract(
        "COMPARISON_DIRECTION",
        "Operand order and direction of comparison",
        ("Check comparative language for explicit operands and direction.",),
        "COMPARISON",
    ),
    BIFClarificationContract(
        "OUTPUT_EXPECTATION",
        "Requested fields, multiplicity, coverage, and detail versus summary",
        ("Check the requested output without inventing presentation requirements.",),
        "OUTPUT_SHAPE",
    ),
    BIFClarificationContract(
        "SET_RELATION",
        "Union, intersection, exclusion, or membership relation",
        ("Check multiple populations or labels for their intended set relation.",),
        "LOGIC_RELATION",
    ),
    BIFClarificationContract(
        "TIE_POLICY",
        "Whether all ties or one result should be returned",
        ("Check extrema and top/bottom selection for an explicit tie policy.",),
        "SCOPE_SELECTION",
    ),
)


BIF_CONTRACT_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "family", "status", "disposition", "source_spans", "reason", "findings"
    ],
    "properties": {
        "family": {"type": "string"},
        "status": {"enum": sorted(ASSESSMENT_STATUSES)},
        "disposition": {"enum": sorted(DISPOSITIONS)},
        "source_spans": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string", "minLength": 1},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_span", "reason", "subject_ref", "alternatives"],
                "properties": {
                    "source_span": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "subject_ref": {"type": "string", "pattern": "^/"},
                    "alternatives": {
                        "type": "array", "minItems": 2,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}


class BIFContractCoverageService:
    """Run and validate one explicit assessment for every registered BIF contract."""

    def __init__(
        self, contracts: tuple[BIFClarificationContract, ...] = BIF_CONTRACTS
    ) -> None:
        self.contracts = contracts
        families = [item.family for item in contracts]
        if len(families) != len(set(families)):
            raise ValueError("BIF contract families must be unique")

    def inspect(
        self,
        *,
        original_question: str,
        bif: dict[str, Any],
        detector: Callable[..., dict[str, Any]],
    ) -> list[dict[str, Any]]:
        allowed_paths = self.frame_paths(bif)
        assessments = []
        errors = []
        for contract in self.contracts:
            try:
                assessments.append(self._inspect_one(
                    original_question=original_question,
                    bif=bif,
                    contract=contract,
                    allowed_paths=allowed_paths,
                    detector=detector,
                ))
            except Exception as exc:
                errors.append({
                    "family": contract.family,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
        if errors:
            detail = "; ".join(
                f"{item['family']}={item['error_type']}: {item['error']}"
                for item in errors
            )
            raise RuntimeError(
                "BIF contract assessments failed after all registered contracts ran: "
                + detail
            )
        self.validate_complete(assessments)
        return assessments

    def _inspect_one(
        self,
        *,
        original_question: str,
        bif: dict[str, Any],
        contract: BIFClarificationContract,
        allowed_paths: set[str],
        detector: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        previous_output = None
        validation_error = None
        for attempt in range(3):
            kwargs = {
                "original_question": original_question,
                "bif": bif,
                "contract": contract.to_dict(),
                "allowed_paths": sorted(allowed_paths),
            }
            if attempt:
                kwargs.update({
                    "previous_output": previous_output,
                    "validation_error": validation_error,
                    "repair_attempt": attempt,
                })
            value = detector(**kwargs)
            try:
                return self._validate_assessment(
                    value,
                    contract=contract,
                    original_question=original_question,
                    allowed_paths=allowed_paths,
                )
            except ValueError as exc:
                previous_output = value
                validation_error = str(exc)
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def validate_complete(self, assessments: list[dict[str, Any]]) -> None:
        expected = {item.family for item in self.contracts}
        actual = [str(item.get("family") or "") for item in assessments]
        duplicates = sorted({item for item in actual if actual.count(item) > 1})
        missing = sorted(expected - set(actual))
        unexpected = sorted(set(actual) - expected)
        invalid = [
            item.get("family") for item in assessments
            if item.get("status") not in ASSESSMENT_STATUSES
            or item.get("disposition") not in DISPOSITIONS
            or not str(item.get("reason") or "").strip()
        ]
        if missing or duplicates or unexpected or invalid or len(actual) != len(expected):
            raise RuntimeError(
                "Incomplete BIF contract coverage: "
                f"missing={missing}, duplicates={duplicates}, unexpected={unexpected}, "
                f"invalid={invalid}, expected={len(expected)}, actual={len(actual)}"
            )

    @staticmethod
    def detected_observations(
        assessments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        contract_by_family = {item.family: item for item in BIF_CONTRACTS}
        for assessment in assessments:
            if assessment.get("status") != "detected":
                continue
            family = str(assessment["family"])
            issue_type = contract_by_family[family].issue_type
            for finding in assessment.get("findings", []):
                subject_ref = str(finding["subject_ref"])
                source_span = BIFContractCoverageService._normalize(
                    str(finding["source_span"])
                )
                key = (issue_type, subject_ref, source_span)
                if key in seen:
                    continue
                seen.add(key)
                observations.append({
                    "issue_type": issue_type,
                    "subject_ref": subject_ref,
                    "description": str(finding["reason"]),
                    "alternatives": list(finding["alternatives"]),
                    "contract_family": family,
                    "source_span": str(finding["source_span"]),
                })
        return observations

    @classmethod
    def _validate_assessment(
        cls,
        value: dict[str, Any],
        *,
        contract: BIFClarificationContract,
        original_question: str,
        allowed_paths: set[str],
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{contract.family} assessment must be an object")
        family = str(value.get("family") or "").upper()
        status = str(value.get("status") or "")
        disposition = str(value.get("disposition") or "")
        reason = str(value.get("reason") or "").strip()
        source_spans = value.get("source_spans")
        findings = value.get("findings")
        if family != contract.family:
            raise ValueError(f"Expected {contract.family}; received {family!r}")
        if status not in ASSESSMENT_STATUSES or disposition not in DISPOSITIONS:
            raise ValueError(f"Invalid {family} status/disposition")
        if not reason or not isinstance(source_spans, list) or not isinstance(findings, list):
            raise ValueError(f"Incomplete {family} assessment")
        if status == "detected" and (disposition != "bif_clarification" or not findings):
            raise ValueError(f"Detected {family} requires clarification findings")
        if status != "detected" and disposition == "bif_clarification":
            raise ValueError(f"Non-detected {family} cannot request BIF clarification")
        if status == "not_applicable" and disposition != "none":
            raise ValueError(f"Not-applicable {family} must use disposition none")
        if status != "detected" and findings:
            raise ValueError(f"Non-detected {family} cannot contain findings")
        normalized_question = cls._normalize(original_question)
        for span in source_spans:
            if (
                not isinstance(span, str)
                or not cls._normalize(span)
                or cls._normalize(span) not in normalized_question
            ):
                raise ValueError(f"{family} source span is not grounded in the question: {span!r}")
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValueError(f"{family} finding must be an object")
            span = str(finding.get("source_span") or "")
            subject_ref = str(finding.get("subject_ref") or "")
            alternatives = finding.get("alternatives")
            if not cls._normalize(span) or cls._normalize(span) not in normalized_question:
                raise ValueError(f"{family} finding span is not grounded: {span!r}")
            if subject_ref not in allowed_paths:
                raise ValueError(f"{family} finding references unknown BIF path: {subject_ref}")
            if not isinstance(alternatives, list) or len({cls._normalize(x) for x in alternatives}) < 2:
                raise ValueError(f"{family} finding requires two distinct alternatives")
            if not str(finding.get("reason") or "").strip():
                raise ValueError(f"{family} finding requires a reason")
        return {
            "family": family,
            "status": status,
            "disposition": disposition,
            "source_spans": list(source_spans),
            "reason": reason,
            "findings": list(findings),
        }

    @staticmethod
    def frame_paths(frame: Any) -> set[str]:
        paths: set[str] = set()

        def visit(value: Any, path: str) -> None:
            if path:
                paths.add(path)
            if isinstance(value, dict):
                for key, child in value.items():
                    token = str(key).replace("~", "~0").replace("/", "~1")
                    visit(child, f"{path}/{token}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}/{index}")

        visit(frame, "")
        return paths

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value)).strip().casefold()
