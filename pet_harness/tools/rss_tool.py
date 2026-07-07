from __future__ import annotations

from pet_harness.tools.tool_models import ToolRequest, ToolResult


def execute(request: ToolRequest) -> ToolResult:
    topic = request.arguments.get("topic", "games")
    return ToolResult(
        tool_name="rss_tool",
        status="completed",
        payload={
            "topic": topic,
            "summary": [f"Mock {topic} headline 1", f"Mock {topic} headline 2"],
        },
        request_id=request.request_id,
    )
