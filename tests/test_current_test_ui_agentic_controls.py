from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest


def _copy_agentic_root(tmp_path: Path) -> Path:
    target = tmp_path / ".agentic"
    if target.exists():
        return target
    shutil.copytree(Path(".agentic"), target)
    return target


def _make_adapter(tmp_path: Path):
    module = importlib.import_module("pet_harness.ui.pyqt_harness_adapter")
    agentic_root = _copy_agentic_root(tmp_path)
    db_path = tmp_path / "pet_state.db"
    snapshot_path = tmp_path / "debug" / "events" / "latest_pet_event.json"
    return module.PyQtHarnessAdapter(
        agentic_root=agentic_root,
        db_path=db_path,
        snapshot_path=snapshot_path,
    )


def test_adapter_imports_without_starting_pyqt():
    module = importlib.import_module("pet_harness.ui.pyqt_harness_adapter")
    assert hasattr(module, "PyQtHarnessAdapter")


def test_adapter_handles_text_and_returns_ui_safe_fields(tmp_path):
    adapter = _make_adapter(tmp_path)

    result = adapter.handle_text_input("hello", provider="mock")

    assert result["reply"]
    assert "matched_skill" in result
    assert result["provider_status"]["provider_type"] == "mock"
    assert "warnings" in result
    assert "xp_display" in result
    assert "tool" in result
    assert "reward_summary" in result
    assert "asset_summary" in result
    assert result["saved_to_db"] is True


def test_adapter_xp_display_updates_after_interaction(tmp_path):
    adapter = _make_adapter(tmp_path)

    before = adapter.get_current_state()
    result = adapter.handle_text_input("please play some bgm", provider="mock")
    after = adapter.get_current_state()

    assert before["xp"]["xp_total"] == 0
    assert result["xp_delta"] > 0
    assert after["xp"]["xp_total"] >= result["xp_delta"]
    assert "Last +" in after["xp"]["display"]


def test_adapter_xp_state_explains_next_level_progress(tmp_path):
    adapter = _make_adapter(tmp_path)
    adapter.store.add_user_xp(42)

    state = adapter.get_current_state()
    xp = state["xp"]

    assert xp["bond_xp"] == 42
    assert xp["level"] == 1
    assert xp["current_level_min_xp"] == 0
    assert xp["next_level_xp"] == 100
    assert xp["xp_to_next_level"] == 58
    assert 0 < xp["progress_to_next_level"] < 1
    assert "42 / 100 XP" in xp["display"]


def test_adapter_reports_level_up_event_when_threshold_crosses(tmp_path):
    adapter = _make_adapter(tmp_path)
    adapter.store.add_user_xp(98)

    result = adapter.handle_text_input("hello", provider="mock")

    assert result["xp_display"]["level"] == 2
    assert result["xp_display"]["level_up_event"]["from_level"] == 1
    assert result["xp_display"]["level_up_event"]["to_level"] == 2


def test_adapter_lists_default_skills_and_tools(tmp_path):
    adapter = _make_adapter(tmp_path)

    skill_names = {item["skill_id"] for item in adapter.list_skills()}
    tool_names = {item["tool_name"] for item in adapter.list_tools()}

    assert {"music_bgm", "game_news", "break_reminder", "gacha_fortune", "system_monitor"} <= skill_names
    assert {"music_search_tool", "rss_tool", "timer_tool", "random_tool", "system_monitor_tool"} <= tool_names


def test_adapter_state_includes_safe_validation_diagnostics(tmp_path):
    adapter = _make_adapter(tmp_path)

    state = adapter.get_current_state()
    diagnostics = state["diagnostics"]

    expected_fields = {
        "bridge_status",
        "last_action",
        "last_error",
        "brain_mode",
        "provider_selected",
        "provider_resolved",
        "provider_status",
        "api_config_status",
        "skill_count",
        "selected_skill",
        "matched_skill",
        "tool_count",
        "selected_tool",
        "tool_status",
        "xp_total",
        "level",
        "next_level_xp",
        "reward_count",
        "asset_manifest_count",
        "behavior_id",
        "webm_key",
        "background_status",
        "voice_stt_status",
        "voice_tts_status",
    }
    assert expected_fields <= set(diagnostics)
    assert state["background"]["status"] in {"loaded", "fallback_default", "fallback_placeholder"}
    assert state["voice"]["stt"]["status"] in {
        "configured_missing_runtime",
        "configured_and_ready",
        "runtime_available",
    }
    assert state["voice"]["tts"]["status"] in {
        "configured_missing_runtime",
        "configured_and_ready",
        "runtime_available",
        "runtime_present_trigger_not_wired",
    }


def test_skill_enable_disable_persists_and_changes_routing(tmp_path):
    adapter = _make_adapter(tmp_path)

    disabled = adapter.set_skill_enabled("music_bgm", False)
    first = adapter.handle_text_input("please play some bgm", provider="mock")

    reloaded = _make_adapter(tmp_path)
    listed = next(item for item in reloaded.list_skills() if item["skill_id"] == "music_bgm")
    enabled = reloaded.set_skill_enabled("music_bgm", True)
    second = reloaded.handle_text_input("please play some bgm", provider="mock")

    assert disabled["enabled"] is False
    assert first["matched_skill"] != "music_bgm"
    assert listed["enabled"] is False
    assert enabled["enabled"] is True
    assert second["matched_skill"] == "music_bgm"


def test_tool_enable_disable_persists_and_blocks_execution(tmp_path):
    adapter = _make_adapter(tmp_path)

    disabled = adapter.set_tool_enabled("music_search_tool", False)
    blocked = adapter.handle_text_input("please play some bgm", provider="mock")

    reloaded = _make_adapter(tmp_path)
    listed = next(item for item in reloaded.list_tools() if item["tool_name"] == "music_search_tool")
    enabled = reloaded.set_tool_enabled("music_search_tool", True)
    completed = reloaded.handle_text_input("please play some bgm", provider="mock")

    assert disabled["enabled"] is False
    assert blocked["tool"]["status"] == "blocked"
    assert blocked["tool"]["reason"] == "disabled_tool"
    assert listed["enabled"] is False
    assert enabled["enabled"] is True
    assert completed["tool"]["status"] == "completed"


def test_adapter_adds_and_deletes_safe_user_skill(tmp_path):
    adapter = _make_adapter(tmp_path)

    created = adapter.add_skill(
        {
            "skill_id": "test_focus",
            "display_name": "Test Focus",
            "description": "Focus mode test skill.",
            "triggers": ["focus test"],
            "default_behavior": "idle",
        }
    )
    listed = next(item for item in adapter.list_skills() if item["skill_id"] == "test_focus")
    routed = adapter.handle_text_input("focus test", provider="mock")

    assert Path(created["file_path"]).is_file()
    assert listed["is_builtin"] is False
    assert listed["enabled"] is True
    assert routed["matched_skill"] == "test_focus"

    deleted = adapter.delete_skill("test_focus")
    assert deleted["deleted"] is True
    assert all(item["skill_id"] != "test_focus" for item in adapter.list_skills())


def test_invalid_skill_payload_is_rejected(tmp_path):
    adapter = _make_adapter(tmp_path)

    with pytest.raises(ValueError):
        adapter.add_skill(
            {
                "skill_id": "../escape",
                "display_name": "Bad Skill",
                "description": "Should fail",
                "triggers": ["bad"],
                "default_behavior": "idle",
            }
        )


def test_metadata_only_tool_config_is_persisted_without_executor(tmp_path):
    adapter = _make_adapter(tmp_path)

    created = adapter.add_tool_config(
        {
            "tool_name": "test_tool_config",
            "description": "Metadata-only test tool",
            "enabled": True,
            "risk_level": "low",
        }
    )
    listed = next(item for item in adapter.list_tools() if item["tool_name"] == "test_tool_config")
    deleted = adapter.delete_tool_config("test_tool_config")

    assert created["tool_name"] == "test_tool_config"
    assert listed["status"] == "configured_but_unimplemented"
    assert listed["enabled"] is True
    assert listed["has_executor"] is False
    assert deleted["deleted"] is True


def test_invalid_tool_config_payload_is_rejected(tmp_path):
    adapter = _make_adapter(tmp_path)

    with pytest.raises(ValueError):
        adapter.add_tool_config({"tool_name": "../bad", "description": "nope"})


def test_api_provider_without_key_falls_back_safely(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOES_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CHATGPT_API_KEY", raising=False)
    adapter = _make_adapter(tmp_path)

    result = adapter.handle_text_input("hello", provider="api")

    assert result["reply"]
    assert result["provider_status"]["healthy"] is False
    assert result["provider_status"]["metadata"]["error_category"] in {"missing_api_key", "missing_base_url"}
    assert result["warnings"]


def test_adapter_prefers_project_env_for_primary_api_path(tmp_path, monkeypatch):
    module = importlib.import_module("pet_harness.ui.pyqt_harness_adapter")
    agentic_root = _copy_agentic_root(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-api-key",
                "OPENAI_MODEL=gpt-4o-mini",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    adapter = module.PyQtHarnessAdapter(
        agentic_root=agentic_root,
        db_path=tmp_path / "pet_state.db",
        snapshot_path=tmp_path / "debug" / "events" / "latest_pet_event.json",
    )

    config = adapter.build_provider_config("api")

    assert config.provider_type.value == "api"
    assert config.api_key_env_var == "OPENAI_API_KEY"
    assert config.base_url == "https://api.openai.com/v1/chat/completions"
    assert config.model_name == "gpt-4o-mini"


def test_adapter_masks_provider_and_voice_secrets_in_state(tmp_path, monkeypatch):
    module = importlib.import_module("pet_harness.ui.pyqt_harness_adapter")
    agentic_root = _copy_agentic_root(tmp_path)
    raw_chatgpt_key = "sk-test-chatgpt-secret"
    raw_azure_key = "azure-test-secret"
    raw_eleven_key = "eleven-test-secret"
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"CHATGPT_API_KEY={raw_chatgpt_key}",
                "OPENAI_API_KEY=${CHATGPT_API_KEY}",
                "OPENAI_MODEL=gpt-test",
                f"AZURE_STT_API_KEY={raw_azure_key}",
                "AZURE_STT_REGION=eastasia",
                f"ELEVENLABS_API_KEY={raw_eleven_key}",
                "ELEVENLABS_MIKU_VOICE_ID=test-voice",
                "ELEVENLABS_MODEL_ID=test-tts-model",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for key in ("CHATGPT_API_KEY", "OPENAI_API_KEY", "AZURE_STT_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    adapter = module.PyQtHarnessAdapter(
        agentic_root=agentic_root,
        db_path=tmp_path / "pet_state.db",
        snapshot_path=tmp_path / "debug" / "events" / "latest_pet_event.json",
    )
    state_text = __import__("json").dumps(adapter.get_current_state(), ensure_ascii=False)

    assert raw_chatgpt_key not in state_text
    assert raw_azure_key not in state_text
    assert raw_eleven_key not in state_text
    assert "OPENAI_API_KEY" in state_text
    assert "configured" in state_text


def test_transparent_window_module_imports_or_skips():
    pytest.importorskip("PyQt5")
    module = importlib.import_module("ui.transparent_window")
    assert hasattr(module, "TransparentWindow")


def test_transparent_window_reserves_agentic_panel_for_clicks():
    pytest.importorskip("PyQt5")
    module = importlib.import_module("ui.transparent_window")
    cls = module.TransparentWindow

    assert cls.should_treat_point_as_caption(1600, 220, cls.WINDOW_WIDTH, cls.WINDOW_HEIGHT) is False
    assert cls.should_treat_point_as_caption(1780, 60, cls.WINDOW_WIDTH, cls.WINDOW_HEIGHT) is False
    assert cls.should_treat_point_as_caption(240, 220, cls.WINDOW_WIDTH, cls.WINDOW_HEIGHT) is True
