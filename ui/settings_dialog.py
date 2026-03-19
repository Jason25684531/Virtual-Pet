"""
ECHOES — 角色設定對話框
提供角色庫選擇、圖片上傳、ComfyUI 算圖觸發與動作預覽。
"""

import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QProgressBar, QTextEdit, QLineEdit,
    QComboBox,
)

from api_client.comfyui_client import ComfyUIClient
from character_library import CharacterLibrary, MOTION_SPECS


# ── 背景算圖 Worker ─────────────────────────────────────────

class GenerationWorker(QThread):
    """在背景執行緒中執行完整 ComfyUI 算圖流程。"""

    progress_updated = pyqtSignal(int)                  # 0-100
    finished_signal = pyqtSignal(bool, str, str, object)  # (成功?, 訊息, character_id, archived)

    def __init__(
        self,
        image_dir: str,
        target_dir: str,
        character_id: str,
        positive_prompt: str = "",
        negative_prompt: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._image_dir = image_dir
        self._target_dir = target_dir
        self._character_id = character_id
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
                self._target_dir,
                on_progress=self._on_progress,
                positive_prompt=self._positive_prompt,
                negative_prompt=self._negative_prompt,
            )
            self.finished_signal.emit(
                True,
                f"✓ 生成完成！已歸檔 {len(archived)} 支影片。",
                self._character_id,
                archived,
            )
        except Exception as e:
            self.finished_signal.emit(False, f"算圖失敗: {e}", self._character_id, None)

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
    """角色設定視窗：角色庫 → 圖片上傳 → 算圖 → 動作預覽"""

    apply_character_requested = pyqtSignal(str)
    preview_motion_requested = pyqtSignal(str, str)
    generation_done = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._library = CharacterLibrary()
        self.setWindowTitle("ECHOES — 角色設定")
        self.setMinimumSize(620, 760)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setWindowModality(Qt.NonModal)

        self._image_path: str | None = None
        self._worker: GenerationWorker | None = None
        self._pending_character_id: str | None = None
        self._init_ui()
        self._reload_characters(select_id=self._library.get_current_character_id())

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
        self._preview.setFixedHeight(200)
        self._preview.setStyleSheet(
            "border: 2px dashed #ccc; border-radius: 8px; background: #fafafa;"
        )
        self._preview.setText("預覽區")
        layout.addWidget(self._preview)

        # 角色名稱
        name_label = QLabel("🪪 角色名稱（未填則使用檔名）")
        name_label.setStyleSheet("font-size: 12px; color: #333; font-weight: bold;")
        layout.addWidget(name_label)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例：小夏 / Norlan_v1")
        self._name_edit.setFixedHeight(34)
        self._name_edit.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 4px;"
            " font-size: 12px; padding: 4px;"
        )
        layout.addWidget(self._name_edit)

        # 角色庫
        character_label = QLabel("🗂 已生成角色")
        character_label.setStyleSheet("font-size: 12px; color: #333; font-weight: bold;")
        layout.addWidget(character_label)

        character_row = QHBoxLayout()
        self._character_combo = QComboBox()
        self._character_combo.currentIndexChanged.connect(self._on_character_changed)
        self._character_combo.setStyleSheet(
            "QComboBox { border: 1px solid #ccc; border-radius: 4px; padding: 4px; font-size: 12px; }"
        )
        character_row.addWidget(self._character_combo, stretch=1)

        self._refresh_btn = QPushButton("重新整理")
        self._refresh_btn.setFixedHeight(34)
        self._refresh_btn.setStyleSheet(_BTN_BLUE)
        self._refresh_btn.clicked.connect(self._reload_characters)
        character_row.addWidget(self._refresh_btn)

        self._apply_btn = QPushButton("套用角色")
        self._apply_btn.setFixedHeight(34)
        self._apply_btn.setStyleSheet(_BTN_BLUE)
        self._apply_btn.clicked.connect(self._apply_selected_character)
        character_row.addWidget(self._apply_btn)
        layout.addLayout(character_row)

        # 動作預覽
        motion_label = QLabel("🎭 動作預覽")
        motion_label.setStyleSheet("font-size: 12px; color: #333; font-weight: bold;")
        layout.addWidget(motion_label)

        motion_row = QHBoxLayout()
        self._motion_combo = QComboBox()
        for motion_spec in MOTION_SPECS:
            self._motion_combo.addItem(motion_spec["title"], motion_spec["key"])
        self._motion_combo.setStyleSheet(
            "QComboBox { border: 1px solid #ccc; border-radius: 4px; padding: 4px; font-size: 12px; }"
        )
        motion_row.addWidget(self._motion_combo, stretch=1)

        self._preview_motion_btn = QPushButton("播放一次")
        self._preview_motion_btn.setFixedHeight(34)
        self._preview_motion_btn.setStyleSheet(_BTN_BLUE)
        self._preview_motion_btn.clicked.connect(self._preview_selected_motion)
        motion_row.addWidget(self._preview_motion_btn)
        layout.addLayout(motion_row)

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

        self._update_character_controls()

    # ── 圖片選擇 ─────────────────────────────────────────

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇角色圖片", "",
            "圖片檔案 (*.png *.jpg *.jpeg *.webp);;所有檔案 (*)",
        )
        if not path:
            return

        self._image_path = path
        self._path_label.setText(path)
        if not self._name_edit.text().strip():
            self._name_edit.setText(os.path.splitext(os.path.basename(path))[0])

        self._set_preview_image(path)

        self._gen_btn.setEnabled(True)
        self._status.setText("")

    # ── 算圖觸發 ─────────────────────────────────────────

    def _start_generation(self):
        if not self._image_path:
            return

        character_name = self._name_edit.text().strip()
        positive_prompt = self._positive_edit.toPlainText().strip()
        negative_prompt = self._negative_edit.text().strip()

        manifest = self._library.create_character(self._image_path, character_name)
        character_id = manifest["id"]
        image_dir = self._library.get_source_dir_path(character_id)
        target_dir = self._library.get_motions_dir_path(character_id)
        self._pending_character_id = character_id

        self._set_generation_state(True)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._status.setText("算圖中，請稍候…")

        self._worker = GenerationWorker(
            image_dir,
            target_dir,
            character_id,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            parent=self,
        )
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, percent: int):
        self._progress.setValue(percent)

    def _on_finished(self, success: bool, message: str, character_id: str, archived):
        self._status.setText(message)
        self._set_generation_state(False)

        if success:
            self._library.register_generated_assets(
                character_id,
                archived or {},
                positive_prompt=self._positive_edit.toPlainText().strip(),
                negative_prompt=self._negative_edit.text().strip(),
            )
            self._status.setStyleSheet("font-size: 13px; color: #27ae60;")
            self._reload_characters(select_id=character_id)
            self.generation_done.emit(character_id)
        else:
            self._status.setStyleSheet("font-size: 13px; color: #c0392b;")
            self._gen_btn.setEnabled(bool(self._image_path))

        self._worker = None
        self._pending_character_id = None

    # ── 角色庫控制 ───────────────────────────────────────

    def _reload_characters(self, *_args, select_id: str | None = None):
        characters = self._library.list_characters()
        current_id = select_id or self._selected_character_id()

        self._character_combo.blockSignals(True)
        self._character_combo.clear()
        self._character_combo.addItem("選擇已生成角色…", "")

        selected_index = 0
        for index, character in enumerate(characters, start=1):
            motion_count = len(character.get("motions", {}))
            label = f"{character['name']}  ({motion_count}/6)"
            self._character_combo.addItem(label, character["id"])
            if character["id"] == current_id:
                selected_index = index

        self._character_combo.setCurrentIndex(selected_index)
        self._character_combo.blockSignals(False)
        self._on_character_changed()

    def _selected_character_id(self) -> str | None:
        value = self._character_combo.currentData()
        return value or None

    def _selected_character(self) -> dict | None:
        return self._library.get_character(self._selected_character_id())

    def _on_character_changed(self):
        character = self._selected_character()
        if character:
            preview_image = self._library.get_preview_image_path(character["id"])
            if preview_image:
                self._set_preview_image(preview_image)
            self._path_label.setText(preview_image or "已選擇角色")
            self._name_edit.setText(character.get("name", ""))
        elif self._image_path:
            self._set_preview_image(self._image_path)
            self._path_label.setText(self._image_path)
        else:
            self._preview.clear()
            self._preview.setText("預覽區")
            self._path_label.setText("尚未選擇圖片")

        self._update_character_controls()

    def _apply_selected_character(self):
        character_id = self._selected_character_id()
        if not character_id:
            self._status.setStyleSheet("font-size: 13px; color: #c0392b;")
            self._status.setText("請先選擇角色。")
            return

        self._library.set_current_character_id(character_id)
        self.apply_character_requested.emit(character_id)
        self._status.setStyleSheet("font-size: 13px; color: #27ae60;")
        self._status.setText("已套用角色 idle 動畫。")

    def _preview_selected_motion(self):
        character_id = self._selected_character_id()
        motion_key = self._motion_combo.currentData()
        if not character_id or not motion_key:
            self._status.setStyleSheet("font-size: 13px; color: #c0392b;")
            self._status.setText("請先選擇角色與動作。")
            return

        motion_path = self._library.get_motion_path(character_id, motion_key)
        if not motion_path:
            self._status.setStyleSheet("font-size: 13px; color: #c0392b;")
            self._status.setText("此角色尚未生成該動作。")
            return

        self.preview_motion_requested.emit(character_id, motion_key)
        self._status.setStyleSheet("font-size: 13px; color: #27ae60;")
        self._status.setText("已送出動作預覽，播放結束後會自動回到 idle。")

    def _update_character_controls(self):
        character = self._selected_character()
        has_character = bool(character)
        self._apply_btn.setEnabled(has_character)
        self._preview_motion_btn.setEnabled(has_character)

        motions = character.get("motions", {}) if character else {}
        for index, motion_spec in enumerate(MOTION_SPECS):
            enabled = motion_spec["key"] in motions
            self._motion_combo.model().item(index).setEnabled(enabled)

        if has_character:
            first_enabled = next(
                (i for i, motion_spec in enumerate(MOTION_SPECS) if motion_spec["key"] in motions),
                0,
            )
            self._motion_combo.setCurrentIndex(first_enabled)

    def _set_preview_image(self, path: str):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._preview.clear()
            self._preview.setText("預覽區")
            return

        scaled = pixmap.scaled(
            self._preview.width() - 4,
            self._preview.height() - 4,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)

    def _set_generation_state(self, generating: bool):
        self._gen_btn.setEnabled(not generating and bool(self._image_path))
        self._pick_btn.setEnabled(not generating)
        self._refresh_btn.setEnabled(not generating)
        self._apply_btn.setEnabled(not generating and bool(self._selected_character_id()))
        self._preview_motion_btn.setEnabled(not generating and bool(self._selected_character_id()))
