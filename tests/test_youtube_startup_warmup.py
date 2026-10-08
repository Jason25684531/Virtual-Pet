from pet_harness.tools import youtube_music_tool
from pet_harness.tools.tool_models import ToolResult


def test_warmup_closes_runtime_after_playback(monkeypatch):
    calls = []

    class FakeTool:
        def execute(self, request):
            calls.append(request)
            return ToolResult("youtube_music_tool", "success")

    monkeypatch.setattr(youtube_music_tool, "YouTubeMusicTool", FakeTool)
    monkeypatch.setattr(youtube_music_tool, "shutdown_default_runtime", lambda: calls.append("closed"))

    result = youtube_music_tool.warmup_once("test music")

    assert result.status == "success"
    assert calls[0].arguments == {"action": "search_and_play", "query": "test music"}
    assert calls[-1] == "closed"
