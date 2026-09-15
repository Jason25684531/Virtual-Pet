"""media-skill-routing:新聞/音樂意圖互斥、否定、衝突與六角色一致性。

這裡只測決定性路由層(不連外部服務),六個角色各自以自己的 profile 解析出的技能
清單建 router,而不是共用一個 fixture —— 規格要求每個角色分別記錄結果。
"""

from __future__ import annotations

from functools import lru_cache

import pytest

from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.engine.media_session_context import MediaSessionContext
from pet_harness.engine.tool_execution_lifecycle import ToolExecutionLifecycle
from pet_harness.models.skill import Skill
from pet_harness.skills.intent_normalizer import normalize
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter, resolve_media_intent
from pet_harness.tools.tool_models import ToolRequest, ToolResult

# 讀的是產品實際的 .agentic/skills 與六個角色 manifest,不是複製出來的 fixture。
pytestmark = pytest.mark.uses_repo_cwd

DEFAULT_CHARACTER_IDS = ("char-Adol", "char-Jack", "char-Kai", "char-Luke", "char-Nico", "char-ROG")


@lru_cache(maxsize=1)
def _production_skills() -> tuple[Skill, ...]:
    return tuple(SkillLoader(".agentic/skills").load_skills())


@lru_cache(maxsize=None)
def _skill_names_for(character_id: str) -> tuple[str, ...]:
    profile, _ = CharacterRouter(registry=CharacterRegistry()).load_profile(character_id)
    return tuple(profile.allowed_skill_refs)


def _production_skills_by_name() -> dict[str, Skill]:
    return {skill.name: skill for skill in _production_skills()}


def _router_for(character_id: str) -> SkillRouter:
    available = {skill.name: skill for skill in _production_skills()}
    return SkillRouter([available[name] for name in _skill_names_for(character_id) if name in available])


# --------------------------------------------------------------------------
# 1.1 / 1.2 意圖判定
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,capability,reason",
    [
        ("新聞", "news", "matched"),
        ("遊戲新聞", "news", "matched"),
        ("播報新聞", "news", "matched"),
        ("播放遊戲新聞", "news", "matched"),          # 播放動詞不得把新聞搶給音樂
        ("請幫我播放新聞", "news", "matched"),
        ("play music", "music", "missing_music_query"),
        ("播放音樂", "music", "missing_music_query"),
        ("我想聽歌曲", "music", "missing_music_query"),
        ("我想聽稻香", "music", "matched"),
        ("播放周杰倫的晴天", "music", "matched"),
        ("不要播放音樂", None, "negated"),
        ("不要播報新聞", None, "negated"),
        ("別播音樂了", None, "negated"),
        ("想聽特別的歌", "music", "matched"),          # 「特別」不是否定
        ("我想聽類別裡的第一首", "music", "matched"),   # 「類別」不是否定
        ("我喜歡遊戲", None, "none"),                  # 一般聊天不得啟動新聞
        ("我喜歡音樂", None, "none"),                  # 提到音樂但沒有動作也不播
        ("幫我播放新聞和音樂", None, "conflict"),
        ("播放影片", None, "none"),
    ],
)
def test_media_intent_is_deterministic(text, capability, reason):
    intent = resolve_media_intent(normalize(text).stripped_text)
    assert (intent.capability, intent.reason) == (capability, reason)


@pytest.mark.parametrize("character_id", DEFAULT_CHARACTER_IDS)
@pytest.mark.parametrize(
    "text,expected_skill",
    [
        ("新聞", "bahamut_daily_news"),
        ("幫我念最新消息", "bahamut_daily_news"),
        ("我想看新聞播報", "bahamut_daily_news"),
        ("遊戲新聞", "bahamut_daily_news"),
        ("播報新聞", "bahamut_daily_news"),
        ("播放遊戲新聞", "bahamut_daily_news"),
        ("我想聽稻香", "youtube_music_playback"),
        ("幫我放稻香", "youtube_music_playback"),
        ("來點周杰倫的歌", "youtube_music_playback"),
        ("播放周杰倫的晴天", "youtube_music_playback"),
        ("不要播放音樂", None),
        ("不要播報新聞", None),
        ("我喜歡遊戲", None),
        ("播放音樂", None),                             # 缺歌名 → 澄清,不帶空 query 呼叫工具
        ("幫我播放新聞和音樂", None),                    # 沒有順序 → 澄清
        ("暫停", None),                                 # 沒有有效工作階段
    ],
)
def test_six_character_routing_matrix(character_id, text, expected_skill):
    """1.4:六個角色分別跑同一份矩陣,不以共用 fixture 或另一角色的結果代替。"""
    matched = _router_for(character_id).match(text)
    assert getattr(matched, "name", None) == expected_skill


@pytest.mark.parametrize("character_id", DEFAULT_CHARACTER_IDS)
def test_music_follow_up_needs_an_active_session_for_every_character(character_id):
    router = _router_for(character_id)
    assert router.match("暫停") is None
    assert router.match("暫停", {"music"}).name == "youtube_music_playback"
    assert router.match("停止播放", {"music"}).name == "youtube_music_playback"


@pytest.mark.parametrize(
    "text,action",
    [
        ("暫停", "pause"), ("暫停音樂", "pause"),
        ("繼續播放", "resume"), ("停止播放", "stop"), ("停止音樂", "stop"),
        ("現在在播放什麼", "get_status"),
    ],
)
def test_playback_control_phrases_control_the_session_instead_of_searching_for_them(text, action):
    """1.3:有效追問要控制該工作階段,不得把「暫停音樂」當成歌名拿去搜尋。"""
    from pet_harness.engine.harness_engine import PetHarnessEngine

    music = _production_skills_by_name()["youtube_music_playback"]
    assert PetHarnessEngine._media_arguments(music, text) == {"action": action, "query": ""}


def test_song_requests_are_still_searched():
    from pet_harness.engine.harness_engine import PetHarnessEngine

    music = _production_skills_by_name()["youtube_music_playback"]
    assert PetHarnessEngine._media_arguments(music, "播放周杰倫的晴天") == {
        "action": "search_and_play", "query": "周杰倫的晴天",
    }


def test_blocked_media_intent_is_not_rescued_by_provider_fallback():
    """否定的回合,provider 建議的高信心技能也不得繞過 deterministic 判定。"""
    router = SkillRouter(list(_production_skills()))
    skill, source = router.route(
        "不要播放音樂",
        suggested_skill_name="youtube_music_playback",
        suggested_confidence=0.99,
        allow_fallback=True,
    )
    assert (skill, source) == (None, "none")
    assert router.last_route_diagnostics["rejection_reason"] == "negated"


def test_route_diagnostics_expose_the_clarification_reason():
    router = SkillRouter(list(_production_skills()))
    router.route("幫我播放新聞和音樂")
    assert router.last_route_diagnostics["media_reason"] == "conflict"


# --------------------------------------------------------------------------
# 1.3 / 1.5 工作階段隔離與工具證據
# --------------------------------------------------------------------------

class _FakeStore:
    def __init__(self) -> None:
        self.settings: dict = {}
        self.logged: list = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def log_tool_result(self, result, request):
        self.logged.append((result, request))


class _StubRegistry:
    def __init__(self, result: ToolResult) -> None:
        self._result = result

    def get(self, _name):
        return None

    def execute(self, _request):
        return self._result


class _AllowAll:
    def evaluate(self, _request, _policy):
        return type("Safety", (), {"allowed": True, "reason": "", "metadata": {}})()


def _run_music(status: str) -> _FakeStore:
    store = _FakeStore()
    result = ToolResult("youtube_music_tool", status, payload={"current_track": {"title": "稻香"}})
    lifecycle = ToolExecutionLifecycle(_AllowAll(), _StubRegistry(result), store)
    lifecycle.run(ToolRequest("youtube_music_tool", "youtube_music_playback", {"action": "search_and_play", "query": "稻香"}))
    return store


def test_unverified_playback_does_not_create_a_controllable_session():
    """1.5:播放未驗證(partial)時不得留下 playback 上下文,否則之後的「暫停」會誤以為有得控。"""
    assert MediaSessionContext(_run_music("partial")).load().get("playback") is None
    assert MediaSessionContext(_run_music("failed")).load().get("playback") is None
    assert MediaSessionContext(_run_music("success")).load().get("playback") is not None


def test_playback_session_does_not_survive_a_restart():
    """1.3:瀏覽器分頁活不過行程結束,換一個 runtime id 就不得再控制舊工作階段。"""
    import pet_harness.engine.media_session_context as module

    store = _run_music("success")
    original = module.RUNTIME_ID
    try:
        module.RUNTIME_ID = "another-run"
        assert MediaSessionContext(store).load().get("playback") is None
        # articles 只是清單快取,重啟後仍可用來追問第幾則
        MediaSessionContext(store).save(articles=[{"title": "a"}])
        assert MediaSessionContext(store).load()["articles"] == [{"title": "a"}]
    finally:
        module.RUNTIME_ID = original


def test_playback_session_is_per_character_store():
    """1.3:每個角色有自己的 state.db,切換角色後不得看到上一角色的工作階段。"""
    adol, jack = _run_music("success"), _FakeStore()
    assert MediaSessionContext(adol).load().get("playback") is not None
    assert MediaSessionContext(jack).load().get("playback") is None
