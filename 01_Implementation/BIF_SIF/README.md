# Staged Business & SQL Intent Framing (BIF-SIF) Implementation

## 1. Overview
This directory contains the complete source code for the proposed **BIF-SIF (Staged Business and SQL Intent Framing)** method of the dissertation:

> **"User-Intent Alignment in Text-to-SQL through Staged Clarification"**

The BIF-SIF architecture introduces a **two-layer staged clarification framework** that systematically resolves the dual-nature ambiguities inherent in enterprise Text-to-SQL:
1. **Layer 1: Business Intent Framing (BIF)** resolves high-level semantic ambiguities (metric definitions, business scope, entity rules) prior to schema grounding, establishing a **Confirmed Query Definition (CQD)**.
2. **Layer 2: SQL Intent Framing (SIF)** receives the confirmed CQD via a clean architectural handoff, decomposes it into structural schema-grounded components (tables, joins, predicates, calculations, projections), resolves technical operational ambiguities, and outputs a **Confirmed SIF Frame**.
3. **SQL Synthesis & Validation**: The final SQL generator synthesizes query syntax directly from the confirmed SIF frame, accompanied by read-only preflight verification and 1-shot repair.

---

## 2. Architecture and Pipeline Workflow

```
[BIRD Case (Question + DB ID)]
             │
             ▼
    [P00: Case Loading & Schema Catalog]
             │
             ▼
    [P01-P02: Layer 1 - Business Intent Framing (BIF)]
    ├── Multi-turn natural language clarification (Simulated User E2)
    └── Produces Confirmed CQD (Confirmed Business Intent)
             │
             ▼ (Handoff to Layer 2)
             │
    [P03: Layer 2 - SQL Intent Framing (SIF)]
    ├── 13-field structural SIF draft from CQD
    ├── 4-axis inspection (Sources, Predicates, Groupings, Projections)
    ├── Structural clarification with E2 Oracle
    └── Produces Confirmed SIF Frame
             │
             ▼
    [P04: Grounded SQL Synthesis from SIF]
             │
             ▼
    [P07: Read-Only SQLite Preflight Execution & 1-Shot Repair]
             │
             ▼
    [P09-P14: Execution, Outcome Canonicalization & Trace Logging]
```

---

## 3. Directory Structure

```
BIF_SIF/
├── data/                      # BIRD dev metadata, schemas, and table documentation
│   ├── bird_dev.json          # 1,534 development queries (question, SQL, difficulty, db_id)
│   ├── bird_dev/              # SQLite database directories (*.sqlite)
│   └── schema_docs/           # Extracted table/column descriptions and foreign keys
│
├── spec/                      # Formal JSON schemas and specification documents
│   ├── business_intent_frame.schema.json
│   ├── sql_intent_frame.schema.json
│   └── TWO_LAYER_INTENT_LAYOUT_KR.md
│
├── intent_sql_agent/          # Core Python agent package
│   ├── run.py                 # CLI entry point (no pip install required)
│   ├── pyproject.toml         # Package definition and dependencies
│   └── src/intent_sql_agent/  # Modular agent pipeline components
│       ├── agents/            # BIF & SIF agents, semantic frontends, E2 simulated user
│       ├── pipeline/          # FullCaseRunner, BatchRunner, and evaluation loops
│       └── services/          # SQLite executor, validator, and artifact logger
│
└── README.md                  # This document
```

---

## 4. Environment Setup

### 4.1 Prerequisites
- Python 3.10+
- OpenAI API Key (configured in environment)

### 4.2 Dependencies
Install required packages using `pip`:
```bash
pip install pandas scipy sqlglot pydantic openai httpx
```

### 4.3 Environment Variable
Set your OpenAI API key before execution:
```bash
# Windows PowerShell
$env:OPENAI_API_KEY="your-api-key-here"

# Linux / macOS Bash
export OPENAI_API_KEY="your-api-key-here"
```

---

## 5. Execution Guide

Navigate to the `intent_sql_agent` directory:
```bash
cd intent_sql_agent
```

### 5.1 Running a Single Case
To run the full two-stage BIF-SIF pipeline for a specific query ID (e.g., query `0`):
```bash
python run.py run-case \
  --case-id 0 \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --sif-layout v3 \
  --pipeline-model gpt-4o-mini \
  --simulated-user-model gpt-4o-mini
```

### 5.2 Running Batch Evaluation
To run batch execution over the entire 1,534-query BIRD benchmark:
```bash
# Run first 10 queries
python run.py run-batch \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --sif-layout v3 \
  --limit 10

# Run all 1,534 queries (Full Dissertation Benchmark)
python run.py run-batch \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --sif-layout v3
```

### 5.3 Evaluating Pre-generated SQL
To evaluate pre-generated SQL against the ground-truth SQL:
```bash
python run.py evaluate-case \
  --case-id 0 \
  --generated-sql-file ../runs/0/sql/sql_final.sql \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases
```

---

## 6. Input Policy & Constraints
- **Zero Question-Specific Evidence**: Operates strictly without question-specific BIRD `evidence` hints in user prompts.
- **Read-Only SQLite Execution**: Preflight execution enforces `PRAGMA query_only = ON` with a strict 30-second per-query timeout.
- **Strict Layer Decoupling**: Layer 1 (BIF) does not perform schema mapping or column matching, preventing premature schema hallucination; Layer 2 (SIF) operates strictly on the confirmed CQD definition.
