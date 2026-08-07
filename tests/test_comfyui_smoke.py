import os
import json
import sys

import pytest

from pet_harness.asset.comfyui_client import ComfyUIClient


@pytest.mark.skipif(os.getenv("COMFYUI_SMOKE") != "1", reason="set COMFYUI_SMOKE=1 with ComfyUI running")
def test_comfyui_is_reachable_for_manual_smoke():
    assert ComfyUIClient(os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")).health_check()


def test_execution_error_is_not_reported_as_timeout(monkeypatch):
    class ErrorSocket:
        def recv(self):
            return json.dumps({"type": "execution_error", "data": {"prompt_id": "prompt-1", "exception_message": "broken workflow"}})

        def close(self):
            pass

    class FakeWebsocket:
        @staticmethod
        def create_connection(*_args, **_kwargs):
            return ErrorSocket()

    monkeypatch.setitem(sys.modules, "websocket", FakeWebsocket)
    monkeypatch.setattr(ComfyUIClient, "get_history", lambda *_args: {})

    with pytest.raises(RuntimeError, match="broken workflow"):
        ComfyUIClient("http://127.0.0.1:8188").watch_prompt("prompt-1", 1)
