"""
ECHOES — PyQt5 透明無邊框桌面視窗
使用 QWebEngineView 載入 HTML/JS WebM 播放器，實現去背精靈渲染。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import sys
import time
from uuid import uuid4

from PyQt5.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWidgets import (
    QAction, QApplication, QLineEdit, QMainWindow, QMenu, QSystemTrayIcon, QWidget,
)
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineSettings, QWebEngineView

from character_library import ASSETS_WEBM_DIR, CharacterLibrary, MOTION_MAP
from interaction_trace import InteractionLatencyTracker

from pet_harness.voice_runtime_status_adapter import VoiceRuntimeStatusAdapter
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from pet_harness.character.router import ActiveCharacterSnapshot
from pet_harness.character.profile import CharacterProfile
from ui.background_resolver import BackgroundResolver
from ui.character_ui_bridge import CharacterUiBridge
from ui.js_gateway import JsGateway


BRIDGE_CONTRACT = {
    "python_to_js": [
        "appendConversationAssistant",
        "beginConversationTurn",
        "changeVideo",
        "clearConversationTurns",
        "clearPanelVideo",
        "clearRoomBackground",
        "finishConversationTurn",
        "hydrateAgenticUI",
        "moveCharacter",
        "playPanelVideo",
        "playRoomAudio",
        "playTemporaryVideo",
        "restoreIdleMotion",
        "setActionStatus",
        "setAgenticBusy",
        "setCharacterObjectPosition",
        "setConversationAssistant",
        "setConversationQueueDepth",
        "setIdleMotionCandidates",
        "setIdleVideo",
        "setPanelVideoMuted",
        "setRoomBackground",
        "setRoomCharacter",
        "setRuntimeMode",
        "startMotionLoop",
        "stopMotionLoop",
        "stopRoomAudio",
    ],
    "js_to_python": [
        "addSkill",
        "addToolConfig",
        "deleteSkill",
        "deleteToolConfig",
        "refreshState",
        "resetRuntime",
        "sendLiveText",
        "sendText",
        "toggleStt",
        "triggerOverlayAction",
        "triggerQuickIntent",
        "toggleSkill",
        "toggleTool",
        "beginWindowDrag",
    ],
    "character_bridge": [
        "listCharacters",
        "listPresets",
        "createFromPreset",
        "pickCharacterImage",
        "createFromUpload",
        "getValidationStatus",
        "switchCharacter",
        "deleteCharacter",
        "getActiveState",
        "triggerSkill",
    ],
}

class EchoesWebPage(QWebEnginePage):
    """自訂 WebPage，將前端 console 訊息轉印至 Python Terminal。"""

    _LEVEL_LABELS = {
        QWebEnginePage.InfoMessageLevel: "INFO",
        QWebEnginePage.WarningMessageLevel: "WARN",
        QWebEnginePage.ErrorMessageLevel: "ERROR",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.panel_ended_callback = None
        self.main_video_ended_callback = None
        self.room_audio_ended_callback = None

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        label = self._LEVEL_LABELS.get(level, "LOG")
        print(f"[JS {label}] {message}  (line {line_number}, {source_id})")
        if message == "[ECHOES:PANEL_ENDED]" and callable(self.panel_ended_callback):
            self.panel_ended_callback()
        if message == "[ECHOES:MAIN_VIDEO_ENDED]" and callable(self.main_video_ended_callback):
            self.main_video_ended_callback()
        if message == "[ECHOES:ROOM_AUDIO_ENDED]" and callable(self.room_audio_ended_callback):
            self.room_audio_ended_callback()


class DeveloperInputLineEdit(QLineEdit):
    """Dev Mode 專用輸入框；失焦時自動交還點擊穿透。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._focus_lost_callback = None

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if callable(self._focus_lost_callback):
            QTimer.singleShot(0, self._focus_lost_callback)


class HarnessUiBridge(QObject):
    def __init__(self, window: "TransparentWindow") -> None:
        super().__init__(window)
        self._window = window

    @pyqtSlot()
    def refreshState(self) -> None:
        self._window.refresh_agentic_ui()

    @pyqtSlot()
    def resetRuntime(self) -> None:
        self._window.request_runtime_reset()

    @pyqtSlot(str)
    def sendText(self, text: str) -> None:
        # 文字提交只傳 text;Provider 設定經受控 global runtime API,不由訊息夾帶。
        self._window.submit_agentic_text(text)

    @pyqtSlot(str)
    def sendLiveText(self, text: str) -> None:
        self._window.submit_live_text(text)

    @pyqtSlot(str, bool)
    def toggleSkill(self, skill_id: str, enabled: bool) -> None:
        self._window.toggle_skill(skill_id, enabled)

    @pyqtSlot(str, bool)
    def toggleTool(self, tool_name: str, enabled: bool) -> None:
        self._window.toggle_tool(tool_name, enabled)

    @pyqtSlot()
    def toggleStt(self) -> None:
        self._window.toggle_stt_from_bridge()

    @pyqtSlot(str)
    def triggerOverlayAction(self, action_name: str) -> None:
        self._window.trigger_overlay_action_from_bridge(action_name)

    @pyqtSlot(str)
    def triggerQuickIntent(self, intent_name: str) -> None:
        self._window.trigger_quick_intent_from_bridge(intent_name)

    @pyqtSlot(bool)
    @pyqtSlot()
    def beginWindowDrag(self) -> None:
        self._window.begin_window_drag()

    @pyqtSlot(str)
    def addSkill(self, payload_json: str) -> None:
        self._window.add_skill(payload_json)

    @pyqtSlot(str)
    def deleteSkill(self, skill_id: str) -> None:
        self._window.delete_skill(skill_id)

    @pyqtSlot(str)
    def addToolConfig(self, payload_json: str) -> None:
        self._window.add_tool_config(payload_json)

    @pyqtSlot(str)
    def deleteToolConfig(self, tool_name: str) -> None:
        self._window.delete_tool_config(tool_name)


class TransparentWindow(QMainWindow):
    """透明無邊框桌面寵物視窗"""
    developer_query_submitted = pyqtSignal(str)
    stt_start_requested = pyqtSignal()
    stt_stop_requested = pyqtSignal()
    RAW_JAVASCRIPT_MARKER = "__raw_javascript__"

    DEV_INPUT_WIDTH = 560
    DEV_INPUT_HEIGHT = 44
    DEV_INPUT_MARGIN_BOTTOM = 28
    # 角色預設位移（相對於視窗中心的像素偏移量）
    DEFAULT_CHARACTER_X_OFFSET = 0
    DEFAULT_CHARACTER_Y_OFFSET = 0
    DEFAULT_CHARACTER_SCALE = 1.0
    DEFAULT_CHARACTER_OBJECT_POSITION = "center bottom"
    DEMO_ANIMATIONS_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "assets",
        "animations",
    )
    DEMO_MOTION_MAPPING = {
        "idle": "Idle.webm",
        "report_news": "report_news.webm",
        "play_music": "play_music.webm",
        "wave_response": "running_forward.webm",
        "laugh": "雀躍大笑.webm",
        "angry": "薄怒嘟嘴.webm",
        "awkward": "尷尬擺手.webm",
        "speechless": "無言微翻白眼.webm",
        "listen": "專心聆聽.webm",
    }

    def __init__(
        self,
        brain_mode: str = "harness",
        latency_tracker: InteractionLatencyTracker | None = None,
        library: CharacterLibrary | None = None,
        adapter: PyQtHarnessAdapter | None = None,
        lifecycle_shutdown=None,
        action_bus=None,
    ):
        super().__init__()
        self._brain_mode = brain_mode
        self._library = library or CharacterLibrary()
        self._latency_tracker = latency_tracker
        self._runtime_contract = {"brain_mode": "harness", "harness_runtime_available": True, "live_runtime_available": False, "openclaw_enabled": False}
        self._background_resolver = BackgroundResolver()
        self._voice_status_adapter = VoiceRuntimeStatusAdapter()
        if adapter is None:
            raise ValueError("TransparentWindow requires an injected PyQtHarnessAdapter")
        if action_bus is None:
            raise ValueError("TransparentWindow requires an injected action bus")
        if lifecycle_shutdown is None:
            raise ValueError("TransparentWindow requires an injected lifecycle shutdown")
        self._adapter = adapter
        self._lifecycle_shutdown = lifecycle_shutdown
        self._action_bus = action_bus
        self._motion_coordinator = None
        self._settings_dialog = None
        self._conversation_pending = False
        self._conversation_character_id: str | None = None
        self._character_x_offset = self.DEFAULT_CHARACTER_X_OFFSET
        self._character_y_offset = self.DEFAULT_CHARACTER_Y_OFFSET
        self._character_scale = self.DEFAULT_CHARACTER_SCALE
        self._character_object_position = self.DEFAULT_CHARACTER_OBJECT_POSITION
        self._background_status = "fallback_placeholder"
        self._background_url: str | None = None
        self._live_runtime_available = self._runtime_contract["live_runtime_available"]
        self._stt_listening = False
        self._stt_available = bool(self._runtime_contract["live_runtime_available"])
        self._stt_state = "idle"
        self._stt_controller = None
        self._latest_agentic_event: dict[str, object] | None = None
        self._playtime_character_id: str | None = None
        self._playtime_started_at: float | None = None
        self._playtime_timer = QTimer(self)
        self._playtime_timer.setInterval(60000)
        self._playtime_timer.timeout.connect(self._flush_playtime_tick)
        self._playtime_timer.start()
        self._init_window()
        self._init_webview()
        self._js_gateway = JsGateway(lambda: self.web_view.page(), self.RAW_JAVASCRIPT_MARKER)
        self._init_developer_input()
        self._init_tray()

    def configure_motion(self, coordinator) -> None:
        """Receive the composition-root-owned coordinator and its JS callbacks."""
        self._motion_coordinator = coordinator
        web_page = self.web_view.page()
        if isinstance(web_page, EchoesWebPage):
            web_page.panel_ended_callback = coordinator._on_panel_video_ended
            web_page.main_video_ended_callback = coordinator._on_main_video_ended
            web_page.room_audio_ended_callback = coordinator._on_room_audio_ended

    # ── 視窗初始化 ──────────────────────────────────────────

    def _init_window(self):
        """設定無邊框、置頂視窗，並填滿主螢幕可用區"""
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # 不在工作列顯示圖示
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        # 視窗尺寸就是 CSS 視口尺寸：寫死解析度時視窗會超出螢幕，使用者只看得到畫布
        # 左上角的裁切（角色與底部導覽整個落在畫面外）。availableGeometry 已扣除工作列。
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.availableGeometry())

    def _init_webview(self):
        """建立 QWebEngineView 並載入本地 HTML 播放器"""
        self.web_view = QWebEngineView(self)
        self.web_view.setStyleSheet("background: transparent;")
        self.web_view.page().setBackgroundColor(Qt.transparent)

        # 停用 Chromium 的任何預設右鍵選單，改由 Qt 視窗層統一處理。
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)

        # 掛上自訂 Page，讓前端 console 訊息可轉印至 Python Terminal。
        self.web_view.setPage(EchoesWebPage(self.web_view))
        self._bridge = HarnessUiBridge(self)
        self._character_bridge = CharacterUiBridge(self._adapter.character_service, self, self._adapter)
        self._channel = QWebChannel(self.web_view.page())
        self._channel.registerObject("harnessBridge", self._bridge)
        self._channel.registerObject("characterBridge", self._character_bridge)
        self.web_view.page().setWebChannel(self._channel)
        self.setCentralWidget(self.web_view)

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)
        settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)

        # 載入本地 index.html
        html_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "web_container", "index.html"
        )
        self.web_view.loadFinished.connect(self._on_webview_loaded)
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))

    def _init_developer_input(self):
        """建立 Dev Mode 底部輸入框，用來手動測試本地大腦與 TTS。"""
        self._developer_input = DeveloperInputLineEdit(self)
        self._developer_input.setObjectName("developer-input")
        self._developer_input.setPlaceholderText("Dev Mode：輸入文字後按 Enter，送進 BrainEngine")
        self._developer_input.setClearButtonEnabled(True)
        self._developer_input.setFixedHeight(self.DEV_INPUT_HEIGHT)
        self._developer_input.setStyleSheet(
            """
            QLineEdit#developer-input {
                background: rgba(10, 12, 18, 180);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 90);
                border-radius: 14px;
                padding: 0 14px;
                selection-background-color: rgba(125, 205, 255, 170);
                font-size: 16px;
            }
            QLineEdit#developer-input:focus {
                border: 1px solid rgba(255, 255, 255, 180);
                background: rgba(18, 24, 32, 210);
            }
            """
        )
        self._developer_input.returnPressed.connect(self._submit_developer_query)
        self.developer_query_submitted.connect(self._on_developer_query_submitted)
        self._developer_input._focus_lost_callback = self._hide_developer_input
        self._developer_input.hide()
        self._developer_input.setEnabled(False)
        self._developer_input.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._developer_input.installEventFilter(self)
        self.web_view.installEventFilter(self)
        self.installEventFilter(self)
        self._update_developer_input_geometry()

    def _get_stt_control_descriptor(self) -> dict[str, object]:
        state = self._stt_state
        if not self._stt_available and state != "loading":
            state = "unavailable"
        if state == "loading":
            return {
                "label": "STT 載入中",
                "statusLabel": "載入中",
                "state": state,
                "enabled": False,
                "background": "rgba(88, 120, 160, 205)",
                "border": "rgba(218, 234, 255, 140)",
            }
        if state == "unavailable":
            return {
                "label": "STT 不可用",
                "statusLabel": "未連線",
                "state": state,
                "enabled": False,
                "background": "rgba(92, 92, 92, 180)",
                "border": "rgba(190, 190, 190, 110)",
            }
        if state == "starting":
            return {
                "label": "STT 啟動中",
                "statusLabel": "啟動中",
                "state": state,
                "enabled": False,
                "background": "rgba(88, 120, 160, 205)",
                "border": "rgba(218, 234, 255, 140)",
            }
        if state == "listening":
            return {
                "label": "停止聆聽",
                "statusLabel": "收音中",
                "state": state,
                "enabled": True,
                "background": "rgba(176, 52, 52, 215)",
                "border": "rgba(255, 214, 214, 160)",
            }
        if state == "stopping":
            return {
                "label": "STT 停止中",
                "statusLabel": "停止中",
                "state": state,
                "enabled": False,
                "background": "rgba(132, 96, 62, 205)",
                "border": "rgba(255, 232, 208, 140)",
            }
        if state == "transcribing":
            return {
                "label": "辨識中…",
                "statusLabel": "辨識中",
                "state": state,
                "enabled": False,
                "background": "rgba(88, 96, 160, 205)",
                "border": "rgba(222, 220, 255, 140)",
            }
        return {
            "label": "開始聆聽",
            "statusLabel": "待命中",
            "state": "idle",
            "enabled": True,
            "background": "rgba(32, 126, 92, 215)",
            "border": "rgba(210, 255, 239, 150)",
        }

    def _build_runtime_controls_state(self) -> dict[str, object]:
        return {
            "stt": self._get_stt_control_descriptor(),
            "reset": {"enabled": True},
        }

    def _sync_runtime_controls_ui(self) -> None:
        self._run_javascript("updateRuntimeControls", self._build_runtime_controls_state())

    def _apply_stt_button_state(self):
        descriptor = self._get_stt_control_descriptor()
        label = str(descriptor["label"])
        enabled = bool(descriptor["enabled"])

        if hasattr(self, "_tray_stt_toggle_action"):
            self._tray_stt_toggle_action.setText(label)
            self._tray_stt_toggle_action.setEnabled(enabled)
        self._sync_runtime_controls_ui()

    def _update_developer_input_geometry(self):
        if not hasattr(self, "_developer_input"):
            return

        available_width = max(320, min(self.DEV_INPUT_WIDTH, self.width() - 48))
        x = max(24, (self.width() - available_width) // 2)
        y = max(
            24,
            self.height() - self.DEV_INPUT_HEIGHT - self.DEV_INPUT_MARGIN_BOTTOM,
        )
        self._developer_input.setGeometry(x, y, available_width, self.DEV_INPUT_HEIGHT)

    def _on_webview_loaded(self, ok: bool):
        if not ok:
            print("[ECHOES] 警告: 房間頁面載入失敗。")
            return
        self._js_gateway.mark_ready()
        self._run_javascript("setRuntimeMode", self._brain_mode)
        QTimer.singleShot(120, self._restore_current_character)
        QTimer.singleShot(160, self.refresh_agentic_ui)

    def configure_runtime_context(
        self,
        *,
        voice_status_adapter: VoiceRuntimeStatusAdapter | None = None,
        live_runtime_available: bool | None = None,
        runtime_contract: dict[str, object] | None = None,
    ) -> None:
        if voice_status_adapter is not None:
            self._voice_status_adapter = voice_status_adapter
        if runtime_contract is not None:
            self._runtime_contract = dict(runtime_contract)
        else:
            self._runtime_contract = {"brain_mode": "harness", "harness_runtime_available": True, "live_runtime_available": False, "openclaw_enabled": False}
        if live_runtime_available is not None:
            self._runtime_contract["live_runtime_available"] = bool(live_runtime_available)
        self._live_runtime_available = bool(self._runtime_contract.get("live_runtime_available"))
        self._adapter.configure_runtime_context(
            brain_mode=self._brain_mode,
            background_resolver=self._background_resolver,
            voice_status_adapter=self._voice_status_adapter,
            runtime_contract=self._runtime_contract,
        )

    # ── 系統匣圖示 ─────────────────────────────────────────

    def _make_tray_icon(self) -> QIcon:
        """以程式碼產生一個簡單的紅色圓形圖示（無需外部圖檔）"""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#c0392b"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        return QIcon(pixmap)

    def _init_tray(self):
        """建立系統匣圖示與右鍵選單"""
        self.tray_icon = QSystemTrayIcon(self._make_tray_icon(), self)
        self.tray_icon.setToolTip("ECHOES 虛擬寵物")
        # 系統匣右鍵選單（持久保存，避免 GC）
        self._tray_menu = self._build_menu()
        self.tray_icon.setContextMenu(self._tray_menu)
        # 左鍵單擊系統匣圖示 → 顯示 / 取回視窗
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        """系統匣圖示左鍵點擊 → 把視窗帶到最前面"""
        if reason == QSystemTrayIcon.Trigger:  # 左鍵單擊
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _show_context_menu(self, global_pos):
        self._tray_menu.exec_(global_pos)

    def _build_menu(self) -> QMenu:
        """建立共用右鍵選單（視窗右鍵 & 系統匣共用）"""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #fff; border: 1px solid #444;"
            " border-radius: 6px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            "QMenu::item:selected { background: #c0392b; }"
            "QMenu::separator { height: 1px; background: #444; margin: 4px 8px; }"
        )

        settings_action = QAction("⚙  角色設定", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        action_menu = menu.addMenu("功能動作")

        report_news_action = QAction("播報新聞", self)
        report_news_action.triggered.connect(lambda: self.dispatch_action("[ACTION:report_news]"))
        action_menu.addAction(report_news_action)

        play_music_action = QAction("播放音樂", self)
        play_music_action.triggered.connect(lambda: self.dispatch_action("[ACTION:play_music]"))
        action_menu.addAction(play_music_action)

        stop_music_action = QAction("停止音樂", self)
        stop_music_action.triggered.connect(self.stop_music)
        action_menu.addAction(stop_music_action)

        reset_action = QAction("重置狀態", self)
        reset_action.triggered.connect(self.reset_runtime_state)
        menu.addAction(reset_action)

        self._tray_stt_toggle_action = QAction("開始收音", self)
        self._tray_stt_toggle_action.triggered.connect(self._handle_stt_button_clicked)
        menu.addAction(self._tray_stt_toggle_action)

        menu.addSeparator()

        quit_action = QAction("✕  離開 ECHOES", self)
        quit_action.triggered.connect(self._request_close_from_tray)
        menu.addAction(quit_action)

        return menu

    def _request_close_from_tray(self) -> None:
        """Let the web UI honor its persisted Close-confirm preference."""
        self._js_gateway.call("requestClose")

    def _open_settings(self):
        """以非阻塞方式開啟角色設定視窗，避免鎖住主角色視窗操作。"""
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return

        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        dlg.apply_character_requested.connect(self.apply_character)
        dlg.preview_motion_requested.connect(self.preview_character_motion)
        dlg.generation_done.connect(self.apply_character)
        dlg.finished.connect(self._on_settings_closed)
        self._settings_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_settings_closed(self):
        self._settings_dialog = None

    def eventFilter(self, watched, event):
        if event.type() == QEvent.KeyPress:
            if watched is self._developer_input and event.key() == Qt.Key_Escape:
                self._hide_developer_input()
                return True
            if watched is not self._developer_input and self._handle_cached_intent_shortcut(event):
                return True
            if (
                watched is not self._developer_input
                and event.key() == Qt.Key_D
                and event.modifiers() == Qt.NoModifier
                and not event.isAutoRepeat()
            ):
                self.toggle_developer_input()
                return True

        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_developer_input_geometry()

    def keyPressEvent(self, event):
        if self._handle_cached_intent_shortcut(event):
            return
        if (
            self.focusWidget() is not self._developer_input
            and event.key() == Qt.Key_D
            and event.modifiers() == Qt.NoModifier
            and not event.isAutoRepeat()
        ):
            self.toggle_developer_input()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self._show_context_menu(event.globalPos())

    def _restore_current_character(self):
        """啟動時只信任 router 的 active snapshot;無 active 時顯示安全 no-active 狀態,
        不做 miku-first fallback、不讀 QSettings。"""
        snapshot = TransparentWindow._active_snapshot(self)
        if snapshot is not None and self.apply_character(snapshot.character_id):
            return

        self._show_no_active_character_state()

    def _show_no_active_character_state(self):
        self._run_javascript("setIdleMotionCandidates", [])
        self.set_room_character("尚未選擇角色")
        self.set_action_status("尚未選擇角色,請從角色選單選擇。", tone="warn", timeout_ms=0)
        self.apply_character_position()
        self._apply_resolved_background(None)

    def apply_character(self, character_id: str) -> bool:
        """套用指定角色並切回 idle。character_id 一律來自 router snapshot。"""
        snapshot = TransparentWindow._active_snapshot(self)
        if snapshot is None or snapshot.character_id != character_id:
            switcher = getattr(self._adapter, "switch_character", None)
            if callable(switcher):
                switcher(character_id)
        character_name = self._library.get_character_name(character_id) or character_id
        idle_path = self._library.get_motion_path(character_id, "idle")
        if not idle_path:
            print(f"[ECHOES] 警告: 角色 {character_id} 尚未生成 idle 動畫。")
            return False

        self.restore_idle_video()
        self.apply_character_layout(character_id)
        self._sync_playtime_session_from_active_character()
        self.set_room_character(character_name)
        self.set_action_status(f"{character_name} 已待命", tone="idle", timeout_ms=2200)
        self._apply_resolved_background(self._library.get_background_path(character_id))

        return True

    def _apply_resolved_background(self, configured_path: str | None) -> None:
        status, safe_url = self._background_resolver.resolve(configured_path=configured_path)
        self._background_status = status
        self._background_url = safe_url
        if safe_url:
            self._run_javascript("setRoomBackground", safe_url)
            return
        self._run_javascript("clearRoomBackground")

    def preview_character_motion(self, character_id: str, motion_key: str):
        """播放指定角色動作，單次動作播完後回到 idle。"""
        motion_path = self._library.get_motion_path(character_id, motion_key)
        if not motion_path:
            print(f"[ECHOES] 警告: 找不到角色 {character_id} 的動作 {motion_key}。")
            return

        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        self.change_video(motion_path, loop=should_loop)

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool | None = None) -> bool:
        should_loop = (
            not MOTION_MAP.get(motion_key, {}).get("play_once", True)
            if loop is None
            else bool(loop)
        )
        print(f"[ECHOES] 播放已解析動作 `{motion_key}`: {motion_path}")
        return self.change_video(motion_path, loop=should_loop)

    def play_action_motion(self, motion_key: str) -> bool:
        """只用 router snapshot 的角色解析動作;缺動作時回到同角色 idle,
        絕不 fallback 到另一個角色的動作。"""
        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        current_character_id = self.get_current_character_id()
        if current_character_id:
            motion_path = self._library.get_action_motion_path(current_character_id, motion_key)
            if not motion_path:
                motion_path = self._library.get_motion_path(current_character_id, motion_key)
            if motion_path:
                print(f"[ECHOES] 播放角色動作 `{motion_key}`: {motion_path}")
                return self.change_video(motion_path, loop=should_loop)
            print(f"[ECHOES] 警告: 角色 {current_character_id} 缺少動作 {motion_key},維持同角色 idle。")
            self.restore_idle_video()
            return False

        demo_filename = self.DEMO_MOTION_MAPPING.get(motion_key)
        if demo_filename:
            demo_path = os.path.join(self.DEMO_ANIMATIONS_DIR, demo_filename)
            if os.path.isfile(demo_path):
                print(f"[ECHOES] 播放示範動作 `{motion_key}`: {demo_path}")
                return self.change_video(demo_path, loop=should_loop)

        print(f"[ECHOES] 警告: 找不到可播放的 action 動作 {motion_key}。")
        return False

    def _set_idle_motion_candidates(self, character_id: str | None) -> list[dict[str, object]]:
        if not character_id:
            self._run_javascript("setIdleMotionCandidates", [])
            return []

        raw_candidates = self._library.get_idle_motion_candidates(character_id)
        payload: list[dict[str, object]] = []
        for candidate in raw_candidates:
            absolute_path = self._resolve_media_path(candidate.get("path"))
            if not absolute_path or not os.path.isfile(absolute_path):
                continue
            payload.append(
                {
                    "source": self._build_media_source_url(absolute_path),
                    "weight": max(1, int(candidate.get("weight") or 1)),
                }
            )

        self._run_javascript("setIdleMotionCandidates", payload)
        return payload

    def restore_idle_video(self) -> bool:
        current_character_id = self.get_current_character_id()
        if current_character_id:
            idle_candidates = self._set_idle_motion_candidates(current_character_id)
            if idle_candidates:
                self._run_javascript("restoreIdleMotion")
                return True

        self._run_javascript("setIdleMotionCandidates", [])

        demo_idle_path = os.path.join(
            self.DEMO_ANIMATIONS_DIR,
            self.DEMO_MOTION_MAPPING["idle"],
        )
        if os.path.isfile(demo_idle_path):
            return self.change_video(demo_idle_path, loop=True)

        fallback_idle = os.path.join(ASSETS_WEBM_DIR, "idle.webm")
        if os.path.isfile(fallback_idle):
            return self.change_video(fallback_idle, loop=True)
        return False

    @property
    def is_busy(self) -> bool:
        return (
            self._stt_listening
            or (self._motion_coordinator is not None and (
                self._motion_coordinator.is_tts_busy or self._motion_coordinator.has_active_motion
            ))
        )

    def dispatch_action(
        self,
        directive: str,
        trace_id: str | None = None,
        allow_tts: bool = True,
        wait_for_tts_start: bool = False,
    ) -> bool:
        from pet_harness.app.commands import action_command_from_directive
        if self._action_bus is None:
            raise RuntimeError("TransparentWindow requires an action bus")
        result = self._action_bus.execute(action_command_from_directive(
            directive, trace_id=trace_id, source="ui", allow_tts=allow_tts,
            wait_for_tts_start=wait_for_tts_start,
        ))
        return result.status == "ok"

    def trigger_cached_intent(self, intent_name: str, trigger_source: str) -> bool:
        from pet_harness.app.commands import ActionCommand
        if self._action_bus is None:
            raise RuntimeError("TransparentWindow requires an action bus")
        result = self._action_bus.execute(ActionCommand(f"cached_{intent_name}", source=trigger_source))
        return result.status == "ok"

    def speak_text(self, message: str, trace_id: str | None = None, has_action: bool = False):
        from pet_harness.app.commands import ActionCommand
        if self._action_bus is None:
            raise RuntimeError("TransparentWindow requires an action bus")
        self._action_bus.execute(ActionCommand("speak", message, trace_id, "ui", metadata={"has_action": has_action}))

    def begin_conversation_turn(self, trace_id: str, source_label: str, user_text: str):
        self._run_javascript("beginConversationTurn", trace_id, source_label, user_text)

    def append_conversation_assistant(self, trace_id: str, fragment: str):
        self._run_javascript("appendConversationAssistant", trace_id, fragment)

    def set_conversation_assistant(self, trace_id: str, message: str):
        self._run_javascript("setConversationAssistant", trace_id, message)

    def finish_conversation_turn(self, trace_id: str):
        self._run_javascript("finishConversationTurn", trace_id)

    def set_conversation_queue_depth(self, queue_depth: int):
        self._run_javascript("setConversationQueueDepth", int(queue_depth))

    def clear_conversation_turns(self):
        self._run_javascript("clearConversationTurns")

    def show_synthetic_conversation_turn(self, source_label: str, user_text: str, assistant_text: str):
        trace_id = f"synthetic-{uuid4().hex}"
        self.begin_conversation_turn(trace_id, source_label, user_text)
        self.set_conversation_assistant(trace_id, assistant_text)
        self.finish_conversation_turn(trace_id)

    def set_stt_listening(self, active: bool):
        self.set_stt_state("listening" if active else "idle")

    def set_stt_state(self, state: str):
        normalized = str(state or "idle").strip().lower()
        if normalized not in {"idle", "starting", "listening", "stopping", "transcribing", "loading", "unavailable"}:
            normalized = "idle"
        self._stt_state = normalized
        self._stt_listening = normalized == "listening"
        # loading 也不可按，但要與「不可用」分開顯示：前者是等待，後者是壞掉。
        self._stt_available = normalized not in {"unavailable", "loading"}
        self._apply_stt_button_state()

    def set_stt_controller(self, controller) -> None:
        """Receive the composition-root-created STT controller for UI state updates."""
        self._stt_controller = controller

    def set_stt_available(self, available: bool):
        self._stt_available = bool(available)
        if not self._stt_available:
            self._stt_state = "unavailable"
            self._stt_listening = False
        elif self._stt_state in {"unavailable", "loading"}:
            self._stt_state = "idle"
        self._apply_stt_button_state()

    def _handle_stt_button_clicked(self):
        if not self._stt_available:
            self.set_action_status("語音輸入尚未就緒。", tone="warn", timeout_ms=3200)
            return
        if self._stt_state in {"starting", "stopping", "transcribing"}:
            return
        if self._stt_state == "listening":
            self.stt_stop_requested.emit()
            return
        self.stt_start_requested.emit()

    def toggle_stt_from_bridge(self) -> None:
        self._handle_stt_button_clicked()

    def request_runtime_reset(self) -> None:
        self.reset_runtime_state()

    def trigger_quick_intent_from_bridge(self, intent_name: str) -> None:
        normalized = str(intent_name or "").strip().lower()
        if normalized not in {"joke", "share"}:
            print(f"[ECHOES] Ignored unknown quick intent from web bridge: {intent_name}")
            return
        self.trigger_cached_intent(normalized, f"{normalized} 面板觸發")

    def trigger_overlay_action_from_bridge(self, action_name: str) -> None:
        normalized = str(action_name or "").strip().lower()
        alias_map = {
            "music": "play_music",
            "news": "report_news",
        }
        resolved = alias_map.get(normalized, normalized)
        # 技能定義的 behavior 欄位一律是 music_idle/news_idle（見 .agentic/skills/*.md）；
        # play_music/report_news 是 action_dispatcher 的一次性動作播放鍵，命名空間不同。
        if resolved == "play_music":
            self.trigger_enabled_skill_for_behavior("music_idle")
            return
        if resolved == "report_news":
            self.trigger_enabled_skill_for_behavior("news_idle")
            return
        if resolved == "quit":
            QApplication.quit()
            return
        print(f"[ECHOES] Ignored unknown overlay action from web bridge: {action_name}")

    def trigger_enabled_skill_for_behavior(self, behavior: str) -> bool:
        """技能快捷入口一律經角色授權與 enabled overlay 後走 Harness。"""
        target = str(behavior or "").strip()
        skill = next(
            (
                item for item in self._adapter.list_skills()
                if item.get("enabled") and item.get("default_behavior") == target
            ),
            None,
        )
        if skill is None:
            self.set_action_status("此角色未啟用對應技能。", tone="warn", timeout_ms=2800)
            return False
        try:
            result = self._adapter.character_service.trigger_skill(str(skill["skill_id"]))
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="warn", timeout_ms=3200)
            return False
        self.consume_interaction_result(result, message="Skill executed.")
        return True

    def begin_window_drag(self) -> None:
        """視窗拖曳的唯一入口:整個視窗都是 client area、點擊一律交給 QWebEngineView,
        所以拖曳由前端的 `.window-drag-handle` 明確呼叫這裡。"""
        window_handle = self.windowHandle()
        if window_handle is not None:
            window_handle.startSystemMove()

    def _flush_playtime_tick(self) -> None:
        self._flush_playtime(force=False)

    def _sync_playtime_session_from_active_character(self) -> None:
        profile = TransparentWindow._active_character(self)
        if profile is None:
            self._stop_playtime_session()
            return

        character_id = str(profile.character_id)
        if character_id == self._playtime_character_id and self._playtime_started_at is not None:
            return

        self._flush_playtime(force=True)
        self._playtime_character_id = character_id
        self._playtime_started_at = time.monotonic()

    def _stop_playtime_session(self) -> None:
        self._flush_playtime(force=True)
        self._playtime_character_id = None
        self._playtime_started_at = None

    def _flush_playtime(self, force: bool) -> None:
        if not self._playtime_character_id or self._playtime_started_at is None:
            return

        elapsed_seconds = max(0, int(time.monotonic() - self._playtime_started_at))
        if elapsed_seconds <= 0 and not force:
            return

        self._adapter.character_service.add_playtime(self._playtime_character_id, elapsed_seconds)
        self._playtime_started_at = time.monotonic()

    def on_character_switched(self, profile_payload: dict) -> None:
        """建立/切換角色成功後的回呼：套用 WebM 動作來源並重整 Agentic UI（Skills 清單）。"""
        character_id = str(profile_payload.get("character_id") or "").strip()
        if character_id:
            self.apply_character(character_id)
        self.refresh_agentic_ui(message="Character switched.", tone="idle", timeoutMs=2200)

    def closeEvent(self, event) -> None:
        self._stop_playtime_session()
        self._lifecycle_shutdown()
        super().closeEvent(event)

    def _emit_cached_intent_request(self, intent_name: str, trigger_source: str):
        normalized = str(intent_name or "").strip().lower()
        self.trigger_cached_intent(normalized, trigger_source)

    def _handle_cached_intent_shortcut(self, event) -> bool:
        if (
            self.focusWidget() is self._developer_input
            or event.modifiers() != Qt.NoModifier
            or event.isAutoRepeat()
        ):
            return False
        if event.key() == Qt.Key_1:
            self._emit_cached_intent_request("joke", "Joke 按鈕觸發")
            return True
        if event.key() == Qt.Key_2:
            self._emit_cached_intent_request("share", "share 按鈕觸發")
            return True
        if event.key() == Qt.Key_3:
            return self.trigger_enabled_skill_for_behavior("play_music")
        if event.key() == Qt.Key_4:
            return self.trigger_enabled_skill_for_behavior("report_news")
        return False

    def toggle_developer_input(self):
        if self._developer_input.isVisible():
            self._hide_developer_input()
            return
        self._show_developer_input()

    def _show_developer_input(self):
        self._developer_input.setEnabled(True)
        self._developer_input.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._developer_input.show()
        self._developer_input.raise_()
        self._developer_input.setFocus(Qt.ShortcutFocusReason)
        self._developer_input.selectAll()
        self.set_action_status("Dev Mode 已開啟，按 Enter 可直接測試大腦與 TTS", tone="working", timeout_ms=2200)

    def _hide_developer_input(self):
        if not self._developer_input.isVisible():
            self._developer_input.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._developer_input.setEnabled(False)
            return

        self._developer_input.clearFocus()
        self._developer_input.hide()
        self._developer_input.setEnabled(False)
        self._developer_input.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _submit_developer_query(self):
        query = self._developer_input.text().strip()
        if not query:
            self.set_action_status("Dev Mode 輸入為空，未送出。", tone="warn", timeout_ms=2200)
            return

        self.developer_query_submitted.emit(query)
        self._developer_input.clear()
        self._hide_developer_input()

    def submit_live_text(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            self.set_action_status("Please enter text first.", tone="warn", timeout_ms=2200)
            return
        if not self._live_runtime_available:
            self.set_action_status("Live Conversation is unavailable in harness mode.", tone="warn", timeout_ms=3200)
            return
        self.developer_query_submitted.emit(cleaned)
        self.set_action_status("Live Conversation queued.", tone="working", timeout_ms=0)

    def _on_developer_query_submitted(self, text: str) -> None:
        self.submit_agentic_text(text)

    def submit_agentic_text(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            self.set_action_status("Please enter text first.", tone="warn", timeout_ms=2200)
            return
        if self._conversation_pending:
            self.set_action_status("Interaction already running.", tone="warn", timeout_ms=2200)
            return
        from pet_harness.app.commands import ActionCommand
        character_id = self.get_current_character_id()
        if not character_id:
            self.set_action_status("No active character.", tone="warn", timeout_ms=2200)
            return
        self._conversation_pending = True
        self._conversation_character_id = character_id
        self._set_agentic_busy(True)
        self.set_action_status("Processing interaction...", tone="working", timeout_ms=0)
        result = self._action_bus.execute(ActionCommand("conversation", cleaned, source="ui", character_id=character_id))
        if result.status != "ok":
            self._conversation_pending = False
            self._conversation_character_id = None
            self._set_agentic_busy(False)
            self.set_action_status(result.reason or "Interaction rejected.", tone="warn", timeout_ms=2200)

    def _on_action_bus_conversation(self, payload: dict) -> None:
        if not self._is_current_conversation_character(payload.get("character_id")):
            self._finish_conversation_for(payload.get("character_id"))
            return
        self.consume_interaction_result(payload, message="Interaction complete.")
        self._finish_conversation_for(payload.get("character_id"))

    def _on_action_bus_error(self, message: str, character_id: str | None = None) -> None:
        """對話經 ActionBus 失敗時復位 busy 狀態，否則 UI 會永遠停在 Processing。"""
        if not self._is_current_conversation_character(character_id):
            self._finish_conversation_for(character_id)
            return
        self._finish_conversation_for(character_id)
        self._on_agentic_error(message)

    def _is_current_conversation_character(self, character_id: str | None) -> bool:
        return bool(character_id) and character_id == self.get_current_character_id()

    def _finish_conversation_for(self, character_id: str | None) -> None:
        if character_id and character_id != self._conversation_character_id:
            return
        self._conversation_pending = False
        self._conversation_character_id = None
        self._set_agentic_busy(False)

    def consume_interaction_result(self, payload: dict, message: str = "Interaction complete.") -> None:
        """所有 Harness 結果（文字互動與立即執行）的唯一 Host 消費流程。"""
        self._latest_agentic_event = dict(payload or {})
        webm_key = self._validated_event_motion_key(payload)
        reply_text = str(payload.get("reply") or "").strip()
        trace_id = f"agentic-{uuid4().hex}"
        source_label = "Skill" if payload.get("matched_skill") else "Talk"
        user_text = str(payload.get("user_text") or "").strip() or (
            f"立即執行：{payload.get('matched_skill')}"
            if payload.get("matched_skill")
            else "你的訊息"
        )
        self.begin_conversation_turn(trace_id, source_label, user_text)
        self.set_conversation_assistant(trace_id, reply_text)
        self.finish_conversation_turn(trace_id)
        if webm_key:
            self.dispatch_action(
                f"[ACTION:{webm_key}] {reply_text}",
                trace_id=trace_id,
                allow_tts=bool(reply_text),
                wait_for_tts_start=bool(reply_text),
            )
        elif reply_text:
            self.speak_text(reply_text, trace_id=trace_id, has_action=False)
        self.refresh_agentic_ui(
            event_payload=payload,
            message=message,
            tone="idle",
            timeoutMs=2400,
        )

    def _validated_event_motion_key(self, payload: dict) -> str:
        """在播放前以 active snapshot 再驗證 action tag，失敗時安全回同角色 idle。"""
        action_tag = str(payload.get("action_tag") or "").strip()
        if not action_tag:
            return str(payload.get("webm_key") or "").strip()
        character_id = self.get_current_character_id()
        resolved = self._library.resolve_action_tag(character_id, action_tag)
        if resolved is None:
            print(f"[ECHOES] 警告: 拒絕無效 action tag `{action_tag}`，回復 idle。")
            self.restore_idle_video()
            return ""
        return resolved["motion_key"]

    def _on_agentic_error(self, message: str) -> None:
        self._set_agentic_busy(False)
        self.refresh_agentic_ui(message=message, tone="error", timeoutMs=4800)

    def toggle_skill(self, skill_id: str, enabled: bool) -> None:
        try:
            result = self._adapter.set_skill_enabled(skill_id, enabled)
            status = "enabled" if result.get("enabled") else "disabled"
            self.refresh_agentic_ui(message=f"Skill {skill_id} {status}.", tone="idle", timeoutMs=2200)
        except Exception as exc:  # noqa: BLE001
            self.refresh_agentic_ui(message=str(exc), tone="warn", timeoutMs=4200)

    def toggle_tool(self, tool_name: str, enabled: bool) -> None:
        try:
            result = self._adapter.set_tool_enabled(tool_name, enabled)
            status = "enabled" if result.get("enabled") else "disabled"
            self.refresh_agentic_ui(message=f"Tool {tool_name} {status}.", tone="idle", timeoutMs=2200)
        except Exception as exc:  # noqa: BLE001
            self.refresh_agentic_ui(message=str(exc), tone="warn", timeoutMs=4200)

    def add_skill(self, payload_json: str) -> None:
        try:
            self._adapter.add_skill(json.loads(payload_json))
            self.refresh_agentic_ui(message="Skill added.", tone="idle", timeoutMs=2200)
        except Exception as exc:  # noqa: BLE001
            self.refresh_agentic_ui(message=str(exc), tone="warn", timeoutMs=4200)

    def delete_skill(self, skill_id: str) -> None:
        try:
            result = self._adapter.delete_skill(skill_id)
            message = "Skill disabled." if result.get("disabled") else "Skill deleted."
            self.refresh_agentic_ui(message=message, tone="idle", timeoutMs=2200)
        except Exception as exc:  # noqa: BLE001
            self.refresh_agentic_ui(message=str(exc), tone="warn", timeoutMs=4200)

    def add_tool_config(self, payload_json: str) -> None:
        try:
            self._adapter.add_tool_config(json.loads(payload_json))
            self.refresh_agentic_ui(message="Tool config added.", tone="idle", timeoutMs=2200)
        except Exception as exc:  # noqa: BLE001
            self.refresh_agentic_ui(message=str(exc), tone="warn", timeoutMs=4200)

    def delete_tool_config(self, tool_name: str) -> None:
        try:
            result = self._adapter.delete_tool_config(tool_name)
            message = "Tool disabled." if result.get("disabled") else "Tool config deleted."
            self.refresh_agentic_ui(message=message, tone="idle", timeoutMs=2200)
        except Exception as exc:  # noqa: BLE001
            self.refresh_agentic_ui(message=str(exc), tone="warn", timeoutMs=4200)

    def refresh_agentic_ui(
        self,
        event_payload: dict | None = None,
        message: str | None = None,
        tone: str = "idle",
        timeoutMs: int = 0,
    ) -> None:
        self._adapter.configure_runtime_context(
            brain_mode=self._brain_mode,
            background_resolver=self._background_resolver,
            voice_status_adapter=self._voice_status_adapter,
            runtime_contract=self._runtime_contract,
        )
        state = self._adapter.get_current_state()
        state["background"] = self._build_runtime_background_state(state.get("background"))
        diagnostics = dict(state.get("diagnostics") or {})
        diagnostics["brain_mode"] = self._brain_mode
        diagnostics["background_status"] = self._background_status
        state["diagnostics"] = diagnostics
        xp_state = dict(state.get("xp") or {})
        payload = {
            "state": state,
            "skills": self._adapter.list_skills(),
            "tools": self._adapter.list_tools(),
            "message": message,
            "tone": tone,
            "timeoutMs": timeoutMs,
            "runtimeControls": self._build_runtime_controls_state(),
            "xp_delta": int((event_payload or {}).get("xp_delta", xp_state.get("last_delta", 0)) or 0),
            "progress_percent": int(xp_state.get("progress_percent", 0) or 0),
        }
        latest_event = event_payload or self._latest_agentic_event
        if latest_event:
            payload["event"] = latest_event
        self._run_javascript("hydrateAgenticUI", payload)

    def _build_runtime_background_state(self, base_background) -> dict:
        background = dict(base_background or {})
        background["status"] = self._background_status
        background["source"] = self._background_url or background.get("source") or "css:room-placeholder"
        background["message"] = (
            self._background_resolver.diagnostics().get("reason")
            or background.get("message")
            or "background diagnostics unavailable"
        )
        return background

    def _set_agentic_busy(self, busy: bool) -> None:
        self._run_javascript("setAgenticBusy", bool(busy))

    def get_render_diagnostics(self) -> dict[str, object]:
        return {
            "brain_mode": self._brain_mode,
            "runtime_contract": dict(self._runtime_contract),
            "background_status": self._background_status,
            "background_url": self._background_url,
            "stt_state": self._stt_state,
            "stt_available": self._stt_available,
            "webview_ready": self._js_gateway.ready,
            "latest_agentic_event": self._latest_agentic_event,
        }

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self._run_javascript("setActionStatus", message, tone, timeout_ms)

    def set_room_character(self, name: str):
        self._run_javascript("setRoomCharacter", name)

    def play_panel_video(self, path: str, muted: bool = True, loop: bool = False):
        bg_url = QUrl.fromLocalFile(path).toString()
        self._run_javascript("playPanelVideo", bg_url, bool(loop), muted)

    def set_panel_video_muted(self, muted: bool):
        self._run_javascript("setPanelVideoMuted", bool(muted))

    def clear_panel_video(self):
        self._run_javascript("clearPanelVideo")

    def start_motion_loop(self, path: str, interval_ms: int = 1000):
        url = QUrl.fromLocalFile(path).toString()
        self._run_javascript("startMotionLoop", url, interval_ms)

    def stop_motion_loop(self):
        self._run_javascript("stopMotionLoop")

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.isfile(absolute_path):
            print(f"[ECHOES] 警告: 音訊不存在，略過播放: {filename}")
            return False

        source_url = QUrl.fromLocalFile(absolute_path).toString(QUrl.FullyEncoded)
        self._run_javascript("playRoomAudio", source_url, title, update_status)
        return True

    def stop_music(self):
        self._run_javascript("stopRoomAudio")

    def reset_runtime_state(self):
        from pet_harness.app.commands import ActionCommand
        self._action_bus.execute(ActionCommand("reset", source="ui"))

    def reset_presentation(self):
        self.set_conversation_queue_depth(0)
        self._hide_developer_input()
        self.stop_music()
        self.stop_motion_loop()
        self.clear_panel_video()
        self.clear_conversation_turns()
        self._run_javascript("resetRoomState")
        self.restore_idle_video()
        self.set_action_status("已重置，等待下一次互動。", tone="idle", timeout_ms=2400)

    def get_current_character_id(self) -> str | None:
        """UI 動作/idle/聲線一律以 router snapshot 為唯一 active character 來源。"""
        snapshot = TransparentWindow._active_snapshot(self)
        return snapshot.character_id if snapshot else None

    def _active_snapshot(self):
        snapshot = self._adapter.get_active_snapshot()
        if snapshot is None or isinstance(snapshot, ActiveCharacterSnapshot):
            return snapshot
        # Compatibility for legacy test doubles that expose only the nested router.
        return getattr(self._adapter, "router").get_active_snapshot()

    def _active_character(self):
        character = self._adapter.get_active_character()
        if character is None or isinstance(character, CharacterProfile):
            return character
        return getattr(self._adapter, "router").get_active_character()

    def apply_character_position(self):
        """套用目前由 Python 管理的角色位移設定。"""
        self.apply_character_transform(
            self._character_x_offset,
            self._character_y_offset,
            self._character_scale,
            self._character_object_position,
        )

    def set_character_position(self, x_offset: int, y_offset: int):
        """更新角色位移設定並立即套用。"""
        self._character_x_offset = x_offset
        self._character_y_offset = y_offset
        self.apply_character_position()

    def apply_character_layout(self, character_id: str | None = None):
        layout = self._library.get_layout_config(character_id)
        self._character_x_offset = self._coerce_int(
            layout.get("character_x_offset"),
            self.DEFAULT_CHARACTER_X_OFFSET,
        )
        self._character_y_offset = self._coerce_int(
            layout.get("character_y_offset"),
            self.DEFAULT_CHARACTER_Y_OFFSET,
        )
        self._character_scale = self._coerce_float(
            layout.get("character_scale"),
            self.DEFAULT_CHARACTER_SCALE,
            minimum=0.1,
            maximum=4.0,
        )
        self._character_object_position = self._coerce_object_position(
            layout.get("object_position"),
            self.DEFAULT_CHARACTER_OBJECT_POSITION,
        )
        self.apply_character_position()

    def move_character_to(self, x_offset: int, y_offset: int):
        """以左為正 x、以下為正 y 的像素偏移量移動角色。"""
        self.apply_character_transform(
            x_offset,
            y_offset,
            self._character_scale,
            self._character_object_position,
        )

    def apply_character_transform(
        self,
        x_offset: int,
        y_offset: int,
        scale: float,
        object_position: str,
    ):
        self._run_javascript("moveCharacter", x_offset, y_offset, scale)
        self._run_javascript("setCharacterObjectPosition", object_position)

    def nativeEvent(self, event_type, message):
        """整個視窗都是 client area。可點區域一律由前端 UI 決定,Qt 端不再用矩形白名單猜:
        回 HTCAPTION 會讓 Windows 改送 WM_NCLBUTTONDOWN(視窗管理員接管拖曳),
        QWebEngineView 就永遠收不到那個點擊。視窗拖曳走前端的 beginWindowDrag()。"""
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)

        wm_nchittest = 0x0084
        try:
            if ctypes.wintypes.MSG.from_address(int(message)).message == wm_nchittest:
                return True, 1  # HTCLIENT
        except Exception:  # noqa: BLE001
            pass
        return super().nativeEvent(event_type, message)

    @staticmethod
    def _coerce_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _coerce_object_position(value, default: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return default
        return normalized

    # ── Python → JS 橋接 ───────────────────────────────────

    def change_video(self, filename, loop=True) -> bool:
        """
        呼叫前端 JS 的 changeVideo() 切換影片。
        :param filename: 絕對路徑、相對路徑或舊版純檔名
        """
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.exists(absolute_path):
            print(f"[ECHOES ERROR] WebM 檔案不存在: {absolute_path or filename}")
            return False

        source_url = self._build_media_source_url(absolute_path)
        print(f"[ECHOES] 送出影片 URL: {source_url}")
        if loop:
            self._run_javascript("setIdleVideo", source_url)
            return True

        safe_url = self._escape_javascript_single_quoted_string(source_url)
        self._run_raw_javascript(
            "if (window.playTemporaryVideo) { "
            f"window.playTemporaryVideo('{safe_url}');"
            " } else { console.error('[ECHOES] playTemporaryVideo bridge 不存在'); }"
        )
        return True

    def _run_raw_javascript(self, script: str):
        self._js_gateway.raw(script)

    def _run_javascript(self, function_name: str, *args):
        self._js_gateway.call(function_name, *args)

    def _flush_pending_javascript_calls(self):
        self._js_gateway.mark_ready()

    @staticmethod
    def _build_javascript_bridge_call(function_name: str, *args) -> str:
        return JsGateway.build_call(function_name, *args)

    @staticmethod
    def _build_media_source_url(absolute_path: str) -> str:
        source_url = QUrl.fromLocalFile(absolute_path).toString(QUrl.FullyEncoded)
        return f"{source_url}?v={int(os.path.getmtime(absolute_path))}"

    def _resolve_media_path(self, filename: str) -> str | None:
        if not filename:
            return None

        if os.path.isabs(filename):
            return self._normalize_absolute_path(filename)

        root_relative = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            filename,
        )
        if os.path.isfile(root_relative):
            return self._normalize_absolute_path(root_relative)

        demo_relative = os.path.join(
            self.DEMO_ANIMATIONS_DIR,
            os.path.basename(filename),
        )
        if os.path.isfile(demo_relative):
            return self._normalize_absolute_path(demo_relative)

        legacy_relative = os.path.join(ASSETS_WEBM_DIR, filename)
        return self._normalize_absolute_path(legacy_relative)

    @staticmethod
    def _normalize_absolute_path(path: str) -> str:
        return os.path.abspath(os.path.normpath(path))

    @staticmethod
    def _escape_javascript_single_quoted_string(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )
