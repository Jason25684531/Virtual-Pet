from PyQt5.QtCore import QTimer
from PyQt5.QtWebEngineWidgets import QWebEnginePage
from PyQt5.QtWidgets import QLineEdit


class EchoesWebPage(QWebEnginePage):
    """將前端 console 訊息轉印至 Python Terminal。"""

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
