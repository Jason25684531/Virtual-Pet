from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PyQt5.QtCore import QObject, pyqtSlot

from pet_harness.ui.character_ui_service import CharacterUiService

if TYPE_CHECKING:
    from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
    from ui.transparent_window import TransparentWindow


class CharacterUiBridge(QObject):
    """薄 QObject 包裝：把 CharacterUiService 的呼叫結果序列化為 JSON 字串給 JS。

    所有 slot 統一回傳 {"ok": true, "data": ...} 或 {"ok": false, "error": "..."}；
    service 拋出的例外在此捕捉，不讓例外穿透 QWebChannel。
    """

    def __init__(self, service: CharacterUiService, window: "TransparentWindow", adapter: "PyQtHarnessAdapter | None" = None) -> None:
        super().__init__(window)
        self._service = service
        self._window = window
        self._adapter = adapter

    def _ok(self, data: Any) -> str:
        return json.dumps({"ok": True, "data": data}, ensure_ascii=False)

    def _error(self, exc: Exception) -> str:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    @pyqtSlot(result=str)
    def listCharacters(self) -> str:
        try:
            result = self._service.list_characters()
            print(f"[BRIDGE] listCharacters OK count={len(result)}", flush=True)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            print(f"[BRIDGE] listCharacters ERROR: {exc}", flush=True)
            return self._error(exc)

    @pyqtSlot(result=str)
    def listPresets(self) -> str:
        try:
            return self._ok(self._service.list_presets())
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def createFromPreset(self, preset_id: str, name: str) -> str:
        try:
            result = self._service.create_from_preset(preset_id, name or None)
            self._window.on_character_switched(result)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def switchCharacter(self, character_id: str) -> str:
        try:
            result = self._service.switch_character(character_id)
            self._window.on_character_switched(result)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def deleteCharacter(self, character_id: str) -> str:
        try:
            return self._ok(self._service.delete_character(character_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(result=str)
    def getActiveState(self) -> str:
        try:
            return self._ok(self._service.get_active_state())
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def triggerSkill(self, skill_id: str) -> str:
        try:
            result = self._service.trigger_skill(skill_id)
            self._window.consume_interaction_result(result, message="Skill executed.")
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(result=str)
    def getProviderStatus(self) -> str:
        try:
            if not self._adapter:
                raise RuntimeError("adapter not available")
            result = self._adapter.get_provider_status()
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def configureProvider(self, provider: str) -> str:
        """受控 settings 入口:設定全域 Provider(api/ollama);文字提交不得夾帶 Provider。"""
        try:
            if not self._adapter:
                raise RuntimeError("adapter not available")
            return self._ok(self._adapter.configure_provider(provider))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, bool, result=str)
    def setSkillEnabled(self, skill_name: str, enabled: bool) -> str:
        try:
            if not self._adapter:
                raise RuntimeError("adapter not available")
            result = self._adapter.set_skill_enabled(skill_name, enabled)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)
