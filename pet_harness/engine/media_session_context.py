from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

# 瀏覽器分頁與播放器活不過行程結束,但 30 分鐘的 TTL 會活下來。重啟後沿用舊
# playback 會讓「暫停」去控制一個已經不存在的工作階段,所以 playback 另外綁
# 本次執行的 runtime id;articles 只是清單快取,重啟後仍可用來追問第幾則。
RUNTIME_ID = uuid4().hex


class MediaSessionContext:
    KEY = "media_session_context"

    def __init__(self, store) -> None:
        self.store = store

    def save(self, articles: list[dict] | None = None, playback: dict | None = None) -> None:
        current = self.store.get_setting(self.KEY, {}) or {}
        if articles is not None:
            current["articles"] = articles
        if playback is not None:
            current["playback"] = playback
            current["playback_runtime_id"] = RUNTIME_ID
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.set_setting(self.KEY, current)

    def load(self) -> dict:
        current = self.store.get_setting(self.KEY, {}) or {}
        try:
            expired = datetime.now(timezone.utc) - datetime.fromisoformat(current["updated_at"]) > timedelta(minutes=30)
        except (KeyError, ValueError):
            expired = True
        if expired:
            return {}
        if current.get("playback") is not None and current.get("playback_runtime_id") != RUNTIME_ID:
            current = {key: value for key, value in current.items() if key not in {"playback", "playback_runtime_id"}}
        return current

    @staticmethod
    def follow_up_index(text: str) -> int | None:
        values = {"第一則": 1, "第二則": 2, "第三則": 3, "剛才那篇": 1}
        return next((index for phrase, index in values.items() if phrase in text), None)
