"""omni_tts_server 的 HTTP 契約:注入 fake synth,不載 torch/GPU。bundle 不在 git 裡,缺了就 skip。"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest
import requests

SERVER_PATH = Path(__file__).resolve().parents[1] / "omni_tts_win_bundle" / "omni_tts_win_bundle" / "omni_tts_server.py"
pytestmark = pytest.mark.skipif(not SERVER_PATH.is_file(), reason="Omni TTS bundle not present")


@pytest.fixture
def server():
    # server 在 import 時會改 os.environ/sys.path(離線旗標),測完還原,不污染其他測試。
    with mock.patch.dict(os.environ), mock.patch.object(sys, "path", list(sys.path)):
        spec = importlib.util.spec_from_file_location("omni_tts_server_under_test", SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    calls = []
    gate = threading.Event()
    gate.set()

    def fake_synth(text):
        calls.append(text)
        gate.wait(5)
        if text == "boom":
            raise RuntimeError(r"C:\secret\path traceback")
        return b"RIFF-fake-wav"

    module.synth = fake_synth
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), module.H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield module, base, calls, gate
    gate.set()
    httpd.shutdown()
    httpd.server_close()


def _tts(base, body: bytes, timeout=5):
    return requests.post(f"{base}/tts", data=body, headers={"Content-Type": "application/json"}, timeout=timeout)


def test_default_bind_is_loopback(server):
    module, *_ = server
    assert module.HOST == "127.0.0.1"


def test_health_and_success(server):
    _, base, calls, _ = server
    health = requests.get(f"{base}/health", timeout=2).json()
    assert health["ok"] is True and health["speaker"] == "Omni"
    resp = _tts(base, json.dumps({"text": "  你好 R O G  "}).encode("utf-8"))
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "audio/wav"
    assert float(resp.headers["X-Synth-Seconds"]) >= 0
    assert resp.content == b"RIFF-fake-wav"
    assert calls == ["你好 R O G"]


def test_plain_utf8_text_is_accepted(server):
    _, base, calls, _ = server
    resp = requests.post(f"{base}/tts", data="歡迎來到 R O G 展區".encode("utf-8"), timeout=5)
    assert resp.status_code == 200 and calls == ["歡迎來到 R O G 展區"]


@pytest.mark.parametrize("body", [b"{not json", b'{"text": 1}', b"{}", b'{"text": "   "}', b"   ", b"", b"\xff\xfe"])
def test_invalid_body_is_400_without_inference(server, body):
    _, base, calls, _ = server
    resp = _tts(base, body)
    assert resp.status_code == 400
    assert resp.json()["ok"] is False
    assert calls == []


def test_oversize_is_413_without_inference(server):
    _, base, calls, _ = server
    assert _tts(base, json.dumps({"text": "字" * 2001}).encode("utf-8")).status_code == 413
    assert _tts(base, b" " * (16 * 1024 + 1)).status_code == 413
    assert calls == []


def test_unknown_path_is_404(server):
    _, base, _, _ = server
    assert requests.get(f"{base}/nope", timeout=2).status_code == 404
    assert requests.post(f"{base}/nope", data=b"{}", timeout=2).status_code == 404


def test_synth_failure_is_500_without_leaking_details(server):
    _, base, _, _ = server
    resp = _tts(base, json.dumps({"text": "boom"}).encode())
    assert resp.status_code == 500
    assert resp.json() == {"ok": False, "error": "synth_failed"}
    assert "secret" not in resp.text


def test_concurrent_synth_is_503_and_health_stays_responsive(server):
    _, base, calls, gate = server
    gate.clear()
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", _tts(base, json.dumps({"text": "一"}).encode())))
    t.start()
    deadline = time.monotonic() + 3
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    busy = _tts(base, json.dumps({"text": "二"}).encode())
    assert busy.status_code == 503 and busy.json()["error"] == "busy"
    assert requests.get(f"{base}/health", timeout=2).status_code == 200
    gate.set()
    t.join(5)
    assert first["r"].status_code == 200
    assert calls == ["一"]  # busy 不排隊


def test_client_disconnect_releases_slot(server):
    module, base, calls, gate = server
    gate.clear()
    with pytest.raises(requests.exceptions.ReadTimeout):
        _tts(base, json.dumps({"text": "斷線"}).encode(), timeout=0.3)
    gate.set()
    deadline = time.monotonic() + 3
    while module._synth_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _tts(base, json.dumps({"text": "再來"}).encode()).status_code == 200


def test_truncated_body_does_not_block_forever(server):
    _, base, calls, _ = server
    host, port = base.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as s:
        s.sendall(b"POST /tts HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{\"text\":")
        start = time.monotonic()
        s.recv(1024)  # handler 5 秒讀取逾時後關閉連線
        assert time.monotonic() - start < 8
    assert calls == []


def test_missing_model_fails_startup(server, tmp_path, monkeypatch):
    module, *_ = server
    monkeypatch.setattr(module, "REQUIRED_FILES", (tmp_path / "missing.pth",))
    with pytest.raises(SystemExit, match="missing model files"):
        module._load_models()
