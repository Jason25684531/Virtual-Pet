from __future__ import annotations

from pet_harness.runtime.playwright_browser_runtime import PlaywrightBrowserRuntime


class Page:
    def __init__(self, closed=False): self.closed = closed
    def is_closed(self): return self.closed


class Context:
    def __init__(self, broken=False): self.broken, self.pages = broken, []
    def new_page(self):
        if self.broken: raise RuntimeError("context closed")
        page = Page(); self.pages.append(page); return page


class Browser:
    def __init__(self, connected=True): self.connected = connected
    def is_connected(self): return self.connected
    def new_context(self): return Context()


def test_closed_page_is_replaced_and_disconnected_browser_is_classified():
    runtime = PlaywrightBrowserRuntime()
    try:
        runtime._browser = Browser()
        old_page = Page(closed=True)
        session = runtime._sessions.create("youtube_music", browser=runtime._browser, context=Context(), page=old_page)
        recovered, page, reason = runtime._prepare_youtube_page()
        assert recovered is session and page is not old_page and reason == "page_closed"
        runtime._browser.connected = False
        assert runtime._prepare_youtube_page() == (None, None, "browser_disconnected")
    finally:
        runtime.shutdown()


def test_controls_do_not_recreate_closed_sessions():
    runtime = PlaywrightBrowserRuntime()
    try:
        runtime._browser = Browser()
        runtime._sessions.create("youtube_music", browser=runtime._browser, context=Context(), page=Page(closed=True))
        result = runtime._youtube({"action": "pause"})
        assert result.error["reason"] == "session_closed_by_user"
    finally:
        runtime.shutdown()
