from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="TIE_POLICY",
    family="TIE_POLICY",
    decision_slot_type="tie_policy",
    description="Whether all ties or one row should be returned",
    detection_rules=("Check extrema and top/bottom selections for an explicit tie-return policy.",),
    materiality_dimensions=('output_cardinality', 'grain_scope'),
)
