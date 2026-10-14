"""共用測試支援:產品已無 mock provider,測試一律注入 fake LLMProviderAdapter。"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import onnxruntime  # noqa: F401 - load native DLLs before any Qt test creates QApplication
from PyQt5.QtCore import QEvent

# Qt callbacks may outlive pytest capture; native Windows streams must accept Chinese.
for stream in (sys.__stdout__, sys.__stderr__):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.models.provider import ProviderStatus, ProviderType
from pet_harness.runtime.provider_runtime import ProviderRuntime


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    from PyQt5.QtCore import QCoreApplication, Qt
    from PyQt5.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolate_test_cwd(monkeypatch, tmp_path, request, qt_app) -> Iterator[None]:
    """Keep relative runtime state inside pytest's temporary directory."""
    if request.node.get_closest_marker("uses_repo_cwd"):
        monkeypatch.chdir(Path(__file__).resolve().parents[1])
    else:
        monkeypatch.chdir(tmp_path)
    yield
    qt_app.processEvents()
    qt_app.sendPostedEvents(None, QEvent.DeferredDelete)


class FakeProvider:
    """deterministic 測試用 adapter;只供測試注入,不進產品 runtime。"""

    def __init__(self, reply_prefix: str = "[fake]") -> None:
        self.reply_prefix = reply_prefix
        self.calls: list[str] = []

    def generate_reply(self, event, matched_skill=None, prompt_text=None) -> ProviderReply:
        self.calls.append(event.text)
        payload = {
            "reply": f"{self.reply_prefix} {event.text}",
            "matched_skill": matched_skill.name if matched_skill else None,
            "behavior_hint": matched_skill.behavior if matched_skill else None,
            "confidence": 1.0 if matched_skill else 0.0,
            "tool_request": None,
            "notes": "fake provider",
        }
        return ProviderReply(
            reply=payload["reply"],
            provider_status=ProviderStatus(
                provider_type=ProviderType.API,
                healthy=True,
                message="fake provider ready",
            ),
            behavior_hint=payload["behavior_hint"],
            raw_text=json.dumps(payload, ensure_ascii=False),
            raw_json=payload,
            prompt_text=prompt_text,
        )


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def fake_provider_runtime(fake_provider) -> ProviderRuntime:
    return ProviderRuntime(provider=fake_provider)
