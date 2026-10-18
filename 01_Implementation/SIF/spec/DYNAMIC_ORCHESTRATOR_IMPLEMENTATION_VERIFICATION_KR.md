# Dynamic Clarification Orchestrator 구현 검증

검증 기준: `DYNAMIC_CLARIFICATION_ORCHESTRATOR_DESIGN_KR.md`

## 1. Phase 검증

| Phase | 판정 | 구현 근거 |
|---|---|---|
| 1. 계약과 상태 | 완료 | `CanonicalEffect`, `CanonicalIntentState`, 원자적 response transaction, rollback, 확장 status/event |
| 2. BIF 동적 queue | 완료 | bulk registration, 전체 reconciliation, implication/merge/invalidate/supersede/option pruning, 동적 priority, 조건부 audit |
| 3. BIF materialization gate | 완료 | A3 provisional revision, decision compliance, semantic audit, unrequested output 제거, compliance re-entry, 실제 summary final confirmation |
| 4. SIF 동적 queue | 완료 | SIF state/reconciliation, BIF→SIF defer, system routing, BIF_REENTRY, A4 재생성, A6 compliance revision, BIF immutability guard |
| 5. 전체 회귀와 평가 | 구조 완료/실행 평가 별도 | 단위·구조 회귀는 통과. Q87/Q2/Q76 및 smoke 30 실제 모델 실행은 별도 실행 결과로 판정 |

## 2. 필수 단위 테스트 15개 대응

| # | 요구사항 | 판정 | 대표 테스트 |
|---|---|---|---|
| 1 | 초기 후보 bulk pending | 완료 | `test_resolving_one_finding_preserves_other_pending_findings` |
| 2 | implication 제거 | 완료 | `test_location_answer_removes_redundant_population_location_question` |
| 3 | conflict invalidation | 완료 | `test_conflicting_same_slot_candidate_is_invalidated_after_answer` |
| 4 | parent-child semantic merge | 완료 | `test_response_updates_canonical_state_and_reconciles_same_slot` |
| 5 | dependency 뒤 candidate 활성화 | 완료 | `test_discovery_audit_can_add_new_candidate_after_answer` |
| 6 | audit의 새 candidate 추가 | 완료 | 위 테스트 및 multi-round pipeline 회귀 |
| 7 | 추가 validation 기본 선택지 | 완료 | `test_validation_question_includes_as_recorded_baseline` |
| 8 | effect 실패 rollback | 완료 | `test_failed_response_transaction_rolls_back_state_and_queue` |
| 9 | materialization 전 종료 금지 | 완료 | `test_audit_and_orchestrator_owned_question_control_termination` |
| 10 | selected effect와 Frame 불일치 | 완료 | `test_selected_location_effect_must_match_materialized_bif` |
| 11 | unrequested BIF output | 완료 | `test_unrequested_identifier_output_is_rejected` |
| 12 | SIF의 BIF 수정 금지 | 완료 | `test_sif_processing_cannot_mutate_confirmed_bif` |
| 13 | SIF BIF-level gap의 BIF_REENTRY | 완료 | `test_sif_bif_level_candidate_is_routed_to_bif_reentry` |
| 14 | BIF revision 뒤 A4 재생성 | 완료 | `test_sif_bif_level_gap_reenters_bif_and_regenerates_sif` |
| 15 | materialized summary 이후 final confirmation | 완료 | final gate 테스트 및 `test_final_revise_reenters_bif_and_sif_materialization` |

## 3. 개발 완료 정의 9개 판정

1. queue 제거·병합·대체·추가: 완료
2. queue transition의 결정론적 reason: 완료
3. CanonicalIntentState와 Frame 분리: 완료
4. 모델의 queue/status/effect/termination 결정 금지: 완료
5. materialization·compliance 뒤 final confirmation: 완료
6. SIF의 BIF 오류를 BIF_REENTRY로 처리: 완료
7. Q87 구조 및 execution equivalence: 실제 모델 실행 필요
8. Q2·Q76 비회귀: 실제 모델 실행 필요
9. run/question/stage/round artifact 재현: 완료

## 4. 정적 검증과 실제 평가의 구분

구조 구현은 완료되었다. 다만 Q87/Q2/Q76과 smoke 30의 execution accuracy는 모델 호출과 SQLite 실행을 포함하는 실험 결과이므로 코드 완료와 분리해 기록한다. 실제 실행이 실패하면 구조 미구현으로 곧바로 간주하지 않고 BIF, SIF, SQL, execution stage attribution을 통해 실패 단계를 판정한다.

## 5. 검증 명령

```powershell
$env:PYTHONPATH='src'
python -m compileall -q src
python -m unittest discover -s tests -p 'test_*.py'
```
