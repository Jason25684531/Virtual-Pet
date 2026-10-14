import logging
from collections.abc import Callable


LOGGER = logging.getLogger(__name__)


class RuntimeLifecycle:
    def __init__(self) -> None:
        self._runtimes: list[CallbackRuntime] = []
        self._shutdown = False

    def register(self, runtime: "CallbackRuntime") -> None:
        self._runtimes.append(runtime)

    def watch_credentials(self, timer, on_status: Callable[[str], None]) -> None:
        from credential_lifecycle import runtime_status
        last_status = [None]

        def check():
            status = runtime_status()
            if status != last_status[0]:
                on_status("" if status is None else "金鑰已到期或設定不可用，請重建 config/.env 並重啟。" if status != "credentials_schedule_failed"
                          else "金鑰清除排程建立失敗；程式仍會檢查到期期限。")
            last_status[0] = status

        timer.timeout.connect(check)
        timer.start(60000)
        self.register(CallbackRuntime("credentials", lambda _wait_ms: timer.stop()))
        check()

    def shutdown_all(self, wait_ms: int = 5000) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        for runtime in reversed(self._runtimes):
            try:
                runtime.stop(wait_ms)
            except Exception:
                LOGGER.warning("runtime stop failed: %s", runtime.name, exc_info=True)


class CallbackRuntime:
    """Minimal adapter for legacy resources that already expose start/stop callbacks."""

    def __init__(self, name: str, stop: Callable[[int], None], start: Callable[[], None] | None = None) -> None:
        self._name, self._stop, self._start = name, stop, start or (lambda: None)

    @property
    def name(self) -> str:
        return self._name

    def start(self) -> None:
        self._start()

    def stop(self, wait_ms: int = 5000) -> None:
        self._stop(wait_ms)
