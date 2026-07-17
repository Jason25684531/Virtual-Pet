from __future__ import annotations

import json
import logging
from pathlib import Path

from pet_harness.models.events import BehaviorEvent
from pet_harness.models.skill import Skill
from pet_harness.storage.sqlite_store import SQLiteStore

LOGGER = logging.getLogger(__name__)


class BehaviorManager:
    def __init__(self, store: SQLiteStore, behavior_map_path: str | Path) -> None:
        self.store = store
        self.behavior_map_path = Path(behavior_map_path)
        self.behavior_map = self._load_behavior_map()

    def resolve(self, matched_skill: Skill | None = None, action_motion_key: str | None = None) -> BehaviorEvent:
        """解析動作優先序：技能 behavior、action motion、persisted fallback behavior。

        behavior_state 只代表「無 skill 且無 action motion 時要播放的持久 fallback
        behavior」，其值恆為 behavior_map 驗證過的 behavior_id。skill behavior 與
        action motion 是一次性 transient presentation，絕不寫入 behavior_state，
        避免一次性動畫污染下一輪 fallback。
        """
        is_fallback_path = matched_skill is None and not action_motion_key
        requested = (
            matched_skill.behavior
            if matched_skill
            else (action_motion_key or self.store.get_behavior_state())
        )
        reason = "skill" if matched_skill else ("action_tag" if action_motion_key else "fallback")
        source_skill = matched_skill.name if matched_skill else None
        if action_motion_key and matched_skill is None:
            # action motion 已由既有 action-tag resolution path(CharacterLibrary)接受並提供；
            # 它不必存在於全域 behavior map，因為其 motion key 本身就是角色資產 key。
            behavior_id, webm_key = action_motion_key, action_motion_key
        else:
            behavior_id, webm_key = self._resolve_key(requested)
        if is_fallback_path:
            self.store.set_behavior_state(behavior_id)
        return BehaviorEvent(
            behavior_id=behavior_id,
            webm_key=webm_key,
            reason=reason,
            source_skill=source_skill,
        )

    def _resolve_key(self, behavior_id: str) -> tuple[str, str]:
        entry = self.behavior_map.get(behavior_id)
        if entry:
            return behavior_id, str(entry.get("webm_key", behavior_id))

        LOGGER.warning("Unknown behavior_id %s; falling back to idle", behavior_id)
        idle = self.behavior_map.get("idle", {"webm_key": "idle"})
        return "idle", str(idle.get("webm_key", "idle"))

    def _load_behavior_map(self) -> dict[str, dict[str, str]]:
        if not self.behavior_map_path.exists():
            return {"idle": {"webm_key": "idle"}}
        payload = json.loads(self.behavior_map_path.read_text(encoding="utf-8"))
        return payload.get("behaviors", payload)
