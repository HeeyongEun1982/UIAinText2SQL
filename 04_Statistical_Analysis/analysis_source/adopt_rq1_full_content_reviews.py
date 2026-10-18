"""Adopt the complete BIF and SIF content reviews as the final RQ1 labels.

Run this after ``finalize_rq1_labels.py`` and the two full-review scripts.  It
validates one reviewed label for every included RQ1 turn, writes the reviewed
labels into the canonical turn-level file, and regenerates both distribution
tables and the coding summary.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "outputs"
CANONICAL = OUTPUT_DIR / "rq1_turn_labels_final.csv"
REVIEWS = {
    "bif_only": OUTPUT_DIR / "rq1_bif_full_content_review.csv",
    "sif_only": OUTPUT_DIR / "rq1_sif_full_content_review.csv",
}

LABEL_NAMES = {
    "RG": "referent/concept grounding",
    "RS": "result selection/multiplicity",
    "MC": "metric/calculation",
    "SC": "population/scope",
    "PL": "predicate logic/boundary",
    "TM": "temporal scope",
    "UG": "unit/granularity",
    "OR": "output/representation",
    "GAP": "schema coverage gap",
    "REL": "relation/join/role",
    "TEMP": "temporal/version record",
    "METRIC": "metric/aggregation grain",
    "SCOPE": "population/context/logical scope",
    "RANK": "ranking/result selection",
    "VALUE": "literal/code/unit/parsing",
    "OUTPUT": "returned identifier/format",
    "ATTRIBUTE": "schema element/authority source",
}

COMMON_MAP = {
    "RG": "CONCEPT_VALUE_SOURCE",
    "RS": "RANK_MULTIPLICITY",
    "MC": "METRIC_GRAIN",
    "SC": "SCOPE_LOGIC",
    "PL": "SCOPE_LOGIC",
    "TM": "TEMPORAL",
    "UG": "METRIC_GRAIN",
    "OR": "OUTPUT_REPRESENTATION",
    "GAP": "SCHEMA_GAP",
    "REL": "RELATION_ROLE",
    "TEMP": "TEMPORAL",
    "METRIC": "METRIC_GRAIN",
    "SCOPE": "SCOPE_LOGIC",
    "RANK": "RANK_MULTIPLICITY",
    "VALUE": "CONCEPT_VALUE_SOURCE",
    "OUTPUT": "OUTPUT_REPRESENTATION",
    "ATTRIBUTE": "CONCEPT_VALUE_SOURCE",
}

EXPECTED = {"bif_only": (432, 212, 1534), "sif_only": (375, 334, 1524)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def distribution(
    rows: list[dict[str, Any]], label_field: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for architecture, (turn_n, clarified_n, eligible_n) in EXPECTED.items():
        subset = [
            row
            for row in rows
            if row["architecture"] == architecture and row["rq1_included"] == "1"
        ]
        for label in sorted({row[label_field] for row in subset}):
            selected = [row for row in subset if row[label_field] == label]
            query_n = len({int(row["question_id"]) for row in selected})
            result.append(
                {
                    "architecture": architecture,
                    "category": label,
                    "turn_count": len(selected),
                    "turn_percent": round(100 * len(selected) / turn_n, 6),
                    "query_count": query_n,
                    "query_percent": round(100 * query_n / 1534, 6),
                    "benchmark_query_percent": round(100 * query_n / 1534, 6),
                    "eligible_query_percent": round(100 * query_n / eligible_n, 6),
                    "clarified_query_percent": round(100 * query_n / clarified_n, 6),
                    "turn_denominator": turn_n,
                    "benchmark_query_denominator": 1534,
                    "eligible_query_denominator": eligible_n,
                    "clarified_query_denominator": clarified_n,
                }
            )
    return result


def main() -> None:
    rows = read_csv(CANONICAL)
    review_map: dict[tuple[str, int, int], dict[str, str]] = {}
    review_hashes: dict[str, str] = {}

    for architecture, path in REVIEWS.items():
        reviews = read_csv(path)
        expected_turns = EXPECTED[architecture][0]
        if len(reviews) != expected_turns:
            raise RuntimeError(
                f"{architecture}: expected {expected_turns} reviews, found {len(reviews)}"
            )
        review_hashes[architecture] = sha256(path)
        for review in reviews:
            key = (architecture, int(review["qid"]), int(review["turn_index"]))
            if key in review_map:
                raise RuntimeError(f"duplicate review key: {key}")
            label = review["reviewed_label"]
            if label not in LABEL_NAMES:
                raise RuntimeError(f"invalid label {label} for {key}")
            if not review.get("rationale", "").strip():
                raise RuntimeError(f"missing rationale for {key}")
            review_map[key] = review

    included_keys = {
        (row["architecture"], int(row["question_id"]), int(row["turn"]))
        for row in rows
        if row["rq1_included"] == "1"
    }
    if included_keys != set(review_map):
        missing = sorted(included_keys - set(review_map))
        extra = sorted(set(review_map) - included_keys)
        raise RuntimeError(f"review coverage mismatch: missing={missing[:5]}, extra={extra[:5]}")

    changes = Counter()
    for row in rows:
        row["full_content_reviewed"] = 0
        row["full_content_review_source"] = ""
        if row["rq1_included"] != "1":
            continue
        key = (row["architecture"], int(row["question_id"]), int(row["turn"]))
        review = review_map[key]
        previous = row["final_native_label"]
        final = review["reviewed_label"]
        changes[row["architecture"]] += int(previous != final)
        row["pre_full_review_native_label"] = previous
        row["final_native_label"] = final
        row["final_native_label_name"] = LABEL_NAMES[final]
        row["final_common_label"] = COMMON_MAP[final]
        row["single_coder_reviewed"] = 0
        row["full_content_reviewed"] = 1
        row["full_content_review_source"] = REVIEWS[row["architecture"]].name
        row["decision_delta"] = review["rationale"]
        row["review_note"] = (
            "Language-model-assisted full-content review using the blinded "
            "question, all options, and recorded decision context. "
            + review["rationale"]
        )
        row["unresolved"] = 0

    write_csv(CANONICAL, rows)
    native = distribution(rows, "final_native_label")
    common = distribution(rows, "final_common_label")
    write_csv(OUTPUT_DIR / "rq1_native_distribution_final.csv", native)
    write_csv(OUTPUT_DIR / "rq1_common_distribution_final.csv", common)

    included = [row for row in rows if row["rq1_included"] == "1"]
    summary = {
        "coding": "language-model-assisted full-content review of every included turn",
        "included_turns": len(included),
        "reviewed_turns": sum(int(row["full_content_reviewed"]) for row in included),
        "unresolved": sum(int(row["unresolved"]) for row in included),
        "changes_from_previous_final": dict(changes),
        "review_inputs": {
            architecture: {"path": str(REVIEWS[architecture]), "sha256": digest}
            for architecture, digest in review_hashes.items()
        },
        "final_native_counts": {
            architecture: dict(
                sorted(
                    Counter(
                        row["final_native_label"]
                        for row in included
                        if row["architecture"] == architecture
                    ).items()
                )
            )
            for architecture in EXPECTED
        },
        "analysis_denominators": {
            "bif_only": {
                "turns": 432,
                "clarified_queries": 212,
                "benchmark_queries": 1534,
                "eligible_queries": 1534,
            },
            "sif_only": {
                "turns_after_qid_1_through_10_exclusion": 375,
                "clarified_queries_after_exclusion": 334,
                "benchmark_queries": 1534,
                "eligible_queries_after_exclusion": 1524,
            },
        },
        "methodological_note": (
            "The review used no selected answer, ground-truth SQL, generated SQL, "
            "or correctness field. It was not independently double-coded by human reviewers."
        ),
        "outputs": {
            "turn_labels": str(CANONICAL),
            "native_distribution": str(OUTPUT_DIR / "rq1_native_distribution_final.csv"),
            "common_distribution": str(OUTPUT_DIR / "rq1_common_distribution_final.csv"),
        },
    }
    (OUTPUT_DIR / "rq1_final_coding_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
