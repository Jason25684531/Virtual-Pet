from __future__ import annotations

from pet_harness.runtime.playwright_browser_runtime import PlaywrightBrowserRuntime


class Page:
    def __init__(self, closed=False): self.closed = closed
    def is_closed(self): return self.closed


class Context:
    """假的持久化 BrowserContext:is_closed()/new_page()/close(),不含 Browser 概念
    (launch_persistent_context 直接回傳 context,見 fix-core-interaction-experience)。"""
    def __init__(self, closed=False): self.closed, self.pages = closed, []
    def is_closed(self): return self.closed
    def new_page(self):
        if self.closed: raise RuntimeError("context closed")
        page = Page(); self.pages.append(page); return page
    def close(self): self.closed = True


def test_closed_page_is_replaced_and_disconnected_context_is_classified():
    runtime = PlaywrightBrowserRuntime()
    try:
        runtime._context = Context()
        old_page = Page(closed=True)
        session = runtime._sessions.create("youtube_music", context=runtime._context, page=old_page)
        recovered, page, reason = runtime._prepare_youtube_page()
        assert recovered is session and page is not old_page and reason == "page_closed"
        runtime._context.closed = True
        assert runtime._prepare_youtube_page() == (None, None, "browser_disconnected")
    finally:
        runtime.shutdown()


def test_controls_do_not_recreate_closed_sessions():
    runtime = PlaywrightBrowserRuntime()
    try:
        runtime._context = Context()
        runtime._sessions.create("youtube_music", context=runtime._context, page=Page(closed=True))
        result = runtime._youtube({"action": "pause"})
        assert result.error["reason"] == "session_closed_by_user"
    finally:
        runtime.shutdown()
