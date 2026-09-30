"""ResultParser 輸入 contract 回歸測試:涵蓋 root 型別、confidence normalization、
tool_request 結構驗證與 optional 欄位正規化。任何不可信 Provider 輸出都必須得到
有效的 AgentResult,不得拋出未捕捉例外。"""

from __future__ import annotations

import json
import logging

import pytest

from pet_harness.agent.result_parser import ResultParser


def _parse(raw: str):
    return ResultParser().parse(raw, provider_type="ollama", fallback_reply="fallback")


class TestNonObjectRoot:
    @pytest.mark.parametrize(
        "raw",
        ["[1, 2, 3]", '"hello"', "42", "null", "true"],
        ids=["list", "string", "number", "null", "boolean"],
    )
    def test_root_is_not_an_object_falls_back(self, raw):
        result = _parse(raw)
        assert result.fallback_used is True
        assert result.parser_status == "fallback_non_object_root"
        assert result.reply == "fallback"

    @pytest.mark.parametrize("raw", ["", "   "], ids=["empty", "whitespace"])
    def test_empty_or_whitespace_falls_back(self, raw):
        result = _parse(raw)
        assert result.fallback_used is True
        assert result.parser_status == "fallback_invalid_json"
        assert result.reply == "fallback"


class TestConfidenceNormalization:
    def test_non_numeric_string_becomes_zero_with_diagnostic(self):
        result = _parse('{"reply": "hi", "confidence": "high"}')
        assert result.confidence == 0.0
        assert result.reply == "hi"
        assert result.fallback_used is False
        assert any(d["field"] == "confidence" for d in result.metadata["diagnostics"])

    def test_numeric_string_is_supported(self):
        result = _parse('{"reply": "hi", "confidence": "0.8"}')
        assert result.confidence == pytest.approx(0.8)
        assert result.fallback_used is False

    @pytest.mark.parametrize("raw_value", ["true", "false"])
    def test_boolean_confidence_is_not_numeric(self, raw_value):
        result = _parse(f'{{"reply": "hi", "confidence": {raw_value}}}')
        assert result.confidence == 0.0
        assert result.reply == "hi"
        assert result.fallback_used is False
        assert any(d["field"] == "confidence" for d in result.metadata["diagnostics"])

    def test_confidence_above_range_is_clamped(self):
        result = _parse('{"reply": "hi", "confidence": 7.5}')
        assert result.confidence == 1.0

    def test_confidence_below_range_is_clamped(self):
        result = _parse('{"reply": "hi", "confidence": -2}')
        assert result.confidence == 0.0

    def test_confidence_as_list_becomes_zero(self):
        result = _parse('{"reply": "hi", "confidence": [1, 2]}')
        assert result.confidence == 0.0

    def test_confidence_missing_has_no_diagnostic(self):
        result = _parse('{"reply": "hi"}')
        assert result.confidence == 0.0
        assert result.metadata["diagnostics"] == []


class TestToolRequestValidation:
    def test_string_tool_request_is_normalized_to_none(self):
        result = _parse('{"reply": "hi", "tool_request": "use_timer"}')
        assert result.tool_request is None
        assert any(d["field"] == "tool_request" for d in result.metadata["diagnostics"])

    def test_list_tool_request_is_normalized_to_none(self):
        result = _parse('{"reply": "hi", "tool_request": [1, 2]}')
        assert result.tool_request is None

    def test_valid_object_tool_request_is_preserved(self):
        result = _parse('{"reply": "hi", "tool_request": {"tool_name": "youtube_music_tool", "arguments": {}}}')
        assert result.tool_request == {"tool_name": "youtube_music_tool", "arguments": {}}


class TestOptionalFieldNormalization:
    def test_all_optional_fields_missing(self):
        result = _parse('{"reply": "hi"}')
        assert result.matched_skill is None
        assert result.tool_request is None
        assert result.confidence == 0.0
        assert result.fallback_used is False
        assert result.metadata["notes"] is None

    def test_matched_skill_wrong_type_normalizes_to_none(self):
        result = _parse('{"reply": "hi", "matched_skill": {"name": "joke"}}')
        assert result.matched_skill is None
        assert any(d["field"] == "matched_skill" for d in result.metadata["diagnostics"])

    def test_notes_missing_has_no_diagnostic(self):
        result = _parse('{"reply": "hi"}')
        assert result.metadata["notes"] is None
        assert result.metadata["diagnostics"] == []

    def test_notes_wrong_type_normalizes_to_default(self):
        result = _parse('{"reply": "hi", "notes": ["a", "b"]}')
        assert result.metadata["notes"] is None
        assert any(d["field"] == "notes" for d in result.metadata["diagnostics"])


class TestReplyNormalization:
    def test_reply_as_list_joins_into_single_string(self):
        result = _parse('{"reply": ["第一句", "第二句", "第三句"]}')
        assert result.reply == "第一句 第二句 第三句"
        assert any(d["field"] == "reply" for d in result.metadata["diagnostics"])

    def test_reply_as_plain_string_is_unchanged(self):
        result = _parse('{"reply": "hi"}')
        assert result.reply == "hi"
        assert result.metadata["diagnostics"] == []

    def test_reply_double_encoded_json_is_unwrapped(self):
        raw = json.dumps({"reply": json.dumps({"reply": "日本女子搖滾樂團宣布活動"})})
        result = _parse(raw)
        assert result.reply == "日本女子搖滾樂團宣布活動"
        assert any(
            d["field"] == "reply" and d["reason"] == "double_encoded_json_unwrapped"
            for d in result.metadata["diagnostics"]
        )

    def test_reply_wrapped_in_braces_but_not_valid_json_is_kept_as_is(self):
        result = _parse('{"reply": "{很開心}"}')
        assert result.reply == "{很開心}"
        assert result.metadata["diagnostics"] == []


class TestJsonNeverLeaksToTheReply:
    """Regression: 回報「前端 UI 偶爾會回吐 JSON 格式」。真正的呼叫端
    (harness_engine._invoke_provider)把 fallback_reply 設成 provider 的原始輸出——
    對 Ollama 而言那本來就預期是 JSON。JSON 解析在任何一層失敗時,都不得把這段
    原始文字原樣當成 reply 顯示出來。"""

    def test_non_object_root_without_a_reply_field_does_not_echo_the_raw_json(self):
        raw = "[1, 2, 3]"
        result = ResultParser().parse(raw, provider_type="ollama", fallback_reply=raw)

        assert result.fallback_used is True
        assert not result.reply.strip().startswith(("[", "{", "```"))
        assert result.reply == ResultParser().default_reply

    def test_non_object_root_with_a_recoverable_reply_field_extracts_it(self):
        raw = '[{"reply": "先幫你查一下", "confidence": 0.4}]'
        result = ResultParser().parse(raw, provider_type="ollama", fallback_reply=raw)

        assert result.reply == "先幫你查一下"
        assert result.parser_status == "parsed_reply_field_only"

    def test_truncated_json_without_any_reply_field_falls_back_to_default_message(self):
        raw = '{"confidence": 0.9, "matched_skil'  # 被截斷,連 reply 都還沒開始
        result = ResultParser().parse(raw, provider_type="ollama", fallback_reply=raw)

        assert result.fallback_used is True
        assert not result.reply.strip().startswith(("{", "[", "```"))
        assert result.reply == ResultParser().default_reply

    def test_non_json_provider_plain_text_fallback_is_unaffected(self):
        """非 JSON provider(例如已經自行抽出文字的 API provider)的 fallback_reply
        本來就是一般文字,不應被本次守衛誤傷。"""
        result = ResultParser().parse(
            "not json at all", provider_type="openai", fallback_reply="抱歉，我剛剛沒聽清楚。",
        )

        assert result.reply == "抱歉，我剛剛沒聽清楚。"


class TestFallbackResultCompleteness:
    def test_fallback_result_is_a_fully_valid_domain_result(self):
        result = _parse("[1, 2, 3]")
        assert isinstance(result.reply, str) and result.reply
        assert 0.0 <= result.confidence <= 1.0
        assert result.tool_request is None or isinstance(result.tool_request, dict)


class TestInteractionIsNeverAborted:
    def test_hostile_list_root_never_raises(self):
        # 直接呼叫即是斷言:任何例外都會讓 pytest 判定測試失敗。
        _parse("[1, 2, 3]")


class TestDiagnosticLoggingIsBounded:
    def test_default_level_log_contains_no_raw_text_substring(self, caplog):
        raw = "x" * 10_000
        with caplog.at_level(logging.INFO, logger="pet_harness.agent.result_parser"):
            _parse(raw)
        for record in caplog.records:
            assert raw not in record.getMessage()
            assert "x" * 50 not in record.getMessage()

    def test_debug_level_preview_is_sanitized_and_bounded(self, caplog):
        raw = "y" * 500
        with caplog.at_level(logging.DEBUG, logger="pet_harness.agent.result_parser"):
            _parse(raw)
        preview_records = [r for r in caplog.records if "preview" in r.getMessage()]
        assert preview_records
        for record in preview_records:
            message = record.getMessage()
            preview = message.split(": ", 1)[1]
            assert len(preview) <= 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
