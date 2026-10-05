"""engine 依 AssetService.growth_mode 分支,而不是 isinstance 具體類別(consolidate-architecture 3.1)。"""
import pytest

from pet_harness.asset.comfyui_asset_service import ComfyUIAssetService
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.asset.service import AssetService


def test_each_implementation_declares_its_growth_mode():
    assert MockAssetService.growth_mode == "interaction"
    assert ComfyUIAssetService.growth_mode == "xp"


def test_growth_mode_is_part_of_the_abstract_contract():
    class Incomplete(AssetService):
        def create_asset(self, request): ...
        def create_reward_asset_request(self, *a, **k): ...
        def create_character_validation_request(self, *a, **k): ...
        def create_variant_motion_request(self, *a, **k): ...

    with pytest.raises(TypeError):
        Incomplete()


def test_engine_no_longer_imports_the_concrete_mock_service():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "pet_harness" / "engine" / "harness_engine.py").read_text(encoding="utf-8")
    assert "MockAssetService" not in source
