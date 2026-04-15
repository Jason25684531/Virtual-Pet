"""
ECHOES — PyQt5 透明無邊框桌面視窗
使用 QWebEngineView 載入 HTML/JS WebM 播放器，實現去背精靈渲染。
"""

import ctypes
import ctypes.wintypes
import json
import os
import sys

from PyQt5.QtCore import Qt, QUrl, QPoint, QTimer
from PyQt5.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QMenu, QAction, QSystemTrayIcon
)
from PyQt5.QtWebEngineWidgets import QWebEngineSettings, QWebEngineView

from character_library import ASSETS_WEBM_DIR, CharacterLibrary, MOTION_MAP


class TransparentWindow(QMainWindow):
    """透明無邊框桌面寵物視窗"""

    WINDOW_WIDTH = 1920
    WINDOW_HEIGHT = 1080
    # 角色預設位移（相對於視窗中心的像素偏移量）
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
        "laugh": "雀躍大笑.webm",
        "angry": "薄怒嘟嘴.webm",
        "awkward": "尷尬擺手.webm",
        "speechless": "無言微翻白眼.webm",
        "listen": "專心聆聽.webm",
    }

    def __init__(self):
        super().__init__()
        self._library = CharacterLibrary()
        self._settings_dialog = None
        self._character_x_offset = self.DEFAULT_CHARACTER_X_OFFSET
        self._character_y_offset = self.DEFAULT_CHARACTER_Y_OFFSET
        self._init_window()
        self._init_webview()
        from action_dispatcher import ActionDispatcher
        self._action_dispatcher = ActionDispatcher(self, self._library, self)
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
        self.setStyleSheet("background: transparent;")
        self.resize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

    def _init_webview(self):
        """建立 QWebEngineView 並載入本地 HTML 播放器"""
        self.web_view = QWebEngineView(self)
        self.web_view.setStyleSheet("background: transparent;")

        # 停用 Chromium 的任何預設右鍵選單
        # 注意：有 HTCAPTION 路徑時，右鍵事件由 nativeEvent (WM_NCRBUTTONUP) 接管
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)

        self.setCentralWidget(self.web_view)

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)

        # 載入本地 index.html
        html_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "web_container", "index.html"
        )
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))
        self.web_view.loadFinished.connect(self._on_webview_loaded)

    def _on_webview_loaded(self, ok: bool):
        if not ok:
            print("[ECHOES] 警告: 房間頁面載入失敗。")
            return
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

    def _restore_current_character(self):
        current_character_id = self._library.get_current_character_id()
        if current_character_id and self.apply_character(current_character_id):
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
        self.change_video(idle_path, loop=True)
        self.apply_character_position()
        self.set_room_character(character_name)
        self.set_action_status(f"{character_name} 已待命", tone="idle", timeout_ms=2200)
        return True

    def preview_character_motion(self, character_id: str, motion_key: str):
        """播放指定角色動作，單次動作播完後回到 idle。"""
        motion_path = self._library.get_motion_path(character_id, motion_key)
        if not motion_path:
            print(f"[ECHOES] 警告: 找不到角色 {character_id} 的動作 {motion_key}。")
            return

        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        self.change_video(motion_path, loop=should_loop)

    def play_action_motion(self, motion_key: str) -> bool:
        should_loop = not MOTION_MAP.get(motion_key, {}).get("play_once", True)
        current_character_id = self._library.get_current_character_id()
        if current_character_id:
            motion_path = self._library.get_action_motion_path(current_character_id, motion_key)
            if motion_path:
                self.change_video(motion_path, loop=should_loop)
                return True

        demo_filename = self.DEMO_MOTION_MAPPING.get(motion_key)
        if demo_filename:
            demo_path = os.path.join(self.DEMO_ANIMATIONS_DIR, demo_filename)
            if os.path.isfile(demo_path):
                self.change_video(demo_path, loop=should_loop)
                return True

        print(f"[ECHOES] 警告: 找不到可播放的 action 動作 {motion_key}。")
        return False

    def restore_idle_video(self) -> bool:
        current_character_id = self._library.get_current_character_id()
        if current_character_id:
            idle_path = self._library.get_motion_path(current_character_id, "idle")
            if idle_path:
                self.change_video(idle_path, loop=True)
                return True

        demo_idle_path = os.path.join(
            self.DEMO_ANIMATIONS_DIR,
            self.DEMO_MOTION_MAPPING["idle"],
        )
        if os.path.isfile(demo_idle_path):
            self.change_video(demo_idle_path, loop=True)
            return True

        fallback_idle = os.path.join(ASSETS_WEBM_DIR, "idle.webm")
        if os.path.isfile(fallback_idle):
            self.change_video(fallback_idle, loop=True)
            return True
        return False

    def dispatch_action(self, directive: str) -> bool:
        return self._action_dispatcher.dispatch(directive)

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self._run_javascript("setActionStatus", message, tone, timeout_ms)

    def clear_action_status(self):
        self._run_javascript("clearActionStatus")

    def set_room_character(self, name: str):
        self._run_javascript("setRoomCharacter", name)

    def play_music(self, filename: str, title: str = "") -> bool:
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.isfile(absolute_path):
            print(f"[ECHOES] 警告: 音訊不存在，略過播放: {filename}")
            return False

        source_url = QUrl.fromLocalFile(absolute_path).toString()
        self._run_javascript("playRoomAudio", source_url, title)
        return True

    def stop_music(self):
        self._run_javascript("stopRoomAudio")

    def apply_character_position(self):
        """套用目前由 Python 管理的角色位移設定。"""
        self.move_character_to(self._character_x_offset, self._character_y_offset)

    def set_character_position(self, x_offset: int, y_offset: int):
        """更新角色位移設定並立即套用。"""
        self._character_x_offset = x_offset
        self._character_y_offset = y_offset
        self.apply_character_position()

    def move_character_to(self, x_offset: int, y_offset: int):
        """以左為正 x、以下為正 y 的像素偏移量移動角色。"""
        self._run_javascript("moveCharacter", x_offset, y_offset)

    # ── Python → JS 橋接 ───────────────────────────────────

    def change_video(self, filename, loop=True):
        """
        呼叫前端 JS 的 changeVideo() 切換影片。
        :param filename: 絕對路徑、相對路徑或舊版純檔名
        """
        absolute_path = self._resolve_media_path(filename)
        if not absolute_path or not os.path.isfile(absolute_path):
            print(f"[ECHOES] 警告: 影片不存在，略過切換: {filename}")
            return

        source_url = QUrl.fromLocalFile(absolute_path).toString()
        function_name = "setIdleVideo" if loop else "playTemporaryVideo"
        self._run_javascript(function_name, source_url)

    def _run_javascript(self, function_name: str, *args):
        js_args = ", ".join(json.dumps(arg) for arg in args)
        self.web_view.page().runJavaScript(f"{function_name}({js_args})")

    def _resolve_media_path(self, filename: str) -> str | None:
        if not filename:
            return None

        if os.path.isabs(filename):
            return filename

        root_relative = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            filename,
        )
        if os.path.isfile(root_relative):
            return root_relative

        demo_relative = os.path.join(
            self.DEMO_ANIMATIONS_DIR,
            os.path.basename(filename),
        )
        if os.path.isfile(demo_relative):
            return demo_relative

        legacy_relative = os.path.join(ASSETS_WEBM_DIR, filename)
        return legacy_relative

    # ── 透明區域點擊穿透 + 右鍵選單 (Windows) ────────────────

    def nativeEvent(self, event_type, message):
        """
        攔截 Windows 訊息：
                    WM_NCHITTEST   → 整個視窗視為標題列，可直接拖曳
          WM_NCRBUTTONUP → 不透明像素右鍵放開，彈出自訂選單
        """
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)

        WM_NCHITTEST   = 0x0084
        WM_NCRBUTTONUP = 0x00A5   # 標題列(NC)右鍵放開時觸發
        HTCAPTION      = 2        # 讓 Windows 處理拖曳

        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))

            # ── NC 右鍵放開 → 彈出自訂選單，阻止 Windows 系統選單 ──
            if msg.message == WM_NCRBUTTONUP:
                sx = ctypes.c_short(msg.lParam & 0xFFFF).value
                sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                self._build_menu().exec_(QPoint(sx, sy))
                return True, 0   # 已消費，不傳遞給 Windows

            if msg.message == WM_NCHITTEST:
                return True, HTCAPTION

            return super().nativeEvent(event_type, message)

        except Exception:
            return super().nativeEvent(event_type, message)
