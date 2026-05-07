from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtWidgets import QApplication, QScrollArea

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.settings_dialog import DEFAULT_NEGATIVE_PROMPT, DEFAULT_POSITIVE_PROMPT, SettingsDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing_app = QCoreApplication.instance()
        if existing_app is not None and not isinstance(existing_app, QApplication):
            raise unittest.SkipTest("QCoreApplication 已存在，無法在同一 process 建立 QApplication")
        cls._app = QApplication.instance() or QApplication([])

    def test_settings_dialog_uses_scrollable_layout_and_prefilled_prompt_examples(self):
        dialog = SettingsDialog()
        try:
            self.assertTrue(dialog.findChildren(QScrollArea))
            self.assertEqual(dialog._positive_edit.toPlainText(), DEFAULT_POSITIVE_PROMPT)
            self.assertEqual(dialog._negative_edit.text(), DEFAULT_NEGATIVE_PROMPT)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
