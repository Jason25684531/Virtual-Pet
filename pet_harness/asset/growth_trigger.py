from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pet_harness.asset.asset_contract import GrowthOffer
from pet_harness.asset.service import AssetService
from pet_harness.storage.sqlite_store import SQLiteStore


class GrowthTriggerService:
    def __init__(self, store: SQLiteStore, asset_service: AssetService, character_id: str, xp_per_level: int, event_interval_minutes: float) -> None:
        self._store = store
        self._asset_service = asset_service
        self._character_id = character_id
        self._xp_per_level = max(1, xp_per_level)
        self._event_interval = timedelta(minutes=event_interval_minutes)

    def on_xp_awarded(self, xp_total: int, source_event_id: str) -> GrowthOffer | None:
        level = max(0, xp_total) // self._xp_per_level
        previous = int(self._store.get_setting("asset_last_triggered_level", 0) or 0)
        if level <= previous:
            return None
        offer = self._offer("development", "level_up", source_event_id)
        if offer is not None:
            self._store.set_setting("asset_last_triggered_level", level)
        return offer

    def check_time_trigger(self, source_event_id: str) -> GrowthOffer | None:
        previous = self._store.get_setting("asset_last_event_variant_at")
        if previous:
            try:
                if datetime.now(UTC) - datetime.fromisoformat(previous) < self._event_interval:
                    return None
            except ValueError:
                pass
        offer = self._offer("event", "time_interval", source_event_id)
        if offer is not None:
            self._store.set_setting("asset_last_event_variant_at", datetime.now(UTC).isoformat())
        return offer

    def build_generation_context(self) -> str:
        now = datetime.now(UTC).isoformat()
        with self._store.connect() as conn:
            rows = conn.execute("SELECT text FROM memory_items WHERE character_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC", (self._character_id, now)).fetchall()
        return "\n".join(str(row["text"]) for row in rows)[:2000]

    def _offer(self, variant: str, reason: str, source_event_id: str) -> GrowthOffer | None:
        if self._store.get_setting("asset_pending_offer"):
            return None
        if not self.build_generation_context():
            return None
        offer = GrowthOffer(variant, reason, source_event_id)
        self._store.set_setting("asset_pending_offer", offer.to_dict())
        return offer
