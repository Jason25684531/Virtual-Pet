"""
SQLiteMemoryManager 單元測試。

驗證：
- add_exchange 後可取回訊息
- 超過 MAX_MESSAGES_PER_SESSION 後自動修剪
- 不同 character_id 的記憶完全隔離
- get_recent_messages 的 limit 參數正確截取
- clear_session 清空單一 session
"""

from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class SQLiteMemoryManagerTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="echoes-db-test-")
        db_path = Path(self._tmpdir.name) / "test_memory.db"
        from database import SQLiteMemoryManager
        self.mgr = SQLiteMemoryManager(db_path=db_path, max_messages=10)

    def tearDown(self):
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------

    def test_add_and_retrieve_exchange(self):
        """add_exchange 後應能透過 get_recent_messages 取回訊息。"""
        self.mgr.add_exchange("miku", "你好", "你好！有什麼可以幫你的嗎？")

        msgs = self.mgr.get_recent_messages("miku")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].content, "你好")
        self.assertEqual(msgs[1].content, "你好！有什麼可以幫你的嗎？")

    def test_session_isolation(self):
        """不同 character_id 的記憶應完全隔離。"""
        self.mgr.add_exchange("miku", "問題A", "回答A")
        self.mgr.add_exchange("Choppr", "問題B", "回答B")

        miku_msgs = self.mgr.get_recent_messages("miku")
        choppr_msgs = self.mgr.get_recent_messages("Choppr")

        self.assertEqual(len(miku_msgs), 2)
        self.assertEqual(len(choppr_msgs), 2)
        self.assertEqual(miku_msgs[0].content, "問題A")
        self.assertEqual(choppr_msgs[0].content, "問題B")

    def test_prune_on_exceed(self):
        """超過 MAX_MESSAGES_PER_SESSION（10）後應自動修剪，保留最新的。"""
        # 新增 6 輪（12 筆），超過 max_messages=10
        for i in range(6):
            self.mgr.add_exchange("miku", f"問題{i}", f"回答{i}")

        msgs = self.mgr.get_recent_messages("miku", limit=100)
        self.assertLessEqual(len(msgs), 10)
        # 最後一筆應是最新的 AI 回覆
        self.assertEqual(msgs[-1].content, "回答5")

    def test_limit_parameter(self):
        """get_recent_messages(limit=N) 應只回傳最後 N 筆。"""
        for i in range(4):
            self.mgr.add_exchange("miku", f"q{i}", f"a{i}")  # 8 筆

        msgs = self.mgr.get_recent_messages("miku", limit=4)
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[-1].content, "a3")

    def test_clear_session_only_affects_target(self):
        """clear_session('miku') 不應影響其他角色的記憶。"""
        self.mgr.add_exchange("miku", "你好", "哈囉")
        self.mgr.add_exchange("Choppr", "嗨", "嗨嗨")

        self.mgr.clear_session("miku")

        self.assertEqual(self.mgr.get_recent_messages("miku"), [])
        self.assertEqual(len(self.mgr.get_recent_messages("Choppr")), 2)

    def test_clear_all_sessions_removes_every_character_memory(self):
        self.mgr.add_exchange("miku", "你好", "哈囉")
        self.mgr.add_exchange("Choppr", "嗨", "嗨嗨")

        self.mgr.clear_all_sessions()

        self.assertEqual(self.mgr.get_recent_messages("miku"), [])
        self.assertEqual(self.mgr.get_recent_messages("Choppr"), [])

    def test_empty_or_none_text_is_ignored(self):
        """空字串或 None 的 human/ai 不應寫入 DB。"""
        self.mgr.add_exchange("miku", "", "有回覆但無問題")
        self.mgr.add_exchange("miku", "有問題但無回覆", "")
        self.mgr.add_exchange("miku", None, None)  # type: ignore[arg-type]

        self.assertEqual(self.mgr.get_recent_messages("miku"), [])

    def test_character_id_normalization(self):
        """包含空白與大寫的 character_id 應正規化後共用同一 session。"""
        self.mgr.add_exchange("Miku", "q1", "a1")
        self.mgr.add_exchange("miku", "q2", "a2")

        # 兩個 id 正規化後都是 "miku"，應在同一 session
        msgs = self.mgr.get_recent_messages("miku")
        self.assertEqual(len(msgs), 4)

    def test_none_character_id_uses_default(self):
        """None character_id 應使用 'default' session，不應崩潰。"""
        self.mgr.add_exchange(None, "測試", "回覆")
        msgs = self.mgr.get_recent_messages(None)
        self.assertEqual(len(msgs), 2)


if __name__ == "__main__":
    unittest.main()
