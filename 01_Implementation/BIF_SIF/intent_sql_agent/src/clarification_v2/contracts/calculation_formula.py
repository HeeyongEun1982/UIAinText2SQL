from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="CALCULATION_FORMULA",
    family="CALCULATION_FORMULA",
    decision_slot_type="formula",
    description="Formula, operator, and operand semantics",
    detection_rules=("Check derived quantities for explicit operators, operands, and formula semantics.",),
    materiality_dimensions=('expression_aggregation',),
)
