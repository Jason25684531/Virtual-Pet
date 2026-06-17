from __future__ import annotations

from typing import Callable

from pet_harness.tools import music_search_tool, random_tool, rss_tool, system_monitor_tool, timer_tool
from pet_harness.tools.tool_models import ToolDefinition, ToolExecutionClass, ToolRequest, ToolResult, ToolRiskLevel


ToolExecutor = Callable[[ToolRequest], ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._executors: dict[str, ToolExecutor] = {}
        self._aliases: dict[str, str] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        self._register(
            ToolDefinition("random_tool", "Mock-safe random helper.", ToolRiskLevel.LOW, ToolExecutionClass.INTERNAL, xp_reward=1),
            random_tool.execute,
            aliases=["random"],
        )
        self._register(
            ToolDefinition("timer_tool", "Create reminder metadata.", ToolRiskLevel.LOW, ToolExecutionClass.INTERNAL, xp_reward=1),
            timer_tool.execute,
            aliases=["timer"],
        )
        self._register(
            ToolDefinition("rss_tool", "Mock-safe feed summary tool.", ToolRiskLevel.LOW, ToolExecutionClass.INTERNAL, xp_reward=1),
            rss_tool.execute,
            aliases=["rss_news", "rss"],
        )
        self._register(
            ToolDefinition("music_search_tool", "Mock-safe music search tool.", ToolRiskLevel.LOW, ToolExecutionClass.INTERNAL, xp_reward=2),
            music_search_tool.execute,
            aliases=["music_search"],
        )
        self._register(
            ToolDefinition("system_monitor_tool", "Mock-safe system monitor tool.", ToolRiskLevel.LOW, ToolExecutionClass.INTERNAL, xp_reward=1),
            system_monitor_tool.execute,
            aliases=["system_monitor"],
        )

    def _register(self, definition: ToolDefinition, executor: ToolExecutor, aliases: list[str] | None = None) -> None:
        self._definitions[definition.name] = definition
        self._executors[definition.name] = executor
        for alias in aliases or []:
            self._aliases[alias] = definition.name

    def register_definition(self, definition: ToolDefinition, executor: ToolExecutor | None = None) -> None:
        self._definitions[definition.name] = definition
        if executor is not None:
            self._executors[definition.name] = executor

    def get(self, tool_name: str) -> ToolDefinition | None:
        normalized = self.resolve_name(tool_name)
        return self._definitions.get(normalized) if normalized else None

    def resolve_name(self, tool_name: str) -> str | None:
        if tool_name in self._definitions:
            return tool_name
        return self._aliases.get(tool_name)

    def list_definitions(self) -> list[ToolDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def has_executor(self, tool_name: str) -> bool:
        normalized = self.resolve_name(tool_name)
        return bool(normalized and normalized in self._executors)

    def execute(self, request: ToolRequest) -> ToolResult:
        normalized = self.resolve_name(request.tool_name)
        if normalized is None:
            return ToolResult(
                tool_name=request.tool_name,
                status="blocked",
                error={"reason": "unknown_tool"},
                request_id=request.request_id,
            )
        if normalized not in self._executors:
            return ToolResult(
                tool_name=normalized,
                status="blocked",
                error={"reason": "configured_but_unimplemented"},
                request_id=request.request_id,
            )
        definition = self._definitions[normalized]
        result = self._executors[normalized](ToolRequest(
            tool_name=normalized,
            source=request.source,
            arguments=request.arguments,
            confirmation_metadata=request.confirmation_metadata,
            request_id=request.request_id,
            created_at=request.created_at,
            metadata=request.metadata,
        ))
        result.metadata.setdefault("definition", definition.to_dict())
        return result
