from __future__ import annotations

from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.network_policy import NetworkPolicy
from pet_harness.tools.tool_models import SafetyCheckResult, ToolExecutionClass, ToolRequest, ToolRiskLevel


class SafetyGuard:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def evaluate(self, request: ToolRequest, tool_policy: dict | None = None) -> SafetyCheckResult:
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
        policy = tool_policy or request.metadata.get("tool_policy") or {}
        if definition.execution_class in {ToolExecutionClass.BROWSER, ToolExecutionClass.NETWORK}:
            if not policy:
                return SafetyCheckResult(False, "missing_tool_policy", definition=definition)
            action = request.arguments.get("action")
            if action not in policy.get("allowed_actions", []):
                return SafetyCheckResult(False, "action_not_allowed", definition=definition)
            url = request.arguments.get("url")
            if url:
                allowed, reason, host = NetworkPolicy(list(policy.get("allowed_domains", []))).check_url(str(url))
                if not allowed:
                    return SafetyCheckResult(False, reason, definition=definition, metadata={"host": host})
            if int(request.metadata.get("active_browser_sessions", 0)) >= 2:
                return SafetyCheckResult(False, "too_many_sessions", definition=definition)
        return SafetyCheckResult(True, "allowed", definition=definition)
