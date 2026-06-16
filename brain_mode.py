"""
ECHOES — brain_mode 解析模組
純 Python，無任何 PyQt / OpenClaw / 網路依賴，便於單元測試。

支援三種模式：
  harness   — 不啟動 OpenClaw WebSocket（預設，用於 UI smoke test）
  openclaw  — 啟動 OpenClaw WebSocket（完整橋接測試）
  auto      — 嘗試 OpenClaw，失敗後降級為 harness（尚未完整實作，保留為 future work）

解析優先順序：
  1. CLI --brain-mode 參數
  2. 環境變數 ECHOES_BRAIN_MODE
  3. 預設值 "harness"
"""

from __future__ import annotations

import os

VALID_BRAIN_MODES = frozenset({"harness", "openclaw", "auto"})
DEFAULT_BRAIN_MODE = "harness"
ENV_VAR_NAME = "ECHOES_BRAIN_MODE"


def resolve_brain_mode(cli_arg: str | None = None) -> str:
    """解析最終 brain_mode。

    Args:
        cli_arg: 來自 argparse 的 --brain-mode 值（可為 None）。

    Returns:
        "harness" | "openclaw" | "auto"

    Raises:
        ValueError: 若傳入不合法的 mode 值。
    """
    # 1. CLI arg 優先
    if cli_arg is not None:
        normalized = cli_arg.strip().lower()
        if normalized not in VALID_BRAIN_MODES:
            raise ValueError(
                f"[ECHOES] 無效的 --brain-mode 值: {cli_arg!r}。"
                f" 合法值: {sorted(VALID_BRAIN_MODES)}"
            )
        return normalized

    # 2. 環境變數次之
    env_value = os.environ.get(ENV_VAR_NAME, "").strip().lower()
    if env_value:
        if env_value not in VALID_BRAIN_MODES:
            raise ValueError(
                f"[ECHOES] 無效的環境變數 {ENV_VAR_NAME}={env_value!r}。"
                f" 合法值: {sorted(VALID_BRAIN_MODES)}"
            )
        return env_value

    # 3. 預設值
    return DEFAULT_BRAIN_MODE


def is_openclaw_enabled(brain_mode: str) -> bool:
    """判斷此 brain_mode 是否應啟動 OpenClaw WebSocket 連線。

    harness → False（不啟動）
    openclaw → True（啟動）
    auto → True（嘗試連線，失敗後由呼叫端決定降級策略）
    """
    return brain_mode in {"openclaw", "auto"}
