"""
ECHOES — PyQt5 透明無邊框桌面視窗
使用 QWebEngineView 載入 HTML/JS WebM 播放器，實現去背精靈渲染。
"""

import os
import sys
import ctypes
import ctypes.wintypes

from PyQt5.QtCore import Qt, QUrl, QPoint
from PyQt5.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QMenu, QAction, QSystemTrayIcon
)
from PyQt5.QtWebEngineWidgets import QWebEngineView


class TransparentWindow(QMainWindow):
    """透明無邊框桌面寵物視窗"""

    WINDOW_SIZE = 800

    def __init__(self):
        super().__init__()
        self._init_window()
        self._init_webview()
        self._move_to_bottom_right()
        self._init_tray()

    # ── 視窗初始化 ──────────────────────────────────────────

    def _init_window(self):
        """設定無邊框、置頂、透明背景"""
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # 不在工作列顯示圖示
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.resize(self.WINDOW_SIZE, self.WINDOW_SIZE)

    def _init_webview(self):
        """建立 QWebEngineView 並載入本地 HTML 播放器"""
        self.web_view = QWebEngineView(self)
        self.web_view.setStyleSheet("background: transparent;")

        # 停用 Chromium 的任何預設右鍵選單
        # 注意：有 HTCAPTION 路徑時，右鍵事件由 nativeEvent (WM_NCRBUTTONUP) 接管
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)

        self.setCentralWidget(self.web_view)

        # 關鍵：讓 Chromium 渲染層背景完全透明
        self.web_view.page().setBackgroundColor(QColor(0, 0, 0, 0))

        # 載入本地 index.html
        html_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "web_container", "index.html"
        )
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))

    def _move_to_bottom_right(self):
        """將視窗定位到螢幕右下角"""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.right() - self.WINDOW_SIZE - 20
            y = geo.bottom() - self.WINDOW_SIZE - 20
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

        menu.addSeparator()

        quit_action = QAction("✕  離開 ECHOES", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        return menu

    def _open_settings(self):
        """開啟角色設定對話框，算圖完成後自動刷新影片"""
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.generation_done.connect(lambda: self.change_video("idle.webm"))
        dlg.exec_()

    # ── Python → JS 橋接 ───────────────────────────────────

    def change_video(self, filename):
        """
        呼叫前端 JS 的 changeVideo() 切換影片。
        :param filename: 純檔名，例如 "happy.webm"
        """
        safe_name = filename.replace('"', '').replace("'", "").replace("\\", "")
        js = f'changeVideo("{safe_name}")'
        self.web_view.page().runJavaScript(js)

    # ── 透明區域點擊穿透 + 右鍵選單 (Windows) ────────────────

    def nativeEvent(self, event_type, message):
        """
        攔截 Windows 訊息：
          WM_NCHITTEST   → 透明像素穿透 / 不透明像素 HTCAPTION（可拖曳）
          WM_NCRBUTTONUP → 不透明像素右鍵放開，彈出自訂選單
        """
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)

        WM_NCHITTEST   = 0x0084
        WM_NCRBUTTONUP = 0x00A5   # 標題列(NC)右鍵放開時觸發
        HTTRANSPARENT  = -1
        HTCAPTION      = 2        # 讓 Windows 處理拖曳

        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))

            # ── NC 右鍵放開 → 彈出自訂選單，阻止 Windows 系統選單 ──
            if msg.message == WM_NCRBUTTONUP:
                sx = ctypes.c_short(msg.lParam & 0xFFFF).value
                sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                self._build_menu().exec_(QPoint(sx, sy))
                return True, 0   # 已消費，不傳遞給 Windows

            if msg.message != WM_NCHITTEST:
                return super().nativeEvent(event_type, message)

            # ── 命中測試 ──
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

            local_pos = self.mapFromGlobal(QPoint(x, y))
            lx, ly = local_pos.x(), local_pos.y()

            if lx < 0 or ly < 0 or lx >= self.width() or ly >= self.height():
                return super().nativeEvent(event_type, message)

            image = self.web_view.grab().toImage()
            if lx < image.width() and ly < image.height():
                pixel = image.pixelColor(lx, ly)
                if pixel.alpha() < 10:
                    return True, HTTRANSPARENT  # 透明 → 點擊穿透

            return True, HTCAPTION  # 有內容 → 拖曳

        except Exception:
            return super().nativeEvent(event_type, message)
