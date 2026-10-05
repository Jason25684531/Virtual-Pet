"""_play_youtube 的等待:不再固定睡 2.5s + 1s(optimize-e2e-turn-latency 4.2)。"""
from types import SimpleNamespace

from pet_harness.runtime.playwright_browser_runtime import PlaywrightBrowserRuntime


class _FakePage:
    def __init__(self, time_samples, links=None, selector_raises=False):
        self.samples = iter(time_samples)
        self.links = links if links is not None else [{"href": "https://www.youtube.com/watch?v=abc", "title": "lofi beats"}]
        self.selector_raises = selector_raises
        self.url = ""
        self.sleeps, self.gotos, self.selector_waits = [], [], []

    def goto(self, url, **_kwargs):
        self.gotos.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        self.sleeps.append(ms)

    def wait_for_selector(self, selector, timeout):
        self.selector_waits.append((selector, timeout))
        if self.selector_raises:
            raise TimeoutError("no results")

    def is_closed(self):
        return False

    def locator(self, selector):
        page = self
        if selector == "video":
            return SimpleNamespace(evaluate=lambda js: (
                None if "play()" in js else {"paused": False, "currentTime": next(page.samples)}))
        return SimpleNamespace(evaluate_all=lambda js: page.links)


def _session():
    return SimpleNamespace(snapshot=lambda: {}, current_track=None, current_url=None, playback_state="idle")


def test_search_waits_for_results_instead_of_fixed_sleep_and_stops_polling_when_time_advances():
    page = _FakePage([0.0, 0.4])  # 第一次取樣 0.0,第一次輪詢就前進
    result = PlaywrightBrowserRuntime._play_youtube(_session(), page, "lofi beats", None)
    assert result.status == "success"
    assert page.selector_waits == [('a[href*="/watch"]', 5000)]
    assert page.sleeps == [100]  # 沒有 2500、也沒有 1000


def test_polling_is_capped_at_one_second_when_time_never_advances():
    page = _FakePage([0.0] * 11)
    result = PlaywrightBrowserRuntime._play_youtube(_session(), page, "lofi beats", None)
    assert page.sleeps == [100] * 10
    assert result.evidence["current_time_samples"] == [0.0, 0.0]


def test_no_results_still_reports_no_results_when_selector_times_out():
    page = _FakePage([], links=[], selector_raises=True)
    result = PlaywrightBrowserRuntime._play_youtube(_session(), page, "zzzz", None)
    assert result.status == "failed" and result.error["reason"] == "no_results"


def test_relax_query_skips_search_entirely():
    page = _FakePage([0.0, 1.0])
    PlaywrightBrowserRuntime._play_youtube(_session(), page, "輕鬆的音樂", None)
    assert page.gotos == ["https://www.youtube.com/watch?v=Ib2osVLmjSU&t=8s"]
    assert page.selector_waits == []
