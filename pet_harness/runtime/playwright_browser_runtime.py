from __future__ import annotations

from pathlib import Path
from typing import Any

from pet_harness.runtime.base_browser_runtime import BaseBrowserRuntime, BrowserCommand, BrowserCommandResult, RuntimeCheckResult
from pet_harness.runtime.browser_session_manager import BrowserSessionManager
from pet_harness.runtime.browser_worker import BrowserWorker


class PlaywrightBrowserRuntime(BaseBrowserRuntime):
    def __init__(self, profile_dir: str | Path = Path("data/runtime/browser_profile")) -> None:
        self._profile_dir = Path(profile_dir)
        self._worker = BrowserWorker(self._handle)
        self._sessions = BrowserSessionManager()
        self._playwright: Any = None
        # 持久化 context(而非一次性 Browser):讓 cookie／YouTube visitor data／
        # 指紋跨啟動累積,避免每次都是全新無狀態瀏覽器觸發反自動化 403
        # (見 fix-core-interaction-experience)。
        self._context: Any = None
        self._availability: RuntimeCheckResult | None = None

    def ensure_started(self) -> RuntimeCheckResult:
        result = self._worker.submit(BrowserCommand("ensure_started"), 15)
        self._availability = RuntimeCheckResult(result.status == "success", (result.error or {}).get("reason", "available"), (result.error or {}).get("message", ""))
        return self._availability

    def submit(self, command: BrowserCommand, timeout_seconds: float) -> BrowserCommandResult:
        check = self.ensure_started()
        if not check.available:
            return BrowserCommandResult("failed", error={"reason": check.reason, "message": check.message, "retryable": False})
        return self._worker.submit(command, timeout_seconds)

    def active_session_snapshot(self) -> dict[str, Any] | None:
        return self._sessions.snapshot()

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        if self._worker:
            self._worker.submit(BrowserCommand("shutdown"), min(2, timeout_seconds))
            self._worker.shutdown(timeout_seconds)

    def _handle(self, command: BrowserCommand) -> BrowserCommandResult:
        if command.action == "ensure_started":
            return self._start()
        if command.action == "shutdown":
            if self._context:
                self._context.close()
            if self._playwright:
                self._playwright.stop()
            self._context = self._playwright = None
            return BrowserCommandResult("success")
        if command.action == "youtube":
            return self._youtube(command.payload)
        if command.action == "article_html":
            return self._article_html(command.payload)
        return BrowserCommandResult("failed", error={"reason": "unknown_browser_action", "message": command.action, "retryable": False})

    def _start(self) -> BrowserCommandResult:
        if self._context and not self._context.is_closed():
            return BrowserCommandResult("success")
        if self._context:
            self._reset_runtime()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserCommandResult("failed", error={"reason": "playwright_not_installed", "message": "Install playwright first", "retryable": False})
        self._playwright = sync_playwright().start()
        executable = Path(self._playwright.chromium.executable_path)
        if not executable.exists():
            self._playwright.stop()
            self._playwright = None
            return BrowserCommandResult("failed", error={"reason": "chromium_not_installed", "message": "Run playwright install chromium", "retryable": False})
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir), headless=False, args=["--autoplay-policy=no-user-gesture-required"]
        )
        return BrowserCommandResult("success")

    def _reset_runtime(self) -> None:
        """Drop stale Playwright objects; context shutdown can make close() raise."""
        for value in (self._context, self._playwright):
            try:
                if value:
                    value.close() if value is self._context else value.stop()
            except Exception:  # noqa: BLE001 - already-disconnected context
                pass
        self._context = self._playwright = None
        self._sessions = BrowserSessionManager()

    def _youtube(self, payload: dict[str, Any]) -> BrowserCommandResult:
        action = payload.get("action")
        session = self._sessions.first("youtube_music")
        if action == "search_and_play":
            return self._search_and_play(payload)
        if session is None or not session.page or session.page.is_closed():
            return BrowserCommandResult("failed", error={"reason": "session_closed_by_user", "message": "No active browser session", "retryable": False})
        if action == "get_status":
            return BrowserCommandResult("success", payload=session.snapshot())
        expression = {"pause": "video.pause()", "resume": "video.play()", "stop": "video.pause(); video.currentTime=0", "set_volume": f"video.volume={float(payload.get('volume', 100)) / 100}"}.get(action)
        if not expression:
            return BrowserCommandResult("failed", error={"reason": "invalid_arguments", "message": "Unsupported action", "retryable": False})
        session.page.locator("video").evaluate(f"video => {{{expression}}}")
        session.playback_state = "paused" if action in {"pause", "stop"} else "playing"
        return BrowserCommandResult("success", payload=session.snapshot())

    def _search_and_play(self, payload: dict[str, Any]) -> BrowserCommandResult:
        import config

        retries = config.BROWSER_SESSION_RECOVERY_MAX_RETRIES if config.BROWSER_SESSION_RECOVERY_ENABLED else 0
        recovery_reason: str | None = None
        for attempt in range(retries + 1):
            start = self._start()
            if start.status != "success":
                return start
            session, page, reason = self._prepare_youtube_page()
            if page is None:
                recovery_reason = reason or "browser_disconnected"
                if attempt >= retries:
                    return BrowserCommandResult("failed", error={"reason": recovery_reason, "message": "Browser session recovery exhausted", "retryable": False})
                self._recover_session(session, recovery_reason)
                continue
            try:
                return self._play_youtube(session, page, str(payload.get("query", "")), recovery_reason)
            except Exception as exc:  # Playwright raises when a user closes Chromium mid-command.
                recovery_reason = "browser_disconnected" if not self._context or self._context.is_closed() else "page_or_context_closed"
                if attempt >= retries:
                    return BrowserCommandResult("failed", error={"reason": recovery_reason, "message": str(exc), "retryable": False})
                self._recover_session(session, recovery_reason)
        return BrowserCommandResult("failed", error={"reason": "browser_recovery_exhausted", "message": "Browser session recovery exhausted", "retryable": False})

    def _prepare_youtube_page(self):
        if not self._context or self._context.is_closed():
            return None, None, "browser_disconnected"
        session = self._sessions.first("youtube_music")
        try:
            if session is None:
                # 持久化 context 只有一份,音樂 session 共用同一個 context、各自開新分頁。
                session = self._sessions.create("youtube_music", context=self._context)
            if session is None:
                return None, None, "too_many_sessions"
            if session.page and not session.page.is_closed():
                return session, session.page, None
            session.page = session.context.new_page()
            return session, session.page, "page_closed" if session.page else None
        except Exception:
            return session, None, "context_closed"

    def _recover_session(self, session, reason: str) -> None:
        if session is not None:
            self._sessions.close(session.session_id)
        if reason == "browser_disconnected":
            self._reset_runtime()

    @staticmethod
    def _play_youtube(session, page, query: str, recovery_reason: str | None) -> BrowserCommandResult:
        page.goto("https://www.youtube.com/results?search_query=" + query.replace(" ", "+"), wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        links = page.locator('a[href*="/watch"]').evaluate_all("els => els.filter(e => e.href.includes('/watch')).slice(0, 8).map(e => ({href:e.href,title:e.textContent,channel:''}))")
        from pet_harness.tools.youtube_music_tool import rank_candidates
        links = rank_candidates(links, query)
        if not links:
            return BrowserCommandResult("failed", error={"reason": "no_results", "message": "No playable videos found", "retryable": False})
        selected = links[0]
        page.goto(selected["href"], wait_until="domcontentloaded", timeout=15000)
        page.locator("video").evaluate("video => video.play()")
        first = page.locator("video").evaluate("video => ({paused: video.paused, currentTime: video.currentTime})")
        page.wait_for_timeout(1000)
        second = page.locator("video").evaluate("video => ({paused: video.paused, currentTime: video.currentTime})")
        session.current_track, session.current_url = {"title": selected["title"].strip()}, page.url
        session.playback_state = "playing" if not second["paused"] else "paused"
        evidence = {"watch_url": page.url, "video_present": True, "paused": second["paused"], "current_time_samples": [first["currentTime"], second["currentTime"]], "page_alive": not page.is_closed()}
        if recovery_reason:
            evidence.update(recovered=True, recovery_reason=recovery_reason)
        return BrowserCommandResult("success", payload=session.snapshot(), evidence=evidence)

    def _article_html(self, payload: dict[str, Any]) -> BrowserCommandResult:
        page = self._context.new_page()
        page.goto(str(payload["url"]), wait_until="domcontentloaded", timeout=15000)
        return BrowserCommandResult("success", payload={"html": page.content(), "url": page.url})
