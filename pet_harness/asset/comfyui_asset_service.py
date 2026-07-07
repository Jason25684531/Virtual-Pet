from __future__ import annotations

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse
from pet_harness.asset.service import AssetService


class ComfyUIAssetService(AssetService):
    def create_asset(self, request: AssetRequest) -> AssetResponse:
        return AssetResponse(
            request_id=request.request_id,
            status="queued",
            metadata={"service": "comfyui_asset_service", "implemented": False},
        )
