"""Run cross-file integrity checks for the Chapter 5 submission package."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
CHAPTER = HERE.parent / "5_results_discussion_draft_ko.tex"
INPUT = HERE / "inputs"
OUTPUT = HERE / "outputs"
FIGURES = HERE / "figures"
EXPECTED_N = 1_534
CONDITIONS = ("bif_only", "sif_only", "bif_sif")
EXPECTED_CORRECT = {"bif_only": 916, "sif_only": 861, "bif_sif": 1008}
EXPECTED_PATTERNS = {
    "000": 356,
    "001": 150,
    "010": 51,
    "011": 61,
    "100": 62,
    "101": 105,
    "110": 57,
    "111": 692,
}
EXPECTED_CHANGED_PATTERNS = {
    "001": 150,
    "010": 51,
    "011": 61,
    "100": 62,
    "101": 105,
    "110": 57,
}
EXPECTED_RQ3_EXECUTION_ISSUES = {
    (102, "bif_only", "timeout"),
    (904, "sif_only", "timeout"),
    (956, "sif_only", "timeout"),
    (637, "bif_sif", "timeout"),
    (1050, "bif_sif", "timeout"),
}
EXPECTED_PAIRWISE_QIDS = {
    "BIF--SIF_vs_BIF-only": 330,
    "BIF--SIF_vs_SIF-only": 363,
}
EXPECTED_RQ1 = {
    "bif_only": {
        "MC": (51, 40),
        "OR": (17, 15),
        "PL": (46, 38),
        "RG": (106, 81),
        "RS": (103, 82),
        "SC": (31, 28),
        "TM": (38, 34),
        "UG": (40, 33),
    },
    "sif_only": {
        "ATTRIBUTE": (12, 12),
        "GAP": (41, 39),
        "METRIC": (62, 60),
        "OUTPUT": (86, 84),
        "RANK": (10, 10),
        "REL": (87, 87),
        "SCOPE": (52, 50),
        "TEMP": (15, 15),
        "VALUE": (10, 9),
    },
}
def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    checks: list[dict[str, object]] = []

    official = read_csv(INPUT / "official_bird_ex_recomputed.csv")
    require(len(official) == EXPECTED_N * 3, "Official EX long table must have 4,602 rows")
    official_keys = {(row["condition"], int(row["question_id"])) for row in official}
    require(len(official_keys) == len(official), "Official EX contains duplicate condition/qid")
    for condition in CONDITIONS:
        qids = {qid for name, qid in official_keys if name == condition}
        require(qids == set(range(EXPECTED_N)), f"{condition} qid census is incomplete")
        correct = sum(
            int(row["official_ex"])
            for row in official
            if row["condition"] == condition
        )
        require(correct == EXPECTED_CORRECT[condition], f"{condition} correct total changed")
    checks.append({"name": "official_ex_census_and_margins", "status": "pass"})

    archived_crosswalk = read_csv(
        OUTPUT / "combined_archived_vs_recomputed_official_ex_crosswalk.csv"
    )
    archived_summary = json.loads(
        (OUTPUT / "combined_archived_vs_recomputed_official_ex_summary.json").read_text(
            encoding="utf-8"
        )
    )
    require(len(archived_crosswalk) == EXPECTED_N, "Archived/recomputed crosswalk must have 1,534 rows")
    require(
        {int(row["question_id"]) for row in archived_crosswalk} == set(range(EXPECTED_N)),
        "Archived/recomputed crosswalk qid census is incomplete",
    )
    require(
        archived_summary["archived_correct"] == 618
        and archived_summary["recomputed_correct"] == 1008
        and archived_summary["old_to_new_transition_counts"]
        == {"0_to_0": 512, "0_to_1": 404, "1_to_0": 14, "1_to_1": 604}
        and archived_summary["archived_gen_row_count_minus_one"]
        == {"new_correct": 387, "new_incorrect": 282, "total": 669},
        "Archived/recomputed Official EX crosswalk counts changed",
    )
    checks.append({"name": "combined_archived_vs_recomputed_official_ex", "status": "pass"})

    official_by_qid: dict[int, dict[str, int]] = {}
    for row in official:
        official_by_qid.setdefault(int(row["question_id"]), {})[row["condition"]] = int(
            row["official_ex"]
        )
    changed_from_official: dict[int, str] = {}
    for qid, outcomes in official_by_qid.items():
        require(set(outcomes) == set(CONDITIONS), f"qid {qid} is missing an Official EX condition")
        pattern = "".join(str(outcomes[name]) for name in CONDITIONS)
        if pattern not in {"000", "111"}:
            changed_from_official[qid] = pattern
    require(len(changed_from_official) == 486, "Official EX no longer yields 486 changed qids")
    require(
        Counter(changed_from_official.values()) == Counter(EXPECTED_CHANGED_PATTERNS),
        "Changed-query pattern counts no longer match 150/51/61/62/105/57",
    )
    checks.append({"name": "rq3_changed_qid_census_from_official_ex", "status": "pass"})

    patterns = read_csv(OUTPUT / "outcome_patterns.csv")
    observed_patterns = {
        str(row["pattern"]).zfill(3): int(row["count"]) for row in patterns
    }
    require(observed_patterns == EXPECTED_PATTERNS, "Outcome pattern counts changed")
    require(sum(observed_patterns.values()) == EXPECTED_N, "Outcome patterns do not sum to 1,534")
    require(
        sum(count for pattern, count in observed_patterns.items() if pattern[0] == "1") == 916,
        "BIF margin does not match pattern table",
    )
    require(
        sum(count for pattern, count in observed_patterns.items() if pattern[1] == "1") == 861,
        "SIF margin does not match pattern table",
    )
    require(
        sum(count for pattern, count in observed_patterns.items() if pattern[2] == "1") == 1008,
        "Combined margin does not match pattern table",
    )
    checks.append({"name": "outcome_patterns_and_margins", "status": "pass"})

    overall = {row["condition"]: row for row in read_csv(OUTPUT / "overall_summary.csv")}
    require(set(overall) == set(CONDITIONS), "Overall summary condition set changed")
    for condition, correct in EXPECTED_CORRECT.items():
        require(int(overall[condition]["official_correct"]) == correct, "Overall correct total mismatch")
        require(int(overall[condition]["official_n"]) == EXPECTED_N, "Overall N mismatch")
    pairwise = {row["comparison"]: row for row in read_csv(OUTPUT / "pairwise_exact_mcnemar_holm.csv")}
    require(
        (int(pairwise["combined_vs_bif"]["first_only_correct"]), int(pairwise["combined_vs_bif"]["second_only_correct"]))
        == (211, 119),
        "Combined/BIF discordant counts changed",
    )
    require(
        (int(pairwise["combined_vs_sif"]["first_only_correct"]), int(pairwise["combined_vs_sif"]["second_only_correct"]))
        == (255, 108),
        "Combined/SIF discordant counts changed",
    )
    cochran = read_csv(OUTPUT / "cochran_q.csv")
    require(len(cochran) == 1 and math.isclose(float(cochran[0]["statistic"]), 68.1028806584, rel_tol=1e-10), "Cochran Q changed")
    checks.append({"name": "overall_and_paired_statistics", "status": "pass"})

    rq1 = read_csv(OUTPUT / "rq1_turn_labels_final.csv")
    rq1_included = [row for row in rq1 if row.get("rq1_included") == "1"]
    rq1_counts = Counter(row["architecture"] for row in rq1_included)
    require(rq1_counts == Counter({"bif_only": 432, "sif_only": 375}), "RQ1 turn census changed")
    require(all(str(row.get("unresolved", "0")) in {"0", "0.0", "False", "false"} for row in rq1_included), "RQ1 contains unresolved labels")
    native = read_csv(OUTPUT / "rq1_native_distribution_final.csv")
    for architecture, expected in (("bif_only", 432), ("sif_only", 375)):
        require(
            sum(int(row["turn_count"]) for row in native if row["architecture"] == architecture) == expected,
            f"{architecture} RQ1 distribution does not sum to the turn census",
        )
    observed_rq1 = {
        architecture: {
            row["category"]: (int(row["turn_count"]), int(row["query_count"]))
            for row in native
            if row["architecture"] == architecture
        }
        for architecture in EXPECTED_RQ1
    }
    require(observed_rq1 == EXPECTED_RQ1, "RQ1 category or query counts changed")
    require(
        all(row.get("full_content_reviewed") == "1" for row in rq1_included),
        "RQ1 contains a turn without full-content review",
    )
    rq1_summary = json.loads((OUTPUT / "rq1_final_coding_summary.json").read_text(encoding="utf-8"))
    require(
        rq1_summary.get("coding")
        == "language-model-assisted full-content review of every included turn",
        "RQ1 review provenance changed",
    )
    checks.append({"name": "rq1_assisted_full_review_and_distributions", "status": "pass"})

    manifest = read_csv(OUTPUT / "rq3_census_manifest.csv")
    manifest_qids = [int(row["question_id"]) for row in manifest]
    require(len(manifest) == 486, "RQ3 census manifest must contain 486 rows")
    require(len(set(manifest_qids)) == 486, "RQ3 census manifest contains duplicate qids")
    require(set(manifest_qids) == set(changed_from_official), "RQ3 manifest qids differ from Official EX")
    require(
        {
            int(row["question_id"]): str(row["pattern"]).zfill(3)
            for row in manifest
        }
        == changed_from_official,
        "RQ3 manifest pattern assignments differ from Official EX",
    )
    eligible_qids = {
        int(row["question_id"])
        for row in manifest
        if row.get("process_trace_eligible") == "1"
    }
    excluded_qids = set(manifest_qids) - eligible_qids
    require(len(eligible_qids) == 486, "RQ3 process-trace census must contain all 486 qids")
    require(not excluded_qids, "RQ3 combined-condition process census must not exclude qids")
    checks.append({"name": "rq3_census_manifest_and_process_scope", "status": "pass"})

    diagnostics = read_csv(OUTPUT / "rq3_census_condition_diagnostics.csv")
    diagnostic_keys = {
        (int(row["question_id"]), row["condition"]) for row in diagnostics
    }
    expected_diagnostic_keys = {
        (qid, condition) for qid in changed_from_official for condition in CONDITIONS
    }
    require(len(diagnostics) == 486 * 3, "RQ3 condition diagnostics must contain 1,458 rows")
    require(
        len(diagnostic_keys) == len(diagnostics),
        "RQ3 condition diagnostics contains duplicate qid/condition rows",
    )
    require(
        diagnostic_keys == expected_diagnostic_keys,
        "RQ3 condition diagnostics is not the complete 486 x 3 census",
    )
    require(
        all(row.get("candidate_parse_ok") == "1" for row in diagnostics),
        "At least one RQ3 candidate SQL failed to parse",
    )
    require(
        all(row.get("ground_truth_parse_ok") == "1" for row in diagnostics),
        "At least one RQ3 ground-truth SQL failed to parse",
    )
    execution_issues = {
        (int(row["question_id"]), row["condition"], row["execution_status"])
        for row in diagnostics
        if row.get("execution_status") != "ok"
    }
    require(
        execution_issues == EXPECTED_RQ3_EXECUTION_ISSUES,
        "RQ3 execution issues no longer match the five expected qid/condition timeouts",
    )
    checks.append({"name": "rq3_condition_census_parse_and_execution_status", "status": "pass"})

    transitions = read_csv(OUTPUT / "rq3_census_pairwise_transitions.csv")
    require(
        set(row["comparison"] for row in transitions) == set(EXPECTED_PAIRWISE_QIDS),
        "RQ3 pairwise comparison names changed",
    )
    for comparison, expected_n in EXPECTED_PAIRWISE_QIDS.items():
        rows = [row for row in transitions if row["comparison"] == comparison]
        pair_qids = {int(row["question_id"]) for row in rows}
        require(len(pair_qids) == expected_n, f"{comparison} must contain {expected_n} unique qids")
        require(
            len(rows) == expected_n * 6,
            f"{comparison} must contain six structural-locus rows per qid",
        )
        require(
            all(
                len({row["locus"] for row in rows if int(row["question_id"]) == qid}) == 6
                for qid in pair_qids
            ),
            f"{comparison} has a qid without all six structural loci",
        )
    expected_bif_pair = {
        qid
        for qid, outcomes in official_by_qid.items()
        if outcomes["bif_sif"] != outcomes["bif_only"]
    }
    expected_sif_pair = {
        qid
        for qid, outcomes in official_by_qid.items()
        if outcomes["bif_sif"] != outcomes["sif_only"]
    }
    observed_pair_qids = {
        comparison: {
            int(row["question_id"])
            for row in transitions
            if row["comparison"] == comparison
        }
        for comparison in EXPECTED_PAIRWISE_QIDS
    }
    require(
        observed_pair_qids["BIF--SIF_vs_BIF-only"] == expected_bif_pair,
        "Combined/BIF pairwise qids differ from Official EX",
    )
    require(
        observed_pair_qids["BIF--SIF_vs_SIF-only"] == expected_sif_pair,
        "Combined/SIF pairwise qids differ from Official EX",
    )
    checks.append({"name": "rq3_pairwise_transition_census", "status": "pass"})

    semantic_review_paths = tuple(
        path
        for path in (
            OUTPUT / "rq3_census_review_final.csv",
            OUTPUT / "rq3_census_semantic_review_final.csv",
        )
        if path.is_file()
    )
    if semantic_review_paths:
        for path in semantic_review_paths:
            semantic_review = read_csv(path)
            require(semantic_review, f"{path.name} is empty")
            qid_field = "question_id" if "question_id" in semantic_review[0] else "qid"
            require(qid_field in semantic_review[0], f"{path.name} lacks a qid field")
            review_qids = [int(row[qid_field]) for row in semantic_review]
            require(len(review_qids) == len(set(review_qids)), f"{path.name} contains duplicate qids")
            require(
                set(review_qids) in (set(changed_from_official), eligible_qids),
                f"{path.name} must cover all 486 changed qids",
            )
            if path.name == "rq3_census_review_final.csv":
                require(
                    set(review_qids) == set(changed_from_official) and len(review_qids) == 486,
                    "Canonical RQ3 review must contain all 486 changed qids",
                )
                eligible_review = [
                    row
                    for row in semantic_review
                    if int(row["clarification_link_denominator_eligible"]) == 1
                ]
                excluded_review = sorted(
                    int(row[qid_field])
                    for row in semantic_review
                    if int(row["clarification_link_denominator_eligible"]) == 0
                )
                require(len(eligible_review) == 486, "Canonical RQ3 linkage denominator must be 486")
                require(
                    excluded_review == [],
                    "Canonical RQ3 review must not exclude combined-condition records",
                )
                link_counts = Counter(row["clarification_link"] for row in eligible_review)
                require(
                    link_counts == Counter({"direct": 347, "partial": 12, "conflict": 100, "none": 27}),
                    f"Canonical RQ3 linkage counts changed: {dict(link_counts)}",
                )
                require(
                    all(int(row["language_model_assisted"]) == 1 for row in semantic_review),
                    "Canonical RQ3 review must disclose language-model assistance",
                )
                require(
                    all(int(row["author_verified"]) == 0 for row in semantic_review),
                    "Canonical RQ3 review unexpectedly claims author verification",
                )
                require(
                    all(int(row["causal_attribution_permitted"]) == 0 for row in semantic_review),
                    "Canonical RQ3 review unexpectedly permits causal attribution",
                )
                require(
                    all(row["concise_evidence_note_ko"].strip() for row in semantic_review),
                    "Canonical RQ3 review contains an empty evidence note",
                )
                codebook_path = OUTPUT / "rq3_census_review_codebook.md"
                summary_path = OUTPUT / "rq3_census_review_summary.json"
                require(codebook_path.is_file(), "Canonical RQ3 codebook is missing")
                require(summary_path.is_file(), "Canonical RQ3 review summary is missing")
                codebook = codebook_path.read_text(encoding="utf-8")
                review_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                require(
                    "RQ3 연결 분석의 분모는 486개이며 제외 문항은 없다" in codebook
                    and "481" not in codebook,
                    "Canonical RQ3 codebook has a stale linkage scope",
                )
                require(
                    review_summary.get("clarification_link_eligible_queries") == 486
                    and review_summary.get("clarification_link_excluded_qids") == []
                    and review_summary.get("clarification_link_distribution_486")
                    == {"direct": 347, "partial": 12, "conflict": 100, "none": 27},
                    "Canonical RQ3 review summary has stale scope or counts",
                )
        checks.append({"name": "rq3_final_semantic_review_if_present", "status": "pass"})
    else:
        checks.append(
            {
                "name": "rq3_final_semantic_review_if_present",
                "status": "pending_optional",
                "note": "No combined final semantic-review file is present; partial positive/negative files are not treated as final.",
            }
        )

    stage_script = HERE / "analyze_rq3_stage_timing.py"
    stage_detail_path = OUTPUT / "rq3_stage_timing_census.csv"
    stage_summary_path = OUTPUT / "rq3_stage_timing_summary.csv"
    stage_locus_path = OUTPUT / "rq3_stage_timing_by_locus.csv"
    stage_manifest_path = OUTPUT / "rq3_stage_timing_summary.json"
    require(
        all(
            path.is_file()
            for path in (
                stage_script,
                stage_detail_path,
                stage_summary_path,
                stage_locus_path,
                stage_manifest_path,
            )
        ),
        "RQ3 process-timing code or output is missing",
    )
    stage_detail = read_csv(stage_detail_path)
    stage_keys = {
        (row["pattern"], int(row["question_id"])) for row in stage_detail
    }
    require(
        len(stage_detail) == 486 and len(stage_keys) == 486,
        "RQ3 process-timing census must contain 486 unique rows",
    )
    require(
        Counter(row["direction"] for row in stage_detail)
        == Counter({"combined_correct": 316, "combined_incorrect": 170}),
        "RQ3 process-timing outcome groups changed",
    )
    require(
        sum(int(row["structural_eligible"]) for row in stage_detail) == 481,
        "RQ3 process-timing structural denominator must be 481",
    )
    expected_process_paths = {
        "combined_correct": {
            "same_choice_in_clarification_and_sql": 226,
            "partial_or_conflicting_correspondence": 81,
            "no_relevant_clarification_record": 9,
        },
        "combined_incorrect": {
            "same_choice_in_clarification_and_sql": 121,
            "partial_or_conflicting_correspondence": 31,
            "no_relevant_clarification_record": 18,
        },
    }
    for direction, expected in expected_process_paths.items():
        observed = Counter(
            row["observable_process_path"]
            for row in stage_detail
            if row["direction"] == direction
        )
        require(
            observed == Counter(expected),
            f"RQ3 process paths changed for {direction}: {dict(observed)}",
        )
    require(
        all(int(row["causal_attribution_permitted"]) == 0 for row in stage_detail),
        "RQ3 process-timing output unexpectedly permits causal attribution",
    )
    stage_manifest = json.loads(stage_manifest_path.read_text(encoding="utf-8"))
    require(
        all(stage_manifest["checks"].values()),
        "RQ3 process-timing manifest contains a failed check",
    )
    checks.append({"name": "rq3_observable_process_timing", "status": "pass"})

    finalisation_input_path = INPUT / "rq3_bif_sif_initial_final_sql.jsonl"
    finalisation_detail_path = OUTPUT / "rq3_sql_finalisation_census.csv"
    finalisation_summary_path = OUTPUT / "rq3_sql_finalisation_summary.csv"
    finalisation_manifest_path = OUTPUT / "rq3_sql_finalisation_summary.json"
    require(
        all(
            path.is_file()
            for path in (
                finalisation_input_path,
                finalisation_detail_path,
                finalisation_summary_path,
                finalisation_manifest_path,
            )
        ),
        "RQ3 SQL-finalisation evidence is incomplete",
    )
    with finalisation_input_path.open("r", encoding="utf-8") as handle:
        finalisation_inputs = [json.loads(line) for line in handle if line.strip()]
    finalisation_detail = read_csv(finalisation_detail_path)
    require(
        len(finalisation_inputs) == 486
        and len(finalisation_detail) == 486
        and len({int(row["question_id"]) for row in finalisation_detail}) == 486,
        "RQ3 SQL-finalisation comparison must contain 486 unique queries",
    )
    changed_after_synthesis = {
        int(row["question_id"])
        for row in finalisation_detail
        if int(row["changed_after_initial_synthesis"]) == 1
    }
    require(
        changed_after_synthesis == {195, 415, 904, 1016, 1126},
        "RQ3 post-synthesis SQL changes no longer match the audited five queries",
    )
    require(
        all(int(row["final_artifact_matches"]) == 1 for row in finalisation_detail),
        "A P04 final SQL does not match its retained P08 artifact",
    )
    finalisation_summary = {
        row["scope"]: (
            int(row["query_n"]),
            int(row["unchanged_n"]),
            int(row["changed_n"]),
        )
        for row in read_csv(finalisation_summary_path)
    }
    require(
        finalisation_summary["all_changed_outcomes"] == (486, 481, 5)
        and finalisation_summary["all_structural_comparisons"] == (481, 477, 4)
        and finalisation_summary["bif_sif_incorrect_all"] == (170, 167, 3)
        and finalisation_summary["bif_sif_incorrect_structural"] == (168, 165, 3),
        "RQ3 SQL-finalisation denominators or change counts changed",
    )
    finalisation_manifest = json.loads(
        finalisation_manifest_path.read_text(encoding="utf-8")
    )
    require(
        all(finalisation_manifest["checks"].values()),
        "RQ3 SQL-finalisation manifest contains a failed check",
    )
    checks.append({"name": "rq3_sql_finalisation_census", "status": "pass"})

    failure_stage_bundle_path = INPUT / "rq3_negative_stage_handoff_bundle.jsonl"
    failure_stage_extraction_manifest_path = (
        INPUT / "rq3_negative_stage_handoff_manifest.json"
    )
    failure_stage_coding_path = INPUT / "rq3_negative_failure_stage_coding.json"
    failure_stage_detail_path = OUTPUT / "rq3_negative_failure_stage_census.csv"
    failure_stage_summary_path = OUTPUT / "rq3_negative_failure_stage_summary.csv"
    failure_stage_manifest_path = OUTPUT / "rq3_negative_failure_stage_summary.json"
    failure_stage_codebook_path = OUTPUT / "rq3_negative_failure_stage_codebook.md"
    require(
        all(
            path.is_file()
            for path in (
                failure_stage_bundle_path,
                failure_stage_extraction_manifest_path,
                failure_stage_coding_path,
                failure_stage_detail_path,
                failure_stage_summary_path,
                failure_stage_manifest_path,
                failure_stage_codebook_path,
            )
        ),
        "RQ3 BIF-SIF failure-stage evidence is incomplete",
    )
    with failure_stage_bundle_path.open("r", encoding="utf-8") as handle:
        failure_stage_bundle = [json.loads(line) for line in handle if line.strip()]
    failure_stage_detail = read_csv(failure_stage_detail_path)
    require(
        len(failure_stage_bundle) == 170
        and len(failure_stage_detail) == 170
        and len({int(row["question_id"]) for row in failure_stage_detail}) == 170,
        "RQ3 BIF-SIF failure-stage census must contain 170 unique queries",
    )
    observed_failure_stages = Counter(
        row["primary_difference_first_observable_stage"]
        for row in failure_stage_detail
    )
    require(
        observed_failure_stages
        == Counter(
            {
                "confirmed_bif": 81,
                "initial_sif": 51,
                "later_sif": 36,
                "unresolved_timeout": 2,
            }
        ),
        f"RQ3 BIF-SIF first-observable-stage counts changed: {dict(observed_failure_stages)}",
    )
    handoff_confidence = Counter(
        row["bif_to_sif_change_confidence"] for row in failure_stage_detail
    )
    require(
        handoff_confidence
        == Counter({"high": 46, "ambiguous": 10, "not_coded": 114}),
        f"RQ3 BIF-to-SIF handoff counts changed: {dict(handoff_confidence)}",
    )
    require(
        sum(
            int(row["primary_mismatch_first_created_after_synthesis"])
            for row in failure_stage_detail
        )
        == 0
        and sum(
            int(row["synthesized_sql_equals_final_sql"])
            for row in failure_stage_detail
        )
        == 167,
        "RQ3 failure-stage SQL-finalisation boundary changed",
    )
    require(
        all(int(row["author_verified"]) == 0 for row in failure_stage_detail)
        and all(
            int(row["causal_attribution_permitted"]) == 0
            for row in failure_stage_detail
        ),
        "RQ3 failure-stage coding verification or causal flag changed",
    )
    failure_stage_extraction_manifest = json.loads(
        failure_stage_extraction_manifest_path.read_text(encoding="utf-8")
    )
    failure_stage_manifest = json.loads(
        failure_stage_manifest_path.read_text(encoding="utf-8")
    )
    require(
        all(failure_stage_extraction_manifest["checks"].values())
        and all(failure_stage_manifest["checks"].values()),
        "RQ3 failure-stage manifest contains a failed check",
    )
    checks.append({"name": "rq3_bif_sif_failure_stage_census", "status": "pass"})

    negative_mechanism = read_csv(OUTPUT / "rq3_negative_census_mechanism_summary.csv")
    detailed_mechanism_counts = {
        row["mechanism"]: int(row["semantic_case_n"])
        for row in negative_mechanism
        if row["summary_level"] == "detailed_observed_mechanism"
    }
    require(
        detailed_mechanism_counts
        == {
            "aggregation_or_calculation_distortion": 7,
            "compound_over_engineering": 14,
            "grouping_or_multiplicity_distortion": 6,
            "ordering_or_tie_distortion": 1,
            "output_representation_distortion": 38,
            "over_projection": 37,
            "predicate_or_scope_distortion": 32,
            "source_or_join_distortion": 27,
            "temporal_scope_distortion": 6,
        }
        and sum(detailed_mechanism_counts.values()) == 168,
        "RQ3 combined-incorrect full-content distribution changed",
    )
    timeout_rows = [
        row
        for row in negative_mechanism
        if row["summary_level"] == "execution_status"
        and row["mechanism"] == "execution_timeout"
    ]
    require(
        len(timeout_rows) == 1 and int(timeout_rows[0]["semantic_case_n"]) == 2,
        "RQ3 combined timeouts are missing from the negative review summary",
    )
    checks.append({"name": "rq3_negative_full_content_distribution", "status": "pass"})

    information = json.loads(
        (OUTPUT / "final_generator_information_audit.json").read_text(encoding="utf-8")
    )["summary"]
    require(
        information["SIF-only"]["requests_with_gold_sql_analysis"] == 338
        and information["SIF-only"]["requests_with_technical_reason"] == 338,
        "SIF-only final-generator information-path counts changed",
    )
    require(
        all(
            information[name]["requests_with_raw_ground_truth_sql_field"] == 0
            for name in ("BIF-only", "SIF-only", "BIF--SIF")
        ),
        "A raw ground-truth SQL payload field was detected",
    )
    dynamic = json.loads(
        (OUTPUT / "dynamic_time_ground_truth_queries.json").read_text(encoding="utf-8")
    )
    require(dynamic["query_count"] == 15, "Dynamic-date ground-truth SQL count changed")
    checks.append({"name": "information_path_and_dynamic_date_audits", "status": "pass"})

    option_quality = json.loads(
        (OUTPUT / "clarification_option_id_quality_summary.json").read_text(encoding="utf-8")
    )
    option_groups = option_quality["by_condition_stage"]
    expected_option_quality = {
        "bif_only:BIF": (432, 432, 0, 0, 0),
        "sif_only:SIF": (379, 362, 0, 16, 1),
        "bif_sif:BIF": (4913, 4912, 0, 1, 0),
        "bif_sif:SIF": (6136, 6070, 66, 0, 0),
    }
    for group, expected in expected_option_quality.items():
        observed = option_groups[group]
        require(
            (
                observed["turns"],
                observed["offered"],
                observed["supported_fallback"],
                observed["not_offered"],
                observed["missing"],
            )
            == expected,
            f"Clarification option-ID quality counts changed for {group}",
        )
    rq1_sif_option_quality = option_quality["sif_only_rq1_included"]
    require(
        (
            rq1_sif_option_quality["turns"],
            rq1_sif_option_quality["offered"],
            rq1_sif_option_quality["not_offered"],
            rq1_sif_option_quality["missing"],
            rq1_sif_option_quality["option_id_exception_total"],
        )
        == (375, 358, 16, 1, 17),
        "RQ1-included SIF-only option-ID quality counts changed",
    )
    option_rows = read_csv(OUTPUT / "clarification_option_id_quality_by_turn.csv")
    require(len(option_rows) == 11860, "Clarification option-ID row census changed")
    checks.append({"name": "clarification_option_id_quality_census", "status": "pass"})

    batch_calls = {
        row["condition"]: row for row in read_csv(OUTPUT / "batch_model_call_summary.csv")
    }
    require(
        (
            int(batch_calls["bif_only"]["batch_call_nonmissing"]),
            int(float(batch_calls["bif_only"]["batch_call_total"])),
            int(batch_calls["sif_only"]["batch_call_nonmissing"]),
            int(float(batch_calls["sif_only"]["batch_call_total"])),
            int(batch_calls["bif_sif"]["batch_call_nonmissing"]),
            int(float(batch_calls["bif_sif"]["batch_call_total"])),
        )
        == (1534, 3948, 1533, 4215, 1534, 23725),
        "Batch model-call coverage or totals changed",
    )
    operational = read_csv(OUTPUT / "operational_metrics_summary.csv")
    operational_by_key = {(row["condition"], row["metric"]): row for row in operational}
    require(
        all(
            int(operational_by_key[(condition, metric)]["n"]) == 1534
            for condition in CONDITIONS
            for metric in ("total_tokens", "summed_model_latency_ms")
        ),
        "Token or latency coverage is not 1,534 queries per condition",
    )
    require(
        (
            int(float(operational_by_key[("bif_only", "model_call_count")]["total"])),
            int(float(operational_by_key[("sif_only", "model_call_count")]["total"])),
            int(float(operational_by_key[("bif_sif", "model_call_count")]["total"])),
        )
        == (3947, 4219, 23725),
        "Detailed model-call totals changed",
    )
    checks.append({"name": "operational_metric_coverage", "status": "pass"})

    expected_figures = {
        "figure_5_1_rq1_decision_distribution.pdf",
        "figure_5_1_rq1_decision_distribution.png",
        "figure_5_2_rq2_official_ex_and_mcnemar.pdf",
        "figure_5_2_rq2_official_ex_and_mcnemar.png",
        "figure_5_3_rq3_outcome_patterns.pdf",
        "figure_5_3_rq3_outcome_patterns.png",
        "figure_5_4_rq3_structural_comparison.pdf",
        "figure_5_4_rq3_structural_comparison.png",
        "figure_5_5_rq3_clarification_sql_links.pdf",
        "figure_5_5_rq3_clarification_sql_links.png",
        "figure_5_6_rq3_failure_stages.pdf",
        "figure_5_6_rq3_failure_stages.png",
    }
    require(all((FIGURES / name).is_file() for name in expected_figures), "One or more Chapter 5 figures are missing")
    provenance = {
        "official_bird_ex_recomputed.json",
        "official_bird_ex_provenance_addendum.json",
        "official_bird_ex_serial_rechecks.json",
        "ReferenceBib.bib",
    }
    require(all((INPUT / name).is_file() for name in provenance), "Evaluation provenance snapshot is incomplete")
    checks.append({"name": "figures_and_provenance_snapshots", "status": "pass"})

    require(CHAPTER.is_file(), "Chapter 5 LaTeX draft is missing")
    chapter = CHAPTER.read_text(encoding="utf-8")
    chapter_snippets = (
        r"BIF Only는 916개를 맞혀 59.71\%",
        r"SIF Only는 861개로 56.13\%",
        r"BIF-SIF는 1,008개로 65.71\%",
        "BIF Only 127개와 SIF Only 120개",
        "807개 질문",
        "명확화가 발생한 338개 문항",
        "316개 중 226개(71.52\\%)",
        "170개 중 121개(71.18\\%)",
        "전체 486개 가운데 347개(71.40\\%)",
        "168개에서는 120개(71.43\\%)",
        "이를 합한 112개(23.05\\%)",
        "관련 명확화가 없었던 27개(5.56\\%)",
        "486개 전체에서는 481개(98.97\\%)가 같고 5개(1.03\\%)만 변경되었다",
        "BIF-SIF가 오답인 170개에서는 167개(98.24\\%)가 같고 3개(1.76\\%)가 변경되었다",
        "Confirmed BIF에서 81개(47.65\\%)",
        "초기 SIF에서 51개(30.00\\%)",
        "후속 SIF 확인ㆍ정제 과정에서 36개(21.18\\%)",
        "명시적 변경으로 분류하였다. 이 기준에 해당한 문항은 46개(27.06\\%)",
        "마지막 보정에서 주된 차이가 처음 관찰된 것으로 분류된 문항은 없었다",
        "식의 대괄호에는 각 조합에서 정답인 시스템을 표시하였다",
        "문항 4는 이러한 변경의 흐름을 구체적으로 보여 준다",
        "값의 표현 또는 출력 형식 & 38 & 22.62\\%",
        "불필요한 출력 열 추가 & 37 & 22.02\\%",
        "SIF Only의 379개에는 제시되지 않은 값 16개와 선택지 식별자가 누락된 응답 한 개",
        "375개 가운데 같은 17개 예외",
        "과거 정답 618개 가운데 604개는 정답으로 유지되었지만 14개는 오답으로 바뀌었고",
        "호출 수의 실행 요약 합계는 BIF Only 3,948회, SIF Only 4,215회, BIF-SIF 23,725회",
    )
    require(
        all(item in chapter for item in chapter_snippets),
        "Chapter 5 contains stale or missing RQ1/RQ2 result text",
    )
    require(
        "figure_5_4_rq3_structural_comparison.pdf" in chapter
        and "figure_5_5_rq3_clarification_sql_links.pdf" in chapter
        and "figure_5_6_rq3_failure_stages.pdf" in chapter
        and "figure_5_4_rq3_structural_and_clarification.pdf" not in chapter,
        "Chapter 5 does not use the three active split RQ3 figures",
    )
    require(
        "BIF-SIF의 전체 정답 1,008개" in chapter
        and "명확화 답변--SQL 연결(제외 없음) & 316 & 170 & 486" in chapter
        and "SQL 구조 비교(시간 초과 5개 제외) & 313 & 168 & 481" in chapter
        and "BIF-SIF 오답의 최초 관찰 단계 & -- & 170 & 170" in chapter,
        "Chapter 5 does not explain the RQ3 population transitions",
    )
    require(
        re.search(r"(?:486\s*개.{0,160}전수|전수.{0,160}486\s*개)", chapter, flags=re.DOTALL),
        "Chapter 5 does not describe RQ3 as a census of all 486 changed queries",
    )
    require("486개 문항" in chapter, "Chapter 5 does not state the 486-query process-analysis scope")
    stale_display_terms = ("BIF-only", "SIF-only", "BIF--SIF", "결합 조건", "단독 조건")
    require(
        not any(term in chapter for term in stale_display_terms),
        "Chapter 5 still contains a superseded condition name",
    )
    unsupported_stage_phrases = (
        "구별되는 차이가 최종 SQL에서만 확인되었다",
        "해당 차이가 최종 SQL에서만 확인된 경우",
        "차이가 관찰된 시점도 구분할 수 있다",
    )
    require(
        not any(phrase in chapter for phrase in unsupported_stage_phrases),
        "Chapter 5 overstates the first stage at which an RQ3 choice arose",
    )
    stale_sample_patterns = (
        r"결과가\s*달라진\s*150\s*개\s*문항의\s*사례별\s*검토",
        r"총\s*150\s*개\s*문항을\s*추출",
        r"150\s*개\s*사례\s*표본에서는",
        r"RQ3의\s*150\s*개\s*사례\s*표본",
        r"150\s*개\s*표본은\s*문항\s*수가",
        r"150\s*개\s*중\s*\d+\s*개",
        r"RQ3\s*표본",
    )
    require(
        not any(re.search(pattern, chapter) for pattern in stale_sample_patterns),
        "Chapter 5 still contains an active claim that RQ3 used a 150-query sample",
    )
    require(chapter.count("\\begin{") == chapter.count("\\end{"), "LaTeX environment count is unbalanced")
    checks.append({"name": "chapter5_result_snapshot_values_and_rq3_census_wording", "status": "pass"})

    result = {"status": "pass", "checks": checks}
    (OUTPUT / "final_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
