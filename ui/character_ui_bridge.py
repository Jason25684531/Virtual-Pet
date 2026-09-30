from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

LOGGER = logging.getLogger(__name__)

from PyQt5.QtCore import QObject, QTimer, pyqtSlot

from pet_harness.runtime.qt_background_executor import QtBackgroundExecutor
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
        # listPresets 的完整摘要(xp/playtime/missing_assets)離開同步回傳路徑,
        # 改在這個背景執行緒上算完再用 hydratePresetSummaries 推回前端;
        # 避免 Qt 主執行緒被資產世代解析卡住,見 fix-create-screen-stall 決策 2。
        # ponytail: 靠 Qt parent-child(self→window)的 deleteLater 級聯回收,
        # 沒有接到 main.py composition root 的 coordinator.lifecycle 關閉清單
        # (CharacterUiBridge 在 TransparentWindow._init_webview 建構,尚未拿到
        # lifecycle 參照)。工作本身是快速、唯讀、冪等的摘要計算,關閉時中止
        # 最壞只印一則 Qt 警告,不影響資料;若未來需要保證關閉時等待收尾,
        # 把 lifecycle 參照傳進 CharacterUiBridge 建構子再註冊。
        self._background = QtBackgroundExecutor(self)

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
        """回傳廉價摘要立即繪製分頁;完整摘要(xp/playtime/missing_assets)在
        背景算完後另以 hydratePresetSummaries 推回前端,不佔用這次同步回傳
        (fix-create-screen-stall 決策 2 —— 真正的熱點在資產世代解析,不在
        這裡,但只要它還留在這條同步路徑上就一樣會卡住 Qt 主執行緒)。"""
        try:
            result = self._service.list_presets_fast()
            character_ids = [str(item.get("character_id") or "") for item in result]
            self._enrich_presets_async([cid for cid in character_ids if cid])
            return self._ok(result)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    def _enrich_presets_async(self, character_ids: list[str]) -> None:
        if not character_ids:
            return

        def job():
            return self._service.enrich_preset_summaries(character_ids)

        def on_done(ok: bool, message: str, payload) -> None:
            if not ok:
                print(f"[BRIDGE] enrich_preset_summaries ERROR: {message}", flush=True)
                return
            self._window._run_javascript("hydratePresetSummaries", payload)

        self._background.submit(job, on_done)

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

    @pyqtSlot(str, result=str)
    def listRenderJobs(self, character_id: str) -> str:
        try:
            return self._ok(self._service.list_render_jobs(character_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, str, result=str)
    def selectStyleGeneration(self, character_id: str, variant: str, asset_id: str) -> str:
        try:
            result = self._service.select_style_generation(character_id, variant, asset_id)
            if character_id == self._service.get_active_state().get("character_id"):
                self._window.apply_character(character_id)
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

    def trigger_festival_event(self) -> dict[str, Any]:
        return self._service.trigger_festival_event()

    @pyqtSlot(str, bool, result=str)
    def confirmMotionGeneration(self, character_id: str, accept: bool) -> str:
        try:
            return self._ok(self._service.confirm_motion_generation(character_id, accept))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, result=str)
    def listSceneBackgrounds(self, character_id: str) -> str:
        try:
            return self._ok(self._service.list_scene_backgrounds(character_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def applyScene(self, character_id: str, scene_id: str) -> str:
        try:
            result = self._service.apply_scene(character_id, scene_id)
            self._window._apply_resolved_background(self._window._library.get_background_path(character_id))
            return self._ok(result)
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

    @pyqtSlot(str, result=str)
    def getCustomization(self, character_id: str) -> str:
        try:
            return self._ok(self._service.get_customization(character_id))
        except Exception as exc:  # noqa: BLE001
            return self._error(exc)

    @pyqtSlot(str, str, result=str)
    def savePersona(self, character_id: str, persona: str) -> str:
        try:
            result = self._service.save_persona(character_id, persona or None)
            if self._adapter is not None:
                # 熱重載 active engine 的 profile,讓 persona 修改立即生效,
                # 不需要切換角色才套用(save_persona 只落盤,不會自己通知 engine)。
                self._adapter.refresh_runtime()
            return self._ok(result)
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
