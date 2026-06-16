from __future__ import annotations

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.asset.service import AssetService
from pet_harness.models.events import utc_now
from pet_harness.storage.sqlite_store import SQLiteStore


class MockAssetService(AssetService):
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def create_asset(self, request: AssetRequest) -> AssetResponse:
        response = AssetResponse(
            request_id=request.request_id,
            status="completed",
            asset_id=f"mock-{request.request_id}",
            file_path=f"assets/mock/{request.request_id}.webm",
            webm_key=request.behavior_id or "idle",
            completed_at=utc_now(),
            metadata={"service": "mock_asset_service"},
        )
        self.store.log_asset_manifest(request, response)
        return response

    def create_reward_asset_request(self, source_event_id: str, reward_id: str, behavior_id: str) -> AssetResponse:
        request = AssetRequest(
            asset_type="reward_asset",
            prompt_params={"reward_id": reward_id},
            source_event_id=source_event_id,
            requested_reward=reward_id,
            behavior_id=behavior_id,
            metadata={"source": "reward_unlock"},
        )
        return self.create_asset(request)
