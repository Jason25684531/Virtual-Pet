from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_WHITESPACE = re.compile(r"\s+")
_PREFIXES = ("可不可以", "能不能", "可以", "麻煩", "幫我", "請", "能")
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
