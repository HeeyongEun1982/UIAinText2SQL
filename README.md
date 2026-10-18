# Supporting Evidence Package

## 1. Overview & Context
This repository contains the complete **Supporting Evidence Package** for the Master's dissertation:

> **"User-Intent Alignment in Text-to-SQL through Staged Clarification"**  
> *Department of Computer Science, University of York*

This package provides the complete source code, 1,534-query raw execution logs, official evaluation engines, and statistical analysis pipelines across three experimental conditions:
1. **BIF-Only (Business Intent Framing)**: Single-stage clarification resolving business logic and entity scopes before database grounding.
2. **SIF-Only (SQL Intent Framing)**: Single-stage clarification resolving structural schema linking, join paths, and SQL operations.
3. **BIF-SIF (Staged Intent Framing)**: The proposed two-layer framework that stages business clarification (Layer 1) and hands off confirmed definitions to SQL intent framing (Layer 2).

All experiments were conducted over the entire **1,534 queries of the BIRD development benchmark** under a strict **Zero Question-Specific Evidence** policy.

---

## 2. Master Directory Architecture

```
Supporting_Evidence/
│
├── README.md                            # Master repository guide (this document)
│
├── 01_Implementation/                  # Pure source code for all three agent implementations
│   ├── README.md                        # Implementation overview and comparison
│   ├── BIF/                             # BIF-Only agent implementation & README.md
│   ├── SIF/                             # SIF-Only agent implementation & README.md
│   └── BIF_SIF/                         # BIF-SIF proposed staged agent implementation & README.md
│
├── 02_Experiment_Logs/                  # Complete, immutable raw execution logs (N=1,534 per condition)
│   ├── BIF/                             # 1,534 query run folders (0/ to 1533/) + batch_summary (.csv/.json)
│   ├── SIF/                             # 1,534 query run folders (0/ to 1533/) + batch_summary (.csv/.json)
│   └── BIF_SIF/                         # 1,534 query run folders (0/ to 1533/) + batch_summary (.csv/.json)
│
├── 03_Evaluation/                       # Official BIRD EX re-evaluation engines, protocols & results
│   ├── README.md                        # Evaluation documentation and dataset schema
│   ├── evaluation_source/               # Re-evaluation engine, crosswalk audit, and protocol document
│   └── evaluation_results/              # 4,602-row census master dataset, paired wide table, and audits
│
└── 04_Statistical_Analysis/             # Statistical hypothesis tests, empirical analysis & results
    ├── README.md                        # Statistical analysis documentation and RQ alignment
    ├── analysis_source/                 # Python statistical pipelines (RQ1, RQ2, RQ3)
    └── analysis_results/                # Overall accuracy, clarification coding, and handoff datasets
```

---

## 3. Benchmark Summary & Key Empirical Findings

### 3.1 Census Performance ($N = 1,534$ per condition)

| Condition | Description | Correct Queries | Total (N) | Official EX Accuracy | 95% Wilson CI |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **BIF-Only** | Layer 1 Business Clarification only | 916 | 1,534 | **59.71%** | [57.24%, 62.15%] |
| **SIF-Only** | Layer 2 SQL Intent Clarification only | 861 | 1,534 | **56.13%** | [53.63%, 58.60%] |
| **BIF-SIF** | **Proposed Two-Stage Framework** | **1,008** | **1,534** | **65.71%** | **[63.30%, 68.06%]** |

### 3.2 Statistical Significance
- **Omnibus Test**: Cochran's $Q = 104.57$ ($df = 2, p < 10^{-22}$), confirming highly statistically significant performance differences among the three architectures.
- **Pairwise Exact McNemar Tests (Holm-corrected)**:
  - **BIF-SIF vs. BIF-Only**: $+6.00\%$ absolute gain ($p = 0.00030$)
  - **BIF-SIF vs. SIF-Only**: $+9.58\%$ absolute gain ($p = 5.25 \times 10^{-12}$)
  - **BIF-Only vs. SIF-Only**: $+3.59\%$ absolute gain ($p = 0.0163$)

---

## 4. Subdirectory Guide

### 4.1 `01_Implementation/`
Contains the modular, self-contained Python source code for each agent pipeline:
- Each folder (`BIF/`, `SIF/`, `BIF_SIF/`) includes the `intent_sql_agent` package, CLI runner (`run.py`), formal JSON schemas (`spec/`), and data definitions (`data/`).
- Includes zero temporary files or caches, serving as an immutable codebase archive.
- Detailed execution instructions are provided in [`01_Implementation/BIF/README.md`](Supporting_Evidence/01_Implementation/BIF/README.md), [`01_Implementation/SIF/README.md`](Supporting_Evidence/01_Implementation/SIF/README.md), and [`01_Implementation/BIF_SIF/README.md`](Supporting_Evidence/01_Implementation/BIF_SIF/README.md).

### 4.2 `02_Experiment_Logs/`
Contains the complete, raw interaction and execution artifacts for all 1,534 queries across all three conditions:
- **Query Folders (`0/` through `1533/`)**: Each query folder contains input snapshots, LLM prompt requests/responses, generated clarification dialogues, intermediate intent frames, preflight logs, and final synthesized SQL.
- **Batch Summaries**: `batch_summary.csv` and `batch_summary.json` record the aggregate runtime status and token consumption.

### 4.3 `03_Evaluation/`
Houses the execution accuracy evaluation infrastructure under the **Official BIRD Execution Accuracy** standard:
- **`evaluation_source/`**: Contains `recompute_bird_official_ex.py`, the official set-equality recomputation engine (`set(predicted) == set(gold)` on read-only SQLite), `audit_combined_archived_official_ex_crosswalk.py`, and `EVALUATION_PROTOCOL_AND_AUDIT.md`.
- **`evaluation_results/`**: Stores the 4,602-row census master dataset (`official_bird_ex_recomputed.csv`), the 1:1:1 paired comparison dataset (`official_bird_ex_paired_wide.csv`), and the serial audit of 13 timeout cases.
- Refer to [`03_Evaluation/README.md`](Supporting_Evidence/03_Evaluation/README.md) for full dataset schemas and protocol details.

### 4.4 `04_Statistical_Analysis/`
Contains the statistical computing suite and result tables addressing the three dissertation Research Questions:
- **RQ1**: Clarification categorization across 807 turns (`rq1_turn_labels_final.csv`, `rq1_native_distribution_final.csv`) and simulated user option ID audit.
- **RQ2**: Omnibus and pairwise significance testing (`cochran_q.csv`, `pairwise_exact_mcnemar_holm.csv`, `overall_summary.csv`).
- **RQ3**: 486 outcome-discrepant query census across 6 SQL structural dimensions, 170 BIF-SIF failure stage attributions, and SQL finalization stability metrics.
- Refer to [`04_Statistical_Analysis/README.md`](Supporting_Evidence/04_Statistical_Analysis/README.md) for detailed descriptions.

---

## 5. System Requirements & Setup

### 5.1 Prerequisites
- Python 3.10 or higher
- SQLite 3.35+
- OpenAI API Key (only required for running new agent queries; not required for evaluation or statistical reproduction)

### 5.2 Python Dependencies
Install required packages:
```bash
pip install pandas scipy sqlglot pydantic openai httpx matplotlib
```

---

## 6. End-to-End Reproduction Workflow

### Step 1: Recompute Official Execution Accuracy
To verify execution accuracy directly from preserved final SQL queries against SQLite databases:
```bash
cd 03_Evaluation/evaluation_source
python recompute_bird_official_ex.py
python audit_combined_archived_official_ex_crosswalk.py
python validate_chapter5_evidence.py
```

### Step 2: Reproduce Full Statistical Analysis
To re-generate all statistical tables, significance tests, and taxonomies reported in the dissertation:
```bash
cd ../../04_Statistical_Analysis/analysis_source

# 1. RQ2 Statistical Testing
python analyze_quantitative.py

# 2. RQ1 Clarification Taxonomy
python finalize_rq1_labels.py
python analyze_clarification_option_id_quality.py

# 3. RQ3 486-Query Structural Census & Error Stage Attribution
python analyze_rq3_census.py
python analyze_rq3_stage_timing.py
python analyze_rq3_sql_finalisation.py
python analyze_rq3_failure_stages.py
python analyze_rq3_final_failure_modes.py
```

---

## 7. Data Integrity and Census Guarantee

- **100% Census**: Every condition contains exactly 1,534 evaluations corresponding to queries 0 through 1,533 with zero missing cases, zero duplicates, and zero post-hoc exclusions.
- **Strict Layer Isolation**: Layer 1 (BIF) does not access database schema catalogs, preventing premature schema hallucination; Layer 2 (SIF) strictly refines confirmed definitions.
- **Concurrency Isolation**: Independent serial re-evaluations confirmed that all timeout cases were authentic execution limits rather than thread contention artifacts.
