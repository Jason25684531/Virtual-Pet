import json
import subprocess
import sys
from pathlib import Path

from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.safety_guard import SafetyGuard
from pet_harness.tools.tool_models import (
    ToolDefinition,
    ToolExecutionClass,
    ToolRequest,
    ToolResult,
    ToolRiskLevel,
)
from pet_harness.agent.ollama_provider import OllamaProvider


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_tool_models_serialize_and_registry_contains_builtins():
    definition = ToolDefinition(
        name="random_tool",
        description="random helper",
        risk_level=ToolRiskLevel.LOW,
        execution_class=ToolExecutionClass.INTERNAL,
        enabled=True,
        xp_reward=1,
    )
    request = ToolRequest(tool_name="random_tool", source="debug_cli", arguments={"mode": "fortune"})
    result = ToolResult(tool_name="random_tool", status="completed", payload={"fortune": "lucky"})

    assert definition.to_dict()["name"] == "random_tool"
    assert request.to_dict()["tool_name"] == "random_tool"
    assert result.to_dict()["status"] == "completed"

    registry = ToolRegistry()
    names = {tool.name for tool in registry.list_definitions()}
    assert {"random_tool", "timer_tool", "rss_tool", "music_search_tool", "system_monitor_tool"} <= names
    assert registry.get("unknown_tool") is None


def test_safety_guard_blocks_unknown_disabled_and_unsafe_tools():
    registry = ToolRegistry()
    guard = SafetyGuard(registry)

    unknown = guard.evaluate(ToolRequest(tool_name="unknown_tool", source="debug_cli"))
    assert unknown.allowed is False
    assert unknown.reason == "unknown_tool"

    disabled_definition = ToolDefinition(
        name="disabled_tool",
        description="disabled",
        risk_level=ToolRiskLevel.LOW,
        execution_class=ToolExecutionClass.INTERNAL,
        enabled=False,
    )
    registry.register_definition(disabled_definition)
    disabled = guard.evaluate(ToolRequest(tool_name="disabled_tool", source="debug_cli"))
    assert disabled.allowed is False
    assert disabled.reason == "disabled_tool"

    unsafe_definition = ToolDefinition(
        name="unsafe_tool",
        description="unsafe",
        risk_level=ToolRiskLevel.HIGH,
        execution_class=ToolExecutionClass.SHELL,
        enabled=True,
    )
    registry.register_definition(unsafe_definition)
    unsafe = guard.evaluate(ToolRequest(tool_name="unsafe_tool", source="debug_cli"))
    assert unsafe.allowed is False
    assert unsafe.reason == "unsafe_execution_class"


def test_registry_executes_mock_safe_tools_and_persists_tool_log(tmp_path):
    store = SQLiteStore(tmp_path / "pet_state.db")
    store.initialize()
    registry = ToolRegistry()

    random_result = registry.execute(ToolRequest(tool_name="random_tool", source="debug_cli", arguments={"mode": "fortune"}))
    timer_result = registry.execute(ToolRequest(tool_name="timer_tool", source="debug_cli", arguments={"minutes": 5}))
    store.log_tool_result(random_result)
    store.log_tool_result(timer_result)

    assert random_result.status == "completed"
    assert "fortune" in random_result.payload
    assert timer_result.payload["minutes"] == 5
    assert len(store.recent_tool_results(limit=2)) == 2


def test_mock_asset_service_and_asset_manifest_persistence(tmp_path):
    store = SQLiteStore(tmp_path / "pet_state.db")
    store.initialize()
    service = MockAssetService(store)
    response = service.create_reward_asset_request(
        source_event_id="pet-1",
        reward_id="reward-1",
        behavior_id="idle",
    )

    assert response.status == "completed"
    manifest = store.list_asset_manifest(limit=5)
    assert manifest
    assert manifest[0]["asset_id"] == response.asset_id


def test_harness_engine_executes_skill_tool_and_records_asset_metadata(tmp_path):
    db_path = tmp_path / "pet_state.db"
    snapshot_path = tmp_path / "latest_pet_event.json"
    engine = PetHarnessEngine(
        agentic_root=Path(".agentic"),
        db_path=db_path,
        snapshot_path=snapshot_path,
    )

    event = engine.handle_event({"text": "please play some bgm", "source": "pytest"})

    assert event.matched_skill == "music_bgm"
    assert event.metadata["tool_result"]["tool_name"] == "music_search_tool"
    assert event.metadata["tool_result"]["status"] == "completed"
    assert "asset_result" in event.metadata

    store = SQLiteStore(db_path)
    store.initialize()
    assert store.recent_tool_results(limit=1)[0]["tool_name"] == "music_search_tool"
    assert store.list_asset_manifest(limit=1)


def test_ollama_provider_health_and_model_checks_are_mockable():
    provider = OllamaProvider(
        ProviderConfig(provider_type=ProviderType.OLLAMA, base_url="http://localhost:11434"),
        request_fn=lambda method, url, timeout: FakeResponse({"models": [{"name": "llama3"}]}),
    )

    health = provider.health_check()
    model = provider.check_model("llama3")
    missing = provider.check_model("mistral")

    assert health["healthy"] is True
    assert model["available"] is True
    assert missing["available"] is False


def test_debug_cli_week3_commands(tmp_path):
    db_path = tmp_path / "pet_state.db"

    list_run = subprocess.run(
        [sys.executable, "scripts/debug_harness.py", "--list-tools", "--db-path", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    listed = json.loads(list_run.stdout)
    assert any(item["name"] == "random_tool" for item in listed)

    run_tool = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--run-tool",
            "random_tool",
            "--show-tool-result",
            "--db-path",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    tool_payload = json.loads(run_tool.stdout)
    assert tool_payload["tool_result"]["tool_name"] == "random_tool"
    assert tool_payload["tool_result"]["status"] == "completed"

    unknown_tool = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--run-tool",
            "unknown_tool",
            "--show-tool-result",
            "--db-path",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    unknown_payload = json.loads(unknown_tool.stdout)
    assert unknown_payload["tool_result"]["status"] == "blocked"
    assert unknown_payload["tool_result"]["error"]["reason"] == "unknown_tool"

    asset_run = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--asset-mock",
            "--show-asset-result",
            "--db-path",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    asset_payload = json.loads(asset_run.stdout)
    assert asset_payload["asset_result"]["status"] == "completed"

    ollama_run = subprocess.run(
        [
            sys.executable,
            "scripts/debug_harness.py",
            "--ollama-health",
            "--db-path",
            str(db_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    ollama_payload = json.loads(ollama_run.stdout)
    assert ollama_payload["provider_status"]["provider_type"] == "ollama"
