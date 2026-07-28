from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from enum import Enum

from pet_harness.models.agent_result import AgentResult
from pet_harness.models.provider import ProviderType

LOGGER = logging.getLogger(__name__)
_RAW_PREVIEW_LIMIT = 200
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def parse_fenced_json(raw_text: str) -> object | None:
    """Parse the first complete JSON object or array, with an optional fence."""
    starts = [index for index in (raw_text.find("{"), raw_text.find("[")) if index != -1]
    if not starts:
        return None
    start = min(starts)
    closing = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_string = escape = False
    for index in range(start, len(raw_text)):
        char = raw_text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in closing:
            stack.append(closing[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                try:
                    return json.loads(raw_text[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


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
        except json.JSONDecodeError:
            parsed_ok = False
            payload = None
        else:
            parsed_ok = True

        if parsed_ok:
            if isinstance(payload, dict):
                return self._from_payload(payload, raw_text, normalized_provider, "parsed_json")
            return self._build_fallback(
                raw_text,
                normalized_provider,
                fallback_reply,
                "fallback_non_object_root",
                f"root_type={type(payload).__name__}",
            )

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
        return self._build_fallback(
            raw_text, normalized_provider, fallback_reply, "fallback_invalid_json", "invalid_json"
        )

    def _parse_fenced_json(self, raw_text: str) -> dict | None:
        """找出第一個「括號平衡」的 {...} 區塊再交給 json.loads;比非貪婪 regex
        更耐長內容/巢狀結構,也不要求一定要有收尾的 ``` fence(小型本地模型偶爾
        會忘記把 fence 補完整)。"""
        payload = parse_fenced_json(raw_text)
        return payload if isinstance(payload, dict) else None

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
        diagnostics: list[dict[str, str]] = []

        confidence, diag = self._normalize_confidence(payload.get("confidence"))
        if diag:
            diagnostics.append(diag)

        tool_request, diag = self._normalize_tool_request(payload.get("tool_request"))
        if diag:
            diagnostics.append(diag)

        matched_skill, diag = self._normalize_optional_str(payload.get("matched_skill"), "matched_skill")
        if diag:
            diagnostics.append(diag)

        behavior_hint, diag = self._normalize_optional_str(payload.get("behavior_hint"), "behavior_hint")
        if diag:
            diagnostics.append(diag)

        action_tag, diag = self._normalize_optional_str(payload.get("action_tag"), "action_tag")
        if diag:
            diagnostics.append(diag)

        notes, diag = self._normalize_optional_str(payload.get("notes"), "notes")
        if diag:
            diagnostics.append(diag)

        if diagnostics:
            self._log_field_diagnostics(raw_text, diagnostics)

        return AgentResult(
            reply=str(payload.get("reply") or self.default_reply),
            matched_skill=matched_skill,
            behavior_hint=behavior_hint,
            action_tag=action_tag,
            confidence=confidence,
            tool_request=tool_request,
            raw_text=raw_text,
            raw_json=payload,
            parser_status=parser_status,
            provider_type=provider_type,
            fallback_used=False,
            metadata={
                "notes": notes,
                "reasoning_summary": payload.get("reasoning_summary"),
                "diagnostics": diagnostics,
            },
        )

    @staticmethod
    def _normalize_confidence(value: object) -> tuple[float, dict[str, str] | None]:
        """confidence contract:bool 絕不視為數值(Python bool 是 int 子類,須先排除);
        finite numeric 與 numeric string 轉換後 clamp 至 [0.0, 1.0];其餘不可轉換值降為
        0.0 並記錄欄位級診斷,任何情況都不拋例外、不整筆 fallback。"""
        if value is None:
            return 0.0, None
        if isinstance(value, bool):
            return 0.0, {"field": "confidence", "reason": "boolean_value"}
        if isinstance(value, (int, float)):
            numeric = float(value)
        elif isinstance(value, str):
            try:
                numeric = float(value.strip())
            except ValueError:
                return 0.0, {"field": "confidence", "reason": "non_numeric_string"}
        else:
            return 0.0, {"field": "confidence", "reason": f"unsupported_type:{type(value).__name__}"}
        if not math.isfinite(numeric):
            return 0.0, {"field": "confidence", "reason": "non_finite_number"}
        clamped = max(0.0, min(1.0, numeric))
        if clamped != numeric:
            return clamped, {"field": "confidence", "reason": "out_of_range_clamped"}
        return clamped, None

    @staticmethod
    def _normalize_tool_request(value: object) -> tuple[dict | None, dict[str, str] | None]:
        if value is None:
            return None, None
        if isinstance(value, dict):
            return value, None
        return None, {"field": "tool_request", "reason": f"unsupported_type:{type(value).__name__}"}

    @staticmethod
    def _normalize_optional_str(value: object, field_name: str) -> tuple[str | None, dict[str, str] | None]:
        if value is None:
            return None, None
        if isinstance(value, str):
            return (value.strip() or None), None
        return None, {"field": field_name, "reason": f"unsupported_type:{type(value).__name__}"}

    def _build_fallback(
        self,
        raw_text: str,
        provider_type: str,
        fallback_reply: str | None,
        parser_status: str,
        reason: str,
    ) -> AgentResult:
        self._log_fallback(raw_text, parser_status, reason)
        return AgentResult(
            reply=fallback_reply or self.default_reply,
            raw_text=raw_text,
            parser_status=parser_status,
            provider_type=provider_type,
            fallback_used=True,
            metadata={"reason": reason},
        )

    @staticmethod
    def _digest(raw_text: str) -> str:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:8]

    def _log_fallback(self, raw_text: str, parser_status: str, reason: str) -> None:
        # ponytail: 預設 log 只留 reason/root type/長度/digest,原文 preview 只在 DEBUG 開啟時記錄。
        LOGGER.warning(
            "result_parser fallback: reason=%s parser_status=%s raw_length=%d digest=%s",
            reason,
            parser_status,
            len(raw_text),
            self._digest(raw_text),
        )
        if LOGGER.isEnabledFor(logging.DEBUG):
            preview = _CONTROL_CHARS_RE.sub(" ", raw_text)[:_RAW_PREVIEW_LIMIT]
            LOGGER.debug("result_parser fallback raw preview: %s", preview)

    def _log_field_diagnostics(self, raw_text: str, diagnostics: list[dict[str, str]]) -> None:
        digest = self._digest(raw_text)
        for entry in diagnostics:
            LOGGER.info(
                "result_parser field diagnostic: field=%s reason=%s raw_length=%d digest=%s",
                entry["field"],
                entry["reason"],
                len(raw_text),
                digest,
            )
        if LOGGER.isEnabledFor(logging.DEBUG):
            preview = _CONTROL_CHARS_RE.sub(" ", raw_text)[:_RAW_PREVIEW_LIMIT]
            LOGGER.debug("result_parser field diagnostic raw preview: %s", preview)
