from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="METRIC_DEFINITION",
    family="METRIC_DEFINITION",
    decision_slot_type="metric",
    description="What business quantity the user intends to measure",
    detection_rules=("Check whether each requested metric has one explicit business meaning.",),
    materiality_dimensions=('expression_aggregation', 'output_cardinality'),
)
