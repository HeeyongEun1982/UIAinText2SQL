"""Crosswalk the archived and recomputed Official BIRD EX for BIF--SIF.

The archived combined-run CSV reports 618 correct queries.  The canonical
evaluation input was subsequently recomputed from the same archived final SQL
and reports 1,008 correct queries.  This audit joins the two evaluations by
``question_id`` and records every old-to-new outcome transition without
rerunning SQL.

Only the row-level crosswalk CSV and its deterministic summary JSON are
written.  Both source paths and their SHA-256 hashes are preserved in the
summary so the comparison can be reproduced from the exact inputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


EVIDENCE_DIR = Path(__file__).resolve().parent
WORKSPACE = EVIDENCE_DIR.parents[3]

ARCHIVED_CSV = (
    WORKSPACE
    / "revised_final_bif_sif"
    / "2.bif_sif"
    / "runs"
    / "2.bif_sif_full_1534_20260823_012700"
    / "batch_summary_bird_official_ex.csv"
)
RECOMPUTED_CSV = EVIDENCE_DIR / "inputs" / "official_bird_ex_recomputed.csv"
OUTPUT_CSV = (
    EVIDENCE_DIR
    / "outputs"
    / "combined_archived_vs_recomputed_official_ex_crosswalk.csv"
)
OUTPUT_JSON = (
    EVIDENCE_DIR
    / "outputs"
    / "combined_archived_vs_recomputed_official_ex_summary.json"
)

EXPECTED_TRANSITIONS = {
    "1_to_1": 604,
    "1_to_0": 14,
    "0_to_1": 404,
    "0_to_0": 512,
}
EXPECTED_NEGATIVE_ROW_COUNT = {"total": 669, "new_correct": 387, "new_incorrect": 282}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def index_unique(rows: list[dict[str, str]], source_name: str) -> dict[int, dict[str, str]]:
    indexed: dict[int, dict[str, str]] = {}
    for row in rows:
        qid = int(row["question_id"])
        if qid in indexed:
            raise AssertionError(f"Duplicate question_id={qid} in {source_name}")
        indexed[qid] = row
    return indexed


def normalized_sql(sql: str) -> str:
    return " ".join(sql.split())


def build_crosswalk() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not ARCHIVED_CSV.is_file():
        raise FileNotFoundError(ARCHIVED_CSV)
    if not RECOMPUTED_CSV.is_file():
        raise FileNotFoundError(RECOMPUTED_CSV)

    archived_rows = read_csv(ARCHIVED_CSV)
    all_recomputed_rows = read_csv(RECOMPUTED_CSV)
    recomputed_rows = [row for row in all_recomputed_rows if row["condition"] == "bif_sif"]

    archived = index_unique(archived_rows, "archived combined-run CSV")
    recomputed = index_unique(recomputed_rows, "recomputed bif_sif rows")
    expected_qids = set(range(1534))

    assert len(archived_rows) == 1534
    assert len(recomputed_rows) == 1534
    assert set(archived) == expected_qids
    assert set(recomputed) == expected_qids
    assert set(archived) == set(recomputed)

    crosswalk: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()
    negative_row_count_new: Counter[str] = Counter()
    db_mismatches: list[int] = []
    difficulty_mismatches: list[int] = []
    generated_sql_mismatches: list[int] = []
    ground_truth_sql_mismatches: list[int] = []

    for qid in sorted(expected_qids):
        old = archived[qid]
        new = recomputed[qid]
        old_ex = int(old["bird_official_execution_accuracy"])
        new_ex = int(new["official_ex"])
        assert old_ex in (0, 1)
        assert new_ex in (0, 1)

        if old["db_id"] != new["db_id"]:
            db_mismatches.append(qid)
        if old["difficulty"] != new["difficulty"]:
            difficulty_mismatches.append(qid)

        generated_sql_identical = normalized_sql(old["generated_sql"]) == normalized_sql(
            new["predicted_sql"]
        )
        ground_truth_sql_identical = normalized_sql(old["gold_sql"]) == normalized_sql(
            new["gold_sql"]
        )
        if not generated_sql_identical:
            generated_sql_mismatches.append(qid)
        if not ground_truth_sql_identical:
            ground_truth_sql_mismatches.append(qid)

        transition = f"{old_ex}_to_{new_ex}"
        transitions[transition] += 1
        old_gen_row_count = int(old["gen_row_count"])
        if old_gen_row_count == -1:
            negative_row_count_new["new_correct" if new_ex else "new_incorrect"] += 1

        crosswalk.append(
            {
                "condition": "bif_sif",
                "question_id": qid,
                "db_id": old["db_id"],
                "difficulty": old["difficulty"],
                "archived_official_ex": old_ex,
                "recomputed_official_ex": new_ex,
                "transition": transition,
                "outcome_changed": int(old_ex != new_ex),
                "archived_gen_row_count": old_gen_row_count,
                "archived_gold_row_count": int(old["gold_row_count"]),
                "recomputed_predicted_row_count": new["predicted_row_count"],
                "recomputed_gold_row_count": new["gold_row_count"],
                "recomputed_status": new["status"],
                "recomputed_error": new["error"],
                "generated_sql_identical": int(generated_sql_identical),
                "ground_truth_sql_identical": int(ground_truth_sql_identical),
            }
        )

    transition_counts = {
        key: transitions[key]
        for key in ("1_to_1", "1_to_0", "0_to_1", "0_to_0")
    }
    negative_summary = {
        "total": sum(1 for row in archived_rows if int(row["gen_row_count"]) == -1),
        "new_correct": negative_row_count_new["new_correct"],
        "new_incorrect": negative_row_count_new["new_incorrect"],
    }

    assert transition_counts == EXPECTED_TRANSITIONS
    assert negative_summary == EXPECTED_NEGATIVE_ROW_COUNT
    assert not db_mismatches
    assert not difficulty_mismatches
    assert not generated_sql_mismatches
    assert not ground_truth_sql_mismatches

    summary: dict[str, Any] = {
        "audit": "combined archived versus recomputed Official BIRD EX crosswalk",
        "condition": "bif_sif",
        "row_count": len(crosswalk),
        "question_id_range": {"minimum": 0, "maximum": 1533},
        "question_id_sets_identical": True,
        "db_id_mismatch_count": len(db_mismatches),
        "difficulty_mismatch_count": len(difficulty_mismatches),
        "generated_sql_mismatch_count_after_whitespace_normalization": len(
            generated_sql_mismatches
        ),
        "ground_truth_sql_mismatch_count_after_whitespace_normalization": len(
            ground_truth_sql_mismatches
        ),
        "archived_correct": sum(int(row["bird_official_execution_accuracy"]) for row in archived_rows),
        "recomputed_correct": sum(int(row["official_ex"]) for row in recomputed_rows),
        "net_correct_difference": sum(int(row["official_ex"]) for row in recomputed_rows)
        - sum(int(row["bird_official_execution_accuracy"]) for row in archived_rows),
        "old_to_new_transition_counts": transition_counts,
        "archived_gen_row_count_minus_one": negative_summary,
        "sources": {
            "archived_combined_run_summary": {
                "path": str(ARCHIVED_CSV.resolve()),
                "sha256": sha256_file(ARCHIVED_CSV),
                "row_count": len(archived_rows),
            },
            "recomputed_official_ex": {
                "path": str(RECOMPUTED_CSV.resolve()),
                "sha256": sha256_file(RECOMPUTED_CSV),
                "all_condition_row_count": len(all_recomputed_rows),
                "bif_sif_row_count": len(recomputed_rows),
            },
        },
    }
    return crosswalk, summary


def write_outputs(crosswalk: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(crosswalk[0])
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(crosswalk)

    summary["row_level_output"] = {
        "path": str(OUTPUT_CSV.resolve()),
        "sha256": sha256_file(OUTPUT_CSV),
    }
    OUTPUT_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    crosswalk, summary = build_crosswalk()
    write_outputs(crosswalk, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
