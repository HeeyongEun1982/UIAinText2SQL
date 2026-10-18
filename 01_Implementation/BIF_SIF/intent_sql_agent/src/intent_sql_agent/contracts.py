from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ExecutionStatus = Literal[
    "success",
    "timeout",
    "runtime_error",
    "resource_limit",
    "invalid_statement",
]


@dataclass(frozen=True)
class ExecutionRecord:
    sql_hash: str
    status: ExecutionStatus
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    row_count: int = 0
    truncated: bool = False
    latency_ms: float = 0.0
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self, *, include_rows: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_rows:
            value.pop("rows", None)
        return value


@dataclass(frozen=True)
class ComparisonRecord:
    comparison_policy: Literal["ordered", "multiset"]
    comparable: bool
    equivalent: bool
    execution_accuracy: int
    column_count_match: bool
    row_count_gold: int
    row_count_generated: int
    missing_rows: tuple[Any, ...] = field(default_factory=tuple)
    extra_rows: tuple[Any, ...] = field(default_factory=tuple)
    value_mismatches: tuple[Any, ...] = field(default_factory=tuple)
    reason: str | None = None
    relaxed_equivalent: bool = False
    relaxed_execution_accuracy: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
