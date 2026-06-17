from __future__ import annotations

from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.tool_models import SafetyCheckResult, ToolExecutionClass, ToolRequest, ToolRiskLevel


class SafetyGuard:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def evaluate(self, request: ToolRequest) -> SafetyCheckResult:
        definition = self.registry.get(request.tool_name)
        if definition is None:
            return SafetyCheckResult(False, "unknown_tool")
        if not definition.enabled:
            return SafetyCheckResult(False, "disabled_tool", definition=definition)
        if definition.execution_class in {
            ToolExecutionClass.SHELL,
            ToolExecutionClass.FILE_SYSTEM,
            ToolExecutionClass.OS_COMMAND,
        }:
            return SafetyCheckResult(False, "unsafe_execution_class", definition=definition)
        if definition.risk_level is ToolRiskLevel.HIGH and not request.confirmation_metadata.get("confirmed"):
            return SafetyCheckResult(False, "missing_confirmation", definition=definition)
        return SafetyCheckResult(True, "allowed", definition=definition)
