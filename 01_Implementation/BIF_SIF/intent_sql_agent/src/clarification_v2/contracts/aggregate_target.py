from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="AGGREGATE_TARGET",
    family="AGGREGATE_TARGET",
    decision_slot_type="aggregate_target",
    description="Which measure/expression an aggregation applies to",
    detection_rules=("Check that each aggregation has exactly one intended measure or expression target.",),
    materiality_dimensions=('expression_aggregation',),
)
