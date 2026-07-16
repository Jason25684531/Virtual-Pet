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
        reply_only = self._extract_reply_field(raw_text)
        if reply_only is not None:
            return AgentResult(
                reply=reply_only,
                raw_text=raw_text,
                parser_status="parsed_reply_field_only",
                provider_type=normalized_provider,
                fallback_used=True,
                metadata={"reason": "outer_json_malformed"},
            )
        return AgentResult(
            reply=fallback_reply or self.default_reply,
            raw_text=raw_text,
            parser_status="fallback_invalid_json",
            provider_type=normalized_provider,
            fallback_used=True,
            metadata={"reason": "invalid_json"},
        )

    def _parse_fenced_json(self, raw_text: str) -> dict | None:
        """找出第一個「括號平衡」的 {...} 區塊再交給 json.loads;比非貪婪 regex
        更耐長內容/巢狀結構,也不要求一定要有收尾的 ``` fence(小型本地模型偶爾
        會忘記把 fence 補完整)。"""
        start = raw_text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw_text)):
            char = raw_text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw_text[start : index + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    @staticmethod
    def _extract_reply_field(raw_text: str) -> str | None:
        """外層 JSON 整段救不回來時的最後手段:單獨抓出 "reply" 欄位的值,
        交給 json.loads 正確處理跳脫字元,避免使用者看到帶 \\n 的原始文字。"""
        match = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(f'"{match.group(1)}"')
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
            action_tag=self._optional_action_tag(payload.get("action_tag")),
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

    @staticmethod
    def _optional_action_tag(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
