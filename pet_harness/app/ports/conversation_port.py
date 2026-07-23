from abc import ABC, abstractmethod
from typing import Any


class ConversationPort(ABC):
    @abstractmethod
    def run_turn(self, text: str, source: str) -> dict[str, Any]: ...
