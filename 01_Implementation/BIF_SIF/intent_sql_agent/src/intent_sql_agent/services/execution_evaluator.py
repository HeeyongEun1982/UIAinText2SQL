from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, Literal

from intent_sql_agent.contracts import ComparisonRecord, ExecutionRecord


def _canonical_value(value: Any, tolerance: float) -> Any:
    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isnan(numeric):
            return ["number", "nan"]
        if math.isinf(numeric):
            return ["number", "inf" if numeric > 0 else "-inf"]
        digits = max(0, int(round(-math.log10(tolerance)))) if tolerance < 1 else 0
        return ["number", round(numeric, digits)]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    return ["text", str(value)]


def _canonical_row(row: tuple[Any, ...], tolerance: float) -> str:
    values = [_canonical_value(value, tolerance) for value in row]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def canonicalize_execution_rows(
    execution: ExecutionRecord, *, numeric_tolerance: float = 1e-9
) -> list[str]:
    return [_canonical_row(row, numeric_tolerance) for row in execution.rows]


import itertools
from itertools import combinations


def _relaxed_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    # If string, check if it's a formatted number like "430.45%" or "$1,234.56"
    val_str = str(val).strip()
    clean_num = val_str.rstrip("%").lstrip("$").replace(",", "").strip()
    try:
        num = float(clean_num)
        return round(num, 2)
    except ValueError:
        return val_str.casefold()


def _check_relaxed_equivalence(gold: ExecutionRecord, generated: ExecutionRecord) -> bool:
    if not gold.rows or not generated.rows:
        return len(gold.rows) == len(generated.rows)
    if len(gold.rows) != len(generated.rows):
        return False

    num_gold_cols = len(gold.columns)
    num_gen_cols = len(generated.columns)
    if num_gen_cols < num_gold_cols:
        return False

    gold_relaxed_rows = [tuple(_relaxed_value(v) for v in row) for row in gold.rows]

    if num_gen_cols == num_gold_cols:
        gen_relaxed_rows = [tuple(_relaxed_value(v) for v in row) for row in generated.rows]
        if Counter(gold_relaxed_rows) == Counter(gen_relaxed_rows):
            return True
        for perm in itertools.permutations(range(num_gen_cols)):
            proj = [tuple(_relaxed_value(row[i]) for i in perm) for row in generated.rows]
            if Counter(gold_relaxed_rows) == Counter(proj):
                return True
        return False

    gen_col_indices = range(num_gen_cols)
    for col_combo in combinations(gen_col_indices, num_gold_cols):
        for perm in itertools.permutations(col_combo):
            proj = [tuple(_relaxed_value(row[i]) for i in perm) for row in generated.rows]
            if Counter(gold_relaxed_rows) == Counter(proj):
                return True

    return False


def compare_execution_results(
    gold: ExecutionRecord,
    generated: ExecutionRecord,
    *,
    policy: Literal["ordered", "multiset"] = "multiset",
    numeric_tolerance: float = 1e-9,
    mismatch_sample_limit: int = 20,
) -> ComparisonRecord:
    if numeric_tolerance <= 0:
        raise ValueError("numeric_tolerance must be positive")
    if gold.status != "success":
        return _not_comparable(policy, gold, generated, "REFERENCE_EXECUTION_FAILED")
    if generated.status != "success":
        return _not_comparable(policy, gold, generated, "GENERATED_EXECUTION_FAILED")
    if gold.truncated or generated.truncated:
        return _not_comparable(policy, gold, generated, "TRUNCATED_RESULT")

    column_match = len(gold.columns) == len(generated.columns)
    gold_rows = canonicalize_execution_rows(gold, numeric_tolerance=numeric_tolerance)
    generated_rows = canonicalize_execution_rows(generated, numeric_tolerance=numeric_tolerance)

    relaxed_equiv = _check_relaxed_equivalence(gold, generated)

    if policy == "ordered":
        equivalent = column_match and gold_rows == generated_rows
        mismatches = tuple(
            {"index": index, "gold": left, "generated": right}
            for index, (left, right) in enumerate(zip(gold_rows, generated_rows))
            if left != right
        )[:mismatch_sample_limit]
        return ComparisonRecord(
            comparison_policy=policy,
            comparable=True,
            equivalent=equivalent,
            execution_accuracy=int(equivalent),
            column_count_match=column_match,
            row_count_gold=len(gold_rows),
            row_count_generated=len(generated_rows),
            value_mismatches=mismatches,
            relaxed_equivalent=equivalent or relaxed_equiv,
            relaxed_execution_accuracy=int(equivalent or relaxed_equiv),
        )

    gold_counter = Counter(gold_rows)
    generated_counter = Counter(generated_rows)
    missing = list((gold_counter - generated_counter).elements())[:mismatch_sample_limit]
    extra = list((generated_counter - gold_counter).elements())[:mismatch_sample_limit]
    equivalent = column_match and not missing and not extra and len(gold_rows) == len(generated_rows)
    return ComparisonRecord(
        comparison_policy=policy,
        comparable=True,
        equivalent=equivalent,
        execution_accuracy=int(equivalent),
        column_count_match=column_match,
        row_count_gold=len(gold_rows),
        row_count_generated=len(generated_rows),
        missing_rows=tuple(missing),
        extra_rows=tuple(extra),
        relaxed_equivalent=equivalent or relaxed_equiv,
        relaxed_execution_accuracy=int(equivalent or relaxed_equiv),
    )


def _not_comparable(
    policy: Literal["ordered", "multiset"],
    gold: ExecutionRecord,
    generated: ExecutionRecord,
    reason: str,
) -> ComparisonRecord:
    return ComparisonRecord(
        comparison_policy=policy,
        comparable=False,
        equivalent=False,
        execution_accuracy=0,
        column_count_match=False,
        row_count_gold=gold.row_count,
        row_count_generated=generated.row_count,
        reason=reason,
        relaxed_equivalent=False,
        relaxed_execution_accuracy=0,
    )
