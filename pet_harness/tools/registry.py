from __future__ import annotations

from typing import Callable

from pet_harness.tools import web_article_tool, youtube_music_tool
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
            ToolDefinition("youtube_music_tool", "Play and control YouTube music.", ToolRiskLevel.MEDIUM, ToolExecutionClass.BROWSER, xp_reward=2),
            youtube_music_tool.execute,
        )
        self._register(
            ToolDefinition("web_article_tool", "Fetch current game news.", ToolRiskLevel.MEDIUM, ToolExecutionClass.NETWORK, xp_reward=2),
            web_article_tool.execute,
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
