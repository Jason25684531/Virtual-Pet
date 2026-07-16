from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str
    published_at: datetime
    category: str
    summary: str


class BaseArticleFetcher(ABC):
    @abstractmethod
    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]: ...


class RssArticleFetcher(BaseArticleFetcher):
    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]:
        import feedparser
        from email.utils import parsedate_to_datetime
        feed = feedparser.parse(source["url"])
        return [Article(str(item.get("id") or item.get("link")), item.get("title", ""), item.get("link", ""),
                        parsedate_to_datetime(item.get("published", "")), str(item.get("category", "")), item.get("summary", ""))
                for item in feed.entries if item.get("title") and item.get("link") and item.get("published")]


class HttpArticleFetcher(BaseArticleFetcher):
    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]:
        import requests
        from bs4 import BeautifulSoup
        url = source["url"]
        html = requests.get("https://gnn.gamer.com.tw/" if url.endswith("rss.xml") else url, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
        items: list[Article] = []
        for link in soup.select("a[href]"):
            title, url = link.get_text(" ", strip=True), link["href"]
            if title and "/detail.php?sn=" in url:
                items.append(Article(url, title, url if url.startswith("http") else "https://gnn.gamer.com.tw/" + url.lstrip("/"), clock(), "", ""))
        return items


class BrowserArticleFetcher(BaseArticleFetcher):
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]:
        from bs4 import BeautifulSoup
        from pet_harness.runtime.base_browser_runtime import BrowserCommand
        url = source["url"]
        result = self.runtime.submit(BrowserCommand("article_html", {"url": "https://gnn.gamer.com.tw/" if url.endswith("rss.xml") else url}), 15)
        if result.status != "success":
            raise RuntimeError((result.error or {}).get("reason", "browser failed"))
        soup = BeautifulSoup(result.payload["html"], "html.parser")
        return [Article(link["href"], title, link["href"], clock(), "", "")
                for link in soup.select("a[href]")
                if (title := link.get_text(" ", strip=True)) and "/detail.php?sn=" in link["href"]]
