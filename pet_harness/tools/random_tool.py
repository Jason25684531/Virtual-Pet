from __future__ import annotations

from pet_harness.tools.tool_models import ToolRequest, ToolResult


def execute(request: ToolRequest) -> ToolResult:
    options = request.arguments.get("options") or ["lucky", "steady", "spark"]
    index = len(request.request_id) % len(options)
    return ToolResult(
        tool_name="random_tool",
        status="completed",
        payload={
            "mode": request.arguments.get("mode", "choice"),
            "fortune": options[index],
            "options": options,
        },
        request_id=request.request_id,
    )
