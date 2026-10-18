# SIF 2.0: BIF 정렬형 Grounded Intent Layout

## 구현 보완 기록 (2026-08-20)

- A4는 한 번의 batch 호출로 여러 grounding을 제안하지만 Admission Gate는
  slot별 원자적 transaction으로 승인한다. 한 slot의 오류가 정상 slot을
  되돌리지 않는다.
- A5는 unresolved grounding ID 중 비개발자가 답할 수 있는 의미 선택만
  observation으로 제출한다. table·column·join·code 선택은 사용자에게 묻지 않는다.
- Orchestrator가 pending queue, 질문 순서, writable grounding ID, 응답 commit,
  재-audit 및 종료를 단독으로 소유한다.
- A6는 Orchestrator가 선택한 unresolved slot만 제안하며 모든 제안은 A4와 동일한
  Admission Gate를 통과한다.
- `run-case --sif-layout v2`가 clarification부터 deterministic SQL compile,
  preflight, Gold SQL execution comparison과 artifact logging까지 연결한다.
- aggregate와 기본 산술 comparison은 BIF calculation/comparison에서 immutable
  implementation descriptor를 결정적으로 생성한 뒤 lowering한다. A4/A6는 이를
  수정할 수 없다.
- 아직 정확히 lowering할 수 없는 window·group-wise selection·subquery·set operation은
  모델 fallback을 사용하지 않고 capability gate에서 차단한다.

## 1. 목적

SIF 2.0은 BIF를 SQL 형태로 재서술하지 않는다. Confirmed BIF의 각 논리 노드를
그대로 참조하고, DB에서 필요한 물리 binding만 추가한다.

```text
Confirmed BIF
  -> deterministic SIF scaffold
  -> slot-level physical grounding
  -> SIF 2.0 invariant validation
  -> deterministic Query Plan lowering
  -> SQL compiler
```

기존 SIF 1.0의 `query_blocks`, `where`, `projection`, `group_by`는 SIF의 사용자
의미가 아니라 내부 Query Plan IR로 취급한다. 모델은 이 IR을 직접 작성하거나
교체하지 않는다.

## 2. 권위와 변경 범위

| 정보 | 권위 | SIF 변경 허용 |
|---|---|---|
| condition ID와 개수 | BIF | 금지 |
| all_of/any_of/none_of | BIF | 금지 |
| metric·calculation·comparison | BIF | 금지 |
| granularity·output expectation | BIF | 금지 |
| table·column·join | SIF grounding | 허용 |
| physical code·datatype·cast | SIF grounding | 허용 |
| subquery·window 등 내부 구현 | SIF physical support | 허용, origin ref 필수 |

## 3. BIF와 SIF의 구조 대응

| BIF 1.1 | SIF 2.0 |
|---|---|
| `target_population.primary_entity` | `target_population.primary_entity_grounding.entity_ref` |
| `target_population.*_conditions[]` | `target_population.*_condition_groundings[].condition_ref` |
| `target_population.*_logic` | `target_population.*_logic_ref` |
| `metrics[].metric_id` | `metrics.metric_groundings[].metric_ref` |
| `calculation.steps[].step_id` | `calculation.step_groundings[].step_ref` |
| `granularity.dimensions[]` | `granularity.dimension_groundings[].dimension_ref` |
| `scope.selections[].selection_id` | `scope.selection_groundings[].selection_ref` |
| `time_basis.periods[]` | `time_basis.period_groundings[].period_ref` |
| `comparisons[].comparison_id` | `comparisons.comparison_groundings[].comparison_ref` |
| `output_expectation.fields[].field_id` | `output_expectation.field_groundings[].field_ref` |

## 4. 허용 transformation

초기 허용 목록은 다음과 같다.

- `direct`
- `code_mapping`
- `range`
- `aggregation`
- `derived_expression`
- `window`
- `subquery`
- `set_operation`
- `type_cast`
- `date_component`

자유형 SQL과 `custom` transformation은 허용하지 않는다. 하나의 BIF 노드는 여러
물리 노드로 확장될 수 있지만, 서로 독립적인 여러 BIF condition을 하나의 binding으로
병합할 수 없다.

## 5. 필수 불변조건

1. `source_bif.frame_id`, revision, hash가 실행 중인 Confirmed BIF와 일치해야 한다.
2. 모든 material BIF condition은 정확히 하나의 condition-grounding record를 가진다.
3. 각 condition-grounding record는 하나의 `condition_ref`만 소유한다.
4. SIF에는 AND/OR를 다시 정의하는 필드가 없으며 BIF logic ref만 보관한다.
5. 모든 grounding ref는 실제 BIF 안정 ID를 참조해야 한다. 배열 인덱스는 사용하지 않는다.
6. output grounding은 BIF output field만 참조한다.
7. `include_supporting_values=false`이면 physical support 컬럼을 output으로 노출할 수 없다.
8. 모든 source, join, 내부 구현 노드는 하나 이상의 `origin_refs`를 가진다.
9. 허용 목록 밖의 transformation은 거부한다.
10. unresolved grounding이 남아 있으면 SQL lowering을 차단한다.

## 6. Agent 권한

- Scaffold Builder는 Confirmed BIF에서 빈 grounding slot을 deterministic하게 만든다.
- A4는 각 slot의 물리 후보만 제안한다.
- A5는 unresolved grounding과 사용자 의미에 영향을 주는 선택만 발견한다.
- Orchestrator만 질문, queue, lock, 종료를 결정한다.
- E2는 승인된 사용자 판단에만 비개발자 언어로 응답한다.
- A6는 전체 SIF를 생성하지 않고 승인된 slot patch만 제안한다.
- Query Plan Lowerer와 SQL Compiler는 deterministic component다.

모든 A4/A6 제안은 다음 Admission Gate를 통과해야 한다.

```text
request/hash/allowed-slot 검사
  -> JSON Schema 검사
  -> BIF node coverage와 logic-ref 검사
  -> table/column/join 물리 검사
  -> BIF operator/value 의미 동등성 검사
  -> documented code mapping 검사
  -> output field/source metric binding 일치 검사
  -> 선택적으로 SQLite literal-domain 검사
  -> slot commit
```

모델은 `proposed` 상태까지만 만들 수 있다. `confirmed` 또는 `system_resolved`로의
전환은 Admission Gate만 수행한다. Orchestrator가 지정하지 않은 grounding ID와 이미
확정된 grounding ID에 대한 제안은 거부한다.

## 7. 단계적 이행

기존 SIF 1.0 실행 경로는 SIF 2.0 검증 기간 동안 유지한다. SIF 2.0 lowerer가 생성한
Query Plan IR을 기존 SQL compiler에 전달함으로써 compiler를 재사용한다. 새 경로가
구조 회귀와 난이도별 smoke test를 통과한 후 기본 경로로 전환한다.

## 8. 현재 구현 상태

- 구현: SIF 2.0 Schema, deterministic scaffold, 구조·물리·의미 validator
- 구현: slot proposal/commit Admission Gate와 A4/A6 공통 coordinator
- 구현: direct/range condition, BIF logic tree, source/join, direct output의 기본 lowerer
- 구현 중: aggregate, derived calculation, comparison, ranking, subquery, set operation lowerer
- 미전환: 기존 `run-case`의 기본 SIF 1.0 경로. SIF 2.0 전체 lowering과 clarification
  연결이 완료되기 전까지 기존 경로를 유지한다.
