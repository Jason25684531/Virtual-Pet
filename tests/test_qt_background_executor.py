import pytest
from threading import Event

from pet_harness.app.runtime_lifecycle import CallbackRuntime, RuntimeLifecycle
from pet_harness.runtime.qt_background_executor import QtBackgroundExecutor


class _Worker:
    def __init__(self, events, running=True, completed=True):
        self.events, self.running, self.completed = events, running, completed
        self.waits = []

    def isRunning(self):
        return self.running

    def wait(self, wait_ms):
        self.waits.append(wait_ms)
        self.events.append("executor")
        return self.completed


def test_executor_stop_rejects_work_and_precedes_router_shutdown():
    events, executor = [], QtBackgroundExecutor()
    worker = _Worker(events)
    executor._jobs[worker] = lambda *_args: None
    lifecycle = RuntimeLifecycle()
    lifecycle.register(CallbackRuntime("router", lambda _wait: events.append("router")))
    lifecycle.register(executor)

    lifecycle.shutdown_all(10)
    executor.shutdown(10)

    assert events == ["executor", "router", "executor"]
    assert worker.waits[0] <= 10
    with pytest.raises(RuntimeError, match="shutting down"):
        executor.submit(lambda: None, lambda *_args: None)


def test_executor_bounded_stop_keeps_running_qthread_referenced_until_it_finishes():
    executor, started, release = QtBackgroundExecutor(), Event(), Event()

    def job():
        started.set()
        release.wait(1)
        return {}

    executor.submit(job, lambda *_args: None)
    assert started.wait(1)
    executor.stop(1)
    assert any(worker.isRunning() for worker in executor._jobs)
    release.set()
    executor.stop(1000)
    assert all(not worker.isRunning() for worker in executor._jobs)
