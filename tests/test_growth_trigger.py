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

    assert growth.build_generation_context() == "new memory"
    assert growth.check_time_trigger("event-1") == GrowthOffer("event", "time_interval", "event-1")
    assert growth.check_time_trigger("event-2") is None
    assert store.get_setting("asset_pending_offer")["variant"] == "event"


def test_empty_memory_does_not_block_event_trigger(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = FakeAssetService()
    growth = GrowthTriggerService(store, service, "char-1", xp_per_level=6, event_interval_minutes=3)

    assert growth.check_time_trigger("event-1") == GrowthOffer("event", "time_interval", "event-1")
    assert store.get_setting("asset_pending_offer")["variant"] == "event"


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

    assert growth.check_time_trigger("event-1") == GrowthOffer("event", "time_interval", "event-1")
    assert store.get_setting("asset_generation_freeze") is None
