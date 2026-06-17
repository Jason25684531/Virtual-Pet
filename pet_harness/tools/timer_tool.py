from __future__ import annotations

from pet_harness.tools.tool_models import ToolRequest, ToolResult


def execute(request: ToolRequest) -> ToolResult:
    minutes = int(request.arguments.get("minutes", 5))
    label = request.arguments.get("label", "break reminder")
    return ToolResult(
        tool_name="timer_tool",
        status="completed",
        payload={"minutes": minutes, "label": label, "scheduled": False},
        request_id=request.request_id,
    )
