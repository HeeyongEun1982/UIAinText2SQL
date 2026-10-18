# Q87 Question/Gold SQL 기준 Frame 분석

## 기준

- Question은 사용자 업무 의도의 근거다.
- Gold SQL은 개발·평가 단계에서 최적 BIF/SIF를 역검증하는 oracle로만 사용한다.
- 실제 Agent 실행에서는 Gold SQL이나 BIRD evidence를 입력으로 제공하지 않는다.

Gold SQL의 의미는 다음과 같다.

```sql
SELECT T2.AdmEmail1, T2.AdmEmail2
FROM frpm AS T1
INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
WHERE T2.County = 'San Bernardino'
  AND T2.City = 'San Bernardino'
  AND T2.DOC = 54
  AND strftime('%Y', T2.OpenDate) BETWEEN '2009' AND '2010'
  AND T2.SOC = 62
```

## 확정 의미

| 업무 의미 | BIF | SIF |
|---|---|---|
| San Bernardino county | 독립 condition | `schools.County = 'San Bernardino'` |
| City of San Bernardino | 독립 condition | `schools.City = 'San Bernardino'` |
| public Intermediate/Middle Schools | 독립 deferred condition | `schools.SOC = '62'` |
| Unified Schools | 독립 deferred condition | `schools.DOC = '54'` |
| opened 2009-01-01 through 2010-12-31 | `time_basis` | `strftime('%Y', OpenDate) BETWEEN '2009' AND '2010'` |
| administrator e-mail addresses | 검증 규칙 없는 기록값 | `AdmEmail1`, `AdmEmail2` 두 출력 컬럼 |

모든 population 조건은 `all_of`다. `San Bernardino City Unified`라는 학군명을
별도 필터로 추가해서는 안 된다. `Unified Schools`는 SIF에서 DOC 분류로 연결된다.

## 현재 실행과의 차이

### 1. A1의 phrase segmentation 오류

현재 Draft BIF는 `City of San Bernardino City Unified`를 하나의
`location_city_and_district` atom으로 만들었다. 이후 clarification option도
“city와 district로 분리” 또는 “combined name 유지”만 제공했다.

Gold 기준의 올바른 후보인 다음 선택지가 없었다.

> City는 San Bernardino로 유지하고, Unified Schools는 이미 별도의 school
> classification atom이므로 location district 조건을 추가하지 않는다.

따라서 E2가 틀린 답을 한 것이 아니라 **정답 option이 choice domain에 없었다**.

### 2. BIF와 SIF ownership 혼합

`City Unified`가 DB의 city인지 district인지 묻는 방식은 사용자 의도
clarification과 schema grounding을 섞는다. BIF는 원문의 business phrase와
독립 atom만 보존해야 한다. City/County의 명시적 의미와 두 school classification
atom을 만든 뒤, SOC/DOC 판단은 SIF가 수행해야 한다.

### 3. “valid e-mail” 과잉 해석

현재 E2는 syntactically valid and non-null을 선택했지만 Gold SQL에는 syntax 검사나
NULL 제거가 없다. 기준 BIF에서는 “기록된 관리자 이메일 값”으로 정의하고 별도의
validation semantics를 두지 않는다. 이 사례에서 `valid`는 새로운 검증 연산을
발명할 근거가 아니다.

### 4. 출력 multiplicity

Gold는 `AdmEmail1`, `AdmEmail2`만 별도 컬럼으로 출력한다. BIF에는 하나의 논리적
복수 필드와 `separate_columns` presentation을 두고, SIF가 두 물리 binding으로
확장하는 것이 적절하다. `AdmEmail3`가 schema에 존재해도 Gold는 사용하지 않는다.
이는 질문만으로 확정하기 어려운 benchmark-specific coverage다.

### 5. 숨은 record-population 제약

Gold는 `frpm`을 inner join하지만 해당 테이블의 컬럼을 필터나 출력에 사용하지
않는다. 이 join은 질문에 나타나지 않은 “FRPM record가 존재하는 학교” 제약을
만든다. evidence를 사용하지 않는 실제 환경에서는 자동 복원이 보장되지 않는다.

Q87 DB에서 읽기 전용으로 확인한 결과, Gold SQL과 `frpm` join을 제거한 SQL은
모두 같은 이메일 2개를 가진 동일한 1행을 반환했다. 따라서 이 사례의 execution
result에는 join이 영향을 주지 않지만, SQL 구조 비교 관점에서는 숨은 benchmark
관례로 남는다.

이 항목은 BIF에 넣으면 안 된다. SIF의 benchmark-only physical support로 표시하고,
논문 평가에서는 다음을 별도로 보고해야 한다.

- user-intent correctness
- benchmark Gold execution equivalence
- hidden dataset convention dependency

### 6. 현재 SIF v2 lowerer의 기능 공백

최적 BIF는 기간을 `time_basis`에 한 번만 표현한다. 현재 lowerer는
`time_basis.periods`를 fail-closed 처리하며 WHERE predicate로 내리지 못한다.
기간을 population condition에 중복 기록해야만 컴파일되는 현재 방식은 Layout
왜곡이다.

## 개발 설계

### Phase A — Reference Frame 평가기

1. `optimal_bif.json`, `optimal_sif.json`을 evaluation-only fixture로 읽는다.
2. Agent BIF와 reference BIF를 atom, logic, metric, time, output 단위로 비교한다.
3. Agent SIF와 reference SIF를 source, join, condition binding, period binding,
   output binding 단위로 비교한다.
4. 전체 JSON 문자열 유사도가 아니라 stable ID 기반 semantic diff를 기록한다.

### Phase B — BIF 생성 개선

1. phrase span을 먼저 보존하고 의미를 나중에 붙인다.
2. 같은 원문 span을 city와 district 두 조건으로 복제하지 못하게 한다.
3. 이미 별도 atom으로 존재하는 `Unified Schools`를 location phrase 해석에 다시
   소비하지 못하게 span-consumption invariant를 둔다.
4. clarification choice domain에 `preserve explicit atom and defer physical meaning`
   option을 의무적으로 포함한다.
5. Gold reference와 비교해 choice-domain recall을 측정한다.

### Phase C — Clarification 평가 개선

E2 정답률만 평가하지 않고 다음을 분리한다.

- gap detection recall
- correct option inclusion rate
- E2 selection accuracy conditional on correct option being present
- unnecessary clarification rate
- clarification-induced intent mutation rate

Q87의 location 실패는 E2 selection failure가 아니라 correct option inclusion failure다.

### Phase D — SIF grounding 개선

1. BIF atom 하나당 grounding slot 하나를 유지한다.
2. `public Intermediate/Middle Schools`와 `Unified Schools`를 독립적으로 schema와
   value description에 연결한다.
3. SIF가 BIF atom을 location district로 재해석하거나 추가 condition을 만들지 못하게
   한다.
4. output의 논리적 복수 필드를 `AdmEmail1`, `AdmEmail2`처럼 여러 physical column에
   연결하는 coverage policy를 명시한다.

### Phase E — Lowerer 개선

1. `time_basis.period_groundings`를 WHERE expression으로 lowering한다.
2. `date_component` transformation과 inclusive range를 지원한다.
3. time condition을 target population에 중복 저장하지 않는다.
4. hidden benchmark join은 user intent와 분리된 provenance로 기록한다.

## 권장 구현 순서

1. Reference Frame schema validation과 semantic diff 도구
2. Q87 reference regression test
3. span-consumption invariant와 choice-domain completeness validator
4. SIF period grounding 및 lowerer
5. Q87을 다시 실행하고 BIF/SIF/SQL 세 단계의 점수를 각각 비교
6. 같은 방식으로 Q2, Q76 reference frame을 추가한 뒤 일반화 여부 확인
