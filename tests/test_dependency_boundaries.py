import ast
import inspect
from pathlib import Path


def test_application_layer_has_no_pyqt_imports():
    project = Path(__file__).parents[1] / "pet_harness"
    roots = (project / "app", project / "engine", project / "character", project / "skills", project / "xp", project / "models")
    violations = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                # 只看 import 節點:ast.Global / ast.Nonlocal 也有 .names,但裡面是字串,
                # 用 getattr 通吃會在任何一個 `global x` 上炸掉。
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                module = getattr(node, "module", "") or ""
                names = [alias.name for alias in node.names]
                if module.startswith("PyQt5") or any(name.startswith("PyQt5") for name in names):
                    violations.append(f"{path}:{node.lineno}")
    assert not violations, "Application layer imports PyQt: " + ", ".join(violations)


def test_ui_does_not_reach_through_adapter_to_router():
    ui_root = Path(__file__).parents[1] / "ui"
    violations = []
    for path in ui_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute) and node.attr == "router"
                or isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and node.args[1].value == "router"
            ):
                violations.append(f"{path}:{node.lineno}")
    assert not violations, "UI reaches through adapter.router: " + ", ".join(violations)


def test_ui_execution_calls_stay_behind_action_bus_and_motion_port():
    root = Path(__file__).parents[1]
    forbidden = ("_action_dispatcher.dispatch(", "_action_dispatcher.trigger_cached_intent(",
                 "_action_dispatcher.speak_text(", "_action_dispatcher.reset_runtime_state(",
                 "_parse_directive", "_bindings")
    violations = [f"{path}:{token}" for path in (root / "ui").rglob("*.py")
                  for token in forbidden if token in path.read_text(encoding="utf-8")]
    assert not violations, "UI bypasses application boundary: " + ", ".join(violations)


def test_legacy_execution_names_do_not_return():
    root = Path(__file__).parents[1]
    violations = []
    for directory in (root / "ui", root / "pet_harness"):
        for path in directory.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "_legacy" in source.replace("migrate_legacy_provider_config", ""):
                violations.append(str(path))
            if "ActionDispatcher" in source:
                violations.append(str(path))
    assert not violations, "Legacy execution names returned: " + ", ".join(violations)


def test_conversation_has_no_worker_or_adapter_fallback():
    source = (Path(__file__).parents[1] / "ui" / "transparent_window.py").read_text(encoding="utf-8")
    assert "HarnessInteractionWorker" not in source
    assert "_adapter.handle_text_input" not in source
    assert "_interaction_worker" not in source


def test_composition_root_does_not_reach_into_window_private_state():
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    assert "window._" not in source
    assert "executor = QtBackgroundExecutor(window)" in source
    assert "coordinator.lifecycle.register(executor)" in source


def test_window_only_requests_lifecycle_shutdown_and_does_not_manage_motion_shutdown():
    source = (Path(__file__).parents[1] / "ui" / "transparent_window.py").read_text(encoding="utf-8")
    assert "shutdown_background_tasks" not in source
    assert "_motion_coordinator.shutdown(" not in source


def test_router_and_application_layers_do_not_import_pyqt():
    root = Path(__file__).parents[1] / "pet_harness"
    for relative in ("character/router.py", "app/application_coordinator.py"):
        assert "PyQt" not in (root / relative).read_text(encoding="utf-8")


def _contract_param_names(func) -> tuple[str, ...]:
    return tuple(param.name for param in inspect.signature(func).parameters.values() if param.name != "self")


def _declares_contract(contract: type, implementation: type) -> bool:
    """Nominal (not structural) "does this implementation declare the contract" check.

    `@runtime_checkable` Protocols make `issubclass()` purely structural: it
    returns True for any class that happens to have matching method names,
    regardless of whether that class ever declared the Protocol as a base —
    exactly the implicit-compatibility gap this guard exists to close. For a
    Protocol contract we therefore check `contract in implementation.__mro__`
    (true only for explicit subclassing). For a plain ABC like
    `BackgroundExecutor`, `issubclass()` stays nominal on its own (respects
    real inheritance and explicit `.register()`), so it is used directly.
    """
    if getattr(contract, "_is_protocol", False):
        return contract in implementation.__mro__
    return issubclass(implementation, contract)


def test_replaceable_boundary_contracts_are_declared_by_every_implementation():
    """runtime-maintainability:「分層替換保持執行契約」——每個宣告為可替換的邊界,
    其正式實作都必須真的宣告符合該契約,且方法簽名不得偏離契約。只有測試替身遵守、
    正式實作靠隱式相容(結構相符但未宣告)通過,是這條規格明確禁止的狀態。"""
    from pet_harness.agent.api_provider import APIProvider
    from pet_harness.agent.ollama_provider import OllamaProvider
    from pet_harness.agent.provider_adapter import LLMProviderAdapter
    from pet_harness.app.ports import BackgroundExecutor
    from pet_harness.memory.fastembed_reranker import FastembedReranker
    from pet_harness.memory.reranker import Reranker
    from pet_harness.runtime.qt_background_executor import QtBackgroundExecutor

    contracts = (
        (BackgroundExecutor, ("submit",), (QtBackgroundExecutor,)),
        (Reranker, ("rerank",), (FastembedReranker,)),
        (LLMProviderAdapter, ("generate_reply", "generate_reply_stream"), (APIProvider, OllamaProvider)),
    )

    violations = []
    for contract, members, implementations in contracts:
        for implementation in implementations:
            if not _declares_contract(contract, implementation):
                violations.append(f"{implementation.__name__} does not declare {contract.__name__}")
                continue
            for member in members:
                contract_params = _contract_param_names(getattr(contract, member))
                impl_params = _contract_param_names(getattr(implementation, member))
                if contract_params != impl_params:
                    violations.append(
                        f"{implementation.__name__}.{member} signature {impl_params} "
                        f"diverges from {contract.__name__}.{member} {contract_params}"
                    )
    assert not violations, "Replaceable-boundary contract drift: " + "; ".join(violations)


def test_streaming_tts_worker_implementations_declare_the_full_contract():
    """StreamingTTSWorker 的訊號是類別層級的資料屬性,typing.Protocol 對含
    非方法成員的 protocol 不支援 issubclass();三個 worker 又是 QThread/QObject,
    無法像上面三個契約一樣直接繼承 Protocol(同一個 sip metaclass 衝突)。改以
    hasattr 逐一檢查 STREAMING_TTS_WORKER_MEMBERS,作為這個契約可行的自動化驗證。"""
    from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker
    from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker
    from api_client.tts_contract import STREAMING_TTS_WORKER_MEMBERS
    from api_client.voai_client import VoAIStreamingTTSWorker

    violations = []
    for implementation in (ElevenLabsStreamingTTSWorker, VoAIStreamingTTSWorker, AdaptiveTTSFallbackWorker):
        missing = [member for member in STREAMING_TTS_WORKER_MEMBERS if not hasattr(implementation, member)]
        if missing:
            violations.append(f"{implementation.__name__} missing {missing}")
    assert not violations, "StreamingTTSWorker contract drift: " + "; ".join(violations)


def test_tts_orchestration_layer_has_no_provider_identity_branching():
    """runtime-maintainability:「語音供應商的增減不得修改編排層」——編排層
    (adaptive_tts_fallback.py、tts_playback.py)不得依供應商名稱字串、實作類別
    名稱或參數探測結果做分支。供應商名稱字面值只允許出現在 tts_contract.py
    (組鏈的資料層,不是編排層的控制流),那裡不受這條規則檢查。"""
    root = Path(__file__).parents[1]
    orchestration_files = (root / "api_client" / "adaptive_tts_fallback.py", root / "tts_playback.py")
    provider_literals = {"voai", "elevenlabs"}

    violations = []
    for path in orchestration_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                literal_operands = [
                    operand.value for operand in (node.left, *node.comparators)
                    if isinstance(operand, ast.Constant) and isinstance(operand.value, str)
                ]
                if any(value in provider_literals for value in literal_operands):
                    violations.append(f"{path}:{node.lineno} compares against provider name literal")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "signature":
                violations.append(f"{path}:{node.lineno} calls inspect.signature")
            elif isinstance(node, ast.Attribute) and node.attr == "__name__":
                violations.append(f"{path}:{node.lineno} reads factory.__name__")
    assert not violations, "TTS orchestration layer branches on provider identity: " + "; ".join(violations)
