from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
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
    def supports(self, url: str) -> bool:
        return True

    @abstractmethod
    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]: ...


class RssArticleFetcher(BaseArticleFetcher):
    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]:
        import feedparser
        import requests
        from bs4 import BeautifulSoup
        # feedparser 自己抓網址用預設 UA,Polygon 會直接斷線;改由 requests 帶瀏覽器 UA 抓,逾時 5 秒。
        response = requests.get(source["url"], headers={"User-Agent": "Mozilla/5.0 Chrome/124 Safari/537.36"}, timeout=5)
        response.raise_for_status()
        articles = []
        for item in feedparser.parse(response.text).entries:
            stamp = item.get("published_parsed") or item.get("updated_parsed")  # 已正規化成 UTC,RSS 2.0 與 Atom 都吃得下
            if not (item.get("title") and item.get("link") and stamp):
                continue
            summary = BeautifulSoup(item.get("summary", ""), "html.parser").get_text(" ", strip=True)
            articles.append(Article(str(item.get("id") or item.get("link")), item["title"], item["link"],
                                    datetime(*stamp[:6], tzinfo=timezone.utc), str(item.get("category", "")), summary))
        return articles


class HttpArticleFetcher(BaseArticleFetcher):
    def supports(self, url: str) -> bool:
        return "gnn.gamer.com.tw" in url

    def fetch(self, source: dict[str, Any], clock: Any) -> list[Article]:
        import requests
        from bs4 import BeautifulSoup
        url = source["url"]
        html = requests.get("https://gnn.gamer.com.tw/" if url.endswith("rss.xml") else url, timeout=5).text
        soup = BeautifulSoup(html, "html.parser")
        items: list[Article] = []
        for link in soup.select("a[href]"):
            title, url = link.get_text(" ", strip=True), link["href"]
            if title and "/detail.php?sn=" in url:
                items.append(Article(url, title, url if url.startswith("http") else "https://gnn.gamer.com.tw/" + url.lstrip("/"), clock(), "", ""))
        return items


class BrowserArticleFetcher(BaseArticleFetcher):
    def supports(self, url: str) -> bool:
        return "gnn.gamer.com.tw" in url

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
