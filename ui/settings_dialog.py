"""
ECHOES — 角色設定對話框
提供圖片上傳、ComfyUI 算圖觸發、進度條顯示。
"""

import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QProgressBar, QTextEdit, QLineEdit,
)

from api_client.comfyui_client import ComfyUIClient


# ── 背景算圖 Worker ─────────────────────────────────────────

class GenerationWorker(QThread):
    """在背景執行緒中執行完整 ComfyUI 算圖流程。"""

    progress_updated = pyqtSignal(int)         # 0-100
    finished_signal = pyqtSignal(bool, str)    # (成功?, 訊息)

    def __init__(self, image_dir: str, positive_prompt: str = "", negative_prompt: str = "", parent=None):
        super().__init__(parent)
        self._image_dir = image_dir
        self._positive_prompt = positive_prompt
        self._negative_prompt = negative_prompt

    def run(self):
        try:
            client = ComfyUIClient()

            if not client.check_connection():
                self.finished_signal.emit(
                    False, "無法連線至 ComfyUI，請確認已啟動。"
                )
                return

            archived = client.generate(
                self._image_dir,
                on_progress=self._on_progress,
                positive_prompt=self._positive_prompt,
                negative_prompt=self._negative_prompt,
            )
            self.finished_signal.emit(
                True, f"✓ 生成完成！已歸檔 {len(archived)} 支影片。"
            )
        except Exception as e:
            self.finished_signal.emit(False, f"算圖失敗: {e}")

    def _on_progress(self, percent: int):
        self.progress_updated.emit(percent)


# ── 設定對話框 ───────────────────────────────────────────────

# 共用按鈕樣式
_BTN_STYLE = (
    "QPushButton {{ background: {bg}; color: #fff; border: none;"
    " border-radius: 6px; font-size: 14px; }}"
    "QPushButton:hover {{ background: {hover}; }}"
    "QPushButton:pressed {{ background: {pressed}; }}"
    "QPushButton:disabled {{ background: #888; }}"
)
_BTN_RED = _BTN_STYLE.format(bg="#c0392b", hover="#e74c3c", pressed="#a93226")
_BTN_BLUE = _BTN_STYLE.format(bg="#2980b9", hover="#3498db", pressed="#1f6fa0")


class SettingsDialog(QDialog):
    """角色設定視窗：圖片上傳 → 算圖 → 進度顯示"""

    generation_done = pyqtSignal()  # 算圖完成後通知外部

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ECHOES — 角色設定")
        self.setMinimumSize(520, 680)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._image_path: str | None = None
        self._worker: GenerationWorker | None = None
        self._init_ui()

    # ── UI 建構 ──────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        # 標題
        title = QLabel("角色設定")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c2c2c;")
        layout.addWidget(title)

        # 分隔線
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #ddd;")
        layout.addWidget(sep)

        # 圖片選擇區
        pick_row = QHBoxLayout()
        self._pick_btn = QPushButton("📂 選擇角色圖片")
        self._pick_btn.setFixedHeight(36)
        self._pick_btn.setStyleSheet(_BTN_BLUE)
        self._pick_btn.clicked.connect(self._pick_image)
        pick_row.addWidget(self._pick_btn)

        self._path_label = QLabel("尚未選擇圖片")
        self._path_label.setStyleSheet("color: #666; font-size: 12px;")
        self._path_label.setWordWrap(True)
        pick_row.addWidget(self._path_label, stretch=1)
        layout.addLayout(pick_row)

        # 圖片預覽
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setFixedHeight(160)
        self._preview.setStyleSheet(
            "border: 2px dashed #ccc; border-radius: 8px; background: #fafafa;"
        )
        self._preview.setText("預覽區")
        layout.addWidget(self._preview)

        # 分隔線
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #ddd;")
        layout.addWidget(sep2)

        # Positive Prompt
        pos_label = QLabel("✨ Positive Prompt（描述角色動作最希望的表現）")
        pos_label.setStyleSheet("font-size: 12px; color: #333; font-weight: bold;")
        layout.addWidget(pos_label)

        self._positive_edit = QTextEdit()
        self._positive_edit.setFixedHeight(72)
        self._positive_edit.setPlaceholderText(
            "例：the character is happy and waving hands, smooth motion"
        )
        self._positive_edit.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 4px;"
            " font-size: 13px; padding: 4px;"
        )
        layout.addWidget(self._positive_edit)

        # Negative Prompt
        neg_label = QLabel("🚫 Negative Prompt（描述希望排除的內容）")
        neg_label.setStyleSheet("font-size: 12px; color: #333; font-weight: bold;")
        layout.addWidget(neg_label)

        self._negative_edit = QLineEdit()
        self._negative_edit.setFixedHeight(34)
        self._negative_edit.setPlaceholderText(
            "留空則使用 JSON 預設負向詞。也可手動輸入。"
        )
        self._negative_edit.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 4px;"
            " font-size: 12px; padding: 4px;"
        )
        layout.addWidget(self._negative_edit)

        # 生成按鈕
        self._gen_btn = QPushButton("🎬 開始生成動態")
        self._gen_btn.setFixedHeight(40)
        self._gen_btn.setStyleSheet(_BTN_BLUE)
        self._gen_btn.setEnabled(False)
        self._gen_btn.clicked.connect(self._start_generation)
        layout.addWidget(self._gen_btn)

        # 進度條
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #ccc; border-radius: 4px;"
            " text-align: center; height: 22px; }"
            "QProgressBar::chunk { background: #2980b9; border-radius: 3px; }"
        )
        layout.addWidget(self._progress)

        # 狀態文字
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("font-size: 13px; color: #444;")
        layout.addWidget(self._status)

        layout.addStretch()

        # 關閉按鈕
        close_btn = QPushButton("關閉")
        close_btn.setFixedHeight(36)
        close_btn.setStyleSheet(_BTN_RED)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    # ── 圖片選擇 ─────────────────────────────────────────

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇角色圖片", "",
            "圖片檔案 (*.png *.jpg *.jpeg *.webp);;所有檔案 (*)",
        )
        if not path:
            return

        self._image_path = path
        self._path_label.setText(os.path.basename(path))

        # 預覽
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self._preview.width() - 4,
                self._preview.height() - 4,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._preview.setPixmap(scaled)

        self._gen_btn.setEnabled(True)
        self._status.setText("")

    # ── 算圖觸發 ─────────────────────────────────────────

    def _start_generation(self):
        if not self._image_path:
            return

        image_dir = os.path.dirname(self._image_path)
        positive_prompt = self._positive_edit.toPlainText().strip()
        negative_prompt = self._negative_edit.text().strip()

        # 鎖定 UI 防止重複觸發
        self._gen_btn.setEnabled(False)
        self._pick_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status.setText("算圖中，請稍候…")

        self._worker = GenerationWorker(
            image_dir,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            parent=self,
        )
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, percent: int):
        self._progress.setValue(percent)

    def _on_finished(self, success: bool, message: str):
        self._status.setText(message)
        self._pick_btn.setEnabled(True)

        if success:
            self._status.setStyleSheet("font-size: 13px; color: #27ae60;")
            self.generation_done.emit()
        else:
            self._status.setStyleSheet("font-size: 13px; color: #c0392b;")
            self._gen_btn.setEnabled(bool(self._image_path))

        self._worker = None
