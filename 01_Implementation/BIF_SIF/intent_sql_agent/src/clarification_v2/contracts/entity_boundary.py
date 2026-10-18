from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="ENTITY_BOUNDARY",
    family="ENTITY_BOUNDARY",
    decision_slot_type="entity_boundary",
    description="Whether a phrase is one named entity or multiple semantic parts",
    detection_rules=("Check compound names and location phrases for competing entity boundaries.",),
    materiality_dimensions=('population', 'predicate_logic'),
)
