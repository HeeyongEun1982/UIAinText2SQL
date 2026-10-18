"""Recompute BIRD development-set execution accuracy from archived final SQL.

The equality rule follows the official BIRD evaluator: execute the predicted
and Gold SQL on the same SQLite database and compare ``set(predicted_rows)``
with ``set(gold_rows)``.  The local wrapper adds read-only connections,
per-case diagnostics, and a SQLite progress-handler timeout so that failed
queries can be audited rather than silently collapsed into a zero.

This script does not generate SQL and does not modify any experiment artifact.
It writes only the CSV/JSON files next to this script.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[4]
EXPERIMENT_ROOT = WORKSPACE / "revised_final_bif_sif"

CONDITIONS: dict[str, dict[str, Any]] = {
    "bif_only": {
        "source": EXPERIMENT_ROOT / "0.bif",
        "runs": [
            EXPERIMENT_ROOT / "0.bif" / "runs" / "0.bif_full_1534_20260821_2201",
            EXPERIMENT_ROOT / "0.bif" / "runs" / "0.bif_full_1534_20260821_2201_",
        ],
    },
    "sif_only": {
        "source": EXPERIMENT_ROOT / "1.sif",
        "runs": [
            EXPERIMENT_ROOT / "1.sif" / "runs" / "1.sif_full_1534_20260831_215201",
        ],
    },
    "bif_sif": {
        "source": EXPERIMENT_ROOT / "2.bif_sif",
        "runs": [
            EXPERIMENT_ROOT / "2.bif_sif" / "runs" / "2.bif_sif_full_1534_20260823_012700",
        ],
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_cases(source: Path) -> list[dict[str, Any]]:
    path = source / "data" / "bird_dev.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError(f"Expected a JSON list: {path}")
    return payload


def final_sql_candidates(run: Path, qid: str) -> list[Path]:
    return [
        run / qid / "P08" / "sql" / "sql_final.sql",
        run / qid / "P07" / "sql" / "sql_final.sql",
    ]


def locate_final_sql(runs: list[Path], qid: str) -> tuple[str | None, str | None, list[str]]:
    found: list[tuple[Path, str]] = []
    for run in runs:
        for candidate in final_sql_candidates(run, qid):
            if candidate.is_file():
                sql = candidate.read_text(encoding="utf-8").strip()
                if sql:
                    found.append((candidate, sql))
                    break

    if not found:
        return None, None, []

    distinct = {sql for _, sql in found}
    conflicts = [str(path) for path, _ in found] if len(distinct) > 1 else []
    chosen_path, chosen_sql = found[0]
    return chosen_sql, str(chosen_path), conflicts


def evaluate_case(task: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    timeout_seconds = float(task["timeout_seconds"])
    connection: sqlite3.Connection | None = None

    def interrupt_when_expired() -> int:
        return int(time.monotonic() - started > timeout_seconds)

    result = dict(task)
    result.pop("timeout_seconds", None)
    result.update(
        {
            "official_ex": 0,
            "status": "error",
            "error": "",
            "predicted_row_count": None,
            "gold_row_count": None,
            "elapsed_seconds": None,
        }
    )

    predicted_sql = task.get("predicted_sql")
    if not predicted_sql:
        result["status"] = "missing_prediction"
        result["error"] = "Archived final SQL was not found"
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
        return result

    try:
        database = Path(task["database"])
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.set_progress_handler(interrupt_when_expired, 10_000)
        cursor = connection.cursor()

        # Preserve the official evaluator's execution order.
        cursor.execute(predicted_sql)
        predicted_rows = cursor.fetchall()
        cursor.execute(task["gold_sql"])
        gold_rows = cursor.fetchall()

        result["predicted_row_count"] = len(predicted_rows)
        result["gold_row_count"] = len(gold_rows)
        result["official_ex"] = int(set(predicted_rows) == set(gold_rows))
        result["status"] = "ok"
    except sqlite3.OperationalError as exc:
        message = str(exc)
        result["status"] = "timeout" if "interrupted" in message.lower() else "execution_error"
        result["error"] = message
    except Exception as exc:  # Keep the complete census even for unusual SQLite values.
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if connection is not None:
            connection.close()
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def build_tasks(condition: str, config: dict[str, Any], timeout_seconds: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source: Path = config["source"]
    runs: list[Path] = config["runs"]
    cases = load_cases(source)
    tasks: list[dict[str, Any]] = []
    conflicts: dict[str, list[str]] = {}

    for case in cases:
        qid = str(case["question_id"])
        predicted_sql, sql_path, conflict_paths = locate_final_sql(runs, qid)
        if conflict_paths:
            conflicts[qid] = conflict_paths
        db_id = str(case["db_id"])
        database = source / "data" / "bird_dev" / "dev_databases" / db_id / f"{db_id}.sqlite"
        tasks.append(
            {
                "condition": condition,
                "question_id": int(qid),
                "db_id": db_id,
                "difficulty": str(case.get("difficulty") or "unknown"),
                "database": str(database),
                "predicted_sql": predicted_sql,
                "predicted_sql_path": sql_path,
                "gold_sql": str(case.get("SQL") or case.get("sql") or ""),
                "timeout_seconds": timeout_seconds,
            }
        )

    provenance = {
        "dataset_path": str(source / "data" / "bird_dev.json"),
        "dataset_sha256": sha256_file(source / "data" / "bird_dev.json"),
        "run_roots": [str(path) for path in runs],
        "conflicting_archived_sql": conflicts,
    }
    return tasks, provenance


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)

    summary: dict[str, Any] = {}
    for condition, condition_rows in by_condition.items():
        condition_rows.sort(key=lambda row: row["question_id"])
        by_difficulty: dict[str, dict[str, Any]] = {}
        for difficulty in ("simple", "moderate", "challenging"):
            subset = [row for row in condition_rows if row["difficulty"] == difficulty]
            correct = sum(int(row["official_ex"]) for row in subset)
            by_difficulty[difficulty] = {
                "n": len(subset),
                "correct": correct,
                "accuracy": correct / len(subset) if subset else None,
            }

        correct = sum(int(row["official_ex"]) for row in condition_rows)
        summary[condition] = {
            "n": len(condition_rows),
            "correct": correct,
            "accuracy": correct / len(condition_rows) if condition_rows else None,
            "status_counts": dict(sorted(Counter(row["status"] for row in condition_rows).items())),
            "by_difficulty": by_difficulty,
        }
    return summary


def write_outputs(rows: list[dict[str, Any]], provenance: dict[str, Any], timeout_seconds: float) -> None:
    output_dir = Path(__file__).resolve().parent
    csv_path = output_dir / "official_bird_ex_recomputed.csv"
    json_path = output_dir / "official_bird_ex_recomputed.json"

    rows.sort(key=lambda row: (row["condition"], row["question_id"]))
    csv_fields = [
        "condition",
        "question_id",
        "db_id",
        "difficulty",
        "official_ex",
        "status",
        "error",
        "predicted_row_count",
        "gold_row_count",
        "elapsed_seconds",
        "predicted_sql_path",
        "predicted_sql",
        "gold_sql",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "evaluation_rule": "set(predicted_rows) == set(gold_rows)",
        "timeout_seconds": timeout_seconds,
        "timeout_implementation": "SQLite progress handler (10,000 VM instructions)",
        "connection_mode": "read-only; PRAGMA query_only=ON",
        "provenance": provenance,
        "summary": summarize(rows),
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=0, help="Debug-only per-condition limit")
    args = parser.parse_args()

    all_tasks: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    for condition, config in CONDITIONS.items():
        tasks, condition_provenance = build_tasks(condition, config, args.timeout_seconds)
        if args.limit > 0:
            tasks = tasks[: args.limit]
        all_tasks.extend(tasks)
        provenance[condition] = condition_provenance

    rows: list[dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(evaluate_case, task) for task in all_tasks]
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            rows.append(future.result())
            if index % 100 == 0 or index == len(futures):
                print(f"Evaluated {index}/{len(futures)}", flush=True)

    write_outputs(rows, provenance, args.timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
