# Business Intent Framing (BIF) Only Implementation

## 1. Overview
This directory contains the complete source code for the **BIF-Only (Business Intent Framing)** experimental condition of the dissertation:

> **"User-Intent Alignment in Text-to-SQL through Staged Clarification"**

The BIF-Only condition evaluates an agent pipeline that focuses purely on **Layer 1: Business-Intent Clarification** prior to database grounding. It identifies ambiguous business concepts, metric formulas, filtering semantics, and entity scopes in the user's natural language question, interacts with a simulated user (E2 oracle), establishes a **Confirmed Query Definition (CQD)**, and directly synthesizes SQL without performing SQL-level intent framing (SIF is bypassed).

---

## 2. Architecture and Pipeline Workflow

```
[BIRD Question]
      │
      ▼
[P00: Schema Snapshot & Question Loading]
      │
      ▼
[P01: Business Intent Analysis & Clarification]
      ├── Generate BIF clarification questions / options
      └── Receive simulated user response (E2 Ground-Truth Oracle)
      │
      ▼
[P02: Confirmed CQD (Confirmed Business Intent)]
      │
      ▼ (SIF Stage Bypassed)
      │
[P04: Direct SQL Synthesis (Prompting with CQD + Full Schema)]
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
BIF/
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
│       ├── agents/            # BIF prompt templates, analyzers, and E2 simulated user
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
To run the full BIF-only pipeline for a specific query ID (e.g., query `0`):
```bash
python run.py run-case \
  --case-id 0 \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --pipeline-model gpt-4o-mini \
  --simulated-user-model gpt-4o-mini
```

### 5.2 Running Batch Evaluation
To run batch execution over the entire dataset or a subset:
```bash
# Run first 10 queries
python run.py run-batch \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs \
  --limit 10

# Run all 1,534 queries
python run.py run-batch \
  --dataset-path ../data/bird_dev.json \
  --databases-root ../data/bird_dev/dev_databases \
  --runs-root ../runs
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
- **Zero Question-Specific Evidence**: The model operates strictly without question-specific BIRD `evidence` hints in user prompts, ensuring a realistic business enterprise Text-to-SQL setting.
- **Read-Only SQLite Execution**: Preflight execution enforces `PRAGMA query_only = ON` with a strict 30-second per-query timeout to prevent runaway queries and state mutations.
