# SQL Intent Framing (SIF) Only Implementation

## 1. Overview
This directory contains the complete source code for the **SIF-Only (SQL Intent Framing)** experimental condition of the dissertation:

> **"User-Intent Alignment in Text-to-SQL through Staged Clarification"**

The SIF-Only condition evaluates an agent pipeline that focuses purely on **Layer 2: Structural SQL-Intent Clarification**. Instead of conducting business-level natural language clarification, this pipeline directly maps the user query to database schema objects and resolves technical structural ambiguities across four core axes:
1. **Source & Joins** (table linking, join paths, keys)
2. **Filters & Conditions** (value representations, WHERE clauses, NULL handling)
3. **Aggregations & Calculations** (SUM/AVG/COUNT, arithmetic, groupings)
4. **Projections & Order** (SELECT column targets, ORDER BY, LIMIT)

BIF natural language clarification is bypassed in this condition.

---

## 2. Architecture and Pipeline Workflow

```
[BIRD Question]
      │
      ▼
[P00: Schema Snapshot & Catalog Extraction]
      │
      ▼ (BIF Natural Language Stage Bypassed)
      │
[P03: SQL Intent Framing & Clarification]
      ├── Generate 13-field SIF Draft
      ├── 4-Axis Inspection (Joins, Filters, Aggregations, Projections)
      ├── E2 Clarification against Ground-Truth SQL
      └── Refine & Produce Confirmed SIF Frame
      │
      ▼
[P04: Grounded SQL Synthesis (Prompting with Confirmed SIF)]
      │
      ▼
[P07: Read-Only SQLite Preflight Execution & 1-Shot Repair]
      │
      ▼
[P09-P14: Execution Comparison & Metric Logging]
```

---

## 3. Directory Structure

```
SIF/
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
│       ├── agents/            # SIF draft generators, 4-axis inspectors, and E2 simulated user
│       ├── pipeline/          # CaseRunner, BatchRunner, and evaluation loops
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
To run the full SIF-only pipeline for a specific query ID (e.g., query `0`):
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
To run batch execution over the dataset:
```bash
# Run first 10 queries
python run.py run-batch \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --sif-layout v3 \
  --limit 10

# Run all 1,534 queries
python run.py run-batch \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --sif-layout v3
```

### 5.3 Evaluating Pre-generated SQL
To evaluate a pre-generated SQL against the ground-truth SQL on the target SQLite DB:
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
