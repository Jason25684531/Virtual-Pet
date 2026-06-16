from __future__ import annotations

import json
import re
from enum import Enum

from pet_harness.models.agent_result import AgentResult
from pet_harness.models.provider import ProviderType


class ResultParser:
    def __init__(self, default_reply: str = "I heard you, but I had trouble understanding that response.") -> None:
        self.default_reply = default_reply

    def parse(
        self,
        raw_text: str,
        provider_type: ProviderType | str,
        fallback_reply: str | None = None,
    ) -> AgentResult:
        normalized_provider = provider_type.value if isinstance(provider_type, Enum) else str(provider_type)
        raw_text = raw_text or ""
        try:
            payload = json.loads(raw_text)
            return self._from_payload(payload, raw_text, normalized_provider, "parsed_json")
        except json.JSONDecodeError:
            fenced_payload = self._parse_fenced_json(raw_text)
            if fenced_payload is not None:
                return self._from_payload(fenced_payload, raw_text, normalized_provider, "parsed_fenced_json")
        return AgentResult(
            reply=fallback_reply or self.default_reply,
            raw_text=raw_text,
            parser_status="fallback_invalid_json",
            provider_type=normalized_provider,
            fallback_used=True,
            metadata={"reason": "invalid_json"},
        )

    def _parse_fenced_json(self, raw_text: str) -> dict | None:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    def _from_payload(
        self,
        payload: dict,
        raw_text: str,
        provider_type: str,
        parser_status: str,
    ) -> AgentResult:
        return AgentResult(
            reply=str(payload.get("reply") or self.default_reply),
            matched_skill=payload.get("matched_skill"),
            behavior_hint=payload.get("behavior_hint"),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            tool_request=payload.get("tool_request"),
            raw_text=raw_text,
            raw_json=payload,
            parser_status=parser_status,
            provider_type=provider_type,
            fallback_used=False,
            metadata={
                "notes": payload.get("notes"),
                "reasoning_summary": payload.get("reasoning_summary"),
            },
        )
