from pathlib import Path
import threading

import character_library as library_module
from character_library import CharacterLibrary
from pet_harness.asset.asset_job_worker import AssetJobWorker
from pet_harness.asset.asset_models import JobStatus
from pet_harness.asset.asset_orchestrator import AssetOrchestrator
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.storage.sqlite_store import SQLiteStore


ROOT = Path(__file__).parents[1] / "ComfyUI_Json"


class FakeClient:
    def __init__(self, history):
        self.history = history

    def upload_image(self, path, subfolder):
        return f"upload/{Path(path).name}"

    def submit_prompt(self, workflow):
        return "prompt-1"

    def watch_prompt(self, prompt_id, timeout_sec):
        return self.history

    def get_history(self, prompt_id):
        return self.history

    def has_prompt(self, prompt_id):
        return False

    def download_output(self, filename, subfolder="", output_type="output"):
        return b"png"


def _worker(tmp_path, monkeypatch, history):
    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    monkeypatch.setattr(library_module, "LEGACY_CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "webm" / "characters")
    monkeypatch.setattr(library_module, "UI_MUSIC_DIR", tmp_path / "ui" / "assets" / "music")
    source = tmp_path / "source.png"
    source.write_bytes(b"png")
    library = CharacterLibrary()
    library.create_validated_character("char-1", str(source), "Character")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(
        AssetRepository(store), ROOT / "AIA_2026_video_gen_260728.json", ROOT / "AIA_2026_image_gen_260720.json",
        max_retries=1, validation_template=ROOT / "AIA_2026_character validation_260811_API.json",
    )
    return orchestrator, AssetJobWorker(orchestrator.repository, orchestrator, FakeClient(history), library), source


def test_timed_out_job_retries_until_its_limit(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}})
    retryable = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    orchestrator.repository.update(retryable.job_id, JobStatus.TIMED_OUT)
    exhausted = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-2")
    orchestrator.repository.update(exhausted.job_id, JobStatus.TIMED_OUT, retry_count=1)

    assert worker.run_once() is True
    assert orchestrator.repository.get(retryable.job_id).status == JobStatus.COMPLETED
    assert orchestrator.repository.get(retryable.job_id).retry_count == 1
    assert orchestrator.repository.get(exhausted.job_id).status == JobStatus.TIMED_OUT


def test_worker_does_not_drain_the_same_queue_from_two_threads(tmp_path, monkeypatch):
    _orchestrator, worker, _source = _worker(tmp_path, monkeypatch, {"outputs": {}})
    entered, release = threading.Event(), threading.Event()

    def blocked_run_once():
        entered.set()
        release.wait(timeout=2)
        return False

    monkeypatch.setattr(worker, "_run_once", blocked_run_once)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert entered.wait(timeout=1)

    assert worker.run_once() is False

    release.set()
    thread.join(timeout=1)


def test_two_worker_instances_claim_a_job_only_once(tmp_path, monkeypatch):
    orchestrator, worker_one, source = _worker(tmp_path, monkeypatch, {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}})
    orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    submitted = []
    worker_one.client.submit_prompt = lambda workflow: submitted.append(workflow) or "prompt-1"
    worker_two = AssetJobWorker(orchestrator.repository, orchestrator, worker_one.client, worker_one.library)
    start = threading.Barrier(3)

    def run(worker):
        start.wait()
        worker.run_once()

    threads = [threading.Thread(target=run, args=(worker,)) for worker in (worker_one, worker_two)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert len(submitted) == 1


def test_recover_finalizes_interrupted_job_with_outputs(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}})
    job = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    orchestrator.repository.update(job.job_id, JobStatus.RUNNING, comfy_prompt_id="prompt-1")

    worker.recover()

    assert orchestrator.repository.get(job.job_id).status == JobStatus.COMPLETED
    assert (tmp_path / "assets" / "characters" / "char-1" / "images" / "development" / f"{job.job_id}.png").is_file()


def test_variant_fanout_failure_remains_recoverable(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}})
    job = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    original = orchestrator.create_motion_set
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("fan-out interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "create_motion_set", fail_once)

    worker.run_once()
    assert orchestrator.repository.get(job.job_id).status == JobStatus.TIMED_OUT

    worker.client.get_history = lambda _prompt_id: (_ for _ in ()).throw(AssertionError("local fan-out must not read Comfy history"))
    worker.recover()
    assert orchestrator.repository.get(job.job_id).status == JobStatus.COMPLETED


def test_drain_retries_fanout_without_resubmitting_png(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}})
    job = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    submitted = 0
    original_submit = worker.client.submit_prompt
    original_fanout = orchestrator.create_motion_set
    fanout_calls = 0

    def submit(workflow):
        nonlocal submitted
        submitted += 1
        return original_submit(workflow)

    def fail_once(*args, **kwargs):
        nonlocal fanout_calls
        fanout_calls += 1
        if fanout_calls == 1:
            raise RuntimeError("fan-out interrupted")
        return original_fanout(*args, **kwargs)

    worker.client.submit_prompt = submit
    monkeypatch.setattr(orchestrator, "create_motion_set", fail_once)

    assert worker.run_once() is True
    assert worker.run_once() is True

    assert submitted == 1
    assert orchestrator.repository.get(job.job_id).status == JobStatus.COMPLETED


def test_recover_and_run_once_share_the_same_store_lock(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {}})
    orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    entered, release = threading.Event(), threading.Event()

    def blocked_recover():
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(worker, "_recover", blocked_recover)
    thread = threading.Thread(target=worker.recover)
    thread.start()
    assert entered.wait(timeout=1)

    assert worker.run_once() is False

    release.set()
    thread.join(timeout=1)


def test_recover_fails_missing_outputs_and_validation_jobs(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {}})
    job = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    orchestrator.repository.update(job.job_id, JobStatus.RUNNING, comfy_prompt_id="prompt-1")

    worker.recover()
    assert orchestrator.repository.get(job.job_id).status == JobStatus.FAILED

    validation = orchestrator.create_character_validation_job(str(source), "New", "event-2")
    orchestrator.repository.update(validation.job_id, JobStatus.RUNNING, comfy_prompt_id="prompt-2")
    worker.client.history = {"outputs": {"16": {"images": [{"filename": "approved.png"}]}}}
    worker.recover()
    assert orchestrator.repository.get(validation.job_id).status == JobStatus.FAILED


def test_recover_releases_a_stale_prompt_and_allows_the_next_job(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {}})
    stale = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    orchestrator.repository.update(stale.job_id, JobStatus.RUNNING, comfy_prompt_id="missing-prompt")
    queued = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-2")

    worker.recover()

    assert orchestrator.repository.get(stale.job_id).status == JobStatus.FAILED
    worker.client.history = {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}}
    assert worker.run_once() is True
    assert orchestrator.repository.get(queued.job_id).status == JobStatus.COMPLETED


def test_recover_rechecks_history_after_prompt_leaves_queue(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {}})
    job = orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1")
    orchestrator.repository.update(job.job_id, JobStatus.RUNNING, comfy_prompt_id="just-finished")
    histories = iter([
        {"outputs": {}},
        {"outputs": {"467": {"images": [{"filename": "variant.png"}]}}},
    ])
    worker.client.get_history = lambda _prompt_id: next(histories)

    worker.recover()

    assert orchestrator.repository.get(job.job_id).status == JobStatus.COMPLETED


def test_recover_finalizes_a_stuck_motion_set_when_all_children_are_terminal(tmp_path, monkeypatch):
    orchestrator, worker, source = _worker(tmp_path, monkeypatch, {"outputs": {}})
    parent, children = orchestrator.create_motion_set("char-1", str(source), "event-1")
    for child in children:
        orchestrator.repository.update(child.job_id, JobStatus.FAILED)

    worker.recover()

    assert orchestrator.repository.get(parent.job_id).status == JobStatus.FAILED
