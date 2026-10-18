# BIF v1.1 논리 정규화 명세

## 목적

BIF는 사용자 질의를 요약하는 문서가 아니라, DB 구조를 사용하지 않은 상태에서 SIF로 전달 가능한 수준으로 논리 정규화된 사용자 의도 계약이다. DB의 테이블, 컬럼, 코드, 조인은 SIF에서만 결정한다.

## Layout 변경

- 각 population condition은 하나의 `semantic_dimension`만 표현한다.
- `inclusion_logic`과 `exclusion_logic`은 `all_of`, `any_of`, `none_of` 및 condition reference로 조건 관계를 명시한다.
- `value_mode`는 `scalar`, `range`, `set`, `none`, `custom`을 구분한다.
- `set_relation`은 집합 값의 `any_of`, `all_of` 의미를 보존한다.
- output field는 `value_multiplicity`와 `coverage`로 단일 값, 복수 값, 모든 요청 값, 모든 가용 값을 구분한다.

## Agent 실행 규칙

1. A1은 county, city, organization, classification, time 등 서로 다른 차원을 별도 atomic condition으로 분리한다.
2. 서로 다른 business dimension의 값을 하나의 `IN` condition으로 합치지 않는다.
3. 질문의 AND/OR를 condition logic에 보존한다.
4. A1 출력 후 결정론적 semantic validator를 수행한다.
5. 복합 위치, AND와 any-of의 불일치, logic reference 오류, 복수 출력 손실이 있으면 A2의 mandatory finding으로 전달한다.
6. finding이 남아 있으면 BIF를 `ready`로 확정하거나 A4로 전달하지 않는다.
7. A3는 사용자 응답을 반영하면서 atomic conditions, condition logic, output multiplicity를 함께 수정한다.
8. `simultaneous`처럼 구조적 의미가 확정된 응답은 A3 재해석 없이 atomic condition split과 logic reference 갱신을 코드로 수행한다.
9. SIF validator는 BIF의 `all_of`/`any_of`와 SIF predicate tree의 AND/OR가 일치하는지 검사한다.
10. BIF `all_of`가 SIF에서 OR로 변환되면 SQL 생성 전에 결정론적으로 복구한다.
11. operand가 하나뿐인 AND/OR wrapper는 malformed SIF로 탐지하고 안전하게 축약한다.
12. condition `value`에는 사용자가 명시한 전체 business category를 보존한다. 예를 들어 `Lunch Provision 2`를 `2`로 축약하지 않는다.
13. 같은 컬럼에서 질문과 일치하는 DB 값이 서로 포함 관계이면 가장 긴 전체 표현을 exact match로 사용한다.
14. 서로 다른 `semantic_dimension`의 BIF `all_of` 조건을 SIF predicate 하나가 동시에 대표하면 병합 오류로 탐지한다.
15. 각 조건과 정확히 대응하는 컬럼이 하나씩 식별될 때만 병합 predicate를 결정론적으로 분리하고, 그렇지 않으면 system grounding으로 보낸다.

## Q87 적용

Q87의 `Public Intermediate/Middle School`과 `Unified School District`는 별도 business dimension의 동시 조건으로 표현한다. BIF에는 `SOC`, `DOC`, `AdmEmail1`, `AdmEmail2`를 기록하지 않는다. BIF는 “모든 가용 관리자 이메일 주소”라는 사용자 출력 의도까지만 확정하며, 실제 코드와 컬럼 연결은 SIF가 담당한다.

## Q76 적용

Q76의 `Lunch Provision 2`, `lowest grade = 9`, `highest grade = 12`는 각각 완전한 business condition으로 보존한다. SIF는 뒤의 두 조건을 하나의 grade-span predicate로 축약할 수 없다. 질문과 DB에 `Provision 2`와 `Lunch Provision 2`가 모두 부분 일치하더라도 전체 사용자 표현인 `Lunch Provision 2`를 우선한다.

## 로그와 지표

- `P01/frames/bif_semantic_findings_initial.json`
- `P02/frames/bif_rounds/round_NN/semantic_findings_after_response.json`
- `BIF_SEMANTIC_FINDINGS_DETECTED`
- `BIF_SEMANTIC_FINDINGS_REMAIN`
- `P14/evaluation/metrics.json`의 `bif_schema_version`, `bif_semantic_findings_initial`, `bif_semantic_findings_final`
