"""Compare BIF-SIF SQL before and after post-generation finalisation.

The BIF-SIF runner preserves two SQL strings in each P04 linking frame:

* ``synthesized_sql``: the first SQL returned by the SQL synthesizer;
* ``final_sql``: the SQL retained after read-only preflight and, only when the
  first preflight fails, an optional correction attempt.

This script covers all 486 queries whose Official EX outcomes differ across
the three systems.  It compares the two strings after removing leading and
trailing whitespace only.  The result identifies whether the retained SQL
changed after initial synthesis; it does not identify where an incorrect
choice was first formed or attribute causation to a pipeline stage.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
RUN_ROOT = (
    WORKSPACE
    / "revised_final_bif_sif"
    / "2.bif_sif"
    / "runs"
    / "2.bif_sif_full_1534_20260823_012700"
)
RUNNER_SOURCE = (
    WORKSPACE
    / "revised_final_bif_sif"
    / "2.bif_sif"
    / "intent_sql_agent"
    / "src"
    / "intent_sql_agent"
    / "pipeline"
    / "full_case_runner.py"
)
MANIFEST_PATH = HERE / "outputs" / "rq3_census_manifest.csv"
INPUT_SNAPSHOT_PATH = HERE / "inputs" / "rq3_bif_sif_initial_final_sql.jsonl"
DETAIL_PATH = HERE / "outputs" / "rq3_sql_finalisation_census.csv"
SUMMARY_CSV_PATH = HERE / "outputs" / "rq3_sql_finalisation_summary.csv"
SUMMARY_JSON_PATH = HERE / "outputs" / "rq3_sql_finalisation_summary.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def relative_to_workspace(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE).as_posix()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def summary_row(scope: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    changed = sum(int(row["changed_after_initial_synthesis"]) for row in rows)
    total = len(rows)
    return {
        "scope": scope,
        "query_n": total,
        "unchanged_n": total - changed,
        "unchanged_percent": round(100 * (total - changed) / total, 2),
        "changed_n": changed,
        "changed_percent": round(100 * changed / total, 2),
    }


def main() -> None:
    manifest = read_csv(MANIFEST_PATH)
    if len(manifest) != 486 or len({int(row["question_id"]) for row in manifest}) != 486:
        raise ValueError("Expected 486 unique RQ3 census queries")

    snapshots: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for row in sorted(manifest, key=lambda item: int(item["question_id"])):
        qid = int(row["question_id"])
        frame_path = RUN_ROOT / str(qid) / "P04" / "frames" / "cqd_sql_final_linking.json"
        final_artifact_path = RUN_ROOT / str(qid) / "P08" / "sql" / "sql_final.sql"
        payload = json.loads(frame_path.read_text(encoding="utf-8-sig"))
        synthesized_sql = str(payload.get("synthesized_sql") or "")
        final_sql = str(payload.get("final_sql") or "")
        if not synthesized_sql.strip() or not final_sql.strip():
            raise ValueError(f"Missing synthesized_sql or final_sql for qid {qid}")

        final_artifact = final_artifact_path.read_text(encoding="utf-8-sig")
        correction_calls = sorted(
            (RUN_ROOT / str(qid) / "P04" / "agents" / "sql_self_correction").glob(
                "call_*"
            )
        )
        changed = int(synthesized_sql.strip() != final_sql.strip())
        final_artifact_matches = int(final_sql.strip() == final_artifact.strip())
        if not final_artifact_matches:
            raise ValueError(f"P04 final_sql does not match P08 artifact for qid {qid}")

        snapshot = {
            "question_id": qid,
            "pattern": str(row["pattern"]),
            "direction": str(row["direction"]),
            "all_condition_executions_ok": int(row["all_condition_executions_ok"]),
            "bif_sif_status": str(row["bif_sif_status"]),
            "preflight_status": str(payload.get("preflight_status") or ""),
            "synthesized_sql": synthesized_sql,
            "final_sql": final_sql,
            "source_path": relative_to_workspace(frame_path),
            "source_sha256": sha256(frame_path),
        }
        snapshots.append(snapshot)
        details.append(
            {
                "question_id": qid,
                "pattern": str(row["pattern"]),
                "db_id": str(row["db_id"]),
                "direction": str(row["direction"]),
                "all_condition_executions_ok": int(row["all_condition_executions_ok"]),
                "bif_only_status": str(row["bif_only_status"]),
                "sif_only_status": str(row["sif_only_status"]),
                "bif_sif_status": str(row["bif_sif_status"]),
                "preflight_status": str(payload.get("preflight_status") or ""),
                "correction_call_n": len(correction_calls),
                "changed_after_initial_synthesis": changed,
                "synthesized_sql_sha256": text_sha256(synthesized_sql),
                "final_sql_sha256": text_sha256(final_sql),
                "final_artifact_matches": final_artifact_matches,
                "linking_frame_path": relative_to_workspace(frame_path),
                "linking_frame_sha256": sha256(frame_path),
                "final_artifact_path": relative_to_workspace(final_artifact_path),
                "final_artifact_sha256": sha256(final_artifact_path),
            }
        )

    all_ok = [row for row in details if int(row["all_condition_executions_ok"]) == 1]
    combined_correct = [row for row in details if row["direction"] == "combined_correct"]
    combined_incorrect = [row for row in details if row["direction"] == "combined_incorrect"]
    correct_all_ok = [row for row in all_ok if row["direction"] == "combined_correct"]
    incorrect_all_ok = [row for row in all_ok if row["direction"] == "combined_incorrect"]
    summaries = [
        summary_row("all_changed_outcomes", details),
        summary_row("all_structural_comparisons", all_ok),
        summary_row("bif_sif_correct_all", combined_correct),
        summary_row("bif_sif_incorrect_all", combined_incorrect),
        summary_row("bif_sif_correct_structural", correct_all_ok),
        summary_row("bif_sif_incorrect_structural", incorrect_all_ok),
    ]

    changed_qids = [
        int(row["question_id"])
        for row in details
        if int(row["changed_after_initial_synthesis"]) == 1
    ]
    correction_qids = [
        int(row["question_id"])
        for row in details
        if int(row["correction_call_n"]) > 0
    ]
    checks = {
        "all_486_linking_frames_present": len(details) == 486,
        "all_final_artifacts_match": all(
            int(row["final_artifact_matches"]) == 1 for row in details
        ),
        "outcome_directions_are_316_and_170": Counter(
            str(row["direction"]) for row in details
        )
        == Counter({"combined_correct": 316, "combined_incorrect": 170}),
        "all_condition_execution_ok_is_481": len(all_ok) == 481,
        "changed_qids_are_expected": changed_qids == [195, 415, 904, 1016, 1126],
        "correction_calls_are_expected": correction_qids
        == [195, 415, 637, 904, 1016, 1050, 1126],
        "structural_groups_are_313_and_168": (
            len(correct_all_ok) == 313 and len(incorrect_all_ok) == 168
        ),
        "structural_changes_are_1_and_3": (
            sum(int(row["changed_after_initial_synthesis"]) for row in correct_all_ok)
            == 1
            and sum(
                int(row["changed_after_initial_synthesis"])
                for row in incorrect_all_ok
            )
            == 3
        ),
        "no_causal_or_first_origin_claim": True,
    }
    if not all(checks.values()):
        raise ValueError(f"SQL finalisation validation failed: {checks}")

    write_jsonl(INPUT_SNAPSHOT_PATH, snapshots)
    write_csv(DETAIL_PATH, details)
    write_csv(SUMMARY_CSV_PATH, summaries)
    SUMMARY_JSON_PATH.write_text(
        json.dumps(
            {
                "scope": "All 486 RQ3 queries with different system outcomes",
                "comparison": (
                    "synthesized_sql versus final_sql after stripping leading and "
                    "trailing whitespace only"
                ),
                "interpretation": (
                    "Whether retained SQL changed after initial synthesis; not a causal "
                    "failure-stage or first-origin label"
                ),
                "changed_question_ids": changed_qids,
                "correction_call_question_ids": correction_qids,
                "summary": summaries,
                "checks": checks,
                "sources": {
                    "rq3_manifest": {
                        "path": str(MANIFEST_PATH.relative_to(HERE).as_posix()),
                        "sha256": sha256(MANIFEST_PATH),
                    },
                    "runner_implementation": {
                        "path": relative_to_workspace(RUNNER_SOURCE),
                        "sha256": sha256(RUNNER_SOURCE),
                        "relevant_lines": "2241-2272",
                    },
                },
                "artifacts": {
                    "input_snapshot": {
                        "path": str(INPUT_SNAPSHOT_PATH.relative_to(HERE).as_posix()),
                        "sha256": sha256(INPUT_SNAPSHOT_PATH),
                    },
                    "detail": {
                        "path": str(DETAIL_PATH.relative_to(HERE).as_posix()),
                        "sha256": sha256(DETAIL_PATH),
                    },
                    "summary_csv": {
                        "path": str(SUMMARY_CSV_PATH.relative_to(HERE).as_posix()),
                        "sha256": sha256(SUMMARY_CSV_PATH),
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"checks": checks, "summary": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
