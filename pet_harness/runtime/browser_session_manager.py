from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


MAX_ACTIVE_BROWSER_SESSIONS = 2


@dataclass
class BrowserSession:
    session_id: str
    kind: str
    browser: Any = None
    context: Any = None
    page: Any = None
    current_track: dict[str, Any] | None = None
    current_url: str | None = None
    playback_state: str = "unknown"
    last_activity_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "current_track": self.current_track,
            "current_url": self.current_url,
            "playback_state": self.playback_state,
            "last_activity_at": self.last_activity_at,
        }


class BrowserSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}

    def create(self, kind: str, **objects: Any) -> BrowserSession | None:
        if len(self._sessions) >= MAX_ACTIVE_BROWSER_SESSIONS:
            return None
        session = BrowserSession(session_id=uuid4().hex, kind=kind, **objects)
        self._sessions[session.session_id] = session
        return session

    def first(self, kind: str) -> BrowserSession | None:
        return next((session for session in self._sessions.values() if session.kind == kind), None)

    def close(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def snapshot(self, kind: str | None = None) -> dict[str, Any] | None:
        session = self.first(kind) if kind else next(iter(self._sessions.values()), None)
        return session.snapshot() if session else None
