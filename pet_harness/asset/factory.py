from __future__ import annotations

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


def build_asset_service(store: SQLiteStore, character_id: str | None, library: CharacterLibrary) -> AssetService:
    if not config.COMFYUI_ENABLED or not character_id:
        return MockAssetService(store)
    client = ComfyUIClient(config.COMFYUI_BASE_URL, config.COMFYUI_WS_URL, config.COMFYUI_TIMEOUT_SEC)
    if not client.health_check():
        return MockAssetService(store)
    root = Path(__file__).resolve().parents[2]
    orchestrator = AssetOrchestrator(AssetRepository(store), root / "ComfyUI_Json" / "AIA_2026_video_gen_260720_api.json", root / "ComfyUI_Json" / "AIA_2026_image_gen_260720_api.json", config.COMFYUI_TIMEOUT_SEC, config.COMFYUI_MAX_RETRIES)
    return ComfyUIAssetService(orchestrator, AssetJobWorker(orchestrator.repository, orchestrator, client, library), character_id, library)
