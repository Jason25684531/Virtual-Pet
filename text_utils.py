"""
Pure-text utility helpers shared across host-side modules.
"""

from __future__ import annotations

import re

ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?P<bracket>[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*(?P<bare>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)


def sanitize_tts_text(text: str) -> str:
    """移除控制標記，保留可朗讀文字。"""
    stripped = ACTION_DIRECTIVE_PATTERN.sub("", text or "")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped
