# Clarification Orchestrator 명세 v2.0

이 문서는 BIF/SIF clarification 제어의 단일 normative specification이다.

동적 pending queue, Canonical Intent State, 답변 후 reconciliation, BIF/SIF re-entry의 구현 상세는 [`DYNAMIC_CLARIFICATION_ORCHESTRATOR_DESIGN_KR.md`](./DYNAMIC_CLARIFICATION_ORCHESTRATOR_DESIGN_KR.md)를 따른다. 기존의 고정 pending 순회 설명과 충돌하면 동적 설계가 우선한다.

## 1. 절대 규칙

Clarification의 모든 최종 결정은 결정론적 `ClarificationOrchestrator`만 수행한다.

- 관찰 후보의 수용·거절·system defer
- issue type의 stage, category, ownership, materiality
- canonical subject path, semantic key, decision ID
- 중복 제거, 질문 순서, request ID, option ID
- 상태 전이, discovery audit, final confirmation, 종료
- 확정 decision ledger와 assembly receipt

모델은 의미를 관찰하거나 지정된 내용을 자연어로 표현할 뿐이다. 모델이 만든 ID, category, materiality, ownership, path 목록, 상태, 순서, 종료 신호는 제어 입력으로 사용할 수 없다.

## 2. 역할과 권한

| 구성요소 | 허용 기능 | 금지 기능 |
|---|---|---|
| A1 | 사용자 질문만으로 Draft BIF와 `IssueObservation[]` 생성 | DB/Gold SQL 사용, decision·질문·종료 생성 |
| A2-DISCOVERY | BIF의 추가 `IssueObservation[]` 제안 | materiality·ownership·category·ID·상태·종료 결정 |
| A2-QUESTION | 지정된 BIF `DecisionSpec`을 비개발자용 질문으로 표현 | 다른 issue 선택, path·option 의미 변경 |
| E2-BIF | 현재 canonical request 하나에 사용자 역할로 응답 | Frame 수정, 다음 질문·종료 선택 |
| A3 | 종료 후 Draft BIF와 frozen ledger로 한 번 조립 | round별 patch, decision receipt 자기보고 |
| A4 | Confirmed BIF와 DB catalog로 Draft SIF 생성 | BIF 의미 변경 |
| A5-DISCOVERY | BIF→query 의미의 추가 `IssueObservation[]` 제안 | 물리 구현 문제를 사용자 결정으로 전환, 제어 결정 |
| A5-QUESTION | 지정된 SIF `DecisionSpec`을 비개발자용 질문으로 표현 | 다른 issue 선택, SQL 용어 노출 |
| E2-SIF | 현재 canonical request 하나에 사용자 역할로 응답 | SIF 수정, 다음 질문·종료 선택 |
| A6 | 종료 후 Draft SIF와 frozen ledger로 한 번 조립 | round별 patch, BIF 의미 변경 |
| Orchestrator | 모든 control-plane 결정과 ledger 관리 | 질문의 자연어 문장 작성, SQL 생성 |

## 3. IssueObservation 계약

Discovery 모델이 낼 수 있는 구조는 아래 네 필드뿐이다.

```json
{
  "candidates": [{
    "issue_type": "SCOPE_SELECTION",
    "subject_ref": "/scope",
    "description": "최솟값의 적용 범위에 따라 대상이 달라질 수 있다.",
    "alternatives": ["전체 대상에서 한 번 선택", "각 그룹에서 따로 선택"]
  }]
}
```

`additionalProperties=false`이므로 다음 제어 필드는 계약 단계에서 거절한다.

```text
decision_id, gap_id, request_id, category, material, ownership,
status, ordering, affected_paths, next_action, final_confirmation
```

## 4. Closed Issue Policy Registry

Orchestrator는 임의 issue type을 허용하지 않고 고정 registry로 판정한다.

```text
BIF user decision:
  DECISION_OBJECTIVE, TARGET_POPULATION, POPULATION_CONDITION_STRUCTURE, LOCATION_ROLE,
  METRIC_DEFINITION, CALCULATION_METHOD, LOGIC_RELATION,
  GRANULARITY, SCOPE_SELECTION, TIME_BASIS, COMPARISON, OUTPUT_SHAPE

SIF user decision:
  QUERY_GRAIN, AGGREGATION_PLACEMENT, QUERY_SELECTION_SCOPE,
  QUERY_TIME_SEMANTICS, QUERY_COMPARISON_SEMANTICS,
  QUERY_OUTPUT_SEMANTICS, SOURCE_POPULATION

System-owned/deferred:
  PHYSICAL_GROUNDING, DATA_AVAILABILITY, DATA_QUALITY, SQL_IMPLEMENTATION
```

Orchestrator의 판정 순서는 고정한다.

1. issue type이 registry에 없으면 거절한다.
2. policy stage가 현재 BIF/SIF stage와 다르면 owner stage로 defer한다.
3. `subject_ref`가 현재 Frame path catalog에 있는지 검사한다.
4. 유효하지 않으면 policy의 canonical path로 치환한다.
5. 서로 다른 대안이 두 개 이상일 때만 material user choice로 수용한다.
6. category와 ownership은 policy에서만 가져온다.
7. issue type, canonical path, canonical alternatives로 semantic key와 ID를 만든다.
8. 같은 semantic key는 새 decision이 아니라 dedup event로 기록한다.

## 5. Canonical DecisionSpec과 질문

Orchestrator가 수용한 관찰만 다음 구조가 된다.

```json
{
  "decision_id": "BIF-BUSINESS_CALCULATION-9A2D31C487",
  "stage": "bif",
  "category": "BUSINESS_CALCULATION",
  "issue": "SCOPE_SELECTION",
  "affected_paths": ["/scope"],
  "choice_domain": ["전체 대상에서 한 번 선택", "각 그룹에서 따로 선택"],
  "status": "pending",
  "policy_version": "1.0"
}
```

Question sub-function은 질문, 이유, option label, `allow_free_text`만 제안한다. Orchestrator가 이를 canonical decision에 다시 결합하며 `request_id`, `gap_id`, `affected_paths`, `OPTION_n`을 직접 생성한다.

## 6. 상태와 종료

```text
observed -> policy accepted -> pending -> asked -> resolved
                               |  |  |      \-> blocked
                               |  |  \-> resolved_by_implication
                               |  \-> merged / superseded
                               \-> invalidated / deferred_to_sif / system_owned
```

응답 하나는 현재 `asked` decision 하나만 resolve한다. 응답 중에는 BIF/SIF를 수정하지 않는다.

```text
needs_audit = pending=0 AND asked=0
              AND audited_revision != resolution_revision

ready_for_materialization = pending=0 AND asked=0
                            AND audited_revision=resolution_revision

ready_for_final_confirmation = ready_for_materialization
                               AND provisional_frame_valid=true
                               AND decision_compliance_valid=true

can_terminate = ready_for_final_confirmation
                AND exact canonical final confirmation accepted
```

final confirmation은 Orchestrator가 만든 정확한 request만 허용한다. `CONFIRM`만 종료 신호이며, `REVISE`는 business-language correction이 있을 때 새 intent revision으로 재진입한다. `yes`, `accept`, 모델이 만든 confirm ID, 질문 문장의 확인 표현은 종료 신호가 아니다.

## 7. Provisional materialization

```text
Draft BIF + Canonical Intent State + resolved ledger -> A3 -> Provisional BIF
Draft SIF + Canonical Query Intent State + resolved ledger -> A6 -> Provisional SIF
```

A3/A6는 응답 round마다 Frame을 patch하지 않는다. queue와 audit가 안정화된 materialization checkpoint에서만 호출하며, audit가 새 decision을 만든 경우 다음 revision을 다시 materialize할 수 있다. 모델 출력에는 `applied_decision_ids`를 받지 않는다. Orchestrator가 ledger에서 receipt를 만들고 다음을 검사한다.

- 선택된 canonical effect와 Provisional BIF/SIF의 실제 의미가 동등
- `assert_absent`로 확정한 의미가 Frame에 존재하지 않음
- SIF decision이 SIF path 또는 BIF `intent_paths`/traceability에 연결
- A6가 BIF 의미를 변경하지 않음

검증된 Provisional Frame의 사용자용 요약을 E2가 확인한 뒤에만 Confirmed BIF/SIF로 고정한다.

물리 grounding 복구는 clarification 종료 뒤 별도 system-owned SIF section transaction으로만 수행한다. 이는 사용자 의미 결정 경로가 아니다.

## 8. E2의 고정 역할

E2는 evaluation에서 사람을 대신하는 답변자다. 현재 canonical request의 선택지를 Gold SQL의 의도와 연결해 한 질문에만 응답한다. E2는 issue 발견, decision 생성, 질문 선택, Frame 수정, 종료를 할 수 없다. 실제 운영에서는 같은 인터페이스의 실제 사용자 응답으로 교체한다.

## 9. 로그

각 run/question/stage/round에 최소 다음 artifact를 남긴다.

```text
issue_observations.json
policy_decision_events.json
canonical_decision.json
canonical_request.json
user_response.json
decision_record.json
discovery_audit.json
clarification_state.json
decision_ledger.json
assembly_invariants.json
```

모델 원문과 Orchestrator 판정 결과를 분리 저장하여 어떤 후보가 왜 수용·거절·defer·dedup되었는지 재현할 수 있어야 한다.

## 10. 구조 검증 기준

1. production code에 A2/A5 GapReport 또는 종료 판정 메서드가 없어야 한다.
2. production code에 사용자 응답별 A3 BIF patch/A6 SIF patch가 없어야 한다.
3. discovery schema가 control field를 거절해야 한다.
4. 모델이 제시한 ID·category·ownership·materiality를 Orchestrator가 무시해야 한다.
5. unknown issue type은 decision을 만들지 못해야 한다.
6. system-owned issue는 BIF/SIF user decision이 되지 못해야 한다.
7. 문구가 달라도 semantic key가 같으면 중복 질문하지 않아야 한다.
8. discovery audit 전에는 final confirmation을 만들지 못해야 한다.
9. Orchestrator가 만들지 않은 final request와 정확하지 않은 option은 종료시키지 못해야 한다.
10. Gold SQL과 generated SQL의 실제 DB 실행 결과 및 전체 과정 로그를 계속 보존해야 한다.
