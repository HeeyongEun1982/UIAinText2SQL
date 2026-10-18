# Q87 Actual vs Optimal BIF/SIF Diff 및 수정 설계

## 1. 비교 대상

- Optimal BIF: `optimal_bif.json`
- Optimal SIF: `optimal_sif.json`
- Actual BIF: `runs/sif_v2_q87_20260820_01/87/P02/frames/bif_confirmed.json`
- Actual SIF proposal: 같은 실행의 `P03/agents/a4/call_001/response.txt`
- Actual clarified SIF proposal: `P03/agents/a6/call_001/response.txt`

해당 실제 실행은 BIF 148 round, 전체 모델 317회 호출 후 첫 SIF clarification
slot admission에서 실패했다.

## 2. BIF Semantic Diff

| 영역 | Optimal | Actual | 판정 | 오류 소유 단계 |
|---|---|---|---|---|
| County | San Bernardino county | 동일 | 일치 | - |
| City | City = San Bernardino | District = San Bernardino City Unified | 중대 의미 변경 | A1/clarification choice domain |
| Intermediate/Middle | 독립 atom, 차원은 SIF 이관 | 독립 atom이나 임의의 `confirmed_separate_dimension...` 차원 생성 | 부분 일치 | A3 |
| Unified Schools | 독립 atom, 차원은 SIF 이관 | 독립 atom이나 임의 차원 생성 | 부분 일치 | A3 |
| Population logic | 네 조건 `all_of` | 최종 Frame은 `all_of` | 표면상 일치 | - |
| Logic provenance | `all_of` 결정과 Frame 일치 | ledger는 `any_of`, Frame은 `all_of` | 치명적 상태 모순 | Orchestrator/materializer |
| Opening period | `time_basis`에서 한 번 표현 | population condition과 `time_basis`에 중복 | Layout 중복 | A1/BIF schema usage |
| Metric | 기록된 관리자 이메일, 검증 없음 | 최종 정의는 대체로 동일 | 일치 | - |
| Calculation | 계산 단계 없음 | retrieve step 추가 | 불필요한 연산 | A1/A3 |
| NULL policy | 지정 없음 | NULL/missing 제외 | 사용자 미요청 의미 추가 | A1/A3 |
| Duplicate policy | 지정 없음 | unique만 포함 | 사용자 미요청 의미 추가 | A1/A3 |
| Granularity | matching school record | administrator email + school dimension/grouping | 의미 과잉 변환 | A1/A3 |
| Output | 논리적 복수 이메일, separate columns | 동일한 출력 의도 | 일치 | - |
| Assumptions | 없음 | `requires_confirmation` 2개가 남아 있으나 ready | 상태 모순 | readiness gate |

### 2.1 Location 오류의 정확한 발생 위치

A1은 원문의 다음 부분을 하나의 atom으로 소비했다.

```text
City of San Bernardino City Unified
```

Clarification은 다음 두 선택지만 제공했다.

1. city와 district로 분리
2. combined name으로 유지

Optimal 의미인 다음 선택지가 없었다.

```text
City = San Bernardino로 보존하고,
Unified Schools는 이미 존재하는 별도 classification atom에서 처리한다.
```

따라서 이 오류는 E2가 주어진 정답을 잘못 선택한 것이 아니라
`choice_domain_recall = 0`인 clarification 생성 오류다.

### 2.2 Logic 반복의 정확한 상태

- 동일 `question_logic_mismatch`가 materialization에서 71회 반복됐다.
- revision 1~71에서는 deterministic split repair가 적용되지 않았다.
- revision 72에서야 split repair가 적용됐다.
- 최종 ledger의 LOGIC_RELATION decision은 `OPTION_2 = any_of`다.
- 최종 BIF는 두 독립 조건의 `all_of`다.

즉 동일한 canonical decision을 Frame이 따르지 않는데도 ready가 되었다. 반복 round의
문제보다 더 근본적으로 **Canonical State, ledger, materialized Frame의 삼자
동등성 검사가 없다**.

## 3. SIF Semantic Diff

| BIF 의미/slot | Optimal grounding | Actual A4 grounding | 판정 |
|---|---|---|---|
| primary school | `schools` | `schools` | 일치 |
| County | `County = San Bernardino` | 동일 | 일치 |
| City | `City = San Bernardino` | `District = San Bernardino City Unified` | BIF 오류 전파 |
| Intermediate/Middle | `SOC = 62` | `SOCType = Intermediate/Middle Schools (Public)` | 의미상 근접, 기준 grounding 불일치 |
| Unified Schools | `DOC = 54` | `SOCType = K-12 Schools (Public)` | 중대 오 grounding |
| Opening period | OpenDate year 2009~2010 | target condition range와 period slot 모두 생성 | 중복 + lowerer 미지원 |
| Metric/output | `AdmEmail1`, `AdmEmail2` | 동일 | 일치 |
| Granularity | 별도 physical grouping 없음 | `CDSCode` dimension grounding | 불필요한 GROUP BY 위험 |
| FRPM join | Gold provenance상 존재하나 결과에 영향 없음 | 없음 | execution-equivalent, 비중대 |

### 3.1 Schema 정보 부족이 아니다

A4 request에는 다음 코드북이 이미 포함됐다.

```text
SOC 62 = Intermediate/Middle Schools (Public)
DOC 54 = Unified School District
```

따라서 `SOC=62`, `DOC=54`를 찾지 못한 이유는 schema slicing이나 token 누락이
아니다. 자유 생성 모델에게 전체 catalog에서 즉시 최종 binding을 쓰게 한 grounding
방식의 실패다.

### 3.2 SIF clarification ownership 오류

A5는 `Unified Schools`의 의미를 사용자에게 다시 물었다. 그러나 이 사례에서
사용자는 이미 business term을 확정했다. 어느 column/code가 이를 구현하는지는
schema/codebook이 해결할 system-owned 문제다.

A6는 E2 응답 후 다음 존재하지 않는 binding을 생성했다.

```text
schools."School Type" = 'Unified Schools'
```

Admission Gate가 이를 거절한 것은 정상이다. 하지만 invalid column 후보가 E2
질문 뒤에 생성된 것 자체가 잘못이다. 물리 후보는 사용자 질문 전에 schema
validation을 통과해야 한다.

### 3.3 실행 의미 비교

A4 proposal을 그대로 `all_of`로 해석하면 같은 `SOCType` column에 서로 다른 두
값을 동시에 요구한다.

```sql
SOCType = 'Intermediate/Middle Schools (Public)'
AND SOCType = 'K-12 Schools (Public)'
```

읽기 전용 DB 실행 결과는 다음과 같다.

```text
Actual A4 의미: 0 rows
Optimal 의미  : 1 row
                 a.lucero@realjourney.org
                 j.hernandez@realjourney.org
```

따라서 Actual SIF는 단순히 Gold와 다른 표현을 사용한 equivalent query가 아니라,
결과 집합을 공집합으로 만드는 의미 오류다.

## 4. BIF 수정 방향

### 4.1 BIF를 `Source Atom + Interpretation` 구조로 변경

각 condition은 최소한 다음을 분리한다.

```json
{
  "condition_id": "...",
  "source_span": {
    "text": "Unified Schools",
    "start": 0,
    "end": 0
  },
  "business_term": "Unified Schools",
  "interpretation": {
    "business_dimension": null,
    "status": "deferred_to_sif"
  },
  "operator": "equals",
  "value": "Unified Schools"
}
```

원문 span과 해석을 같은 필드에서 관리하지 않는다. 모델이 해석을 바꾸더라도
source span과 atom identity는 변경할 수 없다.

### 4.2 Span Consumption Invariant

1. 질문의 material span은 기본적으로 한 atom만 소비한다.
2. 동일 span을 두 atom이 공유하려면 명시적인 `shared_span_reason`이 필요하다.
3. 이미 `Unified Schools` atom이 존재하면 location parser가 `Unified`를 다시
   district atom 생성에 사용할 수 없다.
4. 새로운 atom을 만들 때 source span이 없으면 Admission Gate가 거절한다.

이 규칙이 Q87의 잘못된 district condition 생성을 직접 차단한다.

### 4.3 Business section의 단일 소유 위치

- Population conditions: entity를 포함/제외하는 비시간 업무 조건
- Time basis: 기간과 시간 의미
- Metric: 측정·반환할 업무 값
- Calculation: 실제 derive/aggregate/compare 연산만
- Granularity: 결과 한 행이 의미하는 업무 단위
- Output: 사용자에게 보이는 논리적 필드와 presentation

따라서 time을 population condition에 중복 저장하지 않고, `retrieve`를 calculation
step으로 만들지 않는다. 질문에 없는 NULL/DISTINCT 정책의 기본값은 `null`이다.

### 4.4 Clarification Choice Completeness Gate

Orchestrator가 E2에게 질문하기 전에 각 choice domain을 검사한다.

필수 option 유형:

1. 후보 해석 A
2. 후보 해석 B
3. 원문 atom을 그대로 보존하고 physical meaning을 SIF로 이관

세 번째 option이 논리적으로 가능한데 누락되면 질문하지 않는다. A2에게 choice
domain 재생성을 요청한다. 실험 지표에 `correct_option_inclusion_rate`를 추가한다.

### 4.5 BIF Materializer 권한

A3가 전체 BIF를 자유롭게 다시 쓰지 않게 한다.

```text
A1: source span과 atom 후보 추출
Orchestrator/E2: user-owned interpretation만 canonical state에 확정
Deterministic BIF Assembler: atom + canonical effects로 Frame 생성
A3: 사용자용 summary 작성 또는 구조화가 어려운 국소 interpretation 제안
Admission Gate: source atom, logic, section ownership, no-enrichment 검사
```

즉 A3는 Frame의 semantic authority가 아니다.

### 4.6 Ready 불변식

BIF ready 전에 다음을 모두 만족해야 한다.

```text
canonical_state == resolved_ledger_effects
canonical_state == materialized_frame_semantics
requires_confirmation assumptions == 0
unresolved_user fields == 0
same semantic finding fingerprint is not repeating
```

같은 `(frame_hash, finding_fingerprint, ledger_hash)`가 두 번 반복되면 새 E2 질문을
만들지 않는다. 새로운 정보가 있는 round 수는 제한하지 않되, 동일 상태 cycle은
즉시 구조 오류로 종료한다.

## 5. SIF 수정 방향

### 5.1 Candidate Generation과 Selection 분리

A4가 최종 binding을 한 번에 자유 생성하지 않는다.

```text
Schema Candidate Generator (deterministic)
  -> 실제 table/column만 후보화
  -> column description/codebook label과 BIF term을 매칭
  -> top-k 후보 + 근거 + score

Grounding Selector
  -> 후보 중 선택
  -> unique high-confidence면 system_resolved
  -> business 의미 선택이 필요한 경우에만 Orchestrator/E2

Slot Admission
  -> column 존재
  -> code value 존재/문서화
  -> BIF atom 1:1
  -> 다른 slot 불변
```

### 5.2 Codebook Resolver

문서화된 code/value pair를 구조화된 index로 미리 만든다.

```text
"Intermediate/Middle Schools (Public)" -> schools.SOC = "62"
"Unified School District"             -> schools.DOC = "54"
```

정규화 요소:

- singular/plural
- word order
- punctuation와 괄호
- public/private modifier
- school/district/authority synonym은 동일시하지 않고 별도 semantic role로 보존

Q87에서는 두 mapping이 서로 다른 column에서 독립적으로 높은 점수를 받아야 한다.
두 atom을 한 `SOCType IN (...)`으로 병합할 수 없다.

### 5.3 User-owned와 System-owned gap 분리

| gap | owner |
|---|---|
| 어떤 학교 집단을 뜻하는가 | user/E2 |
| Unified라는 업무 용어를 유지할 것인가 | user/E2 |
| Unified를 DOC와 SOC 중 어디에 연결하는가 | system/schema |
| DOC에서 어떤 code가 Unified인가 | system/codebook |
| join path와 SQL 함수 | system/compiler |

system-owned gap에는 E2를 호출하지 않는다. 후보가 부족하면 SIF를 blocked로 만들고
grounding 진단을 남긴다.

### 5.4 Period Grounding과 Lowering

`time_basis.periods`를 population condition으로 복제하지 않는다. SIF period slot이
다음을 완전히 표현해야 한다.

```text
source column: schools.OpenDate
temporal role: event time
transformation: year extraction
range: 2009 through 2010 inclusive
```

Lowerer는 이를 SQLite의 `strftime('%Y', OpenDate) BETWEEN '2009' AND '2010'`으로
변환한다. date-component가 필요 없는 DB에서는 직접 date range로 낮출 수 있다.

### 5.5 Output Multi-binding

BIF의 하나의 논리적 복수 field를 SIF의 여러 physical column에 연결할 수 있어야
한다. `AdmEmail1`, `AdmEmail2`는 별도 projection이지만 하나의 BIF output field를
추적한다. sibling column 확장은 description과 usefulness metadata로 결정하고,
질문에 없는 `AdmEmail3`을 자동 추가하지 않는다.

### 5.6 Minimal Physical Plan 원칙

Gold SQL에 `frpm` join이 있으나 Q87에서는 join 제거 SQL과 결과가 동일했다.
사용자 의도와 결과 동등성에 필요하지 않은 join을 SIF가 억지로 재현할 필요는 없다.

평가를 분리한다.

- semantic/execution equivalence: 중요
- exact SQL structure match: 진단용
- benchmark-only hidden convention: 별도 태그

## 6. 구현 우선순위

### P0 — 반복·상태 모순 차단

1. canonical state/ledger/Frame 삼자 동등성 validator
2. repeated semantic fingerprint cycle detector
3. `requires_confirmation` assumption이 남은 ready Frame 거절

### P1 — BIF 의미 보존

1. source span 필드와 span-consumption invariant
2. time/calculation/granularity section ownership validator
3. clarification choice completeness gate
4. deterministic BIF assembler

### P2 — SIF grounding 정확도

1. schema/codebook candidate index
2. deterministic top-k grounding 후보 생성
3. physical candidate pre-validation
4. system-owned gap의 E2 호출 금지

### P3 — SQL 연결 완성

1. time period/date-component lowerer
2. logical output multi-binding projection
3. minimal source/join planner

### P4 — 평가

1. BIF semantic diff
2. SIF grounding diff
3. choice-domain inclusion 평가
4. SQL execution comparison
5. Q2, Q76, Q87 reference regression 후 30개 smoke test

## 7. 권장 평가 지표

| Layer | Metric |
|---|---|
| BIF | source atom precision/recall |
| BIF | logic accuracy |
| BIF | section placement accuracy |
| Clarification | material gap recall |
| Clarification | correct option inclusion rate |
| E2 | selection accuracy given correct option |
| Clarification | induced intent mutation rate |
| SIF | slot coverage |
| SIF | table/column/value grounding accuracy |
| SIF | invalid physical candidate rate |
| SIF | unnecessary user clarification rate |
| SQL | execution accuracy |
| Runtime | unique clarification rounds, repeated cycles, model calls, tokens, latency |

Q87의 현재 결과는 E2 정확도 하나로 설명할 수 없다. BIF atomization, choice-domain
coverage, canonical-state consistency, codebook grounding, lowerer coverage를 분리해
측정해야 어느 구성요소를 고쳐야 하는지 정확히 알 수 있다.
