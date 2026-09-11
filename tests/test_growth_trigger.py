from datetime import UTC, datetime, timedelta

from pet_harness.asset.asset_contract import AssetResponse, GrowthOffer
from pet_harness.asset.growth_trigger import GrowthTriggerService
from pet_harness.storage.sqlite_store import SQLiteStore


class FakeAssetService:
    def __init__(self):
        self.requests = []

    def create_asset(self, request):
        self.requests.append(request)
        return AssetResponse(request_id=request.request_id, status="queued")


def test_level_trigger_is_once_per_level_and_records_reason(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    with store.connect() as conn, conn:
        conn.execute("INSERT INTO memory_items (memory_id, character_id, user_id, memory_key, memory_type, text, source_event_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("context", "char-1", "default", "context", "semantic", "memory", "event-1", datetime.now(UTC).isoformat()))
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.on_xp_awarded(5, "event-1") is None
    offer = growth.on_xp_awarded(6, "event-2")
    assert offer == GrowthOffer("development", "level_up", "event-2")
    assert growth.on_xp_awarded(8, "event-3") is None
    assert store.get_setting("asset_pending_offer")["variant"] == "development"


def test_context_excludes_expired_and_time_trigger_persists(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    now = datetime.now(UTC)
    with store.connect() as conn, conn:
        conn.execute("INSERT INTO memory_items (memory_id, character_id, user_id, memory_key, memory_type, text, source_event_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("new", "char-1", "default", "new", "semantic", "new memory", "e", now.isoformat(), None))
        conn.execute("INSERT INTO memory_items (memory_id, character_id, user_id, memory_key, memory_type, text, source_event_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("expired", "char-1", "default", "old", "episodic", "expired memory", "e", (now - timedelta(days=2)).isoformat(), (now - timedelta(days=1)).isoformat()))
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)
    # 建構會把基準重設為本次場次，所以「間隔已過」必須在建構後才成立。
    store.set_setting("asset_last_event_variant_at", (now - timedelta(minutes=10)).isoformat())

    assert growth.build_generation_context() == "new memory"
    assert growth.check_time_trigger("event-1") == GrowthOffer("event", "time_interval", "event-1")
    assert growth.check_time_trigger("event-2") is None
    assert store.get_setting("asset_pending_offer")["variant"] == "event"


def test_first_ever_time_trigger_check_only_establishes_baseline(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.check_time_trigger("event-1") is None
    assert store.get_setting("asset_pending_offer") is None
    assert store.get_setting("asset_last_event_variant_at") is not None


def test_empty_memory_does_not_block_event_trigger(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)
    store.set_setting("asset_last_event_variant_at", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())

    assert growth.check_time_trigger("event-1") == GrowthOffer("event", "time_interval", "event-1")
    assert store.get_setting("asset_pending_offer")["variant"] == "event"


def test_stale_cross_session_timestamp_does_not_trigger_immediately(tmp_path):
    """節慶間隔以本次場次為基準：上次關程式留下的時間戳不得讓第一個回合立刻觸發。"""
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    store.set_setting("asset_last_event_variant_at", (datetime.now(UTC) - timedelta(days=1)).isoformat())

    growth = GrowthTriggerService(store, FakeAssetService(), "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.check_time_trigger("event-1") is None
    assert store.get_setting("asset_pending_offer") is None


def test_rebuilding_service_resets_the_interval_baseline(tmp_path):
    """每次 switch_character 都會重建本服務，重建即代表基準重新起算。"""
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    GrowthTriggerService(store, FakeAssetService(), "char-1", xp_per_level=6, event_interval_minutes=3)
    store.set_setting("asset_last_event_variant_at", (datetime.now(UTC) - timedelta(hours=5)).isoformat())

    switched = GrowthTriggerService(store, FakeAssetService(), "char-2", xp_per_level=6, event_interval_minutes=3)

    assert switched.check_time_trigger("event-1") is None


def test_festival_shortcut_still_ignores_the_interval_threshold(tmp_path):
    """拉長間隔與重設基準都不能影響 F 快捷鍵的手動觸發。"""
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    growth = GrowthTriggerService(store, FakeAssetService(), "char-1", xp_per_level=6, event_interval_minutes=600)

    assert growth.trigger_festival_event("shortcut-f-1") == GrowthOffer("event", "shortcut_f", "shortcut-f-1")


def test_festival_shortcut_trigger_creates_event_offer_without_waiting(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    growth = GrowthTriggerService(store, FakeAssetService(), "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.trigger_festival_event("shortcut-f-1") == GrowthOffer("event", "shortcut_f", "shortcut-f-1")
    assert store.get_setting("asset_pending_offer")["reason"] == "shortcut_f"


def test_empty_memory_still_blocks_development_trigger(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.on_xp_awarded(6, "event-1") is None
    assert store.get_setting("asset_pending_offer") is None


def test_generation_freeze_blocks_new_offers(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    store.set_setting("asset_generation_freeze", {"created_at": datetime.now(UTC).isoformat()})
    store.set_setting("asset_last_event_variant_at", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.check_time_trigger("event-1") is None
    assert growth.on_xp_awarded(6, "event-2") is None
    assert store.get_setting("asset_pending_offer") is None


def test_expired_generation_freeze_is_released(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    store.set_setting("asset_generation_freeze", {"created_at": "2000-01-01T00:00:00+00:00"})
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)
    store.set_setting("asset_last_event_variant_at", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())

    assert growth.check_time_trigger("event-1") == GrowthOffer("event", "time_interval", "event-1")
    assert store.get_setting("asset_generation_freeze") is None
