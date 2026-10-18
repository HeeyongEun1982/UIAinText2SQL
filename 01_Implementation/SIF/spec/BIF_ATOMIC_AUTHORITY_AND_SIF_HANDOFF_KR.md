# BIF 원자 권한과 SIF Grounding 이관 명세

## 목적

이 명세는 사용자의 업무 목적을 표현하는 BIF와 DB 구현을 표현하는 SIF의 경계를
고정한다. 핵심 원칙은 **BIF가 무엇을 요구했는지를 보존하고, SIF가 그것을 어느
테이블·컬럼·코드로 구현할지만 결정한다**는 것이다.

## BIF 계약

1. 질문에서 AND로 연결된 서로 다른 명사구는 각각 독립적인 condition atom으로
   보존한다.
2. DB schema 없이는 정확한 업무 차원을 확정할 수 없는 atom은 추측하지 않는다.
   `semantic_dimension=null`, `semantic_dimension_status=deferred_to_sif`,
   `grounding_requirement=schema`로 표시한다.
3. 각 atom은 원문을 추적할 수 있도록 `source_text`와 `business_concept`를 가진다.
4. 질문에 없는 출력, 정렬, DISTINCT, 유효성 규칙, 단순 retrieve 계산 단계는
   BIF에 추가하지 않는다.
5. A3는 clarification ledger를 반영할 수 있지만, condition inventory·condition
   logic·metric·calculation·scope·time·comparison·output intent를 임의로 다시
   해석할 권한이 없다.
6. A3 결과는 Atomic Admission Gate를 통과해야 하며, atom 병합·삭제·추가,
   논리 변경, 출력 확장이 발견되면 동결하지 않는다.

## Clarification 경계

- 도시인지 조직인지, AND인지 OR인지, 어떤 결과를 원하는지처럼 사용자가 답할 수
  있는 의미 선택은 BIF clarification 대상이다.
- 특정 표현이 SOC인지 DOC인지, 어떤 코드 값인지처럼 schema가 답해야 하는 문제는
  E2 사용자에게 묻지 않고 SIF로 이관한다.
- 한 condition이 deferred라는 이유로 다른 location clarification까지 제거해서는
  안 된다. 이관은 해당 condition path를 직접 가리키는 observation에만 적용한다.

## SIF 계약

1. BIF condition atom 하나마다 SIF grounding slot 하나를 결정적으로 생성한다.
2. SIF는 BIF의 condition ID, 값, operator와 logic reference를 변경하지 않는다.
3. 각 deferred atom은 전체 schema와 문서화된 값 설명을 사용해 독립적으로
   table·column·physical value에 연결한다.
4. 하나의 slot proposal은 다른 slot을 병합·삭제·수정할 수 없다.
5. 모든 필수 slot이 해결되고 Admission Gate와 physical validator를 통과한 경우에만
   SIF를 ready로 만든다.
6. deterministic lowerer는 확인된 slot과 BIF logic으로 SQL을 생성한다. 지원하지
   않는 의미는 목표를 바꾸어 근사하지 않고 fail closed 한다.

## Q87 기대 동작

```text
BIF atom A: Public Intermediate/Middle Schools
BIF atom B: Unified Schools
BIF logic : A AND B

SIF grounding A -> schools.SOC = '62'
SIF grounding B -> schools.DOC = '54'

SQL predicate  : SOC = '62' AND DOC = '54'
output         : 질문에서 요구한 관리자 이메일만
```

BIF는 SOC와 DOC를 알 필요가 없다. 반대로 SIF는 두 표현을 하나의
`school.type IN (...)` 조건으로 합칠 수 없다.

## 검증 및 로그

- BIF 정규화: `bif_initial_atomic_authority_repairs.json`
- BIF에서 SIF로 이관된 observation: 각 BIF round의
  `deferred_observations.json`
- A3 권한 복구 및 Admission Gate 이벤트: `BIF_ATOMIC_AUTHORITY_APPLIED`,
  `BIF_ATOMIC_ADMISSION_REJECTED`
- SIF slot proposal, admission, readiness, lowering 및 최종 SQL은 기존
  run ID/question ID/step 구조에 기록한다.

