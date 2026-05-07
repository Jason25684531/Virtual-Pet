from __future__ import annotations

from pathlib import Path
import unittest


class FrontendResetBridgeTests(unittest.TestCase):
    def test_frontend_exposes_conversation_and_room_reset_bridges(self):
        app_js = Path(__file__).resolve().parents[1] / "ui" / "web_container" / "app.js"
        source = app_js.read_text(encoding="utf-8")

        self.assertIn("window.clearConversationTurns", source)
        self.assertIn("window.resetRoomState", source)
        self.assertIn("window.setPanelVideoMuted", source)
        self.assertIn("[ECHOES:ROOM_AUDIO_ENDED]", source)
        self.assertIn("window.stopRoomAudio();", source)
        self.assertIn("window.clearPanelVideo();", source)
        self.assertIn("window.stopMotionLoop();", source)


if __name__ == "__main__":
    unittest.main()
