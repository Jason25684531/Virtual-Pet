"""使用者先手動關掉音樂瀏覽器視窗,App 結束時不得因 TargetClosedError 噴 traceback,且要繼續停 playwright。"""
from types import SimpleNamespace

from pet_harness.runtime.base_browser_runtime import BrowserCommand
from pet_harness.runtime.playwright_browser_runtime import PlaywrightBrowserRuntime


def _runtime(context_close, playwright_stop):
    runtime = PlaywrightBrowserRuntime.__new__(PlaywrightBrowserRuntime)
    runtime._context = SimpleNamespace(close=context_close)
    runtime._playwright = SimpleNamespace(stop=playwright_stop)
    return runtime


def test_shutdown_survives_an_already_closed_context_and_still_stops_playwright():
    stopped = []

    def already_closed():
        raise RuntimeError("Target page, context or browser has been closed")

    runtime = _runtime(already_closed, lambda: stopped.append(True))
    result = runtime._handle(BrowserCommand("shutdown"))
    assert result.status == "success" and stopped == [True]
    assert runtime._context is None and runtime._playwright is None


def test_shutdown_without_a_started_browser_is_a_noop():
    runtime = PlaywrightBrowserRuntime.__new__(PlaywrightBrowserRuntime)
    runtime._context = runtime._playwright = None
    assert runtime._handle(BrowserCommand("shutdown")).status == "success"
