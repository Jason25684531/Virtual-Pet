import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import character_library as library_module
from character_library import CharacterLibrary
import pet_harness.character.profile as profile_module
import pet_harness.ui.character_ui_service as character_ui_module
from pet_harness.asset.asset_contract import AssetResponse
from pet_harness.asset.asset_models import AssetJob, JobStatus
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.character.exceptions import CharacterNotFoundError, NoActiveCharacterError
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.character.customization_service import CharacterCustomizationService
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.ui.character_ui_service import LAST_PLAYED_AT_KEY, PLAYTIME_SECONDS_KEY, CharacterUiService
from tests.conftest import FakeProvider

_FRESH_CREATED_AT = datetime.now(UTC).isoformat()

_SKILL_FIXTURES = {
    "joke_skill": {"trigger": "joke, funny", "behavior": "laugh", "xp_reward": "5"},
    "mood_skill": {"trigger": "mood, feeling", "behavior": "idle", "xp_reward": "3"},
    "music_skill": {"trigger": "music, bgm", "behavior": "play_music", "xp_reward": "4"},
}


def _write_skill(skills_dir: Path, name: str, meta: dict[str, str]) -> None:
    lines = [
        f"name: {name}",
        f"description: fixture skill {name}",
        f"trigger: {meta['trigger']}",
        f"behavior: {meta['behavior']}",
        f"xp_reward: {meta['xp_reward']}",
    ]
    (skills_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_character(
    root: Path,
    character_id: str,
    skill_config: list[str],
    is_preset: bool = False,
    motions: dict[str, str] | None = None,
) -> None:
    assets_dir = root / "assets" / "webm" / "characters" / character_id
    (assets_dir / "motions").mkdir(parents=True)
    manifest = {
        "id": character_id,
        "name": character_id,
        "background_image": f"assets/webm/characters/{character_id}/bg.png",
        "motions_dir": f"assets/webm/characters/{character_id}/motions",
        "motions": motions or {"idle": f"assets/webm/characters/{character_id}/motions/idle.webm"},
        "idle_pool": [{"motion": "idle", "weight": 1}],
        "voice_id_env_key": "",
        "layout": {},
        "is_preset": is_preset,
    }
    (assets_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    data_dir = root / "data" / "characters" / character_id
    data_dir.mkdir(parents=True)
    profile = {
        "persona_description": f"{character_id} persona",
        "skill_config": skill_config,
    }
    (data_dir / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.fixture
def service(tmp_path, monkeypatch):
    """tmp 根目錄下建好 Choppr（preset）/ miku（preset），回傳 (service, router, registry)。"""
    _write_character(tmp_path, "Choppr", ["joke_skill", "mood_skill"], is_preset=True)
    _write_character(tmp_path, "miku", ["music_skill"], is_preset=True)
    monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
    # CharacterUiService.list_characters() 會用 CharacterLibrary() 補進 library 角色;
    # 沒有這幾行,library 會掃到真實 repo 的 assets/characters/,汙染 preset/角色清單斷言。
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
    monkeypatch.chdir(tmp_path)

    agentic_root = tmp_path / ".agentic"
    skills_dir = agentic_root / "skills"
    skills_dir.mkdir(parents=True)
    for name, meta in _SKILL_FIXTURES.items():
        _write_skill(skills_dir, name, meta)

    registry = CharacterRegistry(
        assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
        data_dir=str(tmp_path / "data" / "characters"),
    )
    router = CharacterRouter(registry=registry, agentic_root=str(agentic_root))
    return CharacterUiService(router=router, registry=registry), router, registry


class TestListCharacters:
    def test_list_characters_includes_is_preset_and_xp(self, service):
        ui_service, _router, _registry = service
        items = {item["character_id"]: item for item in ui_service.list_characters()}

        assert items["Choppr"]["is_preset"] is True
        assert items["miku"]["is_preset"] is True
        assert items["Choppr"]["xp_total"] == 0
        assert items["Choppr"]["level"] == 1

    def test_list_presets_filters_to_preset_only(self, service, tmp_path):
        ui_service, _router, _registry = service
        _write_character(tmp_path, "Choppr_1", ["joke_skill"], is_preset=False)

        preset_ids = {item["character_id"] for item in ui_service.list_presets()}
        assert preset_ids == {"Choppr", "miku"}


class TestCreateFromPreset:
    def test_create_from_choppr_preset_switches_directly_without_copy(self, service):
        ui_service, router, registry = service
        result = ui_service.create_from_preset("Choppr")

        assert result["character_id"] == "Choppr"
        assert router.get_active_character().character_id == "Choppr"
        new_profile = router.get_active_character()
        assert new_profile.skill_config == ["joke_skill", "mood_skill"]
        assert new_profile.is_preset is True
        assert new_profile.motions == registry.load_character("Choppr").motions

    def test_create_from_preset_is_idempotent_no_new_dirs(self, service, tmp_path):
        ui_service, _router, _registry = service
        first = ui_service.create_from_preset("Choppr")
        second = ui_service.create_from_preset("Choppr")

        assert first["character_id"] == "Choppr"
        assert second["character_id"] == "Choppr"
        assert not (tmp_path / "assets" / "webm" / "characters" / "Choppr_1").exists()
        assert not (tmp_path / "data" / "characters" / "Choppr_1").exists()

    def test_create_from_unknown_preset_raises_and_no_dirs(self, service, tmp_path):
        ui_service, _router, _registry = service

        with pytest.raises(CharacterNotFoundError):
            ui_service.create_from_preset("ghost")

        assert not (tmp_path / "assets" / "webm" / "characters" / "ghost").exists()
        assert not (tmp_path / "data" / "characters" / "ghost").exists()


class TestSwitchAndDelete:
    def test_switch_character_delegates_to_router(self, service):
        ui_service, router, _registry = service
        result = ui_service.switch_character("miku")

        assert result["character_id"] == "miku"
        assert router.get_active_character().character_id == "miku"

    def test_delete_preset_raises_value_error(self, service, tmp_path):
        ui_service, _router, _registry = service

        with pytest.raises(ValueError):
            ui_service.delete_character("Choppr")

        assert (tmp_path / "assets" / "webm" / "characters" / "Choppr").exists()

    def test_delete_library_character_removes_library_and_runtime_data(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
        monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
        monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
        character_dir = tmp_path / "assets" / "characters" / "char-lei-jie"
        (character_dir / "motions").mkdir(parents=True)
        (character_dir / "manifest.json").write_text(json.dumps({
            "id": "char-lei-jie", "name": "Lei Jie", "background_image": "",
            "motions_dir": "assets/characters/char-lei-jie/motions", "motions": {},
            "idle_pool": [], "voice_id_env_key": "", "layout": {},
        }), encoding="utf-8")
        data_dir = tmp_path / "data" / "characters" / "char-lei-jie"
        data_dir.mkdir(parents=True)
        (data_dir / "state.db").write_bytes(b"runtime data")
        registry = CharacterRegistry(
            assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
            data_dir=str(tmp_path / "data" / "characters"),
        )
        service = CharacterUiService(
            router=CharacterRouter(registry=registry, agentic_root=str(tmp_path / ".agentic")),
            registry=registry,
            customization_service=object(),
        )

        assert service.delete_character("char-lei-jie") == {"character_id": "char-lei-jie", "deleted": True}
        assert not character_dir.exists()
        assert not data_dir.exists()

    def test_library_persona_reload_uses_same_engine_on_next_turn(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(profile_module, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
        monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
        monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")

        source = tmp_path / "og.png"
        source.write_bytes(b"og")
        CharacterLibrary().create_validated_character("char-foo", str(source), "Foo")
        (tmp_path / ".agentic" / "skills").mkdir(parents=True)
        registry = CharacterRegistry(
            assets_dir=str(tmp_path / "assets" / "webm" / "characters"),
            data_dir=str(tmp_path / "data" / "characters"),
        )
        router = CharacterRouter(
            registry=registry,
            agentic_root=str(tmp_path / ".agentic"),
            provider_runtime=ProviderRuntime(provider=FakeProvider()),
        )
        service = CharacterCustomizationService(registry=registry, router=router, project_root=tmp_path)
        router.switch_character("char-foo")
        engine = router.get_active_engine()

        service.save_persona("char-foo", "persona A")
        engine.handle_event({"text": "first turn"})
        service.save_persona("char-foo", "persona B")
        engine.handle_event({"text": "second turn"})

        assert router.get_active_engine() is engine
        assert "persona B" in engine.last_prompt

    def test_switch_character_writes_last_played_at(self, service):
        ui_service, router, _registry = service
        ui_service.switch_character("miku")

        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        assert store.get_setting(LAST_PLAYED_AT_KEY)


class TestGetActiveState:
    def test_no_active_character_returns_inactive_state(self, service):
        ui_service, _router, _registry = service
        assert ui_service.get_active_state() == {"active": False}

    def test_active_state_reflects_active_character(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")

        state = ui_service.get_active_state()

        assert state["active"] is True
        assert state["character_id"] == "Choppr"
        assert state["xp_total"] == 0
        assert state["level"] == 1
        skill_names = {item["skill_id"] for item in state["skills"]}
        assert skill_names == {"joke_skill", "mood_skill"}

    def test_switching_character_updates_active_state(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        router.get_active_engine().store.add_user_xp(72)

        router.switch_character("miku")
        state = ui_service.get_active_state()

        assert state["character_id"] == "miku"
        assert state["xp_total"] == 0
        skill_names = {item["skill_id"] for item in state["skills"]}
        assert skill_names == {"music_skill"}
        assert "joke_skill" not in skill_names

    def test_confirming_growth_offer_queues_asset_and_clears_pending(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_offer", {"variant": "development", "reason": "level_up", "source_event_id": "event-1"})

        class AssetService:
            def create_asset(self, request):
                assert request.metadata["variant_type"] == "development"
                return AssetResponse(request_id=request.request_id, status="queued")

        monkeypatch.setattr(character_ui_module, "build_asset_service", lambda *_: AssetService())
        assert ui_service.confirm_growth_offer("Choppr", True)["accepted"] is True
        assert store.get_setting("asset_pending_offer") is None

    def test_confirming_growth_offer_keeps_pending_when_comfyui_falls_back_to_mock(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        offer = {"variant": "development", "reason": "level_up", "source_event_id": "event-1"}
        store.set_setting("asset_pending_offer", offer)

        class MockFallback:
            def create_asset(self, request):
                return AssetResponse(
                    request_id=request.request_id,
                    status="completed",
                    metadata={"service": "mock_asset_service"},
                )

        monkeypatch.setattr(character_ui_module, "build_asset_service", lambda *_: MockFallback())

        result = ui_service.confirm_growth_offer("Choppr", True)

        assert result["accepted"] is False
        assert result["pending"] is True
        assert store.get_setting("asset_pending_offer") == offer

    def test_confirming_growth_offer_sets_generation_freeze(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_offer", {"variant": "development", "reason": "level_up", "source_event_id": "event-1"})

        class AssetService:
            def create_asset(self, request):
                return AssetResponse(request_id=request.request_id, status="queued")

        monkeypatch.setattr(character_ui_module, "build_asset_service", lambda *_: AssetService())
        ui_service.confirm_growth_offer("Choppr", True)

        assert store.get_setting("asset_generation_freeze") is not None

    def test_confirm_motion_generation_accept_releases_freeze(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_generation_freeze", {"created_at": _FRESH_CREATED_AT})
        store.set_setting("asset_pending_motion_offer", {"variant": "development", "source_png": "png", "job_id": "job-1", "reason": "level_up", "created_at": _FRESH_CREATED_AT})

        class AssetService:
            def create_variant_motion_request(self, *_args, **_kwargs):
                return AssetResponse(request_id="job-1", status="queued")

        monkeypatch.setattr(character_ui_module, "build_asset_service", lambda *_: AssetService())
        ui_service.confirm_motion_generation("Choppr", True)

        assert store.get_setting("asset_generation_freeze") is None

    def test_confirm_motion_generation_decline_releases_freeze(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_generation_freeze", {"created_at": _FRESH_CREATED_AT})
        store.set_setting("asset_pending_motion_offer", {"variant": "development", "source_png": "png", "job_id": "job-1", "reason": "level_up", "created_at": _FRESH_CREATED_AT})

        ui_service.confirm_motion_generation("Choppr", False)

        assert store.get_setting("asset_generation_freeze") is None

    def test_confirming_event_growth_offer_picks_unused_festival_prompt(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_offer", {"variant": "event", "reason": "time_interval", "source_event_id": "event-1"})
        store.set_setting("asset_event_prompt_history", ["這個角色戴上聖誕帽", "這個角色手上拿春聯"])

        captured = {}

        class AssetService:
            def create_asset(self, request):
                captured["prompt"] = request.metadata.get("event_prompt")
                return AssetResponse(request_id=request.request_id, status="queued")

        monkeypatch.setattr(character_ui_module, "build_asset_service", lambda *_: AssetService())
        ui_service.confirm_growth_offer("Choppr", True)

        assert captured["prompt"] == "這個角色手上拿粽子"
        assert store.get_setting("asset_event_prompt_history") == ["這個角色手上拿春聯", "這個角色手上拿粽子"]

    def test_declining_event_growth_offer_does_not_consume_rotation(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_offer", {"variant": "event", "reason": "time_interval", "source_event_id": "event-1"})

        ui_service.confirm_growth_offer("Choppr", False)

        assert store.get_setting("asset_event_prompt_history") is None

    def test_active_state_reports_pending_motion_offer(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_motion_offer", {"variant": "development", "source_png": "png", "job_id": "job-1", "reason": "level_up", "created_at": _FRESH_CREATED_AT})

        state = ui_service.get_active_state()

        assert state["pending_motion_offer"]["variant"] == "development"

    def test_active_state_clears_expired_pending_motion_offer(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_motion_offer", {"variant": "development", "source_png": "png", "job_id": "job-1", "reason": "level_up", "created_at": "2000-01-01T00:00:00+00:00"})

        state = ui_service.get_active_state()

        assert state["pending_motion_offer"] is None
        assert store.get_setting("asset_pending_motion_offer") is None

    def test_render_job_sequence_is_persistent_and_character_scoped(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        job = AssetRepository(store).create_job(AssetJob("Choppr", "motion_clip", "development", "render-sequence"))

        assert ui_service.list_render_jobs("Choppr")[0]["progress_percent"] is None
        AssetRepository(store).update(job.job_id, JobStatus.RUNNING, stage="rendering", progress_value=15, progress_max=20)
        rendering = ui_service.list_render_jobs("Choppr")[0]
        assert rendering["character_id"] == "Choppr"
        assert rendering["stage"] == "rendering"
        assert rendering["progress_percent"] == 75.0
        AssetRepository(store).update(job.job_id, JobStatus.COMPLETED, stage="saving")
        assert ui_service.list_render_jobs("Choppr")[0]["status"] == "completed"
        assert ui_service.list_render_jobs("miku") == []

    def test_style_variant_awaiting_confirm_when_motion_offer_pending(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_motion_offer", {"variant": "event", "source_png": "png", "job_id": "job-1", "reason": "time_interval", "created_at": _FRESH_CREATED_AT})
        monkeypatch.setattr(character_ui_module.CharacterLibrary, "list_variant_inventory", lambda _self, _id: [
            {"variant": "event", "state": "generating", "thumb": "png", "is_active": False},
        ])

        assert ui_service.list_style_variants("Choppr")[0]["state"] == "awaiting_confirm"

    def test_confirm_motion_generation_accept_queues_motion_set(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_motion_offer", {"variant": "development", "source_png": "png", "job_id": "job-1", "reason": "level_up", "created_at": _FRESH_CREATED_AT})

        captured = {}

        class AssetService:
            def create_variant_motion_request(self, character_id, variant, source_png, source_event_id, trigger_reason=""):
                captured.update(character_id=character_id, variant=variant, source_png=source_png, trigger_reason=trigger_reason)
                return AssetResponse(request_id=source_event_id, status="queued")

        monkeypatch.setattr(character_ui_module, "build_asset_service", lambda *_: AssetService())
        result = ui_service.confirm_motion_generation("Choppr", True)

        assert result["accepted"] is True
        assert captured == {"character_id": "Choppr", "variant": "development", "source_png": "png", "trigger_reason": "level_up"}
        assert store.get_setting("asset_pending_motion_offer") is None

    def test_confirm_motion_generation_decline_clears_offer_without_queueing(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        store.set_setting("asset_pending_motion_offer", {"variant": "development", "source_png": "png", "job_id": "job-1", "reason": "level_up", "created_at": _FRESH_CREATED_AT})

        result = ui_service.confirm_motion_generation("Choppr", False)

        assert result == {"accepted": False, "pending": False}
        assert store.get_setting("asset_pending_motion_offer") is None

    def test_pending_regeneration_overrides_an_existing_ready_idle(self, service, monkeypatch):
        ui_service, router, _registry = service
        profile = router.switch_character("Choppr")
        store = SQLiteStore(profile.sqlite_path)
        store.initialize()
        monkeypatch.setattr(character_ui_module.CharacterLibrary, "list_variant_inventory", lambda _self, _id: [
            {"variant": "event", "state": "ready", "thumb": "old.png", "is_active": False},
        ])
        from pet_harness.asset.asset_models import AssetJob
        AssetRepository(store).create_job(AssetJob(
            "Choppr", "variant_png", "event", "regenerate-event",
            metadata={"source_path": "source.png"},
        ))

        assert ui_service.list_style_variants("Choppr")[0]["state"] == "generating"
        with pytest.raises(ValueError, match="not ready"):
            ui_service.apply_style("Choppr", "event")

    def test_first_time_generation_stays_empty_until_content_lands(self, service, monkeypatch):
        ui_service, router, _registry = service
        router.switch_character("Choppr")
        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        monkeypatch.setattr(character_ui_module.CharacterLibrary, "list_variant_inventory", lambda _self, _id: [
            {"variant": "event", "state": "empty", "thumb": "", "is_active": False},
        ])
        from pet_harness.asset.asset_models import AssetJob
        AssetRepository(store).create_job(AssetJob(
            "Choppr", "variant_png", "event", "first-time-event",
        ))

        assert ui_service.list_style_variants("Choppr")[0]["state"] == "empty"

    def test_applying_variant_without_background_clears_the_previous_background(self, service, monkeypatch):
        ui_service, _router, _registry = service
        manifest = {"background_image": "old-background.png"}

        class Library:
            def list_variant_inventory(self, _character_id):
                return [{"variant": "event", "state": "ready"}]
            def set_active_variant(self, _character_id, _variant):
                return manifest
            def variant_background_path(self, _character_id, _variant):
                return None
            def set_background(self, _character_id, image_path):
                manifest["background_image"] = image_path or ""
                return manifest
            def get_background_mode(self, _character_id):
                return "follow"

        monkeypatch.setattr(character_ui_module, "CharacterLibrary", Library)

        result = ui_service.apply_style("Choppr", "event")

        assert result["background_image"] == ""

    def test_applying_variant_in_manual_mode_does_not_touch_background(self, service, monkeypatch):
        ui_service, _router, _registry = service
        manifest = {"background_image": "manual-background.png"}
        calls = []

        class Library:
            def list_variant_inventory(self, _character_id):
                return [{"variant": "event", "state": "ready"}]
            def set_active_variant(self, _character_id, _variant):
                return manifest
            def variant_background_path(self, _character_id, _variant):
                return "assets/characters/Choppr/images/bg/event.png"
            def set_background(self, _character_id, image_path):
                calls.append(image_path)
                manifest["background_image"] = image_path or ""
                return manifest
            def get_background_mode(self, _character_id):
                return "manual"

        monkeypatch.setattr(character_ui_module, "CharacterLibrary", Library)

        result = ui_service.apply_style("Choppr", "event")

        assert calls == []
        assert result["background_image"] == "manual-background.png"

    def test_list_scene_backgrounds_delegates_to_library(self, service, monkeypatch):
        ui_service, _router, _registry = service
        monkeypatch.setattr(character_ui_module.CharacterLibrary, "list_background_scenes", lambda _self, _id: [
            {"scene_id": "og", "thumb": "bg/og.png", "is_current": True},
        ])

        assert ui_service.list_scene_backgrounds("Choppr") == [{"scene_id": "og", "thumb": "bg/og.png", "is_current": True}]

    def test_apply_scene_sets_manual_mode_and_background(self, service, monkeypatch):
        ui_service, _router, _registry = service
        manifest = {"background_image": ""}
        calls = []

        class Library:
            def variant_background_path(self, _character_id, variant):
                return f"assets/characters/Choppr/images/bg/{variant}.png"
            def set_background_mode(self, _character_id, mode):
                calls.append(mode)
            def set_background(self, _character_id, image_path):
                manifest["background_image"] = image_path or ""
                return manifest
            def get_character(self, _character_id):
                return {"active_variant": "og"}

        monkeypatch.setattr(character_ui_module, "CharacterLibrary", Library)

        result = ui_service.apply_scene("Choppr", "event")

        assert calls == ["manual"]
        assert result["background_mode"] == "manual"
        assert result["background_image"] == "assets/characters/Choppr/images/bg/event.png"

    def test_apply_scene_follow_restores_active_variant_background(self, service, monkeypatch):
        ui_service, _router, _registry = service
        manifest = {"background_image": ""}
        calls = []

        class Library:
            def variant_background_path(self, _character_id, variant):
                return f"assets/characters/Choppr/images/bg/{variant}.png"
            def set_background_mode(self, _character_id, mode):
                calls.append(mode)
            def set_background(self, _character_id, image_path):
                manifest["background_image"] = image_path or ""
                return manifest
            def get_character(self, _character_id):
                return {"active_variant": "development"}

        monkeypatch.setattr(character_ui_module, "CharacterLibrary", Library)

        result = ui_service.apply_scene("Choppr", "follow")

        assert calls == ["follow"]
        assert result["background_mode"] == "follow"
        assert result["background_image"] == "assets/characters/Choppr/images/bg/development.png"


class TestPlaytime:
    def test_new_character_defaults_to_zero_playtime_and_no_last_played(self, service):
        ui_service, _router, _registry = service
        item = {entry["character_id"]: entry for entry in ui_service.list_characters()}["Choppr"]

        assert item["playtime_seconds"] == 0
        assert item["last_played_at"] is None
        assert item["background_image"] == "assets/webm/characters/Choppr/bg.png"

    def test_add_playtime_accumulates_across_calls(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")

        ui_service.add_playtime("Choppr", 30)
        ui_service.add_playtime("Choppr", 45)

        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        assert int(store.get_setting(PLAYTIME_SECONDS_KEY, 0)) == 75
        assert store.get_setting(LAST_PLAYED_AT_KEY)

    def test_add_playtime_with_zero_seconds_still_touches_last_played_at(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")

        ui_service.add_playtime("Choppr", 0)

        store = SQLiteStore(router.get_active_character().sqlite_path)
        store.initialize()
        assert int(store.get_setting(PLAYTIME_SECONDS_KEY, 0)) == 0
        assert store.get_setting(LAST_PLAYED_AT_KEY)


class TestTriggerSkill:
    def test_trigger_skill_owned_by_active_character_dispatches(self, service):
        ui_service, router, _registry = service
        router.switch_character("Choppr")

        result = ui_service.trigger_skill("joke_skill")

        assert result["matched_skill"] == "joke_skill"
        assert router.get_active_engine().store.get_user_progress()["xp_total"] > 0

    def test_trigger_skill_not_in_active_skill_config_raises(self, service):
        ui_service, router, _registry = service
        router.switch_character("miku")

        with pytest.raises(ValueError):
            ui_service.trigger_skill("joke_skill")

        assert router.get_active_engine().store.get_user_progress()["xp_total"] == 0

    def test_trigger_skill_without_active_character_raises(self, service):
        ui_service, _router, _registry = service

        with pytest.raises(NoActiveCharacterError):
            ui_service.trigger_skill("joke_skill")
