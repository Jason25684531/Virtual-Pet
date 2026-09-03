from __future__ import annotations

import hashlib
import shutil
import threading
import time
from pathlib import Path

import character_library as library_module
from character_library import CharacterLibrary
from pet_harness.asset.asset_models import GeneratedAsset, JobStatus
from pet_harness.models.events import utc_now
from pet_harness.storage.sqlite_store import SQLiteStore


class MockRenderWorker:
    """Small async renderer that exercises the same persisted job/UI path as ComfyUI."""

    _FIXED_GENERATIONS = {"development_a": 1, "development_b": 2}

    def __init__(self, store: SQLiteStore, library: CharacterLibrary, duration_sec: float) -> None:
        self.store = store
        self.library = library
        self.duration_sec = max(0.0, float(duration_sec))
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def run_job(self, job_id: str, variant: str, character_id: str, preset: str | None) -> threading.Thread:
        cancelled = threading.Event()
        with self._lock:
            self._events[job_id] = cancelled
        thread = threading.Thread(
            target=self._run,
            args=(job_id, variant, character_id, preset, cancelled),
            daemon=True,
            name=f"mock-render-{job_id}",
        )
        thread.start()
        return thread

    def cancel(self) -> None:
        with self._lock:
            jobs = list(self._events.items())
        for job_id, event in jobs:
            event.set()
            self.store.update_asset_job_status(
                job_id,
                JobStatus.FAILED.value,
                error_code="reset",
                error_message="reset",
                stage="failed",
            )
            self._update_manifest(job_id, JobStatus.FAILED.value, error_message="reset")
        if jobs:
            self.store.set_setting("asset_generation_freeze", None)

    def _run(
        self,
        job_id: str,
        variant: str,
        character_id: str,
        preset: str | None,
        cancelled: threading.Event,
    ) -> None:
        try:
            self._update(job_id, "rendering", 0, 100, cancelled)
            if not self._wait_progress(job_id, cancelled):
                return
            if cancelled.is_set():
                return
            if preset is None:
                self._complete(job_id, None, None)
                return

            motions_root = Path(self.library.get_motions_dir_path(character_id))
            prebuilt = motions_root / variant / "idle.webm"
            if prebuilt.is_file():
                # Preset characters (such as Adol) already contain their
                # approved motion set; the mock job only simulates rendering.
                self._complete(job_id, str(prebuilt), variant)
                return

            source = library_module.PROJECT_ROOT / "assets" / "presets" / preset / "idle.webm"
            if not source.is_file():
                self._fail(job_id, f"mock preset missing: {preset}")
                return
            self._update(job_id, "saving", 90, 100, cancelled)
            if cancelled.is_set():
                return
            destination_variant = variant
            generation = self._FIXED_GENERATIONS.get(preset or "")
            if generation is None:
                generation = self.store.allocate_generation(character_id, destination_variant, motions_root.parent)
            destination = motions_root / destination_variant / f"g{generation:02d}" / "idle.webm"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if cancelled.is_set():
                destination.unlink(missing_ok=True)
                return
            content = destination.read_bytes()
            self.store.insert_character_asset(GeneratedAsset(
                character_id,
                "motion_webm",
                destination_variant,
                destination.as_posix(),
                destination.name,
                "video/webm",
                hashlib.sha256(content).hexdigest(),
                job_id,
                motion_key="idle",
                generation_index=generation,
            ).__dict__)
            self.library.register_generated_assets(character_id, {"idle": str(destination)})
            self.library.auto_select_wearable_generation(character_id, destination_variant)
            self._complete(job_id, str(destination), destination_variant)
        except Exception as exc:  # noqa: BLE001
            if not cancelled.is_set():
                self._fail(job_id, str(exc))
        finally:
            with self._lock:
                self._events.pop(job_id, None)

    def _wait_progress(self, job_id: str, cancelled: threading.Event) -> bool:
        duration = self.duration_sec
        if duration <= 0:
            self._update(job_id, "rendering", 80, 100, cancelled)
            return not cancelled.is_set()
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._update(job_id, "rendering", 80, 100)
                return not cancelled.is_set()
            elapsed = duration - remaining
            value = min(80, max(1, int(elapsed / duration * 80)))
            self._update(job_id, "rendering", value, 100, cancelled)
            if cancelled.wait(min(0.1, remaining)):
                return False

    def _update(
        self,
        job_id: str,
        stage: str,
        value: int,
        maximum: int,
        cancelled: threading.Event | None = None,
    ) -> None:
        if cancelled is not None and cancelled.is_set():
            return
        self.store.update_asset_job_status(
            job_id,
            JobStatus.RUNNING.value,
            stage=stage,
            progress_value=value,
            progress_max=maximum,
        )

    def _complete(self, job_id: str, file_path: str | None, variant: str | None) -> None:
        changes = {"completed_at": utc_now(), "stage": "completed", "progress_value": 100, "progress_max": 100}
        job = self.store.get_asset_job(job_id)
        if job:
            metadata = dict(job.get("metadata") or {})
            metadata["file_path"] = file_path
            if variant:
                metadata["variant"] = variant
            self.store.update_asset_job_status(job_id, JobStatus.COMPLETED.value, metadata=metadata, **changes)
        self._update_manifest(job_id, JobStatus.COMPLETED.value, file_path=file_path)
        self.store.set_setting("asset_generation_freeze", None)

    def _fail(self, job_id: str, message: str) -> None:
        self.store.update_asset_job_status(
            job_id,
            JobStatus.FAILED.value,
            error_code="mock_render_failed",
            error_message=message,
            stage="failed",
        )
        self._update_manifest(job_id, JobStatus.FAILED.value, error_message=message)
        self.store.set_setting("asset_generation_freeze", None)

    def _update_manifest(self, job_id: str, status: str, **changes: str | None) -> None:
        job = self.store.get_asset_job(job_id)
        request_id = (job or {}).get("metadata", {}).get("request_id")
        if not request_id:
            return
        self.store.update_asset_manifest(request_id, status, **changes)
