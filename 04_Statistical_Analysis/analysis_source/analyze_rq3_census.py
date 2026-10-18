"""Run the full RQ3 census for every query whose outcome differs by condition.

The eight outcome patterns use all 1,534 BIRD development queries.  This
script then examines all 486 queries in the six non-stable patterns.  SQL
differences are recorded as reproducible, multi-label structural indicators;
they are descriptive observations rather than causal failure labels.

The operator-designated qid 1--10 exclusion applies to the SIF-only process
trace used in RQ1.  RQ3 relates the preserved combined-condition
clarifications to SQL changes, and the combined run contains complete records
for these qids.  Therefore all 486 outcome-changing queries remain eligible
for the RQ3 clarification--SQL linkage census.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot

from analyze_rq3_traces import (
    CONDITIONS,
    INPUT_DIR,
    LOCUS_ORDER,
    OUTPUT_DIR,
    TARGET_PATTERNS,
    build_case_bundle,
    load_official_long,
    load_queries,
    make_wide,
    sha256,
    write_csv,
    write_json,
)


EXPECTED_PATTERN_COUNTS = {
    "001": 150,
    "010": 51,
    "011": 61,
    "100": 62,
    "101": 105,
    "110": 57,
}
SHARED_ANALYZER_PATH = Path(__file__).with_name("analyze_rq3_traces.py")

LOCUS_KO = {
    "source_join": "사용한 테이블 또는 조인",
    "filter_literal": "필터 조건 또는 비교값",
    "calculation_aggregation": "계산 또는 집계",
    "grouping_deduplication": "그룹화 또는 중복 처리",
    "output_projection": "출력 열과 표시 형식",
    "ordering_limit": "정렬, 결과 수 또는 동률 처리",
}

LOCUS_FEATURE_FIELDS = {
    "source_join": ("tables", "join_count", "join_columns"),
    "filter_literal": ("filter_columns", "filter_literals", "filter_operators"),
    "calculation_aggregation": (
        "aggregate_functions",
        "arithmetic_operators",
        "subquery_count",
    ),
    "grouping_deduplication": ("group_columns", "distinct"),
    "output_projection": ("projection",),
    "ordering_limit": ("order_columns", "order_directions", "limit"),
}


def joined(values: set[str] | list[str]) -> str:
    ordered = [name for name in LOCUS_ORDER if name in set(values)]
    return "|".join(ordered)


def list_from_flags(flags: dict[str, Any]) -> set[str]:
    return {name for name in LOCUS_ORDER if int(flags.get(name, 0)) == 1}


def mark_process_trace_scope(case: dict[str, Any], flat: dict[str, Any]) -> None:
    """Keep every combined-condition trace in the RQ3 full census."""

    flat["process_trace_eligible"] = 1
    flat["process_trace_exclusion_reason"] = ""
    case["process_trace_eligible"] = True
    case["process_trace_exclusion_reason"] = ""


def evidence_note(
    direction: str,
    transition_loci: set[str],
    combined_differences: set[str],
    execution_issue_conditions: list[str],
    process_eligible: bool,
) -> str:
    if execution_issue_conditions:
        names = ", ".join(execution_issue_conditions)
        opening = (
            f"정오 전환에 {names} 조건의 실행 실패 또는 시간 초과가 포함되어, "
            "SQL 구조 차이를 정오 변화의 설명 항목으로 분류하지 않았다."
        )
    elif transition_loci:
        names = ", ".join(LOCUS_KO[name] for name in LOCUS_ORDER if name in transition_loci)
        if direction == "combined_correct":
            opening = (
                f"결합 SQL은 {names}에서 ground-truth SQL과 구조적으로 일치했고, "
                "오답인 단독 조건 중 적어도 하나는 같은 항목에서 차이를 보였다."
            )
        else:
            opening = (
                f"결합 SQL은 {names}에서 ground-truth SQL과 구조적 차이를 보였고, "
                "정답인 단독 조건 중 적어도 하나는 같은 항목에서 일치했다."
            )
    elif combined_differences:
        names = ", ".join(
            LOCUS_KO[name] for name in LOCUS_ORDER if name in combined_differences
        )
        opening = (
            f"결합 SQL과 ground-truth SQL의 구조적 차이는 {names}에서 탐지되었으나, "
            "단독 조건과 대비되는 전환 항목은 이 규칙으로 특정되지 않았다."
        )
    else:
        opening = (
            "여섯 구조 항목에서는 결합 SQL과 ground-truth SQL의 차이가 탐지되지 "
            "않았으며, 실행 결과 차이는 이 구조 비교만으로 설명되지 않았다."
        )

    if not process_eligible:
        return opening + " 이 문항의 처리 기록은 사전에 정한 자료 품질 제외 범위에 속한다."
    return opening + " 명확화 질문과 답변의 원문은 같은 사례 기록에 함께 보존하였다."


def enrich_cases(
    bundle: list[dict[str, Any]], coding: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flat_by_qid = {int(row["question_id"]): row for row in coding}
    for case in bundle:
        qid = int(case["question_id"])
        flat = flat_by_qid[qid]
        mark_process_trace_scope(case, flat)

        combined = case["conditions"]["bif_sif"]
        combined_differences = list_from_flags(combined["difference_loci"])
        failed_singles = [
            name
            for name in ("bif_only", "sif_only")
            if int(case["conditions"][name]["official_ex"]) == 0
        ]
        successful_singles = [
            name
            for name in ("bif_only", "sif_only")
            if int(case["conditions"][name]["official_ex"]) == 1
        ]
        aligned = {
            locus
            for locus in LOCUS_ORDER
            if locus not in combined_differences
            and any(
                int(case["conditions"][name]["difference_loci"][locus]) == 1
                for name in failed_singles
            )
        }
        diverged = {
            locus
            for locus in LOCUS_ORDER
            if locus in combined_differences
            and any(
                int(case["conditions"][name]["difference_loci"][locus]) == 0
                for name in successful_singles
            )
        }
        direction = "combined_correct" if str(case["pattern"])[2] == "1" else "combined_incorrect"
        transition_loci = aligned if direction == "combined_correct" else diverged
        combined_topics = set(combined["clarification_topics"])
        process_eligible = bool(flat["process_trace_eligible"])
        execution_issue_conditions = [
            name for name in CONDITIONS if str(case["conditions"][name]["status"]) != "ok"
        ]
        all_executions_ok = not execution_issue_conditions
        transition_analysis_loci = transition_loci if all_executions_ok else set()
        status = str(combined["status"])

        analysis = {
            "direction": direction,
            "combined_ground_truth_difference_loci": [
                name for name in LOCUS_ORDER if name in combined_differences
            ],
            "combined_alignment_loci_vs_failed_singles": [
                name for name in LOCUS_ORDER if name in aligned
            ],
            "combined_divergence_loci_vs_successful_singles": [
                name for name in LOCUS_ORDER if name in diverged
            ],
            "transition_relevant_loci": [
                name for name in LOCUS_ORDER if name in transition_loci
            ],
            "transition_analysis_loci": [
                name for name in LOCUS_ORDER if name in transition_analysis_loci
            ],
            "combined_clarification_topics": [
                name for name in LOCUS_ORDER if name in combined_topics
            ],
            "combined_status": status,
            "all_condition_executions_ok": all_executions_ok,
            "execution_issue_conditions": execution_issue_conditions,
            "evidence_note_ko": evidence_note(
                direction,
                transition_loci,
                combined_differences,
                execution_issue_conditions,
                process_eligible,
            ),
        }
        case["census_analysis"] = analysis
        flat.update(
            {
                "direction": direction,
                "combined_ground_truth_difference_loci": joined(combined_differences),
                "combined_alignment_loci_vs_failed_singles": joined(aligned),
                "combined_divergence_loci_vs_successful_singles": joined(diverged),
                "transition_relevant_loci": joined(transition_loci),
                "transition_relevant_locus_count": len(transition_loci),
                "transition_analysis_loci": joined(transition_analysis_loci),
                "transition_analysis_locus_count": len(transition_analysis_loci),
                "combined_clarification_topics": joined(combined_topics),
                "combined_execution_status": status,
                "all_condition_executions_ok": int(all_executions_ok),
                "execution_issue_conditions": "|".join(execution_issue_conditions),
                "evidence_note_ko": analysis["evidence_note_ko"],
            }
        )
    return bundle, coding


def subsets(rows: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    for pattern in TARGET_PATTERNS:
        direction = "combined_correct" if pattern[2] == "1" else "combined_incorrect"
        groups.append((pattern, direction, [row for row in rows if row["pattern"] == pattern]))
    groups.append(
        ("ALL", "combined_correct", [row for row in rows if row["direction"] == "combined_correct"])
    )
    groups.append(
        ("ALL", "combined_incorrect", [row for row in rows if row["direction"] == "combined_incorrect"])
    )
    groups.append(("ALL", "all_changed", rows))
    return groups


def contains(row: dict[str, Any], field: str, locus: str) -> bool:
    return locus in {value for value in str(row.get(field, "")).split("|") if value}


def locus_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for pattern, direction, group in subsets(rows):
        analysis_eligible = [row for row in group if int(row["all_condition_executions_ok"]) == 1]
        for locus in LOCUS_ORDER:
            difference_n = sum(
                contains(row, "combined_ground_truth_difference_loci", locus) for row in group
            )
            transition_n = sum(
                contains(row, "transition_analysis_loci", locus) for row in analysis_eligible
            )
            output.append(
                {
                    "pattern": pattern,
                    "direction": direction,
                    "locus": locus,
                    "locus_ko": LOCUS_KO[locus],
                    "query_n": len(group),
                    "combined_ground_truth_difference_n": difference_n,
                    "combined_ground_truth_difference_percent": round(100 * difference_n / len(group), 2),
                    "transition_relevant_n": transition_n,
                    "transition_analysis_eligible_n": len(analysis_eligible),
                    "transition_relevant_percent": (
                        round(100 * transition_n / len(analysis_eligible), 2)
                        if analysis_eligible
                        else 0.0
                    ),
                }
            )
    return output


def status_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for pattern, direction, group in subsets(rows):
        counts = Counter(str(row["combined_execution_status"]) for row in group)
        for status, count in sorted(counts.items()):
            output.append(
                {
                    "pattern": pattern,
                    "direction": direction,
                    "status": status,
                    "count": count,
                    "percent": round(100 * count / len(group), 2),
                    "query_n": len(group),
                }
            )
    return output


def signature(features: dict[str, Any], locus: str) -> str:
    """Return the canonical signature created by the shared SQL analyser."""

    return str(features.get(f"{locus}_signature") or "")


def pairwise_transitions(bundle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one row per discordant pair and SQL-difference dimension.

    The same qid appears in both comparisons for patterns 001 and 110.  These
    rows therefore support pair-specific interpretation and must not be added
    together as though they were unique-query counts.
    """

    output: list[dict[str, Any]] = []
    for case in bundle:
        combined = case["conditions"]["bif_sif"]
        for single, comparison in (
            ("bif_only", "BIF--SIF_vs_BIF-only"),
            ("sif_only", "BIF--SIF_vs_SIF-only"),
        ):
            baseline = case["conditions"][single]
            if int(baseline["official_ex"]) == int(combined["official_ex"]):
                continue
            direction = (
                "combined_correct"
                if int(combined["official_ex"]) == 1
                else "combined_incorrect"
            )
            execution_eligible = (
                str(baseline["status"]) == "ok" and str(combined["status"]) == "ok"
            )
            for locus in LOCUS_ORDER:
                single_diff = int(baseline["difference_loci"][locus])
                combined_diff = int(combined["difference_loci"][locus])
                single_signature = signature(baseline["features"], locus)
                combined_signature = signature(combined["features"], locus)
                if single_diff and not combined_diff:
                    relation = "ground_truth_difference_removed"
                elif not single_diff and combined_diff:
                    relation = "ground_truth_difference_introduced"
                elif single_diff and combined_diff and single_signature != combined_signature:
                    relation = "both_differ_changed_signature"
                elif single_diff and combined_diff:
                    relation = "both_differ_same_signature"
                else:
                    relation = "both_match_ground_truth_signature"
                output.append(
                    {
                        "comparison": comparison,
                        "single_condition": single,
                        "pattern": case["pattern"],
                        "question_id": int(case["question_id"]),
                        "db_id": case["db_id"],
                        "difficulty": case["difficulty"],
                        "outcome_direction": direction,
                        "locus": locus,
                        "locus_ko": LOCUS_KO[locus],
                        "single_ground_truth_difference": single_diff,
                        "combined_ground_truth_difference": combined_diff,
                        "pair_signature_relation": relation,
                        "single_signature": single_signature,
                        "combined_signature": combined_signature,
                        "structural_transition_eligible": int(execution_eligible),
                        "process_trace_eligible": int(case["process_trace_eligible"]),
                    }
                )
    return output


def pairwise_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    comparisons = sorted({str(row["comparison"]) for row in rows})
    directions = ("combined_correct", "combined_incorrect")
    relations = (
        "ground_truth_difference_removed",
        "ground_truth_difference_introduced",
        "both_differ_changed_signature",
        "both_differ_same_signature",
        "both_match_ground_truth_signature",
    )
    for comparison in comparisons:
        for direction in directions:
            pair_rows = [
                row
                for row in rows
                if row["comparison"] == comparison
                and row["outcome_direction"] == direction
                and row["locus"] == LOCUS_ORDER[0]
            ]
            eligible_qids = {
                int(row["question_id"])
                for row in pair_rows
                if int(row["structural_transition_eligible"]) == 1
            }
            for locus in LOCUS_ORDER:
                locus_rows = [
                    row
                    for row in rows
                    if row["comparison"] == comparison
                    and row["outcome_direction"] == direction
                    and row["locus"] == locus
                    and int(row["structural_transition_eligible"]) == 1
                ]
                counts = Counter(str(row["pair_signature_relation"]) for row in locus_rows)
                output.append(
                    {
                        "comparison": comparison,
                        "outcome_direction": direction,
                        "locus": locus,
                        "locus_ko": LOCUS_KO[locus],
                        "discordant_pair_n": len(pair_rows),
                        "structural_transition_eligible_n": len(eligible_qids),
                        **{relation: counts[relation] for relation in relations},
                    }
                )
    return output


def census_manifest(bundle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one auditable index row for each outcome-changing query."""

    rows: list[dict[str, Any]] = []
    for case in bundle:
        statuses = {
            name: str(case["conditions"][name]["status"])
            for name in CONDITIONS
        }
        rows.append(
            {
                "question_id": int(case["question_id"]),
                "pattern": str(case["pattern"]),
                "db_id": str(case["db_id"]),
                "difficulty": str(case["difficulty"]),
                "bif_only_official_ex": int(case["conditions"]["bif_only"]["official_ex"]),
                "sif_only_official_ex": int(case["conditions"]["sif_only"]["official_ex"]),
                "bif_sif_official_ex": int(case["conditions"]["bif_sif"]["official_ex"]),
                "direction": str(case["census_analysis"]["direction"]),
                "all_condition_executions_ok": int(
                    bool(case["census_analysis"]["all_condition_executions_ok"])
                ),
                "execution_issue_conditions": "|".join(
                    case["census_analysis"]["execution_issue_conditions"]
                ),
                "bif_only_status": statuses["bif_only"],
                "sif_only_status": statuses["sif_only"],
                "bif_sif_status": statuses["bif_sif"],
                "process_trace_eligible": int(bool(case["process_trace_eligible"])),
                "process_trace_exclusion_reason": str(
                    case["process_trace_exclusion_reason"]
                ),
            }
        )
    return rows


def condition_diagnostics(bundle: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one row per query and condition (486 x 3 = 1,458 rows)."""

    rows: list[dict[str, Any]] = []
    for case in bundle:
        ground_features = case["ground_truth_features"]
        for condition in CONDITIONS:
            observed = case["conditions"][condition]
            features = observed["features"]
            differences = observed["difference_loci"]
            row: dict[str, Any] = {
                "question_id": int(case["question_id"]),
                "pattern": str(case["pattern"]),
                "db_id": str(case["db_id"]),
                "difficulty": str(case["difficulty"]),
                "condition": condition,
                "official_ex": int(observed["official_ex"]),
                "execution_status": str(observed["status"]),
                "process_trace_eligible": int(bool(case["process_trace_eligible"])),
                "process_trace_exclusion_reason": str(
                    case["process_trace_exclusion_reason"]
                ),
                "predicted_sql": str(observed["predicted_sql"]),
                "ground_truth_sql": str(case["ground_truth_sql"]),
                "candidate_parse_ok": int(bool(features["parse_ok"])),
                "candidate_parse_error": str(features["parse_error"]),
                "ground_truth_parse_ok": int(bool(ground_features["parse_ok"])),
                "ground_truth_parse_error": str(ground_features["parse_error"]),
                "difference_loci": joined(list_from_flags(differences)),
                "difference_locus_count": sum(int(value) for value in differences.values()),
                "multiple_difference_loci": int(
                    sum(int(value) for value in differences.values()) >= 2
                ),
                "directional_difference_labels": "|".join(
                    observed["directional_difference_labels"]
                ),
                "clarification_turns": len(observed["clarification"]),
                "clarification_topics": joined(observed["clarification_topics"]),
            }
            for locus in LOCUS_ORDER:
                row[f"{locus}_different"] = int(differences[locus])
                row[f"candidate_{locus}_signature"] = signature(features, locus)
                row[f"ground_truth_{locus}_signature"] = signature(
                    ground_features, locus
                )
            rows.append(row)
    return rows


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    long = load_official_long()
    queries = load_queries()
    wide = make_wide(long)
    census = [
        {
            "pattern": row["pattern"],
            "question_id": row["question_id"],
            "db_id": row["db_id"],
            "difficulty": row["difficulty"],
        }
        for row in wide
        if row["pattern"] in TARGET_PATTERNS
    ]
    census.sort(key=lambda row: (row["pattern"], int(row["question_id"])))
    counts = Counter(str(row["pattern"]) for row in census)
    if len(census) != 486 or counts != Counter(EXPECTED_PATTERN_COUNTS):
        raise ValueError(f"Unexpected RQ3 census: n={len(census)}, patterns={counts}")

    bundle, coding = build_case_bundle(census, long, queries)
    bundle, coding = enrich_cases(bundle, coding)

    bundle_path = INPUT_DIR / "rq3_census_case_bundle.jsonl"
    with bundle_path.open("w", encoding="utf-8") as handle:
        for case in bundle:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    coding_path = OUTPUT_DIR / "rq3_census_structural_coding.csv"
    manifest_path = OUTPUT_DIR / "rq3_census_manifest.csv"
    diagnostics_path = OUTPUT_DIR / "rq3_census_condition_diagnostics.csv"
    locus_path = OUTPUT_DIR / "rq3_census_locus_summary.csv"
    status_path = OUTPUT_DIR / "rq3_census_execution_status.csv"
    pairwise_path = OUTPUT_DIR / "rq3_census_pairwise_transitions.csv"
    pairwise_summary_path = OUTPUT_DIR / "rq3_census_pairwise_summary.csv"
    write_csv(coding_path, coding)
    write_csv(manifest_path, census_manifest(bundle))
    write_csv(diagnostics_path, condition_diagnostics(bundle))
    write_csv(locus_path, locus_summary(coding))
    write_csv(status_path, status_summary(coding))
    pairwise = pairwise_transitions(bundle)
    write_csv(pairwise_path, pairwise)
    write_csv(pairwise_summary_path, pairwise_summary(pairwise))

    positive = [row for row in coding if row["direction"] == "combined_correct"]
    negative = [row for row in coding if row["direction"] == "combined_incorrect"]
    process_excluded = [row for row in coding if int(row["process_trace_eligible"]) == 0]
    summary = {
        "scope": "Full census of all outcome-changing queries; multi-label structural observations are descriptive, not causal",
        "all_benchmark_queries": 1534,
        "stable_outcome_queries": 1048,
        "changed_outcome_queries": len(coding),
        "combined_correct_changed_queries": len(positive),
        "combined_incorrect_changed_queries": len(negative),
        "pattern_counts": dict(counts),
        "process_trace_eligible_changed_queries": len(coding) - len(process_excluded),
        "process_trace_excluded_changed_queries": len(process_excluded),
        "process_trace_excluded_qids": sorted(
            int(row["question_id"]) for row in process_excluded
        ),
        "transition_locus_counts_among_all_executions_ok": {
            "combined_correct": dict(
                Counter(
                    locus
                    for row in positive
                    if int(row["all_condition_executions_ok"]) == 1
                    for locus in str(row["transition_analysis_loci"]).split("|")
                    if locus
                )
            ),
            "combined_incorrect": dict(
                Counter(
                    locus
                    for row in negative
                    if int(row["all_condition_executions_ok"]) == 1
                    for locus in str(row["transition_analysis_loci"]).split("|")
                    if locus
                )
            ),
        },
        "all_executions_ok_queries": {
            "combined_correct": sum(int(row["all_condition_executions_ok"]) for row in positive),
            "combined_incorrect": sum(int(row["all_condition_executions_ok"]) for row in negative),
        },
        "queries_without_detected_transition_locus_among_all_executions_ok": {
            "combined_correct": sum(
                int(row["all_condition_executions_ok"]) == 1
                and not row["transition_analysis_loci"]
                for row in positive
            ),
            "combined_incorrect": sum(
                int(row["all_condition_executions_ok"]) == 1
                and not row["transition_analysis_loci"]
                for row in negative
            ),
        },
        "combined_execution_status": dict(Counter(row["combined_execution_status"] for row in coding)),
        "execution_issue_queries": [
            {
                "question_id": int(row["question_id"]),
                "pattern": row["pattern"],
                "conditions": str(row["execution_issue_conditions"]).split("|"),
            }
            for row in coding
            if int(row["all_condition_executions_ok"]) == 0
        ],
        "pairwise_discordant_query_counts": {
            "BIF--SIF_vs_BIF-only": len(
                {
                    int(row["question_id"])
                    for row in pairwise
                    if row["comparison"] == "BIF--SIF_vs_BIF-only"
                }
            ),
            "BIF--SIF_vs_SIF-only": len(
                {
                    int(row["question_id"])
                    for row in pairwise
                    if row["comparison"] == "BIF--SIF_vs_SIF-only"
                }
            ),
        },
        "pairwise_counting_note": (
            "Patterns 001 and 110 enter both pairwise comparisons; pairwise rows must not "
            "be summed and interpreted as unique changed queries."
        ),
        "clarification_linkage_note": (
            "All eligible clarification records are preserved case by case. Broad topic-keyword "
            "overlap is not reported as evidence because the combined condition asks four fixed "
            "SIF inspection axes for every query."
        ),
        "sql_parser": f"sqlglot {sqlglot.__version__}",
        "structural_comparison": (
            "Canonical parsed signatures preserve string-literal case, join type and ON predicates, "
            "WHERE/HAVING logic, calculation and grouping in nested queries, final projection order, "
            "and ordering/limit details. Locus flags are descriptive multi-label observations."
        ),
        "inputs": {
            "official_bird_ex_recomputed.csv": sha256(INPUT_DIR / "official_bird_ex_recomputed.csv"),
            "bird_dev_queries.csv": sha256(INPUT_DIR / "bird_dev_queries.csv"),
        },
        "outputs": {
            bundle_path.name: sha256(bundle_path),
            coding_path.name: sha256(coding_path),
            manifest_path.name: sha256(manifest_path),
            diagnostics_path.name: sha256(diagnostics_path),
            locus_path.name: sha256(locus_path),
            status_path.name: sha256(status_path),
            pairwise_path.name: sha256(pairwise_path),
            pairwise_summary_path.name: sha256(pairwise_summary_path),
        },
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "shared_analyzer": {
            "path": str(SHARED_ANALYZER_PATH.resolve()),
            "sha256": sha256(SHARED_ANALYZER_PATH),
        },
    }
    summary_path = OUTPUT_DIR / "rq3_census_summary.json"
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
