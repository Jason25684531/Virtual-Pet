from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_WHITESPACE = re.compile(r"\s+")
_PREFIXES = ("可不可以", "能不能", "可以", "麻煩", "幫我", "請", "能")
# 開頭的語氣詞：感嘆詞一律剝掉；「那你／你／那」只在後面緊接請求詞時才剝，
# 否則「你看新聞了嗎」會被剝成「看新聞了嗎」而誤觸發新聞技能。
_LEAD = re.compile(r"^(?:(?:欸|誒|嗯|對了)[,、\s]*|(?:那你|你|那)(?=可不可以|能不能|可以|能|幫我|請|麻煩))")
_ENDING = re.compile(r"(?:[嗎呢吧]|[?？!！。])+\s*$")


@dataclass(frozen=True)
class NormalizedInput:
    raw_text: str
    normalized_text: str
    stripped_text: str


def normalize(text: str | None) -> NormalizedInput:
    """Return raw, Unicode-normalized, and polite-shell-stripped input."""
    raw = str(text or "")
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", raw).casefold()).strip()
    stripped = normalized
    while True:
        lead = _LEAD.match(stripped)
        if lead:
            stripped = stripped[lead.end():].lstrip()
            continue
        prefix = next((value for value in _PREFIXES if stripped.startswith(value)), None)
        if prefix is None:
            break
        stripped = stripped[len(prefix):].lstrip()
    while True:
        trimmed = _ENDING.sub("", stripped).rstrip()
        if trimmed == stripped:
            break
        stripped = trimmed
    return NormalizedInput(raw, normalized, stripped or normalized)
