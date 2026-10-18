from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="GROUPING_GRAIN",
    family="GROUPING_GRAIN",
    decision_slot_type="grouping_grain",
    description="Global, entity-level, or group-level calculation grain",
    detection_rules=("Check whether calculation grain is explicitly global, per entity, or per group.",),
    materiality_dimensions=('grain_scope', 'output_cardinality'),
)
