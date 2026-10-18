from clarification_v2.models.contract import ClarificationContract

CONTRACT = ClarificationContract(
    contract_id="OUTPUT_EXPECTATION",
    family="OUTPUT_EXPECTATION",
    decision_slot_type="output",
    description="Whether the user expects details, list, summary, single or multiple values",
    detection_rules=("Check requested fields, qualifications, multiplicity, and detail versus summary output.",),
    materiality_dimensions=('output_cardinality',),
)
