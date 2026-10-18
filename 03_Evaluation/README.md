# Evaluation Protocols, Engines, and Official Execution Accuracy Results

## 1. Overview
This directory contains the **evaluation engines, validation scripts, protocol documentation, and query-level execution accuracy outcomes** for all three experimental conditions evaluated across the 1,534 queries of the BIRD development benchmark:
- **BIF Only**: Business Intent Framing only
- **SIF Only**: SQL Intent Framing only
- **BIF-SIF**: Proposed two-stage staged clarification framework

All evaluation outcomes in this directory adhere strictly to the **Official BIRD Execution Accuracy (Official EX)** standard.

---

## 2. Directory Structure

```
03_Evaluation/
│
├── README.md                                    # This documentation
│
├── evaluation_source/                           # Evaluation engines, audits, and protocols
│   ├── recompute_bird_official_ex.py            # Primary Official BIRD EX recomputation engine
│   ├── audit_combined_archived_official_ex_crosswalk.py # Crosswalk audit between archived and recomputed EX
│   ├── validate_chapter5_evidence.py            # Comprehensive 1,534-query census integrity validator
│   ├── extract_process_inputs.py                # Input extraction and query normalization pipeline
│   └── EVALUATION_PROTOCOL_AND_AUDIT.md         # Integrated protocol specification & serial audit
│
└── evaluation_results/                          # Recomputed outcome datasets and paired tables
    ├── official_bird_ex_recomputed.csv          # 4,602-row master census dataset (3 conditions x 1,534 queries)
    ├── official_bird_ex_recomputed.json         # Summary metadata, counts, and SHA-256 file hashes
    ├── official_bird_ex_paired_wide.csv         # 1,534-row 1:1:1 paired comparison wide table
    ├── bif_per_query_results.csv                # Query-level outcomes for BIF-Only (N=1,534)
    ├── sif_per_query_results.csv                # Query-level outcomes for SIF-Only (N=1,534)
    ├── bif_sif_per_query_results.csv            # Query-level outcomes for BIF-SIF (N=1,534)
    ├── combined_archived_vs_recomputed_official_ex_crosswalk.csv # Crosswalk mapping table
    ├── combined_archived_vs_recomputed_official_ex_summary.json  # Summary statistics of the crosswalk
    ├── official_bird_ex_serial_rechecks.json    # Serial re-check audit for 13 non-OK queries
    ├── official_bird_ex_provenance_addendum.json# Operational provenance metadata (e.g., worker concurrency)
    ├── evaluator_diagnostic_comparison.csv       # Comparison between internal evaluator and Official EX
    └── query_level_evaluator_flags.csv          # Query-level diagnostic flags and mismatch traces
```

---

## 3. Official Evaluation Standard & Rules

### 3.1 Primary Execution-Equivalence Rule
Execution accuracy strictly follows the official BIRD benchmark evaluation standard:
```python
set(predicted_rows) == set(gold_rows)
```
- **Database Engine**: SQLite with read-only connection (`PRAGMA query_only = ON`).
- **Row & Multiplicity Semantics**: Python `set` comparison ignores row ordering and duplicate multiplicity.
- **Column Order & Typing**: Tuple element ordering and Python equality semantics (e.g., `1 == 1.0` evaluates to `True`) are preserved.
- **Failures & Timeouts**: Syntax errors, runtime exceptions, missing predictions, and timeouts exceeding 30 seconds are assigned a score of `0`.

### 3.2 Census Summary (N=1,534 per condition)

| Experimental Condition | Correct Queries | Total Queries (N) | Official EX Accuracy (%) |
| :--- | :---: | :---: | :---: |
| **BIF Only** | 916 | 1,534 | **59.71%** |
| **SIF Only** | 861 | 1,534 | **56.13%** |
| **BIF-SIF (Proposed)** | 1,008 | 1,534 | **65.71%** |

---

## 4. Evaluation Source Code & Protocols (`evaluation_source/`)

### 4.1 Python Scripts
- **`recompute_bird_official_ex.py`**:
  - The core evaluation engine that executes preserved final SQL queries against SQLite databases under the official BIRD set-equality rule.
  - Features parallel workers (`--workers 8`), a strict 30-second progress-handler timeout, and per-query status logging (`ok`, `execution_error`, `timeout`, `missing_prediction`).
- **`audit_combined_archived_official_ex_crosswalk.py`**:
  - Performs item-by-item crosswalk validation between legacy runtime summary records (which had 618 correct cases due to an artifact logging anomaly where `gen_row_count == -1`) and the recomputed census (1,008 correct cases).
  - Validates that the 1,008 correct queries are authentic executions on the identical final SQL queries.
- **`validate_chapter5_evidence.py`**:
  - Validates the 100% census completeness, schema constraints, absence of nulls/duplicates, and SHA-256 cryptographic hashes across all evaluation and analysis artifacts.
- **`extract_process_inputs.py`**:
  - Normalizes query metadata, clarification turns, model invocation logs, and execution traces from the raw run directories into structured analysis-ready inputs.

### 4.2 Documentation
- **`EVALUATION_PROTOCOL_AND_AUDIT.md`**:
  - The comprehensive specification integrating the official evaluation methodology, SQLite execution parameters, metric definitions, and the **serial independent audit** of 13 non-OK queries to verify concurrency isolation.

---

## 5. Evaluation Results & Datasets (`evaluation_results/`)

### 5.1 Primary Datasets
- **`official_bird_ex_recomputed.csv`**:
  - Master dataset containing 4,602 rows (1,534 queries $\times$ 3 conditions).
  - **Key Columns**:
    - `condition`: Experimental condition (`bif_only`, `sif_only`, `bif_sif`)
    - `question_id`: BIRD query index (0 to 1,533)
    - `db_id`: Target SQLite database identifier
    - `difficulty`: BIRD difficulty classification (`simple`, `moderate`, `challenging`)
    - `official_ex`: Binary indicator (`1` = correct, `0` = incorrect)
    - `status`: Execution status (`ok`, `execution_error`, `timeout`, `missing_prediction`)
    - `predicted_row_count` / `gold_row_count`: Number of tuples returned
    - `elapsed_seconds`: Execution duration in seconds
    - `generated_sql`: The exact final synthesized SQL query evaluated
    - `gold_sql`: The official ground-truth SQL query
- **`official_bird_ex_paired_wide.csv`**:
  - Wide-format paired comparison dataset (1,534 rows) aligning `bif_only_ex`, `sif_only_ex`, and `bif_sif_ex` side-by-side for each `question_id`, enabling paired statistical tests (McNemar, Cochran's Q).
- **`bif_per_query_results.csv`, `sif_per_query_results.csv`, `bif_sif_per_query_results.csv`**:
  - Condition-specific per-query execution and diagnostic summaries.

### 5.2 Provenance & Audit Datasets
- **`combined_archived_vs_recomputed_official_ex_crosswalk.csv`**:
  - Itemized mapping of all 1,534 BIF-SIF queries comparing the archived summary flags with recomputed official execution results.
- **`official_bird_ex_serial_rechecks.json`**:
  - Records single-threaded sequential re-executions (workers=1, 30s timeout) for the 13 non-OK queries (timeouts/errors) to confirm that results were not compromised by parallel resource contention.
- **`evaluator_diagnostic_comparison.csv` & `query_level_evaluator_flags.csv`**:
  - Detailed diagnostic comparison between the internal runtime agent evaluator (which enforced strict ordering/exact casing) and the official BIRD benchmark set-equality evaluator.

---

## 6. Reproduction Guide

To re-run the official execution accuracy evaluation from source:

```bash
# Navigate to the evaluation source directory
cd evaluation_source

# Recompute Official EX for all 4,602 cases (requires BIRD SQLite databases)
python recompute_bird_official_ex.py

# Run the crosswalk audit between archived summaries and recomputed EX
python audit_combined_archived_official_ex_crosswalk.py

# Run full census integrity validation
python validate_chapter5_evidence.py
```
