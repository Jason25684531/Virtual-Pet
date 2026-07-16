from __future__ import annotations

from datetime import datetime, timedelta, timezone


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
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.set_setting(self.KEY, current)

    def load(self) -> dict:
        current = self.store.get_setting(self.KEY, {}) or {}
        try:
            expired = datetime.now(timezone.utc) - datetime.fromisoformat(current["updated_at"]) > timedelta(minutes=30)
        except (KeyError, ValueError):
            expired = True
        return {} if expired else current

    @staticmethod
    def follow_up_index(text: str) -> int | None:
        values = {"第一則": 1, "第二則": 2, "第三則": 3, "剛才那篇": 1}
        return next((index for phrase, index in values.items() if phrase in text), None)
