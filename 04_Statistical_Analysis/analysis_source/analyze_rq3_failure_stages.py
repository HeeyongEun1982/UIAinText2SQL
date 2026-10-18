"""Build the reproducible RQ3 census of when BIF-SIF errors became observable.

The semantic stage labels are supplied in a separately preserved coding file.
This script does not infer those labels from keywords.  It validates that the
coding covers every BIF-SIF error exactly once, joins each label to the source
bundle and SQL-finalisation census, and writes analysis-ready detail and
summary files.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CODING_PATH = HERE / "inputs" / "rq3_negative_failure_stage_coding.json"
BUNDLE_PATH = HERE / "inputs" / "rq3_negative_stage_handoff_bundle.jsonl"
REVIEW_PATH = HERE / "outputs" / "rq3_census_review_final.csv"
FINALISATION_PATH = HERE / "outputs" / "rq3_sql_finalisation_census.csv"
DETAIL_PATH = HERE / "outputs" / "rq3_negative_failure_stage_census.csv"
SUMMARY_CSV_PATH = HERE / "outputs" / "rq3_negative_failure_stage_summary.csv"
SUMMARY_JSON_PATH = HERE / "outputs" / "rq3_negative_failure_stage_summary.json"

STAGE_ORDER = [
    "confirmed_bif",
    "initial_sif",
    "later_sif",
    "sql_synthesis",
    "unresolved_timeout",
]

STAGE_LABELS = {
    "confirmed_bif": "Confirmed BIF",
    "initial_sif": "Initial SIF after grounding",
    "later_sif": "Later SIF clarification or refinement",
    "sql_synthesis": "First SQL synthesis",
    "unresolved_timeout": "Unresolved execution timeout",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No rows to write: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    coding = json.loads(CODING_PATH.read_text(encoding="utf-8"))
    bundle = {int(row["question_id"]): row for row in read_jsonl(BUNDLE_PATH)}
    review = {
        int(row["question_id"]): row
        for row in read_csv(REVIEW_PATH)
        if row["direction"] == "combined_incorrect"
    }
    finalisation = {
        int(row["question_id"]): row for row in read_csv(FINALISATION_PATH)
    }

    stage_by_qid: dict[int, str] = {}
    duplicate_stage_qids: set[int] = set()
    for stage, qids in coding["primary_difference_first_observable_stage"].items():
        if stage not in STAGE_ORDER:
            raise ValueError(f"Unknown stage category: {stage}")
        for raw_qid in qids:
            qid = int(raw_qid)
            if qid in stage_by_qid:
                duplicate_stage_qids.add(qid)
            stage_by_qid[qid] = stage

    overwrite_cases = {
        int(row["question_id"]): row
        for row in coding["gold_aligned_bif_choice_changed_in_sif"]
    }
    ambiguous_handoff_qids = {
        int(qid) for qid in coding["ambiguous_handoff_question_ids"]
    }
    high_confidence_handoff_qids = set(overwrite_cases) - ambiguous_handoff_qids
    post_synthesis_new_qids = {
        int(qid) for qid in coding["primary_mismatch_first_created_after_synthesis"]
    }
    expected_qids = set(review)

    checks = {
        "review_rows_are_170": len(review) == 170,
        "bundle_rows_are_170": len(bundle) == 170,
        "finalisation_contains_all_170": expected_qids <= set(finalisation),
        "stage_categories_are_known": set(
            coding["primary_difference_first_observable_stage"]
        )
        <= set(STAGE_ORDER),
        "each_query_has_one_stage": not duplicate_stage_qids
        and set(stage_by_qid) == expected_qids,
        "overwrite_cases_are_in_scope": set(overwrite_cases) <= expected_qids,
        "ambiguous_handoff_cases_are_candidates": ambiguous_handoff_qids
        <= set(overwrite_cases),
        "post_synthesis_cases_are_in_scope": post_synthesis_new_qids
        <= expected_qids,
        "coding_is_noncausal": coding["review_metadata"].get(
            "causal_attribution_permitted"
        )
        is False,
    }
    if not all(checks.values()):
        raise ValueError(f"Failure-stage validation failed: {checks}")

    detail_rows: list[dict[str, Any]] = []
    for qid in sorted(expected_qids):
        reviewed = review[qid]
        bundled = bundle[qid]
        final = finalisation[qid]
        overwrite = overwrite_cases.get(qid, {})
        sources = bundled["sources"]
        detail_rows.append(
            {
                "question_id": qid,
                "pattern": reviewed["pattern"],
                "db_id": reviewed["db_id"],
                "question": reviewed["question"],
                "primary_observed_mechanism": reviewed[
                    "primary_observed_mechanism"
                ],
                "clarification_link": reviewed["clarification_link"],
                "primary_difference_first_observable_stage": stage_by_qid[qid],
                "primary_difference_first_observable_stage_label": STAGE_LABELS[
                    stage_by_qid[qid]
                ],
                "gold_aligned_bif_choice_changed_in_sif": int(bool(overwrite)),
                "bif_to_sif_change_confidence": (
                    "ambiguous"
                    if qid in ambiguous_handoff_qids
                    else "high"
                    if qid in high_confidence_handoff_qids
                    else "not_coded"
                ),
                "bif_to_sif_change_stage": overwrite.get("change_stage", ""),
                "bif_to_sif_decision_locus": overwrite.get("decision_locus", ""),
                "bif_to_sif_evidence_note_ko": overwrite.get(
                    "evidence_note_ko", ""
                ),
                "synthesized_sql_equals_final_sql": int(
                    int(final["changed_after_initial_synthesis"]) == 0
                ),
                "sql_changed_after_synthesis": int(
                    final["changed_after_initial_synthesis"]
                ),
                "primary_mismatch_first_created_after_synthesis": int(
                    qid in post_synthesis_new_qids
                ),
                "language_model_assisted": int(
                    coding["review_metadata"]["language_model_assisted"]
                ),
                "author_verified": int(
                    coding["review_metadata"]["author_verified"]
                ),
                "causal_attribution_permitted": int(
                    coding["review_metadata"]["causal_attribution_permitted"]
                ),
                "confirmed_bif_source": sources["confirmed_bif"]["path"],
                "confirmed_bif_sha256": sources["confirmed_bif"]["sha256"],
                "initial_sif_source": sources["initial_sif"]["path"],
                "initial_sif_sha256": sources["initial_sif"]["sha256"],
                "confirmed_sif_source": sources["confirmed_sif"]["path"],
                "confirmed_sif_sha256": sources["confirmed_sif"]["sha256"],
                "sql_linking_source": sources["sql_linking"]["path"],
                "sql_linking_sha256": sources["sql_linking"]["sha256"],
                "gold_sql_source": sources["gold_sql"]["path"],
                "gold_sql_sha256": sources["gold_sql"]["sha256"],
            }
        )

    stage_counts = Counter(stage_by_qid.values())
    denominator = len(expected_qids)
    summary_rows: list[dict[str, Any]] = []
    for stage in STAGE_ORDER:
        count = stage_counts[stage]
        summary_rows.append(
            {
                "classification": "primary_difference_first_observable_stage",
                "category": stage,
                "category_label": STAGE_LABELS[stage],
                "query_n": denominator,
                "count": count,
                "percent": round(100 * count / denominator, 2),
            }
        )

    overwrite_n = len(overwrite_cases)
    high_confidence_handoff_n = len(high_confidence_handoff_qids)
    ambiguous_handoff_n = len(ambiguous_handoff_qids)
    for category, label, count in [
        (
            "high_confidence_bif_choice_changed_in_sif",
            "High-confidence Gold-aligned Confirmed BIF choice changed in SIF",
            high_confidence_handoff_n,
        ),
        (
            "ambiguous_bif_to_sif_change_candidate",
            "Ambiguous BIF-to-SIF change candidate",
            ambiguous_handoff_n,
        ),
        (
            "no_coded_bif_to_sif_handoff_change",
            "No coded BIF-to-SIF handoff change",
            denominator - overwrite_n,
        ),
    ]:
        summary_rows.append(
            {
                "classification": "bif_to_sif_handoff",
                "category": category,
                "category_label": label,
                "query_n": denominator,
                "count": count,
                "percent": round(100 * count / denominator, 2),
            }
        )

    unchanged_n = sum(
        int(row["synthesized_sql_equals_final_sql"]) for row in detail_rows
    )
    for category, label, count in [
        (
            "synthesized_sql_equals_final_sql",
            "First synthesized SQL equals retained final SQL",
            unchanged_n,
        ),
        (
            "sql_changed_after_synthesis",
            "SQL changed after synthesis",
            denominator - unchanged_n,
        ),
        (
            "primary_mismatch_first_created_after_synthesis",
            "Primary mismatch first created after synthesis",
            len(post_synthesis_new_qids),
        ),
    ]:
        summary_rows.append(
            {
                "classification": "sql_finalisation",
                "category": category,
                "category_label": label,
                "query_n": denominator,
                "count": count,
                "percent": round(100 * count / denominator, 2),
            }
        )

    write_csv(DETAIL_PATH, detail_rows)
    write_csv(SUMMARY_CSV_PATH, summary_rows)

    output_checks = {
        **checks,
        "detail_rows_are_170": len(detail_rows) == 170,
        "stage_counts_sum_to_170": sum(stage_counts.values()) == 170,
        "finalisation_unchanged_plus_changed_is_170": unchanged_n
        + (denominator - unchanged_n)
        == 170,
    }
    if not all(output_checks.values()):
        raise ValueError(f"Failure-stage output validation failed: {output_checks}")

    summary_payload = {
        "scope": "All 170 RQ3 queries where BIF-SIF was incorrect",
        "analysis_unit": "One primary Gold-SQL difference per query",
        "stage_definition": coding["stage_definition"],
        "overwrite_definition": coding["overwrite_definition"],
        "interpretation_limit": coding["interpretation_limit"],
        "review_metadata": coding["review_metadata"],
        "counts": {
            "first_observable_stage": {
                stage: stage_counts[stage] for stage in STAGE_ORDER
            },
            "gold_aligned_bif_choice_changed_in_sif": overwrite_n,
            "high_confidence_bif_choice_changed_in_sif": high_confidence_handoff_n,
            "ambiguous_bif_to_sif_change_candidate": ambiguous_handoff_n,
            "first_synthesized_sql_equals_final_sql": unchanged_n,
            "sql_changed_after_synthesis": denominator - unchanged_n,
            "primary_mismatch_first_created_after_synthesis": len(
                post_synthesis_new_qids
            ),
        },
        "overwrite_question_ids": sorted(overwrite_cases),
        "high_confidence_handoff_question_ids": sorted(
            high_confidence_handoff_qids
        ),
        "ambiguous_handoff_question_ids": sorted(ambiguous_handoff_qids),
        "post_synthesis_new_primary_mismatch_question_ids": sorted(
            post_synthesis_new_qids
        ),
        "checks": output_checks,
        "inputs": {
            str(CODING_PATH.relative_to(HERE)): sha256(CODING_PATH),
            str(BUNDLE_PATH.relative_to(HERE)): sha256(BUNDLE_PATH),
            str(REVIEW_PATH.relative_to(HERE)): sha256(REVIEW_PATH),
            str(FINALISATION_PATH.relative_to(HERE)): sha256(FINALISATION_PATH),
        },
        "outputs": {
            str(DETAIL_PATH.relative_to(HERE)): sha256(DETAIL_PATH),
            str(SUMMARY_CSV_PATH.relative_to(HERE)): sha256(SUMMARY_CSV_PATH),
        },
    }
    SUMMARY_JSON_PATH.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary_payload["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
