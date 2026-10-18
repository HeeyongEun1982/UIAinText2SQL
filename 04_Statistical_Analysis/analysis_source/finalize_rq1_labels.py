"""Create an intermediate RQ1 label table before full-content review.

The provisional rule output is used only as a work list.  Every row marked
``review_required=1`` must appear in ``PROVISIONAL_REVIEW_LABELS``; the script stops
if even one review item is missing or if an extra key is present.  Labels are
based on the blinded decision context, clarification question, and options.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
RULE_CODING = HERE / "outputs" / "rq1_rule_coding.csv"
OUTPUT_DIR = HERE / "outputs"
ROOT = HERE.parents[3]
SIF_ROOT = (
    ROOT
    / "revised_final_bif_sif"
    / "1.sif"
    / "runs"
    / "1.sif_full_1534_20260831_215201"
)

LABEL_NAMES = {
    "RG": "referent/concept grounding",
    "RS": "result selection/multiplicity",
    "MC": "metric/calculation",
    "SC": "population/scope",
    "PL": "predicate logic/boundary",
    "TM": "temporal scope",
    "UG": "unit/granularity",
    "OR": "output/representation",
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


def key(architecture: str, question_id: int, turn: int) -> str:
    return f"{architecture}:{question_id}:{turn}"


# Explicit labels assigned by reading each flagged record's decision context,
# clarification question, and complete option set.  Entries are grouped in
# source-qid order to make the review auditable against the CSV work list.
PROVISIONAL_REVIEW_LABELS: dict[str, str] = {
    # BIF review batch 1
    key("bif_only", 21, 1): "RG",
    key("bif_only", 23, 1): "MC",
    key("bif_only", 25, 1): "MC",
    key("bif_only", 26, 2): "UG",
    key("bif_only", 26, 4): "RG",
    key("bif_only", 28, 1): "RG",
    key("bif_only", 38, 4): "RG",
    key("bif_only", 42, 2): "TM",
    key("bif_only", 42, 3): "MC",
    key("bif_only", 44, 1): "MC",
    key("bif_only", 58, 1): "RS",
    key("bif_only", 67, 1): "MC",
    key("bif_only", 67, 2): "RG",
    key("bif_only", 83, 1): "SC",
    key("bif_only", 84, 1): "RG",
    key("bif_only", 84, 3): "RS",
    key("bif_only", 87, 1): "PL",
    key("bif_only", 87, 2): "RG",
    key("bif_only", 87, 4): "RG",
    key("bif_only", 88, 1): "RG",
    key("bif_only", 94, 2): "PL",
    key("bif_only", 108, 5): "TM",
    key("bif_only", 116, 1): "TM",
    key("bif_only", 120, 1): "TM",
    key("bif_only", 120, 2): "RG",
    key("bif_only", 120, 3): "OR",
    key("bif_only", 129, 1): "MC",
    key("bif_only", 131, 1): "MC",
    key("bif_only", 144, 1): "UG",
    key("bif_only", 145, 1): "UG",
    key("bif_only", 170, 1): "TM",
    key("bif_only", 176, 1): "RG",
    key("bif_only", 176, 2): "RG",
    key("bif_only", 181, 1): "MC",
    key("bif_only", 207, 2): "OR",
    key("bif_only", 220, 3): "RS",
    key("bif_only", 233, 1): "MC",
    key("bif_only", 233, 2): "RG",
    key("bif_only", 243, 2): "PL",
    key("bif_only", 253, 2): "UG",
    # BIF review batch 2
    key("bif_only", 254, 3): "UG",
    key("bif_only", 260, 2): "UG",
    key("bif_only", 273, 1): "MC",
    key("bif_only", 293, 1): "UG",
    key("bif_only", 297, 1): "RG",
    key("bif_only", 305, 1): "UG",
    key("bif_only", 324, 2): "MC",
    key("bif_only", 324, 3): "MC",
    key("bif_only", 325, 1): "PL",
    key("bif_only", 329, 2): "OR",
    key("bif_only", 334, 1): "RG",
    key("bif_only", 334, 2): "RS",
    key("bif_only", 339, 2): "RS",
    key("bif_only", 354, 1): "RG",
    key("bif_only", 362, 2): "RG",
    key("bif_only", 390, 1): "RG",
    key("bif_only", 420, 1): "PL",
    key("bif_only", 421, 1): "MC",
    key("bif_only", 426, 1): "MC",
    key("bif_only", 442, 1): "PL",
    key("bif_only", 448, 2): "RS",
    key("bif_only", 448, 3): "OR",
    key("bif_only", 503, 1): "RS",
    key("bif_only", 558, 1): "RG",
    key("bif_only", 558, 3): "SC",
    key("bif_only", 576, 1): "PL",
    key("bif_only", 584, 2): "SC",
    key("bif_only", 587, 1): "UG",
    key("bif_only", 595, 1): "PL",
    key("bif_only", 612, 1): "RS",
    key("bif_only", 616, 1): "RS",
    key("bif_only", 616, 2): "RS",
    key("bif_only", 618, 1): "RG",
    key("bif_only", 642, 1): "TM",
    key("bif_only", 650, 2): "RG",
    key("bif_only", 673, 1): "MC",
    key("bif_only", 683, 2): "TM",
    key("bif_only", 685, 1): "RG",
    key("bif_only", 692, 1): "RS",
    key("bif_only", 692, 2): "RS",
    # BIF review batch 3
    key("bif_only", 692, 3): "OR",
    key("bif_only", 715, 1): "UG",
    key("bif_only", 716, 1): "UG",
    key("bif_only", 720, 1): "MC",
    key("bif_only", 763, 1): "OR",
    key("bif_only", 767, 2): "UG",
    key("bif_only", 840, 1): "OR",
    key("bif_only", 851, 1): "RG",
    key("bif_only", 851, 2): "RG",
    key("bif_only", 871, 1): "RG",
    key("bif_only", 880, 2): "SC",
    key("bif_only", 891, 1): "RG",
    key("bif_only", 917, 1): "RG",
    key("bif_only", 922, 1): "TM",
    key("bif_only", 942, 1): "SC",
    key("bif_only", 945, 2): "RG",
    key("bif_only", 951, 1): "PL",
    key("bif_only", 963, 2): "UG",
    key("bif_only", 976, 1): "RS",
    key("bif_only", 976, 2): "SC",
    key("bif_only", 988, 1): "TM",
    key("bif_only", 988, 2): "TM",
    key("bif_only", 1012, 1): "RG",
    key("bif_only", 1013, 2): "TM",
    key("bif_only", 1016, 1): "TM",
    key("bif_only", 1016, 2): "RG",
    key("bif_only", 1016, 3): "MC",
    key("bif_only", 1038, 1): "RS",
    key("bif_only", 1054, 1): "RG",
    key("bif_only", 1054, 2): "RG",
    key("bif_only", 1058, 2): "OR",
    key("bif_only", 1062, 2): "PL",
    key("bif_only", 1166, 3): "UG",
    key("bif_only", 1171, 2): "TM",
    key("bif_only", 1189, 1): "MC",
    key("bif_only", 1200, 2): "MC",
    key("bif_only", 1239, 1): "PL",
    key("bif_only", 1241, 1): "PL",
    key("bif_only", 1253, 3): "RS",
    key("bif_only", 1290, 2): "UG",
    # BIF review batch 4
    key("bif_only", 1292, 1): "PL",
    key("bif_only", 1292, 2): "PL",
    key("bif_only", 1322, 2): "SC",
    key("bif_only", 1330, 1): "RS",
    key("bif_only", 1339, 1): "MC",
    key("bif_only", 1339, 2): "UG",
    key("bif_only", 1353, 1): "RG",
    key("bif_only", 1353, 2): "RG",
    key("bif_only", 1385, 1): "PL",
    key("bif_only", 1430, 1): "RG",
    key("bif_only", 1434, 1): "RG",
    key("bif_only", 1434, 2): "SC",
    key("bif_only", 1434, 3): "RG",
    key("bif_only", 1436, 1): "MC",
    key("bif_only", 1436, 2): "RG",
    key("bif_only", 1450, 1): "PL",
    key("bif_only", 1450, 2): "MC",
    key("bif_only", 1456, 1): "MC",
    key("bif_only", 1457, 1): "PL",
    key("bif_only", 1457, 2): "UG",
    key("bif_only", 1467, 1): "UG",
    key("bif_only", 1488, 3): "RG",
    key("bif_only", 1507, 1): "RG",
    key("bif_only", 1508, 2): "SC",
    key("bif_only", 1511, 2): "UG",
    key("bif_only", 1511, 3): "RG",
    key("bif_only", 1513, 1): "TM",
    key("bif_only", 1513, 2): "RG",
    key("bif_only", 1513, 4): "RG",
    key("bif_only", 1515, 1): "RG",
    key("bif_only", 1515, 3): "TM",
    key("bif_only", 1520, 1): "RS",
    key("bif_only", 1522, 1): "UG",
    key("bif_only", 1524, 1): "UG",
    key("bif_only", 1525, 3): "PL",
    key("bif_only", 1528, 1): "RG",
    key("bif_only", 1528, 4): "MC",
    key("bif_only", 1530, 3): "SC",
    key("bif_only", 1531, 1): "MC",
    key("bif_only", 1532, 1): "MC",
    key("bif_only", 1533, 1): "TM",
    key("bif_only", 1533, 2): "RG",
    # SIF review batch 1
    key("sif_only", 15, 2): "METRIC",
    key("sif_only", 25, 1): "ATTRIBUTE",
    key("sif_only", 26, 1): "GAP",
    key("sif_only", 26, 2): "ATTRIBUTE",
    key("sif_only", 28, 1): "ATTRIBUTE",
    key("sif_only", 32, 1): "GAP",
    key("sif_only", 34, 1): "ATTRIBUTE",
    key("sif_only", 37, 1): "ATTRIBUTE",
    key("sif_only", 42, 1): "ATTRIBUTE",
    key("sif_only", 51, 1): "ATTRIBUTE",
    key("sif_only", 56, 1): "VALUE",
    key("sif_only", 56, 2): "VALUE",
    key("sif_only", 59, 1): "SCOPE",
    key("sif_only", 65, 1): "ATTRIBUTE",
    key("sif_only", 75, 1): "VALUE",
    key("sif_only", 85, 1): "SCOPE",
    key("sif_only", 86, 1): "ATTRIBUTE",
    key("sif_only", 95, 1): "REL",
    key("sif_only", 101, 1): "TEMP",
    key("sif_only", 110, 1): "GAP",
    key("sif_only", 125, 1): "GAP",
    key("sif_only", 125, 2): "ATTRIBUTE",
    key("sif_only", 128, 1): "REL",
    key("sif_only", 130, 1): "REL",
    key("sif_only", 141, 1): "OUTPUT",
    key("sif_only", 142, 1): "ATTRIBUTE",
    key("sif_only", 163, 1): "OUTPUT",
    key("sif_only", 173, 1): "ATTRIBUTE",
    key("sif_only", 174, 1): "REL",
    key("sif_only", 176, 1): "ATTRIBUTE",
    key("sif_only", 177, 1): "REL",
    key("sif_only", 179, 1): "ATTRIBUTE",
    key("sif_only", 191, 1): "METRIC",
    key("sif_only", 233, 1): "OUTPUT",
    key("sif_only", 246, 1): "VALUE",
    key("sif_only", 321, 1): "OUTPUT",
    key("sif_only", 326, 1): "REL",
    key("sif_only", 328, 1): "VALUE",
    key("sif_only", 364, 1): "ATTRIBUTE",
    key("sif_only", 365, 1): "OUTPUT",
    # SIF review batch 2
    key("sif_only", 392, 1): "TEMP",
    key("sif_only", 403, 1): "OUTPUT",
    key("sif_only", 427, 1): "SCOPE",
    key("sif_only", 445, 1): "SCOPE",
    key("sif_only", 447, 1): "ATTRIBUTE",
    key("sif_only", 447, 2): "SCOPE",
    key("sif_only", 449, 1): "ATTRIBUTE",
    key("sif_only", 453, 1): "ATTRIBUTE",
    key("sif_only", 483, 1): "GAP",
    key("sif_only", 506, 2): "SCOPE",
    key("sif_only", 539, 1): "OUTPUT",
    key("sif_only", 541, 1): "OUTPUT",
    key("sif_only", 546, 1): "OUTPUT",
    key("sif_only", 562, 1): "METRIC",
    key("sif_only", 599, 1): "METRIC",
    key("sif_only", 633, 1): "ATTRIBUTE",
    key("sif_only", 634, 1): "REL",
    key("sif_only", 635, 1): "ATTRIBUTE",
    key("sif_only", 639, 2): "GAP",
    key("sif_only", 646, 1): "OUTPUT",
    # SIF review batch 3
    key("sif_only", 648, 1): "OUTPUT",
    key("sif_only", 652, 1): "REL",
    key("sif_only", 656, 1): "OUTPUT",
    key("sif_only", 657, 1): "REL",
    key("sif_only", 667, 1): "REL",
    key("sif_only", 678, 1): "ATTRIBUTE",
    key("sif_only", 680, 1): "REL",
    key("sif_only", 681, 1): "SCOPE",
    key("sif_only", 682, 1): "OUTPUT",
    key("sif_only", 685, 1): "ATTRIBUTE",
    key("sif_only", 685, 2): "TEMP",
    key("sif_only", 689, 1): "TEMP",
    key("sif_only", 692, 1): "RANK",
    key("sif_only", 714, 1): "ATTRIBUTE",
    key("sif_only", 849, 1): "ATTRIBUTE",
    key("sif_only", 864, 1): "SCOPE",
    key("sif_only", 893, 1): "RANK",
    key("sif_only", 908, 1): "METRIC",
    key("sif_only", 928, 1): "RANK",
    key("sif_only", 948, 1): "METRIC",
    # SIF review batch 4
    key("sif_only", 950, 1): "METRIC",
    key("sif_only", 956, 1): "RANK",
    key("sif_only", 958, 1): "METRIC",
    key("sif_only", 973, 1): "METRIC",
    key("sif_only", 978, 1): "METRIC",
    key("sif_only", 979, 1): "ATTRIBUTE",
    key("sif_only", 984, 1): "METRIC",
    key("sif_only", 987, 1): "METRIC",
    key("sif_only", 998, 1): "METRIC",
    key("sif_only", 998, 2): "RANK",
    key("sif_only", 1005, 1): "OUTPUT",
    key("sif_only", 1006, 1): "METRIC",
    key("sif_only", 1010, 1): "METRIC",
    key("sif_only", 1014, 1): "SCOPE",
    key("sif_only", 1016, 2): "METRIC",
    key("sif_only", 1017, 1): "ATTRIBUTE",
    key("sif_only", 1026, 1): "OUTPUT",
    key("sif_only", 1037, 1): "TEMP",
    key("sif_only", 1050, 2): "TEMP",
    key("sif_only", 1052, 2): "TEMP",
    # SIF review batch 5
    key("sif_only", 1090, 1): "REL",
    key("sif_only", 1090, 2): "TEMP",
    key("sif_only", 1119, 1): "GAP",
    key("sif_only", 1119, 2): "GAP",
    key("sif_only", 1128, 1): "REL",
    key("sif_only", 1147, 1): "REL",
    key("sif_only", 1148, 2): "METRIC",
    key("sif_only", 1152, 1): "METRIC",
    key("sif_only", 1164, 1): "REL",
    key("sif_only", 1167, 1): "METRIC",
    key("sif_only", 1168, 1): "SCOPE",
    key("sif_only", 1168, 2): "ATTRIBUTE",
    key("sif_only", 1178, 1): "ATTRIBUTE",
    key("sif_only", 1186, 1): "SCOPE",
    key("sif_only", 1190, 1): "VALUE",
    key("sif_only", 1195, 1): "SCOPE",
    key("sif_only", 1196, 1): "SCOPE",
    key("sif_only", 1197, 1): "OUTPUT",
    key("sif_only", 1198, 1): "SCOPE",
    key("sif_only", 1200, 1): "SCOPE",
    # SIF review batch 6
    key("sif_only", 1223, 1): "METRIC",
    key("sif_only", 1247, 1): "SCOPE",
    key("sif_only", 1250, 1): "SCOPE",
    key("sif_only", 1250, 2): "VALUE",
    key("sif_only", 1253, 1): "OUTPUT",
    key("sif_only", 1285, 1): "ATTRIBUTE",
    key("sif_only", 1288, 1): "OUTPUT",
    key("sif_only", 1296, 2): "RANK",
    key("sif_only", 1335, 1): "METRIC",
    key("sif_only", 1335, 2): "GAP",
    key("sif_only", 1376, 1): "METRIC",
    key("sif_only", 1387, 1): "GAP",
    key("sif_only", 1404, 1): "ATTRIBUTE",
    key("sif_only", 1418, 1): "ATTRIBUTE",
    key("sif_only", 1450, 1): "GAP",
    key("sif_only", 1472, 1): "ATTRIBUTE",
    key("sif_only", 1478, 1): "ATTRIBUTE",
    key("sif_only", 1504, 1): "METRIC",
    key("sif_only", 1510, 1): "METRIC",
    key("sif_only", 1512, 1): "METRIC",
    # SIF review batch 7
    key("sif_only", 1520, 1): "METRIC",
    key("sif_only", 1523, 1): "GAP",
    key("sif_only", 1524, 1): "GAP",
    key("sif_only", 1524, 2): "METRIC",
    key("sif_only", 1526, 1): "VALUE",
    key("sif_only", 1529, 1): "METRIC",
    key("sif_only", 1530, 1): "METRIC",
}


# A second category-boundary pass re-read these turns against the exact
# definitions printed in the v0.3 methodology.  Keeping these decisions apart
# from PROVISIONAL_REVIEW_LABELS makes it possible to distinguish the original
# ambiguous/weak review from later category-boundary corrections.  The final
# entry deliberately assigns SIF q1016-t1 to METRIC rather than the suggested
# ATTRIBUTE: the options contrast a metric recalculated from raw laps with the
# stored official fastest-lap metric, which is the methodology's explicit
# METRIC boundary rule.
BOUNDARY_AUDIT: dict[str, tuple[str, str]] = {
    key("bif_only", 125, 2): (
        "UG",
        "district versus state changes the group level of the calculation",
    ),
    key("bif_only", 87, 3): (
        "RG",
        "the options change the meaning of the term valid email address",
    ),
    key("bif_only", 430, 1): (
        "RS",
        "the options select the ranking criterion used to obtain the top two",
    ),
    key("bif_only", 362, 1): (
        "RG",
        "the options identify which card entity the request targets",
    ),
    key("bif_only", 108, 3): (
        "RG",
        "the options identify which kind of account the term denotes",
    ),
    key("bif_only", 1437, 1): (
        "SC",
        "the options change the included population from attendees to all approved members",
    ),
    key("sif_only", 1168, 2): (
        "TEMP",
        "the options select between two event-date columns",
    ),
    key("sif_only", 220, 1): (
        "RANK",
        "the options change how the top three elements are ranked and selected",
    ),
    key("sif_only", 1442, 1): (
        "METRIC",
        "the options contrast any negative budget row with event-level summed remaining",
    ),
    key("sif_only", 130, 2): (
        "SCOPE",
        "the options change the ownership filter from an explicit credit type to any card",
    ),
    key("sif_only", 263, 1): (
        "SCOPE",
        "the options apply an any-bond versus all-bonds inclusion condition",
    ),
    key("sif_only", 445, 1): (
        "OUTPUT",
        "the options choose which flavor-text attribute or attributes are returned",
    ),
    key("sif_only", 445, 2): (
        "OUTPUT",
        "the options choose which type attribute or attributes are returned",
    ),
    key("sif_only", 128, 2): (
        "METRIC",
        "the options change the counting unit among people, accounts, and holder-account rows",
    ),
    key("sif_only", 386, 1): (
        "METRIC",
        "the options change the counting unit from rows to distinct card entities",
    ),
    key("sif_only", 1315, 2): (
        "GAP",
        "student status is not directly represented and requires a proxy or unavailable field",
    ),
    key("sif_only", 876, 1): (
        "SCOPE",
        "the options change the database condition used to include non-finishers",
    ),
    key("sif_only", 90, 1): (
        "SCOPE",
        "the options change the eligibility filter from any loan record to an approval status",
    ),
    key("sif_only", 1016, 1): (
        "METRIC",
        "the options contrast a fastest-lap metric recalculated from raw laps with the stored official metric",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def final_distribution(
    rows: list[dict[str, Any]], label_field: str
) -> list[dict[str, Any]]:
    denominators = {
        "bif_only": (432, 212, 1534),
        "sif_only": (375, 334, 1524),
    }
    output: list[dict[str, Any]] = []
    for architecture in ("bif_only", "sif_only"):
        subset = [
            row
            for row in rows
            if row["architecture"] == architecture and row["rq1_included"] == "1"
        ]
        turn_n, clarified_n, eligible_n = denominators[architecture]
        benchmark_n = 1534
        for label in sorted({str(row[label_field]) for row in subset}):
            selected = [row for row in subset if row[label_field] == label]
            query_n = len({int(row["question_id"]) for row in selected})
            output.append(
                {
                    "architecture": architecture,
                    "category": label,
                    "turn_count": len(selected),
                    "turn_percent": round(100 * len(selected) / turn_n, 6),
                    "query_count": query_n,
                    # query_percent is retained as the manuscript-facing alias
                    # for prevalence over the complete 1,534-query benchmark.
                    "query_percent": round(100 * query_n / benchmark_n, 6),
                    "benchmark_query_percent": round(
                        100 * query_n / benchmark_n, 6
                    ),
                    "eligible_query_percent": round(100 * query_n / eligible_n, 6),
                    "clarified_query_percent": round(
                        100 * query_n / clarified_n, 6
                    ),
                    "turn_denominator": turn_n,
                    "benchmark_query_denominator": benchmark_n,
                    "eligible_query_denominator": eligible_n,
                    "clarified_query_denominator": clarified_n,
                }
            )
    return output


def unelicited_sif_conflicts() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for qid in (125, 128, 1119, 1450):
        source = (
            SIF_ROOT
            / str(qid)
            / "P01"
            / "frames"
            / "direct_sif_initial_grounding.json"
        )
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        conflicts = payload.get("schema_conflicts") or []
        for index, conflict in enumerate(conflicts, start=1):
            if index <= 2:
                continue
            output.append(
                {
                    "architecture": "sif_only",
                    "question_id": qid,
                    "conflict_index": index,
                    "elicited": 0,
                    "reason": "not elicited because the SIF-only service cap is two turns",
                    "attribute_name": str(conflict.get("attribute_name") or ""),
                    "candidate_columns": " || ".join(
                        str(value) for value in (conflict.get("candidate_columns") or [])
                    ),
                    "conflict_description": str(
                        conflict.get("conflict_description") or ""
                    ),
                    "source_path": str(source),
                    "source_sha256": sha256(source),
                }
            )
    output.sort(key=lambda row: (row["question_id"], row["conflict_index"]))
    return output


def main() -> None:
    rows = read_csv(RULE_CODING)
    included_source_rows = [row for row in rows if row["rq1_included"] == "1"]
    included_by_architecture = Counter(
        row["architecture"] for row in included_source_rows
    )
    included_queries_by_architecture = {
        architecture: len(
            {
                int(row["question_id"])
                for row in included_source_rows
                if row["architecture"] == architecture
            }
        )
        for architecture in ("bif_only", "sif_only")
    }
    expected_turns = {"bif_only": 432, "sif_only": 375}
    expected_queries = {"bif_only": 212, "sif_only": 334}
    if dict(included_by_architecture) != expected_turns:
        raise RuntimeError(
            f"Unexpected included turn counts: {dict(included_by_architecture)}"
        )
    if included_queries_by_architecture != expected_queries:
        raise RuntimeError(
            f"Unexpected clarified-query counts: {included_queries_by_architecture}"
        )

    required = {
        key(row["architecture"], int(row["question_id"]), int(row["turn"]))
        for row in rows
        if row["rq1_included"] == "1" and row["review_required"] == "1"
    }
    supplied = set(PROVISIONAL_REVIEW_LABELS)
    missing = sorted(required - supplied)
    extra = sorted(supplied - required)
    if missing or extra:
        raise RuntimeError(
            f"Provisional review map is incomplete: missing={len(missing)} "
            f"extra={len(extra)}; next_missing={missing[:20]}; extra_keys={extra[:20]}"
        )

    included_keys = {
        key(row["architecture"], int(row["question_id"]), int(row["turn"]))
        for row in included_source_rows
    }
    audit_missing = sorted(set(BOUNDARY_AUDIT) - included_keys)
    if audit_missing:
        raise RuntimeError(f"Boundary-audit keys not found in included corpus: {audit_missing}")

    final_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["rq1_included"] != "1":
            final_rows.append(
                {
                    **row,
                    "decision_delta": "",
                    "final_native_label": "EXCLUDED",
                    "final_native_label_name": "excluded process trace",
                    "final_common_label": "EXCLUDED",
                    "single_coder_reviewed": 0,
                    "review_note": row.get("exclusion_reason", ""),
                    "boundary_audit_reviewed": 0,
                    "boundary_audit_note": "",
                    "unresolved": 0,
                }
            )
            continue

        row_key = key(row["architecture"], int(row["question_id"]), int(row["turn"]))
        provisionally_reviewed = row_key in PROVISIONAL_REVIEW_LABELS
        pre_audit_label = PROVISIONAL_REVIEW_LABELS.get(row_key, row["native_label"])
        audit_entry = BOUNDARY_AUDIT.get(row_key)
        final_label = audit_entry[0] if audit_entry else pre_audit_label
        question = (row.get("clarification_question") or row.get("decision_context") or "").strip()
        decision_delta = f"Options change the {LABEL_NAMES[final_label]} decision: {question}"
        if audit_entry:
            review_note = (
                "Boundary case re-read against the v0.3 methodology using only the blinded "
                "decision context, question, options, and attribute/candidate fields; "
                f"changed {pre_audit_label} to {final_label}."
            )
        elif provisionally_reviewed:
            review_note = (
                "Provisional case review used the blinded decision context, question, and all options; "
                f"assigned {final_label} by the primary decision delta "
                f"(rule draft: {row['native_label']}, status: {row['classification_status']})."
            )
        else:
            review_note = (
                "Rule assignment retained after category-level audit; this row was not in "
                "the mandatory ambiguous/weak review set."
            )
        final_rows.append(
            {
                **row,
                "decision_delta": decision_delta,
                "final_native_label": final_label,
                "final_native_label_name": LABEL_NAMES[final_label],
                "final_common_label": COMMON_MAP[final_label],
                "single_coder_reviewed": 0,
                "review_note": review_note,
                "boundary_audit_reviewed": int(audit_entry is not None),
                "boundary_audit_note": audit_entry[1] if audit_entry else "",
                "unresolved": 0,
            }
        )

    final_path = OUTPUT_DIR / "rq1_turn_labels_final.csv"
    write_csv(final_path, final_rows, list(final_rows[0].keys()))
    dist_fields = [
        "architecture",
        "category",
        "turn_count",
        "turn_percent",
        "query_count",
        "query_percent",
        "benchmark_query_percent",
        "eligible_query_percent",
        "clarified_query_percent",
        "turn_denominator",
        "benchmark_query_denominator",
        "eligible_query_denominator",
        "clarified_query_denominator",
    ]
    native = final_distribution(final_rows, "final_native_label")
    common = final_distribution(final_rows, "final_common_label")
    native_path = OUTPUT_DIR / "rq1_native_distribution_final.csv"
    common_path = OUTPUT_DIR / "rq1_common_distribution_final.csv"
    write_csv(native_path, native, dist_fields)
    write_csv(common_path, common, dist_fields)

    unelicited = unelicited_sif_conflicts()
    unelicited_path = OUTPUT_DIR / "rq1_sif_detected_not_elicited.csv"
    write_csv(unelicited_path, unelicited, list(unelicited[0].keys()))

    included = [row for row in final_rows if row["rq1_included"] == "1"]
    summary = {
        "coding": "intermediate labels pending full-content review",
        "input_rule_coding": {
            "path": str(RULE_CODING),
            "sha256": sha256(RULE_CODING),
        },
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "included_turns": len(included),
        "provisionally_reviewed_flagged_turns": sum(
            int(key(row["architecture"], int(row["question_id"]), int(row["turn"])) in PROVISIONAL_REVIEW_LABELS)
            for row in included
        ),
        "boundary_audit_reviewed_turns": sum(
            int(row["boundary_audit_reviewed"]) for row in included
        ),
        "boundary_audit_final_labels": {
            audit_key: label_and_reason[0]
            for audit_key, label_and_reason in sorted(BOUNDARY_AUDIT.items())
        },
        "unresolved": sum(int(row["unresolved"]) for row in included),
        "final_native_counts": {
            architecture: dict(
                sorted(
                    Counter(
                        row["final_native_label"]
                        for row in included
                        if row["architecture"] == architecture
                    ).items()
                )
            )
            for architecture in ("bif_only", "sif_only")
        },
        "detected_not_elicited_sif_conflicts": len(unelicited),
        "labeling_fields_used": [
            "decision_context",
            "clarification_question",
            "options_text",
            "attribute_name",
            "candidate_columns",
        ],
        "outcome_fields_not_used_for_type_coding": [
            "selected_option_id",
            "answer_text",
            "technical_reason",
            "ground_truth_analysis",
            "ground_truth_sql",
            "generated_sql",
            "correctness",
        ],
        "analysis_denominators": {
            "bif_only": {
                "turns": 432,
                "clarified_queries": 212,
                "benchmark_queries": 1534,
                "eligible_queries": 1534,
            },
            "sif_only": {
                "turns_after_qid_1_through_10_exclusion": 375,
                "clarified_queries_after_exclusion": 334,
                "benchmark_queries": 1534,
                "eligible_queries_after_exclusion": 1524,
            },
        },
        "outputs": {
            "turn_labels": str(final_path),
            "native_distribution": str(native_path),
            "common_distribution": str(common_path),
            "detected_not_elicited": str(unelicited_path),
        },
    }
    summary_path = OUTPUT_DIR / "rq1_final_coding_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
