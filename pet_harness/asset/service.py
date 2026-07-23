from __future__ import annotations

from abc import ABC, abstractmethod

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse


class AssetService(ABC):
    @abstractmethod
    def create_asset(self, request: AssetRequest) -> AssetResponse:
        raise NotImplementedError

    @abstractmethod
    def create_reward_asset_request(self, source_event_id: str, reward_id: str, behavior_id: str, variant_type: str = "development") -> AssetResponse:
        raise NotImplementedError

    @abstractmethod
    def create_character_motion_request(self, source_event_id: str) -> AssetResponse:
        raise NotImplementedError
