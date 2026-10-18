from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="SEMANTIC_DIMENSION",
    family="SEMANTIC_DIMENSION",
    decision_slot_type="predicate_dimension",
    description="Whether parallel terms are values of the same semantic dimension or independent conditions",
    detection_rules=("Check parallel labels for same-dimension versus cross-dimension readings.",),
    materiality_dimensions=('population', 'predicate_logic'),
)
