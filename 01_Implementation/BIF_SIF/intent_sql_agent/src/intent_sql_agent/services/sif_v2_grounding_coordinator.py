from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from copy import deepcopy

from intent_sql_agent.services.frame_state import frame_hash

from intent_sql_agent.services.sif_v2 import build_sif_v2_scaffold
from intent_sql_agent.services.sif_v2_patch import admit_sif_v2_grounding_proposal


@dataclass(frozen=True)
class SIFV2GroundingResult:
    sif: dict[str, Any]
    events: list[dict[str, Any]]
    admitted: bool


class SIFV2GroundingCoordinator:
    """Route every A4/A6 proposal through the same semantic admission gate."""

    def __init__(
        self,
        agents: Any,
        *,
        sif_schema: dict[str, Any],
    ) -> None:
        self.agents = agents
        self.sif_schema = sif_schema

    def initial_grounding(
        self,
        *,
        case: dict[str, Any],
        bif: dict[str, Any],
        schema_catalog: dict[str, Any],
        schema_fingerprint: str | None = None,
        database: Path | None = None,
    ) -> SIFV2GroundingResult:
        scaffold = build_sif_v2_scaffold(
            bif,
            dialect="sqlite",
            schema_fingerprint=schema_fingerprint,
        )
        request_id = "sif-v2-initial-grounding"
        allowed = unresolved_grounding_ids(scaffold)
        proposal = self.agents.a4_sif_v2_grounding_proposal(
            case,
            bif,
            scaffold,
            schema_catalog,
            request_id=request_id,
        )
        admitted, events = self._admit_slotwise(
            bif=bif, sif=scaffold, proposal=proposal,
            schema_catalog=schema_catalog, expected_request_id=request_id,
            allowed_grounding_ids=allowed, resolution="system_resolved",
            database=database,
        )
        return SIFV2GroundingResult(
            sif=admitted,
            events=events,
            admitted=_requested_slots_resolved(admitted, allowed),
        )

    def ground_selected_slots(
        self,
        *,
        bif: dict[str, Any],
        sif: dict[str, Any],
        schema_catalog: dict[str, Any],
        target_grounding_ids: list[str],
        decision_ledger: list[dict[str, Any]],
        resolution: str,
        request_id: str,
        database: Path | None = None,
    ) -> SIFV2GroundingResult:
        allowed = {str(item) for item in target_grounding_ids}
        unresolved = unresolved_grounding_ids(sif)
        if not allowed <= unresolved:
            events = [{
                "operation": "reject_orchestrator_request",
                "reason": "target_is_not_unresolved",
                "grounding_ids": sorted(allowed - unresolved),
            }]
            return SIFV2GroundingResult(sif=sif, events=events, admitted=False)
        proposal = self.agents.a6_sif_v2_grounding_proposal(
            bif,
            sif,
            schema_catalog,
            request_id=request_id,
            target_grounding_ids=sorted(allowed),
            decision_ledger=decision_ledger,
        )
        admitted, events = self._admit_slotwise(
            bif=bif, sif=sif, proposal=proposal,
            schema_catalog=schema_catalog, expected_request_id=request_id,
            allowed_grounding_ids=allowed, resolution=resolution,
            database=database,
        )
        return SIFV2GroundingResult(
            sif=admitted,
            events=events,
            admitted=_requested_slots_resolved(admitted, allowed),
        )

    def _admit_slotwise(
        self, *, bif: dict[str, Any], sif: dict[str, Any], proposal: dict[str, Any],
        schema_catalog: dict[str, Any], expected_request_id: str,
        allowed_grounding_ids: set[str], resolution: str,
        database: Path | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Commit independent slots atomically while retaining one model batch call."""
        current = deepcopy(sif)
        events: list[dict[str, Any]] = []
        units: list[tuple[str, dict[str, Any]]] = []
        if proposal.get("primary_entity_source_ref") is not None:
            units.append(("ground_primary_entity", {
                **proposal, "slot_proposals": [],
            }))
        for slot in proposal.get("slot_proposals", []) or []:
            units.append((str(slot.get("grounding_id")), {
                **proposal,
                "primary_entity_source_ref": None,
                "slot_proposals": [slot],
            }))
        if not units:
            return current, [{"operation": "no_grounding_proposed"}]
        for grounding_id, unit in units:
            unit = deepcopy(unit)
            unit["base_frame_hash"] = frame_hash(current)
            candidate, unit_events = admit_sif_v2_grounding_proposal(
                bif, current, unit, schema_catalog,
                expected_request_id=expected_request_id,
                allowed_grounding_ids={grounding_id} & allowed_grounding_ids,
                resolution=resolution, sif_schema=self.sif_schema,
                database=database,
            )
            events.append({
                "operation": "slot_admission_result",
                "grounding_id": grounding_id,
                "admitted": any(
                    item.get("operation") == "admission_committed"
                    for item in unit_events
                ),
                "events": unit_events,
            })
            events.extend(unit_events)
            if any(
                item.get("operation") == "admission_committed"
                for item in unit_events
            ):
                current = candidate
        return current, events


def unresolved_grounding_ids(sif: dict[str, Any]) -> set[str]:
    return {
        str(item.get("grounding_id"))
        for item in sif.get("unresolved_groundings", []) or []
        if item.get("grounding_id")
    }


def _requested_slots_resolved(sif: dict[str, Any], requested: set[str]) -> bool:
    return not (requested & unresolved_grounding_ids(sif))
