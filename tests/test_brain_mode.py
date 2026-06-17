"""
測試 brain_mode 解析邏輯與 OpenClaw startup gate。

這些測試完全不依賴 PyQt、OpenClaw server、網路、API key、Ollama、ComfyUI 或 GPU。
"""

from __future__ import annotations

import os
import importlib
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# brain_mode 模組測試
# ---------------------------------------------------------------------------

class TestResolveBrainMode:
    """測試 brain_mode.resolve_brain_mode() 的優先順序與解析邏輯。"""

    def _resolve(self, cli_arg=None, env_value=None):
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        env = {}
        if env_value is not None:
            env[ENV_VAR_NAME] = env_value

        with mock.patch.dict(os.environ, env, clear=False):
            # 移除可能存在的環境變數（若 env_value 為 None）
            if env_value is None:
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop(ENV_VAR_NAME, None)
                    return resolve_brain_mode(cli_arg)
            return resolve_brain_mode(cli_arg)

    def test_default_brain_mode_is_harness(self):
        """沒有 CLI arg 且沒有 env var 時，預設應為 harness。"""
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        env_backup = os.environ.pop(ENV_VAR_NAME, None)
        try:
            result = resolve_brain_mode(None)
            assert result == "harness"
        finally:
            if env_backup is not None:
                os.environ[ENV_VAR_NAME] = env_backup

    def test_env_var_can_set_brain_mode_to_openclaw(self):
        """環境變數可設定 brain_mode 為 openclaw。"""
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        with mock.patch.dict(os.environ, {ENV_VAR_NAME: "openclaw"}, clear=False):
            result = resolve_brain_mode(None)
        assert result == "openclaw"

    def test_env_var_can_set_brain_mode_to_harness(self):
        """環境變數可設定 brain_mode 為 harness。"""
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        with mock.patch.dict(os.environ, {ENV_VAR_NAME: "harness"}, clear=False):
            result = resolve_brain_mode(None)
        assert result == "harness"

    def test_cli_arg_overrides_env_var(self):
        """CLI arg 應優先於環境變數。"""
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        with mock.patch.dict(os.environ, {ENV_VAR_NAME: "openclaw"}, clear=False):
            result = resolve_brain_mode("harness")
        assert result == "harness"

    def test_cli_arg_openclaw_overrides_env_harness(self):
        """CLI openclaw 應覆蓋 env harness。"""
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        with mock.patch.dict(os.environ, {ENV_VAR_NAME: "harness"}, clear=False):
            result = resolve_brain_mode("openclaw")
        assert result == "openclaw"

    def test_invalid_cli_arg_raises_value_error(self):
        """不合法的 CLI arg 應 raise ValueError。"""
        from brain_mode import resolve_brain_mode

        with pytest.raises(ValueError, match="無效的 --brain-mode 值"):
            resolve_brain_mode("invalid_mode")

    def test_invalid_env_var_raises_value_error(self):
        """不合法的 env var 應 raise ValueError。"""
        from brain_mode import resolve_brain_mode, ENV_VAR_NAME

        with mock.patch.dict(os.environ, {ENV_VAR_NAME: "bad_mode"}, clear=False):
            with pytest.raises(ValueError, match="無效的環境變數"):
                resolve_brain_mode(None)

    def test_auto_mode_is_accepted(self):
        """auto 是合法的 brain_mode 值。"""
        from brain_mode import resolve_brain_mode

        result = resolve_brain_mode("auto")
        assert result == "auto"

    def test_all_valid_modes_accepted(self):
        """harness、openclaw、auto 都應被接受。"""
        from brain_mode import resolve_brain_mode, VALID_BRAIN_MODES

        for mode in VALID_BRAIN_MODES:
            result = resolve_brain_mode(mode)
            assert result == mode


class TestIsOpenclawEnabled:
    """測試 brain_mode.is_openclaw_enabled() 的判斷邏輯。"""

    def test_harness_mode_does_not_enable_openclaw(self):
        from brain_mode import is_openclaw_enabled
        assert is_openclaw_enabled("harness") is False

    def test_openclaw_mode_enables_openclaw(self):
        from brain_mode import is_openclaw_enabled
        assert is_openclaw_enabled("openclaw") is True

    def test_auto_mode_enables_openclaw_attempt(self):
        from brain_mode import is_openclaw_enabled
        assert is_openclaw_enabled("auto") is True


class TestRuntimeModeContract:
    def test_harness_mode_plan_skips_live_runtime(self):
        from brain_mode import build_runtime_mode_contract

        contract = build_runtime_mode_contract("harness")

        assert contract["brain_mode"] == "harness"
        assert contract["live_runtime_available"] is False
        assert contract["harness_runtime_available"] is True
        assert contract["openclaw_enabled"] is False

    def test_auto_mode_plan_preserves_live_runtime(self):
        from brain_mode import build_runtime_mode_contract

        contract = build_runtime_mode_contract("auto")

        assert contract["brain_mode"] == "auto"
        assert contract["live_runtime_available"] is True
        assert contract["harness_runtime_available"] is True
        assert contract["openclaw_enabled"] is True


# ---------------------------------------------------------------------------
# VMConnector 啟動 gate 測試
# ---------------------------------------------------------------------------

class TestVMConnectorHarnessGate:
    """測試 harness mode 下 VMConnector 不被啟動。"""

    def test_harness_mode_does_not_call_vm_connector_start(self):
        """harness mode 下，VMConnector.start() 不應被呼叫。"""
        from brain_mode import is_openclaw_enabled

        # harness 模式下 is_openclaw_enabled 應為 False
        assert is_openclaw_enabled("harness") is False

        # 模擬 main.py 邏輯：只有 is_openclaw_enabled 才建立/啟動 VMConnector
        mock_start = mock.MagicMock()
        mock_connector = mock.MagicMock()
        mock_connector.start = mock_start

        brain_mode = "harness"
        if is_openclaw_enabled(brain_mode):
            mock_connector.start()

        mock_start.assert_not_called()

    def test_openclaw_mode_allows_vm_connector_start(self):
        """openclaw mode 下，VMConnector.start() 應被呼叫。"""
        from brain_mode import is_openclaw_enabled

        assert is_openclaw_enabled("openclaw") is True

        mock_start = mock.MagicMock()
        mock_connector = mock.MagicMock()
        mock_connector.start = mock_start

        brain_mode = "openclaw"
        if is_openclaw_enabled(brain_mode):
            mock_connector.start()

        mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# brain_mode 模組 import 測試
# ---------------------------------------------------------------------------

class TestBrainModeModuleImport:
    """腦模式模組應可獨立 import，不依賴 PyQt 或其他外部套件。"""

    def test_brain_mode_module_imports_without_pyqt(self):
        """brain_mode 模組應可在無 PyQt 環境下 import。"""
        module = importlib.import_module("brain_mode")
        assert hasattr(module, "resolve_brain_mode")
        assert hasattr(module, "is_openclaw_enabled")
        assert hasattr(module, "VALID_BRAIN_MODES")
        assert hasattr(module, "DEFAULT_BRAIN_MODE")

    def test_default_brain_mode_constant_is_harness(self):
        """DEFAULT_BRAIN_MODE 常數應為 harness。"""
        from brain_mode import DEFAULT_BRAIN_MODE
        assert DEFAULT_BRAIN_MODE == "harness"


# ---------------------------------------------------------------------------
# TransparentWindow brain_mode 參數測試（跳過若無 PyQt）
# ---------------------------------------------------------------------------

class TestTransparentWindowBrainModeParam:
    """測試 TransparentWindow 可接受 brain_mode 參數。"""

    def test_transparent_window_accepts_brain_mode_param(self):
        """TransparentWindow.__init__ 應有 brain_mode 參數（不需要實際執行 PyQt）。"""
        pytest.importorskip("PyQt5")
        import inspect
        module = importlib.import_module("ui.transparent_window")
        cls = module.TransparentWindow
        sig = inspect.signature(cls.__init__)
        assert "brain_mode" in sig.parameters, (
            "TransparentWindow.__init__ 缺少 brain_mode 參數"
        )
        param = sig.parameters["brain_mode"]
        assert param.default == "harness", (
            "brain_mode 預設值應為 'harness'"
        )
