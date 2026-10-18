from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="BOOLEAN_ATTACHMENT",
    family="BOOLEAN_ATTACHMENT",
    decision_slot_type="boolean_relation",
    description="AND/OR/NOT and parenthesis attachment",
    detection_rules=("Check every conjunction, disjunction, negation, and implicit parenthesis attachment.",),
    materiality_dimensions=('predicate_logic',),
)
