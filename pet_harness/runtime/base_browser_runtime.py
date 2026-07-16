from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeCheckResult:
    available: bool
    reason: str = "available"
    message: str = ""


@dataclass
class BrowserCommand:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrowserCommandResult:
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


class BaseBrowserRuntime(ABC):
    @abstractmethod
    def ensure_started(self) -> RuntimeCheckResult: ...

    @abstractmethod
    def submit(self, command: BrowserCommand, timeout_seconds: float) -> BrowserCommandResult: ...

    @abstractmethod
    def active_session_snapshot(self) -> dict[str, Any] | None: ...

    @abstractmethod
    def shutdown(self, timeout_seconds: float = 5.0) -> None: ...
