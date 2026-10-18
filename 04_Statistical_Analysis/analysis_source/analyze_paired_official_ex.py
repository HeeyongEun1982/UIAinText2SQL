"""Validate and analyse the paired Official BIRD EX census.

The input is the locked query-level output produced by
``recompute_bird_official_ex.py``.  This script never executes or edits SQL.
It constructs the three-condition paired table, verifies all marginal and
8-cell identities, and implements the Chapter 3 statistical analysis plan.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import scipy
from scipy.stats import binomtest, chi2


CONDITION_ORDER = ("bif_only", "sif_only", "bif_sif")
BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_SEED = 20_260_902
Z_975 = 1.959963984540054
EXPECTED_INPUT_SHA256 = "DC7EAA3AD145D215AC2011A3709F1E8AD0B6A617AC9278CA1F5010EC4397090F"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def read_long_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"condition", "question_id", "db_id", "difficulty", "official_ex", "status"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Input is missing required columns: {sorted(required)}")
    invalid_failures = [
        f"{row['condition']}/{row['question_id']}"
        for row in rows
        if row["status"] != "ok" and int(row["official_ex"]) != 0
    ]
    if invalid_failures:
        raise ValueError(f"Non-ok rows must have Official EX 0: {invalid_failures[:10]}")
    return rows


def build_paired_rows(long_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    expected_conditions = set(CONDITION_ORDER)
    by_qid: dict[int, dict[str, Any]] = {}
    duplicate_keys: list[str] = []

    for row in long_rows:
        condition = row["condition"]
        if condition not in expected_conditions:
            raise ValueError(f"Unexpected condition: {condition}")
        qid = int(row["question_id"])
        record = by_qid.setdefault(
            qid,
            {
                "question_id": qid,
                "db_id": row["db_id"],
                "difficulty": row["difficulty"],
                "outcomes": {},
            },
        )
        if record["db_id"] != row["db_id"] or record["difficulty"] != row["difficulty"]:
            raise ValueError(f"Metadata mismatch across conditions for qid {qid}")
        if condition in record["outcomes"]:
            duplicate_keys.append(f"{condition}/{qid}")
        record["outcomes"][condition] = int(row["official_ex"])

    if duplicate_keys:
        raise ValueError(f"Duplicate condition/qid rows: {duplicate_keys[:10]}")
    if sorted(by_qid) != list(range(1534)):
        raise ValueError("Expected unique qids 0--1533")

    paired: list[dict[str, Any]] = []
    for qid in sorted(by_qid):
        record = by_qid[qid]
        if set(record["outcomes"]) != expected_conditions:
            raise ValueError(f"Incomplete condition set for qid {qid}")
        outcomes = [record["outcomes"][condition] for condition in CONDITION_ORDER]
        if any(value not in (0, 1) for value in outcomes):
            raise ValueError(f"Non-binary outcome for qid {qid}")
        pattern = "".join(str(value) for value in outcomes)
        paired.append(
            {
                "question_id": qid,
                "db_id": record["db_id"],
                "difficulty": record["difficulty"],
                "bif_only": outcomes[0],
                "sif_only": outcomes[1],
                "bif_sif": outcomes[2],
                "pattern": pattern,
            }
        )
    return paired


def wilson_interval(successes: int, n: int, z: float = Z_975) -> tuple[float, float]:
    if n <= 0:
        return math.nan, math.nan
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    half_width = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator
    return centre - half_width, centre + half_width


def accuracy_summary(values: np.ndarray) -> dict[str, Any]:
    successes = int(values.sum())
    n = int(values.size)
    lower, upper = wilson_interval(successes, n)
    return {
        "n": n,
        "correct": successes,
        "accuracy": successes / n,
        "wilson_95_ci": [lower, upper],
    }


def cochran_q(outcomes: np.ndarray) -> dict[str, float]:
    if outcomes.ndim != 2 or outcomes.shape[1] != 3:
        raise ValueError("Cochran Q requires an N x 3 binary matrix")
    k = outcomes.shape[1]
    column_totals = outcomes.sum(axis=0, dtype=np.int64)
    row_totals = outcomes.sum(axis=1, dtype=np.int64)
    total = int(column_totals.sum())
    denominator = k * total - int(np.square(row_totals).sum())
    if denominator == 0:
        statistic = math.nan
        p_value = math.nan
    else:
        numerator = (k - 1) * (k * int(np.square(column_totals).sum()) - total * total)
        statistic = numerator / denominator
        p_value = float(chi2.sf(statistic, k - 1))
    return {"statistic": statistic, "df": k - 1, "p_value": p_value}


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    m = len(ordered)
    running_max = 0.0
    adjusted: dict[str, float] = {}
    for rank, (name, p_value) in enumerate(ordered):
        candidate = min(1.0, (m - rank) * p_value)
        running_max = max(running_max, candidate)
        adjusted[name] = running_max
    return adjusted


def paired_bootstrap(
    outcomes: np.ndarray,
    pattern_codes: np.ndarray,
    replicates: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = outcomes.shape[0]
    differences = np.column_stack(
        (
            outcomes[:, 2] - outcomes[:, 0],
            outcomes[:, 2] - outcomes[:, 1],
            outcomes[:, 0] - outcomes[:, 1],
        )
    )
    difference_samples = np.empty((replicates, 3), dtype=np.float64)
    pattern_samples = np.empty((replicates, 8), dtype=np.float64)
    chunk_size = 500

    for start in range(0, replicates, chunk_size):
        stop = min(start + chunk_size, replicates)
        indices = rng.integers(0, n, size=(stop - start, n), endpoint=False)
        difference_samples[start:stop] = differences[indices].mean(axis=1)
        sampled_codes = pattern_codes[indices]
        pattern_samples[start:stop] = np.column_stack(
            [(sampled_codes == code).mean(axis=1) for code in range(8)]
        )

    return difference_samples, pattern_samples


def cluster_bootstrap_risk_differences(
    paired_rows: list[dict[str, Any]],
    replicates: int,
    seed: int,
) -> np.ndarray:
    by_database: dict[str, list[list[int]]] = defaultdict(list)
    for row in paired_rows:
        by_database[row["db_id"]].append(
            [int(row["bif_only"]), int(row["sif_only"]), int(row["bif_sif"])]
        )
    database_ids = sorted(by_database)
    clusters = [np.asarray(by_database[db_id], dtype=np.int8) for db_id in database_ids]
    rng = np.random.default_rng(seed)
    samples = np.empty((replicates, 3), dtype=np.float64)

    for replicate in range(replicates):
        selected = rng.integers(0, len(clusters), size=len(clusters), endpoint=False)
        sample = np.concatenate([clusters[index] for index in selected], axis=0)
        samples[replicate] = (
            np.mean(sample[:, 2] - sample[:, 0]),
            np.mean(sample[:, 2] - sample[:, 1]),
            np.mean(sample[:, 0] - sample[:, 1]),
        )
    return samples


def percentile_interval(samples: np.ndarray) -> list[float]:
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return [float(lower), float(upper)]


def analyse(paired_rows: list[dict[str, Any]], input_path: Path) -> dict[str, Any]:
    outcomes = np.asarray(
        [
            [row["bif_only"], row["sif_only"], row["bif_sif"]]
            for row in paired_rows
        ],
        dtype=np.int8,
    )
    pattern_codes = outcomes[:, 0] * 4 + outcomes[:, 1] * 2 + outcomes[:, 2]
    pattern_counts = Counter(row["pattern"] for row in paired_rows)

    difference_samples, pattern_samples = paired_bootstrap(
        outcomes,
        pattern_codes,
        BOOTSTRAP_REPLICATES,
        BOOTSTRAP_SEED,
    )
    cluster_samples = cluster_bootstrap_risk_differences(
        paired_rows,
        BOOTSTRAP_REPLICATES,
        BOOTSTRAP_SEED + 1,
    )

    contrast_specs = (
        ("combined_vs_bif", 2, 0, 0),
        ("combined_vs_sif", 2, 1, 1),
        ("bif_vs_sif", 0, 1, 2),
    )
    comparisons: dict[str, dict[str, Any]] = {}
    raw_p_values: dict[str, float] = {}
    for name, a_index, b_index, sample_index in contrast_specs:
        a_values = outcomes[:, a_index]
        b_values = outcomes[:, b_index]
        gain = int(np.sum((a_values == 1) & (b_values == 0)))
        regression = int(np.sum((a_values == 0) & (b_values == 1)))
        discordant = gain + regression
        p_value = (
            float(binomtest(gain, discordant, p=0.5, alternative="two-sided").pvalue)
            if discordant
            else 1.0
        )
        raw_p_values[name] = p_value
        comparisons[name] = {
            "gain": gain,
            "regression": regression,
            "discordant": discordant,
            "risk_difference": float(np.mean(a_values - b_values)),
            "paired_bootstrap_95_ci": percentile_interval(difference_samples[:, sample_index]),
            "database_cluster_bootstrap_95_ci_sensitivity": percentile_interval(
                cluster_samples[:, sample_index]
            ),
            "exact_mcnemar_p": p_value,
        }
    adjusted = holm_adjust(raw_p_values)
    for name, adjusted_p in adjusted.items():
        comparisons[name]["holm_adjusted_p"] = adjusted_p

    primary_accuracy = {
        condition: accuracy_summary(outcomes[:, index])
        for index, condition in enumerate(CONDITION_ORDER)
    }
    by_difficulty: dict[str, Any] = {}
    for difficulty in ("simple", "moderate", "challenging"):
        mask = np.asarray([row["difficulty"] == difficulty for row in paired_rows])
        by_difficulty[difficulty] = {
            condition: accuracy_summary(outcomes[mask, index])
            for index, condition in enumerate(CONDITION_ORDER)
        }

    patterns: dict[str, Any] = {}
    for code in range(8):
        pattern = f"{code:03b}"
        count = int(pattern_counts.get(pattern, 0))
        patterns[pattern] = {
            "count": count,
            "proportion": count / len(paired_rows),
            "paired_bootstrap_95_ci": percentile_interval(pattern_samples[:, code]),
        }

    conditional_rates = {
        "rescue_rate_among_dual_failures": {
            "numerator": int(pattern_counts["001"]),
            "denominator": int(pattern_counts["000"] + pattern_counts["001"]),
            "rate": pattern_counts["001"] / (pattern_counts["000"] + pattern_counts["001"]),
            "paired_bootstrap_95_ci": percentile_interval(
                pattern_samples[:, 1] / (pattern_samples[:, 0] + pattern_samples[:, 1])
            ),
        },
        "strict_regression_rate_among_dual_successes": {
            "numerator": int(pattern_counts["110"]),
            "denominator": int(pattern_counts["110"] + pattern_counts["111"]),
            "rate": pattern_counts["110"] / (pattern_counts["110"] + pattern_counts["111"]),
            "paired_bootstrap_95_ci": percentile_interval(
                pattern_samples[:, 6] / (pattern_samples[:, 6] + pattern_samples[:, 7])
            ),
        },
    }

    expected_marginals = {
        condition: int(outcomes[:, index].sum())
        for index, condition in enumerate(CONDITION_ORDER)
    }
    integrity = {
        "n_paired_queries": len(paired_rows),
        "qid_min": min(row["question_id"] for row in paired_rows),
        "qid_max": max(row["question_id"] for row in paired_rows),
        "unique_qids": len({row["question_id"] for row in paired_rows}),
        "database_count": len({row["db_id"] for row in paired_rows}),
        "pattern_count_sum": sum(item["count"] for item in patterns.values()),
        "marginal_correct": expected_marginals,
        "checks_passed": (
            len(paired_rows) == 1534
            and sum(item["count"] for item in patterns.values()) == 1534
            and expected_marginals
            == {"bif_only": 916, "sif_only": 861, "bif_sif": 1008}
        ),
    }
    if not integrity["checks_passed"]:
        raise ValueError(f"Integrity checks failed: {integrity}")

    return {
        "analysis_scope": "Official BIRD set-equality EX only",
        "condition_order": list(CONDITION_ORDER),
        "input": {
            "path": input_path.name,
            "sha256": sha256_file(input_path),
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "platform": platform.platform(),
        },
        "bootstrap": {
            "replicates": BOOTSTRAP_REPLICATES,
            "query_level_seed": BOOTSTRAP_SEED,
            "database_cluster_seed": BOOTSTRAP_SEED + 1,
            "interval": "2.5th and 97.5th percentiles",
            "database_cluster_method": "Sample 11 database IDs with replacement, concatenate every query in each selected cluster, and recompute the query-weighted risk difference",
        },
        "integrity": integrity,
        "primary_accuracy": primary_accuracy,
        "difficulty_descriptive": by_difficulty,
        "cochran_q": cochran_q(outcomes),
        "pairwise_comparisons": comparisons,
        "outcome_patterns": patterns,
        "conditional_pattern_rates": conditional_rates,
    }


def write_paired_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "question_id",
        "db_id",
        "difficulty",
        "bif_only",
        "sif_only",
        "bif_sif",
        "pattern",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parent / "official_bird_ex_recomputed.csv",
    )
    args = parser.parse_args()
    input_path = args.input.resolve()
    output_dir = Path(__file__).resolve().parent

    actual_input_sha256 = sha256_file(input_path)
    if actual_input_sha256 != EXPECTED_INPUT_SHA256:
        raise ValueError(
            "Input SHA-256 does not match the locked Official EX census: "
            f"{actual_input_sha256}"
        )

    long_rows = read_long_csv(input_path)
    paired_rows = build_paired_rows(long_rows)
    summary = analyse(paired_rows, input_path)

    paired_path = output_dir / "official_bird_ex_paired_wide.csv"
    summary_path = output_dir / "official_bird_ex_statistical_summary.json"
    write_paired_csv(paired_path, paired_rows)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary["integrity"], indent=2, ensure_ascii=False))
    print(f"Wrote: {paired_path}")
    print(f"Wrote: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
