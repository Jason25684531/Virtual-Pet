import re

_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_reply_language(text: str) -> str:
    """無 CJK 且含拉丁字母 -> "en",其餘(含中英混雜、空字串)-> "zh"。"""
    text = text or ""
    return "en" if _LATIN.search(text) and not _CJK.search(text) else "zh"
