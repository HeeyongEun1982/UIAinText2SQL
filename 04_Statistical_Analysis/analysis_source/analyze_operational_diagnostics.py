"""Summarise operational cost and non-primary evaluator diagnostics."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


HERE = Path(__file__).resolve().parent
INPUT_DIR = HERE / "inputs"
OUTPUT_DIR = HERE / "outputs"
CONDITIONS = ("bif_only", "sif_only", "bif_sif")
BATCH_FILES = {
    "bif_only": "bif_only_batch_summary.csv",
    "sif_only": "sif_only_batch_summary.csv",
    "bif_sif": "bif_sif_batch_summary.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def distribution(condition: str, metric: str, values: pd.Series) -> dict[str, Any]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return {
        "condition": condition,
        "metric": metric,
        "n": int(len(numeric)),
        "total": float(numeric.sum()),
        "mean": float(numeric.mean()),
        "median": float(numeric.median()),
        "p25": float(numeric.quantile(0.25)),
        "p75": float(numeric.quantile(0.75)),
        "p95": float(numeric.quantile(0.95)),
        "minimum": float(numeric.min()),
        "maximum": float(numeric.max()),
    }


def operational_summary() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    process = pd.read_csv(INPUT_DIR / "operational_query_metrics.csv")
    calls = pd.read_csv(INPUT_DIR / "model_calls.csv")
    rows: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        subset = process[process["condition"] == condition]
        for metric in (
            "model_call_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "summed_model_latency_ms",
            "bif_clarification_turns",
            "sif_clarification_interactions",
        ):
            rows.append(distribution(condition, metric, subset[metric]))

    agent_rows: list[dict[str, Any]] = []
    for (condition, agent), subset in calls.groupby(["condition", "agent"], dropna=False):
        tokens = pd.to_numeric(subset["total_tokens"], errors="coerce")
        latency = pd.to_numeric(subset["latency_ms"], errors="coerce")
        agent_rows.append(
            {
                "condition": condition,
                "agent": agent,
                "calls_with_metadata": int(len(subset)),
                "calls_with_token_usage": int(tokens.notna().sum()),
                "total_tokens": float(tokens.sum()),
                "mean_total_tokens": float(tokens.mean()) if tokens.notna().any() else "",
                "summed_latency_ms": float(latency.sum()),
                "mean_latency_ms": float(latency.mean()) if latency.notna().any() else "",
                "actual_models": "|".join(sorted(str(v) for v in subset["actual_model"].dropna().unique())),
            }
        )
    agent_rows.sort(key=lambda row: (row["condition"], str(row["agent"])))
    return rows, agent_rows


def evaluation_diagnostics() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    official = pd.read_csv(INPUT_DIR / "official_bird_ex_recomputed.csv")[
        ["condition", "question_id", "official_ex", "status"]
    ]
    joined_rows: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    batch_call_coverage: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        batch = pd.read_csv(INPUT_DIR / BATCH_FILES[condition])
        batch = batch.rename(
            columns={
                "execution_accuracy": "internal_strict",
                "relaxed_execution_accuracy": "internal_relaxed",
                "model_call_count_total": "batch_model_calls",
            }
        )
        batch["question_id"] = pd.to_numeric(batch["question_id"], errors="raise").astype(int)
        if len(batch) != 1534 or batch["question_id"].nunique() != 1534:
            raise ValueError(f"{condition}: invalid batch qid coverage")
        left = official[official["condition"] == condition].copy()
        merged = left.merge(
            batch[["question_id", "internal_strict", "internal_relaxed", "batch_model_calls"]],
            on="question_id",
            how="left",
            validate="one_to_one",
        )
        for column in ("official_ex", "internal_strict", "internal_relaxed"):
            merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(0).astype(int)
        merged["condition"] = condition
        joined_rows.append(merged)

        official_flag = merged["official_ex"]
        strict = merged["internal_strict"]
        relaxed = merged["internal_relaxed"]
        summaries.append(
            {
                "condition": condition,
                "n": 1534,
                "official_correct": int(official_flag.sum()),
                "internal_strict_correct": int(strict.sum()),
                "internal_relaxed_correct": int(relaxed.sum()),
                "official_1_strict_0": int(((official_flag == 1) & (strict == 0)).sum()),
                "official_0_strict_1": int(((official_flag == 0) & (strict == 1)).sum()),
                "official_1_relaxed_0": int(((official_flag == 1) & (relaxed == 0)).sum()),
                "official_0_relaxed_1": int(((official_flag == 0) & (relaxed == 1)).sum()),
                "strict_0_relaxed_1": int(((strict == 0) & (relaxed == 1)).sum()),
                "strict_1_relaxed_0": int(((strict == 1) & (relaxed == 0)).sum()),
            }
        )
        batch_calls = pd.to_numeric(merged["batch_model_calls"], errors="coerce")
        batch_call_coverage.append(
            {
                "condition": condition,
                "n_queries": 1534,
                "batch_call_nonmissing": int(batch_calls.notna().sum()),
                "batch_call_total": float(batch_calls.sum()),
                "batch_call_mean": float(batch_calls.mean()),
                "batch_call_median": float(batch_calls.median()),
                "batch_call_p95": float(batch_calls.quantile(0.95)),
            }
        )
    joined = pd.concat(joined_rows, ignore_index=True)
    joined.to_csv(
        OUTPUT_DIR / "query_level_evaluator_flags.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return summaries, batch_call_coverage


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    operational, by_agent = operational_summary()
    evaluator, batch_calls = evaluation_diagnostics()
    write_csv(OUTPUT_DIR / "operational_metrics_summary.csv", operational)
    write_csv(OUTPUT_DIR / "model_call_summary_by_agent.csv", by_agent)
    write_csv(OUTPUT_DIR / "evaluator_diagnostic_comparison.csv", evaluator)
    write_csv(OUTPUT_DIR / "batch_model_call_summary.csv", batch_calls)

    manifest = {
        "script_sha256": sha256(Path(__file__).resolve()),
        "input_sha256": {
            name: sha256(INPUT_DIR / name)
            for name in [
                "operational_query_metrics.csv",
                "model_calls.csv",
                "official_bird_ex_recomputed.csv",
                *BATCH_FILES.values(),
            ]
        },
        "metric_hierarchy": {
            "primary": "Official BIRD set-equality EX",
            "sensitivity": "archived internal duplicate-sensitive strict comparison",
            "diagnostic": "archived internal relaxed comparison",
        },
        "latency_note": "Summed model-call latency is not end-to-end wall-clock time.",
        "call_note": "Batch call counts are used for call totals; metadata-derived token and latency summaries report their own coverage.",
    }
    (OUTPUT_DIR / "operational_diagnostics_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"evaluator": evaluator, "batch_calls": batch_calls}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
