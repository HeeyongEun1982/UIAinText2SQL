#!/usr/bin/env python3
"""Audit clarification selections against the options shown to simulated users.

The audit covers the BIF-only and SIF-only turn census already normalized for
RQ1, plus both clarification stages of the combined run.  Every output row
retains the archived source path and SHA-256 digest.  The combined SIF stage
explicitly allowed ``none_of_the_above`` in addition to the displayed options;
that value is reported as a supported fallback, not as a mismatch.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
PAPER_DIR = ROOT.parent
PROJECT_ROOT = PAPER_DIR.parents[2]
RUN_BASE = PROJECT_ROOT / "revised_final_bif_sif"
RQ1_INPUT = ROOT / "inputs" / "rq1_turns_full.csv"
COMBINED_ROOT = (
    RUN_BASE / "2.bif_sif" / "runs" / "2.bif_sif_full_1534_20260823_012700"
)
OUTPUT_CSV = ROOT / "outputs" / "clarification_option_id_quality_by_turn.csv"
OUTPUT_JSON = ROOT / "outputs" / "clarification_option_id_quality_summary.json"

OUTPUT_FIELDS = [
    "condition",
    "stage",
    "question_id",
    "turn",
    "rq1_included",
    "selected_option_id",
    "offered_option_ids",
    "option_id_status",
    "clarification_question",
    "answer_text",
    "question_source_path",
    "question_source_sha256",
    "selection_source_path",
    "selection_source_sha256",
    "source_json_status",
    "source_json_error",
]

EXPECTED = {
    "bif_only:BIF": {
        "turns": 432,
        "queries": 212,
        "offered": 432,
        "supported_fallback": 0,
        "not_offered": 0,
        "missing": 0,
    },
    "sif_only:SIF": {
        "turns": 379,
        "queries": 338,
        "offered": 362,
        "supported_fallback": 0,
        "not_offered": 16,
        "missing": 1,
    },
    "bif_sif:BIF": {
        "turns": 4913,
        "queries": 1284,
        "offered": 4912,
        "supported_fallback": 0,
        "not_offered": 1,
        "missing": 0,
    },
    "bif_sif:SIF": {
        "turns": 6136,
        "queries": 1534,
        "offered": 6070,
        "supported_fallback": 66,
        "not_offered": 0,
        "missing": 0,
    },
}

EXPECTED_SIF_RQ1_INCLUDED = {
    "turns": 375,
    "queries": 334,
    "offered": 358,
    "supported_fallback": 0,
    "not_offered": 16,
    "missing": 1,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def selection_status(
    selected: str, offered: list[str], allowed_fallbacks: set[str] | None = None
) -> str:
    if not selected:
        return "missing"
    if selected in offered:
        return "offered"
    if selected in (allowed_fallbacks or set()):
        return "supported_fallback"
    return "not_offered"


def normalized_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with RQ1_INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            condition = source["architecture"]
            if condition not in {"bif_only", "sif_only"}:
                continue
            stage = "BIF" if condition == "bif_only" else "SIF"
            options = json.loads(source["options_json"])
            offered = [str(option.get("option_id", "")) for option in options]
            selected = source["selected_option_id"].strip()
            rows.append(
                {
                    "condition": condition,
                    "stage": stage,
                    "question_id": source["question_id"],
                    "turn": source["turn"],
                    "rq1_included": source["rq1_included"],
                    "selected_option_id": selected,
                    "offered_option_ids": " || ".join(offered),
                    "option_id_status": selection_status(selected, offered),
                    "clarification_question": source["clarification_question"],
                    "answer_text": source["answer_text"],
                    "question_source_path": source["source_path"],
                    "question_source_sha256": source["source_sha256"],
                    "selection_source_path": source["source_path"],
                    "selection_source_sha256": source["source_sha256"],
                    "source_json_status": "valid",
                    "source_json_error": "",
                }
            )
    return rows


def recover_bif_option_fields(text: str) -> tuple[list[str], str]:
    """Recover ASCII option IDs from a malformed archived JSON record."""
    offered = re.findall(r'"option_id"\s*:\s*"([^"\\]*)"', text)
    selected_match = re.search(
        r'"selected_option_id"\s*:\s*"([^"\\]*)"', text
    )
    selected = selected_match.group(1) if selected_match else ""
    return offered, selected


def combined_bif_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for qdir in sorted(
        (path for path in COMBINED_ROOT.iterdir() if path.is_dir() and path.name.isdigit()),
        key=lambda path: int(path.name),
    ):
        pattern_root = qdir / "P01" / "semantic_frontend" / "05_clarification"
        for path in sorted(pattern_root.glob("turn_*/turn_record.json")):
            source_status = "valid"
            source_error = ""
            text = path.read_text(encoding="utf-8-sig")
            try:
                payload = json.loads(text)
                options = payload.get("options") or []
                offered = [str(option.get("option_id", "")) for option in options]
                selected = str(payload.get("selected_option_id") or "").strip()
                question = str(payload.get("question_text") or "")
                answer = str(payload.get("answer_text") or "")
                turn = int(payload.get("turn") or path.parent.name.removeprefix("turn_"))
            except json.JSONDecodeError as exc:
                offered, selected = recover_bif_option_fields(text)
                source_status = "invalid_json_fields_recovered"
                source_error = str(exc)
                question = ""
                answer = ""
                turn = int(path.parent.name.removeprefix("turn_"))

            digest = sha256_file(path)
            rows.append(
                {
                    "condition": "bif_sif",
                    "stage": "BIF",
                    "question_id": qdir.name,
                    "turn": str(turn),
                    "rq1_included": "not_applicable",
                    "selected_option_id": selected,
                    "offered_option_ids": " || ".join(offered),
                    "option_id_status": selection_status(selected, offered),
                    "clarification_question": question,
                    "answer_text": answer,
                    "question_source_path": str(path),
                    "question_source_sha256": digest,
                    "selection_source_path": str(path),
                    "selection_source_sha256": digest,
                    "source_json_status": source_status,
                    "source_json_error": source_error,
                }
            )
    return rows


def nested_user_payload(request: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages") or []
    for message in reversed(messages):
        if message.get("role") == "user":
            return json.loads(str(message.get("content") or "{}"))
    raise ValueError("No user message in request")


def combined_sif_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for qdir in sorted(
        (path for path in COMBINED_ROOT.iterdir() if path.is_dir() and path.name.isdigit()),
        key=lambda path: int(path.name),
    ):
        request_root = qdir / "P03" / "restricted" / "e2_oracle"
        response_root = qdir / "P03" / "agents" / "sif_oracle_simulated_e2"
        for request_path in sorted(request_root.glob("call_*/request.json")):
            call_name = request_path.parent.name
            response_path = response_root / call_name / "response.txt"
            request = nested_user_payload(read_json(request_path))
            response = read_json(response_path)
            options = request.get("options") or []
            offered = [str(option.get("option_id", "")) for option in options]
            selected = str(response.get("selected_option_id") or "").strip()
            rows.append(
                {
                    "condition": "bif_sif",
                    "stage": "SIF",
                    "question_id": qdir.name,
                    "turn": str(int(call_name.removeprefix("call_"))),
                    "rq1_included": "not_applicable",
                    "selected_option_id": selected,
                    "offered_option_ids": " || ".join(offered),
                    "option_id_status": selection_status(
                        selected, offered, {"none_of_the_above"}
                    ),
                    "clarification_question": str(
                        request.get("clarification_question") or ""
                    ),
                    "answer_text": str(response.get("answer_text") or ""),
                    "question_source_path": str(request_path),
                    "question_source_sha256": sha256_file(request_path),
                    "selection_source_path": str(response_path),
                    "selection_source_sha256": sha256_file(response_path),
                    "source_json_status": "valid",
                    "source_json_error": "",
                }
            )
    return rows


def summarize(rows: Iterable[dict[str, str]]) -> dict[str, object]:
    selected_rows = list(rows)
    counts = Counter(row["option_id_status"] for row in selected_rows)
    total = len(selected_rows)
    exception_total = counts["not_offered"] + counts["missing"]
    invalid_json = sum(
        row["source_json_status"] != "valid" for row in selected_rows
    )

    def percent(value: int) -> float:
        return round(100.0 * value / total, 4) if total else 0.0

    return {
        "turns": total,
        "queries": len({row["question_id"] for row in selected_rows}),
        "offered": counts["offered"],
        "supported_fallback": counts["supported_fallback"],
        "not_offered": counts["not_offered"],
        "missing": counts["missing"],
        "not_offered_percent": percent(counts["not_offered"]),
        "missing_percent": percent(counts["missing"]),
        "option_id_exception_total": exception_total,
        "option_id_exception_percent": percent(exception_total),
        "invalid_source_json_with_ids_recovered": invalid_json,
    }


def require_expected(actual: dict[str, object], expected: dict[str, int], name: str) -> None:
    for field, value in expected.items():
        if actual.get(field) != value:
            raise ValueError(
                f"Unexpected {name} {field}: {actual.get(field)!r} != {value!r}"
            )


def verify_source_provenance(rows: list[dict[str, str]]) -> int:
    expected_by_path: dict[Path, str] = {}
    for row in rows:
        for prefix in ("question", "selection"):
            path = Path(row[f"{prefix}_source_path"])
            expected_hash = row[f"{prefix}_source_sha256"].lower()
            prior = expected_by_path.setdefault(path, expected_hash)
            if prior != expected_hash:
                raise ValueError(f"Conflicting recorded hashes for {path}")

    for path, expected_hash in expected_by_path.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        actual_hash = sha256_file(path)
        if actual_hash.lower() != expected_hash:
            raise ValueError(
                f"Source hash mismatch for {path}: {actual_hash} != {expected_hash}"
            )
    return len(expected_by_path)


def main() -> None:
    rows = normalized_rows() + combined_bif_rows() + combined_sif_rows()
    rows.sort(
        key=lambda row: (
            row["condition"],
            row["stage"],
            int(row["question_id"]),
            int(row["turn"]),
        )
    )
    keys = [
        (row["condition"], row["stage"], row["question_id"], row["turn"])
        for row in rows
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate condition/stage/qid/turn key")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["condition"], row["stage"])].append(row)
    group_summary = {
        f"{condition}:{stage}": summarize(group)
        for (condition, stage), group in sorted(grouped.items())
    }
    sif_included = [
        row
        for row in rows
        if row["condition"] == "sif_only"
        and row["stage"] == "SIF"
        and row["rq1_included"] == "1"
    ]
    exceptional_values: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["option_id_status"] in {"not_offered", "missing"}:
            key = f'{row["condition"]}:{row["stage"]}'
            exceptional_values[key][row["selected_option_id"] or "<missing>"] += 1

    if len(rows) != 11860:
        raise ValueError(f"Unexpected total row count: {len(rows)}")
    for name, expected in EXPECTED.items():
        require_expected(group_summary[name], expected, name)
    sif_included_summary = summarize(sif_included)
    require_expected(
        sif_included_summary, EXPECTED_SIF_RQ1_INCLUDED, "sif_only_rq1_included"
    )
    verified_source_files = verify_source_provenance(rows)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis": "Clarification selected option ID membership in offered options",
        "interpretation_note": (
            "RQ1 decision-type coding did not use selected options or answers; "
            "these findings concern implementation and validity, not the RQ1 labels."
        ),
        "input": {
            "rq1_normalized_path": str(RQ1_INPUT),
            "rq1_normalized_sha256": sha256_file(RQ1_INPUT),
            "combined_run_root": str(COMBINED_ROOT),
        },
        "status_definition": {
            "offered": "selected_option_id exactly matches a displayed option_id",
            "supported_fallback": (
                "selected_option_id is not displayed but is explicitly allowed by the "
                "combined SIF prompt (none_of_the_above)"
            ),
            "not_offered": (
                "selected_option_id is non-empty, is not displayed, and is not an "
                "explicitly supported fallback"
            ),
            "missing": "selected_option_id is empty",
        },
        "by_condition_stage": group_summary,
        "sif_only_rq1_included": sif_included_summary,
        "exceptional_selected_values": {
            key: dict(sorted(values.items()))
            for key, values in sorted(exceptional_values.items())
        },
        "output": {
            "by_turn_csv": str(OUTPUT_CSV),
            "rows": len(rows),
            "sha256": sha256_file(OUTPUT_CSV),
        },
        "validation": {
            "expected_counts_pass": True,
            "unique_condition_stage_qid_turn_pass": True,
            "source_path_and_sha256_pass": True,
            "unique_source_files_verified": verified_source_files,
        },
    }
    with OUTPUT_JSON.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
