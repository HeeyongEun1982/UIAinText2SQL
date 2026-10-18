"""Locate when RQ3-relevant decisions are visible in the preserved trace.

This is a descriptive record-sequence analysis for all 486 queries whose
correctness differs across BIF Only, SIF Only, and BIF-SIF.  It does not assign
a causal failure stage or claim where a choice was first formed.  A lexical aid
records the earliest preserved clarification stage in which a decision
concerning the transition-relevant SQL dimension can be localised:

* BIF clarification (before database grounding),
* SIF clarification (after database grounding),
* no relevant clarification record for the choice observed in final SQL, or
* not localised by the reproducible topic rules.

The script also preserves whether relevant topics occur in BIF only, SIF only,
or both stages.  Topic localisation is a deterministic lexical aid; the
clarification--SQL link label comes from the existing full-content assisted
review and therefore remains subject to the author-verification limitation
documented in Chapter 5.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from analyze_rq3_traces import LOCUS_ORDER, clarification_topics


HERE = Path(__file__).resolve().parent
INPUT_PATH = HERE / "inputs" / "rq3_census_case_bundle.jsonl"
REVIEW_PATH = HERE / "outputs" / "rq3_census_review_final.csv"
DETAIL_PATH = HERE / "outputs" / "rq3_stage_timing_census.csv"
SUMMARY_CSV_PATH = HERE / "outputs" / "rq3_stage_timing_summary.csv"
LOCUS_CSV_PATH = HERE / "outputs" / "rq3_stage_timing_by_locus.csv"
SUMMARY_JSON_PATH = HERE / "outputs" / "rq3_stage_timing_summary.json"

STAGE_ORDER = (
    "bif_before_grounding",
    "sif_after_grounding",
    "no_relevant_clarification_record",
    "not_localised",
)
STAGE_LABELS = {
    "bif_before_grounding": "BIF clarification (before grounding)",
    "sif_after_grounding": "SIF clarification (after grounding)",
    "no_relevant_clarification_record": (
        "No relevant clarification record (choice observed in final SQL)"
    ),
    "not_localised": "Relevant clarification not localised",
}
PRESENCE_ORDER = ("bif_only", "sif_only", "both", "none", "not_localised")
PRESENCE_LABELS = {
    "bif_only": "BIF stage only",
    "sif_only": "SIF stage only",
    "both": "Both BIF and SIF stages",
    "none": "No relevant clarification",
    "not_localised": "Relevant clarification not localised",
}
DIRECTION_ORDER = ("combined_correct", "combined_incorrect", "all_changed")
PROCESS_ORDER = (
    "same_choice_in_clarification_and_sql",
    "partial_or_conflicting_correspondence",
    "no_relevant_clarification_record",
)
PROCESS_LABELS = {
    "same_choice_in_clarification_and_sql": (
        "Same choice observed in prior clarification and final SQL"
    ),
    "partial_or_conflicting_correspondence": (
        "Incomplete or conflicting relation across clarification records and final SQL"
    ),
    "no_relevant_clarification_record": (
        "No relevant clarification record for the choice observed in final SQL"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def trace_stage(turn: dict[str, Any]) -> str:
    source = str(turn.get("source_path") or "").replace("\\", "/").lower()
    if "/p01/semantic_frontend/" in source:
        return "bif"
    if "/p03/" in source or source.endswith("/confirmed_sif.json"):
        return "sif"
    return "other"


def split_loci(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value if str(item) in LOCUS_ORDER}
    return {
        item
        for item in str(value or "").split("|")
        if item in LOCUS_ORDER
    }


def topics_by_stage(turns: list[dict[str, Any]]) -> dict[str, set[str]]:
    output = {"bif": set(), "sif": set(), "other": set()}
    for turn in turns:
        stage = trace_stage(turn)
        output[stage].update(
            topic for topic in clarification_topics([turn]) if topic in LOCUS_ORDER
        )
    return output


def stage_presence(
    link: str,
    bif_relevant: set[str],
    sif_relevant: set[str],
) -> str:
    if link == "none":
        return "none"
    if bif_relevant and sif_relevant:
        return "both"
    if bif_relevant:
        return "bif_only"
    if sif_relevant:
        return "sif_only"
    return "not_localised"


def localised_stage_category(
    link: str,
    bif_relevant: set[str],
    sif_relevant: set[str],
) -> str:
    if link == "none":
        return "no_relevant_clarification_record"
    if bif_relevant:
        return "bif_before_grounding"
    if sif_relevant:
        return "sif_after_grounding"
    return "not_localised"


def observable_process_path(link: str) -> str:
    if link == "direct":
        return "same_choice_in_clarification_and_sql"
    if link in {"partial", "conflict"}:
        return "partial_or_conflicting_correspondence"
    if link == "none":
        return "no_relevant_clarification_record"
    raise ValueError(f"Unexpected clarification link: {link}")


def ordered_join(values: set[str]) -> str:
    return "|".join(locus for locus in LOCUS_ORDER if locus in values)


def make_summary(
    rows: list[dict[str, Any]],
    field: str,
    categories: tuple[str, ...],
    labels: dict[str, str],
    classification: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for direction in DIRECTION_ORDER:
        group = rows if direction == "all_changed" else [
            row for row in rows if row["direction"] == direction
        ]
        counts = Counter(str(row[field]) for row in group)
        for category in categories:
            count = counts[category]
            output.append(
                {
                    "classification": classification,
                    "direction": direction,
                    "category": category,
                    "category_label": labels[category],
                    "query_n": len(group),
                    "count": count,
                    "percent": round(100 * count / len(group), 2),
                }
            )
    return output


def locus_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for direction in ("combined_correct", "combined_incorrect"):
        group = [
            row
            for row in rows
            if row["direction"] == direction and int(row["structural_eligible"]) == 1
        ]
        for locus in LOCUS_ORDER:
            locus_rows = [
                row
                for row in group
                if locus in split_loci(row["transition_relevant_loci"])
            ]
            counts = Counter(
                str(row["localised_clarification_stage"]) for row in locus_rows
            )
            for stage in STAGE_ORDER:
                count = counts[stage]
                output.append(
                    {
                        "direction": direction,
                        "locus": locus,
                        "direction_query_n": len(group),
                        "locus_query_n": len(locus_rows),
                        "stage": stage,
                        "stage_label": STAGE_LABELS[stage],
                        "count": count,
                        "percent_within_locus": (
                            round(100 * count / len(locus_rows), 2) if locus_rows else 0.0
                        ),
                    }
                )
    return output


def main() -> None:
    cases = load_jsonl(INPUT_PATH)
    reviews = load_csv(REVIEW_PATH)
    review_by_key = {
        (str(row["pattern"]), int(row["question_id"])): row for row in reviews
    }
    if len(cases) != 486 or len(review_by_key) != 486:
        raise ValueError("Expected 486 unique RQ3 census cases and reviews")

    rows: list[dict[str, Any]] = []
    for case in cases:
        key = (str(case["pattern"]), int(case["question_id"]))
        if key not in review_by_key:
            raise ValueError(f"Missing full-content review for {key}")
        review = review_by_key[key]
        analysis = case["census_analysis"]
        combined = case["conditions"]["bif_sif"]
        transition_loci = split_loci(analysis["transition_relevant_loci"])
        stage_topics = topics_by_stage(combined.get("clarification") or [])
        bif_relevant = transition_loci & stage_topics["bif"]
        sif_relevant = transition_loci & stage_topics["sif"]
        link = str(review["clarification_link"])
        presence = stage_presence(link, bif_relevant, sif_relevant)
        localised_stage = localised_stage_category(link, bif_relevant, sif_relevant)
        structural_eligible = int(bool(analysis["all_condition_executions_ok"]))

        rows.append(
            {
                "pattern": key[0],
                "question_id": key[1],
                "db_id": str(case["db_id"]),
                "difficulty": str(case["difficulty"]),
                "direction": str(analysis["direction"]),
                "structural_eligible": structural_eligible,
                "execution_issue_conditions": "|".join(
                    str(value) for value in analysis["execution_issue_conditions"]
                ),
                "transition_relevant_loci": ordered_join(transition_loci),
                "bif_stage_topics": ordered_join(stage_topics["bif"]),
                "sif_stage_topics": ordered_join(stage_topics["sif"]),
                "bif_relevant_loci": ordered_join(bif_relevant),
                "sif_relevant_loci": ordered_join(sif_relevant),
                "clarification_link": link,
                "observable_process_path": observable_process_path(link),
                "relevant_stage_presence": presence,
                "localised_clarification_stage": localised_stage,
                "stage_interpretation": (
                    "Earliest lexically localised clarification stage when available; "
                    "descriptive, not causal, and no-record is not a first-origin claim"
                ),
                "language_model_assisted_link": int(review["language_model_assisted"]),
                "author_verified": int(review["author_verified"]),
                "causal_attribution_permitted": 0,
                "case_evidence_locator": str(review["case_evidence_locator"]),
            }
        )

    rows.sort(key=lambda row: (row["pattern"], int(row["question_id"])))
    stage_summary = make_summary(
        rows,
        "localised_clarification_stage",
        STAGE_ORDER,
        STAGE_LABELS,
        "lexically_localised_clarification_stage",
    )
    presence_summary = make_summary(
        rows,
        "relevant_stage_presence",
        PRESENCE_ORDER,
        PRESENCE_LABELS,
        "relevant_stage_presence",
    )
    process_summary = make_summary(
        rows,
        "observable_process_path",
        PROCESS_ORDER,
        PROCESS_LABELS,
        "observable_process_path",
    )
    locus_rows = locus_summary(rows)

    checks = {
        "detail_rows_are_486": len(rows) == 486,
        "keys_are_unique": len({(row["pattern"], row["question_id"]) for row in rows}) == 486,
        "directions_are_316_and_170": Counter(row["direction"] for row in rows)
        == Counter({"combined_correct": 316, "combined_incorrect": 170}),
        "structural_eligible_is_481": sum(row["structural_eligible"] for row in rows) == 481,
        "stage_categories_are_complete": {
            row["localised_clarification_stage"] for row in rows
        }
        <= set(STAGE_ORDER),
        "presence_categories_are_complete": {
            row["relevant_stage_presence"] for row in rows
        }
        <= set(PRESENCE_ORDER),
        "process_categories_are_complete": {
            row["observable_process_path"] for row in rows
        }
        <= set(PROCESS_ORDER),
        "no_causal_attribution": all(
            int(row["causal_attribution_permitted"]) == 0 for row in rows
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Stage-timing validation failed: {checks}")

    write_csv(DETAIL_PATH, rows)
    write_csv(
        SUMMARY_CSV_PATH,
        [*stage_summary, *presence_summary, *process_summary],
    )
    write_csv(LOCUS_CSV_PATH, locus_rows)
    write_json(
        SUMMARY_JSON_PATH,
        {
            "scope": "All 486 RQ3 outcome-changing queries",
            "definition": (
                "Earliest lexically localised BIF-SIF clarification stage when available; "
                "not a causal failure-stage or first-origin label"
            ),
            "topic_localisation": (
                "Deterministic per-turn lexical topic rules from analyze_rq3_traces.py"
            ),
            "clarification_link_source": (
                "Language-model-assisted full-content census; author verification pending"
            ),
            "script": {
                "path": Path(__file__).name,
                "sha256": sha256(Path(__file__).resolve()),
            },
            "inputs": {
                str(INPUT_PATH.relative_to(HERE)): sha256(INPUT_PATH),
                str(REVIEW_PATH.relative_to(HERE)): sha256(REVIEW_PATH),
            },
            "outputs": {
                str(DETAIL_PATH.relative_to(HERE)): sha256(DETAIL_PATH),
                str(SUMMARY_CSV_PATH.relative_to(HERE)): sha256(SUMMARY_CSV_PATH),
                str(LOCUS_CSV_PATH.relative_to(HERE)): sha256(LOCUS_CSV_PATH),
            },
            "checks": checks,
            "observable_process_path_summary": process_summary,
        },
    )
    print(json.dumps({"checks": checks, "stage_summary": stage_summary}, indent=2))


if __name__ == "__main__":
    main()
