from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

LOGGER = logging.getLogger(__name__)

from PyQt5.QtCore import QObject, QTimer, pyqtSlot

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
            self._notify_character_switched(result)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(result=str)
    def pickCharacterImage(self) -> str:
        """QtWebEngine 的 <input type=file> 拿不到真實路徑，改由原生對話框選檔。"""
        try:
            from PyQt5.QtWidgets import QFileDialog

            path, _ = QFileDialog.getOpenFileName(
                self._window, "選擇角色圖片", "", "Images (*.png *.jpg *.jpeg *.webp)"
            )
            return self._ok({"image_path": path})
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def createFromUpload(self, image_path: str, name: str) -> str:
        try:
            return self._ok(self._service.create_from_upload(image_path, name))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def getValidationStatus(self, job_id: str) -> str:
        try:
            return self._ok(self._service.get_validation_status(job_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def switchCharacter(self, character_id: str) -> str:
        try:
            result = self._service.switch_character(character_id)
            self._notify_character_switched(result)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    def _notify_character_switched(self, profile_payload: dict[str, Any]) -> None:
        """Run WebView work after the current QWebChannel slot returns its response.

        This runs outside the calling slot's try/except (deferred via QTimer), so an
        uncaught exception here would escape into a bare Qt callback. PyQt5 aborts the
        whole process with no traceback in that case, so it must be caught here instead.
        """
        QTimer.singleShot(0, lambda: self._safe_notify_character_switched(profile_payload))

    def _safe_notify_character_switched(self, profile_payload: dict[str, Any]) -> None:
        try:
            self._window.on_character_switched(profile_payload)
        except Exception:  # noqa: BLE001
            LOGGER.exception("on_character_switched failed for payload=%s", profile_payload)

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
    def listStyleVariants(self, character_id: str) -> str:
        try:
            result = self._service.list_style_variants(character_id)
            active = self._service.get_active_state()
            if active.get("character_id") == character_id and any(item.get("is_active") for item in result):
                self._window._apply_resolved_background(self._window._library.get_background_path(character_id))
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def applyStyle(self, character_id: str, variant: str) -> str:
        try:
            result = self._service.apply_style(character_id, variant)
            self._window.apply_character(character_id)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, bool, result=str)
    def confirmGrowthOffer(self, character_id: str, accept: bool) -> str:
        try:
            return self._ok(self._service.confirm_growth_offer(character_id, accept))
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

    @pyqtSlot(str, result=str)
    def getCustomization(self, character_id: str) -> str:
        try:
            return self._ok(self._service.get_customization(character_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def savePersona(self, character_id: str, persona: str) -> str:
        try:
            return self._ok(self._service.save_persona(character_id, persona or None))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def upsertLocalSkill(self, character_id: str, payload_json: str) -> str:
        try:
            payload = json.loads(payload_json)
            return self._ok(self._service.upsert_local_skill(character_id, payload))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def deleteLocalSkill(self, character_id: str, skill_id: str) -> str:
        try:
            return self._ok(self._service.delete_local_skill(character_id, skill_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, str, int, result=str)
    def saveSkillOverride(self, character_id: str, skill_id: str, aliases_json: str, priority: int) -> str:
        try:
            aliases = json.loads(aliases_json) if aliases_json else []
            result = self._service.save_skill_override(character_id, skill_id, aliases, priority)
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def previewSkillMatch(self, character_id: str, text: str) -> str:
        try:
            return self._ok(self._service.preview_skill_match(character_id, text))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)
