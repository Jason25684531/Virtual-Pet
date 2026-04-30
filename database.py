"""
ECHOES — SQLite 持久記憶管理器。

以 LangChain SQLChatMessageHistory 為底層，依 character_id 區分 session，
實現跨重啟的對話記憶。

設計原則：
- session_id = 正規化的 character_id（小寫、去空白）
- DB 每個 session 保留最新 MAX_MESSAGES_PER_SESSION 筆；超過時刪除最舊的
- 提供給 BrainEngine 的 Context 僅取最近 limit 筆（預設 20）
- 所有寫入操作均透過 SQLChatMessageHistory，不直接操作 SQLite
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    from langchain_community.chat_message_histories import SQLChatMessageHistory
    from langchain_core.messages import BaseMessage
    LANGCHAIN_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover
    SQLChatMessageHistory = None  # type: ignore[assignment,misc]
    BaseMessage = None  # type: ignore[assignment]
    LANGCHAIN_IMPORT_ERROR = exc

PROJECT_ROOT = Path(__file__).resolve().parent

_SESSION_NORMALIZE_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _normalize_session_id(character_id: str | None) -> str:
    """將 character_id 轉換為合法的 SQLite session_id。"""
    raw = str(character_id or "default").strip()
    normalized = _SESSION_NORMALIZE_RE.sub("_", raw).lower()
    return normalized or "default"


class SQLiteMemoryManager:
    """依 character_id 管理 SQLite 對話記憶。

    - session 以 character_id 隔離，切換角色自動切換記憶上下文
    - 每個 session 最多保留 MAX_MESSAGES_PER_SESSION 筆（預設 200）
    - get_recent_messages() 僅取最近 limit 筆送入 LLM context
    """

    MAX_MESSAGES_PER_SESSION: int = 200

    def __init__(
        self,
        db_path: Path | str | None = None,
        max_messages: int | None = None,
    ):
        self._db_path = Path(db_path) if db_path else PROJECT_ROOT / "local_memory.db"
        if max_messages is not None:
            self.MAX_MESSAGES_PER_SESSION = int(max_messages)
        self._connection_string = f"sqlite:///{self._db_path}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recent_messages(self, character_id: str | None, limit: int = 20) -> list:
        """取最近 limit 筆訊息作為 LLM context（不超過 MAX_MESSAGES_PER_SESSION）。"""
        if SQLChatMessageHistory is None:
            return []
        history = self._get_history(character_id)
        try:
            msgs = history.messages
        except Exception:  # pragma: no cover
            return []
        if not msgs:
            return []
        return msgs[-limit:] if len(msgs) > limit else msgs

    def add_exchange(
        self,
        character_id: str | None,
        human_text: str,
        ai_text: str,
    ) -> None:
        """記錄一輪對話（Human + AI），並在超過上限時修剪舊訊息。"""
        if SQLChatMessageHistory is None:
            return
        human = str(human_text or "").strip()
        ai = str(ai_text or "").strip()
        if not human or not ai:
            return

        history = self._get_history(character_id)
        try:
            history.add_user_message(human)
            history.add_ai_message(ai)
        except Exception:  # pragma: no cover
            return

        self._prune_if_needed(history)

    def clear_session(self, character_id: str | None) -> None:
        """清空特定角色的全部記憶。"""
        if SQLChatMessageHistory is None:
            return
        try:
            self._get_history(character_id).clear()
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_history(self, character_id: str | None) -> "SQLChatMessageHistory":
        session_id = _normalize_session_id(character_id)
        # 使用 connection 參數（新版 API，避免 DeprecationWarning）
        return SQLChatMessageHistory(
            session_id=session_id,
            connection=self._connection_string,
        )

    def _prune_if_needed(self, history: "SQLChatMessageHistory") -> None:
        """若訊息數超過 MAX_MESSAGES_PER_SESSION，保留最新的 MAX 筆。"""
        try:
            msgs = history.messages
        except Exception:  # pragma: no cover
            return

        if len(msgs) <= self.MAX_MESSAGES_PER_SESSION:
            return

        keep = msgs[-self.MAX_MESSAGES_PER_SESSION:]
        try:
            history.clear()
            for msg in keep:
                history.add_message(msg)
        except Exception:  # pragma: no cover
            pass
