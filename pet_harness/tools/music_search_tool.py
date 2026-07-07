from __future__ import annotations

from pet_harness.tools.tool_models import ToolRequest, ToolResult


def execute(request: ToolRequest) -> ToolResult:
    query = request.arguments.get("query", "bgm")
    return ToolResult(
        tool_name="music_search_tool",
        status="completed",
        payload={
            "query": query,
            "results": [
                {"title": "Lo-fi Focus Stream", "source": "mock"},
                {"title": "Cozy Coding BGM", "source": "mock"},
            ],
        },
        request_id=request.request_id,
    )
