from __future__ import annotations

from datetime import datetime
from pathlib import Path
from datetime import timezone, timedelta

from pet_harness.engine.tool_execution_lifecycle import ToolExecutionLifecycle
from pet_harness.models.events import UserEvent
from pet_harness.models.skill import Skill
from pet_harness.runtime.base_browser_runtime import BaseBrowserRuntime, BrowserCommand, BrowserCommandResult, RuntimeCheckResult
from pet_harness.runtime.browser_session_manager import BrowserSessionManager
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.tools.article_fetchers import Article, BaseArticleFetcher
from pet_harness.tools.network_policy import NetworkPolicy
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.tool_models import ToolDefinition, ToolExecutionClass, ToolRequest, ToolResult, ToolRiskLevel
from pet_harness.tools.web_article_tool import WebArticleTool
from pet_harness.tools.youtube_music_tool import YouTubeMusicTool
from pet_harness.tools.youtube_music_tool import rank_candidates


class FakeRuntime(BaseBrowserRuntime):
    def __init__(self, result: BrowserCommandResult) -> None:
        self.result, self.commands = result, []

    def ensure_started(self):
        return RuntimeCheckResult(True)

    def submit(self, command, timeout_seconds):
        self.commands.append(command)
        return self.result

    def active_session_snapshot(self):
        return None

    def shutdown(self, timeout_seconds=5):
        pass


class StaticFetcher(BaseArticleFetcher):
    def __init__(self, articles): self.articles = articles
    def fetch(self, source, clock): return self.articles


def test_tool_result_evidence_and_completed_compatibility():
    result = ToolResult("mock", "completed", evidence={"verified": True}, error={"reason": "x"})
    assert result.to_dict()["evidence"] == {"verified": True}
    assert result.error == {"reason": "x", "message": "x", "retryable": False}


def test_skill_loader_skips_invalid_policy(tmp_path: Path):
    (tmp_path / "valid.md").write_text('name: valid\ndescription: x\ntrigger: x\nbehavior: idle\nxp_reward: 1\ntool_policy_json: {"allowed_domains":["example.com"],"allowed_actions":["go"]}', encoding="utf-8")
    (tmp_path / "invalid.md").write_text('name: invalid\ndescription: x\ntrigger: x\nbehavior: idle\nxp_reward: 1\ntool_policy_json: {bad}', encoding="utf-8")
    skills = SkillLoader(tmp_path).load_skills()
    assert [skill.name for skill in skills] == ["valid"]


def test_network_policy_blocks_unsafe_urls():
    policy = NetworkPolicy(["example.com"], resolver=lambda _: ["127.0.0.1"])
    assert policy.check_url("file:///tmp/x")[1] == "scheme_blocked"
    assert policy.check_url("https://evil.example")[1] == "domain_blocked"
    assert policy.check_url("https://example.com/")[1] == "ssrf_blocked"


def test_session_snapshot_excludes_playwright_objects():
    manager = BrowserSessionManager()
    session = manager.create("youtube_music", browser=object(), context=object(), page=object())
    assert set(session.snapshot()) == {"session_id", "kind", "current_track", "current_url", "playback_state", "last_activity_at"}


def test_youtube_success_requires_all_evidence():
    evidence = {"watch_url": "https://www.youtube.com/watch?v=1", "video_present": True, "paused": False, "current_time_samples": [1, 2], "page_alive": True}
    tool = YouTubeMusicTool(FakeRuntime(BrowserCommandResult("success", evidence=evidence)))
    assert tool.execute(ToolRequest("youtube_music_tool", "test", {"action": "search_and_play", "query": "song"})).status == "success"
    evidence["paused"] = True
    assert tool.execute(ToolRequest("youtube_music_tool", "test", {"action": "search_and_play", "query": "song"})).status == "partial"


def test_ranking_filters_short_and_prefers_matching_title():
    ranked = rank_candidates([
        {"href": "b", "title": "lofi shorts", "badges": ["Shorts"]},
        {"href": "a", "title": "lofi beats", "channel": "Official"},
    ], "lofi beats")
    assert ranked[0]["href"] == "a"


def test_articles_are_filtered_sorted_and_limited():
    now = datetime(2026, 7, 15, 12, tzinfo=timezone(timedelta(hours=8)))
    articles = [
        Article("1", "old", "https://gnn.gamer.com.tw/detail.php?sn=1", now.replace(hour=8), "A", "x"),
        Article("2", "new", "https://gnn.gamer.com.tw/detail.php?sn=2", now.replace(hour=10), "A", "x"),
    ]
    tool = WebArticleTool([StaticFetcher(articles)], clock=lambda: now)
    result = tool.execute(ToolRequest("web_article_tool", "test", {"action": "list_articles", "limit": 1}))
    assert result.status == "success"
    assert result.payload["articles"][0]["title"] == "new"


def test_lifecycle_retries_then_logs(tmp_path):
    from pet_harness.storage.sqlite_store import SQLiteStore
    from pet_harness.tools.safety_guard import SafetyGuard
    store = SQLiteStore(tmp_path / "state.db"); store.initialize()
    registry = ToolRegistry()
    calls = []
    registry.register_definition(ToolDefinition("retry", "x", ToolRiskLevel.LOW, ToolExecutionClass.INTERNAL), lambda request: (calls.append(1) or ToolResult("retry", "success", request_id=request.request_id)))
    result = ToolExecutionLifecycle(SafetyGuard(registry), registry, store).run(ToolRequest("retry", "test"))
    assert result.status == "success" and len(store.recent_tool_results()) == 1
