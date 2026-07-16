from __future__ import annotations

from pet_harness.runtime.base_browser_runtime import BaseBrowserRuntime, BrowserCommand, BrowserCommandResult, RuntimeCheckResult


class FakeBrowserRuntime(BaseBrowserRuntime):
    def __init__(self, result: BrowserCommandResult | None = None) -> None:
        self.result = result or BrowserCommandResult("success")
        self.commands: list[BrowserCommand] = []
        self.closed = False

    def ensure_started(self) -> RuntimeCheckResult:
        return RuntimeCheckResult(True)

    def submit(self, command: BrowserCommand, timeout_seconds: float) -> BrowserCommandResult:
        self.commands.append(command)
        return self.result

    def active_session_snapshot(self):
        return self.result.payload or None

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        self.closed = True
