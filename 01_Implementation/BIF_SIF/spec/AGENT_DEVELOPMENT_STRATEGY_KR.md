# Intent-to-SQL Agent 개발 전략 v3.0

이 문서는 현재 구현의 전체 개발 전략이다. Clarification 제어의 기본 계약은 [`CLARIFICATION_ORCHESTRATOR_SPEC_KR.md`](./CLARIFICATION_ORCHESTRATOR_SPEC_KR.md), 동적 queue 기반의 다음 구현 상세는 [`DYNAMIC_CLARIFICATION_ORCHESTRATOR_DESIGN_KR.md`](./DYNAMIC_CLARIFICATION_ORCHESTRATOR_DESIGN_KR.md)를 따른다.

## 1. 연구 목적

사용자의 자연어 질의를 곧바로 SQL로 변환하지 않고 두 의미 Layer를 거친다.

```text
User Question
  -> Business Intent Frame (BIF)
  -> Schema-grounded Intent Frame (SIF)
  -> SQL
  -> Gold SQL과 실제 DB 실행 결과 비교
```

- BIF는 사용자가 이해하고 수정·승인할 수 있는 업무 의도 표현이다.
- SIF는 동일한 업무 의도를 SQL로 컴파일할 수 있도록 schema와 query semantics에 연결한 자연어·구조 표현이다.
- BIF clarification은 목적, 대상, 지표, 계산, 범위, 시간, 비교, 출력을 명확히 한다.
- SIF clarification은 BIF가 DB 질의 구조로 연결될 때 새로 생긴 의미 선택만 명확히 한다.
- table/column/join/cast/dialect/data-quality 문제는 사용자에게 묻지 않고 system-owned로 처리한다.
- BIRD evidence는 사용하지 않는다. E2 Simulated User가 평가 시 사람 역할로 답한다.

## 2. 핵심 통제 원칙

Clarification의 최종 결정은 반드시 결정론적 Orchestrator가 한다.

```text
Model sub-function: 의미 관찰 또는 문장 표현
Orchestrator: 수용·분류·ID·path·순서·상태·audit·종료
E2: 현재 질문에만 답변
Assembler: 종료 후 ledger 전체로 Frame을 한 번 조립
```

이 원칙 때문에 다음 경로는 두지 않는다.

- A2/A5가 materiality, ownership, category 또는 종료를 결정하는 경로
- 모델이 만든 gap/request/decision ID를 제어 신호로 쓰는 경로
- E2 응답마다 A3/A6가 BIF/SIF 전체 또는 일부를 patch하는 경로
- 모델이 만든 confirm 문구를 종료 신호로 쓰는 경로
- old GapReport를 IssueObservation으로 변환하는 compatibility fallback

## 3. Sub Agent 목록

모델 역할은 A1~A8과 E2이며, 모든 역할이 매 case마다 호출되는 것은 아니다. Orchestrator, validator, compiler, executor, evaluator, logger는 결정론적 서비스다.

| ID | 이름 | 주요 입력 | 출력 | 호출 조건 |
|---|---|---|---|---|
| A1 | Business Intent Analyzer | question | Draft BIF + 최초 IssueObservation | case당 1회 |
| A2-DISCOVERY | BIF Issue Observer | question, Draft BIF, resolved ledger | IssueObservation[] | BIF audit 필요 시 |
| A2-QUESTION | BIF Question Writer | canonical BIF DecisionSpec | QuestionDraft | pending BIF decision 존재 시 |
| E2-BIF | Simulated User | canonical request, 숨은 Gold 의도 | UserResponse | BIF 질문마다 |
| A3 | One-shot BIF Assembler | Draft BIF, frozen BIF ledger | Confirmed BIF | BIF 종료 후 1회 |
| A4 | SIF Draft Constructor | Confirmed BIF, DB catalog | Draft SIF | BIF 확정 후 1회 |
| A5-DISCOVERY | SIF Issue Observer | BIF, Draft SIF, resolved ledger | IssueObservation[] | SIF audit 필요 시 |
| A5-QUESTION | SIF Question Writer | canonical SIF DecisionSpec | QuestionDraft | pending SIF decision 존재 시 |
| E2-SIF | Simulated User | canonical request, 숨은 Gold 의도 | UserResponse | SIF 질문마다 |
| A6 | One-shot SIF Assembler | Draft SIF, frozen SIF ledger, schema | Confirmed SIF | SIF 종료 후 1회 |
| A7 | SQL Generator | Confirmed SIF | SQLite SELECT | compiler fallback 시 |
| A8 | SQL Verifier/Repairer | Confirmed SIF, SQL, runtime error | accept/repair/return | 검증·실행 오류 시 제한 호출 |

## 4. 각 Agent 상세 기능

### A1 Business Intent Analyzer

- DB schema, Gold SQL, BIRD evidence를 보지 않는다.
- question에서 decision objective, target population, metric, calculation, granularity, scope, time basis, comparison, output expectation을 구조화한다.
- 명시되지 않은 의미는 임의 확정하지 않고 BIF의 assumption/field state에 보존한다.
- 동시에 가능한 의미 선택을 `IssueObservation`으로 관찰한다.
- observation에는 `issue_type`, `subject_ref`, `description`, `alternatives`만 쓴다.
- decision 생성, materiality, ownership, category, 질문, 종료는 결정하지 않는다.

### A2-DISCOVERY

- 현재 Draft BIF와 이미 확정된 ledger를 읽는다.
- 아직 질문하지 않은 새로운 업무 의미 선택만 관찰한다.
- DB 가용성, field 신뢰도, table/column 선택 문제는 system issue type으로 표시한다.
- 답변된 decision을 뒤집거나 다시 질문하지 않는다.

### A2-QUESTION

- Orchestrator가 선택한 DecisionSpec 하나만 비개발자용 질문으로 표현한다.
- 질문이 결과에 미치는 이유와 상호 배타적 option label을 만든다.
- ID, path, choice domain, 다음 질문, 종료를 바꾸지 않는다.

### E2-BIF

- 현재 canonical request 하나에만 답한다.
- Gold SQL은 evaluation oracle로만 사용하며 사용자에게 보이지 않는다.
- SQL 용어가 아니라 사용자의 업무 언어로 option을 선택하거나 필요한 경우 free text로 답한다.
- BIF를 수정하거나 다음 decision을 고르지 않는다.

### A3 Provisional BIF Materializer

- 현재 revision의 pending queue와 discovery audit가 안정화된 뒤 호출한다.
- 원 질문, untouched Draft BIF, Canonical Intent State, resolved ledger 전체를 함께 사용한다.
- 여러 decision이 같은 영역에 영향을 주어도 전체 의미가 일관되도록 Provisional BIF를 조립한다.
- Draft BIF의 condition atom, condition logic, metric, calculation, scope, time,
  comparison, output inventory는 권한 있는 기준선이다. A3는 ledger가 명시적으로
  허용한 효과 외에는 이를 병합·삭제·추가·재해석하지 않는다.
- DB schema 없이는 확정할 수 없는 condition 차원은 `deferred_to_sif` 상태를
  유지한다. A3가 schema 차원을 추측하거나 사용자에게 DB 구현 선택을 요구하지 않는다.
- round별 patch와 decision receipt 자기보고를 하지 않는다.
- 이후 validator와 Atomic Admission Gate가 canonical effect의 동등성 및 BIF
  atom·논리·출력 불변성을 검사한다.
- audit가 새 candidate를 만들면 clarification 후 새 revision을 다시 materialize할 수 있으며, final confirmation은 검증된 Provisional BIF 뒤에만 수행한다.

### A4 SIF Draft Constructor

- 이 단계에서 처음 전체 DB catalog를 사용한다.
- Confirmed BIF의 population, predicate, aggregation, grouping, ranking, comparison, output을 sources, joins, query blocks, expressions, intent paths에 연결한다.
- BIF 의미를 추가·삭제·변경하지 않는다.
- BIF condition atom 하나마다 독립적인 grounding slot 하나를 만들고,
  `deferred_to_sif` atom의 실제 차원·컬럼·코드 값을 schema에서 해결한다.
- 물리 mapping이 불가능하거나 불확실하면 unresolved grounding으로 남긴다.

### A5-DISCOVERY

- BIF 의미를 query semantics로 옮길 때 생긴 새로운 사용자 선택만 관찰한다.
- 예: record population의 업무적 정의, 집계 전후 선택, group별/전체 selection scope, query grain, 출력 의미.
- join path, cast, physical identifier, SQLite 문법, data availability/quality는 사용자 질문으로 만들지 않는다.
- deterministic source ambiguity detector의 후보를 중복 생성하지 않는다.

### A5-QUESTION

- Orchestrator가 지정한 SIF DecisionSpec 하나를 비개발자 업무 언어로 표현한다.
- table, column, JOIN, GROUP BY, CTE, SQL 함수명을 노출하지 않는다.
- “어느 테이블을 쓸까요?”가 아니라 “어느 업무 기록 집합을 의미합니까?”처럼 질문한다.

### E2-SIF

- 현재 SIF 의미 선택 하나에만 답한다.
- Gold SQL의 record population과 계산 의미를 canonical option에 연결한다.
- SQL 구현 설명이 아니라 비개발자 관점의 업무 정의로 답한다.
- SIF를 수정하거나 종료를 결정하지 않는다.

### A6 Provisional SIF Materializer

- 현재 SIF revision의 pending queue와 discovery audit가 안정화된 뒤 호출한다.
- Confirmed BIF, untouched Draft SIF, Canonical Query Intent State, resolved ledger, reviewed source candidates, schema catalog를 사용한다.
- sources, joins, predicates, aggregation, grain, query blocks, outputs를 함께 일관되게 Provisional SIF로 조립한다.
- BIF 의미를 변경하거나 decision receipt를 자기보고하지 않는다.
- 이후 validator가 canonical effect, BIF intent path, SIF traceability를 검사하고, BIF-level 누락이면 BIF_REENTRY를 요청한다.
- final confirmation은 검증된 Provisional SIF의 사용자용 의미 요약 뒤에만 수행한다.

### A7 SQL Generator

- Confirmed SIF만 semantic source로 사용한다.
- SIF에 없는 필터·집계·정렬·범위를 추가하지 않는다.
- read-only SQLite SELECT/CTE 하나만 출력한다.
- 우선 deterministic compiler를 사용하고 지원하지 않는 SIF 표현일 때만 모델 fallback을 사용한다.

### A8 SQL Verifier/Repairer

- SQL을 Confirmed SIF와 schema에 대해 검증한다.
- 생성 SQL을 실제 SQLite DB에서 read-only preflight 실행한다.
- syntax, alias, quoting, cast 등 구현 오류만 제한적으로 수정한다.
- 새로운 업무 의미를 결정하지 않는다. 새로운 material semantic choice가 발견되면 SIF clarification으로 반환한다.

## 5. Clarification Orchestrator 동작

### 5.1 후보 판정

1. 모델 출력 contract를 검증한다.
2. closed issue policy registry에 없는 type을 거절한다.
3. 현재 stage와 ownership을 policy로 결정한다.
4. 실제 Frame path catalog로 subject를 canonicalize한다.
5. 서로 다른 대안 두 개 이상이 있는지를 material choice 기준으로 검사한다.
6. semantic key와 stable decision ID를 만든다.
7. 중복 후보를 deduplicate한다.
8. 수용된 후보를 동적 pending queue에 등록한다.
9. 현재 intent revision과 dependency/priority를 기준으로 다음 decision 하나를 선택한다.

### 5.2 질문과 응답

1. Question Writer는 문구만 제안한다.
2. Orchestrator가 canonical request ID, gap ID, path, `OPTION_n`을 결합한다.
3. E2 또는 실제 사용자가 답한다.
4. Orchestrator가 request ownership과 option validity를 검증한다.
5. 선택 option의 canonical effect를 Canonical Intent State에 atomic하게 적용한다.
6. 현재 decision 하나를 resolved로 기록하고 revision을 증가시킨다.
7. pending queue 전체를 재평가하여 implication·conflict·merge·supersede·defer를 처리한다.
8. 필요할 때 새 discovery candidate를 추가하고 다음 질문의 priority를 다시 계산한다.
9. 이 시점에는 BIF/SIF 전체를 모델로 patch하지 않는다.

### 5.3 Audit와 종료

- pending/asked decision이 없더라도 현재 intent revision의 discovery audit가 끝나지 않으면 종료하지 않는다.
- audit에서 새 candidate가 나오면 queue에 추가하고 clarification을 계속한다.
- queue와 audit가 안정화되면 A3/A6가 Provisional Frame을 materialize한다.
- decision compliance와 semantic audit가 새 candidate를 만들면 queue로 돌아간다.
- 실제 Provisional Frame이 검증된 뒤에만 Orchestrator가 사용자용 의미 요약과 final confirmation을 만든다.
- 정확한 canonical final request에서 `CONFIRM`이 선택되어야 종료한다.
- `yes`, `none`, 모델이 만든 confirm ID, 문장의 “확인” 표현은 종료 신호가 아니다.

## 6. BIF와 SIF Layout

### BIF 주요 영역

```text
provenance
decision_objective
target_population
metrics
calculation
granularity
scope
time_basis
comparisons
output_expectation
assumptions
field_states
readiness
```

### SIF 주요 영역

```text
source_frame_id / database
query_blocks / main_block_id
sources / joins
where / having
projection / derived_fields
aggregation / group_by / windows
order_by / limit / tie policy
traceability / intent_paths
unresolved_grounding
status
```

BIF의 모든 material 의미는 SIF node의 `intent_paths` 또는 traceability에 연결되어야 한다.

## 7. 물리 Grounding 복구

Clarification 종료 후 SIF에 physical warning이 남으면 다음 순서로 처리한다.

1. deterministic grounding validator
2. deterministic repair 가능한 항목 복구
3. 여전히 남은 항목만 A6 system grounding repair 호출
4. complete query block/traceability/unresolved-grounding section 단위 transaction 적용
5. 재검증

이 transaction은 사용자 응답 patch가 아니다. status, IDs, source frame, database metadata, BIF 의미는 변경할 수 없다.

## 8. 실행과 평가

각 case에서 Gold SQL과 Agent SQL을 같은 SQLite DB에서 read-only로 실행한다.

```text
Gold SQL -> ReferenceExecutionRecord
Agent SQL -> GeneratedExecutionRecord
          -> column/row normalization
          -> execution-result equivalence
```

- Gold 실행 실패: `REFERENCE_EXECUTION_FAILED`, 기본 accuracy 분모에서 별도 처리
- Agent 실행 실패: `GENERATED_EXECUTION_FAILED`, 해당 case 실패
- 둘 다 성공: 결과의 row/column semantics를 기준으로 비교
- 문자열 SQL 동일성은 성공 기준이 아니다.

평가에는 최소 다음을 포함한다.

- execution accuracy
- difficulty별 accuracy
- BIF/SIF clarification round 수
- detected/resolved/pending material decisions
- redundant question count
- premature termination prevention count
- 모델 호출 수, latency, input/output token
- deterministic compiler/repair/model fallback 비율
- BIF/SIF/SQL 단계별 실패 attribution

## 9. 로그 구조

```text
logs/
  <run_id>/
    run_manifest.json
    <question_id>/
      case_input.json
      P00_load/
      P01_bif_analysis/
      P02_bif_clarification/
        round_001/
          issue_observations.json
          policy_decision_events.json
          canonical_decision.json
          canonical_request.json
          user_response.json
          decision_record.json
      P03_bif_assembly/
      P04_sif_draft/
      P05_sif_clarification/
      P06_sif_assembly/
      P07_grounding_validation/
      P08_sql_generation/
      P09_sql_preflight/
      P10_gold_execution/
      P11_generated_execution/
      P12_result_comparison/
      P13_metrics/
      P14_complete/
```

모든 모델 호출에는 agent ID, actual model, prompt version, attempt, timestamps, latency, token usage, finish reason, input/output hash, validation result, retry reason을 기록한다.

## 10. 모델 정책

- 모델 이름과 token limit는 설정으로 교체 가능하게 둔다.
- 현재 기본 실험 모델과 Sphinteract 비교 모델은 run manifest에 실제 model ID로 고정 기록한다.
- BIF 단계에는 DB schema를 넣지 않는다.
- A4와 A6 one-shot SIF assembly, system grounding처럼 필요한 단계에만 schema catalog를 제공한다.
- 단순히 호출 수를 줄이기 위해 의미 해석을 규칙으로 대체하지 않는다.
- 중복 호출, 제어 판정 호출, 매 응답 Frame patch 호출을 제거해 비용과 불안정성을 줄인다.

## 11. 구현 완료 조건

1. IssueObservation 모델 계약에 제어 필드가 없다.
2. Orchestrator 외부에서 decision ID·상태·순서·종료를 정하지 않는다.
3. A3/A6 사용자 응답별 patch 경로와 legacy fallback이 없다.
4. BIF/SIF final confirmation은 Orchestrator만 생성·승인한다.
5. one-shot assembly 뒤 decision invariant가 모두 통과한다.
6. system grounding과 user clarification이 코드·로그에서 분리된다.
7. Gold/Agent SQL이 실제 DB에서 실행되고 결과가 비교된다.
8. run ID/question ID/step 구조로 전 과정을 재현할 수 있다.
9. 로컬 contract·state·regression test가 모두 통과한다.
10. smoke 3건 후 difficulty 합산 30건을 동일 설정으로 실행할 수 있다.
11. BIF의 DB 미확정 atom은 condition-scoped 방식으로만 SIF에 이관되며, 관련 없는
    사용자 의미 clarification은 보존된다.
12. SIF는 BIF condition atom과 1:1 slot cardinality 및 원래 logic reference를
    유지하고, SQL lowering까지 동일 의미를 보존한다.
