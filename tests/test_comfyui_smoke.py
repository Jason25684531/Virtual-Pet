import os

import pytest

from pet_harness.asset.comfyui_client import ComfyUIClient


@pytest.mark.skipif(os.getenv("COMFYUI_SMOKE") != "1", reason="set COMFYUI_SMOKE=1 with ComfyUI running")
def test_comfyui_is_reachable_for_manual_smoke():
    assert ComfyUIClient(os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")).health_check()
