# Statistical Analysis Scripts and Empirical Results

## 1. Overview
This directory contains the **statistical analysis scripts, hypothesis testing routines, and comprehensive empirical result tables** for the dissertation:

> **"User-Intent Alignment in Text-to-SQL through Staged Clarification"**

The artifacts in this directory provide complete statistical evidence answering the three core Research Questions (RQs):
- **RQ1 (Clarification Nature)**: What types of ambiguities are resolved by BIF versus SIF, and what is the quality of simulated user interactions?
- **RQ2 (Execution Accuracy & Significance)**: Does the proposed two-stage BIF-SIF framework achieve statistically significant execution accuracy gains over single-stage baselines (BIF-Only and SIF-Only)?
- **RQ3 (Mechanism & Handoff Analysis)**: Through what structural SQL transformations and stage handoffs does BIF-SIF succeed or fail across the 486 outcome-discrepant queries?

---

## 2. Directory Structure

```
04_Statistical_Analysis/
│
├── README.md                                    # This documentation
│
├── analysis_source/                             # Python analysis pipelines & statistical tests
│   ├── analyze_quantitative.py                  # RQ2 Primary statistical suite (Wilson CI, Cochran's Q, McNemar-Holm)
│   ├── analyze_paired_official_ex.py            # Paired 1:1:1 hypothesis testing on Official EX
│   ├── classify_rq1.py                          # RQ1 Rule-based initial clarification classification
│   ├── finalize_rq1_labels.py                   # RQ1 Review integration and final label distribution generator
│   ├── review_rq1_bif_full_content.py           # RQ1 Full-content reviewer for 432 BIF clarification turns
│   ├── review_rq1_sif_full_content.py           # RQ1 Full-content reviewer for 375 SIF clarification turns
│   ├── adopt_rq1_full_content_reviews.py        # Applies LLM-assisted full reviews to final RQ1 distribution
│   ├── analyze_clarification_option_id_quality.py # Comprehensive audit of simulated user option ID matching
│   ├── analyze_rq3_census.py                    # RQ3 486-query census on 6 SQL structural dimensions
│   ├── analyze_rq3_traces.py                    # Multi-turn interaction trace and stage trajectory analyzer
│   ├── analyze_rq3_stage_timing.py              # Stage timing and sequential mechanism attribution
│   ├── analyze_rq3_sql_finalisation.py          # Comparison between initial synthesized SQL and final adopted SQL
│   ├── extract_rq3_negative_stage_handoffs.py   # Extraction pipeline for 170 BIF-SIF error cases
│   ├── analyze_rq3_failure_stages.py            # First observed error stage analysis for 170 BIF-SIF failures
│   ├── analyze_rq3_final_failure_modes.py       # Final SQL syntax and semantic failure mode categorizer
│   ├── analyze_boundary_correlations.py         # Schema scale (tables, joins, columns) boundary correlations
│   └── analyze_operational_diagnostics.py       # Pipeline latency, token, and model call diagnostics
│
└── analysis_results/                            # Statistical output datasets, summaries, and tables
    ├── overall_accuracy.*                       # RQ2 Overall accuracy, Cochran's Q, McNemar-Holm, Difficulty/DB
    ├── clarification_categories.*               # RQ1 807-question final coding, distributions, ID audit
    └── bif_sif_handoff_analysis.*               # RQ3 486-case census, 6-axis SQL coding, stage failures
```

---

## 3. Research Questions & Empirical Findings

### 3.1 RQ1: Clarification Taxonomy & Interaction Quality
- **Corpus**: 807 evaluated clarification turns (BIF-Only: 432 turns; SIF-Only: 375 turns, excluding trial qid 1--10).
- **Finding**:
  - BIF predominates in **business logic & formula definitions** (48.1%), **entity scope resolution** (28.7%), and **temporal constraints** (14.6%).
  - SIF concentrates heavily on **column disambiguation & data representation** (44.5%), **join path confirmation** (31.2%), and **aggregation/grouping operations** (18.4%).
- **Option ID Quality**: 100% of BIF-Only simulated user responses matched provided option IDs. In SIF-Only, 17 responses used valid text-grounded substitutions, with zero downstream parsing breakages.

### 3.2 RQ2: Overall Execution Accuracy & Hypothesis Testing
- **Sample**: $N = 1,534$ full benchmark census per condition (4,602 total queries).
- **Official EX Accuracy**:
  - **BIF-Only**: 916 / 1,534 (**59.71%**, 95% Wilson CI: [57.24%, 62.15%])
  - **SIF-Only**: 861 / 1,534 (**56.13%**, 95% Wilson CI: [53.63%, 58.60%])
  - **BIF-SIF**: 1,008 / 1,534 (**65.71%**, 95% Wilson CI: [63.30%, 68.06%])
- **Omnibus Test**:
  - Cochran's $Q = 104.57$ ($df = 2, p < 10^{-22}$), confirming highly significant performance variation across conditions.
- **Pairwise McNemar Tests (Holm-corrected)**:
  - **BIF-SIF vs. BIF-Only**: $+6.00\%$ absolute gain ($p = 0.00030$)
  - **BIF-SIF vs. SIF-Only**: $+9.58\%$ absolute gain ($p = 5.25 \times 10^{-12}$)
  - **BIF-Only vs. SIF-Only**: $+3.59\%$ absolute gain ($p = 0.0163$)

### 3.3 RQ3: SQL Structural Locus & Stage Handoff Mechanism
- **Census Scope**: All 486 queries where at least one condition yielded a different correctness outcome (BIF-SIF correct: 316; BIF-SIF incorrect: 170).
- **6-Axis Structural Locus**:
  - Resolving **Conditions & Values** (WHERE clauses) accounted for 68.4% of BIF-SIF positive recoveries.
  - Resolving **Calculations & Aggregations** accounted for 42.1% of improvements.
  - Correct **Source & Join selection** accounted for 35.8% of improvements.
- **BIF-SIF Error Stage Attribution (170 Failures)**:
  - 58.2% (99 cases) originated in Layer 1 (BIF business intent ambiguity misidentification).
  - 34.1% (58 cases) originated in Layer 2 (SIF schema linking/join path selection).
  - 7.7% (13 cases) were final SQL synthesis syntax/operator slips.
- **SQL Finalization Stability**: 98.97% (481/486) of final adopted SQLs matched the initial synthesis exactly, demonstrating that structural divergence occurs during intent framing rather than late-stage repairs.

---

## 4. Analysis Source Code (`analysis_source/`)

| Script Name | Target RQ | Primary Function & Outputs |
| :--- | :---: | :--- |
| `analyze_quantitative.py` | **RQ2** | Calculates Official EX, Wilson 95% CIs, Cochran's Q test, pairwise exact McNemar tests with Holm correction, and 8-way outcome patterns (`000` to `111`). |
| `analyze_paired_official_ex.py` | **RQ2** | Executes paired statistical verification directly on the wide dataset. |
| `classify_rq1.py` | **RQ1** | Applies heuristic and keyword-based decision rules to classify raw clarification turns into candidate taxonomy categories. |
| `finalize_rq1_labels.py` | **RQ1** | Produces provisional native (8-class BIF / 9-class SIF) and common (5-class) taxonomy distribution tables. |
| `review_rq1_bif_full_content.py` / `review_rq1_sif_full_content.py` | **RQ1** | LLM-assisted re-reading pipeline inspecting full question context, generated options, and chosen responses for all 807 turns. |
| `adopt_rq1_full_content_reviews.py` | **RQ1** | Integrates full-content review labels into the final RQ1 dataset (`rq1_turn_labels_final.csv`, `rq1_native_distribution_final.csv`, `rq1_common_distribution_final.csv`). |
| `analyze_clarification_option_id_quality.py` | **RQ1** | Audits 11,860 clarification interaction records across all runs to ensure option ID consistency and simulated user fidelity. |
| `analyze_rq3_census.py` | **RQ3** | Analyzes the 486 outcome-discrepant queries across 6 SQL structural dimensions (Sources, Conditions, Aggregations, Groupings, Projections, Order/Limit). |
| `analyze_rq3_stage_timing.py` | **RQ3** | Quantifies whether clarification turns directly matched, partially matched, or conflicted with downstream SQL structural choices. |
| `analyze_rq3_sql_finalisation.py` | **RQ3** | Computes exact string and canonical match rates between initial synthesized SQL and final adopted SQL. |
| `extract_rq3_negative_stage_handoffs.py` | **RQ3** | Extracts intermediate artifacts (Confirmed BIF, Initial SIF, Confirmed SIF, Initial SQL, Final SQL) for the 170 BIF-SIF error cases. |
| `analyze_rq3_failure_stages.py` | **RQ3** | Traces the earliest observed divergence stage for all 170 BIF-SIF errors. |
| `analyze_rq3_final_failure_modes.py` | **RQ3** | Categorizes final errors into syntax errors, join failures, filter misalignments, or projection mismatches. |
| `analyze_boundary_correlations.py` | **RQ2/RQ3** | Evaluates accuracy correlations across database schema size, table count, and join complexity. |
| `analyze_operational_diagnostics.py` | **General** | Evaluates latency, token consumption, and model call efficiency across pipeline stages. |

---

## 5. Statistical Results & Datasets (`analysis_results/`)

### 5.1 Overall Accuracy & Significance (`overall_accuracy.*`)
- **`overall_summary.csv`**: Summary table reporting Correct counts, Total $N=1,534$, Accuracy %, Wilson Score 95% Confidence Intervals for BIF, SIF, and BIF-SIF.
- **`cochran_q.csv`**: Omnibus Cochran's Q test statistic ($Q = 104.57$), degrees of freedom ($df = 2$), and asymptotic $p$-value.
- **`pairwise_exact_mcnemar_holm.csv`**: Contingency counts ($n_{01}, n_{10}$), two-tailed exact binomial McNemar $p$-values, Holm-adjusted alpha thresholds, and significance flags.
- **`official_ex_by_difficulty.csv`**: Execution accuracy breakdown across BIRD difficulty tiers (Simple: 925, Moderate: 464, Challenging: 145).
- **`official_ex_by_database.csv`**: Execution accuracy breakdown across all 11 evaluation SQLite databases.
- **`official_evaluation_status_summary.csv`**: Census breakdown of execution status (`ok`, `execution_error`, `timeout`, `missing_prediction`).
- **`quantitative_summary.json`**: Master machine-readable JSON storing all quantitative values reported in the dissertation.

### 5.2 Clarification Categories (`clarification_categories.*`)
- **`rq1_turn_labels_final.csv`**: Master per-turn label table (807 rows) containing query ID, turn index, native category, common category, and qualitative rationale.
- **`rq1_native_distribution_final.csv`**: Native category frequency distribution for BIF (8 categories) and SIF (9 categories).
- **`rq1_common_distribution_final.csv`**: Cross-condition comparison distribution across 5 unified semantic categories.
- **`rq1_sif_detected_not_elicited.csv`**: Inventory of 5 SIF items detected as ambiguous but capped from active user prompting.
- **`rq1_final_coding_summary.json`**: Metadata summary including total turns, review changes, and input hashes.
- **`rq1_bif_full_content_review.csv` & `rq1_sif_full_content_review.csv`**: Detailed per-turn context, prompt text, options, and full-content review notes.
- **`clarification_option_id_quality_by_turn.csv` & `.json`**: Turn-by-turn audit verifying that simulated user responses correctly referenced provided option IDs.

### 5.3 BIF-SIF Handoff & Error Mechanism (`bif_sif_handoff_analysis.*`)
- **`rq3_census_structural_coding.csv`**: 6-axis structural coding for all 486 outcome-discrepant queries comparing generated SQL against Gold SQL.
- **`rq3_census_condition_diagnostics.csv`**: Condition-by-condition diagnostic breakdown across the 486 discrepant queries (1,458 evaluations).
- **`rq3_census_locus_summary.csv`**: Aggregated frequency of structural SQL differences grouped by outcome direction (BIF-SIF Win vs. BIF-SIF Loss).
- **`rq3_stage_timing_census.csv` & `summary.csv / .json`**: Verification of whether clarification choices directly carried through to final SQL selections.
- **`rq3_negative_failure_stage_census.csv` & `summary.csv / .json`**: Stage attribution for 170 BIF-SIF error cases identifying the initial stage of deviation.
- **`rq3_sql_finalisation_census.csv` & `summary.csv / .json`**: Comparison between initial synthesized SQL and final adopted SQL across all 486 cases.
- **`rq3_final_failure_mode_census.csv` & `summary.csv / .json`**: Failure mode taxonomy for incorrect queries (syntax, join, predicate, aggregation).

---

## 6. Reproduction Guide

To re-run the entire statistical analysis suite:

```bash
# Navigate to the analysis source directory
cd analysis_source

# 1. Run RQ2 primary quantitative suite (Wilson CI, Cochran's Q, McNemar-Holm)
python analyze_quantitative.py

# 2. Run RQ1 clarification taxonomy and interaction quality analysis
python finalize_rq1_labels.py
python analyze_clarification_option_id_quality.py

# 3. Run RQ3 outcome-discrepant census (486 queries) & 6-axis structural analysis
python analyze_rq3_census.py
python analyze_rq3_stage_timing.py
python analyze_rq3_sql_finalisation.py

# 4. Run RQ3 negative case stage attribution (170 error cases)
python extract_rq3_negative_stage_handoffs.py
python analyze_rq3_failure_stages.py
python analyze_rq3_final_failure_modes.py

# 5. Run boundary scale correlations and operational diagnostics
python analyze_boundary_correlations.py
python analyze_operational_diagnostics.py
```
