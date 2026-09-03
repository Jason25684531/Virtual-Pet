from pathlib import Path
from typing import Callable

from character_library import CharacterLibrary, PROJECT_ROOT
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.character.profile import CharacterProfile
from pet_harness.memory.base_memory_store import BaseMemoryStore
from pet_harness.runtime.provider_runtime import ProviderRuntime, migrate_legacy_provider_config
from pet_harness.storage.sqlite_store import SQLiteStore

from .action_bus import ActionBus
from .event_bus import SimpleEventBus
from .runtime_lifecycle import RuntimeLifecycle
from .handlers import ConversationHandler, MotionOnlyHandler, QuickIntentHandler, ResetHandler, SpeakHandler


class ApplicationCoordinator:
    """Owns application wiring only; domain decisions remain behind injected ports."""

    def __init__(
        self,
        *,
        event_bus: SimpleEventBus | None = None,
        lifecycle: RuntimeLifecycle | None = None,
        provider_runtime: ProviderRuntime | None = None,
        character_registry: CharacterRegistry | None = None,
        character_router: CharacterRouter | None = None,
        agentic_root: str | Path = ".agentic",
        default_character_id: str | None = None,
        memory_store_factory: Callable[[str, CharacterProfile], BaseMemoryStore] | None = None,
        semantic_index_enabled: bool = False,
    ) -> None:
        self._events = event_bus or SimpleEventBus()
        self._lifecycle = lifecycle or RuntimeLifecycle()
        self._bus = ActionBus(self._events)
        self._motion_configured = False
        self.provider_runtime = provider_runtime or ProviderRuntime()
        self.character_registry = character_registry or CharacterRegistry()
        migrate_legacy_provider_config(self.provider_runtime)
        self.character_router = character_router or CharacterRouter(
            registry=self.character_registry,
            agentic_root=str(agentic_root),
            provider_runtime=self.provider_runtime,
            memory_store_factory=memory_store_factory,
            semantic_index_enabled=semantic_index_enabled,
        )
        if default_character_id is not None:
            self.character_router.switch_character(default_character_id)

    @property
    def action_bus(self) -> ActionBus:
        return self._bus

    @property
    def event_bus(self) -> SimpleEventBus:
        return self._events

    @property
    def lifecycle(self) -> RuntimeLifecycle:
        return self._lifecycle

    def shutdown(self) -> None:
        # ProviderRuntime owns configuration and clients supplied by callers; it has no closeable resource.
        self._lifecycle.shutdown_all()

    def configure_motion(self, motion) -> None:
        if self._motion_configured:
            raise RuntimeError("motion already configured")
        for handler in (
            QuickIntentHandler(motion),
            SpeakHandler(motion),
            ResetHandler(motion, self._bus.cancel_conversation, self._reset_domain_state),
        ):
            self._bus.register(handler)
        # MotionOnlyHandler is the catch-all and must be registered last.
        self._bus.register(MotionOnlyHandler(motion))
        self._motion_configured = True

    def _reset_domain_state(self, reset_all: bool = False) -> None:
        engine = self.character_router.get_active_engine()
        if engine is not None:
            growth = getattr(engine, "growth_trigger", None)
            reset_growth = getattr(growth, "reset", None)
            if callable(reset_growth):
                reset_growth()
            cancel_mock = getattr(getattr(engine, "asset_service", None), "cancel", None)
            if callable(cancel_mock):
                cancel_mock()

        if not reset_all:
            return

        library = CharacterLibrary()
        for manifest in library.list_characters():
            character_id = str(manifest.get("id") or "")
            if not character_id:
                continue
            state_path = PROJECT_ROOT / "data" / "characters" / character_id / "state.db"
            if not state_path.exists():
                continue
            library.reset_style_state(character_id)
            store = SQLiteStore(state_path)
            store.initialize()
            for key in (
                "interaction_count",
                "asset_triggered_interaction_thresholds",
                "asset_pending_offer",
                "asset_pending_motion_offer",
                "asset_last_triggered_level",
                "asset_last_event_variant_at",
                "asset_generation_freeze",
            ):
                store.set_setting(key, None)
            store.clear_style_jobs(character_id)

    def configure_conversation(self, conversation, executor) -> None:
        self._bus.register(ConversationHandler(conversation, executor, self._events))
