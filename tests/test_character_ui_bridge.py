from __future__ import annotations

import json
from types import SimpleNamespace

from PyQt5.QtCore import QObject

import ui.character_ui_bridge as bridge_module
from ui.transparent_window import HarnessUiBridge
from ui.character_ui_bridge import CharacterUiBridge


class _Window(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.switches: list[dict] = []

    def on_character_switched(self, payload: dict) -> None:
        self.switches.append(payload)


class _Service:
    def switch_character(self, character_id: str) -> dict:
        return {"character_id": character_id}


def test_switch_character_returns_webchannel_response_before_touching_webview(monkeypatch):
    """Avoid re-entering QtWebChannel by scheduling the WebView update after its response."""
    window = _Window()
    callbacks = []
    monkeypatch.setattr(bridge_module.QTimer, "singleShot", lambda _delay, callback: callbacks.append(callback))
    bridge = CharacterUiBridge(_Service(), window)

    response = json.loads(bridge.switchCharacter("char-1"))

    assert response == {"ok": True, "data": {"character_id": "char-1"}}
    assert window.switches == []
    assert len(callbacks) == 1

    callbacks.pop()()

    assert window.switches == [{"character_id": "char-1"}]


def test_refresh_state_ignores_a_webchannel_call_before_window_initialization():
    """The page may emit refreshState while TransparentWindow is still being constructed."""
    window = SimpleNamespace()

    HarnessUiBridge.refreshState(SimpleNamespace(_window=window))


def test_refresh_state_updates_an_initialized_window():
    calls = []
    window = SimpleNamespace(_adapter=object(), refresh_agentic_ui=lambda: calls.append("refresh"))

    HarnessUiBridge.refreshState(SimpleNamespace(_window=window))

    assert calls == ["refresh"]
