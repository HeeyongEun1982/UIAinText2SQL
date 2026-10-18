# Agent Implementation Architectures

## 1. Overview
This directory contains the self-contained source code for the three agent architectures evaluated in the dissertation:

> **"User-Intent Alignment in Text-to-SQL through Staged Clarification"**

Each subfolder represents an isolated, reproducible implementation package for one experimental condition.

---

## 2. Implementations Comparison

```
01_Implementation/
│
├── BIF/        # Single-stage: Business Intent Framing Only (Layer 1)
├── SIF/        # Single-stage: SQL Intent Framing Only (Layer 2)
└── BIF_SIF/    # Two-stage: Proposed Staged Intent Framing (Layer 1 -> Layer 2)
```

| Dimension | BIF Only (`BIF/`) | SIF Only (`SIF/`) | BIF-SIF (`BIF_SIF/`) [Proposed] |
| :--- | :--- | :--- | :--- |
| **Clarification Layer** | Layer 1: Business Intent Only | Layer 2: SQL Intent Only | Two-Stage: Layer 1 $\rightarrow$ Layer 2 |
| **Interaction Scope** | High-level natural language metrics, business logic, entity scopes | Technical column disambiguation, table linking, join paths | Staged: Business ambiguity first, structural SQL grounding second |
| **Intermediate Frame** | Confirmed Query Definition (CQD) | Confirmed SQL Intent Frame (SIF) | Confirmed CQD $\rightarrow$ Confirmed SIF Frame |
| **Database Grounding** | Direct schema prompting from CQD | 4-Axis schema inspection & framing | Grounded synthesis from refined SIF frame |
| **Official EX Accuracy** | 59.71% (916 / 1,534) | 56.13% (861 / 1,534) | **65.71% (1,008 / 1,534)** |
| **Detailed Guide** | [BIF README](file:///d:/Study/York/Dissertation/paper_draft/v1.4/Supporting_Evidence/01_Implementation/BIF/README.md) | [SIF README](file:///d:/Study/York/Dissertation/paper_draft/v1.4/Supporting_Evidence/01_Implementation/SIF/README.md) | [BIF_SIF README](file:///d:/Study/York/Dissertation/paper_draft/v1.4/Supporting_Evidence/01_Implementation/BIF_SIF/README.md) |

---

## 3. Shared Design Principles

Across all three implementations:
1. **Zero Question-Specific Evidence**: The model operates strictly without question-specific BIRD `evidence` hints in user prompts, adhering to authentic enterprise Text-to-SQL constraints.
2. **Read-Only SQLite Preflight**: Generated SQL queries undergo preflight execution with `PRAGMA query_only = ON` and a 30-second timeout.
3. **Simulated User Oracle (E2)**: Ambiguities are clarified using a simulated user model consulting the ground-truth SQL semantics without exposing raw SQL strings.
4. **Standard CLI Interface**: All three packages share the unified `run.py` CLI interface supporting `run-case`, `run-batch`, and `evaluate-case`.
