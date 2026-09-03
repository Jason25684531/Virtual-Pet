from __future__ import annotations

import logging
import threading
from pathlib import Path

import config
from character_library import CharacterLibrary
from pet_harness.asset.asset_job_worker import AssetJobWorker
from pet_harness.asset.asset_orchestrator import AssetOrchestrator
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.comfyui_asset_service import ComfyUIAssetService
from pet_harness.asset.comfyui_client import ComfyUIClient
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.asset.service import AssetService
from pet_harness.storage.sqlite_store import SQLiteStore


LOGGER = logging.getLogger(__name__)


def build_asset_service(store: SQLiteStore, character_id: str | None, library: CharacterLibrary) -> AssetService:
    if not config.COMFYUI_ENABLED:
        LOGGER.warning("ComfyUI disabled by COMFYUI_ENABLED, falling back to MockAssetService — queued jobs will not be processed by ComfyUI; mock worker active")
        return MockAssetService(store, character_id=character_id, library=library)
    client = ComfyUIClient(config.COMFYUI_BASE_URL, config.COMFYUI_WS_URL, config.COMFYUI_TIMEOUT_SEC)
    if not client.health_check():
        LOGGER.warning("ComfyUI health check failed at %s, falling back to MockAssetService — queued jobs will not be processed by ComfyUI; mock worker active", config.COMFYUI_BASE_URL)
        return MockAssetService(store, character_id=character_id, library=library)
    root = Path(__file__).resolve().parents[2]
    orchestrator = AssetOrchestrator(
        AssetRepository(store),
        root / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json",
        root / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json",
        config.COMFYUI_TIMEOUT_SEC,
        config.COMFYUI_MAX_RETRIES,
        root / "ComfyUI_Json" / "AIA_2026_character validation_260811_API.json",
        root / "ComfyUI_Json" / "AIA_2026_background_gen_260728.json",
        config.COMFYUI_VIDEO_TIMEOUT_SEC,
    )
    worker = AssetJobWorker(orchestrator.repository, orchestrator, client, library)

    def _resume() -> None:
        # 啟動即恢復中斷 job 並排空殘留佇列;否則 App 重啟後 queued job 永遠不會被處理。
        worker.recover()
        while worker.run_once():
            pass

    threading.Thread(target=_resume, daemon=True, name="comfyui-asset-resume").start()
    return ComfyUIAssetService(orchestrator, worker, character_id, library)
