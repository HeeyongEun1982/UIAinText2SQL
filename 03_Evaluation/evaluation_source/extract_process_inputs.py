"""Extract normalized Chapter 5 process-analysis inputs from archived runs.

The script never changes the archived runs.  It creates query-, clarification-
turn-, and model-call-level tables under ``5_evidence_data/inputs`` while
retaining each source path and SHA-256 digest.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
PAPER_DIR = HERE.parent
ROOT = PAPER_DIR.parents[2]
RUN_BASE = ROOT / "revised_final_bif_sif"
INPUT_DIR = HERE / "inputs"
OUTPUT_DIR = HERE / "outputs"

DATASET = RUN_BASE / "0.bif" / "data" / "bird_dev.json"
BIF_ROOTS = [
    RUN_BASE / "0.bif" / "runs" / "0.bif_full_1534_20260821_2201",
    RUN_BASE / "0.bif" / "runs" / "0.bif_full_1534_20260821_2201_",
]
SIF_ROOT = RUN_BASE / "1.sif" / "runs" / "1.sif_full_1534_20260831_215201"
COMBINED_ROOT = (
    RUN_BASE / "2.bif_sif" / "runs" / "2.bif_sif_full_1534_20260823_012700"
)

# The operator identified qids 1--10 in the SIF-only process trace as test-
# contaminated.  They remain in the 1,534-query Official EX analysis required
# by Chapter 3, but their SIF-only questions are excluded from RQ1 coding.
SIF_PROCESS_EXCLUSIONS = set(range(1, 11))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def flatten_options(options: Any) -> str:
    if not isinstance(options, list):
        return ""
    parts: list[str] = []
    for option in options:
        if isinstance(option, dict):
            option_id = str(option.get("option_id") or "").strip()
            description = str(option.get("description") or option.get("text") or "").strip()
            parts.append(f"{option_id}: {description}" if option_id else description)
        else:
            parts.append(str(option))
    return " || ".join(part for part in parts if part)


def load_dataset() -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    raw = read_json(DATASET)
    records = raw if isinstance(raw, list) else raw.get("data", raw.get("examples", []))
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        qid = int(item.get("question_id", index))
        normalized.append(
            {
                "question_id": qid,
                "db_id": str(item.get("db_id") or ""),
                "difficulty": str(item.get("difficulty") or ""),
                "question": str(item.get("question") or ""),
                "evidence": str(item.get("evidence") or ""),
                "ground_truth_sql": str(item.get("SQL") or item.get("sql") or ""),
            }
        )
    normalized.sort(key=lambda row: row["question_id"])
    if [row["question_id"] for row in normalized] != list(range(1534)):
        raise RuntimeError("Dataset qids are not the complete range 0--1533")
    return normalized, {row["question_id"]: row for row in normalized}


def collect_bif_turn_paths() -> dict[tuple[int, int], Path]:
    found: dict[tuple[int, int], Path] = {}
    for root in BIF_ROOTS:
        for path in root.glob(
            "*/P01/semantic_frontend/05_clarification/turn_*/turn_record.json"
        ):
            try:
                qid = int(path.relative_to(root).parts[0])
                turn = int(path.parent.name.split("_")[-1])
            except (ValueError, IndexError):
                continue
            found.setdefault((qid, turn), path)
    return found


def collect_sif_conflicts(qid: int) -> list[dict[str, Any]]:
    path = SIF_ROOT / str(qid) / "P01" / "frames" / "direct_sif_initial_grounding.json"
    if not path.is_file():
        return []
    payload = read_json(path)
    conflicts = payload.get("schema_conflicts")
    if not isinstance(conflicts, list):
        conflicts = (payload.get("sif_grounding") or {}).get("schema_conflicts", [])
    return [item for item in conflicts if isinstance(item, dict)]


def build_rq1_turns(dataset: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for (qid, turn), path in sorted(collect_bif_turn_paths().items()):
        payload = read_json(path)
        item = dataset[qid]
        options = payload.get("options") if isinstance(payload.get("options"), list) else []
        rows.append(
            {
                "architecture": "bif_only",
                "question_id": qid,
                "db_id": item["db_id"],
                "difficulty": item["difficulty"],
                "turn": int(payload.get("turn") or turn),
                "rq1_included": 1,
                "exclusion_reason": "",
                "original_question": item["question"],
                "evidence": item["evidence"],
                "decision_context": str(payload.get("ambiguity") or ""),
                "attribute_name": "",
                "candidate_columns": "",
                "clarification_question": str(payload.get("question_text") or ""),
                "options_json": json.dumps(options, ensure_ascii=False),
                "options_text": flatten_options(options),
                "selected_option_id": str(payload.get("selected_option_id") or ""),
                "answer_text": str(payload.get("answer_text") or ""),
                "technical_reason": "",
                "ground_truth_analysis": "",
                "source_path": str(path),
                "source_sha256": sha256(path),
            }
        )

    for path in sorted(
        SIF_ROOT.glob("*/P01/frames/sif_clarification/turn_*/turn_record.json")
    ):
        qid = int(path.relative_to(SIF_ROOT).parts[0])
        payload = read_json(path)
        turn = int(payload.get("turn") or path.parent.name.split("_")[-1])
        conflicts = collect_sif_conflicts(qid)
        conflict = conflicts[turn - 1] if 0 < turn <= len(conflicts) else {}
        item = dataset[qid]
        options = payload.get("options") if isinstance(payload.get("options"), list) else []
        excluded = qid in SIF_PROCESS_EXCLUSIONS
        rows.append(
            {
                "architecture": "sif_only",
                "question_id": qid,
                "db_id": item["db_id"],
                "difficulty": item["difficulty"],
                "turn": turn,
                "rq1_included": 0 if excluded else 1,
                "exclusion_reason": "SIF qids 1--10: test-contaminated process trace" if excluded else "",
                "original_question": item["question"],
                "evidence": item["evidence"],
                "decision_context": str(conflict.get("conflict_description") or ""),
                "attribute_name": str(conflict.get("attribute_name") or ""),
                "candidate_columns": " || ".join(
                    str(value) for value in (conflict.get("candidate_columns") or [])
                ),
                "clarification_question": str(payload.get("question_text") or ""),
                "options_json": json.dumps(options, ensure_ascii=False),
                "options_text": flatten_options(options),
                "selected_option_id": str(payload.get("selected_option_id") or ""),
                "answer_text": str(payload.get("answer_text") or ""),
                "technical_reason": str(payload.get("technical_reason") or ""),
                "ground_truth_analysis": str(payload.get("gold_sql_analysis") or ""),
                "source_path": str(path),
                "source_sha256": sha256(path),
            }
        )

    rows.sort(key=lambda row: (row["architecture"], row["question_id"], row["turn"]))
    bif_rows = [row for row in rows if row["architecture"] == "bif_only"]
    sif_rows = [row for row in rows if row["architecture"] == "sif_only"]
    included_sif = [row for row in sif_rows if row["rq1_included"] == 1]
    all_conflicts = sum(len(collect_sif_conflicts(qid)) for qid in dataset)
    included_conflicts = sum(
        len(collect_sif_conflicts(qid))
        for qid in dataset
        if qid not in SIF_PROCESS_EXCLUSIONS
    )
    summary = {
        "bif": {
            "turns": len(bif_rows),
            "queries": len({row["question_id"] for row in bif_rows}),
        },
        "sif_all_archived": {
            "elicited_turns": len(sif_rows),
            "queries": len({row["question_id"] for row in sif_rows}),
            "detected_conflicts": all_conflicts,
        },
        "sif_rq1_after_exclusion": {
            "excluded_qids": sorted(SIF_PROCESS_EXCLUSIONS),
            "elicited_turns": len(included_sif),
            "queries": len({row["question_id"] for row in included_sif}),
            "detected_conflicts": included_conflicts,
            "detected_but_not_elicited": included_conflicts - len(included_sif),
        },
    }
    return rows, summary


def metadata_rows(condition: str, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in root.glob("*/P*/agents/*/call_*/metadata.json"):
        try:
            qid = int(path.relative_to(root).parts[0])
        except (ValueError, IndexError):
            continue
        payload = read_json(path)
        rel = path.relative_to(root).parts
        rows.append(
            {
                "condition": condition,
                "question_id": qid,
                "stage": rel[1],
                "agent": str(payload.get("agent_id") or rel[3]),
                "attempt": payload.get("attempt"),
                "model_alias": payload.get("model_alias"),
                "actual_model": payload.get("actual_model"),
                "finish_reason": payload.get("finish_reason"),
                "latency_ms": payload.get("latency_ms"),
                "prompt_tokens": payload.get("prompt_tokens"),
                "completion_tokens": payload.get("completion_tokens"),
                "total_tokens": payload.get("total_tokens"),
                "source_path": str(path),
                "source_sha256": sha256(path),
            }
        )
    rows.sort(key=lambda row: (row["condition"], row["question_id"], row["stage"], row["agent"], row["attempt"] or 0))
    return rows


def bif_metadata_rows() -> list[dict[str, Any]]:
    """Reconstruct BIF-only model-call metadata across the split run roots.

    The archived batch resumes in the underscore-suffixed root for qids
    788--798.  For qid 788, the primary root contains a stale P01 intent
    analysis from the interrupted attempt, while the resumed P01 calls are in
    the supplemental root and the final P04 synthesizer is in the primary
    root.  Those complementary records are combined here.  One expected qid
    778 call has no preserved metadata file; the batch summary remains the
    authoritative source for total call counts.
    """
    primary = metadata_rows("bif_only", BIF_ROOTS[0])
    primary = [
        row
        for row in primary
        if not (row["question_id"] == 788 and row["stage"] == "P01")
    ]
    supplemental = metadata_rows("bif_only", BIF_ROOTS[1])
    combined = primary + supplemental
    combined.sort(
        key=lambda row: (
            row["condition"],
            row["question_id"],
            row["stage"],
            row["agent"],
            row["attempt"] or 0,
        )
    )
    return combined


def count_paths(root: Path, qid: int, pattern: str) -> int:
    return sum(1 for _ in (root / str(qid)).glob(pattern))


def combined_sif_turns(qid: int) -> int:
    path = COMBINED_ROOT / str(qid) / "P03" / "frames" / "confirmed_sif.json"
    if not path.is_file():
        return 0
    payload = read_json(path)
    decisions = payload.get("active_inspection_decisions") or []
    return len(decisions) if isinstance(decisions, list) else 0


def build_operational_query_metrics(
    dataset: dict[int, dict[str, Any]], calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in calls:
        grouped.setdefault((row["condition"], row["question_id"]), []).append(row)

    roots = {
        "bif_only": BIF_ROOTS[0],
        "sif_only": SIF_ROOT,
        "bif_sif": COMBINED_ROOT,
    }
    output: list[dict[str, Any]] = []
    for condition, root in roots.items():
        for qid, item in dataset.items():
            case_calls = grouped.get((condition, qid), [])
            bif_turns = 0
            sif_turns = 0
            if condition == "bif_only":
                bif_turns = count_paths(
                    root,
                    qid,
                    "P01/semantic_frontend/05_clarification/turn_*/turn_record.json",
                )
                if qid == 788 and bif_turns == 0:
                    bif_turns = count_paths(
                        BIF_ROOTS[1],
                        qid,
                        "P01/semantic_frontend/05_clarification/turn_*/turn_record.json",
                    )
            elif condition == "sif_only":
                sif_turns = count_paths(
                    root, qid, "P01/frames/sif_clarification/turn_*/turn_record.json"
                )
            else:
                bif_turns = count_paths(
                    root,
                    qid,
                    "P01/semantic_frontend/05_clarification/turn_*/turn_record.json",
                )
                sif_turns = combined_sif_turns(qid)

            def total(field: str) -> float:
                values = [row.get(field) for row in case_calls if row.get(field) is not None]
                return float(sum(float(value) for value in values)) if values else 0.0

            output.append(
                {
                    "condition": condition,
                    "question_id": qid,
                    "db_id": item["db_id"],
                    "difficulty": item["difficulty"],
                    "model_call_count": len(case_calls),
                    "prompt_tokens": int(total("prompt_tokens")),
                    "completion_tokens": int(total("completion_tokens")),
                    "total_tokens": int(total("total_tokens")),
                    "summed_model_latency_ms": round(total("latency_ms"), 3),
                    "bif_clarification_turns": bif_turns,
                    "sif_clarification_interactions": sif_turns,
                    "process_trace_excluded": int(
                        condition == "sif_only" and qid in SIF_PROCESS_EXCLUSIONS
                    ),
                }
            )
    output.sort(key=lambda row: (row["condition"], row["question_id"]))
    return output


def snapshot_documents() -> dict[str, Any]:
    sources = {
        "methodology_pdf": PAPER_DIR / "User-Intent Alignment in Text-to-SQL through Staged Clarification_v0.3.pdf",
        "results_guide": PAPER_DIR / "5.result_작성가이드.md",
        "dataset": DATASET,
    }
    manifest: dict[str, Any] = {}
    for name, source in sources.items():
        target = INPUT_DIR / source.name
        shutil.copy2(source, target)
        manifest[name] = {
            "source_path": str(source),
            "snapshot_path": str(target),
            "sha256": sha256(target),
            "bytes": target.stat().st_size,
        }
    return manifest


def main() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset_rows, dataset = load_dataset()
    query_fields = [
        "question_id",
        "db_id",
        "difficulty",
        "question",
        "evidence",
        "ground_truth_sql",
    ]
    write_csv(INPUT_DIR / "bird_dev_queries.csv", dataset_rows, query_fields)

    turns, turn_summary = build_rq1_turns(dataset)
    full_fields = [
        "architecture",
        "question_id",
        "db_id",
        "difficulty",
        "turn",
        "rq1_included",
        "exclusion_reason",
        "original_question",
        "evidence",
        "decision_context",
        "attribute_name",
        "candidate_columns",
        "clarification_question",
        "options_json",
        "options_text",
        "selected_option_id",
        "answer_text",
        "technical_reason",
        "ground_truth_analysis",
        "source_path",
        "source_sha256",
    ]
    write_csv(INPUT_DIR / "rq1_turns_full.csv", turns, full_fields)
    blinded_fields = [
        field
        for field in full_fields
        if field
        not in {
            "selected_option_id",
            "answer_text",
            "technical_reason",
            "ground_truth_analysis",
        }
    ]
    write_csv(INPUT_DIR / "rq1_turns_blinded.csv", turns, blinded_fields)
    write_json(OUTPUT_DIR / "rq1_corpus_integrity.json", turn_summary)

    calls: list[dict[str, Any]] = []
    calls.extend(bif_metadata_rows())
    calls.extend(metadata_rows("sif_only", SIF_ROOT))
    calls.extend(metadata_rows("bif_sif", COMBINED_ROOT))
    call_fields = [
        "condition",
        "question_id",
        "stage",
        "agent",
        "attempt",
        "model_alias",
        "actual_model",
        "finish_reason",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "source_path",
        "source_sha256",
    ]
    write_csv(INPUT_DIR / "model_calls.csv", calls, call_fields)

    operational = build_operational_query_metrics(dataset, calls)
    operational_fields = [
        "condition",
        "question_id",
        "db_id",
        "difficulty",
        "model_call_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "summed_model_latency_ms",
        "bif_clarification_turns",
        "sif_clarification_interactions",
        "process_trace_excluded",
    ]
    write_csv(INPUT_DIR / "operational_query_metrics.csv", operational, operational_fields)

    manifest = {
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256(Path(__file__).resolve()),
        "document_snapshots": snapshot_documents(),
        "normalized_inputs": {
            name: {
                "sha256": sha256(INPUT_DIR / name),
                "bytes": (INPUT_DIR / name).stat().st_size,
            }
            for name in [
                "bird_dev_queries.csv",
                "rq1_turns_full.csv",
                "rq1_turns_blinded.csv",
                "model_calls.csv",
                "operational_query_metrics.csv",
            ]
        },
        "rq1_process_exclusion": {
            "condition": "sif_only",
            "question_ids": sorted(SIF_PROCESS_EXCLUSIONS),
            "scope": "RQ1 SIF-only clarification coding only; Official EX retains N=1534",
            "reason": "Operator-identified test contamination in qids 1--10",
        },
    }
    write_json(INPUT_DIR / "process_input_manifest.json", manifest)
    print(json.dumps(turn_summary, ensure_ascii=False, indent=2))
    print(f"model calls: {len(calls)}")
    print(f"operational rows: {len(operational)}")


if __name__ == "__main__":
    main()
