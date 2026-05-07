"""
ECHOES — PyQt5 透明無邊框桌面視窗
使用 QWebEngineView 載入 HTML/JS WebM 播放器，實現去背精靈渲染。
"""

import json
import os

from PyQt5.QtCore import QEvent, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt5.QtWidgets import (
    QAction, QApplication, QLineEdit, QMainWindow, QMenu, QPushButton, QSystemTrayIcon, QWidget,
)
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineSettings, QWebEngineView

from character_library import ASSETS_WEBM_DIR, CharacterLibrary, MOTION_MAP
from interaction_trace import InteractionLatencyTracker


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


class TransparentWindow(QMainWindow):
    """透明無邊框桌面寵物視窗"""
    developer_query_submitted = pyqtSignal(str)
    stt_start_requested = pyqtSignal()
    stt_stop_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    RAW_JAVASCRIPT_MARKER = "__raw_javascript__"

    # 視窗尺寸（可根據需求調整，或改為全螢幕）：
    WINDOW_WIDTH = 1920
    WINDOW_HEIGHT = 1080
    DRAG_SURFACE_HEIGHT = 160
    DEV_INPUT_WIDTH = 560
    DEV_INPUT_HEIGHT = 44
    DEV_INPUT_MARGIN_BOTTOM = 28
    STT_BUTTON_WIDTH = 132
    STT_BUTTON_HEIGHT = 40
    STT_BUTTON_MARGIN_LEFT = 24
    STT_BUTTON_MARGIN_BOTTOM = 30
    RESET_BUTTON_WIDTH = 96
    RESET_BUTTON_HEIGHT = 40
    RESET_BUTTON_GAP = 12
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

    def __init__(self, latency_tracker: InteractionLatencyTracker | None = None):
        super().__init__()
        self._library = CharacterLibrary()
        self._latency_tracker = latency_tracker
        self._settings_dialog = None
        self._character_x_offset = self.DEFAULT_CHARACTER_X_OFFSET
        self._character_y_offset = self.DEFAULT_CHARACTER_Y_OFFSET
        self._character_scale = self.DEFAULT_CHARACTER_SCALE
        self._character_object_position = self.DEFAULT_CHARACTER_OBJECT_POSITION
        self._webview_ready = False
        self._drag_pos = None
        self._stt_listening = False
        self._stt_available = True
        self._stt_state = "idle"
        self._pending_javascript_calls: list[tuple[str, tuple[object, ...]]] = []
        self._init_window()
        self._init_webview()
        self._init_drag_surface()
        self._init_developer_input()
        self._init_stt_button()
        self._init_reset_button()
        from action_dispatcher import ActionDispatcher
        self._action_dispatcher = ActionDispatcher(
            self,
            self._library,
            latency_tracker=self._latency_tracker,
            parent=self,
        )
        # 將 panel video 結束回調掛上 web page（JS → Python 通知）
        web_page = self.web_view.page()
        if isinstance(web_page, EchoesWebPage):
            web_page.panel_ended_callback = self._action_dispatcher._on_panel_video_ended
            web_page.main_video_ended_callback = self._action_dispatcher._on_main_video_ended
            web_page.room_audio_ended_callback = self._action_dispatcher._on_room_audio_ended
        self._move_to_bottom_right()
        self._init_tray()

    # ── 視窗初始化 ──────────────────────────────────────────

    def _init_window(self):
        """設定無邊框、置頂視窗"""
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # 不在工作列顯示圖示
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.resize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

    def _init_webview(self):
        """建立 QWebEngineView 並載入本地 HTML 播放器"""
        self.web_view = QWebEngineView(self)
        self.web_view.setStyleSheet("background: transparent;")

        # 停用 Chromium 的任何預設右鍵選單，改由 Qt 視窗層統一處理。
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)

        # 掛上自訂 Page，讓前端 console 訊息可轉印至 Python Terminal。
        self.web_view.setPage(EchoesWebPage(self.web_view))

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

    def _init_drag_surface(self):
        """建立僅覆蓋房間頂部的拖曳層，避免攔截整個 WebGL 畫面。"""
        self._drag_surface = QWidget(self)
        self._drag_surface.setObjectName("drag-surface")
        self._drag_surface.setStyleSheet("background: transparent;")
        self._drag_surface.setCursor(Qt.OpenHandCursor)
        self._drag_surface.installEventFilter(self)
        self._update_drag_surface_geometry()
        self._drag_surface.raise_()

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
        self._developer_input._focus_lost_callback = self._hide_developer_input
        self._developer_input.hide()
        self._developer_input.setEnabled(False)
        self._developer_input.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._developer_input.installEventFilter(self)
        self.web_view.installEventFilter(self)
        self.installEventFilter(self)
        self._update_developer_input_geometry()

    def _init_stt_button(self):
        self._stt_button = QPushButton(self)
        self._stt_button.setObjectName("stt-toggle-button")
        self._stt_button.setFixedSize(self.STT_BUTTON_WIDTH, self.STT_BUTTON_HEIGHT)
        self._stt_button.clicked.connect(self._handle_stt_button_clicked)
        self._stt_button.installEventFilter(self)
        self._apply_stt_button_state()
        self._update_stt_button_geometry()

    def _init_reset_button(self):
        self._reset_button = QPushButton(self)
        self._reset_button.setObjectName("runtime-reset-button")
        self._reset_button.setText("重置")
        self._reset_button.setFixedSize(self.RESET_BUTTON_WIDTH, self.RESET_BUTTON_HEIGHT)
        self._reset_button.clicked.connect(self.reset_requested.emit)
        self._reset_button.installEventFilter(self)
        self._reset_button.setStyleSheet(
            """
            QPushButton#runtime-reset-button {
                background: rgba(42, 64, 86, 215);
                color: #ffffff;
                border: 1px solid rgba(210, 232, 255, 140);
                border-radius: 14px;
                font-size: 15px;
                font-weight: 600;
                padding: 0 14px;
            }
            QPushButton#runtime-reset-button:hover {
                background: rgba(54, 82, 110, 235);
            }
            """
        )
        self._update_reset_button_geometry()

    def _apply_stt_button_state(self):
        if not hasattr(self, "_stt_button"):
            return

        state = "unavailable" if not self._stt_available else self._stt_state

        if state == "unavailable":
            label = "STT 不可用"
            background = "rgba(92, 92, 92, 180)"
            border = "rgba(190, 190, 190, 110)"
            enabled = False
        elif state == "starting":
            label = "啟動中..."
            background = "rgba(88, 120, 160, 205)"
            border = "rgba(218, 234, 255, 140)"
            enabled = False
        elif state == "listening":
            label = "結束收音"
            background = "rgba(176, 52, 52, 215)"
            border = "rgba(255, 214, 214, 160)"
            enabled = True
        elif state == "stopping":
            label = "停止中..."
            background = "rgba(132, 96, 62, 205)"
            border = "rgba(255, 232, 208, 140)"
            enabled = False
        else:
            label = "開始收音"
            background = "rgba(32, 126, 92, 215)"
            border = "rgba(210, 255, 239, 150)"
            enabled = True

        self._stt_button.setText(label)
        self._stt_button.setEnabled(enabled)
        self._stt_button.setStyleSheet(
            f"""
            QPushButton#stt-toggle-button {{
                background: {background};
                color: #ffffff;
                border: 1px solid {border};
                border-radius: 14px;
                font-size: 15px;
                font-weight: 600;
                padding: 0 14px;
            }}
            QPushButton#stt-toggle-button:disabled {{
                color: rgba(255, 255, 255, 0.75);
            }}
            """
        )
        if hasattr(self, "_tray_stt_toggle_action"):
            self._tray_stt_toggle_action.setText(label)
            self._tray_stt_toggle_action.setEnabled(enabled)

    def _update_stt_button_geometry(self):
        if not hasattr(self, "_stt_button"):
            return
        y = max(
            self.DRAG_SURFACE_HEIGHT + 24,
            self.height() - self.STT_BUTTON_HEIGHT - self.STT_BUTTON_MARGIN_BOTTOM,
        )
        self._stt_button.move(self.STT_BUTTON_MARGIN_LEFT, y)

    def _update_reset_button_geometry(self):
        if not hasattr(self, "_reset_button"):
            return
        y = max(
            self.DRAG_SURFACE_HEIGHT + 24,
            self.height() - self.RESET_BUTTON_HEIGHT - self.STT_BUTTON_MARGIN_BOTTOM,
        )
        x = self.STT_BUTTON_MARGIN_LEFT + self.STT_BUTTON_WIDTH + self.RESET_BUTTON_GAP
        self._reset_button.move(x, y)

    def _update_drag_surface_geometry(self):
        if hasattr(self, "_drag_surface"):
            self._drag_surface.setGeometry(0, 0, self.width(), self.DRAG_SURFACE_HEIGHT)

    def _update_developer_input_geometry(self):
        if not hasattr(self, "_developer_input"):
            return

        available_width = max(320, min(self.DEV_INPUT_WIDTH, self.width() - 48))
        x = max(24, (self.width() - available_width) // 2)
        y = max(
            self.DRAG_SURFACE_HEIGHT + 24,
            self.height() - self.DEV_INPUT_HEIGHT - self.DEV_INPUT_MARGIN_BOTTOM,
        )
        self._developer_input.setGeometry(x, y, available_width, self.DEV_INPUT_HEIGHT)

    def _raise_overlay_widgets(self):
        if hasattr(self, "_drag_surface"):
            self._drag_surface.raise_()
        if hasattr(self, "_stt_button"):
            self._stt_button.raise_()
        if hasattr(self, "_reset_button"):
            self._reset_button.raise_()
        if hasattr(self, "_developer_input") and self._developer_input.isVisible():
            self._developer_input.raise_()

    def _on_webview_loaded(self, ok: bool):
        if not ok:
            print("[ECHOES] 警告: 房間頁面載入失敗。")
            return
        self._webview_ready = True
        self._flush_pending_javascript_calls()
        self._raise_overlay_widgets()
        QTimer.singleShot(120, self._restore_current_character)

    def _move_to_bottom_right(self):
        """將視窗定位到螢幕右下角"""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + max(0, geo.width() - self.WINDOW_WIDTH - 20)
            y = geo.y() + max(0, geo.height() - self.WINDOW_HEIGHT - 20)
            self.move(x, y)

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
        reset_action.triggered.connect(self.reset_requested.emit)
        menu.addAction(reset_action)

        self._tray_stt_toggle_action = QAction("開始收音", self)
        self._tray_stt_toggle_action.triggered.connect(self._handle_stt_button_clicked)
        menu.addAction(self._tray_stt_toggle_action)

        menu.addSeparator()

        quit_action = QAction("✕  離開 ECHOES", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        return menu

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
            if (
                watched is not self._developer_input
                and event.key() == Qt.Key_D
                and event.modifiers() == Qt.NoModifier
                and not event.isAutoRepeat()
            ):
                self.toggle_developer_input()
                return True

        if watched is self._drag_surface:
            event_type = event.type()
            if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._handle_drag_press(event)
                return True
            if event_type == QEvent.MouseMove and event.buttons() & Qt.LeftButton:
                self._handle_drag_move(event)
                return True
            if event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._handle_drag_release(event)
                return True
            if event_type == QEvent.ContextMenu:
                self._show_context_menu(event.globalPos())
                return True

        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_drag_surface_geometry()
        self._update_developer_input_geometry()
        self._update_stt_button_geometry()
        self._update_reset_button_geometry()
        self._raise_overlay_widgets()

    def keyPressEvent(self, event):
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

    def mousePressEvent(self, event):
        self._handle_drag_press(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._handle_drag_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._handle_drag_release(event)
        super().mouseReleaseEvent(event)

    def _handle_drag_press(self, event):
        if event.button() != Qt.LeftButton:
            return

        window_handle = self.windowHandle()
        if window_handle is not None and window_handle.startSystemMove():
            self._drag_pos = None
            return

        self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _handle_drag_move(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        if self._drag_pos is None:
            return
        self.move(event.globalPos() - self._drag_pos)

    def _handle_drag_release(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = None

    _FALLBACK_CHARACTER_IDS = ("miku", "Choppr")

    def _restore_current_character(self):
        current_character_id = self._library.get_current_character_id()
        if current_character_id and self.apply_character(current_character_id):
            return

        for fallback_id in self._FALLBACK_CHARACTER_IDS:
            if self.apply_character(fallback_id):
                return

        if self.restore_idle_video():
            self.set_room_character("訪客模式")
            self.set_action_status("房間模式已載入", tone="idle", timeout_ms=2400)
            self.apply_character_position()

    def apply_character(self, character_id: str) -> bool:
        """套用指定角色並切回 idle。"""
        character_name = self._library.get_character_name(character_id) or character_id
        idle_path = self._library.get_motion_path(character_id, "idle")
        if not idle_path:
            print(f"[ECHOES] 警告: 角色 {character_id} 尚未生成 idle 動畫。")
            return False

        self._library.set_current_character_id(character_id)
        self.restore_idle_video()
        self.apply_character_layout(character_id)
        self.set_room_character(character_name)
        self.set_action_status(f"{character_name} 已待命", tone="idle", timeout_ms=2200)

        bg_path = self._library.get_background_path(character_id)
        if bg_path and os.path.isfile(bg_path):
            bg_url = QUrl.fromLocalFile(bg_path).toString()
            self._run_javascript("setRoomBackground", bg_url)

        return True

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
        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        current_character_id = self._library.get_current_character_id()
        if current_character_id:
            motion_path = self._library.get_action_motion_path(current_character_id, motion_key)
            if not motion_path:
                motion_path = self._library.get_motion_path(current_character_id, motion_key)
            if motion_path:
                print(f"[ECHOES] 播放角色動作 `{motion_key}`: {motion_path}")
                return self.change_video(motion_path, loop=should_loop)

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
        current_character_id = self._library.get_current_character_id()
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
            or self._action_dispatcher.is_tts_busy
            or self._action_dispatcher.has_active_motion
        )

    def dispatch_action(
        self,
        directive: str,
        trace_id: str | None = None,
        allow_tts: bool = True,
    ) -> bool:
        return self._action_dispatcher.dispatch(
            directive,
            trace_id=trace_id,
            allow_tts=allow_tts,
        )

    def speak_text(self, message: str, trace_id: str | None = None, has_action: bool = False):
        self._action_dispatcher.speak_text(message, trace_id=trace_id, has_action=has_action)

    def complete_tts_trace(self, trace_id: str | None):
        self._action_dispatcher.complete_tts_trace(trace_id)

    def begin_conversation_turn(self, trace_id: str, source_label: str, user_text: str):
        self._run_javascript("beginConversationTurn", trace_id, source_label, user_text)

    def append_conversation_assistant(self, trace_id: str, fragment: str):
        self._run_javascript("appendConversationAssistant", trace_id, fragment)

    def finish_conversation_turn(self, trace_id: str):
        self._run_javascript("finishConversationTurn", trace_id)

    def set_conversation_queue_depth(self, queue_depth: int):
        self._run_javascript("setConversationQueueDepth", int(queue_depth))

    def clear_conversation_turns(self):
        self._run_javascript("clearConversationTurns")

    def set_stt_listening(self, active: bool):
        self.set_stt_state("listening" if active else "idle")

    def set_stt_state(self, state: str):
        normalized = str(state or "idle").strip().lower()
        if normalized not in {"idle", "starting", "listening", "stopping", "unavailable"}:
            normalized = "idle"
        self._stt_state = normalized
        self._stt_listening = normalized == "listening"
        self._stt_available = normalized != "unavailable"
        self._apply_stt_button_state()

    def set_stt_available(self, available: bool):
        self._stt_available = bool(available)
        if not self._stt_available:
            self._stt_state = "unavailable"
            self._stt_listening = False
        elif self._stt_state == "unavailable":
            self._stt_state = "idle"
        self._apply_stt_button_state()

    def _handle_stt_button_clicked(self):
        if not self._stt_available:
            self.set_action_status("Azure STT 尚未配置完成。", tone="warn", timeout_ms=3200)
            return
        if self._stt_state in {"starting", "stopping"}:
            return
        if self._stt_state == "listening":
            self.stt_stop_requested.emit()
            return
        self.stt_start_requested.emit()

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
        self._action_dispatcher.reset_runtime_state()
        self.stop_music()
        self.stop_motion_loop()
        self.clear_panel_video()
        self.clear_conversation_turns()
        self.set_conversation_queue_depth(0)
        self._hide_developer_input()
        self._run_javascript("resetRoomState")
        self.restore_idle_video()
        self.set_action_status("已重置，等待下一次互動。", tone="idle", timeout_ms=2400)

    def shutdown_background_tasks(self):
        self._action_dispatcher.shutdown()

    def get_current_character_id(self) -> str | None:
        return self._library.get_current_character_id()

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
        if not self._webview_ready:
            self._pending_javascript_calls.append((self.RAW_JAVASCRIPT_MARKER, (script,)))
            return

        self.web_view.page().runJavaScript(script)

    def _run_javascript(self, function_name: str, *args):
        if not self._webview_ready:
            self._pending_javascript_calls.append((function_name, args))
            return

        if function_name == self.RAW_JAVASCRIPT_MARKER:
            script = str(args[0]) if args else ""
            self.web_view.page().runJavaScript(script)
            return

        self.web_view.page().runJavaScript(self._build_javascript_bridge_call(function_name, *args))

    def _flush_pending_javascript_calls(self):
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
            f"if (typeof fn !== 'function') {{ console.warn('[ECHOES] JS bridge 缺少函式:', {js_function_name}); return false; }}"
            f"fn({js_args});"
            "return true;"
            "})();"
        )

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
