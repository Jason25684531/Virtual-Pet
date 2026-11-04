from __future__ import annotations

from abc import ABC, abstractmethod

from pet_harness.asset.asset_contract import AssetRequest, AssetResponse


class AssetService(ABC):
    @property
    @abstractmethod
    def growth_mode(self) -> str:
        """成長 offer 的觸發依據:"interaction"(互動次數門檻,Mock 用)或 "xp"(XP 升級,真實生成用)。
        engine 依這個屬性分支,不再用 isinstance 判斷具體類別。"""
        raise NotImplementedError

    @abstractmethod
    def create_asset(self, request: AssetRequest) -> AssetResponse:
        raise NotImplementedError

    @abstractmethod
    def create_reward_asset_request(self, source_event_id: str, reward_id: str, behavior_id: str, variant_type: str = "development") -> AssetResponse:
        raise NotImplementedError

    @abstractmethod
    def create_character_validation_request(self, upload_path: str, character_name: str, source_event_id: str) -> AssetResponse:
        raise NotImplementedError

    @abstractmethod
    def create_variant_motion_request(self, character_id: str, variant: str, source_png: str, source_event_id: str, trigger_reason: str = "") -> AssetResponse:
        raise NotImplementedError
