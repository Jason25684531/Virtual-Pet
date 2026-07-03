from pet_harness.character.exceptions import NoActiveCharacterError
from pet_harness.character.profile import CharacterProfile
from pet_harness.character.registry import CharacterRegistry
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.events import PetEvent, UserEvent


class CharacterRouter:
    """主流程的角色切換中樞：持有單一 active PetHarnessEngine instance。

    切換角色時原子性地替換 engine instance、motions 來源與 voice_id_env_key
    ——三者皆來自同一個剛載入的 CharacterProfile。
    """

    def __init__(
        self,
        registry: CharacterRegistry | None = None,
        agentic_root: str = ".agentic",
    ):
        self._registry = registry or CharacterRegistry()
        self._agentic_root = agentic_root
        self._active_profile: CharacterProfile | None = None
        self._active_engine: PetHarnessEngine | None = None

    def switch_character(self, character_id: str) -> CharacterProfile:
        """切換 active 角色；character_id 不存在時拋出例外且不影響現有 active。"""
        profile = self._registry.load_character(character_id)
        self._active_profile = profile
        self._active_engine = PetHarnessEngine(
            character_id=character_id,
            agentic_root=self._agentic_root,
        )
        return profile

    def get_active_character(self) -> CharacterProfile | None:
        return self._active_profile

    def get_active_engine(self) -> PetHarnessEngine | None:
        return self._active_engine

    def get_active_motions(self) -> dict[str, str]:
        if self._active_profile is None:
            return {}
        return self._active_profile.motions

    def get_voice_id_env_key(self) -> str | None:
        if self._active_profile is None:
            return None
        return self._active_profile.voice_id_env_key

    def dispatch_event(self, event: UserEvent | dict) -> PetEvent:
        if self._active_engine is None:
            raise NoActiveCharacterError("no active character to dispatch event to")
        return self._active_engine.handle_event(event)
