"""reduce-turn-latency 1.4 / 4.1:啟動與切換角色時送進 background executor 的預熱工作。"""

from __future__ import annotations

from types import SimpleNamespace

from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter


class _Executor:
    def __init__(self):
        self.jobs = []

    def submit(self, job, _on_done):
        self.jobs.append(job)


def _adapter(engine, executor=None):
    adapter = PyQtHarnessAdapter.__new__(PyQtHarnessAdapter)
    adapter._background_executor = executor
    adapter.router = SimpleNamespace(
        get_active_engine=lambda: engine, switch_character=lambda _cid: "profile"
    )
    adapter._check_time_trigger = lambda: None
    adapter._refresh_runtime = lambda: None
    return adapter


def _engine(warmup_llm=True):
    engine = SimpleNamespace(
        provider=SimpleNamespace(),
        warmup_memory=lambda: None,
        configure_background_executor=lambda _e: None,
        configure_slow_tool_failure_callback=lambda _c: None,
    )
    if warmup_llm:
        engine.warmup_llm = lambda: True
    return engine


def test_startup_submits_memory_warmup_and_llm_prefix_preload():
    engine, executor = _engine(), _Executor()
    PyQtHarnessAdapter.configure_background_executor(_adapter(engine), executor)
    assert executor.jobs == [engine.warmup_memory, engine.warmup_llm]


def test_startup_skips_llm_warmup_for_engine_without_it():
    engine, executor = _engine(warmup_llm=False), _Executor()
    PyQtHarnessAdapter.configure_background_executor(_adapter(engine), executor)
    assert executor.jobs == [engine.warmup_memory]


def test_character_switch_also_rewarms_the_llm_prefix():
    """切角色後前綴換成新角色的 persona/技能,舊角色的 KV cache 用不上,必須重新預熱。"""
    engine, executor = _engine(), _Executor()
    _adapter(engine, executor).warm_active_engine()
    assert executor.jobs == [engine.warmup_memory, engine.warmup_llm]


def test_ollama_prefix_preload_sends_prompt_with_one_token_and_same_options():
    from pet_harness.agent.ollama_provider import OllamaProvider
    from pet_harness.models.provider import ProviderConfig, ProviderType

    sent = []
    provider = OllamaProvider(
        ProviderConfig(provider_type=ProviderType.OLLAMA, model_name="m", metadata={"format": "json"}),
        request_fn=lambda *a, **k: (sent.append(k["json"]), SimpleNamespace(status_code=200))[1],
    )
    assert provider.preload("PREFIX") is True
    assert sent[0]["prompt"] == "PREFIX" and sent[0]["format"] == "json" and sent[0]["options"]["num_predict"] == 1
    assert provider.preload() is True and "prompt" not in sent[1]  # 啟動時舊行為:只載模型


def test_warm_active_engine_without_executor_is_noop():
    _adapter(_engine()).warm_active_engine()  # 不得拋例外


def test_ui_switch_callback_triggers_warmup():
    """真正的 UI 切換路徑(bridge→CharacterUiService→router)不經過 adapter.switch_character,
    唯一會被呼叫到的是視窗的 on_character_switched。"""
    from unittest.mock import MagicMock

    from ui.transparent_window import TransparentWindow

    window = MagicMock()
    TransparentWindow.on_character_switched(window, {"character_id": "char-Adol"})
    window._adapter.warm_active_engine.assert_called_once_with()
