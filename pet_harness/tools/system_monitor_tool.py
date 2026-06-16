from __future__ import annotations

from pet_harness.tools.tool_models import ToolRequest, ToolResult


def execute(request: ToolRequest) -> ToolResult:
    return ToolResult(
        tool_name="system_monitor_tool",
        status="completed",
        payload={
            "cpu_percent": 12,
            "memory_percent": 34,
            "mode": request.arguments.get("mode", "summary"),
            "source": "mock_safe",
        },
        request_id=request.request_id,
    )
