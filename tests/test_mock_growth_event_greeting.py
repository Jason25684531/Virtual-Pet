import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import character_library as library_module
from character_library import CharacterLibrary
from pet_harness.asset.asset_contract import AssetRequest
from pet_harness.asset.asset_models import AssetJob
from pet_harness.asset.growth_trigger import GrowthTriggerService
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.storage.sqlite_store import SQLiteStore


def _library(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    root = tmp_path / "assets" / "characters" / "pet"
    (root / "motions").mkdir(parents=True)
    (root / "motions" / "og").mkdir()
    (root / "motions" / "og" / "idle.webm").write_bytes(b"og")
    (root / "manifest.json").write_text(json.dumps({
        "id": "pet", "name": "Pet", "motions_dir": "assets/characters/pet/motions",
        "motions": {}, "active_variant": "og", "selected_generations": {},
    }), encoding="utf-8")
    for preset in ("development_a", "development_b", "event"):
        preset_dir = tmp_path / "assets" / "presets" / preset
        preset_dir.mkdir(parents=True)
        (preset_dir / "idle.webm").write_bytes(preset.encode())
    return CharacterLibrary()


def test_mock_interaction_thresholds_and_reset(tmp_path, monkeypatch):
    monkeypatch.setattr("config.COMFYUI_ENABLED", False)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    growth = GrowthTriggerService(store, object(), "pet", 6, 3)

    offers = []
    for count in range(1, 10):
        offer = growth.on_interaction(f"event-{count}")
        if offer:
            offers.append(offer)
            store.set_setting("asset_pending_offer", None)

    assert [offer.metadata["threshold"] for offer in offers] == [3, 6, 9]
    assert offers[-1].metadata["output"] is None
    assert store.get_setting("interaction_count") == 9
    assert growth.on_interaction("event-10") is None

    growth.reset()
    assert store.get_setting("interaction_count") is None
    assert growth.on_interaction("event-reset-1") is None


def test_mock_interaction_thresholds_name_the_prebuilt_variant_directories(tmp_path, monkeypatch):
    monkeypatch.setattr("config.COMFYUI_ENABLED", False)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    growth = GrowthTriggerService(store, object(), "char-Adol", 6, 3)

    offers = []
    for count in range(1, 7):
        offer = growth.on_interaction(f"event-{count}")
        if offer:
            offers.append(offer)
            store.set_setting("asset_pending_offer", None)

    assert [(offer.variant, offer.metadata["preset"]) for offer in offers] == [
        ("development_a", "development_a"),
        ("development_b", "development_b"),
    ]


def test_engine_uses_interaction_milestones_when_comfyui_falls_back_to_mock(tmp_path, monkeypatch):
    monkeypatch.setattr("config.COMFYUI_ENABLED", True)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    calls = []

    class Trigger:
        def on_interaction(self, event_id):
            calls.append(("interaction", event_id))

        def on_xp_awarded(self, *_args):
            raise AssertionError("mock fallback must not use the ComfyUI XP trigger")

    engine = SimpleNamespace(
        store=store,
        asset_service=MockAssetService(store, duration_sec=0),
        growth_trigger=Trigger(),
        xp_manager=SimpleNamespace(award_for_event=lambda _skill: 0),
        reward_manager=SimpleNamespace(check_unlocks=lambda _xp: []),
        _handle_reward_assets=lambda **_kwargs: [],
    )

    PetHarnessEngine._award_and_reward(
        engine, SimpleNamespace(event_id="fallback-event"), None, 0, "idle",
    )

    assert calls == [("interaction", "fallback-event")]


def _wait_for_status(store, job_id, status, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get_asset_job(job_id)
        if job and job["status"] == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {status}: {store.get_asset_job(job_id)}")


def test_mock_render_lands_preset_and_null_round_does_not_change_manifest(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = MockAssetService(store, character_id="pet", library=library, duration_sec=0.02)

    response = service.create_asset(AssetRequest(
        "variant_png", {}, "event-a", metadata={"variant_type": "development", "preset": "development_a"},
    ))
    assert response.status == "queued"
    completed = _wait_for_status(store, response.job_id, "completed")
    landed = tmp_path / "assets" / "characters" / "pet" / "motions" / "development" / "g01" / "idle.webm"
    assert landed.is_file()
    assert completed["progress_value"] == completed["progress_max"] == 100
    assert library.list_variant_inventory("pet")[0]["state"] == "ready"
    assert library.get_character("pet")["active_variant"] == "og"
    assert library.get_motion_path("pet", "idle").endswith("motions\\og\\idle.webm")

    second = service.create_asset(AssetRequest(
        "variant_png", {}, "event-b", metadata={"variant_type": "development", "preset": "development_b"},
    ))
    _wait_for_status(store, second.job_id, "completed")
    assert (tmp_path / "assets" / "characters" / "pet" / "motions" / "development" / "g02" / "idle.webm").is_file()
    assert library.get_character("pet")["active_variant"] == "og"

    festival = service.create_asset(AssetRequest(
        "variant_png", {}, "event-festival", metadata={"variant_type": "event", "preset": "event"},
    ))
    _wait_for_status(store, festival.job_id, "completed")
    assert (tmp_path / "assets" / "characters" / "pet" / "motions" / "event" / "g01" / "idle.webm").is_file()
    assert library.get_character("pet")["active_variant"] == "og"

    before = json.loads((tmp_path / "assets" / "characters" / "pet" / "manifest.json").read_text(encoding="utf-8"))
    null_response = service.create_asset(AssetRequest(
        "variant_png", {}, "event-null", metadata={"variant_type": "development", "output": None},
    ))
    null_job = _wait_for_status(store, null_response.job_id, "completed")
    after = json.loads((tmp_path / "assets" / "characters" / "pet" / "manifest.json").read_text(encoding="utf-8"))
    assert null_job["metadata"]["file_path"] is None
    assert after == before


@pytest.mark.parametrize("variant", ["development_a", "development_b", "event"])
def test_mock_render_uses_an_adol_prebuilt_variant_without_copying_a_preset(tmp_path, monkeypatch, variant):
    library = _library(tmp_path, monkeypatch)
    motions = tmp_path / "assets" / "characters" / "pet" / "motions"
    prebuilt = motions / variant / "idle.webm"
    prebuilt.parent.mkdir()
    prebuilt.write_bytes(variant.encode())
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = MockAssetService(store, character_id="pet", library=library, duration_sec=0)

    response = service.create_asset(AssetRequest(
        "variant_png", {}, f"adol-{variant}",
        metadata={"variant_type": variant, "preset": variant},
    ))

    completed = _wait_for_status(store, response.job_id, "completed")
    assert completed["metadata"]["file_path"] == str(prebuilt)
    assert not (motions / variant / "g01" / "idle.webm").exists()
    library.set_active_variant("pet", variant)
    assert Path(library.get_motion_path("pet", "idle")).read_bytes() == variant.encode()


def test_mock_render_missing_preset_fails(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = MockAssetService(store, character_id="pet", library=library, duration_sec=0)

    response = service.create_asset(AssetRequest(
        "variant_png", {}, "event-missing", metadata={"variant_type": "development", "preset": "missing"},
    ))

    failed = _wait_for_status(store, response.job_id, "failed")
    assert "preset missing" in failed["error_message"]


def test_mock_render_keeps_legacy_og_motion_visible_until_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    root = tmp_path / "assets" / "webm" / "characters" / "pet"
    (root / "motions").mkdir(parents=True)
    (root / "motions" / "Idle.webm").write_bytes(b"og")
    (root / "manifest.json").write_text(json.dumps({
        "id": "pet", "name": "Pet", "motions_dir": "assets/webm/characters/pet/motions",
        "motions": {"idle": "assets/webm/characters/pet/motions/Idle.webm"},
        "active_variant": "og",
    }), encoding="utf-8")
    for preset in ("development_a", "development_b", "event"):
        preset_dir = tmp_path / "assets" / "presets" / preset
        preset_dir.mkdir(parents=True)
        (preset_dir / "idle.webm").write_bytes(preset.encode())

    library = CharacterLibrary()
    store = SQLiteStore(tmp_path / "data" / "characters" / "pet" / "state.db")
    store.initialize()
    service = MockAssetService(store, character_id="pet", library=library, duration_sec=0)
    response = service.create_asset(AssetRequest(
        "variant_png", {}, "legacy-dev", metadata={"variant_type": "development", "preset": "development_a"},
    ))
    _wait_for_status(store, response.job_id, "completed")

    assert library.get_character("pet")["active_variant"] == "og"
    visible_motion = library.get_motion_path("pet", "idle")
    assert visible_motion is not None
    assert Path(visible_motion).read_bytes() == b"og"


def test_mock_render_cancel_marks_failed_without_landing_asset(tmp_path, monkeypatch):
    library = _library(tmp_path, monkeypatch)
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = MockAssetService(store, character_id="pet", library=library, duration_sec=0.5)

    response = service.create_asset(AssetRequest(
        "variant_png", {}, "event-reset", metadata={"variant_type": "development", "preset": "development_b"},
    ))
    time.sleep(0.03)
    service.cancel()

    failed = _wait_for_status(store, response.job_id, "failed")
    assert failed["error_code"] == "reset"
    assert not (tmp_path / "assets" / "characters" / "pet" / "motions" / "development" / "g02" / "idle.webm").exists()
    assert store.list_character_assets("pet", active_only=False) == []


def test_clear_style_jobs_keeps_non_style_jobs(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    store.insert_asset_job(AssetJob("pet", "variant_png", "event", "style").to_dict())
    store.insert_asset_job(AssetJob("pet", "background_png", "og", "background").to_dict())

    store.clear_style_jobs("pet")

    assert [job["workflow_type"] for job in store.list_asset_jobs("pet")] == ["background_png"]


@pytest.mark.uses_repo_cwd
def test_preset_characters_have_all_unlockable_motion_sets():
    root = Path(__file__).parents[1] / "assets" / "characters"
    for character in ("char-Adol", "char-Jack", "char-Kai", "char-Luke", "char-Nico", "char-Omni", "char-ROG"):
        for variant in ("og", "development_a", "development_b", "event"):
            assert (root / character / "motions" / variant / "idle.webm").is_file()


def test_proactive_greeter_round_robin_and_busy_skip():
    pytest.importorskip("PyQt5")
    from PyQt5.QtCore import QCoreApplication

    from ui.proactive_greeter import ProactiveGreeter

    app = QCoreApplication.instance() or QCoreApplication([])
    spoken = []
    busy = [True]
    greeter = ProactiveGreeter(spoken.append, lambda: busy[0], ["a", "b", "c"], 30)
    greeter._on_tick()
    assert spoken == []
    busy[0] = False
    for _ in range(6):
        greeter._on_tick()
    assert len(spoken) == 6
    assert all(spoken[index] != spoken[index + 1] for index in range(5))
    greeter.reset()
    assert not greeter._history
    app.processEvents()


def test_transparent_window_wires_busy_property_as_callback(monkeypatch):
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication

    import ui.transparent_window as window_module
    from ui.transparent_window import TransparentWindow

    app = QApplication.instance() or QApplication([])
    captured = {}

    class FakeGreeter:
        def __init__(self, _speak, is_busy, _phrases, _interval_sec):
            captured["is_busy"] = is_busy

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(window_module, "ProactiveGreeter", FakeGreeter)
    for method_name in ("_init_window", "_init_webview", "_init_developer_input", "_init_tray"):
        monkeypatch.setattr(TransparentWindow, method_name, lambda self: None)

    window = TransparentWindow(
        library=object(),
        adapter=MagicMock(),
        action_bus=MagicMock(),
        lifecycle_shutdown=MagicMock(),
    )
    try:
        assert callable(captured["is_busy"])
        assert captured["is_busy"]() is False
    finally:
        window._playtime_timer.stop()
        window.deleteLater()
        app.processEvents()


def test_proactive_greeting_adds_chat_turn_and_dispatches_wave():
    pytest.importorskip("PyQt5")
    from ui.transparent_window import TransparentWindow

    window = SimpleNamespace(
        _proactive_greeting_active=False,
        _proactive_greeting_release_timer=MagicMock(),
        show_synthetic_conversation_turn=MagicMock(),
        dispatch_action=MagicMock(return_value=True),
        speak_text=MagicMock(),
    )

    TransparentWindow._speak_proactive_greeting(window, "嗨，今天好嗎？")

    window.show_synthetic_conversation_turn.assert_called_once_with("主動打招呼", "", "嗨，今天好嗎？")
    assert "[ACTION:wave_response]" in window.dispatch_action.call_args.args[0]
    window.speak_text.assert_not_called()
