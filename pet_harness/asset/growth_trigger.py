from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pet_harness.asset.asset_contract import GrowthOffer
from pet_harness.asset.service import AssetService
from pet_harness.storage.sqlite_store import SQLiteStore


def is_generation_frozen(store: SQLiteStore, ttl_hours: float) -> bool:
    """互動凍結窗(D4):PNG 完工後至 webm 決定前,XP 與觸發偵測皆短路。
    逾時(created_at 超過 ttl_hours)視為失效並自動清除,避免 worker 崩潰時永久卡死。"""
    freeze = store.get_setting("asset_generation_freeze")
    if not freeze:
        return False
    try:
        created_at = datetime.fromisoformat(str(freeze.get("created_at", "")))
    except ValueError:
        return False
    if datetime.now(UTC) - created_at > timedelta(hours=ttl_hours):
        store.set_setting("asset_generation_freeze", None)
        return False
    return True


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
        if previous is None:
            # 沒有基準時間就代表這是本次啟動/角色的第一次檢查——先記錄基準,
            # 不能把「從未檢查過」誤判成「間隔已過」而立刻觸發算圖。
            self._store.set_setting("asset_last_event_variant_at", datetime.now(UTC).isoformat())
            return None
        try:
            if datetime.now(UTC) - datetime.fromisoformat(previous) < self._event_interval:
                return None
        except ValueError:
            pass
        offer = self._offer("event", "time_interval", source_event_id)
        if offer is not None:
            self._store.set_setting("asset_last_event_variant_at", datetime.now(UTC).isoformat())
        return offer

    def on_interaction(self, source_event_id: str) -> GrowthOffer | None:
        """Mock-only interaction milestones; greeting calls never enter this method."""
        import config

        count = int(self._store.get_setting("interaction_count", 0) or 0) + 1
        self._store.set_setting("interaction_count", count)
        triggered = set(self._store.get_setting("asset_triggered_interaction_thresholds", []) or [])
        threshold = next(
            (value for value in config.INTERACTION_TRIGGER_THRESHOLDS
             if count >= value and value not in triggered),
            None,
        )
        if threshold is None:
            return None

        preset = {3: "development_a", 6: "development_b"}.get(threshold)
        metadata = {"threshold": threshold, "preset": preset}
        if threshold == 9:
            metadata.update({"output": None, "preset": None})
        variant = preset or "development"
        offer = self._offer(variant, "interaction_milestone", source_event_id, require_context=False, metadata=metadata)
        if offer is not None:
            self._store.set_setting(
                "asset_triggered_interaction_thresholds",
                sorted((*triggered, threshold)),
            )
        return offer

    def trigger_festival_event(self, source_event_id: str) -> GrowthOffer | None:
        """由快捷鍵主動建立節慶 event offer，不受定時門檻限制。"""
        offer = self._offer("event", "shortcut_f", source_event_id)
        if offer is not None:
            self._store.set_setting("asset_last_event_variant_at", datetime.now(UTC).isoformat())
        return offer

    def reset(self) -> None:
        for key in (
            "interaction_count",
            "asset_triggered_interaction_thresholds",
            "asset_pending_offer",
            "asset_last_triggered_level",
        ):
            self._store.set_setting(key, None)

    def build_generation_context(self) -> str:
        now = datetime.now(UTC).isoformat()
        with self._store.connect() as conn:
            rows = conn.execute("SELECT text FROM memory_items WHERE character_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at DESC", (self._character_id, now)).fetchall()
        return "\n".join(str(row["text"]) for row in rows)[:2000]

    def _offer(
        self,
        variant: str,
        reason: str,
        source_event_id: str,
        *,
        require_context: bool = True,
        metadata: dict | None = None,
    ) -> GrowthOffer | None:
        if self._store.get_setting("asset_pending_offer"):
            return None
        import config
        if is_generation_frozen(self._store, config.PREVIEW_OFFER_TTL_HOURS):
            return None
        if require_context and variant == "development" and not self.build_generation_context():
            return None
        offer = GrowthOffer(variant, reason, source_event_id, metadata or {})
        self._store.set_setting("asset_pending_offer", offer.to_dict())
        return offer
