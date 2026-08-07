from typing import Any, Callable

from pet_harness.app.ports import BackgroundExecutor


class FakeBackgroundExecutor(BackgroundExecutor):
    def submit(self, job: Callable[[], Any], on_done: Callable[[bool, str, Any], None]) -> None:
        try:
            on_done(True, "", job())
        except Exception as exc:
            on_done(False, str(exc), None)
