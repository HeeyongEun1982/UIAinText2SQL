"""Validate and summarize the full RQ3 final-failure-mode coding.

The three coding parts are model-assisted content reviews of disjoint query-id
ranges.  This script checks that they form an exact census of the 170
BIF-SIF-incorrect queries with differing system outcomes, then produces a
single detail table and reproducible summaries.  It does not infer or modify
any content label.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REFERENCE_PATH = HERE / "outputs" / "rq3_negative_failure_stage_census.csv"
PART_PATHS = [
    HERE / "inputs" / "rq3_final_failure_coding_part1.csv",
    HERE / "inputs" / "rq3_final_failure_coding_part2.csv",
    HERE / "inputs" / "rq3_final_failure_coding_part3.csv",
]
DETAIL_PATH = HERE / "outputs" / "rq3_final_failure_mode_census.csv"
SUMMARY_PATH = HERE / "outputs" / "rq3_final_failure_mode_summary.csv"
BY_PATTERN_PATH = HERE / "outputs" / "rq3_final_failure_mode_by_outcome.csv"
SUMMARY_JSON_PATH = HERE / "outputs" / "rq3_final_failure_mode_summary.json"

ALLOWED_MODES = [
    "clarification_question_failure",
    "clarification_answer_failure",
    "confirmation_or_transfer_failure",
    "grounding_failure",
    "sql_implementation_failure",
    "compound_or_indeterminate",
    "timeout",
]
MODE_LABELS_KO = {
    "clarification_question_failure": "명확화 질문 또는 선택지 문제",
    "clarification_answer_failure": "명확화 답변 선택 문제",
    "confirmation_or_transfer_failure": "확정 기록 또는 단계 간 전달 문제",
    "grounding_failure": "데이터베이스 연결 문제",
    "sql_implementation_failure": "최종 SQL 구현 문제",
    "compound_or_indeterminate": "복합 또는 판정 불가",
    "timeout": "실행 시간 초과",
}
EXPECTED_COLUMNS = [
    "question_id",
    "pattern",
    "primary_failure_mode",
    "failure_subtype",
    "successful_reference",
    "question_assessment",
    "answer_assessment",
    "confirmation_assessment",
    "grounding_assessment",
    "sql_assessment",
    "overwrites_earlier_correct_decision",
    "confidence",
    "evidence_note_ko",
    "key_source_paths",
    "language_model_assisted",
    "author_verified",
    "causal_attribution_permitted",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ValueError(
                f"Unexpected columns in {path.name}: {reader.fieldnames}; "
                f"expected {EXPECTED_COLUMNS}"
            )
        return list(reader)


def read_reference() -> dict[int, dict[str, str]]:
    with REFERENCE_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return {int(row["question_id"]): row for row in csv.DictReader(handle)}


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    reference = read_reference()
    rows: list[dict[str, str]] = []
    part_counts: dict[str, int] = {}
    for path in PART_PATHS:
        part = read_csv(path)
        rows.extend(part)
        part_counts[path.name] = len(part)

    if part_counts != {
        "rq3_final_failure_coding_part1.csv": 57,
        "rq3_final_failure_coding_part2.csv": 57,
        "rq3_final_failure_coding_part3.csv": 56,
    }:
        raise ValueError(f"Unexpected coding part sizes: {part_counts}")

    qids = [int(row["question_id"]) for row in rows]
    if len(qids) != 170 or len(set(qids)) != 170 or set(qids) != set(reference):
        raise ValueError("Coding rows must be an exact unique census of the 170 reference qids")

    for row in rows:
        qid = int(row["question_id"])
        if row["pattern"] != reference[qid]["pattern"]:
            raise ValueError(f"Pattern mismatch for qid {qid}")
        if row["primary_failure_mode"] not in ALLOWED_MODES:
            raise ValueError(
                f"Unexpected failure mode for qid {qid}: {row['primary_failure_mode']}"
            )
        if row["confidence"] not in {"high", "medium", "low"}:
            raise ValueError(f"Unexpected confidence for qid {qid}")
        for field in (
            "overwrites_earlier_correct_decision",
            "language_model_assisted",
            "author_verified",
            "causal_attribution_permitted",
        ):
            if row[field] not in {"0", "1"}:
                raise ValueError(f"{field} must be 0/1 for qid {qid}")
        if not row["evidence_note_ko"].strip() or not row["key_source_paths"].strip():
            raise ValueError(f"Missing evidence note or source path for qid {qid}")

    rows.sort(key=lambda row: int(row["question_id"]))
    write_csv(DETAIL_PATH, rows, EXPECTED_COLUMNS)

    mode_counts = Counter(row["primary_failure_mode"] for row in rows)
    denominator = len(rows)
    summary_rows: list[dict[str, Any]] = []
    for mode in ALLOWED_MODES:
        count = mode_counts[mode]
        summary_rows.append(
            {
                "primary_failure_mode": mode,
                "label_ko": MODE_LABELS_KO[mode],
                "count": count,
                "percent": f"{100 * count / denominator:.2f}",
                "query_n": denominator,
            }
        )
    write_csv(
        SUMMARY_PATH,
        summary_rows,
        ["primary_failure_mode", "label_ko", "count", "percent", "query_n"],
    )

    by_pattern: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_pattern[row["pattern"]][row["primary_failure_mode"]] += 1
    by_pattern_rows: list[dict[str, Any]] = []
    for pattern in sorted(by_pattern):
        pattern_n = sum(by_pattern[pattern].values())
        for mode in ALLOWED_MODES:
            count = by_pattern[pattern][mode]
            by_pattern_rows.append(
                {
                    "pattern": pattern,
                    "pattern_n": pattern_n,
                    "primary_failure_mode": mode,
                    "label_ko": MODE_LABELS_KO[mode],
                    "count": count,
                    "percent_within_pattern": f"{100 * count / pattern_n:.2f}",
                }
            )
    write_csv(
        BY_PATTERN_PATH,
        by_pattern_rows,
        [
            "pattern",
            "pattern_n",
            "primary_failure_mode",
            "label_ko",
            "count",
            "percent_within_pattern",
        ],
    )

    confidence_counts = Counter(row["confidence"] for row in rows)
    overwrite_n = sum(
        int(row["overwrites_earlier_correct_decision"]) for row in rows
    )
    checks = {
        "detail_rows_are_170": len(rows) == 170,
        "question_ids_match_reference": set(qids) == set(reference),
        "mode_counts_sum_to_170": sum(mode_counts.values()) == 170,
        "pattern_counts": Counter(row["pattern"] for row in rows)
        == Counter({"010": 51, "100": 62, "110": 57}),
        "all_rows_model_assisted": all(
            row["language_model_assisted"] == "1" for row in rows
        ),
        "no_rows_marked_author_verified": all(
            row["author_verified"] == "0" for row in rows
        ),
        "no_causal_attribution_permitted": all(
            row["causal_attribution_permitted"] == "0" for row in rows
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Final-failure summary validation failed: {checks}")

    SUMMARY_JSON_PATH.write_text(
        json.dumps(
            {
                "scope": (
                    "All 170 differing-outcome queries where BIF-SIF was "
                    "incorrect and BIF Only or SIF Only was correct"
                ),
                "analysis_direction": (
                    "Backward comparison from the main final SQL difference; "
                    "not a first-observed-stage analysis"
                ),
                "primary_failure_modes": {
                    mode: mode_counts[mode] for mode in ALLOWED_MODES
                },
                "confidence": dict(confidence_counts),
                "overwrites_earlier_correct_decision": overwrite_n,
                "part_counts": part_counts,
                "checks": checks,
                "limitations": [
                    "Model-assisted content coding; author verification is pending",
                    "Operational failure-pattern attribution does not identify an independent causal effect",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "counts": {mode: mode_counts[mode] for mode in ALLOWED_MODES},
                "overwrite_n": overwrite_n,
                "confidence": dict(confidence_counts),
                "checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
