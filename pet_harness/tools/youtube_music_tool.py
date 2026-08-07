from __future__ import annotations

from typing import Any

from pet_harness.runtime.base_browser_runtime import BaseBrowserRuntime, BrowserCommand
from pet_harness.runtime.playwright_browser_runtime import PlaywrightBrowserRuntime
from pet_harness.tools.tool_models import ToolRequest, ToolResult


_ACTIONS = {"search_and_play", "pause", "resume", "stop", "set_volume", "get_status"}
_FORBIDDEN = {"selector", "xpath", "javascript", "js"}
_runtime: BaseBrowserRuntime | None = None


def rank_candidates(candidates: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Stable, local ranking; page order never decides the selected track alone."""
    tokens = set(query.casefold().split())
    def score(item: dict[str, Any]) -> tuple[int, str, str]:
        title = str(item.get("title", "")).casefold()
        channel = str(item.get("channel", "")).casefold()
        points = 3 * len(tokens.intersection(title.split())) + (2 if "topic" in channel else 0) + (1 if "official" in channel else 0)
        return (points, title, str(item.get("href", "")))
    playable = [item for item in candidates if not any(word in f"{item.get('title', '')} {' '.join(map(str, item.get('badges', [])))}".casefold() for word in ("shorts", "live", "playlist"))]
    return sorted(playable, key=lambda item: (-score(item)[0], score(item)[1], score(item)[2]))


def _default_runtime() -> BaseBrowserRuntime:
    global _runtime
    if _runtime is None:
        _runtime = PlaywrightBrowserRuntime()
    return _runtime


class YouTubeMusicTool:
    def __init__(self, runtime: BaseBrowserRuntime | None = None) -> None:
        self.runtime = runtime or _default_runtime()

    def execute(self, request: ToolRequest) -> ToolResult:
        arguments = request.arguments
        action = arguments.get("action", "search_and_play")
        if action not in _ACTIONS or _FORBIDDEN.intersection(arguments) or set(arguments) - {"action", "query", "volume", "autoplay", "selection_policy", "mode"}:
            return self._failed(request, "invalid_arguments", "Unsupported music request")
        if action == "search_and_play" and not str(arguments.get("query", "")).strip():
            return self._failed(request, "invalid_arguments", "A search query is required")
        if action == "set_volume" and (not isinstance(arguments.get("volume"), (int, float)) or not 0 <= arguments["volume"] <= 100):
            return self._failed(request, "invalid_arguments", "Volume must be between 0 and 100")
        result = self.runtime.submit(BrowserCommand("youtube", dict(arguments, action=action)), 25)
        if result.status != "success":
            return ToolResult("youtube_music_tool", result.status, error=result.error, request_id=request.request_id, evidence=result.evidence)
        if action == "search_and_play":
            evidence = result.evidence
            verified = (
                str(evidence.get("watch_url", "")).startswith("https://www.youtube.com/watch")
                and evidence.get("video_present") is True
                and evidence.get("paused") is False
                and len(evidence.get("current_time_samples", [])) == 2
                and evidence["current_time_samples"][1] > evidence["current_time_samples"][0]
                and evidence.get("page_alive") is True
            )
            if not verified:
                return ToolResult("youtube_music_tool", "partial", payload=result.payload, evidence=evidence,
                                  error={"reason": "autoplay_blocked", "message": "Playback could not be verified", "retryable": False}, request_id=request.request_id)
        return ToolResult("youtube_music_tool", "success", payload=result.payload, evidence=result.evidence, request_id=request.request_id)

    @staticmethod
    def _failed(request: ToolRequest, reason: str, message: str) -> ToolResult:
        return ToolResult("youtube_music_tool", "failed", error={"reason": reason, "message": message, "retryable": False}, request_id=request.request_id)


def execute(request: ToolRequest) -> ToolResult:
    return YouTubeMusicTool().execute(request)


def shutdown_default_runtime() -> None:
    if _runtime is not None:
        _runtime.shutdown()
