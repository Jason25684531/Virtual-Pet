from pet_harness.tools import youtube_music_tool
from pet_harness.tools.tool_models import ToolResult


def test_startup_warmup_handles_executor_completion(monkeypatch, capsys):
    import main
    from PyQt5.QtCore import QTimer

    class FakeExecutor:
        def submit(self, job, done):
            done(True, "", ToolResult("youtube_music_tool", "success"))

    monkeypatch.setattr(QTimer, "singleShot", lambda _delay, callback: callback())
    main._schedule_youtube_warmup(FakeExecutor())
    assert "[YOUTUBE WARMUP] success" in capsys.readouterr().out


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
