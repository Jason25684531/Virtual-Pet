from abc import ABC, abstractmethod

from .commands import ActionCommand
from .results import ActionResult


class ActionHandler(ABC):
    @abstractmethod
    def can_handle(self, command: ActionCommand) -> bool: ...

    @abstractmethod
    def handle(self, command: ActionCommand) -> ActionResult: ...
