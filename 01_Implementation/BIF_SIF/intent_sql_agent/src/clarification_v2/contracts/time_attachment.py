from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="TIME_ATTACHMENT",
    family="TIME_ATTACHMENT",
    decision_slot_type="time_attachment",
    description="Which event or measure a time phrase constrains",
    detection_rules=("Check each time phrase for an explicit event, state, or measurement attachment.",),
    materiality_dimensions=('time', 'population'),
)
