"""
ECHOES — PyQt5 透明無邊框桌面視窗
使用 QWebEngineView 載入 HTML/JS WebM 播放器，實現去背精靈渲染。
"""

import os
import sys
import ctypes
import ctypes.wintypes

from PyQt5.QtCore import Qt, QUrl, QPoint
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtWebEngineWidgets import QWebEngineView


class TransparentWindow(QMainWindow):
    """透明無邊框桌面寵物視窗"""

    WINDOW_SIZE = 800

    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self._init_window()
        self._init_webview()
        self._move_to_bottom_right()

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

    # ── Python → JS 橋接 ───────────────────────────────────

    def change_video(self, filename):
        """
        呼叫前端 JS 的 changeVideo() 切換影片。
        :param filename: 純檔名，例如 "happy.webm"
        """
        safe_name = filename.replace('"', '').replace("'", "").replace("\\", "")
        js = f'changeVideo("{safe_name}")'
        self.web_view.page().runJavaScript(js)

    # ── 滑鼠拖曳 ──────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    def contextMenuEvent(self, event):
        """右鍵選單：提供結束程序的入口"""
        from PyQt5.QtWidgets import QMenu, QAction
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #fff; border: 1px solid #444; border-radius: 6px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            "QMenu::item:selected { background: #c0392b; }"
        )
        quit_action = QAction("結束 ECHOES", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        menu.exec_(event.globalPos())

    # ── 透明區域點擊穿透 (Windows) ─────────────────────────

    def nativeEvent(self, event_type, message):
        """攔截 WM_NCHITTEST，透明像素回傳 HTTRANSPARENT 讓點擊穿過"""
        if sys.platform != "win32":
            return super().nativeEvent(event_type, message)

        WM_NCHITTEST = 0x0084
        HTTRANSPARENT = -1
        HTCLIENT = 1

        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message != WM_NCHITTEST:
                return super().nativeEvent(event_type, message)

            # 從 lParam 解出螢幕座標
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

            # 轉為視窗內座標
            local_pos = self.mapFromGlobal(QPoint(x, y))
            lx, ly = local_pos.x(), local_pos.y()

            # 邊界檢查
            if lx < 0 or ly < 0 or lx >= self.width() or ly >= self.height():
                return super().nativeEvent(event_type, message)

            # 擷取當前渲染畫面，取像素 alpha 值
            image = self.web_view.grab().toImage()
            if lx < image.width() and ly < image.height():
                pixel = image.pixelColor(lx, ly)
                if pixel.alpha() < 10:
                    # 透明像素 → 點擊穿透
                    return True, HTTRANSPARENT

            # 有內容的像素 → 正常處理（可拖曳）
            return True, HTCLIENT

        except Exception:
            return super().nativeEvent(event_type, message)
