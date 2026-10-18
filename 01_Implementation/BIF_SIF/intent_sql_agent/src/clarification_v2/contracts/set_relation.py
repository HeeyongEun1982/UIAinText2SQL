from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="SET_RELATION",
    family="SET_RELATION",
    decision_slot_type="set_relation",
    description="Union, intersection, exclusion, or membership relation",
    detection_rules=("Check multiple populations or labels for union, intersection, exclusion, or membership.",),
    materiality_dimensions=('population', 'predicate_logic'),
)
