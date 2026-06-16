from __future__ import annotations

from pet_harness.models.skill import Skill
from pet_harness.storage.sqlite_store import SQLiteStore


class XPManager:
    def __init__(self, store: SQLiteStore, chat_xp: int = 2) -> None:
        self.store = store
        self.chat_xp = chat_xp

    def award_for_event(self, matched_skill: Skill | None = None) -> int:
        delta = matched_skill.xp_reward if matched_skill else self.chat_xp
        self.store.add_user_xp(delta)
        if matched_skill:
            self.store.add_skill_xp(matched_skill.name, delta)
        return delta
