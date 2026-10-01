from __future__ import annotations

import json
from pathlib import Path

from pet_harness import protected_resources
from pet_harness.models.events import RewardEvent
from pet_harness.models.reward import RewardRule
from pet_harness.storage.sqlite_store import SQLiteStore


class RewardManager:
    def __init__(self, store: SQLiteStore, rules_path: str | Path) -> None:
        self.store = store
        self.rules_path = Path(rules_path)

    def load_rules(self) -> list[RewardRule]:
        if not protected_resources.exists(self.rules_path):
            return []
        payload = json.loads(protected_resources.read_text(self.rules_path))
        raw_rules = payload.get("rewards", payload if isinstance(payload, list) else [])
        return [RewardRule.from_dict(rule) for rule in raw_rules]

    def check_unlocks(self, xp_total: int) -> list[RewardEvent]:
        unlocked: list[RewardEvent] = []
        for rule in self.load_rules():
            if xp_total < rule.xp_threshold:
                continue
            event = RewardEvent(
                reward_id=rule.reward_id,
                reward_type=rule.reward_type,
                unlock_reason=rule.unlock_reason,
                xp_threshold=rule.xp_threshold,
                inventory_item_id=rule.inventory_item_id,
                metadata=rule.metadata,
            )
            if self.store.unlock_reward(event, metadata=rule.metadata):
                unlocked.append(event)
        return unlocked
