from pathlib import Path
from typing import Callable

from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.character.profile import CharacterProfile
from pet_harness.memory.base_memory_store import BaseMemoryStore
from pet_harness.runtime.provider_runtime import ProviderRuntime, migrate_legacy_provider_config

from .action_bus import ActionBus
from .event_bus import EventBus, SimpleEventBus
from .runtime_lifecycle import RuntimeLifecycle
from .handlers import ConversationHandler, MusicHandler, MotionOnlyHandler, NewsHandler, QuickIntentHandler, ResetHandler, SpeakHandler, WaveHandler
from .ports import MotionPort


class ApplicationCoordinator:
    """Owns application wiring only; domain decisions remain behind injected ports."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
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
    def event_bus(self) -> EventBus:
        return self._events

    @property
    def lifecycle(self) -> RuntimeLifecycle:
        return self._lifecycle

    def shutdown(self) -> None:
        # ProviderRuntime owns configuration and clients supplied by callers; it has no closeable resource.
        self._lifecycle.shutdown_all()

    def configure_motion(self, motion: MotionPort) -> None:
        if self._motion_configured:
            raise RuntimeError("motion already configured")
        for handler in (NewsHandler(motion), MusicHandler(motion), WaveHandler(motion), QuickIntentHandler(motion), SpeakHandler(motion), MotionOnlyHandler(motion), ResetHandler(motion)):
            self._bus.register(handler)
        self._motion_configured = True

    def configure_conversation(self, conversation, executor) -> None:
        self._bus.register(ConversationHandler(conversation, executor, self._events))
