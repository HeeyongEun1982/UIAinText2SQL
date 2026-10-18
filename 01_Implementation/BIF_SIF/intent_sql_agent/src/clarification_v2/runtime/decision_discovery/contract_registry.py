from __future__ import annotations
from clarification_v2.contracts import ALL_CONTRACTS

class ContractRegistry:
    def __init__(self, contracts) -> None:
        self._contracts = tuple(contracts)
        self._by_family = {c.family: c for c in self._contracts}
        if len(self._by_family) != len(self._contracts):
            raise ValueError("Clarification contract families must be unique")

    @classmethod
    def default(cls) -> "ContractRegistry":
        return cls(ALL_CONTRACTS)

    def get(self, family: str):
        key = family.upper()
        if key not in self._by_family:
            raise KeyError(f"Unknown clarification family: {family}")
        return self._by_family[key]

    def families(self) -> list[str]:
        return sorted(self._by_family)

    def contracts(self):
        return self._contracts
