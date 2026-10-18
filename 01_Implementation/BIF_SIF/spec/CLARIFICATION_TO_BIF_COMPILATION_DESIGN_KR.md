# Clarification 응답을 최종 BIF로 컴파일하는 설계

## 1. 핵심 구조

```text
Question
  -> NLP Intent Graph Draft
  -> Gap/Candidate Forest
  -> Orchestrator pending queue
  -> E2 clarification responses
  -> Typed Decision Ledger
  -> Canonical Intent State
  -> Deterministic BIF Compiler
  -> Provisional BIF validation
  -> E2 final confirmation
  -> Confirmed BIF
```

E2 응답으로 BIF JSON을 직접 수정하지 않는다. E2 응답은 typed decision event로
저장하고 Canonical Intent State에 적용한다. BIF는 항상 Source Graph와 Canonical
Intent State에서 새로 컴파일한다.

## 2. 세 가지 authoritative state

### 2.1 Immutable Source Intent Graph

Question을 NLP로 한 번 분석한 결과다.

- source span
- atom
- clause
- coordination
- semantic relation 후보
- scope 후보
- unresolved slot

E2 응답이나 모델이 source span과 atom inventory를 변경할 수 없다.

### 2.2 Typed Decision Ledger

모든 clarification 결과를 append-only event로 저장한다.

```json
{
  "decision_id": "decision_location_1",
  "slot_ref": "slot_location_role_1",
  "selected_candidate_id": "candidate_city_only",
  "effect": {
    "operation": "select_interpretation",
    "target_node_ref": "atom_location_1",
    "value": {
      "concept": "city",
      "canonical_value": "San Bernardino"
    }
  },
  "source": "E2",
  "status": "active"
}
```

Ledger에는 자연어 답변만 저장하지 않는다. 선택 option에 미리 연결된 typed effect를
저장한다.

### 2.3 Canonical Intent State

Ledger event를 순서대로 reduce한 현재 의미 상태다.

```json
{
  "slots": {
    "slot_location_role_1": {
      "state": "resolved",
      "candidate_id": "candidate_city_only",
      "value": {
        "concept": "city",
        "canonical_value": "San Bernardino"
      },
      "decision_id": "decision_location_1"
    }
  }
}
```

Canonical State는 Frame이 아니다. 최종 BIF compiler의 입력이다.

## 3. NLP Intent Graph Draft

NLP가 처음 만드는 것은 완성 BIF가 아니라 다음 세 요소다.

```json
{
  "confirmed_nodes": [],
  "unresolved_slots": [],
  "candidate_interpretations": []
}
```

### Confirmed node

질문에서 의미가 충분히 명시된 node다.

```json
{
  "node_id": "condition_county",
  "source_atom_ref": "atom_county",
  "kind": "population_condition",
  "value": {
    "business_dimension": "county",
    "operator": "equals",
    "business_value": "San Bernardino"
  },
  "state": "explicit"
}
```

### Unresolved slot

사용자 답이 필요한 의미 위치다.

```json
{
  "slot_id": "slot_output_multiplicity",
  "kind": "OUTPUT_PRESENTATION",
  "source_atom_refs": ["atom_email_addresses"],
  "required": true,
  "owner": "user",
  "candidate_ids": ["separate_fields", "repeated_rows"]
}
```

### Candidate interpretation

```json
{
  "candidate_id": "separate_fields",
  "slot_ref": "slot_output_multiplicity",
  "user_label": "Show the e-mail addresses side by side",
  "typed_effect": {
    "operation": "set_output_presentation",
    "value": "separate_columns"
  },
  "preconditions": [],
  "implications": [],
  "conflicts": []
}
```

선택지의 label보다 typed effect를 먼저 만든다. Question Writer가 option 의미를
발명하지 않는다.

## 4. Gap Discovery

두 경로를 함께 사용한다.

### 4.1 Deterministic Gap Detectors

- coordination attachment
- AND/OR relation
- comparison direction
- population scope
- aggregation target
- global/group-wise selection
- time event/period attachment
- output multiplicity/presentation
- unresolved coreference
- incomplete typed expression

### 4.2 Optional Gap Proposer

비생성 NLP classifier 또는 제한된 모델이 놓친 gap을 observation으로만 제안할 수
있다. 다음 권한은 없다.

- decision 생성
- option effect 생성
- queue 등록
- BIF 수정
- 종료 결정

Orchestrator는 source span, materiality, owner, candidate completeness를 검증한
observation만 slot으로 등록한다.

## 5. Candidate Completeness

E2에게 질문하기 전에 Orchestrator가 option domain을 검사한다.

필수 조건:

1. option이 최소 두 개의 material하게 다른 의미를 가져야 한다.
2. 각 option에 typed effect가 있어야 한다.
3. effect가 source atom 범위를 벗어나면 안 된다.
4. 모든 유효한 parser candidate가 option으로 포함되어야 한다.
5. schema-owned 의미일 가능성이 있으면 `preserve_and_defer_to_sif` option을
   포함해야 한다.
6. 서로 중복되거나 동시에 참일 수 있는 option을 상호 배타적 선택으로 제시하지
   않는다.

정답 후보가 포함되지 않았으면 E2를 호출하지 않고 candidate generation을 다시
수행한다.

## 6. Orchestrator Queue

Queue item:

```json
{
  "slot_id": "slot_logic_1",
  "priority": 30,
  "dependencies": [],
  "status": "pending",
  "candidate_ids": ["all_of", "any_of"],
  "source_atom_refs": ["atom_type_1", "atom_type_2"]
}
```

동작:

1. 모든 발견 gap을 pending queue에 넣는다.
2. dependency와 downstream 영향으로 다음 slot을 선택한다.
3. E2 응답을 typed decision event로 commit한다.
4. constraint propagation을 수행한다.
5. 이미 해결된 slot을 제거한다.
6. 답으로 인해 불필요해진 slot을 invalidated 처리한다.
7. 새로 드러난 slot을 추가한다.
8. fixpoint까지 반복한다.

Frame을 매 round 수정하지 않는다. 사용자에게 보여줄 Working BIF가 필요하면 현재
Canonical State에서 read-only view를 다시 렌더링한다.

## 7. E2 응답 처리

### 7.1 Option selection

E2는 `request_id`와 `selected_candidate_id`만 결정한다.

```json
{
  "request_id": "request_logic_1",
  "action": "select",
  "selected_candidate_ids": ["all_of"]
}
```

Orchestrator는 candidate에 이미 등록된 typed effect를 적용한다.

### 7.2 Free text

Free text를 BIF에 직접 쓰지 않는다.

```text
E2 free text
  -> candidate matcher
  -> 기존 candidate 선택
  또는
  -> 새 candidate proposal
  -> typed effect validation
  -> E2 재확인
```

새 free-text meaning은 한 번의 재확인 없이 Canonical State에 들어갈 수 없다.

## 8. Constraint Propagation

E2 응답 하나가 다른 gap을 제거하거나 추가할 수 있다.

예:

```text
answer: each county 안에서 top 5
  -> selection.partition_by = county
  -> global-vs-group scope gap 제거
  -> output grain = school candidate 유지
  -> tie policy gap 추가 가능
```

Constraint rule은 typed state를 대상으로 수행한다.

- implication
- conflict
- mutual exclusion
- required dependency
- supersession
- scope inheritance
- source span consumption

## 9. Clarification 종료 조건

다음 조건이 모두 충족될 때만 BIF compiler를 호출한다.

```text
required user-owned pending slots == 0
invalid/conflicting active decisions == 0
source atom coverage complete
typed expression graph complete
scope references complete
output references complete
constraint propagation at fixpoint
discovery audit adds no new material user gap
```

Schema-owned unresolved slot은 `deferred_to_sif`로 남기고 BIF 종료를 막지 않는다.

## 10. Deterministic BIF Compilation

```text
Immutable Source Graph
+ Canonical Intent State
+ active Decision Ledger provenance
  -> canonical BIF 2.0
```

Compiler는 section별 pure function으로 구현한다.

```text
compile_source_atoms()
compile_entities()
compile_population_scopes()
compile_time_scopes()
compile_measures()
compile_derivations()
compile_comparisons()
compile_selections()
compile_output()
compile_clarification_provenance()
```

동일 input hash는 동일 BIF hash를 생성해야 한다.

## 11. Provisional BIF Validation

### Structural

- JSON Schema
- ID uniqueness
- reference integrity
- graph acyclic

### Semantic

- source atom coverage
- question logic preservation
- scope/measure/time/output completeness
- no unrequested enrichment
- no schema identifier

### Decision consistency

```text
active ledger effects == canonical state
canonical state == compiled BIF semantics
```

검증 실패 시 A3에게 전체 Frame 재작성을 요청하지 않는다.

- compiler bug: system error
- invalid candidate effect: candidate generation으로 복귀
- 새 user meaning 필요: 해당 slot만 queue에 추가

## 12. Final Confirmation

검증된 BIF에서 사용자용 summary를 결정론적으로 렌더링한다.

E2가 approve하면 Confirmed BIF로 동결한다. revise하면 수정 대상 slot을 선택하고
해당 decision 이후의 dependent decision만 supersede한다. BIF JSON을 직접 patch하지
않는다.

## 13. Q87 동작 예

### NLP Draft

```text
explicit atoms:
  County = San Bernardino
  City phrase = San Bernardino
  public Intermediate/Middle Schools
  Unified Schools
  opened 2009-01-01 through 2010-12-31
  administrator e-mail addresses

unresolved:
  location phrase attachment
  output presentation
```

### Location candidates

```text
A. City = San Bernardino; Unified Schools remains separate
B. District = San Bernardino City Unified
C. Preserve phrase and defer physical classification to SIF
```

E2가 A를 선택하면 typed effect만 Canonical State에 적용한다. `Unified Schools`
source atom은 그대로 유지된다.

### Compiled BIF

```text
County = San Bernardino
AND City = San Bernardino
AND public Intermediate/Middle Schools
AND Unified Schools
time = 2009-01-01 through 2010-12-31
output = administrator e-mail addresses, separate columns
```

SOC/DOC는 BIF에 없고 SIF grounding으로 넘어간다.

## 14. 모델 호출 수

정상 경로:

```text
NLP parse: local
gap detection: local
question rendering: template/local
E2: material clarification 수만큼
state update: local
BIF compile/validate: local
E2 final confirmation: 1회
```

기존 A1/A2/A3 반복 호출이 사라진다. 모델 호출 수는 `실제 사용자 의미 gap 수 + 최종
확인`에 근접한다.

## 15. 핵심 결정

Clarification과 BIF assembly를 분리하되 연결은 느슨하지 않게 한다.

- NLP가 source-grounded candidate semantics를 만든다.
- Orchestrator가 질문, 순서, 상태, 종료를 관리한다.
- E2는 candidate를 선택한다.
- typed effect가 Canonical State를 갱신한다.
- deterministic compiler가 최종 BIF를 만든다.

이 구조에서는 E2 응답을 잃지 않으면서도 모델이 답변을 반영하는 과정에서 원래
의도나 이미 확정된 다른 영역을 뒤집을 수 없다.
