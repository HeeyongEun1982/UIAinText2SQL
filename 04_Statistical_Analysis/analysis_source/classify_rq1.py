"""Create a reproducible, blinded rule-based draft coding for RQ1.

Only the fields exposed in ``inputs/rq1_turns_blinded.csv`` are read for
classification.  In particular, the script never reads the selected answer,
ground-truth SQL/analysis, generated SQL, or correctness outcome.  The output
is deliberately labelled *provisional*: low-margin and weak matches are
exported for full-content case review rather than silently treated as reliable
coding.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
INPUT = HERE / "inputs" / "rq1_turns_blinded.csv"
OUTPUT_DIR = HERE / "outputs"
RULE_VERSION = "rq1-native-rules-v1.0"

# These are the only fields whose text may influence a label.
ALLOWED_TEXT_FIELDS = (
    "decision_context",
    "attribute_name",
    "candidate_columns",
    "clarification_question",
    "options_text",
)
FORBIDDEN_FIELDS = {
    "selected_option_id",
    "answer_text",
    "technical_reason",
    "ground_truth_analysis",
    "ground_truth_sql",
    "generated_sql",
    "correctness",
}

BIF_LABELS = {
    "RG": "referent/concept grounding",
    "RS": "result selection/multiplicity",
    "MC": "metric/calculation",
    "SC": "population/scope",
    "PL": "predicate logic/boundary",
    "TM": "temporal scope",
    "UG": "unit/granularity",
    "OR": "output/representation",
}

SIF_LABELS = {
    "GAP": "schema coverage gap",
    "REL": "relation/join/role",
    "TEMP": "temporal/version record",
    "METRIC": "metric/aggregation grain",
    "SCOPE": "population/context/logical scope",
    "RANK": "ranking/result selection",
    "VALUE": "literal/code/unit/parsing",
    "OUTPUT": "returned identifier/format",
    "ATTRIBUTE": "schema element/authority source",
}

COMMON_MAP = {
    "RG": "CONCEPT_VALUE_SOURCE",
    "RS": "RANK_MULTIPLICITY",
    "MC": "METRIC_GRAIN",
    "SC": "SCOPE_LOGIC",
    "PL": "SCOPE_LOGIC",
    "TM": "TEMPORAL",
    "UG": "METRIC_GRAIN",
    "OR": "OUTPUT_REPRESENTATION",
    "GAP": "SCHEMA_GAP",
    "REL": "RELATION_ROLE",
    "TEMP": "TEMPORAL",
    "METRIC": "METRIC_GRAIN",
    "SCOPE": "SCOPE_LOGIC",
    "RANK": "RANK_MULTIPLICITY",
    "VALUE": "CONCEPT_VALUE_SOURCE",
    "OUTPUT": "OUTPUT_REPRESENTATION",
    "ATTRIBUTE": "CONCEPT_VALUE_SOURCE",
}

# Each tuple is (rule id, regex, score).  Patterns describe the decision delta,
# not the selected answer.  Strong anchors receive larger weights; broad terms
# receive smaller weights so that they cannot decide a label by themselves.
BIF_RULES: dict[str, list[tuple[str, str, int]]] = {
    "RS": [
        ("tie", r"\b(tie|ties|tied|tie[- ]?break(?:er|ing)?)\b", 9),
        ("all-vs-one", r"\b(all|every)\b.{0,55}\b(one|single|only one|first match)\b|\b(one|single|only one)\b.{0,55}\b(all|every)\b", 7),
        ("multiple-match", r"\b(multiple|more than one|several)\b.{0,45}\b(match(?:es)?|result(?:s)?|candidate(?:s)?|client(?:s)?|patient(?:s)?|school(?:s)?|player(?:s)?)\b", 5),
        ("empty-result", r"\b(no result|no match|none found|empty result|if no .+ (?:exists|has|is|meets))\b", 6),
        ("selection", r"\b(any one|choose one|return all|which result|single best match)\b", 5),
        ("duplicate-selection", r"\b(if|when|where) (?:there (?:is|are) )?multiple\b|\bduplicates?\b|\bearliest .+ most recent\b", 5),
    ],
    "OR": [
        ("return-field", r"\b(what|which)\b.{0,35}\b(field|attribute|identifier|id|name|address|email|label|column|information)\b.{0,35}\b(return|report|show|display|list|include|provide)\b|\b(return|report|show|display|list|include|provide)\b.{0,35}\b(field|attribute|identifier|id|name|address|email|label|column|information)\b", 7),
        ("include-extra", r"\b(also include|include the .+ (?:count|rate|score|value)|without the .+|only the .+ identifier|along with)\b", 6),
        ("format-unit", r"\b(output|display|presentation|format|formatted|human[- ]readable|decimal places?|percentage sign|units?)\b", 4),
        ("ordering-field", r"\b(alphabetical(?:ly)?|sort(?:ed|ing)?)\b.{0,50}\b(by|field|name|id|code|label)\b", 7),
    ],
    "TM": [
        ("time-window", r"\b(time span|time period|date range|reporting (?:period|window)|calendar (?:period|year)|entire (?:available )?history|last \d+ (?:days?|months?|years?))\b", 8),
        ("date-choice", r"\b(which date|what date|which year|specific date|start and end dates?|as[- ]of date|cut[- ]?off date|timezone)\b", 7),
        ("temporal-boundary", r"\b(timestamp|same day|before|after|during|between dates?|from .+ to .+)\b", 3),
        ("current-history", r"\b(active|current)\b.{0,45}\b(historical|history|closed|past)\b|\b(historical|history|closed|past)\b.{0,45}\b(active|current)\b", 7),
    ],
    "UG": [
        ("distinct-occurrence", r"\b(distinct|unique)\b.{0,45}\b(occurrence|record|row|entity|person|client|patient|student|atom|type|value)s?\b|\b(occurrence|record|row|entity|person|client|patient|student|atom|type|value)s?\b.{0,45}\b(distinct|unique)\b", 8),
        ("per-unit", r"\b(per[- ](?:row|record|entity|person|client|patient|student|school|game|match|race|season)|overall|group(?:ed|ing)? (?:by|unit)|deduplicat(?:e|ed|ion)|count repeated|counting repeated)\b", 6),
        ("one-row-many", r"\bone (?:row|record) per\b|\bmultiple (?:rows|records) per\b", 6),
        ("ordered-pair", r"\b(ordered or unordered|ordered versus unordered|ordered vs\.? unordered|a.?b .+ b.?a)\b", 7),
        ("joint-separate-unit", r"\b(single|same|shared|common)\b.{0,55}\b(separately|each person|for each)\b|\b(separately|each person|for each)\b.{0,55}\b(single|same|shared|common)\b", 6),
    ],
    "MC": [
        ("formula", r"\b(formula|calculation|calculate|computed?|computation)\b|\bcount\s*\(|\bsum\s*\(|\bavg\s*\(|\bdivide\s*\(", 6),
        ("ratio-denominator", r"\b(percentage|percent|ratio|proportion|denominator|numerator|rate)\b", 5),
        ("aggregate", r"\b(average|mean|sum|total|maximum|minimum|aggregate|metric)\b", 3),
        ("ranking-metric", r"\bwhich metric\b|\b(top|best|highest|lowest)\b.{0,35}\b(metric|score|rating|goals?|height|amount|value)\b", 5),
        ("which-measure", r"\bwhich (?:count|counts|measure|metric|calculation)s?\b|\bwhat .+ should be counted\b", 6),
    ],
    "PL": [
        ("logic", r"\b(and/or|and versus or|either .+ or|both .+ and|any versus all|any/all|all conditions?|at least one)\b", 7),
        ("boundary", r"\b(strictly|inclusive|exclusive|include the boundary|greater than or equal|less than or equal|at least|at most|more than|less than|exactly equal|equal timestamp)\b", 5),
        ("null-policy", r"\b(null|missing values?|unknown values?|blank values?)\b", 5),
        ("predicate", r"\b(filter placement|which condition|comparison boundary|predicate)\b", 5),
        ("grouping-expression", r"\b(logical grouping|parenthes(?:is|es)|take .+ literally|thresholds? as upper limits?)\b", 8),
    ],
    "RG": [
        ("meaning", r"\b(what does|what do you mean|which meaning|which definition|does .+ mean|refer(?:s|red)? to|interpret(?:ation|ed)?|term .+ ambiguous)\b", 4),
        ("entity-role", r"\b(which (?:person|client|patient|student|player|school|account|role|administrator|entity)|same name|named .+ may not be unique|which .+ do you mean)\b", 5),
        ("concept", r"\b(concept|real[- ]world meaning|category|attribute|designation)\b", 3),
        ("identity-or-value", r"\bwhich (?:one|individual|currency)\b|\bcurrency .+ not specified\b", 5),
    ],
    "SC": [
        ("population", r"\b(population|which (?:records|people|clients|patients|students|schools|transactions|sessions|states|regions|groups|types|categories)|include (?:only|both|all)|exclude|inclusion|eligible|who counts as)\b", 5),
        ("scope", r"\b(scope|universe|subpopulation|geographic|school[- ]level|district[- ]level|all transactions|transaction types?)\b", 4),
    ],
}

SIF_RULES: dict[str, list[tuple[str, str, int]]] = {
    "GAP": [
        ("no-schema", r"\b(schema|database|data)\b.{0,45}\b(no|not|does not|doesn't|cannot|can't|lacks?|missing|without)\b.{0,45}\b(column|field|table|link|record|represent|store|provide|contain)\b|\b(no|not|cannot|can't|missing)\b.{0,35}\b(explicit|clear|reliable|unambiguous)\b.{0,35}\b(column|field|mapping|link)\b", 10),
        ("external-proxy", r"\b(proxy|external (?:assumption|data|mapping)|provide (?:an|a) explicit|i will provide|not possible|cannot be (?:computed|determined|answered)|unknown source)\b", 7),
        ("free-text-gap", r"\bunstructured (?:text|json)|parse .+ text|extract .+ from .+ text\b", 4),
    ],
    "REL": [
        ("join", r"\b(join (?:key|path|column|identifier)|which .+ (?:join|link)|link(?:ing)? (?:the |each )?(?:table|record|player|entity|account)|match(?:ing)? records? between|same real[- ]world)\b", 9),
        ("relationship-role", r"\b(relationship|relation direction|owner|actor|sender|receiver|home (?:side|team)|away (?:side|team)|borrower|holder role|participant role)\b", 5),
        ("key-expression", r"\b[a-z_][\w.]*\s*=\s*[a-z_][\w.]*\b", 7),
        ("id-link", r"\b(internal .+ id|external .+ id|identifier .+ match|match by .+ identifier)\b", 6),
    ],
    "TEMP": [
        ("version", r"\b(multiple|historical|history|different)\b.{0,45}\b(record(?:s)?|entr(?:y|ies)|rating(?:s)?|value(?:s)?|snapshot(?:s)?|version(?:s)?|date(?:s)?)\b", 8),
        ("current-latest", r"\b(current|latest|earliest|most recent|first recorded|last recorded|as of|snapshot|version|at the time|per[- ](?:visit|exam|event)|season[- ]end)\b", 5),
        ("date-field", r"\bwhich date (?:field|column)|recorded on different dates|effective date\b", 7),
    ],
    "METRIC": [
        ("formula", r"\b(formula|calculation|calculate|computed?|aggregation|aggregate|denominator|numerator|ratio|percentage|percent|average|mean|sum|count distinct|distinct count)\b", 6),
        ("grain", r"\b(row versus|row vs|per[- ](?:row|entity|person|player|race|season)|distinct (?:entity|person|player|record)|deduplicat(?:e|ed|ion)|group(?:ing)? (?:unit|grain))\b", 8),
        ("stored-derived", r"\b(stored (?:value|metric)|recomput(?:e|ed)|derived (?:value|metric)|running total|cumulative)\b", 5),
    ],
    "SCOPE": [
        ("population", r"\b(population|who counts|which (?:people|clients|patients|students|schools|accounts|records|rows|events)|include|exclude|inclusion|eligible|universe|context)\b", 4),
        ("logic", r"\b(any/all|any versus all|and/or|either .+ or|both .+ and|at least one|co[- ]occurrence|same (?:row|record|event|visit))\b", 6),
        ("location-context", r"\b(live there|registered there|home district|account district|spatial|organizational|location of)\b", 6),
    ],
    "RANK": [
        ("rank", r"\b(top|bottom|highest|lowest|maximum|minimum|most|least|rank(?:ed|ing)?|sort(?:ed|ing)?|order(?:ed|ing)?|limit)\b", 4),
        ("first-last", r"\b(first|last)\b.{0,35}\b(result|record|row|match|event|transaction|race|season|entry)\b|\bwhich .+ (?:first|last)\b", 6),
        ("tie", r"\b(tie|ties|tied|tie[- ]?break)\b", 7),
    ],
    "VALUE": [
        ("literal-code", r"\b(literal|code|coded|abbreviation|label value|status value|exact value|value\(s\)|enum|category value)\b", 7),
        ("unit-format-parse", r"\b(unit|milliseconds?|seconds?|minutes?|date format|numeric string|text value|parse|parsing|cast|encoded|free[- ]text)\b", 5),
        ("value-meaning", r"\bwhat (?:value|label) (?:means|indicates)|which .+ value\(s\)|marked as|status indicates\b", 5),
    ],
    "OUTPUT": [
        ("returned-id", r"\b(which|what)\b.{0,30}\b(identifier|id|name|label|address|position|format|form)\b.{0,35}\b(return|report|show|display|list|provide|want)|\b(return|report|show|display|list|provide)\b.{0,35}\b(identifier|id|name|label|address|format|form)\b", 9),
        ("output-format", r"\b(output|returned?|display|human[- ]readable|raw numeric|formatted?|format)\b", 4),
        ("which-column-output", r"\bwhich (?:kind|type|form) of .+ (?:return|returned|want)|\bwhat should (?:be )?returned\b", 6),
    ],
    "ATTRIBUTE": [
        ("competing-columns", r"\b(multiple|both|several|different|competing)\b.{0,45}\b(column(?:s)?|field(?:s)?|source(?:s)?|table(?:s)?|attribute(?:s)?)\b", 7),
        ("which-source", r"\b(which|what)\b.{0,35}\b(column|field|source|table|attribute|data source)\b|\b(use|choose|select)\b.{0,30}\b(source|column|field|table)\b", 6),
        ("authority", r"\b(patient[- ]level|examination[- ]level|visit[- ]level|post[- ]level|profile|denormalized|normalized|official source|authority source|stored directly|linked user)\b", 5),
        ("candidate-default", r"\bcandidate columns?\b|\bcould (?:each|both) (?:be used|represent)\b", 4),
    ],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_rows() -> list[dict[str, str]]:
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        leaked = fields & FORBIDDEN_FIELDS
        if leaked:
            raise RuntimeError(f"Blinded input contains forbidden fields: {sorted(leaked)}")
        missing = set(ALLOWED_TEXT_FIELDS) - fields
        if missing:
            raise RuntimeError(f"Blinded input is missing fields: {sorted(missing)}")
        return [dict(row) for row in reader]


def normalized_text(row: dict[str, str]) -> str:
    parts = []
    for field in ALLOWED_TEXT_FIELDS:
        value = (row.get(field) or "").strip()
        if value:
            parts.append(f"{field}: {value}")
    return "\n".join(parts).lower()


def score_rules(
    text: str, rules: dict[str, list[tuple[str, str, int]]]
) -> tuple[dict[str, int], dict[str, list[str]]]:
    scores = {label: 0 for label in rules}
    matched: dict[str, list[str]] = {label: [] for label in rules}
    for label, entries in rules.items():
        for rule_id, pattern, weight in entries:
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                scores[label] += weight
                matched[label].append(rule_id)
    return scores, matched


def add_structural_scores(
    architecture: str,
    row: dict[str, str],
    text: str,
    scores: dict[str, int],
    matched: dict[str, list[str]],
) -> None:
    """Add transparent architecture-specific anchors not suited to one regex."""
    candidates = [
        part.strip()
        for part in (row.get("candidate_columns") or "").split("||")
        if part.strip()
    ]
    attribute = (row.get("attribute_name") or "").lower()
    question = (row.get("clarification_question") or "").lower()

    if architecture == "bif_only":
        # The BIF decision order in Chapter 3 makes ties, pure output choices,
        # date scope, counting grain, and formulas particularly diagnostic.
        anchors = [
            ("RS", r"\b(tie|ties|tied)\b", 8, "anchor-tie"),
            ("TM", r"\b(time span|time period|date range|which date|which year)\b", 7, "anchor-time-scope"),
            ("UG", r"\b(distinct|unique)\b.{0,50}\b(occurrence|record|row|atom|entity|person|client|patient|type)s?\b", 6, "anchor-counting-unit"),
            ("MC", r"\b(denominator|numerator|formula|percentage definition|which calculation)\b", 8, "anchor-formula"),
            ("OR", r"\bshould (?:the answer|i|we) include|\bwhat .+ (?:return|report|display)\b", 5, "anchor-output-only"),
        ]
        for label, pattern, weight, rule_id in anchors:
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                scores[label] += weight
                matched[label].append(rule_id)
        return

    # SIF-specific structural evidence: candidate columns are informative but
    # not conclusive.  Join expressions, missing candidates, and output wording
    # receive distinct anchors before ATTRIBUTE is used as the fallback.
    if any("=" in candidate for candidate in candidates) or "join key" in attribute:
        scores["REL"] += 8
        matched["REL"].append("anchor-candidate-join")
    if not candidates and re.search(
        r"\b(no|not|cannot|missing|lacks?)\b.{0,45}\b(column|field|table|link|schema|data)\b",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        scores["GAP"] += 8
        matched["GAP"].append("anchor-empty-candidates-gap")
    if len(candidates) >= 2:
        scores["ATTRIBUTE"] += 3
        matched["ATTRIBUTE"].append("anchor-multiple-candidates")
    if re.search(r"\b(which|what)\b.{0,30}\b(return|returned|output|format)\b", question):
        scores["OUTPUT"] += 6
        matched["OUTPUT"].append("anchor-return-question")
    if re.search(r"\b(date|time|year|snapshot|version|latest|earliest|current)\b", attribute):
        scores["TEMP"] += 3
        matched["TEMP"].append("anchor-temporal-attribute")
    if re.search(r"\b(count|percentage|ratio|average|sum|metric|rate|score)\b", attribute):
        scores["METRIC"] += 3
        matched["METRIC"].append("anchor-metric-attribute")
    if re.search(r"\b(code|status|unit|literal|value|indicator)\b", attribute):
        scores["VALUE"] += 3
        matched["VALUE"].append("anchor-value-attribute")


def classify(row: dict[str, str]) -> dict[str, Any]:
    architecture = row["architecture"]
    text = normalized_text(row)
    rules = BIF_RULES if architecture == "bif_only" else SIF_RULES
    labels = BIF_LABELS if architecture == "bif_only" else SIF_LABELS
    fallback = "RG" if architecture == "bif_only" else "ATTRIBUTE"
    scores, matched = score_rules(text, rules)
    add_structural_scores(architecture, row, text, scores, matched)

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    winner, winner_score = ordered[0]
    runner, runner_score = ordered[1]
    margin = winner_score - runner_score

    if winner_score == 0:
        winner = fallback
        status = "unclassifiable"
        confidence = "low"
        review_reason = "no rule matched; architecture fallback assigned"
    elif margin <= 1 and runner_score >= 4:
        status = "ambiguous"
        confidence = "low"
        review_reason = f"top-score margin <= 1 ({winner}={winner_score}, {runner}={runner_score})"
    elif winner_score < 5:
        status = "weak_match"
        confidence = "low"
        review_reason = f"winning score below 5 ({winner_score})"
    elif margin <= 3 and runner_score >= 5:
        status = "ambiguous"
        confidence = "medium"
        review_reason = f"small top-score margin ({winner}={winner_score}, {runner}={runner_score})"
    elif winner_score >= 10 and margin >= 5:
        status = "assigned"
        confidence = "high"
        review_reason = ""
    else:
        status = "assigned"
        confidence = "medium"
        review_reason = ""

    close = [
        label
        for label, score in ordered[1:]
        if score >= 5 and score >= winner_score - 3
    ]
    mixed_issue = int(bool(close))
    review_required = int(status != "assigned" or confidence == "low")
    return {
        "native_label": winner,
        "native_label_name": labels[winner],
        "common_label": COMMON_MAP[winner],
        "classification_status": status,
        "confidence": confidence,
        "review_required": review_required,
        "review_reason": review_reason,
        "mixed_issue": mixed_issue,
        "secondary_candidates": "|".join(close),
        "winner_score": winner_score,
        "runner_up_label": runner,
        "runner_up_score": runner_score,
        "score_margin": margin,
        "matched_rules": "|".join(matched[winner]),
        "all_scores_json": json.dumps(scores, ensure_ascii=False, sort_keys=True),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def distribution_rows(
    coded: list[dict[str, Any]], label_field: str
) -> list[dict[str, Any]]:
    included = [row for row in coded if row["rq1_included"] == "1"]
    result: list[dict[str, Any]] = []
    architecture_denominators = {
        "bif_only": {"turns": 432, "clarified_queries": 212, "benchmark_queries": 1534},
        "sif_only": {"turns": 375, "clarified_queries": 334, "benchmark_queries": 1524},
    }
    for architecture in ("bif_only", "sif_only"):
        subset = [row for row in included if row["architecture"] == architecture]
        labels = sorted({str(row[label_field]) for row in subset})
        denom = architecture_denominators[architecture]
        for label in labels:
            rows = [row for row in subset if row[label_field] == label]
            query_count = len({int(row["question_id"]) for row in rows})
            result.append(
                {
                    "architecture": architecture,
                    "label": label,
                    "turn_count": len(rows),
                    "turn_share": round(len(rows) / denom["turns"], 6),
                    "query_count": query_count,
                    "benchmark_query_share": round(
                        query_count / denom["benchmark_queries"], 6
                    ),
                    "clarified_query_share": round(
                        query_count / denom["clarified_queries"], 6
                    ),
                    "turn_denominator": denom["turns"],
                    "benchmark_query_denominator": denom["benchmark_queries"],
                    "clarified_query_denominator": denom["clarified_queries"],
                }
            )
    return result


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = read_rows()
    coded: list[dict[str, Any]] = []
    for row in rows:
        if row.get("rq1_included") != "1":
            result = {
                "native_label": "EXCLUDED",
                "native_label_name": "excluded by the pre-specified SIF trace rule",
                "common_label": "EXCLUDED",
                "classification_status": "excluded",
                "confidence": "not_applicable",
                "review_required": 0,
                "review_reason": row.get("exclusion_reason", ""),
                "mixed_issue": 0,
                "secondary_candidates": "",
                "winner_score": "",
                "runner_up_label": "",
                "runner_up_score": "",
                "score_margin": "",
                "matched_rules": "",
                "all_scores_json": "{}",
            }
        else:
            result = classify(row)
        coded.append({**row, **result, "rule_version": RULE_VERSION})

    output_fields = list(coded[0].keys())
    coding_path = OUTPUT_DIR / "rq1_rule_coding.csv"
    write_csv(coding_path, coded, output_fields)

    review = [row for row in coded if int(row["review_required"]) == 1]
    review_path = OUTPUT_DIR / "rq1_rule_coding_review_required.csv"
    write_csv(review_path, review, output_fields)

    native = distribution_rows(coded, "native_label")
    common = distribution_rows(coded, "common_label")
    distribution_fields = [
        "architecture",
        "label",
        "turn_count",
        "turn_share",
        "query_count",
        "benchmark_query_share",
        "clarified_query_share",
        "turn_denominator",
        "benchmark_query_denominator",
        "clarified_query_denominator",
    ]
    native_path = OUTPUT_DIR / "rq1_native_distribution_provisional.csv"
    common_path = OUTPUT_DIR / "rq1_common_distribution_provisional.csv"
    write_csv(native_path, native, distribution_fields)
    write_csv(common_path, common, distribution_fields)

    included = [row for row in coded if row["rq1_included"] == "1"]
    summary = {
        "rule_version": RULE_VERSION,
        "provisional_not_adjudicated": True,
        "input": {
            "path": str(INPUT),
            "sha256": sha256(INPUT),
            "rows": len(rows),
            "classification_text_fields": list(ALLOWED_TEXT_FIELDS),
            "forbidden_fields": sorted(FORBIDDEN_FIELDS),
        },
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "exclusion": {
            "sif_qids": list(range(1, 11)),
            "excluded_rows": sum(row["rq1_included"] != "1" for row in coded),
        },
        "included": {
            "turns": len(included),
            "by_architecture": dict(Counter(row["architecture"] for row in included)),
        },
        "classification_status": dict(
            Counter(row["classification_status"] for row in included)
        ),
        "confidence": dict(Counter(row["confidence"] for row in included)),
        "review_required": len(review),
        "mixed_issue": sum(int(row["mixed_issue"]) for row in included),
        "native_label_counts": {
            architecture: dict(
                Counter(
                    row["native_label"]
                    for row in included
                    if row["architecture"] == architecture
                )
            )
            for architecture in ("bif_only", "sif_only")
        },
        "common_label_counts": {
            architecture: dict(
                Counter(
                    row["common_label"]
                    for row in included
                    if row["architecture"] == architecture
                )
            )
            for architecture in ("bif_only", "sif_only")
        },
        "outputs": {
            "coding": str(coding_path),
            "review_required": str(review_path),
            "native_distribution": str(native_path),
            "common_distribution": str(common_path),
        },
    }
    summary_path = OUTPUT_DIR / "rq1_rule_coding_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
