from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="COMPARISON_DIRECTION",
    family="COMPARISON_DIRECTION",
    decision_slot_type="comparison_direction",
    description="Operand order and direction of comparison",
    detection_rules=("Check comparative language for explicit left/right operands and direction.",),
    materiality_dimensions=('comparison',),
)
