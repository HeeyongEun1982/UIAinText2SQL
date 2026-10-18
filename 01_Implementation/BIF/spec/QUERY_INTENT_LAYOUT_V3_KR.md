# Query Intent Layout 3.0

## 목적

BIF와 SIF는 서로 다른 분류 체계가 아니다. 둘은 하나의 SQL 의미 구조를 공유한다.

```text
Confirmed CQD
  -> BIF Query Intent Layout (논리 의미, grounding=null)
  -> SIF Query Intent Layout (동일 노드에 grounding 추가)
  -> deterministic SQL compiler
```

SIF 단계에서 BIF 노드의 `node_id`, `clause`, `node_kind`, `meaning`,
`constraints`, `source_refs`, `decision_refs`, `origin_node_ids`를 변경하거나 다른
분류로 옮길 수 없다. 물리적으로 필요한 source/join만 `technical=true`로 추가할 수
있으며, 반드시 기존 의미 노드를 `origin_node_ids`로 추적해야 한다.

## 전체 구조

`query`는 SQL과 직접 대응하는 다음 영역만 가진다.

- `sources`: 조회 대상의 논리적 엔터티와 물리 source
- `joins`: 사용자 의미의 관계 또는 물리 구현을 위한 technical join
- `select`: 반환할 속성, 집계, 계산
- `where`: 행 선택 predicate와 AND/OR/NOT 구조
- `group_by`: 결과 grain
- `having`: 집계 결과 조건
- `order_by`: 정렬 표현식과 방향
- `limit`: 결과 개수
- `set_operations`: UNION/INTERSECT/EXCEPT 의미
- `distinct`: 중복 제거 요구

각 노드는 다음 세 덩어리로 읽는다.

1. 의미: `clause`, `node_kind`, `meaning`
2. 근거와 제약: `source_refs`, `decision_refs`, `constraints`
3. 물리화: `grounding`

`slot`, `legacy_refs`, `metric_groundings`, `selection_groundings`,
`output_groundings` 같은 중간 재분류는 사용하지 않는다.

## BIF와 SIF의 차이

| 항목 | BIF | SIF |
|---|---|---|
| SQL 논리 영역 | 확정 | 그대로 유지 |
| 의미 노드 ID | 확정 | 그대로 유지 |
| AND/OR/NOT | 확정 | 그대로 유지 |
| 테이블/컬럼/코드 | 없음 | `grounding`에 기록 |
| `=`, `LIKE`, `IN`, 범위 | 의미 제약만 기록 | 물리 operator/value로 확정 |
| join key | 없음 | traced technical join으로 추가 가능 |
| 새 필터/출력 의미 | 금지 | 금지 |

## 검증 규칙

- BIF의 모든 노드는 `grounding=null`이어야 한다.
- SIF는 모든 BIF 의미 노드를 같은 SQL 영역과 같은 순서로 보존해야 한다.
- SIF의 grounded 노드는 catalog evidence를 가져야 한다.
- 모든 node constraint는 정확히 한 개의 `constraint_result`를 가져야 한다.
- represented CQD requirement는 하나 이상의 노드로 coverage되어야 한다.
- WHERE/HAVING predicate는 논리 트리에서 정확히 한 번 이상 참조되어야 한다.
- 미해결 노드가 하나라도 있으면 SQL compile을 시작하지 않는다.
- compiler는 SIF에서 SQL 의미를 다시 추론하지 않는다.

## 별도 예시

- `examples/q2.bif_query_layout.json`: CQD를 SQL 논리 역할로 한 번만 구성한 BIF
- `examples/q2.sif_query_layout.json`: 같은 노드 ID에 테이블·컬럼·값·operator를 추가한 SIF

Q2의 핵심은 `/selections/0`과 `/outputs/0`을 두 slot으로 만들지 않는 것이다.
둘 다 동일한 SQL 출력 역할이므로 BIF의 `select_zip` 한 노드가 두 reference를
coverage한다. SIF에서도 같은 `select_zip`이 `schools.Zip`으로 grounded된다. 필터가
있는 `frpm`과 출력이 있는 `schools`를 잇는 CDSCode join만 기술 노드로 추가된다.

