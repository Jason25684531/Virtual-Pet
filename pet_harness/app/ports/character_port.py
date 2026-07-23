from abc import ABC, abstractmethod
from typing import Any

from pet_harness.character.router import ActiveCharacterSnapshot


class CharacterPort(ABC):
    @abstractmethod
    def get_active_snapshot(self) -> ActiveCharacterSnapshot | None: ...

    @abstractmethod
    def switch_character(self, character_id: str) -> dict[str, Any]: ...
