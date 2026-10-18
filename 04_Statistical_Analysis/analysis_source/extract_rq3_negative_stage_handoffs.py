"""Extract BIF-to-SIF handoff evidence for all 170 BIF-SIF errors in RQ3.

The output is an analysis-ready snapshot of the preserved sequence used for
stage review: confirmed BIF, the initial schema-grounded SIF, confirmed SIF
after SIF clarification, the first synthesized SQL, the retained final SQL,
and Gold SQL.  It contains source paths and hashes so every field can be
traced to the archived run.  Extraction itself does not label a failure stage.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[3]
RUN_ROOT = (
    WORKSPACE
    / "revised_final_bif_sif"
    / "2.bif_sif"
    / "runs"
    / "2.bif_sif_full_1534_20260823_012700"
)
REVIEW_PATH = HERE / "outputs" / "rq3_census_review_final.csv"
OUTPUT_PATH = HERE / "inputs" / "rq3_negative_stage_handoff_bundle.jsonl"
MANIFEST_PATH = HERE / "inputs" / "rq3_negative_stage_handoff_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE).as_posix()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def source(path: Path) -> dict[str, str]:
    return {"path": relative(path), "sha256": sha256(path)}


def first_successful_response(agent_root: Path) -> Path:
    """Return the first non-empty assembler response after any failed retries."""
    candidates = sorted(
        agent_root.glob("call_*/response.txt"),
        key=lambda path: int(path.parent.name.split("_")[-1]),
    )
    for path in candidates:
        if path.stat().st_size > 0:
            return path
    raise ValueError(f"No non-empty assembler response found under {agent_root}")


def main() -> None:
    rows = [
        row
        for row in read_csv(REVIEW_PATH)
        if row["direction"] == "combined_incorrect"
    ]
    if len(rows) != 170 or len({int(row["question_id"]) for row in rows}) != 170:
        raise ValueError("Expected 170 unique BIF-SIF-incorrect review rows")

    records: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item["question_id"])):
        qid = int(row["question_id"])
        case_root = RUN_ROOT / str(qid)
        bif_path = case_root / "P01" / "frames" / "semantic_frontend_result.json"
        initial_sif_path = first_successful_response(
            case_root / "P03" / "agents" / "sif_13_contract_assembler"
        )
        confirmed_sif_path = case_root / "P03" / "frames" / "confirmed_sif.json"
        linking_path = case_root / "P04" / "frames" / "cqd_sql_final_linking.json"
        gold_path = case_root / "P09" / "sql" / "gold.sql"

        bif = read_json(bif_path)
        confirmed_sif = read_json(confirmed_sif_path)
        linking = read_json(linking_path)
        initial_sif_raw = initial_sif_path.read_text(encoding="utf-8-sig")
        gold_sql = gold_path.read_text(encoding="utf-8-sig")
        paths = {
            "confirmed_bif": source(bif_path),
            "initial_sif": source(initial_sif_path),
            "confirmed_sif": source(confirmed_sif_path),
            "sql_linking": source(linking_path),
            "gold_sql": source(gold_path),
        }
        record = {
            "question_id": qid,
            "pattern": str(row["pattern"]),
            "db_id": str(row["db_id"]),
            "difficulty": str(row["difficulty"]),
            "question": str(row["question"]),
            "clarification_link": str(row["clarification_link"]),
            "observed_sql_difference": str(row["concise_evidence_note_ko"]),
            "confirmed_bif": {
                "rendered_text": str(
                    (bif.get("confirmed_cqd") or {}).get("rendered_text") or ""
                ),
                "decision_ledger": bif.get("decision_ledger") or [],
            },
            "initial_sif_raw": initial_sif_raw,
            "confirmed_sif": {
                "contracts": confirmed_sif.get("contracts") or {},
                "clauses": confirmed_sif.get("clauses") or {},
                "active_inspection_decisions": confirmed_sif.get(
                    "active_inspection_decisions"
                )
                or [],
            },
            "synthesized_sql": str(linking.get("synthesized_sql") or ""),
            "final_sql": str(linking.get("final_sql") or ""),
            "preflight_status": str(linking.get("preflight_status") or ""),
            "gold_sql": gold_sql,
            "sources": paths,
        }
        records.append(record)
        source_records.append({"question_id": qid, **paths})

    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    checks = {
        "records_are_170": len(records) == 170,
        "question_ids_are_unique": len({row["question_id"] for row in records}) == 170,
        "patterns_are_combined_incorrect": {
            row["pattern"] for row in records
        }
        <= {"010", "100", "110"},
        "all_stage_fields_present": all(
            row["confirmed_bif"]["rendered_text"]
            and row["initial_sif_raw"]
            and row["confirmed_sif"]["contracts"]
            and row["synthesized_sql"]
            and row["final_sql"]
            and row["gold_sql"]
            for row in records
        ),
        "no_stage_attribution_added_by_extraction": True,
    }
    if not all(checks.values()):
        raise ValueError(f"Stage-handoff extraction failed: {checks}")

    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "scope": "All 170 RQ3 queries where BIF-SIF was incorrect and another system was correct",
                "sequence": [
                    "confirmed BIF",
                    "initial schema-grounded SIF",
                    "confirmed SIF after clarification",
                    "first synthesized SQL",
                    "retained final SQL",
                    "Gold SQL",
                ],
                "interpretation": (
                    "Source extraction only; stage labels require content review and are "
                    "descriptive rather than causal"
                ),
                "checks": checks,
                "source_review": {
                    "path": str(REVIEW_PATH.relative_to(HERE).as_posix()),
                    "sha256": sha256(REVIEW_PATH),
                },
                "output": {
                    "path": str(OUTPUT_PATH.relative_to(HERE).as_posix()),
                    "sha256": sha256(OUTPUT_PATH),
                },
                "source_files": source_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"checks": checks, "output": str(OUTPUT_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
