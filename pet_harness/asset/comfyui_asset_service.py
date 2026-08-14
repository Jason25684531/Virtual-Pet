from __future__ import annotations

import threading

from character_library import CharacterLibrary
from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.asset.asset_job_worker import AssetJobWorker
from pet_harness.asset.asset_orchestrator import AssetOrchestrator
from pet_harness.asset.service import AssetService


class ComfyUIAssetService(AssetService):
    def __init__(self, orchestrator: AssetOrchestrator, worker: AssetJobWorker, character_id: str | None, library: CharacterLibrary | None = None) -> None:
        self.orchestrator, self.worker, self.character_id, self.library = orchestrator, worker, character_id, library or CharacterLibrary()

    def _start_worker(self, name: str) -> None:
        def drain() -> None:
            while self.worker.run_once():
                pass
        threading.Thread(target=drain, daemon=True, name=name).start()

    def create_asset(self, request: AssetRequest) -> AssetResponse:
        if not self.character_id:
            return AssetResponse(request_id=request.request_id, status="failed", error_message="character is required for variant generation")
        source_path = self.library.get_preview_image_path(self.character_id)
        if not source_path:
            return AssetResponse(request_id=request.request_id, status="failed", error_message="character source image is unavailable")
        variant = str(request.metadata.get("variant_type", "development"))
        job = self.orchestrator.create_variant_png_job(
            self.character_id,
            source_path,
            variant,
            request.source_event_id,
            reward_id=request.requested_reward or "",
            generation_context=str(request.prompt_params.get("generation_context", "")),
            trigger_reason=str(request.metadata.get("trigger_reason", "")),
        )
        self._start_worker("comfyui-asset-worker")
        return AssetResponse(request_id=request.request_id, status="queued", job_id=job.job_id, asset_id=job.job_id, metadata={"service": "comfyui", "variant_type": variant})

    def create_reward_asset_request(self, source_event_id: str, reward_id: str, behavior_id: str, variant_type: str = "development") -> AssetResponse:
        return self.create_asset(AssetRequest(asset_type="variant_png", prompt_params={}, source_event_id=source_event_id, requested_reward=reward_id, behavior_id=behavior_id, metadata={"variant_type": variant_type}))

    def create_character_motion_request(self, source_event_id: str) -> AssetResponse:
        source_path = self.library.get_preview_image_path(self.character_id)
        if not source_path:
            return AssetResponse(request_id=source_event_id, status="failed", error_message="character source image is unavailable")
        parent, children = self.orchestrator.create_motion_set(self.character_id, source_path, source_event_id)
        self._start_worker("comfyui-motion-worker")
        return AssetResponse(request_id=source_event_id, status="queued", job_id=parent.job_id, asset_id=parent.job_id, metadata={"child_job_ids": [item.job_id for item in children]})

    def create_character_validation_request(self, upload_path: str, character_name: str, source_event_id: str) -> AssetResponse:
        job = self.orchestrator.create_character_validation_job(upload_path, character_name, source_event_id)
        self._start_worker("comfyui-validation-worker")
        return AssetResponse(request_id=source_event_id, status="queued", job_id=job.job_id, asset_id=job.job_id)

    def create_background_request(self, character_id: str, source_event_id: str) -> AssetResponse:
        source_path = self.library.get_preview_image_path(character_id)
        if not source_path:
            return AssetResponse(request_id=source_event_id, status="failed", error_message="character source image is unavailable")
        job = self.orchestrator.create_background_job(character_id, source_path, "assets/backgrounds/default_room.jpg", source_event_id)
        self._start_worker("comfyui-background-worker")
        return AssetResponse(request_id=source_event_id, status="queued", job_id=job.job_id, asset_id=job.job_id)
