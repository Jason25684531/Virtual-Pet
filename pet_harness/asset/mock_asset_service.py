from __future__ import annotations

from typing import Any

import config
from character_library import CharacterLibrary
from pet_harness.asset.asset_models import AssetJob
from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.asset.mock_render_worker import MockRenderWorker
from pet_harness.asset.service import AssetService
from pet_harness.storage.sqlite_store import SQLiteStore


class MockAssetService(AssetService):
    def __init__(
        self,
        store: SQLiteStore,
        character_id: str | None = None,
        library: CharacterLibrary | None = None,
        duration_sec: float | None = None,
    ) -> None:
        self.store = store
        self.character_id = character_id
        self.library = library or CharacterLibrary()
        self.worker = MockRenderWorker(
            store,
            self.library,
            duration_sec if duration_sec is not None else config.MOCK_RENDER_DURATION_SEC,
        )

    def create_asset(self, request: AssetRequest) -> AssetResponse:
        metadata = dict(request.metadata)
        preset = self._preset_for(metadata) if request.asset_type == "variant_png" else None
        character_id = str(self.character_id or request.prompt_params.get("character_id") or "default")
        variant = str(metadata.get("variant_type") or ("event" if preset == "event" else "development"))
        job_id = f"mock-{request.request_id}"
        job = AssetJob(
            character_id,
            "variant_png" if request.asset_type == "variant_png" else request.asset_type,
            variant,
            request.request_id,
            job_id=job_id,
            metadata={
                **metadata,
                "request_id": request.request_id,
                "output": metadata.get("output", "preset"),
                "preset": preset,
            },
        )
        self.store.insert_asset_job(job.to_dict())
        response = AssetResponse(
            request_id=request.request_id,
            status="queued",
            asset_id=job_id,
            job_id=job_id,
            metadata={
                "service": "mock_asset_service",
                "queued": True,
                "preset": preset,
                "output": metadata.get("output", "preset"),
            },
        )
        self.store.log_asset_manifest(request, response)
        self.worker.run_job(job_id, variant, character_id, preset)
        return response

    @staticmethod
    def _preset_for(metadata: dict[str, Any]) -> str | None:
        if "output" in metadata and metadata["output"] is None:
            return None
        preset = str(metadata.get("preset") or "").strip()
        if preset:
            return preset
        return "event" if metadata.get("variant_type") == "event" else "development_a"

    def cancel(self) -> None:
        self.worker.cancel()

    def create_reward_asset_request(self, source_event_id: str, reward_id: str, behavior_id: str, variant_type: str = "development") -> AssetResponse:
        request = AssetRequest(
            asset_type="reward_asset",
            prompt_params={"reward_id": reward_id},
            source_event_id=source_event_id,
            requested_reward=reward_id,
            behavior_id=behavior_id,
            metadata={"source": "reward_unlock", "variant_type": variant_type},
        )
        return self.create_asset(request)

    def create_character_motion_request(self, source_event_id: str) -> AssetResponse:
        return self.create_asset(AssetRequest(asset_type="motion_set", prompt_params={}, source_event_id=source_event_id))

    def create_character_validation_request(self, upload_path: str, character_name: str, source_event_id: str) -> AssetResponse:
        return self.create_asset(AssetRequest(asset_type="character_validation", prompt_params={"upload_path": upload_path, "character_name": character_name}, source_event_id=source_event_id))

    def create_background_request(self, character_id: str, source_event_id: str) -> AssetResponse:
        return self.create_asset(AssetRequest(asset_type="background_png", prompt_params={"character_id": character_id}, source_event_id=source_event_id))

    def create_variant_motion_request(self, character_id: str, variant: str, source_png: str, source_event_id: str, trigger_reason: str = "") -> AssetResponse:
        return self.create_asset(AssetRequest(asset_type="motion_set", prompt_params={"character_id": character_id, "source_png": source_png}, source_event_id=source_event_id, metadata={"variant_type": variant, "trigger_reason": trigger_reason}))
