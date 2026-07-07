from __future__ import annotations

from abc import ABC, abstractmethod

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse


class AssetService(ABC):
    @abstractmethod
    def create_asset(self, request: AssetRequest) -> AssetResponse:
        raise NotImplementedError
