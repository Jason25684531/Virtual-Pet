from dataclasses import dataclass, field

from pet_harness.character.exceptions import NoActiveCharacterError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.events import PetEvent, UserEvent
from pet_harness.runtime.provider_runtime import ProviderRuntime


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
    ):
        self._registry = registry or CharacterRegistry()
        self._agentic_root = agentic_root
        # 未注入時建立 fail-closed 的預設 runtime(未設定 → unavailable,不會退回 mock)。
        self._provider_runtime = provider_runtime or ProviderRuntime()
        self._active_profile: CharacterProfile | None = None
        self._active_engine: PetHarnessEngine | None = None
        self._active_snapshot: ActiveCharacterSnapshot | None = None

    @property
    def provider_runtime(self) -> ProviderRuntime:
        return self._provider_runtime

    def switch_character(self, character_id: str) -> CharacterProfile:
        """切換 active 角色;character_id 不存在時拋出例外且不影響現有 active。"""
        profile = self._registry.load_character(character_id)
        # 注入 runtime 本身(滿足 LLMProviderAdapter 協定):configure() 之後
        # 不需要重建 engine,下一個請求自動使用新 adapter。
        engine = PetHarnessEngine(
            provider=self._provider_runtime,
            character_id=character_id,
            agentic_root=self._agentic_root,
        )
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
        # profile/engine/snapshot 全部就緒後才一次替換,避免消費者看到分裂狀態。
        self._active_profile = profile
        self._active_engine = engine
        self._active_snapshot = snapshot
        return profile

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
        if self._active_engine is None:
            raise NoActiveCharacterError("no active character to dispatch event to")
        return self._active_engine.handle_event(event)
