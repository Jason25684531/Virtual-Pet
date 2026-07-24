"""Desktop composition arguments stay compatible without desktop side effects."""

from pet_harness.app.application_coordinator import ApplicationCoordinator
from pet_harness.memory.base_memory_store import NullMemoryStore
from pet_harness.runtime.provider_runtime import ProviderRuntime
from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401


class _Store(NullMemoryStore):
    def __init__(self, character_id): self.character_id = character_id


def test_desktop_composition_arguments_build_router_and_isolated_memory(harness_env):
    _tmp_path, agentic_root = harness_env
    stores = []

    def factory(character_id, _profile):
        store = _Store(character_id)
        stores.append(store)
        return store

    coordinator = ApplicationCoordinator(
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
        agentic_root=agentic_root,
        default_character_id="Choppr",
        memory_store_factory=factory,
        semantic_index_enabled=True,
    )

    first = coordinator.character_router.get_active_engine()
    coordinator.character_router.switch_character("Choppr")
    second = coordinator.character_router.get_active_engine()

    assert first.memory_store is stores[0]
    assert second.memory_store is stores[1]
    assert first.memory_store is not second.memory_store
    assert coordinator.character_router._semantic_index_enabled is True
