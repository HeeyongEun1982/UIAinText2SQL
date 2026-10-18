from __future__ import annotations

from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient
from intent_sql_agent.services.direct_sif_service import DirectSIFService


class AgentSuite:
    """Pure Direct SIF Agent Suite."""

    def __init__(
        self,
        client: OpenAIChatModelClient,
        *,
        bif_schema: dict[str, Any] | None = None,
        sif_schema: dict[str, Any] | None = None,
        sif_v2_schema: dict[str, Any] | None = None,
        query_layout_schema: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.bif_schema = bif_schema or {}
        self.sif_schema = sif_schema or {}
        self.sif_v2_schema = sif_v2_schema or {}
        self.query_layout_schema = query_layout_schema or {}
        self.direct_sif_service: DirectSIFService | None = None
        self.direct_sif_result: dict[str, Any] | None = None

    def run_direct_sif(
        self, case: dict[str, Any], schema_catalog: dict[str, Any]
    ) -> dict[str, Any]:
        """Direct SIF Grounding on original question and database schema."""
        self.direct_sif_service = DirectSIFService(
            self.client, self.client.logger
        )
        self.direct_sif_result = self.direct_sif_service.run(
            case=case, schema_catalog=schema_catalog
        )
        return self.direct_sif_result
