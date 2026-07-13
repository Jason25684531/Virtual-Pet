from __future__ import annotations

import os
from typing import Any, Callable

import requests

from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.models.skill import Skill


class APIProvider:
    def __init__(
        self,
        config: ProviderConfig,
        request_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.request_fn = request_fn or requests.post

    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        prompt_text = prompt_text or event.text
        api_key = os.getenv(self.config.api_key_env_var or "")
        if self.config.api_key_env_var and not api_key:
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="missing_api_key",
                message=f"Missing API key env var: {self.config.api_key_env_var}",
            )
        if not self.config.base_url:
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="missing_base_url",
                message="Missing API provider base URL.",
            )
        try:
            response = self.request_fn(
                url=self.config.base_url,
                headers=self._build_headers(api_key),
                json=self._build_payload(prompt_text),
                timeout=self.config.timeout_seconds,
            )
        except requests.Timeout:
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="timeout",
                message="API request timed out.",
            )
        except Exception as exc:  # noqa: BLE001
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="request_error",
                message=f"API request failed: {exc}",
            )

        if getattr(response, "status_code", 500) >= 400:
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="http_error",
                message=f"API request returned status {response.status_code}.",
                extra_metadata={"status_code": response.status_code},
            )
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="invalid_json",
                message="API response JSON could not be decoded.",
                extra_metadata={"raw_response": getattr(response, "text", "")[:500]},
            )

        content = self._extract_content(payload)
        if not content:
            return self._fallback_reply(
                event,
                matched_skill,
                prompt_text,
                error_category="invalid_response_shape",
                message="API response did not include content.",
            )

        return ProviderReply(
            reply=content,
            provider_status=ProviderStatus(
                provider_type=ProviderType.API,
                healthy=True,
                message="api provider ready",
                metadata={
                    "model_name": self.config.model_name,
                    "endpoint_class": "chat_completions",
                    "base_url": self.config.base_url,
                },
            ),
            raw_text=content,
            raw_json=payload,
            prompt_text=prompt_text,
            metadata={"requested_provider": ProviderType.API.value},
        )

    def _build_headers(self, api_key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _build_payload(self, prompt_text: str) -> dict[str, Any]:
        return {
            "model": self.config.model_name,
            "messages": [{"role": "user", "content": prompt_text}],
        }

    def _extract_content(self, payload: dict[str, Any]) -> str | None:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and message.get("content"):
                    return str(message["content"])
                if first.get("text"):
                    return str(first["text"])
        if payload.get("output_text"):
            return str(payload["output_text"])
        return None

    def _fallback_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None,
        prompt_text: str,
        error_category: str,
        message: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> ProviderReply:
        # fail-closed:不偽造回覆,回傳結構化 unavailable 結果。
        metadata = {
            "error_category": error_category,
            "requested_provider": ProviderType.API.value,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        return ProviderReply(
            reply=f"AI provider unavailable: {message}",
            provider_status=ProviderStatus(
                provider_type=ProviderType.API,
                healthy=False,
                message=message,
                metadata=metadata,
            ),
            prompt_text=prompt_text,
            metadata=metadata,
        )
