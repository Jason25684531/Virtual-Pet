import json
import math

from PyQt5.QtCore import QObject, QRect, pyqtSlot

from ui.interaction_region_manager import InteractionRegionManager


BRIDGE_CONTRACT = {
    "python_to_js": ["appendConversationAssistant", "beginConversationTurn", "changeVideo", "clearConversationTurns", "clearPanelVideo", "clearRoomBackground", "finishConversationTurn", "hydrateAgenticUI", "moveCharacter", "playPanelVideo", "playRoomAudio", "playTemporaryVideo", "restoreIdleMotion", "setActionStatus", "setAgenticBusy", "setCharacterObjectPosition", "setConversationAssistant", "setConversationQueueDepth", "setIdleMotionCandidates", "setIdleVideo", "setMainMenuPreview", "setPanelVideoMuted", "setRoomBackground", "setRoomCharacter", "setRuntimeMode", "startMotionLoop", "stopMotionLoop", "stopRoomAudio"],
    "js_to_python": ["addSkill", "addToolConfig", "deleteSkill", "deleteToolConfig", "refreshState", "resetRuntime", "sendText", "toggleStt", "triggerOverlayAction", "triggerQuickIntent", "toggleSkill", "toggleTool", "beginWindowDrag", "update_hit_regions"],
    "character_bridge": ["listCharacters", "listPresets", "createFromPreset", "pickCharacterImage", "createFromUpload", "getValidationStatus", "switchCharacter", "deleteCharacter", "getActiveState", "triggerSkill"],
}


class HarnessUiBridge(QObject):
    def __init__(self, window, interaction_region_manager: InteractionRegionManager | None = None) -> None:
        super().__init__(window)
        self._window = window
        self._interaction_region_manager = interaction_region_manager or getattr(window, "_interaction_regions", None)
        self._web_regions: list[QRect] = []

    @pyqtSlot()
    def refreshState(self) -> None:
        if getattr(self._window, "_adapter", None) is not None:
            self._window.refresh_agentic_ui()

    @pyqtSlot()
    def resetRuntime(self) -> None: self._window.request_runtime_reset()

    @pyqtSlot()
    def saveProgress(self) -> None: self._window._save_progress_from_tray()

    def sync_developer_input_region(self) -> None:
        self._sync_interaction_regions()

    def _sync_interaction_regions(self) -> None:
        if self._interaction_region_manager is None:
            return
        regions = list(self._web_regions)
        developer_input = getattr(self._window, "_developer_input", None)
        if developer_input is not None and developer_input.isVisible():
            regions.append(developer_input.geometry())
        self._interaction_region_manager.update_regions(regions)
        self._interaction_region_manager.set_fallback_interactive(not self._web_regions)

    @pyqtSlot(str)
    def sendText(self, text: str) -> None: self._window.submit_agentic_text(text)

    @pyqtSlot(str, bool)
    def toggleSkill(self, skill_id: str, enabled: bool) -> None: self._window.toggle_skill(skill_id, enabled)

    @pyqtSlot(str, bool)
    def toggleTool(self, tool_name: str, enabled: bool) -> None: self._window.toggle_tool(tool_name, enabled)

    @pyqtSlot()
    def toggleStt(self) -> None: self._window.toggle_stt_from_bridge()

    @pyqtSlot(str)
    def triggerOverlayAction(self, action_name: str) -> None: self._window.trigger_overlay_action_from_bridge(action_name)

    @pyqtSlot(str)
    def triggerQuickIntent(self, intent_name: str) -> None: self._window.trigger_quick_intent_from_bridge(intent_name)

    @pyqtSlot(bool)
    @pyqtSlot()
    def beginWindowDrag(self) -> None: self._window.begin_window_drag()

    @pyqtSlot(str)
    def addSkill(self, payload_json: str) -> None: self._window.add_skill(payload_json)

    @pyqtSlot(str)
    def deleteSkill(self, skill_id: str) -> None: self._window.delete_skill(skill_id)

    @pyqtSlot(str)
    def addToolConfig(self, payload_json: str) -> None: self._window.add_tool_config(payload_json)

    @pyqtSlot(str)
    def deleteToolConfig(self, tool_name: str) -> None: self._window.delete_tool_config(tool_name)

    @pyqtSlot(str)
    def update_hit_regions(self, payload_json: str) -> None:
        """Accept visible DOM rectangles; malformed reports leave hit-testing fail-open."""
        try:
            payload = json.loads(payload_json)
            if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
                raise ValueError("regions must be a list")
            ratio = float(payload.get("devicePixelRatio", 1))
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError("devicePixelRatio must be positive")
            rects = []
            for region in payload["regions"]:
                if not isinstance(region, dict):
                    continue
                values = [region.get(key) for key in ("x", "y", "width", "height")]
                if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
                    continue
                x, y, width, height = values
                if width > 0 and height > 0:
                    rects.append(QRect(round(x), round(y), round(width), round(height)))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._web_regions = []
            if self._interaction_region_manager is not None:
                self._interaction_region_manager.clear()
                self._interaction_region_manager.set_fallback_interactive(True)
            self._window.set_stage_active(False)
            return

        self._web_regions = rects
        self._window.set_stage_active(payload.get("stageActive"))
        self._sync_interaction_regions()
