from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="SELECTION_SCOPE",
    family="SELECTION_SCOPE",
    decision_slot_type="selection_scope",
    description="Whether min/max/rank applies globally or within each group",
    detection_rules=("Check whether extrema and ranking select globally or within each group.",),
    materiality_dimensions=('grain_scope', 'output_cardinality'),
)
