from abc import ABC, abstractmethod


class MotionPort(ABC):
    @abstractmethod
    def dispatch_directive(
        self, directive: str, *, trace_id: str | None = None,
        allow_tts: bool = True, wait_for_tts_start: bool = False,
    ) -> bool: ...

    @abstractmethod
    def trigger_cached_intent(self, intent_name: str, source: str) -> bool: ...

    @abstractmethod
    def speak(self, text: str, *, trace_id: str | None = None, has_action: bool = False) -> None: ...

    @abstractmethod
    def reset(self) -> None: ...
