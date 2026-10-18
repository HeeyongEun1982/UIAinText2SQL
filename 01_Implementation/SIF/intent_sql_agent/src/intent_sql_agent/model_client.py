from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from intent_sql_agent.services.artifact_logger import ArtifactLogger
from intent_sql_agent.services.contract_validator import ContractError, validate_contract


_FENCE = re.compile(r"^```(?:json|sql|text)?\s*|\s*```$", re.IGNORECASE)


def _build_chat_request(
    *,
    profile: "ModelProfile",
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens_override: int | None = None,
) -> dict[str, Any]:
    completion_limit = max(
        profile.max_tokens,
        int(max_tokens_override or profile.max_tokens),
    )
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "n": 1,
        "stream": False,
    }
    if model.startswith("gpt-5"):
        # Older GPT-5 family models reject temperature/logprobs and use the
        # Chat Completions reasoning-token-aware output limit parameter.
        request["max_completion_tokens"] = completion_limit
    else:
        request.update(
            {
                "temperature": profile.temperature,
                "max_tokens": completion_limit,
                "logprobs": profile.logprobs,
            }
        )
    return request


@dataclass(frozen=True)
class ModelProfile:
    pipeline_model: str = "gpt-5-mini"
    simulated_user_model: str = "gpt-5-mini"
    temperature: float = 0.0
    max_tokens: int = 4096
    logprobs: bool = True
    request_timeout_seconds: float = 120.0
    max_retries: int = 2


class OpenAIChatModelClient:
    def __init__(self, profile: ModelProfile, logger: ArtifactLogger) -> None:
        self.profile = profile
        self.logger = logger
        self.client = OpenAI(
            timeout=profile.request_timeout_seconds,
            max_retries=profile.max_retries,
        )
        self._calls: defaultdict[str, int] = defaultdict(int)

    @property
    def call_counts(self) -> dict[str, int]:
        return dict(self._calls)

    @property
    def total_calls(self) -> int:
        return sum(self._calls.values())

    def complete_json(
        self,
        *,
        agent_id: str,
        system_prompt: str,
        payload: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        contract_name: str = "JSON",
        simulated_user: bool = False,
        restricted_request: bool = False,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        response = self._call(
            agent_id=agent_id,
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2),
            simulated_user=simulated_user,
            restricted_request=restricted_request,
            max_tokens_override=max_tokens_override,
        )
        repair_attempt = 0
        while True:
            try:
                value = self._parse_json(response)
                # Some models echo the repair envelope and place the corrected
                # value in ``invalid_output``. Accept that value as the repair
                # result instead of starting another otherwise-identical turn.
                if repair_attempt and "invalid_output" in value:
                    nested = value["invalid_output"]
                    if isinstance(nested, str):
                        value = self._parse_json(nested)
                    elif isinstance(nested, dict):
                        value = nested
                if output_schema is not None:
                    validate_contract(value, output_schema, contract_name)
                return value
            except (json.JSONDecodeError, ContractError, TypeError) as validation_error:
                repair_attempt += 1
                validation_error_text = str(validation_error)
            repair_payload = {
                "invalid_output": response,
                "validation_error": validation_error_text,
                "required_json_schema": output_schema,
                "repair_attempt": repair_attempt,
                "instruction": "Return only corrected JSON. Preserve the intended meaning and add no explanation.",
            }
            response = self._call(
                agent_id=agent_id,
                system_prompt="You repair JSON format and schema violations only.",
                user_prompt=json.dumps(repair_payload, ensure_ascii=False, indent=2),
                simulated_user=simulated_user,
                restricted_request=restricted_request,
                max_tokens_override=max_tokens_override,
            )

    def complete_text(
        self,
        *,
        agent_id: str,
        system_prompt: str,
        payload: dict[str, Any],
    ) -> str:
        attempt = 0
        while True:
            attempt += 1
            response = self._call(
                agent_id=agent_id,
                system_prompt=system_prompt,
                user_prompt=json.dumps(
                    {**payload, "attempt": attempt}, ensure_ascii=False, indent=2
                ),
                simulated_user=False,
                restricted_request=False,
                max_tokens_override=None,
            )
            cleaned = _FENCE.sub("", response.strip()).strip()
            if cleaned:
                return cleaned

    def _call(
        self,
        *,
        agent_id: str,
        system_prompt: str,
        user_prompt: str,
        simulated_user: bool,
        restricted_request: bool,
        max_tokens_override: int | None,
    ) -> str:
        self._calls[agent_id] += 1
        call_number = self._calls[agent_id]
        call_name = f"call_{call_number:03d}"
        model = self.profile.simulated_user_model if simulated_user else self.profile.pipeline_model
        public_base = f"agents/{agent_id.lower()}/{call_name}"
        request_base = f"restricted/e2_oracle/{call_name}" if restricted_request else public_base
        request = _build_chat_request(
            profile=self.profile,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens_override=max_tokens_override,
        )
        self.logger.write_json(f"{request_base}/request.json", request)
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(**request)
            content = response.choices[0].message.content or ""
            metadata = {
                "agent_id": agent_id,
                "model_alias": model,
                "actual_model": response.model,
                "attempt": call_number,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "finish_reason": response.choices[0].finish_reason,
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
            self.logger.write_text(f"{public_base}/response.txt", content)
            self.logger.write_json(f"{public_base}/metadata.json", metadata)
            self.logger.append_event("AGENT_CALL_COMPLETED", metadata)
            return content
        except Exception as exc:
            metadata = {
                "agent_id": agent_id,
                "model_alias": model,
                "attempt": call_number,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self.logger.write_json(f"{public_base}/metadata.json", metadata)
            self.logger.append_event("AGENT_CALL_FAILED", metadata)
            raise

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        cleaned = _FENCE.sub("", content.strip()).strip()
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise TypeError("Expected a JSON object")
        return value
