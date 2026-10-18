# Question-to-BIF NLP Semantic Compiler 설계

## 1. 결론

Question에서 BIF 전체를 생성형 LLM으로 작성하게 하지 않는다. 다음 구조의
constrained semantic compiler를 사용한다.

```text
Question
  -> Text Normalizer
  -> Span/Clause Analyzer
  -> Semantic Role Tagger
  -> Relation & Scope Graph Builder
  -> Typed Intent Parser
  -> Deterministic BIF Assembler
  -> Invariant Validator
  -> Candidate BIF 또는 Clarification Choices
```

LLM은 기본 경로가 아니다. 결정론적 parser가 둘 이상의 유효한 BIF 후보를 만들거나
어떤 span의 semantic role을 분류하지 못할 때 해당 slot에만 제한적으로 사용할 수
있다. LLM이 Frame JSON 전체를 다시 작성하는 경로는 제거한다.

## 2. 왜 keyword rule만으로는 부족한가

BIRD dev 1,534건에서 Gold SQL 구조와 질문의 명시 cue를 비교한 결과다.

| Gold 의미 | Gold 건수 | 질문 명시 cue recall |
|---|---:|---:|
| Average | 53 | 100.0% |
| Arithmetic | 228 | 77.6% |
| Top/bottom | 288 | 74.3% |
| Count | 478 | 69.2% |
| Nested population | 126 | 65.9% |
| Conditional calculation | 149 | 65.8% |
| Time transformation | 123 | 54.5% |
| Sum | 183 | 16.4% |
| Grouping | 120 | 15.0% |

예를 들어 다음 표현은 단어 그대로 SQL operation과 대응하지 않는다.

```text
most common       -> count by category + maximum selection
majority          -> conditional counts + comparison
which city has... -> group by city
above the average -> reference population aggregate + comparison
in each county    -> partitioned selection
```

따라서 regex는 lexical normalization에 사용하고, 최종 구조는 clause dependency와
semantic relation으로 만든다.

## 3. Compiler 중간 표현

Question을 BIF로 바로 쓰지 않고 세 단계 IR을 거친다.

### 3.1 Text IR

```json
{
  "original_text": "...",
  "normalized_text": "...",
  "tokens": [],
  "clauses": [],
  "coordination_groups": [],
  "coreference_candidates": []
}
```

모든 token과 clause는 원문 offset을 유지한다.

### 3.2 Semantic Span IR

```json
{
  "spans": [
    {
      "span_id": "span_1",
      "text": "annual average consumption",
      "start": 0,
      "end": 26,
      "roles": ["MEASURE", "AGGREGATION"],
      "normalized_concepts": ["consumption", "average"],
      "confidence": 1.0,
      "source": "lexicon_and_dependency"
    }
  ]
}
```

필수 semantic role:

- OBJECTIVE
- ENTITY
- POPULATION_CONDITION
- CONDITION_SUBJECT
- CONDITION_OPERATOR
- CONDITION_VALUE
- LOGIC_CONNECTOR
- MEASURE
- AGGREGATION
- DERIVATION_OPERATOR
- GRAIN
- GROUP_SCOPE
- TIME_VALUE
- TIME_EVENT
- SELECTION_MODE
- SELECTION_N
- PARTITION_SCOPE
- COMPARISON_LEFT
- COMPARISON_RIGHT
- COMPARISON_DIRECTION
- OUTPUT_FIELD
- OUTPUT_PRESENTATION

### 3.3 Intent Relation Graph

```json
{
  "nodes": [],
  "edges": [
    {"type": "FILTERS", "from": "condition_1", "to": "population_1"},
    {"type": "MEASURED_ON", "from": "measure_1", "to": "population_1"},
    {"type": "GROUPED_BY", "from": "measure_1", "to": "dimension_1"},
    {"type": "RANKED_BY", "from": "selection_1", "to": "measure_1"},
    {"type": "PARTITIONED_BY", "from": "selection_1", "to": "dimension_2"},
    {"type": "COMPARES", "from": "comparison_1", "to": "measure_1"},
    {"type": "OUTPUTS", "from": "output_1", "to": "measure_1"}
  ]
}
```

이 graph에서 BIF 2.0을 결정론적으로 조립한다.

## 4. NLP Component

### N0 Text Normalizer

기능:

- Unicode와 whitespace 정규화
- 날짜, 통화, 퍼센트, 숫자 범위 인식
- `no. of`, `number of`, `how many` canonicalization
- `highest`, `biggest`, `most`, `majority` selection 후보화
- singular/plural canonicalization
- 원문 offset 보존

출력은 Text IR이다. 원문 token을 삭제하거나 재배열하지 않는다.

### N1 Clause and Coordination Analyzer

기능:

- main request와 subordinate clause 분리
- `and`, `or`, `either`, `both`, `respectively` 구조 분석
- coordinated noun phrase와 coordinated condition 구분
- relative clause가 수식하는 entity 연결
- `among`, `within`, `for each`, `per` scope 경계 생성

Q87에서는 다음 span을 중복 소비하지 않도록 한다.

```text
City of San Bernardino
Unified Schools
```

`Unified`가 이미 classification span에 포함되면 location condition 생성에 다시
사용할 수 없다.

### N2 Semantic Role Tagger

구현은 두 층으로 나눈다.

1. deterministic lexicon/rule tagger
2. non-generative contextual tagger

Contextual tagger는 token/span label만 예측한다. JSON이나 자연어를 생성하지 않는다.
가능한 출력 label은 closed enum이다.

예:

```text
"most common bond type"
most common -> SELECTION_MODE(max_frequency)
bond type   -> ENTITY_ATTRIBUTE + GROUP_SCOPE
```

### N3 Operator and Measure Parser

다음 표현을 typed operator로 변환한다.

| 표현 | Typed 의미 |
|---|---|
| how many, number of | count |
| average, mean | average |
| total amount | sum |
| difference between A and B | subtract(A, B) |
| ratio of A to B | divide(A, B) |
| percentage of A among B | scale(divide(A, B), 100) |
| increase rate from A to B | divide(subtract(B, A), A) |
| most common X | top-1 by count grouped by X |
| above average | greater-than target value vs reference average |

Parser는 typed expression tree를 만들며 free-text calculation method를 만들지 않는다.

### N4 Population and Scope Parser

독립 population scope를 생성하는 cue:

- among X
- of customers who...
- compared with/to Y
- all other types
- above/below the average
- in their respective groups
- from A and B
- whose related entity satisfies...

같은 condition 집합을 공유하면 parent scope와 delta conditions를 사용한다.

```text
base_population
  -> SME population
  -> LAM population
  -> KAM population
```

### N5 Time Parser

구조:

- time value/range
- grain
- inclusive boundary
- business event
- applies-to population/measure

`in 1996`, `during the 1980s`, `from 1993/3/22 to 1998/12/27`, `annual`을
time scope로 만든다. SQL의 `strftime` 여부는 결정하지 않는다.

### N6 Selection Parser

다음 요소를 분리한다.

- selection target
- rank measure
- ascending/descending
- N
- partition/group scope
- tie policy
- selected scope의 downstream output

```text
top 5 schools in each county by reading score
```

는 global limit가 아니라 `partition_by=county`인 selection이다.

### N7 Output Parser

질문의 주 요청과 `also provide`, `indicate`, `showing`, `along with` 절을 output으로
수집한다. 필터나 grouping에 사용된 supporting field를 자동 출력하지 않는다.

## 5. Deterministic BIF Assembler

Assembler는 Intent Relation Graph만 입력으로 받는다. 모델 output을 입력으로 받지
않는다.

처리 순서:

1. source atom 생성
2. entity와 population scope 생성
3. condition/logic 연결
4. time scope 연결
5. measure와 aggregation 생성
6. derivation/comparison 생성
7. selection과 result scope 생성
8. output 생성
9. source span provenance 연결
10. invariant 검사

같은 입력 graph는 항상 byte-equivalent canonical BIF를 생성해야 한다.

## 6. Candidate Parse와 Clarification

NLP parser가 하나의 Frame을 추측하지 않고 가능한 parse 후보를 유지한다.

```json
{
  "parse_slot": "location_role_1",
  "candidates": [
    {
      "candidate_id": "city",
      "meaning": "San Bernardino is the city",
      "effects": []
    },
    {
      "candidate_id": "district",
      "meaning": "San Bernardino City Unified is the district",
      "effects": []
    },
    {
      "candidate_id": "preserve_and_defer",
      "meaning": "Preserve the stated terms and defer database classification",
      "effects": []
    }
  ]
}
```

Orchestrator는 후보 중 material하게 다른 의미만 E2에게 묻는다. 질문 writer가 새
option을 발명하지 못한다.

### Clarification 종료 조건

- 모든 required semantic slot에 후보 하나가 선택됨
- source span coverage invariant 통과
- population/measure/output reference가 모두 연결됨
- logic graph cycle 없음
- unresolved user-owned slot 없음

Schema-owned slot은 BIF 종료를 막지 않고 SIF로 이관한다.

## 7. LLM Fallback 정책

### 기본적으로 사용하지 않는 영역

- BIF JSON 전체 생성
- condition ID 생성
- logic topology 작성
- calculation expression 작성
- clarification 종료 결정
- 기존 Frame 수정

### 제한적으로 허용할 수 있는 영역

1. 특정 span의 closed-label semantic role 분류
2. 두 명사구의 coreference 여부
3. 기존 candidate meanings의 비개발자용 paraphrase

Fallback response contract 예:

```json
{
  "span_id": "span_17",
  "selected_role": "GROUP_SCOPE",
  "selected_candidate_id": "candidate_2",
  "confidence": "medium"
}
```

새 node, 새로운 조건, SQL identifier를 반환하지 못하게 한다.

## 8. Pure NLP와 Hybrid 선택

### Pure rule-only

장점:

- 완전한 재현성
- 매우 낮은 latency/cost
- 디버깅 용이

한계:

- `most common`, `majority`, `above average`처럼 암시된 연산 grammar가 빠르게 증가
- 비문과 다양한 paraphrase 유지 비용
- grouping과 nested scope의 keyword recall이 낮음

### 권장: constrained NLP hybrid

```text
rules/lexicon
  + dependency/clause parser
  + closed-label span classifier
  + deterministic graph/BIF compiler
  + E2 clarification
  + optional slot-only LLM fallback
```

생성형 모델 의존성을 최소화하면서 rule-only보다 paraphrase에 강하다.

## 9. Offline 학습 자료 구성

Gold SQL은 runtime 입력이 아니라 offline supervision에만 사용한다.

```text
Question + Gold Query IR
  -> operation cue alignment
  -> span/role weak labels
  -> scope/relation candidates
  -> Reference BIF
```

주의:

- SQL table/column을 BIF business term으로 복사하지 않는다.
- Gold에만 있는 join/cast는 BIF label이 아니다.
- parser rule을 특정 question ID에 맞춰 추가하지 않는다.
- 같은 template이 train/test에 동시에 들어가 성능을 부풀리지 않도록 template cluster
  단위로 분할한다.

## 10. 평가 설계

### 10.1 Data split

1. 질문 skeleton/template clustering
2. DB와 difficulty를 고려한 cluster-level split
3. development cluster에서 rule/tagger 개발
4. held-out cluster에서 일반화 평가
5. parser 동결 뒤 전체 1,534건 평가

### 10.2 지표

| 평가 영역 | 지표 |
|---|---|
| Span | precision/recall/F1 |
| Role | closed-label accuracy/F1 |
| Relation | edge precision/recall/F1 |
| Scope | population/time/selection scope exact match |
| Expression | typed tree exact/structural match |
| BIF | node/edge semantic match |
| Clarification | correct candidate inclusion rate |
| Runtime | deterministic completion rate |
| Runtime | E2 clarification rate |
| Runtime | LLM fallback rate |
| Performance | latency, tokens, cost |

최종 BIF exact JSON match만 사용하지 않는다. ID와 표현 문구가 달라도 의미가 같은
경우를 node/edge 단위로 평가한다.

## 11. 구현 모듈

```text
nlp/
  normalizer.py
  clause_analyzer.py
  span_tagger.py
  operator_lexicon.py
  relation_parser.py
  population_scope_parser.py
  time_parser.py
  selection_parser.py
  output_parser.py
  candidate_forest.py

bif_compiler/
  graph_to_bif.py
  canonical_ids.py
  source_span_validator.py
  scope_validator.py
  expression_validator.py
  readiness.py

evaluation/
  reference_bif_loader.py
  span_metrics.py
  graph_metrics.py
  bif_semantic_diff.py
```

## 12. 개발 순서

### Step 1

1,534개 질문의 token/clause/source span corpus를 생성한다.

### Step 2

빈도가 높은 구조부터 deterministic parser를 구현한다.

1. detail retrieval
2. count/sum/average
3. filter/boolean logic
4. top/bottom
5. arithmetic ratio/difference
6. grouping
7. time
8. comparison populations
9. group-wise selection
10. nested reference population

### Step 3

Candidate forest와 clarification completeness validator를 구현한다.

### Step 4

Deterministic BIF 2.0 assembler와 invariant를 구현한다.

### Step 5

Held-out template cluster에서 deterministic completion과 semantic accuracy를 측정한다.
fallback은 이 결과를 보고 추가한다.

## 13. 목표

- 전체 BIF 생성 LLM 호출: 0회
- normal path BIF latency: local NLP 수준
- 동일 질문 BIF byte-level reproducibility: 100%
- unresolved case만 E2 clarification
- LLM fallback은 slot 단위이며 목표 사용률을 실험으로 결정
- BIF semantic accuracy는 Reference BIF와 node/edge 단위로 측정

핵심은 LLM을 더 잘 prompt하는 것이 아니라, Question-to-BIF를 생성 문제가 아닌
**제약된 semantic parsing과 compilation 문제**로 바꾸는 것이다.
