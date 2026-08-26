from PyQt5.QtCore import QObject, pyqtSlot


BRIDGE_CONTRACT = {
    "python_to_js": ["appendConversationAssistant", "beginConversationTurn", "changeVideo", "clearConversationTurns", "clearPanelVideo", "clearRoomBackground", "finishConversationTurn", "hydrateAgenticUI", "moveCharacter", "playPanelVideo", "playRoomAudio", "playTemporaryVideo", "restoreIdleMotion", "setActionStatus", "setAgenticBusy", "setCharacterObjectPosition", "setConversationAssistant", "setConversationQueueDepth", "setIdleMotionCandidates", "setIdleVideo", "setMainMenuPreview", "setPanelVideoMuted", "setRoomBackground", "setRoomCharacter", "setRuntimeMode", "startMotionLoop", "stopMotionLoop", "stopRoomAudio"],
    "js_to_python": ["addSkill", "addToolConfig", "deleteSkill", "deleteToolConfig", "refreshState", "resetRuntime", "sendText", "toggleStt", "triggerOverlayAction", "triggerQuickIntent", "toggleSkill", "toggleTool", "beginWindowDrag"],
    "character_bridge": ["listCharacters", "listPresets", "createFromPreset", "pickCharacterImage", "createFromUpload", "getValidationStatus", "switchCharacter", "deleteCharacter", "getActiveState", "triggerSkill"],
}


class HarnessUiBridge(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window

    @pyqtSlot()
    def refreshState(self) -> None:
        if getattr(self._window, "_adapter", None) is not None:
            self._window.refresh_agentic_ui()

    @pyqtSlot()
    def resetRuntime(self) -> None: self._window.request_runtime_reset()

    @pyqtSlot()
    def saveProgress(self) -> None: self._window._save_progress_from_tray()

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
