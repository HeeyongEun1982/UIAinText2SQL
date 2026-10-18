"""Reproduce the Chapter 5 quantitative results from the preserved runs.

Primary correctness is taken only from ``official_bird_ex_recomputed.csv``.
The ``execution_accuracy`` and ``relaxed_execution_accuracy`` columns in the
three batch summaries are retained as separate runtime diagnostic measures;
they never overwrite the Official BIRD EX outcome.

The script copies every input it analyses into ``5_evidence_data/inputs`` and
writes all derived tables to ``5_evidence_data/outputs``.  It does not execute,
edit, or regenerate SQL.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import shutil
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import scipy
from scipy.stats import binomtest, chi2


EXPECTED_N = 1_534
EXPECTED_QIDS = set(range(EXPECTED_N))
Z_975 = 1.959963984540054

CONDITIONS = ("bif_only", "sif_only", "bif_sif")
DISPLAY_NAMES = {
    "bif_only": "BIF-only",
    "sif_only": "SIF-only",
    "bif_sif": "BIF-SIF",
}
DIFFICULTY_ORDER = ("simple", "moderate", "challenging")
PATTERN_LABELS = {
    "000": "persistent failure",
    "001": "joint-only success",
    "010": "SIF-only success / regression from SIF",
    "011": "SIF success retained",
    "100": "BIF-only success / regression from BIF",
    "101": "BIF success retained",
    "110": "integration regression",
    "111": "robust success",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def parse_binary(value: str, *, field: str, context: str) -> int:
    text = value.strip()
    if text in {"0", "False", "false"}:
        return 0
    if text in {"1", "True", "true"}:
        return 1
    raise ValueError(f"{context}: {field} is not binary: {value!r}")


def parse_optional_binary(value: str, *, field: str, context: str) -> int | None:
    if not value.strip():
        return None
    return parse_binary(value, field=field, context=context)


def parse_optional_float(value: str) -> float | None:
    text = value.strip()
    return None if not text else float(text)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"CSV is empty: {path}")
    return rows


def require_columns(rows: Sequence[dict[str, str]], required: set[str], path: Path) -> None:
    missing = required.difference(rows[0])
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")


def write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def wilson_interval(successes: int, n: int, z: float = Z_975) -> tuple[float, float]:
    if n <= 0:
        return math.nan, math.nan
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    half_width = z * math.sqrt(
        p * (1.0 - p) / n + z * z / (4.0 * n * n)
    ) / denominator
    return centre - half_width, centre + half_width


def accuracy_fields(values: Sequence[int]) -> dict[str, Any]:
    n = len(values)
    correct = sum(values)
    lower, upper = wilson_interval(correct, n)
    return {
        "n": n,
        "correct": correct,
        "accuracy": correct / n,
        "accuracy_percent": 100.0 * correct / n,
        "wilson_95_lower": lower,
        "wilson_95_upper": upper,
        "wilson_95_lower_percent": 100.0 * lower,
        "wilson_95_upper_percent": 100.0 * upper,
    }


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] * (1.0 - fraction) + ordered[upper_index] * fraction


def call_summary(values: Sequence[float | None]) -> dict[str, Any]:
    observed = [value for value in values if value is not None]
    if not observed:
        return {
            "n_nonmissing": 0,
            "n_missing": len(values),
            "sum": None,
            "mean": None,
            "median": None,
            "q1": None,
            "q3": None,
            "min": None,
            "max": None,
        }
    return {
        "n_nonmissing": len(observed),
        "n_missing": len(values) - len(observed),
        "sum": sum(observed),
        "mean": statistics.fmean(observed),
        "median": statistics.median(observed),
        "q1": quantile(observed, 0.25),
        "q3": quantile(observed, 0.75),
        "min": min(observed),
        "max": max(observed),
    }


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running_max = 0.0
    total = len(ordered)
    for rank, (name, p_value) in enumerate(ordered):
        candidate = min(1.0, (total - rank) * p_value)
        running_max = max(running_max, candidate)
        adjusted[name] = running_max
    return adjusted


def cochran_q(outcomes: Sequence[Sequence[int]]) -> dict[str, Any]:
    if not outcomes or any(len(row) != 3 for row in outcomes):
        raise ValueError("Cochran Q requires a non-empty N x 3 binary table")
    k = 3
    column_totals = [sum(row[index] for row in outcomes) for index in range(k)]
    row_totals = [sum(row) for row in outcomes]
    grand_total = sum(column_totals)
    denominator = k * grand_total - sum(total * total for total in row_totals)
    if denominator == 0:
        statistic = math.nan
        p_value = math.nan
    else:
        numerator = (k - 1) * (
            k * sum(total * total for total in column_totals) - grand_total * grand_total
        )
        statistic = numerator / denominator
        p_value = float(chi2.sf(statistic, k - 1))
    return {
        "statistic": statistic,
        "df": k - 1,
        "p_value": p_value,
        "condition_correct_totals": dict(zip(CONDITIONS, column_totals)),
    }


def copy_inputs(
    sources: dict[str, Path], input_dir: Path
) -> tuple[dict[str, Path], list[dict[str, Any]]]:
    input_dir.mkdir(parents=True, exist_ok=True)
    snapshots: dict[str, Path] = {}
    manifest: list[dict[str, Any]] = []
    for key, source in sources.items():
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = input_dir / f"{key}.csv"
        if source != destination.resolve():
            shutil.copy2(source, destination)
        snapshots[key] = destination
        manifest.append(
            {
                "key": key,
                "source_path": str(source),
                "snapshot_path": str(destination.resolve()),
                "size_bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
    return snapshots, manifest


def build_official_paired(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Counter[str]]]:
    rows = read_csv(path)
    require_columns(
        rows,
        {"condition", "question_id", "db_id", "difficulty", "official_ex", "status"},
        path,
    )
    by_qid: dict[int, dict[str, Any]] = {}
    status_counts: dict[str, Counter[str]] = {condition: Counter() for condition in CONDITIONS}
    seen: set[tuple[str, int]] = set()

    for raw in rows:
        condition = raw["condition"].strip()
        if condition not in CONDITIONS:
            raise ValueError(f"Unexpected Official EX condition: {condition!r}")
        qid = int(raw["question_id"])
        key = (condition, qid)
        if key in seen:
            raise ValueError(f"Duplicate Official EX row: {key}")
        seen.add(key)
        outcome = parse_binary(
            raw["official_ex"], field="official_ex", context=f"{condition}/{qid}"
        )
        status = raw["status"].strip()
        if status != "ok" and outcome != 0:
            raise ValueError(f"Non-ok Official EX row is marked correct: {condition}/{qid}")
        status_counts[condition][status] += 1

        record = by_qid.setdefault(
            qid,
            {
                "question_id": qid,
                "db_id": raw["db_id"].strip(),
                "difficulty": raw["difficulty"].strip().lower(),
                "official": {},
            },
        )
        if (
            record["db_id"] != raw["db_id"].strip()
            or record["difficulty"] != raw["difficulty"].strip().lower()
        ):
            raise ValueError(f"Official metadata mismatch across conditions for qid {qid}")
        record["official"][condition] = outcome

    if set(by_qid) != EXPECTED_QIDS:
        missing = sorted(EXPECTED_QIDS.difference(by_qid))
        extra = sorted(set(by_qid).difference(EXPECTED_QIDS))
        raise ValueError(f"Official qid census mismatch; missing={missing[:10]}, extra={extra[:10]}")

    paired: list[dict[str, Any]] = []
    for qid in range(EXPECTED_N):
        record = by_qid[qid]
        if set(record["official"]) != set(CONDITIONS):
            raise ValueError(f"Incomplete Official condition set for qid {qid}")
        outcomes = [record["official"][condition] for condition in CONDITIONS]
        paired.append(
            {
                "question_id": qid,
                "db_id": record["db_id"],
                "difficulty": record["difficulty"],
                "bif_only_official_ex": outcomes[0],
                "sif_only_official_ex": outcomes[1],
                "bif_sif_official_ex": outcomes[2],
                "outcome_pattern": "".join(str(value) for value in outcomes),
            }
        )
    return paired, status_counts


def read_batch_summary(path: Path, condition: str) -> dict[int, dict[str, Any]]:
    rows = read_csv(path)
    require_columns(
        rows,
        {
            "question_id",
            "db_id",
            "difficulty",
            "pipeline_status",
            "execution_accuracy",
            "relaxed_execution_accuracy",
            "model_call_count_total",
        },
        path,
    )
    by_qid: dict[int, dict[str, Any]] = {}
    for raw in rows:
        qid = int(raw["question_id"])
        if qid in by_qid:
            raise ValueError(f"Duplicate {condition} batch-summary qid: {qid}")
        by_qid[qid] = {
            "question_id": qid,
            "db_id": raw["db_id"].strip(),
            "difficulty": raw["difficulty"].strip().lower(),
            "pipeline_status": raw["pipeline_status"].strip(),
            "runtime_strict": parse_optional_binary(
                raw["execution_accuracy"],
                field="execution_accuracy",
                context=f"{condition}/{qid}",
            ),
            "runtime_relaxed": parse_optional_binary(
                raw["relaxed_execution_accuracy"],
                field="relaxed_execution_accuracy",
                context=f"{condition}/{qid}",
            ),
            "model_calls": parse_optional_float(raw["model_call_count_total"]),
        }
    if set(by_qid) != EXPECTED_QIDS:
        missing = sorted(EXPECTED_QIDS.difference(by_qid))
        extra = sorted(set(by_qid).difference(EXPECTED_QIDS))
        raise ValueError(
            f"{condition} batch-summary qid census mismatch; "
            f"missing={missing[:10]}, extra={extra[:10]}"
        )
    return by_qid


def merge_runtime_metrics(
    official_rows: list[dict[str, Any]],
    summaries: dict[str, dict[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for official in official_rows:
        qid = official["question_id"]
        output = dict(official)
        for condition in CONDITIONS:
            runtime = summaries[condition][qid]
            if (
                runtime["db_id"] != official["db_id"]
                or runtime["difficulty"] != official["difficulty"]
            ):
                raise ValueError(f"Official/batch metadata mismatch for {condition}/{qid}")
            output[f"{condition}_runtime_strict"] = runtime["runtime_strict"]
            output[f"{condition}_runtime_relaxed"] = runtime["runtime_relaxed"]
            output[f"{condition}_model_calls"] = runtime["model_calls"]
            output[f"{condition}_pipeline_status"] = runtime["pipeline_status"]
        merged.append(output)
    return merged


def official_values(rows: Sequence[dict[str, Any]], condition: str) -> list[int]:
    return [int(row[f"{condition}_official_ex"]) for row in rows]


def make_overall_summary(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        official = official_values(rows, condition)
        runtime_strict = [row[f"{condition}_runtime_strict"] for row in rows]
        runtime_relaxed = [row[f"{condition}_runtime_relaxed"] for row in rows]
        calls = [row[f"{condition}_model_calls"] for row in rows]
        official_stats = accuracy_fields(official)
        call_stats = call_summary(calls)
        strict_correct = sum(value == 1 for value in runtime_strict)
        relaxed_correct = sum(value == 1 for value in runtime_relaxed)
        strict_missing = sum(value is None for value in runtime_strict)
        relaxed_missing = sum(value is None for value in runtime_relaxed)
        output.append(
            {
                "condition": condition,
                "architecture": DISPLAY_NAMES[condition],
                **{f"official_{key}": value for key, value in official_stats.items()},
                "runtime_strict_correct": strict_correct,
                "runtime_strict_missing": strict_missing,
                "runtime_strict_accuracy": strict_correct / EXPECTED_N,
                "runtime_strict_percent": 100.0 * strict_correct / EXPECTED_N,
                "runtime_relaxed_correct": relaxed_correct,
                "runtime_relaxed_missing": relaxed_missing,
                "runtime_relaxed_accuracy": relaxed_correct / EXPECTED_N,
                "runtime_relaxed_percent": 100.0 * relaxed_correct / EXPECTED_N,
                "official_minus_runtime_strict_pp": 100.0
                * (sum(official) - strict_correct)
                / EXPECTED_N,
                "official_minus_runtime_relaxed_pp": 100.0
                * (sum(official) - relaxed_correct)
                / EXPECTED_N,
                **{f"model_calls_{key}": value for key, value in call_stats.items()},
            }
        )
    return output


def grouped_official_summary(
    rows: Sequence[dict[str, Any]], group_field: str, group_order: Iterable[str]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for group in group_order:
        selected = [row for row in rows if row[group_field] == group]
        if not selected:
            continue
        for condition in CONDITIONS:
            stats = accuracy_fields(official_values(selected, condition))
            output.append(
                {
                    group_field: group,
                    "condition": condition,
                    "architecture": DISPLAY_NAMES[condition],
                    **stats,
                }
            )
    return output


def make_pairwise_tests(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = (
        ("combined_vs_bif", "bif_sif", "bif_only"),
        ("combined_vs_sif", "bif_sif", "sif_only"),
        ("bif_vs_sif", "bif_only", "sif_only"),
    )
    raw_p: dict[str, float] = {}
    output: list[dict[str, Any]] = []
    for name, first, second in comparisons:
        first_values = official_values(rows, first)
        second_values = official_values(rows, second)
        first_only = sum(
            left == 1 and right == 0 for left, right in zip(first_values, second_values)
        )
        second_only = sum(
            left == 0 and right == 1 for left, right in zip(first_values, second_values)
        )
        discordant = first_only + second_only
        p_value = (
            float(binomtest(first_only, discordant, p=0.5, alternative="two-sided").pvalue)
            if discordant
            else 1.0
        )
        raw_p[name] = p_value
        output.append(
            {
                "comparison": name,
                "first_condition": first,
                "second_condition": second,
                "first_only_correct": first_only,
                "second_only_correct": second_only,
                "discordant_total": discordant,
                "first_official_ex": sum(first_values) / EXPECTED_N,
                "second_official_ex": sum(second_values) / EXPECTED_N,
                "paired_risk_difference": (first_only - second_only) / EXPECTED_N,
                "paired_difference_percentage_points": 100.0
                * (first_only - second_only)
                / EXPECTED_N,
                "exact_mcnemar_two_sided_p": p_value,
            }
        )
    adjusted = holm_adjust(raw_p)
    for row in output:
        row["holm_adjusted_p"] = adjusted[row["comparison"]]
    return output


def make_outcome_patterns(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(row["outcome_pattern"]) for row in rows)
    return [
        {
            "pattern": pattern,
            "bif_only": int(pattern[0]),
            "sif_only": int(pattern[1]),
            "bif_sif": int(pattern[2]),
            "interpretation": PATTERN_LABELS[pattern],
            "count": counts[pattern],
            "proportion": counts[pattern] / EXPECTED_N,
            "percent": 100.0 * counts[pattern] / EXPECTED_N,
        }
        for pattern in sorted(PATTERN_LABELS)
    ]


def make_metric_crosschecks(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        official = official_values(rows, condition)
        for metric, suffix in (
            ("runtime_strict", "runtime_strict"),
            ("runtime_relaxed", "runtime_relaxed"),
        ):
            diagnostic = [row[f"{condition}_{suffix}"] for row in rows]
            cells = Counter(zip(official, diagnostic))
            missing = cells[(0, None)] + cells[(1, None)]
            observed = EXPECTED_N - missing
            output.append(
                {
                    "condition": condition,
                    "architecture": DISPLAY_NAMES[condition],
                    "diagnostic_metric": metric,
                    "official_0_diagnostic_0": cells[(0, 0)],
                    "official_0_diagnostic_1": cells[(0, 1)],
                    "official_1_diagnostic_0": cells[(1, 0)],
                    "official_1_diagnostic_1": cells[(1, 1)],
                    "official_0_diagnostic_missing": cells[(0, None)],
                    "official_1_diagnostic_missing": cells[(1, None)],
                    "diagnostic_observed": observed,
                    "diagnostic_missing": missing,
                    "agreement_count": cells[(0, 0)] + cells[(1, 1)],
                    "agreement_percent_of_all_queries": 100.0
                    * (cells[(0, 0)] + cells[(1, 1)])
                    / EXPECTED_N,
                    "agreement_percent_of_observed": (
                        100.0 * (cells[(0, 0)] + cells[(1, 1)]) / observed
                        if observed
                        else None
                    ),
                }
            )
    return output


def make_pipeline_status(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        counts = Counter(str(row[f"{condition}_pipeline_status"]) for row in rows)
        for status, count in sorted(counts.items()):
            output.append(
                {
                    "condition": condition,
                    "architecture": DISPLAY_NAMES[condition],
                    "pipeline_status": status,
                    "count": count,
                    "percent": 100.0 * count / EXPECTED_N,
                }
            )
    return output


def integrity_report(
    rows: Sequence[dict[str, Any]],
    official_status: dict[str, Counter[str]],
    patterns: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    qids = [int(row["question_id"]) for row in rows]
    database_counts = Counter(str(row["db_id"]) for row in rows)
    difficulty_counts = Counter(str(row["difficulty"]) for row in rows)
    marginal_from_rows = {
        condition: sum(official_values(rows, condition)) for condition in CONDITIONS
    }
    marginal_from_patterns = {
        condition: sum(
            int(pattern[condition]) * int(pattern["count"]) for pattern in patterns
        )
        for condition in CONDITIONS
    }
    checks = {
        "n_is_1534": len(rows) == EXPECTED_N,
        "qids_are_unique": len(set(qids)) == EXPECTED_N,
        "qids_are_exactly_0_to_1533": set(qids) == EXPECTED_QIDS,
        "database_count_is_11": len(database_counts) == 11,
        "difficulty_total_is_1534": sum(difficulty_counts.values()) == EXPECTED_N,
        "outcome_pattern_total_is_1534": sum(int(row["count"]) for row in patterns)
        == EXPECTED_N,
        "pattern_marginals_match_official_rows": marginal_from_patterns == marginal_from_rows,
    }
    if not all(checks.values()):
        raise ValueError(f"Integrity checks failed: {checks}")
    return {
        "all_checks_passed": True,
        "checks": checks,
        "n_paired_queries": len(rows),
        "qid_min": min(qids),
        "qid_max": max(qids),
        "unique_qids": len(set(qids)),
        "database_count": len(database_counts),
        "queries_by_database": dict(sorted(database_counts.items())),
        "queries_by_difficulty": {
            level: difficulty_counts[level] for level in DIFFICULTY_ORDER
        },
        "official_status_by_condition": {
            condition: dict(sorted(official_status[condition].items()))
            for condition in CONDITIONS
        },
        "marginal_correct_from_query_rows": marginal_from_rows,
        "marginal_correct_from_outcome_patterns": marginal_from_patterns,
        "outcome_pattern_total": sum(int(row["count"]) for row in patterns),
    }


def main() -> int:
    script_path = Path(__file__).resolve()
    evidence_dir = script_path.parent
    paper_root = evidence_dir.parent
    dissertation_root = paper_root.parents[2]

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--official-input",
        type=Path,
        default=paper_root / "3_evidence_data" / "official_bird_ex_recomputed.csv",
    )
    parser.add_argument(
        "--bif-summary",
        type=Path,
        default=dissertation_root
        / "revised_final_bif_sif"
        / "0.bif"
        / "runs"
        / "0.bif_full_1534_20260821_2201"
        / "batch_summary.csv",
    )
    parser.add_argument(
        "--sif-summary",
        type=Path,
        default=dissertation_root
        / "revised_final_bif_sif"
        / "1.sif"
        / "runs"
        / "1.sif_full_1534_20260831_215201"
        / "batch_summary.csv",
    )
    parser.add_argument(
        "--combined-summary",
        type=Path,
        default=dissertation_root
        / "revised_final_bif_sif"
        / "2.bif_sif"
        / "runs"
        / "2.bif_sif_full_1534_20260823_012700"
        / "batch_summary.csv",
    )
    parser.add_argument("--evidence-dir", type=Path, default=evidence_dir)
    args = parser.parse_args()

    output_root = args.evidence_dir.resolve()
    input_dir = output_root / "inputs"
    output_dir = output_root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    sources = {
        "official_bird_ex_recomputed": args.official_input,
        "bif_only_batch_summary": args.bif_summary,
        "sif_only_batch_summary": args.sif_summary,
        "bif_sif_batch_summary": args.combined_summary,
    }
    snapshots, input_manifest = copy_inputs(sources, input_dir)
    write_json(input_dir / "quantitative_input_manifest_sha256.json", input_manifest)

    official_rows, official_status = build_official_paired(
        snapshots["official_bird_ex_recomputed"]
    )
    summaries = {
        "bif_only": read_batch_summary(snapshots["bif_only_batch_summary"], "bif_only"),
        "sif_only": read_batch_summary(snapshots["sif_only_batch_summary"], "sif_only"),
        "bif_sif": read_batch_summary(snapshots["bif_sif_batch_summary"], "bif_sif"),
    }
    paired_rows = merge_runtime_metrics(official_rows, summaries)

    overall = make_overall_summary(paired_rows)
    database_order = sorted({str(row["db_id"]) for row in paired_rows})
    by_database = grouped_official_summary(paired_rows, "db_id", database_order)
    by_difficulty = grouped_official_summary(
        paired_rows, "difficulty", DIFFICULTY_ORDER
    )
    pairwise = make_pairwise_tests(paired_rows)
    patterns = make_outcome_patterns(paired_rows)
    outcomes = [
        [
            int(row["bif_only_official_ex"]),
            int(row["sif_only_official_ex"]),
            int(row["bif_sif_official_ex"]),
        ]
        for row in paired_rows
    ]
    cochran = cochran_q(outcomes)
    metric_crosschecks = make_metric_crosschecks(paired_rows)
    pipeline_status = make_pipeline_status(paired_rows)
    integrity = integrity_report(paired_rows, official_status, patterns)
    official_status_rows = [
        {
            "condition": condition,
            "architecture": DISPLAY_NAMES[condition],
            "official_status": status,
            "count": count,
            "percent": 100.0 * count / EXPECTED_N,
        }
        for condition in CONDITIONS
        for status, count in sorted(official_status[condition].items())
    ]

    paired_fields = [
        "question_id",
        "db_id",
        "difficulty",
        "bif_only_official_ex",
        "sif_only_official_ex",
        "bif_sif_official_ex",
        "outcome_pattern",
    ]
    for condition in CONDITIONS:
        paired_fields.extend(
            [
                f"{condition}_runtime_strict",
                f"{condition}_runtime_relaxed",
                f"{condition}_model_calls",
                f"{condition}_pipeline_status",
            ]
        )

    overall_fields = list(overall[0])
    grouped_fields = [
        "db_id",
        "condition",
        "architecture",
        "n",
        "correct",
        "accuracy",
        "accuracy_percent",
        "wilson_95_lower",
        "wilson_95_upper",
        "wilson_95_lower_percent",
        "wilson_95_upper_percent",
    ]
    difficulty_fields = grouped_fields.copy()
    difficulty_fields[0] = "difficulty"
    pairwise_fields = list(pairwise[0])
    pattern_fields = list(patterns[0])
    crosscheck_fields = list(metric_crosschecks[0])
    status_fields = list(pipeline_status[0])
    official_status_fields = list(official_status_rows[0])

    write_csv(output_dir / "paired_query_metrics.csv", paired_rows, paired_fields)
    write_csv(output_dir / "overall_summary.csv", overall, overall_fields)
    write_csv(output_dir / "official_ex_by_database.csv", by_database, grouped_fields)
    write_csv(
        output_dir / "official_ex_by_difficulty.csv", by_difficulty, difficulty_fields
    )
    write_csv(output_dir / "pairwise_exact_mcnemar_holm.csv", pairwise, pairwise_fields)
    write_csv(output_dir / "outcome_patterns.csv", patterns, pattern_fields)
    write_csv(
        output_dir / "official_vs_runtime_diagnostics.csv",
        metric_crosschecks,
        crosscheck_fields,
    )
    write_csv(output_dir / "pipeline_status_summary.csv", pipeline_status, status_fields)
    write_csv(
        output_dir / "official_evaluation_status_summary.csv",
        official_status_rows,
        official_status_fields,
    )
    write_csv(
        output_dir / "cochran_q.csv",
        [
            {
                "statistic": cochran["statistic"],
                "df": cochran["df"],
                "p_value": cochran["p_value"],
            }
        ],
        ["statistic", "df", "p_value"],
    )
    write_json(output_dir / "integrity_report.json", integrity)

    complete_summary = {
        "analysis_scope": {
            "primary": "Official BIRD set-equality EX from the recomputed query-level file",
            "diagnostic": [
                "batch-summary runtime execution_accuracy",
                "batch-summary relaxed_execution_accuracy",
                "batch-summary model_call_count_total",
            ],
            "bootstrap_included": False,
            "qualitative_coding_included": False,
        },
        "input_manifest": input_manifest,
        "analysis_script": {
            "path": str(script_path),
            "sha256": sha256_file(script_path),
        },
        "software": {
            "python": platform.python_version(),
            "scipy": scipy.__version__,
            "platform": platform.platform(),
        },
        "integrity": integrity,
        "overall_summary": overall,
        "cochran_q": cochran,
        "pairwise_exact_mcnemar_holm": pairwise,
        "official_ex_by_database": by_database,
        "official_ex_by_difficulty": by_difficulty,
        "outcome_patterns": patterns,
        "official_vs_runtime_diagnostics": metric_crosschecks,
        "pipeline_status_summary": pipeline_status,
    }
    write_json(output_dir / "quantitative_summary.json", complete_summary)

    print(json.dumps(integrity, ensure_ascii=False, indent=2))
    print("\nOverall Official EX:")
    for row in overall:
        print(
            f"  {row['architecture']}: {row['official_correct']}/{row['official_n']} "
            f"= {row['official_accuracy_percent']:.4f}% "
            f"(Wilson 95% CI {row['official_wilson_95_lower_percent']:.4f}--"
            f"{row['official_wilson_95_upper_percent']:.4f}%)"
        )
    print(f"\nCochran Q({cochran['df']})={cochran['statistic']:.6f}, "
          f"p={cochran['p_value']:.12g}")
    print(f"\nWrote inputs to: {input_dir}")
    print(f"Wrote outputs to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
