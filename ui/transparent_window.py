"""
Transparent PyQt window that hosts the current ECHOES room scene and
the Week 3 agentic validation controls inside the existing WebView.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import sys

from PyQt5.QtCore import QObject, QPoint, Qt, QThread, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView
from PyQt5.QtWidgets import QAction, QApplication, QMainWindow, QMenu, QSystemTrayIcon

from character_library import ASSETS_WEBM_DIR, CharacterLibrary, MOTION_MAP
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter


class HarnessInteractionWorker(QThread):
    finished_payload = pyqtSignal(dict)
    failed_message = pyqtSignal(str)

    def __init__(self, adapter: PyQtHarnessAdapter, text: str, provider: str, parent=None) -> None:
        super().__init__(parent)
        self._adapter = adapter
        self._text = text
        self._provider = provider

    def run(self) -> None:
        try:
            payload = self._adapter.handle_text_input(self._text, provider=self._provider)
        except Exception as exc:  # noqa: BLE001
            self.failed_message.emit(str(exc))
            return
        self.finished_payload.emit(payload)


class HarnessUiBridge(QObject):
    def __init__(self, window: "TransparentWindow") -> None:
        super().__init__(window)
        self._window = window

    @pyqtSlot()
    def refreshState(self) -> None:
        self._window.refresh_agentic_ui()

    @pyqtSlot(str, str)
    def sendText(self, text: str, provider: str) -> None:
        self._window.submit_agentic_text(text, provider)

    @pyqtSlot(str, bool)
    def toggleSkill(self, skill_id: str, enabled: bool) -> None:
        self._window.toggle_skill(skill_id, enabled)

    @pyqtSlot(str, bool)
    def toggleTool(self, tool_name: str, enabled: bool) -> None:
        self._window.toggle_tool(tool_name, enabled)

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
    WINDOW_WIDTH = 1920
    WINDOW_HEIGHT = 1080
    DEFAULT_CHARACTER_X_OFFSET = 960
    DEFAULT_CHARACTER_Y_OFFSET = 540
    DEMO_ANIMATIONS_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "assets",
        "animations",
    )
    DEMO_MOTION_MAPPING = {
        "idle": "Idle.webm",
        "report_news": "report_news.webm",
        "play_music": "play_music.webm",
        "laugh": "laugh.webm",
        "angry": "angry.webm",
        "awkward": "awkward.webm",
        "speechless": "speechless.webm",
        "listen": "listen.webm",
    }
    BEHAVIOR_PREVIEW_MAP = {
        "idle": "idle",
        "music_idle": "play_music",
        "news_idle": "report_news",
        "break_idle": "listen",
        "gacha_idle": "laugh",
        "monitor_idle": "listen",
    }
    XP_BADGE_TOP = 28
    XP_BADGE_RIGHT = 30
    XP_BADGE_WIDTH = 260
    XP_BADGE_HEIGHT = 72
    AGENTIC_PANEL_TOP = 104
    AGENTIC_PANEL_RIGHT = 30
    AGENTIC_PANEL_BOTTOM = 106
    AGENTIC_PANEL_MAX_WIDTH = 420
    AGENTIC_PANEL_WIDTH_RATIO = 0.34

    def __init__(self, brain_mode: str = "harness") -> None:
        super().__init__()
        self._brain_mode = brain_mode
        self._library = CharacterLibrary()
        self._adapter = PyQtHarnessAdapter()
        self._settings_dialog = None
        self._interaction_worker: HarnessInteractionWorker | None = None
        self._character_x_offset = self.DEFAULT_CHARACTER_X_OFFSET
        self._character_y_offset = self.DEFAULT_CHARACTER_Y_OFFSET
        self._webview_ready = False
        self._pending_javascript_calls: list[tuple[str, tuple[object, ...]]] = []
        self._current_preview_key = "idle"

        self._init_window()
        self._init_webview()

        from action_dispatcher import ActionDispatcher

        self._action_dispatcher = ActionDispatcher(self, self._library, self)
        self._move_to_bottom_right()
        self._init_tray()

    def _init_window(self) -> None:
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.resize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

    def _init_webview(self) -> None:
        self.web_view = QWebEngineView(self)
        self.web_view.setStyleSheet("background: transparent;")
        self.web_view.page().setBackgroundColor(Qt.transparent)
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)
        self.setCentralWidget(self.web_view)

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)

        self._bridge = HarnessUiBridge(self)
        self._channel = QWebChannel(self.web_view.page())
        self._channel.registerObject("harnessBridge", self._bridge)
        self.web_view.page().setWebChannel(self._channel)

        # JS console → Python stdout（偵錯關鍵：可以看到 JS 錯誤）
        try:
            self.web_view.page().javaScriptConsoleMessage.connect(self._on_js_console)
        except Exception:
            pass

        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_container", "index.html")
        self.web_view.loadFinished.connect(self._on_webview_loaded)
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))

    def _on_js_console(self, level: int, message: str, line: int, source: str) -> None:
        """將 WebView JS console 訊息轉發到 Python stdout（方便偵錯）。"""
        level_tag = {0: "JS", 1: "JS:INFO", 2: "JS:WARN", 3: "JS:ERROR"}.get(level, "JS")
        src = source.split("/")[-1] if source else "?"
        print(f"[ECHOES {level_tag}:{src}:{line}] {message}")

    def _on_webview_loaded(self, ok: bool) -> None:
        if not ok:
            print("[ECHOES] warning: web container failed to load")
            return
        self._webview_ready = True
        self._flush_pending_javascript_calls()
        QTimer.singleShot(120, self._restore_current_character)
        QTimer.singleShot(160, self.refresh_agentic_ui)

    def _move_to_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + max(0, geo.width() - self.WINDOW_WIDTH - 20)
            y = geo.y() + max(0, geo.height() - self.WINDOW_HEIGHT - 20)
            self.move(x, y)

    def _make_tray_icon(self) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#c0392b"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        return QIcon(pixmap)

    def _init_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(self._make_tray_icon(), self)
        self.tray_icon.setToolTip("ECHOES desktop pet")
        self._tray_menu = self._build_menu()
        self.tray_icon.setContextMenu(self._tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #fff; border: 1px solid #444;"
            " border-radius: 6px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            "QMenu::item:selected { background: #c0392b; }"
            "QMenu::separator { height: 1px; background: #444; margin: 4px 8px; }"
        )

        settings_action = QAction("Character settings", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        action_menu = menu.addMenu("Quick actions")

        report_news_action = QAction("Report news", self)
        report_news_action.triggered.connect(lambda: self.dispatch_action("[ACTION:report_news]"))
        action_menu.addAction(report_news_action)

        play_music_action = QAction("Play music", self)
        play_music_action.triggered.connect(lambda: self.dispatch_action("[ACTION:play_music]"))
        action_menu.addAction(play_music_action)

        stop_music_action = QAction("Stop music", self)
        stop_music_action.triggered.connect(self.stop_music)
        action_menu.addAction(stop_music_action)

        menu.addSeparator()

        quit_action = QAction("Quit ECHOES", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        return menu

    def _open_settings(self) -> None:
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return

        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog()
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.apply_character_requested.connect(self.apply_character)
        dialog.preview_motion_requested.connect(self.preview_character_motion)
        dialog.generation_done.connect(self.apply_character)
        dialog.finished.connect(self._on_settings_closed)
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_settings_closed(self) -> None:
        self._settings_dialog = None

    def _restore_current_character(self) -> None:
        current_character_id = self._library.get_current_character_id()
        if current_character_id and self.apply_character(current_character_id):
            return

        if self.restore_idle_video():
            self.set_room_character("Pet Preview")
            self.set_action_status("Character restored.", tone="idle", timeout_ms=2200)
            self.apply_character_position()

    def apply_character(self, character_id: str) -> bool:
        character_name = self._library.get_character_name(character_id) or character_id
        idle_path = self._library.get_motion_path(character_id, "idle")
        if not idle_path:
            print(f"[ECHOES] warning: missing idle motion for {character_id}")
            return False

        self._library.set_current_character_id(character_id)
        self.change_video(idle_path, loop=True)
        self._current_preview_key = "idle"
        self.apply_character_position()
        self.set_room_character(character_name)
        self.set_action_status(f"{character_name} ready.", tone="idle", timeout_ms=2200)
        return True

    def preview_character_motion(self, character_id: str, motion_key: str) -> None:
        motion_path = self._library.get_motion_path(character_id, motion_key)
        if not motion_path:
            print(f"[ECHOES] warning: missing motion {motion_key} for {character_id}")
            return

        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        self.change_video(motion_path, loop=should_loop)
        self._current_preview_key = motion_key

    def play_action_motion(self, motion_key: str) -> bool:
        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        current_character_id = self._library.get_current_character_id()
        if current_character_id:
            motion_path = self._library.get_action_motion_path(current_character_id, motion_key)
            if not motion_path:
                motion_path = self._library.get_motion_path(current_character_id, motion_key)
            if motion_path:
                self.change_video(motion_path, loop=should_loop)
                self._current_preview_key = motion_key
                return True

        demo_filename = self.DEMO_MOTION_MAPPING.get(motion_key)
        if demo_filename:
            demo_path = os.path.join(self.DEMO_ANIMATIONS_DIR, demo_filename)
            if os.path.isfile(demo_path):
                self.change_video(demo_path, loop=should_loop)
                self._current_preview_key = motion_key
                return True

        print(f"[ECHOES] warning: missing demo motion for {motion_key}")
        return False

    def restore_idle_video(self) -> bool:
        current_character_id = self._library.get_current_character_id()
        if current_character_id:
            idle_path = self._library.get_motion_path(current_character_id, "idle")
            if idle_path:
                self.change_video(idle_path, loop=True)
                self._current_preview_key = "idle"
                return True

        demo_idle_path = os.path.join(self.DEMO_ANIMATIONS_DIR, self.DEMO_MOTION_MAPPING["idle"])
        if os.path.isfile(demo_idle_path):
            self.change_video(demo_idle_path, loop=True)
            self._current_preview_key = "idle"
            return True

        fallback_idle = os.path.join(ASSETS_WEBM_DIR, "idle.webm")
        if os.path.isfile(fallback_idle):
            self.change_video(fallback_idle, loop=True)
            self._current_preview_key = "idle"
            return True
        return False

    def apply_behavior_preview(self, behavior_id: str | None, webm_key: str | None) -> dict[str, object]:
        preview_key = self.BEHAVIOR_PREVIEW_MAP.get(behavior_id or "", self.BEHAVIOR_PREVIEW_MAP.get(webm_key or ""))
        if preview_key is None:
            return {
                "applied": False,
                "animation_key": None,
                "warning": "WebM switching not wired; displaying webm_key only",
            }
        if preview_key == "idle":
            self.restore_idle_video()
            return {"applied": True, "animation_key": "idle", "warning": None}
        if self.play_action_motion(preview_key):
            return {"applied": True, "animation_key": preview_key, "warning": None}
        return {
            "applied": False,
            "animation_key": preview_key,
            "warning": "WebM switching not wired; displaying webm_key only",
        }

    def submit_agentic_text(self, text: str, provider: str) -> None:
        if self._interaction_worker and self._interaction_worker.isRunning():
            self.set_action_status("Interaction already running.", tone="warn", timeout_ms=2200)
            return

        cleaned = str(text or "").strip()
        if not cleaned:
            self.set_action_status("Please enter some text first.", tone="warn", timeout_ms=2200)
            return

        self._set_agentic_busy(True)
        self.set_action_status("Processing interaction...", tone="working")
        self._interaction_worker = HarnessInteractionWorker(self._adapter, cleaned, provider, self)
        self._interaction_worker.finished_payload.connect(self._on_agentic_result)
        self._interaction_worker.failed_message.connect(self._on_agentic_error)
        self._interaction_worker.finished.connect(self._clear_interaction_worker)
        self._interaction_worker.start()

    def _on_agentic_result(self, payload: dict) -> None:
        preview = self.apply_behavior_preview(payload.get("behavior_id"), payload.get("webm_key"))
        warnings = list(payload.get("warnings") or [])
        if preview.get("warning"):
            warnings.append(str(preview["warning"]))
        payload["warnings"] = warnings
        payload["animation_preview"] = preview
        tone = "warn" if warnings else "idle"
        self.set_action_status(payload.get("reply") or "Interaction complete.", tone=tone, timeout_ms=4200)
        self.refresh_agentic_ui(event_payload=payload)
        self._set_agentic_busy(False)

    def _on_agentic_error(self, message: str) -> None:
        self.set_action_status(message, tone="error", timeout_ms=4200)
        self.refresh_agentic_ui(message=message, tone="error", timeout_ms=4200)
        self._set_agentic_busy(False)

    def _clear_interaction_worker(self) -> None:
        if self._interaction_worker is not None:
            self._interaction_worker.deleteLater()
            self._interaction_worker = None

    def toggle_skill(self, skill_id: str, enabled: bool) -> None:
        try:
            self._adapter.set_skill_enabled(skill_id, enabled)
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="error", timeout_ms=3600)
            self.refresh_agentic_ui(message=str(exc), tone="error", timeoutMs=3600)
            return
        state_text = "enabled" if enabled else "disabled"
        self.set_action_status(f"Skill {skill_id} {state_text}.", tone="idle", timeout_ms=2400)
        self.refresh_agentic_ui(message=f"Skill {skill_id} {state_text}.", tone="idle", timeoutMs=2400)

    def toggle_tool(self, tool_name: str, enabled: bool) -> None:
        try:
            self._adapter.set_tool_enabled(tool_name, enabled)
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="error", timeout_ms=3600)
            self.refresh_agentic_ui(message=str(exc), tone="error", timeoutMs=3600)
            return
        state_text = "enabled" if enabled else "disabled"
        self.set_action_status(f"Tool {tool_name} {state_text}.", tone="idle", timeout_ms=2400)
        self.refresh_agentic_ui(message=f"Tool {tool_name} {state_text}.", tone="idle", timeoutMs=2400)

    def add_skill(self, payload_json: str) -> None:
        try:
            payload = json.loads(payload_json or "{}")
            created = self._adapter.add_skill(payload)
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="error", timeout_ms=3600)
            self.refresh_agentic_ui(message=str(exc), tone="error", timeoutMs=3600)
            return
        message = f"Skill {created['skill_id']} added."
        self.set_action_status(message, tone="idle", timeout_ms=2600)
        self.refresh_agentic_ui(message=message, tone="idle", timeoutMs=2600)

    def delete_skill(self, skill_id: str) -> None:
        try:
            result = self._adapter.delete_skill(skill_id)
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="error", timeout_ms=3600)
            self.refresh_agentic_ui(message=str(exc), tone="error", timeoutMs=3600)
            return
        message = f"Skill {skill_id} deleted." if result.get("deleted") else f"Skill {skill_id} disabled."
        self.set_action_status(message, tone="idle", timeout_ms=2600)
        self.refresh_agentic_ui(message=message, tone="idle", timeoutMs=2600)

    def add_tool_config(self, payload_json: str) -> None:
        try:
            payload = json.loads(payload_json or "{}")
            created = self._adapter.add_tool_config(payload)
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="error", timeout_ms=3600)
            self.refresh_agentic_ui(message=str(exc), tone="error", timeoutMs=3600)
            return
        message = f"Tool config {created['tool_name']} added."
        self.set_action_status(message, tone="idle", timeout_ms=2600)
        self.refresh_agentic_ui(message=message, tone="idle", timeoutMs=2600)

    def delete_tool_config(self, tool_name: str) -> None:
        try:
            result = self._adapter.delete_tool_config(tool_name)
        except Exception as exc:  # noqa: BLE001
            self.set_action_status(str(exc), tone="error", timeout_ms=3600)
            self.refresh_agentic_ui(message=str(exc), tone="error", timeoutMs=3600)
            return
        message = f"Tool config {tool_name} deleted." if result.get("deleted") else f"Tool {tool_name} disabled."
        self.set_action_status(message, tone="idle", timeout_ms=2600)
        self.refresh_agentic_ui(message=message, tone="idle", timeoutMs=2600)

    def refresh_agentic_ui(self, event_payload: dict | None = None, message: str | None = None, tone: str = "idle", timeoutMs: int = 0) -> None:
        try:
            payload = {
                "state": self._adapter.get_current_state(),
                "skills": self._adapter.list_skills(),
                "tools": self._adapter.list_tools(),
                "event": event_payload,
                "message": message,
                "tone": tone,
                "timeoutMs": timeoutMs,
            }
            self._run_javascript("hydrateAgenticUI", payload)
        except Exception as exc:
            print(f"[ECHOES] warning: refresh_agentic_ui failed: {exc}")

    def _set_agentic_busy(self, busy: bool) -> None:
        self._run_javascript("setAgenticBusy", bool(busy))

    def dispatch_action(self, directive: str) -> bool:
        return self._action_dispatcher.dispatch(directive)

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0) -> None:
        self._run_javascript("setActionStatus", message, tone, timeout_ms)

    def clear_action_status(self) -> None:
        self._run_javascript("clearActionStatus")

    def set_room_character(self, name: str) -> None:
        self._run_javascript("setRoomCharacter", name)

    def play_music(self, filename: str, title: str = "") -> bool:
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.isfile(absolute_path):
            print(f"[ECHOES] warning: missing audio file {filename}")
            return False

        source_url = QUrl.fromLocalFile(absolute_path).toString()
        self._run_javascript("playRoomAudio", source_url, title)
        return True

    def stop_music(self) -> None:
        self._run_javascript("stopRoomAudio")

    def get_render_diagnostics(self) -> dict[str, object]:
        settings = self.web_view.settings()
        return {
            "configured_width": self.WINDOW_WIDTH,
            "configured_height": self.WINDOW_HEIGHT,
            "current_width": self.width(),
            "current_height": self.height(),
            "webgl_enabled": settings.testAttribute(QWebEngineSettings.WebGLEnabled),
            "accelerated_2d_canvas_enabled": settings.testAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled),
        }

    def apply_character_position(self) -> None:
        self.move_character_to(self._character_x_offset, self._character_y_offset)

    def set_character_position(self, x_offset: int, y_offset: int) -> None:
        self._character_x_offset = x_offset
        self._character_y_offset = y_offset
        self.apply_character_position()

    def move_character_to(self, x_offset: int, y_offset: int) -> None:
        self._run_javascript("moveCharacter", x_offset, y_offset)

    def change_video(self, filename: str, loop: bool = True) -> None:
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.isfile(absolute_path):
            print(f"[ECHOES] warning: missing video file {filename}")
            return

        source_url = QUrl.fromLocalFile(absolute_path).toString()
        function_name = "setIdleVideo" if loop else "playTemporaryVideo"
        self._run_javascript(function_name, source_url)

    def _run_javascript(self, function_name: str, *args) -> None:
        if not self._webview_ready:
            self._pending_javascript_calls.append((function_name, args))
            return
        self.web_view.page().runJavaScript(self._build_javascript_bridge_call(function_name, *args))

    def _flush_pending_javascript_calls(self) -> None:
        if not self._webview_ready or not self._pending_javascript_calls:
            return

        pending_calls = self._pending_javascript_calls
        self._pending_javascript_calls = []
        for function_name, args in pending_calls:
            self._run_javascript(function_name, *args)

    @staticmethod
    def _build_javascript_bridge_call(function_name: str, *args) -> str:
        js_function_name = json.dumps(function_name)
        js_args = ", ".join(json.dumps(arg) for arg in args)
        return (
            "(function(){"
            f"var fn = window[{js_function_name}];"
            f"if (typeof fn !== 'function') {{ console.warn('[ECHOES] missing JS bridge fn:', {js_function_name}); return false; }}"
            f"fn({js_args});"
            "return true;"
            "})();"
        )

    def _resolve_media_path(self, filename: str) -> str | None:
        if not filename:
            return None
        if os.path.isabs(filename):
            return filename

        root_relative = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)
        if os.path.isfile(root_relative):
            return root_relative

        demo_relative = os.path.join(self.DEMO_ANIMATIONS_DIR, os.path.basename(filename))
        if os.path.isfile(demo_relative):
            return demo_relative

        return os.path.join(ASSETS_WEBM_DIR, filename)

    def nativeEvent(self, event_type, message):
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)

        WM_NCHITTEST = 0x0084
        WM_NCRBUTTONUP = 0x00A5
        HTCAPTION = 2

        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_NCRBUTTONUP:
                sx = ctypes.c_short(msg.lParam & 0xFFFF).value
                sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                local = self.mapFromGlobal(QPoint(sx, sy))
                if self.should_treat_point_as_caption(local.x(), local.y(), self.width(), self.height()):
                    self._build_menu().exec_(QPoint(sx, sy))
                    return True, 0
                return super().nativeEvent(event_type, message)
            if msg.message == WM_NCHITTEST:
                sx = ctypes.c_short(msg.lParam & 0xFFFF).value
                sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                local = self.mapFromGlobal(QPoint(sx, sy))
                if self.should_treat_point_as_caption(local.x(), local.y(), self.width(), self.height()):
                    return True, HTCAPTION
                return super().nativeEvent(event_type, message)
            return super().nativeEvent(event_type, message)
        except Exception:  # noqa: BLE001
            return super().nativeEvent(event_type, message)

    @classmethod
    def should_treat_point_as_caption(cls, local_x: int, local_y: int, width: int, height: int) -> bool:
        if local_x < 0 or local_y < 0 or local_x > width or local_y > height:
            return True

        panel_width = min(cls.AGENTIC_PANEL_MAX_WIDTH, int(width * cls.AGENTIC_PANEL_WIDTH_RATIO))
        panel_left = width - cls.AGENTIC_PANEL_RIGHT - panel_width
        panel_top = cls.AGENTIC_PANEL_TOP
        panel_bottom = height - cls.AGENTIC_PANEL_BOTTOM
        if panel_left <= local_x <= width - cls.AGENTIC_PANEL_RIGHT and panel_top <= local_y <= panel_bottom:
            return False

        xp_left = width - cls.XP_BADGE_RIGHT - cls.XP_BADGE_WIDTH
        xp_top = cls.XP_BADGE_TOP
        xp_bottom = xp_top + cls.XP_BADGE_HEIGHT
        if xp_left <= local_x <= width - cls.XP_BADGE_RIGHT and xp_top <= local_y <= xp_bottom:
            return False

        return True
