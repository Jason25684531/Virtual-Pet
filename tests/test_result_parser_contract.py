"""ResultParser 輸入 contract 回歸測試:涵蓋 root 型別、confidence normalization、
tool_request 結構驗證與 optional 欄位正規化。任何不可信 Provider 輸出都必須得到
有效的 AgentResult,不得拋出未捕捉例外。"""

from __future__ import annotations

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
        result = _parse('{"reply": "hi", "tool_request": {"tool_name": "timer_tool", "arguments": {}}}')
        assert result.tool_request == {"tool_name": "timer_tool", "arguments": {}}


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
