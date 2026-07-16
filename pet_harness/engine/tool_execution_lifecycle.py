from __future__ import annotations

import time

from pet_harness.engine.media_session_context import MediaSessionContext
from pet_harness.tools.tool_models import ToolRequest, ToolResult


MAX_TOOL_ROUNDS = 3
MAX_EXECUTION_ATTEMPTS = 2
MAX_REPLAN_ROUNDS = 1
MAX_TOTAL_RUN_DURATION_SECONDS = 45


class ToolExecutionLifecycle:
    def __init__(self, safety_guard, registry, store, skills=(), media_context: MediaSessionContext | None = None) -> None:
        self.safety_guard, self.registry, self.store = safety_guard, registry, store
        self.skills = {skill.name: skill for skill in skills}
        self.media_context = media_context or MediaSessionContext(store)

    def run(self, request: ToolRequest) -> ToolResult:
        started = time.monotonic()
        skill = self.skills.get(request.source)
        policy = skill.tool_policy if skill else {}
        request.arguments = self._normalize(request.arguments)
        request.metadata["tool_policy"] = policy
        if request.source == "agent_result" and self.registry.get(request.tool_name) and self.registry.get(request.tool_name).execution_class.value in {"browser", "network"}:
            return self._commit(request, ToolResult(request.tool_name, "blocked", error={"reason": "missing_tool_policy", "message": "Media tools require a skill policy", "retryable": False}, request_id=request.request_id), 0, 0, "authorize")
        safety = self.safety_guard.evaluate(request, policy)
        if not safety.allowed:
            return self._commit(request, ToolResult(request.tool_name, "blocked", error={"reason": safety.reason, "message": "Tool request blocked", "retryable": False, "metadata": safety.metadata}, request_id=request.request_id), 0, 0, "authorize")
        for attempt in range(1, MAX_EXECUTION_ATTEMPTS + 1):
            if time.monotonic() - started >= MAX_TOTAL_RUN_DURATION_SECONDS:
                return self._commit(request, ToolResult(request.tool_name, "failed", error={"reason": "budget_exhausted", "message": "Tool time budget exhausted", "retryable": False}, request_id=request.request_id), 1, attempt, "execute")
            result = self.registry.execute(request)
            if not (result.error or {}).get("retryable"):
                return self._commit(request, result, 1, attempt, "commit")
        return self._commit(request, result, 1, MAX_EXECUTION_ATTEMPTS, "commit")

    @staticmethod
    def _normalize(arguments: dict) -> dict:
        normalized = dict(arguments)
        if "query" in normalized:
            normalized["query"] = " ".join(str(normalized["query"]).split())
        normalized.setdefault("action", "search_and_play" if "query" in normalized else "list_articles")
        return normalized

    def _commit(self, request: ToolRequest, result: ToolResult, round_number: int, attempt: int, phase: str) -> ToolResult:
        result.metadata.update({"round": round_number, "attempt": attempt, "lifecycle_phase": phase})
        self.store.log_tool_result(result, request.to_dict())
        if result.status == "success" and result.tool_name == "web_article_tool":
            self.media_context.save(articles=result.payload.get("articles", []))
        if result.tool_name == "youtube_music_tool":
            self.media_context.save(playback=result.payload)
        return result
