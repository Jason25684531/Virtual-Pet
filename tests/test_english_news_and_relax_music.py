"""英文新聞走 Polygon、新聞列表呈現、放鬆類音樂固定網址(add-english-news-and-relax-music)。"""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.engine.harness_engine import PetHarnessEngine, _SentenceSplitter
from pet_harness.models.events import UserEvent
from pet_harness.runtime.playwright_browser_runtime import _fixed_url_for
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.tools.article_fetchers import Article, BaseArticleFetcher, RssArticleFetcher
from pet_harness.tools.tool_models import ToolRequest, ToolResult
from pet_harness.tools.web_article_tool import WebArticleTool

SKILLS_DIR = Path(__file__).resolve().parents[1] / ".agentic" / "skills"
GNN = "https://gnn.gamer.com.tw/rss.xml"
POLYGON = "https://www.polygon.com/rss/index.xml"


@pytest.fixture(scope="module")
def news_skill():
    return next(s for s in SkillLoader(SKILLS_DIR).load_skills() if s.capability == "news")


def _candidate_url(skill, text):
    engine = SimpleNamespace(
        media_session_context=SimpleNamespace(follow_up_index=lambda _t: None, load=lambda: {}),
        skills=[skill],
        _media_arguments=PetHarnessEngine._media_arguments,
    )
    request = PetHarnessEngine._build_tool_request_candidate(engine, UserEvent(text=text), skill)
    return request.arguments["url"]


# --- 路由與來源選擇 ---------------------------------------------------------

@pytest.mark.parametrize("text", ["Tell me the latest gaming news", "any game news today?", "video game news please"])
def test_english_phrases_route_to_news_skill(news_skill, text):
    assert SkillRouter([news_skill]).match(text).name == news_skill.name


def test_english_uses_polygon_and_chinese_keeps_gnn(news_skill):
    assert _candidate_url(news_skill, "any game news today?") == POLYGON
    assert _candidate_url(news_skill, "今天有什麼遊戲新聞") == GNN


def test_polygon_is_whitelisted_by_skill_policy(news_skill):
    assert "www.polygon.com" in news_skill.tool_policy["allowed_domains"]


# --- 工具:來源、快取、fetcher 鏈 -------------------------------------------

class RecordingFetcher(BaseArticleFetcher):
    def __init__(self, gnn_only=False):
        self.urls, self.gnn_only = [], gnn_only

    def supports(self, url):
        return not self.gnn_only or "gnn.gamer.com.tw" in url

    def fetch(self, source, clock):
        self.urls.append(source["url"])
        return [Article(f"{source['url']}#{i}", f"t{i}", f"https://x/{i}", datetime(2026, 10, 5, 12 - i, tzinfo=timezone.utc), "c", "s") for i in range(7)]


def _list(tool, url, **extra):
    return tool.execute(ToolRequest("web_article_tool", "t", {"action": "list_articles", "limit": 5, "url": url, **extra}, metadata={"character_id": "a"}))


def test_chinese_and_english_caches_do_not_mix():
    fetcher = RecordingFetcher()
    tool = WebArticleTool([fetcher])
    _list(tool, GNN)
    _list(tool, POLYGON)
    _list(tool, POLYGON)
    assert fetcher.urls == [GNN, POLYGON]  # 第三次命中 polygon 自己的快取


def test_polygon_returns_latest_five_and_skips_gnn_only_fetchers():
    gnn_only, generic = RecordingFetcher(gnn_only=True), RecordingFetcher()
    result = _list(WebArticleTool([gnn_only, generic]), POLYGON)
    assert result.status == "success" and len(result.payload["articles"]) == 5
    assert gnn_only.urls == []


def test_unknown_feed_host_is_rejected():
    result = _list(WebArticleTool([RecordingFetcher()]), "https://evil.example.com/rss.xml")
    assert result.status == "failed" and result.error["reason"] == "invalid_arguments"


def test_polygon_failure_is_honest_not_gnn_fallback():
    class Boom(RecordingFetcher):
        def fetch(self, source, clock):
            raise RuntimeError("down")

    result = _list(WebArticleTool([Boom()]), POLYGON)
    assert result.status == "failed" and result.error["reason"] == "all_sources_failed"


def test_rss_fetcher_parses_rfc822_and_atom_and_strips_html(monkeypatch):
    rss = ('<rss version="2.0"><channel><item><title>A</title><link>https://p/a</link>'
           "<pubDate>Mon, 05 Oct 2026 03:00:17 GMT</pubDate><description>&lt;p&gt;hi &lt;b&gt;there&lt;/b&gt;&lt;/p&gt;</description></item></channel></rss>")
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title><link href="https://p/b"/><id>b</id>'
            "<updated>2026-10-05T03:00:17Z</updated><summary>plain</summary></entry></feed>")
    bodies = iter([rss, atom])
    monkeypatch.setattr("requests.get", lambda *a, **k: SimpleNamespace(text=next(bodies), raise_for_status=lambda: None))
    first = RssArticleFetcher().fetch({"url": POLYGON}, None)[0]
    second = RssArticleFetcher().fetch({"url": POLYGON}, None)[0]
    assert first.summary == "hi there" and first.published_at.tzinfo is not None
    assert second.title == "B" and second.published_at == datetime(2026, 10, 5, 3, 0, 17, tzinfo=timezone.utc)


# --- 列表呈現 ---------------------------------------------------------------

def test_numbered_items_stay_whole_sentences():
    splitter, out = _SentenceSplitter(), []
    for char in "1. Zelda gets a set.\n2. Switch hidden gems.\n":
        out += splitter.feed(char)
    out += splitter.flush()
    assert out == ["1. Zelda gets a set.", "2. Switch hidden gems."]


def _prompt(text, with_articles):
    result = ToolResult("web_article_tool", "success", payload={"articles": [{"title": "A", "summary": "s"}, {"title": "B", "summary": "t"}]}) if with_articles else None
    return PromptBuilder(Path(".")).build(UserEvent(text=text), [], {}, tool_result=result).prompt


def test_news_prompt_asks_for_one_line_per_article_in_english():
    prompt = _prompt("any game news today?", True)
    assert "on its own line" in prompt
    assert prompt.rstrip().endswith("one short sentence per article.")


def test_non_news_english_prompt_is_unchanged():
    prompt = _prompt("hello there", False)
    assert "on its own line" not in prompt
    assert prompt.rstrip().endswith("in one or two short sentences.")


# --- 放鬆音樂:語句 -> 路由 -> query -> 固定網址,整條走完(2026-10-05 實機 log 暴露的缺口) ---

FIXED = "https://www.youtube.com/watch?v=Ib2osVLmjSU&t=8s"


@pytest.fixture(scope="module")
def music_router():
    skills = SkillLoader(SKILLS_DIR).load_skills()
    return SkillRouter(skills), next(s for s in skills if s.capability == "music")


def _resolve(music_router, text):
    """回傳 (是否路由到音樂, 最終開啟的固定網址或 None);和實機走同一條路徑。"""
    router, music = music_router
    matched = router.match(text)
    if matched is None or matched.capability != "music":
        return False, None
    return True, _fixed_url_for(PetHarnessEngine._media_arguments(music, text)["query"])


@pytest.mark.parametrize("text", [
    "播放輕鬆的音樂", "放鬆音樂", "放一首放鬆的音樂", "來點輕鬆的音樂", "我想聽放鬆的音樂",
    "給我一首放鬆的音樂", "你可以給我放鬆的音樂嗎", "幫我找一首舒壓的歌",
    "我今天心情不好 你可以給我一首放鬆的音樂嗎?",   # 2026-10-05 實機 log:LLM 嘴上答應、卻沒有播放
    "play relaxing music", "play some relaxing music", "put on chill music", "give me some relax songs",
])
def test_relax_requests_route_to_music_and_open_the_fixed_video(music_router, text):
    assert _resolve(music_router, text) == (True, FIXED)


@pytest.mark.parametrize("text", ["放一首輕鬆的爵士", "播放周杰倫的晴天", "play chill jazz", "放一首輕鬆的爵士音樂", "播放 lofi beats"])
def test_music_with_a_genre_or_title_still_searches(music_router, text):
    routed, url = _resolve(music_router, text)
    assert routed and url is None


@pytest.mark.parametrize("text", [
    "我喜歡輕鬆的音樂", "我今天很放鬆", "我想放鬆一下", "不要放鬆的音樂", "不要播輕鬆的音樂", "放鬆音樂跟遊戲新聞", "can you play something relaxing",
])
def test_statements_negations_and_mixed_requests_never_start_playback(music_router, text):
    router, _ = music_router
    matched = router.match(text)
    assert matched is None or matched.capability != "music"


def test_controls_still_win_over_relax_wording(music_router):
    router, music = music_router
    assert router.match("暫停輕鬆的音樂", {"music"}).capability == "music"
    assert PetHarnessEngine._media_arguments(music, "暫停輕鬆的音樂")["action"] == "pause"


def test_pause_without_an_active_session_is_ignored(music_router):
    router, _ = music_router
    assert router.match("暫停輕鬆的音樂") is None


def test_chinese_news_prompt_ends_with_the_numbered_list_rule():
    """2026-10-05 實機:中文新聞沒有專屬收尾規則,只剩「不用條列、編號」的全域規則,LLM 約 7 成不列點
    (本機 gemma3:12b 實測 3/10 照格式;補上後 21/24)。規則必須貼著 Output Contract 末端。"""
    prompt = _prompt("遊戲新聞", True)
    assert prompt.rstrip().endswith("這個編號列表格式優先於其他格式規則。")
    assert "行首編號 1. 2. 3." in prompt.splitlines()[-1]


def test_chinese_non_news_prompt_keeps_the_plain_language_rule():
    prompt = _prompt("你好", False)
    assert prompt.rstrip().endswith("Write reply in 繁體中文（台灣用語）.")


def test_english_news_rule_is_unchanged_because_a_more_explicit_one_made_the_model_mix_in_chinese():
    """實測:加上「每則各占一行並編號」的英文規則後,回覆混入中文 3/8;原規則 0/8。不要再改它。"""
    assert _prompt("any game news today?", True).rstrip().endswith("Write reply in English, one short sentence per article.")
