from __future__ import annotations

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.asset.service import AssetService
from pet_harness.models.events import utc_now
from pet_harness.storage.sqlite_store import SQLiteStore


class MockAssetService(AssetService):
    def __init__(self, store: SQLiteStore, complete_immediately: bool = True) -> None:
        self.store = store
        self.complete_immediately = complete_immediately

    def create_asset(self, request: AssetRequest) -> AssetResponse:
        response = AssetResponse(
            request_id=request.request_id,
            status="completed" if self.complete_immediately else "queued",
            asset_id=f"mock-{request.request_id}",
            job_id=f"mock-{request.request_id}",
            file_path=f"assets/mock/{request.request_id}.webm" if self.complete_immediately else None,
            webm_key=(request.behavior_id or "idle") if self.complete_immediately else None,
            completed_at=utc_now() if self.complete_immediately else None,
            metadata={"service": "mock_asset_service", "queued": True},
        )
        self.store.log_asset_manifest(request, response)
        return response

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
