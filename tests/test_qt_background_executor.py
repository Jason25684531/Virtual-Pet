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


def test_job_submitted_from_inside_another_job_still_delivers_its_callback():
    """對話回合（job）內再送出慢速工具（job）。Nuitka 版曾因每個 job 各自 connect 而永遠收不到
    內層 callback；此測試在 CPython 下鎖住「完成訊號只在 executor 建構時連一次」的契約。"""
    import time
    from PyQt5.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    executor = QtBackgroundExecutor()
    results = []

    def outer():
        executor.submit(lambda: (time.sleep(0.2), "inner")[1], lambda ok, _m, payload: results.append(payload))
        return "outer"

    executor.submit(outer, lambda ok, _m, payload: results.append(payload))
    deadline = time.monotonic() + 5
    while len(results) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    executor.stop(1000)
    assert sorted(results) == ["inner", "outer"]
    assert not hasattr(executor, "_jobs") or not executor._jobs
