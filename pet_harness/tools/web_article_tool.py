from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pet_harness.runtime.base_browser_runtime import BaseBrowserRuntime
from pet_harness.tools.article_fetchers import Article, BaseArticleFetcher, BrowserArticleFetcher, HttpArticleFetcher, RssArticleFetcher
from pet_harness.runtime.playwright_browser_runtime import PlaywrightBrowserRuntime
from pet_harness.tools.tool_models import ToolRequest, ToolResult


class WebArticleTool:
    def __init__(self, fetchers: list[BaseArticleFetcher], runtime: BaseBrowserRuntime | None = None, clock=None) -> None:
        self.fetchers, self.runtime = fetchers, runtime
        self.clock = clock or (lambda: datetime.now(timezone(timedelta(hours=8))))
        self._cache: dict[str, tuple[datetime, list[Article]]] = {}
        self._recent: list[dict] = []

    def execute(self, request: ToolRequest) -> ToolResult:
        action = request.arguments.get("action", "list_articles")
        if action == "list_articles":
            return self._list(request)
        index = request.arguments.get("article_index")
        supplied = request.arguments.get("article")
        if action in {"get_article_detail", "open_article"} and isinstance(supplied, dict) and supplied.get("url"):
            return ToolResult("web_article_tool", "success", payload={"article": supplied}, request_id=request.request_id)
        if action in {"get_article_detail", "open_article"} and isinstance(index, int) and 1 <= index <= len(self._recent):
            article = self._recent[index - 1]
            return ToolResult("web_article_tool", "success", payload={"article": article}, request_id=request.request_id)
        return ToolResult("web_article_tool", "failed", error={"reason": "invalid_arguments", "message": "Unknown article action", "retryable": False}, request_id=request.request_id)

    def _list(self, request: ToolRequest) -> ToolResult:
        now = self.clock()
        key = str(request.metadata.get("character_id", "default"))
        cached = self._cache.get(key)
        articles: list[Article] = cached[1] if cached and now - cached[0] < timedelta(minutes=10) else []
        if not articles:
            source = {"url": "https://gnn.gamer.com.tw/rss.xml"}
            for fetcher in self.fetchers:
                try:
                    articles = fetcher.fetch(source, self.clock)
                except Exception:
                    continue
                if articles:
                    break
            self._cache[key] = (now, articles)
        taipei = timezone(timedelta(hours=8))
        today = now.astimezone(taipei).date()
        normalized: dict[str, Article] = {}
        for article in articles:
            if not all((article.id, article.title, article.url, article.published_at, article.category is not None, article.summary is not None)):
                continue
            if article.published_at.astimezone(taipei).date() == today:
                # ponytail: 以 article.id(RSS guid,或抓取器填入的完整 URL)去重;
                # GNN 文章識別碼在 query string(?sn=...),去除 query 會把當日全部
                # 文章誤併成一篇。
                normalized[article.id or article.url] = article
        category = str(request.arguments.get("category", "")).strip()
        result = sorted(normalized.values(), key=lambda item: item.published_at, reverse=True)
        if category:
            result = [item for item in result if item.category == category]
        limit = min(10, max(1, int(request.arguments.get("limit", 5))))
        self._recent = [{"id": item.id, "title": item.title, "url": item.url, "published_at": item.published_at.isoformat(), "category": item.category, "summary": item.summary} for item in result[:limit]]
        if not self._recent:
            return ToolResult("web_article_tool", "failed", error={"reason": "all_sources_failed", "message": "No current articles found", "retryable": True}, request_id=request.request_id)
        return ToolResult("web_article_tool", "success", payload={"articles": self._recent}, evidence={"source_count": len(articles)}, request_id=request.request_id)


_tool: WebArticleTool | None = None


def execute(request: ToolRequest) -> ToolResult:
    global _tool
    if _tool is None:
        runtime = PlaywrightBrowserRuntime()
        _tool = WebArticleTool([RssArticleFetcher(), HttpArticleFetcher(), BrowserArticleFetcher(runtime)], runtime)
    return _tool.execute(request)
