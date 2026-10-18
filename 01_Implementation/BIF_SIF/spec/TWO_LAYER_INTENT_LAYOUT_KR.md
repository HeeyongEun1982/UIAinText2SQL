# BIRD용 2-Layer Intent-to-SQL Layout v1.0

## 1. 목적

이 문서는 `bird_dev.json`의 자연어 질문과 gold SQL을 연결하기 위한 두 개의 중간 표현을 정의한다.

```text
User Question
  -> Layer 1 Draft: Business Intent Frame
  -> BIF User Confirmation / Correction
  -> Confirmed Business Intent Frame
  -> Layer 2 Draft: SQL Intent Frame
  -> SIF Gap Analysis
  -> SIF User Clarification in Business Language, if required
  -> Confirmed SQL Intent Frame
  -> SQL Generation
  -> SQL
```

두 Layer는 다음 책임을 가진다.

- **Business Intent Frame(BIF)**: 사용자가 읽고 자신의 분석 목적을 확인하거나 수정하는 의미 계약
- **SQL Intent Frame(SIF)**: 확정된 BIF를 변경하지 않고 schema와 SQL 연산에 연결하며, 남은 semantic gap을 사용자 업무 언어로 확인한 뒤 확정되는 시스템 내부 중간 질의

SIF는 개발자가 수동으로 검증하는 문서가 아니다. 시스템이 BIF와 DB schema로부터 자동 생성하고 SQL generator가 소비하는 표현이다. 사용자는 raw SIF나 table/column을 검토하지 않고, SIF gap에서 투영된 비개발자용 업무 질문에만 답한다.

## 2. 설계 원칙

### 2.1 Layer 경계

BIF에는 실제 테이블명, 컬럼명, JOIN 조건, SQL 함수명을 넣지 않는다. 사용자가 이해할 수 있는 업무 및 분석 언어만 사용한다.

SIF에는 테이블, 컬럼, 관계, 필터, 집계, 순위, 비교, 출력 구조를 명시한다. 단, SIF가 BIF의 의미를 새롭게 해석하거나 변경해서는 안 된다.

### 2.2 모든 SIF 연산의 추적 가능성

SIF의 각 source, filter, calculation, output에는 BIF의 원본 필드를 가리키는 JSON Pointer를 기록한다.

```json
{
  "intent_paths": [
    "/target_population/inclusion_conditions/0"
  ]
}
```

### 2.3 적용되지 않는 요소

모든 질문에 Metric, Time, Comparison이 존재하는 것은 아니다. 빈 의미를 모델이 만들어내지 않도록 `not_applicable`을 명시한다.

### 2.4 명시된 정보와 추론된 정보의 구분

BIF의 각 중요 필드는 다음 상태 중 하나를 갖는다.

```text
explicit
inferred
defaulted
user_confirmed
user_corrected
unresolved
not_applicable
```

SQL 결과를 바꿀 수 있는 `inferred` 또는 `defaulted` 항목은 사용자 확인 전까지 material assumption으로 취급한다.

## 3. BIRD 기반 커버리지 요구사항

로컬 `bird_dev.json` 1,534건을 기준으로 확인한 주요 SQL 패턴은 다음과 같다. 수치는 정규식 기반 구조 점검 결과이므로 상호 배타적이지 않다.

| 패턴 | 건수 | Layout 요구사항 |
|---|---:|---|
| JOIN | 1,140 | source와 join predicate |
| 집계 함수 | 673 | calculation step과 aggregate expression |
| 산술 또는 파생 계산 | 444 | derived measure와 expression tree |
| ORDER BY | 305 | ordering |
| LIMIT | 293 | selection 및 limit |
| DISTINCT | 244 | distinct projection/aggregate |
| CASE/IIF | 149 | conditional expression |
| Subquery | 124 | 다중 query block과 block reference |
| GROUP BY | 120 | grouping grain |
| NULL 검사 | 98 | predicate expression |
| BETWEEN | 76 | range predicate 및 time normalization |
| HAVING | 21 | post-aggregation predicate |
| Window function | 5 | partition/order/tie semantics |
| Set operation | 3 | UNION/INTERSECT/EXCEPT |

따라서 SIF는 단일 평면 SELECT 구조가 아니라 중첩 가능한 `query_blocks` 구조를 사용한다.

추가로 1,534개 gold SQL을 SQLGlot의 SQLite dialect로 구조 분석한 결과 전부 파싱됐고 실패는 0건이었다. 최상위 구조는 `SELECT` 1,532건과 `EXCEPT` 2건이며, 전체 68종의 AST node가 확인됐다. 현재 SIF는 이를 다음과 같이 포괄한다.

| Gold SQL 구조 | SIF 표현 |
|---|---|
| SELECT, CTE, Subquery | query block 및 block reference |
| Table, JOIN | source 및 join |
| WHERE, HAVING | expression predicate |
| COUNT/SUM/AVG/MIN/MAX | function expression |
| 사칙연산, CAST, 문자열·날짜 함수 | binary/unary/function expression |
| CASE/IIF | conditional expression |
| GROUP BY | group_by expression |
| RANK/DENSE_RANK | window expression |
| ORDER BY, LIMIT, OFFSET | ordering 및 limit |
| UNION, EXCEPT | set operation |
| EXISTS, IN, BETWEEN, LIKE, NULL 검사 | expression tree |

여기서 “커버”는 1,534개 정답을 미리 SIF로 변환했다는 의미가 아니라, 해당 gold SQL에 나타난 모든 구조를 Layout이 표현할 수 있다는 의미다. 개별 BIF/SIF reference instance 생성은 이 명세 다음 단계의 dataset 구축 작업이다.

## 4. Layer 1: Business Intent Frame

### 4.1 역할

BIF는 다음 질문에 답한다.

| 의도 영역 | 확인할 내용 |
|---|---|
| Decision objective | 사용자가 무엇을 알아보고 비교·판단하려는가 |
| Target population | 어떤 업무 대상이 포함되거나 제외되는가 |
| Metric | 무엇을 측정하거나 반환하는가 |
| Calculation | 측정값을 어떤 업무 규칙으로 계산하는가 |
| Granularity | 입력과 결과의 분석 단위가 무엇인가 |
| Scope | 필터·최솟값·순위 등이 전체인지 그룹별인지 |
| Time basis | 기간, 시점, 누적 또는 상대 시간 기준이 무엇인가 |
| Comparison | 어떤 집단이나 측정값을 어떤 방향으로 비교하는가 |
| Output expectation | 단일 값, 목록, 그룹표, 순위표 등 어떤 결과를 원하는가 |

### 4.2 최상위 Layout

```json
{
  "schema_version": "1.0",
  "frame_id": "BIF_1481",
  "source": {},
  "lifecycle": {},
  "decision_objective": {},
  "target_population": {},
  "metrics": [],
  "calculation": {},
  "granularity": {},
  "scope": {},
  "time_basis": {},
  "comparisons": [],
  "output_expectation": {},
  "assumptions": [],
  "field_states": [],
  "readiness": {}
}
```

### 4.3 Decision objective

`summary`는 사용자에게 그대로 보여줄 수 있어야 한다. `intent_types`는 단일 분류가 아니라 복수 선택이다. 한 질문이 조회, 집계, 순위, 비교를 동시에 포함할 수 있기 때문이다.

```json
{
  "summary": "각 고객 세그먼트의 최저 연간 소비 고객을 찾아 세그먼트 간 차이를 비교한다.",
  "intent_types": ["summarize", "select_extreme", "compare"]
}
```

지원 유형:

```text
retrieve, count, summarize, derive, compare, rank,
select_extreme, filter, test_existence, trend, custom
```

### 4.4 Target population

업무 대상과 포함·제외 조건을 표현한다. 조건은 SQL 컬럼이 아니라 사용자 용어를 사용한다.

```json
{
  "primary_entity": "customer",
  "entity_description": "카드 고객",
  "inclusion_conditions": [
    {
      "condition_id": "C1",
      "subject": "customer currency",
      "operator": "equals",
      "value": "CZK",
      "description": "CZK 통화를 사용하는 고객"
    }
  ],
  "exclusion_conditions": []
}
```

### 4.5 Metric

반환하거나 계산할 업무 측정값을 정의한다. 단순 이름·주소 조회처럼 측정값이 없는 경우 배열은 비워 둔다.

```json
{
  "metric_id": "M1",
  "name": "annual customer consumption",
  "definition": "한 고객의 2013년 전체 소비량",
  "measure_kind": "derived",
  "unit": null
}
```

### 4.6 Calculation

복합 질의를 커버하기 위해 계산을 순서 있는 step으로 표현한다. 각 step은 업무 문장과 입출력 참조를 가진다.

```json
{
  "steps": [
    {
      "step_id": "CALC1",
      "operation": "aggregate",
      "description": "각 고객의 월별 소비량을 2013년 동안 합산한다.",
      "input_refs": ["monthly_consumption"],
      "output_ref": "M1",
      "method": "sum"
    },
    {
      "step_id": "CALC2",
      "operation": "aggregate",
      "description": "세그먼트별 최저 소비 고객의 연간 소비량을 평균한다.",
      "input_refs": ["M1", "selected_lowest_customers"],
      "output_ref": "segment_average",
      "method": "average"
    }
  ],
  "null_handling": "exclude missing consumption values",
  "duplicate_handling": "use one customer-month record per recorded month"
}
```

지원 operation:

```text
retrieve, filter, derive, aggregate, compare, rank,
select, set_combine, existence, custom
```

### 4.7 Granularity

`input_unit`과 `output_unit`을 분리한다. 이것은 평균의 분모나 GROUP BY 오류를 방지하는 핵심 필드다.

```json
{
  "input_unit": "one monthly consumption record per customer",
  "output_unit": "one comparison summary",
  "dimensions": ["customer", "calendar year", "segment"]
}
```

### 4.8 Scope

일반 필터와 최솟값·상위 N·순위 같은 선택 범위를 분리한다.

```json
{
  "grouping_dimensions": ["segment"],
  "selections": [
    {
      "selection_id": "SEL1",
      "mode": "minimum",
      "target_ref": "M1",
      "within_dimensions": ["segment"],
      "n": 1,
      "tie_policy": "include_all"
    }
  ]
}
```

`within_dimensions`가 비어 있으면 전역 범위를 의미한다. 따라서 “각 세그먼트에서 최저”와 “전체에서 최저”를 명시적으로 구분할 수 있다.

### 4.9 Time basis

시간이 없는 질의도 동일한 Layout을 유지하기 위해 `mode: not_applicable`을 사용한다.

```json
{
  "mode": "fixed_period",
  "description": "2013년 1월부터 12월까지",
  "periods": [
    {
      "start": "2013-01",
      "end": "2013-12",
      "grain": "month"
    }
  ],
  "temporal_semantics": "calendar_period",
  "cumulative": false
}
```

지원 mode:

```text
not_applicable, point_in_time, fixed_period,
relative_period, latest, earliest, recurring, custom
```

### 4.10 Comparison

차이, 비율, 백분율 변화, 순위 비교를 방향성과 함께 표현한다.

```json
{
  "comparison_id": "CMP1",
  "type": "difference",
  "left": "segment_average[SME]",
  "right": "segment_average[LAM]",
  "direction": "left_minus_right",
  "output_ref": "SME_minus_LAM"
}
```

### 4.11 Output expectation

결과의 shape와 사용자가 보고 싶은 필드를 정의한다.

```json
{
  "shape": "single_row",
  "fields": [
    {
      "field_id": "O1",
      "label": "SME minus LAM",
      "semantic_ref": "SME_minus_LAM"
    }
  ],
  "ordering": [],
  "limit": null,
  "include_supporting_values": false
}
```

지원 shape:

```text
scalar, single_row, detail_list, grouped_table,
ranked_list, time_series, boolean, custom
```

### 4.12 Assumptions와 readiness

```json
{
  "assumptions": [
    {
      "assumption_id": "A1",
      "description": "연간 소비량은 월별 소비량의 합계이다.",
      "material": true,
      "status": "requires_confirmation",
      "affected_paths": ["/calculation/steps/0"]
    }
  ],
  "readiness": {
    "state": "needs_confirmation",
    "blocking_paths": ["/calculation/steps/0"],
    "reason": "연간 소비량 계산 방식의 확인이 필요하다."
  }
}
```

## 5. Layer 2: SQL Intent Frame

### 5.1 역할

SIF는 Confirmed BIF를 DB schema와 관계형 연산에 자동 연결한다. 사용자의 의도를 변경하거나 새 업무 규칙을 추가하지 않는다. 이 변환에서 사용자에게 답할 수 있는 material semantic gap이 발견되면 업무 언어 clarification을 거쳐 Confirmed SIF가 된다.

SIF는 SQL 문자열이 아니라 SQL 생성에 필요한 구조화 중간 표현이다.

### 5.2 최상위 Layout

```json
{
  "schema_version": "1.0",
  "sql_intent_id": "SIF_1481",
  "source_frame_id": "BIF_1481",
  "database": {},
  "status": "ready",
  "query_blocks": [],
  "main_block_id": "Q_FINAL",
  "traceability": [],
  "unresolved_grounding": []
}
```

### 5.3 Query block

각 query block은 하나의 SELECT 또는 CTE/subquery 역할을 한다.

```json
{
  "block_id": "Q_ANNUAL",
  "role": "aggregation",
  "sources": [],
  "joins": [],
  "where": [],
  "derived_fields": [],
  "group_by": [],
  "having": [],
  "windows": [],
  "projection": [],
  "distinct": false,
  "order_by": [],
  "limit": null,
  "set_operations": [],
  "intent_paths": []
}
```

### 5.4 Source와 join

```json
{
  "sources": [
    {
      "source_id": "SRC_CUSTOMER",
      "kind": "table",
      "name": "customers",
      "alias": "c",
      "intent_paths": ["/target_population"]
    },
    {
      "source_id": "SRC_MONTH",
      "kind": "table",
      "name": "yearmonth",
      "alias": "y",
      "intent_paths": ["/metrics/0"]
    }
  ],
  "joins": [
    {
      "join_id": "J1",
      "type": "inner",
      "left_source": "SRC_CUSTOMER",
      "right_source": "SRC_MONTH",
      "predicates": [
        {
          "kind": "binary",
          "operator": "equals",
          "left": {"kind": "column", "source": "c", "name": "CustomerID"},
          "right": {"kind": "column", "source": "y", "name": "CustomerID"}
        }
      ],
      "intent_paths": ["/target_population", "/metrics/0"]
    }
  ]
}
```

### 5.5 Expression tree

필터, 파생 계산, 집계, 비교, 출력은 공통 expression tree를 사용한다.

지원 expression kind:

```text
column, literal, parameter, reference, function, binary,
unary, conditional, subquery, star, raw_sql_hint
```

예: `Currency = 'CZK'`

```json
{
  "kind": "binary",
  "operator": "equals",
  "left": {
    "kind": "column",
    "source": "c",
    "name": "Currency"
  },
  "right": {
    "kind": "literal",
    "value": "CZK"
  },
  "intent_paths": ["/target_population/inclusion_conditions/0"]
}
```

예: 고객별 연간 소비량

```json
{
  "field_id": "annual_total",
  "expression": {
    "kind": "function",
    "name": "SUM",
    "args": [
      {
        "kind": "column",
        "source": "y",
        "name": "Consumption"
      }
    ]
  },
  "intent_paths": ["/calculation/steps/0", "/metrics/0"]
}
```

### 5.6 Grouping, HAVING, window와 selection scope

```json
{
  "group_by": [
    {"kind": "column", "source": "c", "name": "Segment"},
    {"kind": "column", "source": "c", "name": "CustomerID"}
  ],
  "windows": [
    {
      "window_id": "W1",
      "function": "DENSE_RANK",
      "args": [],
      "partition_by": [
        {"kind": "reference", "name": "Segment"}
      ],
      "order_by": [
        {
          "expression": {"kind": "reference", "name": "annual_total"},
          "direction": "asc"
        }
      ],
      "output_field": "consumption_rank",
      "intent_paths": ["/scope/selections/0"]
    }
  ]
}
```

`within_dimensions: ["segment"]`는 SIF에서 `partition_by: [Segment]`로 보존되어야 한다.

### 5.7 Subquery와 set operation

Subquery는 source 또는 expression에서 다른 `block_id`를 참조한다.

```json
{
  "source_id": "SRC_RANKED",
  "kind": "query_block",
  "block_ref": "Q_RANKED",
  "alias": "r"
}
```

UNION, INTERSECT, EXCEPT는 별도 block 사이의 set operation으로 표현한다.

```json
{
  "operator": "except",
  "query_block_ref": "Q_MEETINGS",
  "all": false,
  "intent_paths": ["/target_population/exclusion_conditions/0"]
}
```

### 5.8 Projection과 output shape

```json
{
  "projection": [
    {
      "output_id": "SME_minus_LAM",
      "label": "SME minus LAM",
      "expression": {
        "kind": "binary",
        "operator": "subtract",
        "left": {"kind": "reference", "name": "segment_average_SME"},
        "right": {"kind": "reference", "name": "segment_average_LAM"}
      },
      "intent_paths": ["/comparisons/0", "/output_expectation/fields/0"]
    }
  ]
}
```

## 6. Layer 간 필수 매핑

| BIF | SIF | 예상 SQL |
|---|---|---|
| target population | source, where | FROM, JOIN, WHERE |
| metric | column/function expression | SELECT expression |
| calculation | derived field, aggregate block | 함수, 산술식, CTE/subquery |
| granularity | group_by, projection grain | GROUP BY |
| scope filter | where/having | WHERE/HAVING |
| scope selection | window/order/limit | PARTITION BY, ORDER BY, LIMIT |
| time basis | normalized predicate | date/time predicate |
| comparison | binary/conditional expression | 차이, 비율, CASE/IIF |
| output expectation | projection/order/limit | SELECT, ORDER BY, LIMIT |

## 7. 자동 일관성 규칙

다음 규칙은 사람이 아니라 시스템이 적용한다.

1. `readiness.state`가 `ready` 또는 `confirmed`가 아니면 SIF를 생성하지 않는다.
2. material assumption이 미확정이면 SQL을 생성하지 않는다.
3. SIF의 모든 업무 필터에는 하나 이상의 `intent_path`가 있어야 한다.
4. BIF의 모든 metric과 output field는 SIF의 expression 또는 projection으로 추적되어야 한다.
5. BIF의 grouping dimension은 SIF의 group/window/output grain 중 하나에 나타나야 한다.
6. BIF의 selection scope가 그룹별이면 SIF의 partition 또는 상관 subquery에 같은 scope가 있어야 한다.
7. 비교 방향과 피연산자 순서를 변경하지 않는다.
8. BIF에 없는 filter, grouping, limit을 SIF가 새로 추가하면 unresolved assumption으로 되돌린다.
9. schema에 연결되지 않은 업무 개념이 남으면 `status: needs_grounding`으로 둔다.
10. SIF가 `ready`일 때 `unresolved_grounding`은 비어 있어야 한다.

## 8. BIRD evidence 금지와 gold SQL 격리 정책

BIRD evidence는 운영 Agent, 평가 Agent, Simulated User 어디에도 사용하지 않는다. 원본 dataset과 동일한 포맷을 유지하기 위해 파일에 필드가 존재하더라도 experiment loader가 즉시 제거한다.

### 운영/실험 대상 시스템 입력

```text
question + DB schema + 재사용 가능한 schema metadata
```

BIRD의 question-specific evidence와 gold SQL은 BIF/SIF/SQL 생성 Agent에 입력하지 않는다.

### Batch Simulated User

```text
Draft BIF summary + original question + gold SQL
  -> confirm 또는 business-language correction
```

Gold SQL은 E2 Simulated User가 사용자의 숨은 의도를 판단할 때와 S7 Evaluator가 실행 결과를 비교할 때만 사용한다. E2의 응답에는 SQL 문법, table, column, JOIN을 노출하지 않으며, gold SQL은 이후 Agent로 전달하지 않는다.

30건의 BIF/SIF 구조 평가용 reference가 필요하면 evidence 없이 `question + gold SQL + schema`만으로 사람이 작성하고 검수한다. 질문과 gold SQL이 충돌할 경우 다음 상태를 별도 annotation에 기록한다.

```text
aligned
question_gold_conflict
manual_review_required
```

이 품질 상태는 BIF/SIF 본문이 아니라 평가 dataset wrapper에 저장한다.

## 9. 개발 순서

1. 두 JSON Schema를 데이터 계약으로 고정한다.
2. 질문에서 Draft BIF를 생성하는 extractor를 구현한다.
3. BIF를 사용자용 네 영역 요약으로 렌더링한다: 목적, 대상/기간, 계산, 비교/출력.
4. 사용자 또는 simulated user의 수정 사항을 BIF JSON Patch로 반영한다.
5. Confirmed BIF와 schema에서 Draft SIF를 생성한다.
6. SIF의 user-answerable gap과 system-resolvable grounding gap을 분류한다.
7. User-answerable material gap은 비개발자 업무 언어로 clarification한다.
8. 사용자 응답을 반영해 Confirmed SIF를 생성한다.
9. SIF의 traceability 및 readiness를 자동 검사한다.
10. Confirmed SIF에서 SQL을 생성한다.
11. SQL AST를 다시 추출해 SIF와 일치하는지 검사한다.
12. 30건 smoke set, DB별 표본, 전체 1,534건 순서로 확장한다.

## 10. 완료 조건

Layout v1.0의 구현 완료 조건은 다음과 같다.

- BIF가 사용자에게 schema/SQL 용어 없이 표시된다.
- 사용자가 수정한 의미가 BIF에 구조적으로 반영된다.
- SIF 단계의 사용자 질문은 table/column/SQL 용어 없이 업무 언어로 표현된다.
- SIF clarification 응답이 affected node에만 반영된다.
- SIF의 모든 의미 연산이 BIF로 추적된다.
- BIRD의 단순 조회, JOIN, 집계, 비율, 순위, subquery, window, conditional, set operation을 표현할 수 있다.
- 미확정 의도나 미해결 grounding이 있으면 SQL 생성을 차단한다.
- SIF에서 SQL을 만들고 다시 구조를 추출하여 자동 비교할 수 있다.
