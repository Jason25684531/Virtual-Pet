"""
Helpers for selecting the active brain runtime mode.

This module is intentionally dependency-light so tests can import it without
PyQt, OpenClaw, or any network services.
"""

from __future__ import annotations

import os

VALID_BRAIN_MODES = frozenset({"harness", "openclaw", "auto"})
DEFAULT_BRAIN_MODE = "harness"
ENV_VAR_NAME = "ECHOES_BRAIN_MODE"


def resolve_brain_mode(cli_arg: str | None = None) -> str:
    """Resolve the effective brain mode from CLI, env, or default."""

    if cli_arg is not None:
        normalized = cli_arg.strip().lower()
        if normalized not in VALID_BRAIN_MODES:
            raise ValueError(
                f"無效的 --brain-mode 值: {cli_arg!r}。"
                f" 可用值: {sorted(VALID_BRAIN_MODES)}"
            )
        return normalized

    env_value = os.environ.get(ENV_VAR_NAME, "").strip().lower()
    if env_value:
        if env_value not in VALID_BRAIN_MODES:
            raise ValueError(
                f"無效的環境變數 {ENV_VAR_NAME}={env_value!r}。"
                f" 可用值: {sorted(VALID_BRAIN_MODES)}"
            )
        return env_value

    return DEFAULT_BRAIN_MODE


def is_openclaw_enabled(brain_mode: str) -> bool:
    """Return True when the runtime should attempt the OpenClaw connector."""

    return str(brain_mode).strip().lower() in {"openclaw", "auto"}


def build_runtime_mode_contract(brain_mode: str) -> dict[str, object]:
    resolved = resolve_brain_mode(brain_mode)
    return {
        "brain_mode": resolved,
        "live_runtime_available": resolved in {"openclaw", "auto"},
        "harness_runtime_available": True,
        "openclaw_enabled": is_openclaw_enabled(resolved),
    }
