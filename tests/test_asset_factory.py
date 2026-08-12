import logging

from character_library import CharacterLibrary
from pet_harness.asset import factory
from pet_harness.asset.comfyui_asset_service import ComfyUIAssetService
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.storage.sqlite_store import SQLiteStore


def test_mock_asset_fallback_logs_that_queued_jobs_are_not_processed(tmp_path, monkeypatch, caplog):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    monkeypatch.setattr(factory.config, "COMFYUI_ENABLED", False)

    with caplog.at_level(logging.WARNING, logger=factory.__name__):
        service = factory.build_asset_service(store, None, CharacterLibrary())

    assert isinstance(service, MockAssetService)
    assert "MockAssetService" in caplog.text
    assert "queued jobs will not be processed" in caplog.text


def test_comfy_service_initializes_with_the_current_validation_template(tmp_path, monkeypatch):
    class HealthyClient:
        def __init__(self, *args):
            pass

        def health_check(self):
            return True

    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    monkeypatch.setattr(factory.config, "COMFYUI_ENABLED", True)
    monkeypatch.setattr(factory, "ComfyUIClient", HealthyClient)

    service = factory.build_asset_service(store, None, CharacterLibrary())

    assert isinstance(service, ComfyUIAssetService)
