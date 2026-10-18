# BIRD Dev 1,534건 기반 BIF/SIF 일반화 설계

## 1. 목적과 원칙

Q87은 특정 실패를 설명하는 regression case로는 유용하지만 Layout 전체를 설계하는
근거로는 부족하다. 이 문서는 `bird_dev.json`의 Question과 Gold SQL 1,534쌍 및
11개 DB schema를 기준으로 BIF/SIF가 표현해야 하는 의미 범위를 정리한다.

적용 원칙:

1. BIRD evidence는 런타임 Agent 입력으로 사용하지 않는다.
2. Gold SQL은 reference Frame 작성과 evaluation에서만 사용한다.
3. BIF에는 table, column, join, SQL 함수와 code를 기록하지 않는다.
4. SIF는 BIF 의미를 변경하지 않고 물리 구현과 query execution structure만 추가한다.
5. Q87-specific rule이 아니라 corpus feature coverage와 invariant를 설계한다.

## 2. Corpus 구성

### 2.1 규모

| 구분 | 건수 |
|---|---:|
| 전체 | 1,534 |
| Simple | 925 |
| Moderate | 464 |
| Challenging | 145 |
| Database | 11 |

### 2.2 DB별 건수

| DB | 건수 |
|---|---:|
| card_games | 191 |
| codebase_community | 186 |
| formula_1 | 174 |
| thrombosis_prediction | 163 |
| student_club | 158 |
| toxicology | 145 |
| european_football_2 | 129 |
| superhero | 129 |
| financial | 106 |
| california_schools | 89 |
| debit_card_specializing | 64 |

## 3. Gold SQL 구조 분포

SQL 문자열의 quoted identifier와 literal을 제거한 뒤 구조 keyword/function을
계수했다. 이는 완전한 SQL AST 통계가 아니라 Layout coverage를 결정하기 위한
corpus census다.

| 구조 | 건수 | 비율 |
|---|---:|---:|
| JOIN | 1,140 | 74.3% |
| Aggregate | 659 | 43.0% |
| COUNT | 478 | 31.2% |
| ORDER BY | 305 | 19.9% |
| LIMIT | 293 | 19.1% |
| Arithmetic output/expression | 228 | 14.9% |
| SUM | 183 | 11.9% |
| DISTINCT | 179 | 11.7% |
| CAST | 152 | 9.9% |
| CASE/IIF | 149 | 9.7% |
| Nested SELECT | 126 | 8.2% |
| Date/time function | 123 | 8.0% |
| GROUP BY | 120 | 7.8% |
| NULL predicate | 98 | 6.4% |
| BETWEEN | 76 | 5.0% |
| AVG | 53 | 3.5% |
| IN predicate | 48 | 3.1% |
| MIN/MAX function | 44 | 2.9% |
| LIKE | 42 | 2.7% |
| HAVING | 21 | 1.4% |
| CTE | 9 | 0.6% |
| Window | 5 | 0.3% |
| Set operation | 3 | 0.2% |
| EXISTS | 2 | 0.1% |

### 3.1 의미 구조별 건수

| 의미/query shape | 건수 |
|---|---:|
| Detail without aggregate | 875 |
| Scalar aggregate | 550 |
| Top/bottom selection | 288 |
| Multi-column output | 284 |
| Conditional aggregate | 136 |
| Nested query | 126 |
| Grouped aggregate | 109 |
| Group-wise selection | 76 |
| Scalar subquery comparison | 55 |
| IN subquery | 28 |
| FROM subquery | 25 |
| Output 4개 이상 | 16 |
| Multiple aggregate outputs | 9 |
| Correlated EXISTS | 2 |

Top/bottom selection과 group-wise selection은 일부 중복되며, 모든 분류는 상호
배타적이지 않다.

### 3.2 Query block 수

| SELECT block 수 | 건수 |
|---|---:|
| 1 | 1,408 |
| 2 | 109 |
| 3 | 13 |
| 4 | 2 |
| 5 | 1 |
| 8 | 1 |

단일 target population과 단일 query block만 가정하면 최소 126건의 구조를 직접
표현할 수 없다.

## 4. 현재 Layout coverage 평가

### 4.1 현재 BIF가 비교적 잘 표현하는 영역

- 하나의 population에 대한 detail retrieval
- 단일 scalar count/sum/average
- 단일 grouping dimension의 grouped aggregate
- global top/bottom N
- 기본 difference, ratio, percentage
- 고정 기간과 단순 조건
- 단일 또는 복수 논리 출력 field

### 4.2 현재 BIF의 구조적 공백

#### 복수 population scope

다음 의미는 하나의 `target_population`만으로 정확히 표현하기 어렵다.

- 특정 집단의 값과 전체 평균 비교
- A 집단 count와 B 집단 count의 ratio
- outer population을 inner top-N population으로 제한
- 두 독립 집단의 차이
- IN/EXISTS를 사용하는 관계 집합

현재는 조건을 metric, calculation, comparison에 반복하거나 free-text `method`에
숨겨야 한다.

#### Typed expression graph 부재

`calculation.steps[].method`가 문자열이므로 다음을 구조적으로 구분하지 못한다.

- conditional count/sum
- numerator와 denominator의 독립 filter
- cast와 null-safe division
- nested arithmetic
- CASE/IIF 결과
- aggregate 위의 aggregate

#### Aggregation phase 부재

같은 filter라도 다음 위치가 다르다.

- row filter before aggregation
- group filter after aggregation
- rank filter after window calculation

현재 BIF/SIF는 이를 안정적으로 구분하지 못해 WHERE, HAVING, outer query가
뒤섞인다.

#### Selection scope 부족

현재 selection의 `within_dimensions`는 유용하지만 다음을 완전히 표현하지 못한다.

- global top-N
- each group 안의 top-N
- top-N group을 고른 뒤 그 그룹의 detail rows 반환
- max/min tie를 모두 포함할지 한 행만 반환할지
- selection metric과 output metric이 다른 경우

#### Time의 단일 전역 구조

Nested comparison에서는 outer population과 reference population이 서로 다른 기간을
가질 수 있다. 현재 전역 `time_basis` 하나로는 표현이 부족하다.

### 4.3 현재 SIF v2/lowerer의 구조적 공백

현재 fail-closed policy상 다음을 직접 지원하지 않는다.

- `time_basis.periods`
- `scope.selections`
- internal expression
- subquery requirement
- window, set operation, date component, type cast grounding
- conditional expression
- HAVING phase

Gold 구조에서 cast, nested block, date function, CASE/IIF, HAVING, window, set operation,
EXISTS 중 하나 이상이 있는 case는 414건이다. 이들을 제외한 단일-block 후보는
1,120건, 73.0%다. 이는 grounding 실패와 semantic 오류를 제외한 구조적 상한이다.

| 난이도 | 구조적 단일-block 후보 | 비율 |
|---|---:|---:|
| Simple | 786 / 925 | 85.0% |
| Moderate | 281 / 464 | 60.6% |
| Challenging | 53 / 145 | 36.6% |

따라서 현재 Layout을 유지한 채 모델 prompt만 개선해서는 challenging coverage를
확보할 수 없다.

## 5. 일반화된 BIF Layout

### 5.1 설계 목표

BIF는 사용자에게 읽히는 업무 의미 계약이면서 다음 SIF 구조로 손실 없이 전달될
수 있어야 한다. SQL 문법을 노출하지 않되, 복수 population과 계산 dependency는
명시해야 한다.

### 5.2 최상위 구조

```json
{
  "schema_version": "2.0",
  "objective": {},
  "entities": [],
  "source_atoms": [],
  "population_scopes": [],
  "time_scopes": [],
  "measures": [],
  "derivations": [],
  "comparisons": [],
  "selections": [],
  "output": {},
  "clarification_state": {},
  "readiness": {}
}
```

### 5.3 Source Atom

```json
{
  "atom_id": "atom_1",
  "source_span": {
    "text": "customers paid in CZK",
    "start": 0,
    "end": 21
  },
  "business_term": "customers paid in CZK",
  "semantic_role": "population_condition",
  "interpretation": {
    "concept": "customer currency",
    "status": "explicit"
  },
  "state": "explicit"
}
```

모든 material BIF node는 하나 이상의 source atom을 참조한다. 이를 통해 모델이
질문에 없는 조건, 출력, NULL, DISTINCT 정책을 추가하는 것을 검출한다.

### 5.4 Population Scope

```json
{
  "scope_id": "population_sme_2013",
  "entity_ref": "customer",
  "purpose": "comparison_operand",
  "conditions": [
    {
      "condition_id": "condition_currency",
      "atom_refs": ["atom_currency"],
      "business_dimension": "currency",
      "operator": "equals",
      "value": "CZK",
      "grounding_status": "needs_schema"
    }
  ],
  "condition_logic": {},
  "time_scope_ref": "time_2013",
  "parent_scope_ref": null
}
```

`population_scopes`를 배열로 두어 target, reference, comparison operand, membership,
selected group을 각각 표현한다. Nested SELECT가 있다고 항상 별도 BIF scope가 필요한
것은 아니지만, 업무적으로 독립된 모집단이면 별도 scope가 필요하다.

### 5.5 Time Scope

```json
{
  "time_scope_id": "time_2013",
  "atom_refs": ["atom_2013"],
  "applies_to_scope_refs": ["population_sme_2013"],
  "business_event": "consumption recorded",
  "mode": "fixed_period",
  "start": "2013-01-01",
  "end": "2013-12-31",
  "grain": "year",
  "inclusive": {"start": true, "end": true}
}
```

기간은 population condition에 중복하지 않는다. 서로 다른 scope가 서로 다른 기간을
가질 수 있다.

### 5.6 Measure

```json
{
  "measure_id": "annual_average_consumption_sme",
  "atom_refs": ["atom_average_consumption"],
  "name": "annual average consumption",
  "scope_ref": "population_sme_2013",
  "value_kind": "numeric",
  "aggregation": {
    "function": "average",
    "input_measure_ref": "consumption",
    "distinct": null,
    "null_policy": "unspecified"
  },
  "grain_refs": ["customer", "year"]
}
```

`attribute`, `count`, `sum`, `average`, `minimum`, `maximum`, `indicator`를 free-text가
아닌 typed aggregation으로 표현한다.

### 5.7 Derivation Expression

```json
{
  "derivation_id": "ratio_a_to_b",
  "atom_refs": ["atom_ratio"],
  "expression": {
    "kind": "binary",
    "operator": "divide",
    "left": {"kind": "measure_ref", "ref": "count_a"},
    "right": {"kind": "measure_ref", "ref": "count_b"}
  },
  "result_kind": "ratio",
  "scale": 1,
  "zero_denominator_policy": "unspecified"
}
```

필요한 expression node:

- measure_ref, literal
- binary arithmetic
- unary operation
- conditional value
- coalesce/null policy
- set membership

BIF expression은 업무 연산 이름을 사용하며 SQL cast/function은 포함하지 않는다.

### 5.8 Selection

```json
{
  "selection_id": "top_5_per_county",
  "atom_refs": ["atom_top_5", "atom_each_county"],
  "input_scope_ref": "eligible_schools",
  "rank_by_ref": "average_reading_score",
  "direction": "descending",
  "n": 5,
  "partition_by": ["county"],
  "tie_policy": "include_all",
  "result_scope_id": "selected_schools"
}
```

`partition_by=[]`이면 global selection이고 값이 있으면 group-wise selection이다.
selection 결과를 새로운 population scope로 참조할 수 있어야 detail output과 nested
query를 표현할 수 있다.

### 5.9 Comparison

```json
{
  "comparison_id": "sme_minus_lam",
  "atom_refs": ["atom_sme_lam_difference"],
  "left_ref": "annual_average_consumption_sme",
  "right_ref": "annual_average_consumption_lam",
  "operation": "difference",
  "direction": "left_minus_right",
  "output_ref": "difference_sme_lam"
}
```

같은 measure definition을 서로 다른 scope에 적용할 수 있어야 한다. 비교 순서는
반드시 명시한다.

### 5.10 Output

```json
{
  "shape": "table",
  "row_grain_refs": ["school"],
  "fields": [
    {
      "field_id": "administrator_emails",
      "atom_refs": ["atom_admin_email"],
      "value_ref": "administrator_email",
      "multiplicity": "multiple",
      "presentation": "separate_columns",
      "coverage": "all_requested"
    }
  ],
  "ordering": [],
  "limit": null
}
```

Output field는 논리적 값이며 여러 physical column이나 row로 확장될 수 있다.

## 6. 일반화된 SIF Layout

### 6.1 설계 목표

SIF는 BIF node ID를 변경하지 않는 physical/query implementation overlay다. 단일
query block을 전제로 하지 않는다.

```json
{
  "schema_version": "3.0",
  "source_bif": {},
  "groundings": {},
  "query_graph": {},
  "physical_support": {},
  "admission": {},
  "readiness": {}
}
```

### 6.2 Grounding Registry

모든 BIF node에 독립적인 grounding slot을 둔다.

```json
{
  "grounding_id": "ground_condition_unified",
  "bif_ref": "condition_unified",
  "candidate_bindings": [
    {
      "source_ref": "schools",
      "column": "DOC",
      "operator": "equals",
      "value": "54",
      "transformation": "code_mapping",
      "evidence": {
        "kind": "schema_codebook",
        "label": "Unified School District"
      },
      "score": 0.98
    }
  ],
  "selected_binding": 0,
  "owner": "system",
  "status": "system_resolved"
}
```

후보 생성과 최종 선택을 분리한다. 존재하지 않는 column은 후보 registry에 들어갈 수
없다.

### 6.3 Query Graph

```json
{
  "blocks": [
    {
      "block_id": "main",
      "role": "main",
      "implements_scope_refs": ["selected_schools"],
      "source_refs": [],
      "join_refs": [],
      "pre_aggregation_predicates": [],
      "grouping_refs": [],
      "measure_expression_refs": [],
      "post_aggregation_predicates": [],
      "window_expression_refs": [],
      "post_window_predicates": [],
      "selection_ref": null,
      "projection_refs": [],
      "ordering_refs": []
    }
  ],
  "dependencies": [],
  "main_block_ref": "main",
  "set_operations": []
}
```

Block role enum:

- main
- scalar_reference
- membership
- aggregate_reference
- ranking
- set_operand
- existence
- derived_source

126개 nested query와 window/group-wise selection을 이 구조로 표현한다.

### 6.4 Physical Expression AST

필요한 node:

- column, literal
- aggregate
- binary, unary
- conditional
- cast
- date_component
- scalar_subquery_ref
- membership_subquery_ref
- exists_ref
- window
- function

각 expression은 `bif_refs`와 `grounding_refs`를 가져야 한다. SQL에 존재하지만 어느
BIF 의미에서도 유도되지 않는 expression은 admission에서 거절하거나
`benchmark_only` provenance를 요구한다.

### 6.5 Join과 Relationship

Join은 BIF condition이 아니라 SIF physical support다. 다음을 분리한다.

- source table/view
- relationship path
- key pairs
- join cardinality
- join type
- join necessity: required / output_path / filter_path / benchmark_only

74.3%가 join을 사용하므로 join planning은 모델의 부수 작업이 아니라 독립적인
deterministic component여야 한다.

### 6.6 Phase-aware Predicate

```json
{
  "predicate_id": "above_average",
  "bif_refs": ["condition_above_average"],
  "phase": "post_aggregation",
  "expression_ref": "expr_group_average_gt_400"
}
```

Phase enum:

- pre_aggregation
- post_aggregation
- post_window
- outer_scope

이 구분으로 WHERE, HAVING, ranked subquery filter를 결정론적으로 lowering한다.

## 7. Clarification Ownership

### 7.1 BIF에서 사용자/E2에게 물을 내용

- 비교하거나 판단하려는 업무 목적
- 대상 업무 집단
- measure의 업무 정의
- 합계, 평균, 비율 등의 계산 의미
- 결과 grain
- global/group-wise scope
- 기간과 업무 event
- 비교 방향
- output shape와 복수 값 presentation

### 7.2 SIF에서 사용자/E2에게 물을 수 있는 내용

DB를 보면서 새로 발견됐지만 여전히 업무적인 선택인 경우만 허용한다.

- 어느 업무 record population을 의미하는지
- transaction 기준인지 account 기준인지
- 여러 업무상 유효한 날짜 중 어느 event date인지

질문은 비개발자 업무 용어로 표현한다.

### 7.3 System이 결정할 내용

- table, column, join key/path
- code/value mapping
- cast와 date function
- WHERE/HAVING/window 위치
- query block과 subquery 구조
- SQL dialect syntax

System-owned gap은 E2에게 전달하지 않는다.

## 8. 전체 데이터 기반 Reference/Evaluation 설계

1,534건을 사람이 모두 작성하지 않는다.

### 8.1 Offline Reference Builder

```text
Question + Gold SQL + Schema
  -> Gold SQL parser / neutral query IR
  -> Question-based BIF candidate
  -> Gold IR과 BIF 의미 reconciliation
  -> Reference BIF (schema-free)
  -> Reference SIF (grounded)
  -> schema validation
  -> SQL regeneration
  -> Gold/Regenerated execution comparison
```

Gold SQL의 table/column이 BIF로 역유입되지 않도록 BIF schema에 물리 identifier를
금지한다. Gold에서만 발견되는 join이나 cast는 SIF implementation provenance로
남긴다.

### 8.2 Reference 품질 관리

- 전체 1,534건 자동 생성
- difficulty/DB/feature stratified sample 수동 검토
- 초기 30건, 이후 100건 수동 gold-frame audit
- 동일 semantic structure cluster에서 대표 case 검수
- execution mismatch reference는 blocked queue로 분리

### 8.3 Layer별 평가

| Layer | 지표 |
|---|---|
| BIF | source atom precision/recall |
| BIF | population scope coverage |
| BIF | measure/expression accuracy |
| BIF | grain/time/output accuracy |
| Clarification | material gap recall |
| Clarification | correct option inclusion rate |
| E2 | correct-option 조건부 selection accuracy |
| SIF | grounding slot coverage |
| SIF | table/column/value accuracy |
| SIF | query-block/phase accuracy |
| SQL | execution accuracy |
| Runtime | unique rounds, repeated cycles, calls, tokens, latency |

## 9. 개발 순서

### Phase 0 — Corpus analyzer와 Reference IR

1. SQL AST parser 도입 또는 구현
2. 1,534건 feature manifest 생성
3. neutral Gold Query IR 정의
4. semantic structure cluster 생성

### Phase 1 — BIF 2.0

1. source atom/span
2. multiple population scopes
3. scoped time
4. typed measures/derivations
5. selections/comparisons/output
6. canonical state/ledger/Frame invariant

### Phase 2 — SIF 3.0

1. grounding candidate registry
2. deterministic schema/codebook matcher
3. multi-block query graph
4. phase-aware predicate
5. physical expression AST
6. join planner

### Phase 3 — Compiler

1. single-block detail/aggregate
2. conditional aggregate/cast/date
3. HAVING and group selection
4. scalar/IN/EXISTS subquery
5. window and group-wise top-N
6. set operations

### Phase 4 — Evaluation

1. Q2/Q76/Q87 regression
2. feature-stratified 30건
3. feature-stratified 100건
4. 전체 1,534건 batch
5. difficulty·DB·feature별 ablation과 error attribution

## 10. 결론

Q87에서 확인한 atom 보존과 system-owned grounding 원칙은 유지해야 한다. 하지만
최종 Layout은 Q87의 SOC/DOC 문제를 해결하는 구조가 아니라, 1,534건에서 반복되는
다음 의미를 공통으로 표현해야 한다.

- 복수 population scope
- scoped measure와 aggregation
- typed arithmetic/conditional derivation
- global/group-wise selection
- nested reference population
- phase-aware predicate
- multi-output/multi-binding
- physical query graph

따라서 기존 BIF 1.1/SIF 2.0에 예외 필드를 계속 추가하지 않고 BIF 2.0과 SIF 3.0을
새 contract로 설계하고, 기존 Layout은 regression baseline으로 유지하는 것이
적절하다.
