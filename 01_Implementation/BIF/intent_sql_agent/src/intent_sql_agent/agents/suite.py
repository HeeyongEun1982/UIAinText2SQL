from __future__ import annotations

import copy
from typing import Any

from intent_sql_agent.model_client import OpenAIChatModelClient
from intent_sql_agent.services.semantic_frontend import SemanticFrontendService


class AgentSuite:
    """Pure BIF Agent Suite for 0.bif: Business Intent Analysis & Clarification ONLY."""

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
        self.semantic_frontend_result: dict[str, Any] | None = None
        self.semantic_frontend_service: SemanticFrontendService | None = None
        self.semantic_frontend_active = False

    def a1_bif_analyze(
        self, case: dict[str, Any], schema_catalog: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run BIF Business Intent Analysis and Clarification."""
        self.semantic_frontend_service = SemanticFrontendService(
            self.client, self.client.logger
        )
        self.semantic_frontend_result = self.semantic_frontend_service.run(
            case=case, schema_catalog=schema_catalog or {}
        )
        self.semantic_frontend_active = True
        return {
            "draft_bif": {"goal": case["question"]},
            "issue_observations": {"candidates": []},
        }
