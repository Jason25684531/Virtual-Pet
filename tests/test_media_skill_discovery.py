from __future__ import annotations

import json
from pathlib import Path
from threading import Event

import pet_harness.character.profile as profile_module
from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.memory.memory_models import RetrievalResult, RetrievalTrace
from pet_harness.models.provider import ProviderStatus, ProviderType
from pet_harness.models.skill import Skill
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.tool_models import ToolResult
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter


class ConflictProvider:
    def generate_reply(self, event, matched_skill=None, prompt_text=None):
        return ProviderReply(
            reply="ok",
            raw_text=json.dumps({"reply": "ok", "tool_request": {"tool_name": "music_search_tool", "arguments": {"query": "wrong"}}}),
            provider_status=ProviderStatus(provider_type=ProviderType.API, healthy=True, message="test"),
        )


class RecordingProvider:
    """記錄每次 generate_reply 收到的 prompt_text,用來驗證工具先行順序。"""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def generate_reply(self, event, matched_skill=None, prompt_text=None):
        self.calls.append(prompt_text)
        return ProviderReply(
            reply="今日新聞整理完畢",
            raw_text=json.dumps({"reply": "今日新聞整理完畢"}),
            provider_status=ProviderStatus(provider_type=ProviderType.API, healthy=True, message="test"),
        )


def _write_workspace(root: Path, monkeypatch) -> Path:
    monkeypatch.chdir(root)
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", root)
    character = root / "data" / "characters" / "Miku"
    character.mkdir(parents=True)
    (character / "profile.json").write_text(json.dumps({"persona_description": "test", "skill_config": ["music_bgm", "game_news"]}), encoding="utf-8")
    assets = root / "assets" / "webm" / "characters" / "Miku"
    assets.mkdir(parents=True)
    (assets / "manifest.json").write_text(json.dumps({"id": "Miku", "name": "Miku", "background_image": "", "motions_dir": "", "motions": {}, "idle_pool": [], "layout": {}}), encoding="utf-8")
    skills = root / ".agentic" / "skills"
    skills.mkdir(parents=True)
    files = {
        "music_bgm": "name: music_bgm\ndescription: old\ntrigger: music\nbehavior: music_idle\nxp_reward: 1\nrequired_tool: music_search\n",
        "game_news": "name: game_news\ndescription: old\ntrigger: news\nbehavior: news_idle\nxp_reward: 1\nrequired_tool: rss_news\n",
        "youtube_music_playback": "name: youtube_music_playback\ndescription: music\ntrigger: 播歌\nbehavior: music_idle\nxp_reward: 8\nrequired_tool: youtube_music_tool\ntool_policy_json: {\"allowed_domains\":[\"www.youtube.com\"],\"allowed_actions\":[\"search_and_play\",\"pause\",\"resume\",\"stop\",\"get_status\"],\"priority\":100}\n",
        "bahamut_daily_news": "name: bahamut_daily_news\ndescription: news\ntrigger: 巴哈新聞\nbehavior: news_idle\nxp_reward: 7\nrequired_tool: web_article_tool\npriority: 100\ncapability: news\ntool_policy_json: {\"allowed_domains\":[\"gnn.gamer.com.tw\"],\"allowed_actions\":[\"list_articles\",\"get_article_detail\",\"open_article\"],\"defaults\":{\"action\":\"list_articles\",\"limit\":5,\"url\":\"https://gnn.gamer.com.tw/rss.xml\"}}\n",
    }
    for name, content in files.items():
        (skills / f"{name}.md").write_text(content, encoding="utf-8")
    return root / ".agentic"


def test_loader_normalizes_policy_priority_and_capability(tmp_path, monkeypatch):
    agentic = _write_workspace(tmp_path, monkeypatch)
    loaded = {skill.name: skill for skill in SkillLoader(agentic / "skills").load_skills()}
    assert loaded["youtube_music_playback"].priority == 100
    assert loaded["youtube_music_playback"].capability == "music"
    assert loaded["bahamut_daily_news"].priority == 100
    assert loaded["bahamut_daily_news"].capability == "news"


def test_music_intent_is_deterministic_but_video_is_not():
    music = Skill("youtube_music_playback", "music", ["播歌"], "music_idle", 8, capability="music", priority=100)
    router = SkillRouter([music])
    assert router.match("播放周杰倫的晴天").name == "youtube_music_playback"
    assert router.match("播放影片") is None


def test_normalized_skill_priority_breaks_trigger_tie():
    low = Skill("low", "low", ["go"], "idle", 1, priority=1)
    high = Skill("high", "high", ["go"], "idle", 1, priority=2)
    assert SkillRouter([low, high]).match("go").name == "high"


def test_migration_discovery_and_conflicting_agent_tool(tmp_path, monkeypatch):
    agentic = _write_workspace(tmp_path, monkeypatch)
    engine = PetHarnessEngine(ConflictProvider(), agentic_root=agentic, character_id="Miku")
    assert {skill.name for skill in engine.discoverable_skills()} == {"music_bgm", "game_news", "youtube_music_playback", "bahamut_daily_news"}
    profile = json.loads((tmp_path / "data/characters/Miku/profile.json").read_text(encoding="utf-8"))
    assert "youtube_music_playback" in profile["skill_config"]
    registry = ToolRegistry()
    definition = registry.get("youtube_music_tool")
    registry.register_definition(definition, lambda request: ToolResult("youtube_music_tool", "success", payload={"current_track": {"title": "晴天"}}, request_id=request.request_id))
    engine.refresh_tool_registry(registry)
    event = engine.handle_event({"text": "播放周杰倫的晴天", "source": "test"})
    assert event.matched_skill == "youtube_music_playback"
    assert event.tool_request.tool_name == "youtube_music_tool"
    assert event.tool_request.source_skill == "youtube_music_playback"
    assert event.tool_request.metadata["arguments"]["query"] == "周杰倫的晴天"
    assert event.metadata["tool_result"]["tool_name"] == "youtube_music_tool"


def test_required_tool_skill_executes_tool_before_llm_call(tmp_path, monkeypatch):
    """deterministic 命中帶 required_tool 的 skill 時,工具 MUST 先執行,LLM 只呼叫一次,
    且 prompt 必須包含本輪真實工具結果(而非事後才附加)(fix-core-interaction-experience)。"""
    agentic = _write_workspace(tmp_path, monkeypatch)
    provider = RecordingProvider()
    engine = PetHarnessEngine(provider, agentic_root=agentic, character_id="Miku")
    registry = ToolRegistry()
    definition = registry.get("web_article_tool")
    registry.register_definition(
        definition,
        lambda request: ToolResult(
            "web_article_tool", "success",
            payload={"articles": [{"title": "今日新聞頭條", "summary": "重點摘要"}]},
            request_id=request.request_id,
        ),
    )
    engine.refresh_tool_registry(registry)

    event = engine.handle_event({"text": "跟我說今天的巴哈新聞", "source": "test"})

    assert len(provider.calls) == 1
    assert "今日新聞頭條" in (provider.calls[0] or "")
    assert event.tool_request.tool_name == "web_article_tool"
    assert event.metadata["tool_result"]["payload"]["articles"][0]["title"] == "今日新聞頭條"


def test_tool_first_and_retrieval_run_in_parallel(tmp_path, monkeypatch):
    agentic = _write_workspace(tmp_path, monkeypatch)
    tool_started = Event()
    retrieval_started = Event()

    class WaitingRetriever:
        def retrieve(self, _request):
            retrieval_started.set()
            assert tool_started.wait(1)
            return RetrievalResult([], RetrievalTrace.empty("news"))

    engine = PetHarnessEngine(
        RecordingProvider(),
        agentic_root=agentic,
        character_id="Miku",
        memory_retriever=WaitingRetriever(),
    )

    def run_tool(_event, _skill):
        tool_started.set()
        assert retrieval_started.wait(1)
        return None, None

    engine._run_tool_first = run_tool
    engine.handle_event({"text": "news", "source": "test"})


def test_adapter_returns_and_persists_normalized_discovery(tmp_path, monkeypatch):
    agentic = _write_workspace(tmp_path, monkeypatch)
    adapter = PyQtHarnessAdapter(default_character_id="Miku", agentic_root=agentic, provider_runtime=ProviderRuntime(provider=ConflictProvider()))
    items = {item["name"]: item for item in adapter.list_skills()}
    assert items["youtube_music_playback"]["required_tool"] == "youtube_music_tool"
    assert items["youtube_music_playback"]["priority"] == 100
    assert items["youtube_music_playback"]["enabled"] is True
    registry = ToolRegistry()
    definition = registry.get("youtube_music_tool")
    registry.register_definition(definition, lambda request: ToolResult("youtube_music_tool", "success", payload={"current_track": {"title": "晴天"}}, request_id=request.request_id))
    adapter._build_registry = lambda *_args: registry
    payload = adapter.handle_text_input("播放周杰倫的晴天")
    assert payload["matched_skill"] == "youtube_music_playback"
    assert payload["tool"]["name"] == "youtube_music_tool"
    adapter.set_skill_enabled("youtube_music_playback", False)
    assert {item["name"]: item for item in adapter.list_skills()}["youtube_music_playback"]["enabled"] is False
    restarted = PyQtHarnessAdapter(default_character_id="Miku", agentic_root=agentic, provider_runtime=ProviderRuntime(provider=ConflictProvider()))
    assert {item["name"]: item for item in restarted.list_skills()}["youtube_music_playback"]["enabled"] is False
