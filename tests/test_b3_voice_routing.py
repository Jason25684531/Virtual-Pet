"""
B-3：角色聲線端對端驗證。

涵蓋：
- Choppr manifest 包含 voice_id_env_key = "CHOPPER_VOICE_ID"
- BrainProfile.from_character_library() 對 Choppr 解析出 CHOPPER_VOICE_ID
- config.get_voice_id_for_character("Choppr") 回傳正確聲線
- config.get_voice_id_for_character(None/unknown) fallback 至全域預設
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from api_client.brain_engine import BrainProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHOPPR_MANIFEST_PATH = PROJECT_ROOT / "assets/webm/characters/Choppr/manifest.json"
FAKE_CHOPPER_VOICE = "FAKE_CHOPPER_VOICE_ID_FOR_TEST"
FAKE_MIKU_VOICE = "FAKE_MIKU_VOICE_ID_FOR_TEST"


class _FakeLibrary:
    """模擬 CharacterLibrary，回傳指定角色的 manifest。"""

    def __init__(self, character_id: str, manifest: dict):
        self._character_id = character_id
        self._manifest = manifest

    def get_current_character_id(self) -> str:
        return self._character_id

    def get_character(self, _character_id) -> dict:
        return self._manifest


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ChopprManifestTests(unittest.TestCase):
    def test_choppr_manifest_has_voice_id_env_key(self):
        """Choppr manifest.json 應包含 voice_id_env_key 欄位。"""
        manifest = json.loads(CHOPPR_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIn("voice_id_env_key", manifest)
        self.assertEqual(manifest["voice_id_env_key"], "CHOPPER_VOICE_ID")


class BrainProfileVoiceResolutionTests(unittest.TestCase):
    def _choppr_manifest(self) -> dict:
        return json.loads(CHOPPR_MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_choppr_profile_resolves_chopper_voice_id(self):
        """BrainProfile.from_character_library() 應從 CHOPPER_VOICE_ID 解析出 Choppr 聲線。"""
        library = _FakeLibrary("Choppr", self._choppr_manifest())
        with patch.dict(os.environ, {"CHOPPER_VOICE_ID": FAKE_CHOPPER_VOICE}):
            profile = BrainProfile.from_character_library(library)
        self.assertEqual(profile.voice_id, FAKE_CHOPPER_VOICE)
        self.assertEqual(profile.character_id, "Choppr")

    def test_choppr_profile_falls_back_when_env_unset(self):
        """CHOPPER_VOICE_ID 未設定時應 fallback 至全域預設。"""
        library = _FakeLibrary("Choppr", self._choppr_manifest())
        env_without_chopper = {k: v for k, v in os.environ.items() if k != "CHOPPER_VOICE_ID"}
        with patch.dict(os.environ, env_without_chopper, clear=True):
            profile = BrainProfile.from_character_library(library)
        # fallback = config.get_voice_id_for_character("Choppr") = config.ELEVENLABS_VOICE_ID
        self.assertIsNotNone(profile.voice_id)
        self.assertNotEqual(profile.voice_id, "")

    def test_manifest_voice_id_takes_priority_over_env_key(self):
        """manifest 直接設定 voice_id 應優先於 voice_id_env_key。"""
        manifest = {**self._choppr_manifest(), "voice_id": "DIRECT_VOICE_ID_IN_MANIFEST"}
        library = _FakeLibrary("Choppr", manifest)
        with patch.dict(os.environ, {"CHOPPER_VOICE_ID": FAKE_CHOPPER_VOICE}):
            profile = BrainProfile.from_character_library(library)
        self.assertEqual(profile.voice_id, "DIRECT_VOICE_ID_IN_MANIFEST")


class ConfigVoiceRoutingTests(unittest.TestCase):
    def test_get_voice_id_for_choppr_uses_chopper_env(self):
        """config.get_voice_id_for_character('Choppr') 應讀取 CHOPPER_VOICE_ID。"""
        with patch.dict(
            os.environ,
            {
                "CHOPPER_VOICE_ID": FAKE_CHOPPER_VOICE,
                "ELEVENLABS_CHOPPR_VOICE_ID": "",
                "ELEVENLABS_CHOPPER_VOICE_ID": "",
            },
        ):
            import importlib
            importlib.reload(config)
            result = config.get_voice_id_for_character("Choppr")
        self.assertEqual(result, FAKE_CHOPPER_VOICE)

    def test_get_elevenlabs_voice_id_for_choppr_prefers_provider_specific_env(self):
        """provider-aware fallback 應優先使用 ELEVENLABS_CHOPPR_VOICE_ID。"""
        with patch.dict(
            os.environ,
            {
                "ELEVENLABS_CHOPPR_VOICE_ID": "FAKE_PROVIDER_SPECIFIC_CHOPPR",
                "CHOPPER_VOICE_ID": FAKE_CHOPPER_VOICE,
            },
        ):
            import importlib
            importlib.reload(config)
            result = config.get_elevenlabs_voice_id_for_character("Choppr")
        self.assertEqual(result, "FAKE_PROVIDER_SPECIFIC_CHOPPR")

    def test_get_voice_id_for_unknown_character_falls_back(self):
        """未知 character_id 應 fallback 至全域 ELEVENLABS_VOICE_ID。"""
        result = config.get_voice_id_for_character("unknown_char_xyz")
        self.assertEqual(result, config.ELEVENLABS_VOICE_ID)

    def test_get_voice_id_for_none_falls_back(self):
        """None character_id 應 fallback 至全域 ELEVENLABS_VOICE_ID。"""
        result = config.get_voice_id_for_character(None)
        self.assertEqual(result, config.ELEVENLABS_VOICE_ID)

    def test_read_news_alias_canonicalizes_to_report_news(self):
        """read_news alias 應保持對齊既有 report_news runtime token。"""
        self.assertEqual(config.canonicalize_host_action("read_news"), "report_news")


if __name__ == "__main__":
    unittest.main()
