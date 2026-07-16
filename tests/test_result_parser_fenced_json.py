"""ResultParser 的 fenced JSON 解析回歸測試:長回覆／巢狀轉義不應該退回顯示原始文字。"""

from __future__ import annotations

from pet_harness.agent.result_parser import ResultParser


def test_fenced_json_with_long_escaped_newlines_parses_cleanly():
    """回歸測試:5 篇新聞的長 reply(含多個 \\n)過去會讓非貪婪 regex 抓失敗,
    導致整段原始 JSON(含 fence 與跳脫符號)被當成回覆顯示給使用者。"""
    raw = (
        '```json\n'
        '{\n'
        '  "reply": "今日的遊戲新聞有：\\n1. 《A》上線。\\n2. 《B》開跑。\\n3. 《C》公開先導CG。",\n'
        '  "matched_skill": "bahamut_daily_news",\n'
        '  "confidence": 1.0\n'
        '}\n'
        '```'
    )
    result = ResultParser().parse(raw, provider_type="ollama", fallback_reply="fallback")
    assert result.parser_status == "parsed_fenced_json"
    assert result.fallback_used is False
    assert "```" not in result.reply
    assert "\\n" not in result.reply
    assert "今日的遊戲新聞有：\n1. 《A》上線。\n2. 《B》開跑。\n3. 《C》公開先導CG。" == result.reply


def test_truncated_outer_json_falls_back_to_reply_field_only():
    """外層 JSON 被截斷(模型忘記收尾)時,至少要救出乾淨的 reply 內容,
    不能把整段帶跳脫符號的原始文字丟給使用者。"""
    raw = '```json\n{\n  "reply": "今日新聞：\\n1. 《A》上線。"\n'  # 故意不收尾
    result = ResultParser().parse(raw, provider_type="ollama", fallback_reply="fallback")
    assert result.parser_status == "parsed_reply_field_only"
    assert result.reply == "今日新聞：\n1. 《A》上線。"
    assert "```" not in result.reply


def test_completely_malformed_text_still_falls_back_to_default():
    result = ResultParser().parse("not json at all, no reply field", provider_type="ollama", fallback_reply="fallback text")
    assert result.parser_status == "fallback_invalid_json"
    assert result.reply == "fallback text"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
