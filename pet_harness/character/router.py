from dataclasses import dataclass, field
from threading import RLock

from pet_harness.character.exceptions import NoActiveCharacterError
from pet_harness.character.exceptions import CharacterNotFoundError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.memory.base_memory_store import BaseMemoryStore, NullMemoryStore
from pet_harness.models.events import PetEvent, UserEvent
from pet_harness.runtime.provider_runtime import ProviderRuntime
from typing import Callable
from character_library import CharacterLibrary


@dataclass(frozen=True)
class ActiveCharacterSnapshot:
    """原子的 active character 快照:UI 動作、背景、聲線、skills 都只從這裡解析。"""

    character_id: str
    name: str
    profile: CharacterProfile
    motions: dict[str, str] = field(default_factory=dict)
    idle_pool: tuple = ()
    background_image: str = ""
    layout: dict = field(default_factory=dict)
    voice_id_env_key: str = ""
    skill_refs: tuple[str, ...] = ()


class CharacterRouter:
    """主流程的角色切換中樞:持有單一 active PetHarnessEngine instance。

    切換角色時原子性地替換 engine instance 與 active snapshot;Provider 來自
    注入的全域 ProviderRuntime,切換角色不改變 Provider 選擇,也不從角色
    SQLite 讀寫任何 provider 設定。
    """

    def __init__(
        self,
        registry: CharacterRegistry | None = None,
        agentic_root: str = ".agentic",
        provider_runtime: ProviderRuntime | None = None,
        memory_store_factory: Callable[[str, CharacterProfile], BaseMemoryStore] | None = None,
        semantic_index_enabled: bool = False,
    ):
        self._registry = registry or CharacterRegistry()
        self._library = CharacterLibrary()
        self._agentic_root = agentic_root
        # 未注入時建立 fail-closed 的預設 runtime(未設定 → unavailable,不會退回 mock)。
        self._provider_runtime = provider_runtime or ProviderRuntime()
        self._memory_store_factory = memory_store_factory or (lambda _cid, _profile: NullMemoryStore())
        self._semantic_index_enabled = semantic_index_enabled
        self._active_profile: CharacterProfile | None = None
        self._active_engine: PetHarnessEngine | None = None
        self._active_snapshot: ActiveCharacterSnapshot | None = None
        self._lock = RLock()
        self._inflight: dict[PetHarnessEngine, int] = {}
        self._retired_engines: set[PetHarnessEngine] = set()
        self._shutdown_requested = False

    @property
    def provider_runtime(self) -> ProviderRuntime:
        return self._provider_runtime

    def switch_character(self, character_id: str) -> CharacterProfile:
        """切換 active 角色;character_id 不存在時拋出例外且不影響現有 active。"""
        profile, is_library_character = self.load_profile(character_id)
        with self._lock:
            previous_engine = self._active_engine
            if previous_engine is not None:
                self._retire_engine(previous_engine)
            memory_store = self._memory_store_factory(character_id, profile)
            engine = PetHarnessEngine(
                provider=self._provider_runtime,
                character_id=character_id,
                character_profile=profile if is_library_character else None,
                agentic_root=self._agentic_root,
                memory_store=memory_store,
                semantic_index_enabled=self._semantic_index_enabled,
            )
            profile = engine.character_profile or profile
            snapshot = ActiveCharacterSnapshot(
                character_id=profile.character_id,
                name=profile.name,
                profile=profile,
                motions=dict(profile.motions),
                idle_pool=tuple(profile.idle_pool),
                background_image=profile.background_image,
                layout=dict(profile.layout),
                voice_id_env_key=profile.voice_id_env_key,
                skill_refs=tuple(skill.name for skill in engine.skills),
            )
            self._active_profile = profile
            self._active_engine = engine
            self._active_snapshot = snapshot
        return profile

    def load_profile(self, character_id: str) -> tuple[CharacterProfile, bool]:
        """解析角色 profile:registry 優先,退回 library(上傳生成的角色)。
        回傳 (profile, is_library_character);所有需要跨兩個世界找角色的呼叫端都走這裡。"""
        try:
            return self._registry.load_character(character_id), False
        except CharacterNotFoundError:
            manifest = self._library.get_character(character_id)
            if not manifest:
                raise
            return CharacterProfile(
                character_id=str(manifest["id"]),
                name=str(manifest.get("name") or character_id),
                background_image=str(manifest.get("background_image") or ""),
                motions_dir=str(manifest.get("motions_dir") or ""),
                motions=dict(manifest.get("motions") or {}),
                idle_pool=list(manifest.get("idle_pool") or []),
                voice_id_env_key=str(manifest.get("voice_id_env_key") or ""),
                layout=dict(manifest.get("layout") or {}),
                persona_description="",
                skill_config=[],
            ), True

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
            if self._active_engine is not None:
                self._retire_engine(self._active_engine)
            self._active_engine = None
            self._active_profile = None
            self._active_snapshot = None

    def acquire_engine(self, character_id: str) -> PetHarnessEngine:
        """Reserve the current engine before a background conversation is queued."""
        with self._lock:
            if self._shutdown_requested or self._active_engine is None or self._active_profile is None:
                raise NoActiveCharacterError("no active character to dispatch event to")
            if self._active_profile.character_id != character_id:
                raise NoActiveCharacterError(f"character is no longer active: {character_id}")
            self._inflight[self._active_engine] = self._inflight.get(self._active_engine, 0) + 1
            return self._active_engine

    def release_engine(self, engine: PetHarnessEngine) -> None:
        with self._lock:
            count = self._inflight.get(engine, 0)
            if count <= 1:
                self._inflight.pop(engine, None)
                if engine in self._retired_engines:
                    self._retired_engines.remove(engine)
                    engine.shutdown()
            else:
                self._inflight[engine] = count - 1

    def dispatch_event_for_character(self, character_id: str, event: UserEvent | dict) -> PetEvent:
        engine = self.acquire_engine(character_id)
        try:
            return engine.handle_event(event)
        finally:
            self.release_engine(engine)

    def _retire_engine(self, engine: PetHarnessEngine) -> None:
        self._retired_engines.add(engine)
        if not self._inflight.get(engine):
            self._retired_engines.remove(engine)
            engine.shutdown()

    def get_active_character(self) -> CharacterProfile | None:
        return self._active_profile

    def get_active_engine(self) -> PetHarnessEngine | None:
        return self._active_engine

    def get_active_snapshot(self) -> ActiveCharacterSnapshot | None:
        return self._active_snapshot

    def get_active_motions(self) -> dict[str, str]:
        if self._active_snapshot is None:
            return {}
        return self._active_snapshot.motions

    def get_voice_id_env_key(self) -> str | None:
        if self._active_snapshot is None:
            return None
        return self._active_snapshot.voice_id_env_key

    def dispatch_event(self, event: UserEvent | dict) -> PetEvent:
        profile = self.get_active_character()
        if profile is None:
            raise NoActiveCharacterError("no active character to dispatch event to")
        return self.dispatch_event_for_character(profile.character_id, event)
