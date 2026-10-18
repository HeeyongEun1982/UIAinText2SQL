from .semantic_dimension import CONTRACT as SEMANTIC_DIMENSION
from .entity_boundary import CONTRACT as ENTITY_BOUNDARY
from .boolean_attachment import CONTRACT as BOOLEAN_ATTACHMENT
from .metric_definition import CONTRACT as METRIC_DEFINITION
from .calculation_formula import CONTRACT as CALCULATION_FORMULA
from .aggregate_target import CONTRACT as AGGREGATE_TARGET
from .grouping_grain import CONTRACT as GROUPING_GRAIN
from .selection_scope import CONTRACT as SELECTION_SCOPE
from .time_attachment import CONTRACT as TIME_ATTACHMENT
from .comparison_direction import CONTRACT as COMPARISON_DIRECTION
from .output_expectation import CONTRACT as OUTPUT_EXPECTATION
from .set_relation import CONTRACT as SET_RELATION
from .tie_policy import CONTRACT as TIE_POLICY

ALL_CONTRACTS = [
    SEMANTIC_DIMENSION, ENTITY_BOUNDARY, BOOLEAN_ATTACHMENT, METRIC_DEFINITION,
    CALCULATION_FORMULA, AGGREGATE_TARGET, GROUPING_GRAIN, SELECTION_SCOPE,
    TIME_ATTACHMENT, COMPARISON_DIRECTION, OUTPUT_EXPECTATION, SET_RELATION, TIE_POLICY,
]
