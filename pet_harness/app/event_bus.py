from collections import defaultdict
from typing import Callable

from .commands import AppEvent


class SimpleEventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[[AppEvent], None]]] = defaultdict(list)

    def publish(self, event: AppEvent) -> None:
        for listener in tuple(self._listeners[event.name]):
            listener(event)

    def subscribe(self, event_name: str, listener: Callable[[AppEvent], None]) -> None:
        self._listeners[event_name].append(listener)
