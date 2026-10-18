# Dynamic Clarification Orchestrator 상세 개발 설계 v1.0

## 1. 목적

이 문서는 BIF와 SIF clarification을 동적 pending queue로 관리하는 구현 명세다. 목표는 질문을 미리 모두 발견해 queue에 넣을 수 있으면서도, 각 사용자 응답 뒤 Orchestrator가 queue와 현재 의미 상태를 다시 평가하여 충돌·중복·불필요한 질문을 제거하고 새 질문을 추가하도록 만드는 것이다.

이 문서에서 `Orchestrator`는 LLM Agent가 아니라 결정론적 control-plane 서비스다. 의미 관찰과 자연어 표현은 모델 sub-function이 지원할 수 있지만, 후보 수용, 상태 변경, 충돌 판정, 다음 질문, 종료, BIF/SIF 확정은 Orchestrator만 결정한다.

## 2. 절대 불변조건

1. 모든 관찰 후보를 초기 pending queue에 등록할 수 있다.
2. pending queue는 고정 FIFO가 아니라 매 응답 뒤 재계산되는 동적 queue다.
3. E2 응답은 현재 `asked` decision 하나에만 적용한다.
4. 응답은 BIF/SIF 전체를 직접 patch하지 않고 canonical semantic slot에 atomic effect로 적용한다.
5. 응답 적용 뒤 모든 pending candidate를 재평가한다.
6. 이미 해결된 후보는 `resolved_by_implication`, 충돌한 후보는 `invalidated`, 대체된 후보는 `superseded`, 중복 후보는 `merged` 처리한다.
7. 재평가 또는 discovery audit에서 새 material issue가 발견되면 queue에 추가한다.
8. BIF가 정확히 확정되기 전에는 SIF로 진행하지 않는다.
9. SIF는 Confirmed BIF를 직접 수정할 수 없다.
10. SIF에서 BIF 수준의 누락이 발견되면 Orchestrator가 BIF revision을 재개방하고 SIF를 처음부터 재생성한다.
11. 실제로 조립된 Provisional BIF/SIF를 검증하기 전에는 final confirmation을 요청하지 않는다.
12. path 존재만으로 decision 반영을 인정하지 않고 선택된 canonical effect와 실제 Frame 의미의 동등성을 검사한다.

## 3. 전체 구조

```text
Question
  -> A1 Draft BIF + IssueObservation[]
  -> BIF Dynamic Clarification Loop
  -> A3 Provisional BIF Materialization
  -> BIF Decision Compliance + Semantic Audit
  -> E2 Final BIF Confirmation
  -> Confirmed BIF revision N
  -> A4 Draft SIF
  -> SIF Dynamic Clarification Loop
  -> A6 Provisional SIF Materialization
  -> SIF Decision Compliance + BIF Trace Audit
  -> E2 Final SIF Meaning Confirmation
  -> Confirmed SIF
  -> SQL generation, execution, Gold execution-result comparison
```

하나의 `IntentClarificationOrchestrator`가 전체 lifecycle을 소유하고, 내부에서 BIF와 SIF stage controller를 사용한다.

## 4. 구성요소 책임

| 구성요소 | 책임 | 결정할 수 없는 것 |
|---|---|---|
| A1 | Draft BIF와 최초 의미 관찰 생성 | decision ID, materiality, ownership, queue, 종료 |
| A2-DISCOVERY | 현재 BIF intent state에서 새 관찰 제안 | 후보 수용, 기존 후보 제거, 다음 질문 |
| A2-QUESTION | 지정된 BIF DecisionSpec을 사용자 질문으로 표현 | semantic slot, effect, option ID 변경 |
| E2-BIF | 현재 canonical request에 사용자 역할로 응답 | Frame 수정, queue 관리, 종료 |
| A3 | Draft BIF와 canonical intent state로 Provisional BIF materialize | 새 decision 생성, 응답 재해석 |
| A4 | Confirmed BIF를 Draft SIF로 grounding | BIF 의미 변경 |
| A5-DISCOVERY | 현재 SIF/query intent state에서 새 관찰 제안 | user/system ownership 최종 판정, 종료 |
| A5-QUESTION | 지정된 SIF DecisionSpec을 비개발자 질문으로 표현 | SQL 용어 노출, effect 변경 |
| E2-SIF | 현재 query semantic request에 사용자 역할로 응답 | SIF 수정, BIF 수정 |
| A6 | Draft SIF와 canonical query intent state로 Provisional SIF materialize | BIF 의미 변경, 새 decision 생성 |
| Orchestrator | policy, slot, effect, queue, conflict, state, audit, confirmation, termination | 질문 문구 창작, SQL 생성 |

## 5. 핵심 데이터 모델

### 5.1 IssueObservation

모델 sub-function이 제안할 수 있는 최소 구조다.

```json
{
  "issue_type": "LOCATION_ROLE",
  "subject_ref": "/target_population/inclusion_conditions/1",
  "description": "The phrase may identify a city or a district.",
  "alternatives": ["city", "district"]
}
```

이 구조는 decision이 아니다. ID, materiality, ownership, category, state, ordering, termination을 포함할 수 없다.

### 5.2 DecisionCandidate

Orchestrator가 closed policy registry를 적용하여 만든 내부 구조다.

```json
{
  "decision_id": "BIF-BUSINESS_INTENT-...",
  "stage": "bif",
  "issue_type": "LOCATION_ROLE",
  "semantic_slot": "target_population.location.primary_role",
  "subject_paths": ["/target_population/inclusion_conditions/1"],
  "question_basis": "The location phrase has two business interpretations.",
  "options": [
    {
      "option_id": "OPTION_1",
      "label_seed": "Interpret it as a city",
      "canonical_effect": {
        "set": {"target_population.location.city": "San Bernardino"},
        "unset": ["target_population.location.district"]
      }
    },
    {
      "option_id": "OPTION_2",
      "label_seed": "Interpret it as a district",
      "canonical_effect": {
        "set": {"target_population.location.district": "San Bernardino City Unified"},
        "unset": ["target_population.location.city"]
      }
    }
  ],
  "dependencies": [],
  "status": "pending",
  "priority": 100,
  "policy_version": "dynamic-1.0"
}
```

### 5.3 CanonicalEffect

허용 operation은 의미 상태에 대한 작은 집합으로 제한한다.

```text
set(slot, value)
unset(slot)
append_unique(collection_slot, value)
replace_collection(collection_slot, values)
set_relation(relation_slot, relation)
assert_absent(slot)
```

모델이 JSON Pointer patch나 전체 Frame replacement를 반환하게 하지 않는다. Effect는 issue policy compiler가 생성하고 Orchestrator가 검증한다.

### 5.4 CanonicalIntentState

```json
{
  "stage": "bif",
  "revision": 4,
  "assertions": {
    "target_population.location.county": "San Bernardino",
    "target_population.location.city": "San Bernardino",
    "target_population.location.district": {"state": "absent"},
    "target_population.classification.logic": "all_of",
    "output.administrator_email.presentation": "separate_columns"
  },
  "assertion_provenance": {
    "target_population.location.city": {
      "source": "E2",
      "decision_id": "BIF-BUSINESS_INTENT-...",
      "request_id": "...-Q1"
    }
  },
  "conflicts": [],
  "materialization_revision": 0
}
```

### 5.5 QueueRecord

```json
{
  "decision_id": "...",
  "status": "resolved_by_implication",
  "status_reason": "semantic_slot already fixed by decision D1",
  "related_decision_ids": ["D1"],
  "created_revision": 0,
  "last_evaluated_revision": 2,
  "asked_round": null,
  "resolved_round": 1
}
```

## 6. Queue 상태 모델

```text
observed
  -> rejected
  -> deferred_to_sif
  -> system_owned
  -> pending

pending
  -> asked
  -> merged
  -> invalidated
  -> superseded
  -> resolved_by_implication

asked
  -> resolved
  -> blocked

resolved
  -> remains resolved
  -> reopened only by explicit BIF revision rollback
```

`pending` 이외 상태는 다음 질문 선택 대상이 아니다. 모든 상태 변경은 reason과 관련 decision ID를 기록한다.

## 7. Semantic Slot Registry

path가 아니라 의미 slot을 중복·충돌 판단의 기본 단위로 사용한다.

### BIF slot 예시

```text
decision.objective
target_population.entity
target_population.location.county
target_population.location.city
target_population.location.district
target_population.classification.<classification_id>.dimension
target_population.classification.logic
metric.<metric_id>.definition
metric.<metric_id>.additional_validation
calculation.method
granularity.unit
scope.selection
time.start
time.end
comparison.direction
output.<field_id>.inclusion
output.<field_id>.multiplicity
output.<field_id>.presentation
```

### SIF slot 예시

```text
query.population.record_definition
query.grain
query.selection_scope
query.aggregation.placement
query.time.semantics
query.comparison.semantics
query.output.shape
query.source_population.business_definition
```

table, column, join, cast, dialect, physical code는 semantic slot이 아니라 system grounding 영역이다.

## 8. 후보 수용과 선택지 완전성

Orchestrator는 observation을 pending으로 등록하기 전에 다음을 검사한다.

1. closed issue type인가
2. 현재 stage에서 user-answerable인가
3. 실제 사용자 결과를 바꿀 material choice인가
4. semantic slot을 canonicalize할 수 있는가
5. 선택지가 상호 배타적인가
6. 가능한 해석의 중요한 기준 선택지가 빠지지 않았는가
7. 선택지가 사용자 질의에 없는 추가 제약만으로 구성되지 않았는가
8. 이미 확정된 assertion으로 해결됐는가

특히 추가 validation/filter를 제안하는 후보에는 기본 의미를 보존하는 선택지가 반드시 있어야 한다.

```text
AS_STATED_OR_RECORDED
NO_ADDITIONAL_CONSTRAINT
```

Q87의 `valid email`에는 최소 다음 선택지가 필요하다.

```text
1. 데이터에 관리자 이메일로 기록된 값을 그대로 사용
2. 형식이 올바르고 비어 있지 않은 값만 사용
3. 외부 검증까지 완료된 값만 사용
```

기본 의미 선택지가 없으면 candidate를 질문하지 않고 `incomplete_choice_domain`으로 거절하거나 option policy로 보완한다.

## 9. 답변 적용 Transaction

```text
function apply_response(decision_id, response):
    assert decision.status == asked
    assert response.request_id == decision.active_request_id
    assert selected options are canonical

    begin transaction
      effect = decision.options[selected].canonical_effect
      validate effect against slot policy
      apply effect to CanonicalIntentState
      mark decision resolved
      increment intent revision
      reconcile entire queue
      run deterministic implication rules
      run discovery audit if required
      reprioritize pending queue
    commit transaction
```

어느 단계든 실패하면 state와 queue를 transaction 시작 전 상태로 복구한다.

## 10. Queue Reconciliation 알고리즘

각 resolved response 뒤 모든 pending candidate에 다음 규칙을 순서대로 적용한다.

### R1. Exact semantic slot satisfaction

candidate가 묻는 slot이 이미 같은 값으로 확정됐으면:

```text
status = resolved_by_implication
```

### R2. Parent-child semantic containment

`output.email`과 `output.email.presentation`처럼 path가 다르더라도 같은 최종 slot을 묻는 후보는 merge 또는 implication 처리한다.

### R3. Conflict invalidation

candidate의 모든 option effect가 현재 assertion과 충돌하면:

```text
status = invalidated
```

### R4. Partial option pruning

일부 option만 충돌하면 충돌 option을 제거한다. 남은 material option이 두 개 미만이면 질문하지 않고 implication 또는 invalidation으로 처리한다.

### R5. Superseding question

기존 질문 basis가 더 이상 유효하지 않지만 새로운 material choice가 생기면 기존 candidate를 `superseded` 처리하고 새 candidate를 등록한다.

### R6. Dependency activation

상위 decision이 특정 값으로 해결된 후에만 의미가 생기는 후보를 pending으로 활성화한다.

### R7. System/deferred routing

갱신된 의미 상태에서 candidate가 physical mapping 문제로 판명되면 `system_owned`, SIF에서 다룰 query semantic 문제면 `deferred_to_sif`로 전환한다.

### R8. Deduplication

다음 canonical signature가 같으면 하나만 유지한다.

```text
stage + semantic_slot + normalized option effects
```

질문 문구와 JSON path는 signature에 사용하지 않는다.

## 11. 질문 선택 정책

queue reconciliation 뒤 pending candidate의 priority를 다시 계산한다.

```text
priority =
  downstream_impact
  + dependency_unlock_count
  + conflict_resolution_value
  + user_answerability
  - cognitive_cost
```

우선순위 원칙:

1. population과 scope처럼 downstream 구조를 바꾸는 decision
2. 다른 질문을 제거하거나 활성화하는 decision
3. metric/calculation 의미
4. output shape
5. 독립적인 presentation 세부사항

한 round에는 하나의 atomic semantic slot만 질문한다.

## 12. Discovery 정책

모든 initial observation을 queue에 등록할 수 있다. 추가 discovery는 다음 시점에 수행한다.

```text
- 답변 effect가 둘 이상의 downstream slot을 무효화했을 때
- dependency가 활성화됐을 때
- pending queue가 비었을 때
- Provisional Frame audit가 새 material issue를 발견했을 때
- SIF grounding이 BIF 의미 누락을 발견했을 때
```

비용 절감을 위해 매 응답마다 모델 discovery를 호출하지 않는다. 먼저 결정론적 reconciliation과 implication을 수행하고, 위 조건에 해당할 때만 A2/A5 discovery를 호출한다.

## 13. BIF Dynamic Clarification Loop

```text
1. A1 Draft BIF + initial observations
2. deterministic BIF semantic findings 생성
3. 모든 observation/finding을 policy registry로 평가
4. 수용된 후보를 pending queue에 등록

repeat:
  5. queue reconciliation
  6. pending candidate가 있으면 가장 우선순위가 높은 하나 선택
  7. A2-QUESTION은 해당 DecisionSpec의 문구만 생성
  8. Orchestrator가 canonical request 생성
  9. E2 응답
 10. response transaction 적용
 11. 필요할 때만 discovery audit

until pending=0 and audit_current_revision=true

12. A3 Provisional BIF materialization
13. Decision Compliance Validator
14. BIF Semantic Validator
15. 실패 finding을 candidate/system correction으로 routing
16. 새 candidate가 있으면 loop 재진입
17. 통과하면 사용자용 BIF summary 생성
18. E2 final BIF confirmation
19. Confirmed BIF freeze
```

## 14. Provisional BIF Materialization

A3 입력:

```text
original question
untouched Draft BIF
CanonicalIntentState assertions
resolved Decision Ledger
explicit absent assertions
```

A3는 새로운 의미를 선택하지 않는다. Canonical assertions를 BIF layout으로 materialize한다.

### BIF compliance 검사

- assertion별 실제 BIF 값 일치
- `assert_absent` slot이 BIF에 존재하지 않음
- unresolved conflict 없음
- 질문에 없는 output field 없음
- 질문/decision에 없는 filter·validation 없음
- 독립 condition이 하나의 dimension으로 병합되지 않음
- simultaneous condition이 OR/IN으로 변경되지 않음
- output multiplicity와 presentation consistency
- 모든 material field의 provenance 존재

path 존재만 검사하는 현재 invariant를 이 검사로 대체한다.

## 15. BIF Final Confirmation

final confirmation은 Provisional BIF 검증 뒤에만 수행한다. generic confirmation 문구만 보여주지 않고 실제 의미를 사용자 언어로 요약한다.

```text
대상, 위치, 기간, 분류 조건, 계산, 출력 항목, 추가 validation 여부
```

응답:

```text
CONFIRM -> Confirmed BIF freeze
REVISE  -> correction description을 observation으로 변환하여 queue 재진입
```

## 16. SIF Dynamic Clarification Loop

SIF는 Confirmed BIF의 `frame_id`, revision, hash를 immutable source로 사용한다.

```text
1. A4 Draft SIF
2. deterministic source ambiguity와 query semantic observation 생성
3. SIF policy registry로 pending queue 등록
4. BIF와 동일한 response/reconciliation loop 수행
5. A6 Provisional SIF materialization
6. Decision Compliance + BIF Trace + Grounding validation
7. 사용자 query semantics issue는 queue 재진입
8. physical issue는 system grounding
9. BIF-level omission은 BIF_REENTRY
10. 검증 통과 뒤 user-facing query summary final confirmation
11. Confirmed SIF freeze
```

## 17. SIF에서 BIF로 복귀

다음 조건이면 SIF가 BIF를 고치지 않고 상위 Orchestrator에 re-entry를 요청한다.

```text
- BIF의 두 condition이 실제로 같은 업무 차원인지 다른 차원인지 결정되지 않음
- metric 정의가 query semantics를 결정하기에 불충분함
- target population의 업무적 record definition이 누락됨
- comparison 방향이나 time basis가 BIF에 없음
```

복귀 절차:

```text
1. 현재 SIF revision을 abandoned로 기록
2. Confirmed BIF를 revision N+1 clarification 상태로 재개방
3. BIF_REENTRY observation을 pending queue에 추가
4. BIF loop와 final confirmation 수행
5. 새 Confirmed BIF freeze
6. 기존 Draft/Provisional SIF를 재사용하지 않고 A4부터 재생성
```

## 18. E2 계약

E2는 다음만 수행한다.

- 현재 canonical request 하나에 응답
- 제공된 option 중 사용자 의도에 맞는 option 선택
- 선택지가 불완전하면 business-language free text 제공
- SQL이나 DB 물리 구조를 설명하지 않음

E2는 다음을 수행하지 않는다.

- queue 전체 검토
- 과거 응답과 충돌 해결
- 다음 질문 선택
- BIF/SIF 수정
- final termination 결정

과거 응답과 queue 충돌 해결은 전적으로 Orchestrator 책임이다.

## 19. 로그 구조

각 response transaction에 다음 artifact를 저장한다.

```text
round_N/
  queue_before.json
  selected_decision.json
  canonical_request.json
  user_response.json
  canonical_effect.json
  intent_state_before.json
  intent_state_after.json
  reconciliation_events.json
  queue_after.json
  discovery_observations.json       # 호출했을 때만
  transaction_result.json
```

reconciliation event 예시:

```json
{
  "event": "candidate_invalidated",
  "decision_id": "D4",
  "reason": "conflicts_with_resolved_assertion",
  "conflicting_slots": ["target_population.location.district"],
  "caused_by_decision_id": "D1"
}
```

Provisional materialization마다 다음을 저장한다.

```text
provisional_bif.json / provisional_sif.json
decision_compliance.json
semantic_audit.json
new_candidates.json
user_facing_summary.json
final_confirmation.json
```

## 20. 실패와 종료 상태

```text
READY
BLOCKED_USER_CANNOT_ANSWER
BLOCKED_NO_COMPLETE_CHOICE_DOMAIN
CONFLICT_UNRESOLVED
MATERIALIZATION_FAILED
DECISION_COMPLIANCE_FAILED
BIF_REENTRY_REQUIRED
SYSTEM_GROUNDING_FAILED
OPTIONAL_BUDGET_EXHAUSTED
SYSTEM_ERROR
```

`pending=0`만으로 READY가 될 수 없다. READY는 queue, audit, materialization, compliance, semantic validation, final confirmation을 모두 통과해야 한다.

## 21. Q87 목표 시뮬레이션

초기 queue:

```text
D1 location role
D2 classification relation
D3 output presentation
D4 city/district separate condition
D5 valid email definition
D6 duplicate output presentation
```

예상 transition:

```text
D1 = city
  -> assertion district=absent
  -> D4 invalidated

D2 = simultaneous
  -> two independent business classification dimensions

D3 = separate_columns
  -> D6 resolved_by_implication

D5 = as_recorded
  -> additional_validation=none
  -> null_filter=not_requested
```

Provisional BIF 기대조건:

```text
County=San Bernardino
City=San Bernardino
District absent
Open date 2009-01-01 through 2010-12-31
Intermediate/Middle classification AND Unified organizational classification
administrator emails as recorded
output=email fields only
no School ID
```

Provisional SIF 기대조건:

```text
County='San Bernardino'
City='San Bernardino'
SOC=62
DOC=54
OpenDate in requested period
projection AdmEmail1, AdmEmail2
exclude AdmEmail3 because documented not useful
no email NOT NULL predicates unless explicitly requested
no District predicate
```

## 22. 구현 모듈 방향

```text
services/
  intent_state.py                 # CanonicalIntentState와 transaction
  semantic_slot_registry.py       # BIF/SIF slot 정의
  decision_policy_registry.py     # observation -> DecisionCandidate/effect
  dynamic_decision_queue.py       # queue 상태와 priority
  queue_reconciler.py             # R1~R8
  option_domain_validator.py      # 선택지 완전성·기본 의미 검사
  decision_compliance.py          # effect와 materialized Frame 동등성
  stage_router.py                 # BIF/SIF/system/BIF_REENTRY routing

pipeline/
  clarification_orchestrator.py   # 전체 lifecycle
  bif_clarification_loop.py
  sif_clarification_loop.py
```

기존 `ClarificationOrchestrator`의 stable ID, request binding, response validation, audit logging 기능은 재사용하되, 고정 pending list와 path-only invariant는 교체한다.

## 23. 구현 단계

### Phase 1. 계약과 상태

- semantic slot registry
- CanonicalEffect schema
- CanonicalIntentState
- 확장된 queue status와 event
- transaction rollback

### Phase 2. BIF 동적 queue

- initial bulk registration 유지
- response 후 전체 reconciliation
- implication/conflict/merge/supersede/defer
- priority 재계산
- 조건부 discovery audit

### Phase 3. BIF materialization gate

- A3 Provisional BIF
- decision compliance validator
- unrequested output/filter validator
- actual BIF summary final confirmation
- Q87 BIF regression

### Phase 4. SIF 동적 queue

- CanonicalQueryIntentState
- SIF reconciliation
- user/system/BIF_REENTRY routing
- A6 Provisional SIF compliance

### Phase 5. 전체 회귀와 평가

- Q87
- Q2 simple
- Q76 moderate
- 세 case batch isolation
- difficulty-stratified smoke 30

## 24. 필수 단위 테스트

1. 초기 후보를 모두 pending에 등록할 수 있다.
2. 한 답변이 다른 pending candidate를 `resolved_by_implication`으로 제거한다.
3. 한 답변과 충돌하는 candidate가 `invalidated`된다.
4. parent-child path가 같지 않아도 semantic slot이 같으면 merge된다.
5. dependency 해결 뒤 새 candidate가 활성화된다.
6. discovery audit가 새 candidate를 추가할 수 있다.
7. 기본 의미 선택지가 없는 추가 validation 후보를 거절한다.
8. 응답 effect 적용 실패 시 state와 queue가 rollback된다.
9. pending=0이어도 materialization 전에는 종료되지 않는다.
10. selected effect와 BIF 값이 다르면 compliance가 실패한다.
11. Provisional BIF에 unrequested output이 있으면 실패한다.
12. SIF는 Confirmed BIF를 수정하지 못한다.
13. SIF의 BIF-level gap은 BIF_REENTRY를 발생시킨다.
14. BIF revision 후 SIF가 A4부터 재생성된다.
15. final confirmation은 실제 materialized summary 이후에만 가능하다.

## 25. Q87 회귀 완료조건

- conflicting location question이 둘 다 resolved로 남지 않는다.
- district condition이 BIF와 SIF에 존재하지 않는다.
- output presentation 질문은 한 번만 수행된다.
- `valid` clarification에는 `as recorded/no additional validation` 선택지가 존재한다.
- Confirmed BIF에 School ID output이 없다.
- 두 classification은 별도 business dimension과 `all_of`로 표현된다.
- SIF에 SOC=62와 DOC=54가 각각 한 번만 존재한다.
- SIF에 `SOC IN (62,65)`가 없다.
- AdmEmail3가 projection에 없다.
- 사용자 요청 없는 email NOT NULL predicate가 없다.
- final confirmation artifact가 Provisional BIF/SIF summary를 포함한다.
- Gold SQL과 generated SQL의 SQLite 실행 결과가 equivalent다.

## 26. 성능 정책

- initial observations는 한 번에 생성하고 bulk queue registration한다.
- queue reconciliation은 코드로 수행한다.
- Question Writer는 실제로 질문할 decision에만 호출한다.
- `resolved_by_implication`, `merged`, `invalidated` candidate에는 E2를 호출하지 않는다.
- 모델 discovery는 조건부 audit 시점에만 호출한다.
- A3/A6는 per-response patch가 아니라 materialization checkpoint에서 호출한다.
- BIF에는 DB schema를 제공하지 않는다.
- SIF materialization과 system grounding에만 필요한 schema catalog를 제공한다.

## 27. 개발 완료 정의

다음 조건을 모두 만족해야 동적 Orchestrator 구현이 완료된 것으로 본다.

1. queue가 응답 뒤 제거·병합·대체·추가될 수 있다.
2. 모든 queue transition에 결정론적 reason이 있다.
3. CanonicalIntentState가 Frame과 분리되어 있다.
4. 모델이 queue/status/effect/termination을 결정하지 않는다.
5. final confirmation이 materialization과 compliance 뒤에 수행된다.
6. BIF 오류가 SIF에서 조용히 보정되지 않고 BIF_REENTRY로 처리된다.
7. Q87 BIF/SIF 구조 회귀와 execution equivalence가 통과한다.
8. Q2·Q76 기존 성공 case가 회귀하지 않는다.
9. 전체 과정이 run/question/stage/round별 artifact로 재현된다.
