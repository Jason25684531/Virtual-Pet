from __future__ import annotations

import json
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def save_keywords(keywords: set[str], path: str | Path) -> None:
    """由灌庫腳本呼叫，把關鍵詞/別名寫成執行期可讀的 sidecar，避免每次啟動重新解析 Markdown。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(keywords), ensure_ascii=False), encoding="utf-8")


def load_keywords(path: str | Path) -> frozenset[str]:
    """讀不到（尚未灌庫）時回傳空集合——閘門會判定一律不需要檢索，安全降級。"""
    try:
        return frozenset(json.loads(Path(path).read_text(encoding="utf-8")))
    except Exception:
        LOGGER.info("knowledge keywords sidecar unavailable at %s; retrieval gate defaults to closed", path)
        return frozenset()


def needs_retrieval(text: str, keywords: frozenset[str]) -> bool:
    """RAG 前的輕量判斷：本輪有沒有提到任何已知的遊戲知識關鍵詞/別名。

    ponytail: 對 ~2-3 千個詞做逐一 substring 掃描（O(n*m)），不是 Aho-Corasick。
    查詢字串通常數十字、詞表數千則，單次掃描是微秒等級，遠低於 3 秒預算；
    詞表成長到造成可量測延遲時，再換 Aho-Corasick 或 jieba 分詞比對。
    """
    if not text or not keywords:
        return False
    haystack = text.casefold()
    return any(keyword.casefold() in haystack for keyword in keywords)
