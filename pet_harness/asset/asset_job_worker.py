from __future__ import annotations

import hashlib
from pathlib import Path

from character_library import CharacterLibrary
from pet_harness.asset.asset_models import GeneratedAsset, JobStatus
from pet_harness.asset.asset_orchestrator import AssetOrchestrator
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.comfyui_client import BaseComfyUIClient, ComfyUIClient
from pet_harness.models.events import utc_now


class AssetJobWorker:
    def __init__(self, repository: AssetRepository, orchestrator: AssetOrchestrator, client: BaseComfyUIClient, library: CharacterLibrary | None = None) -> None:
        self.repository, self.orchestrator, self.client, self.library = repository, orchestrator, client, library or CharacterLibrary()

    def run_once(self) -> bool:
        jobs = [job for job in self.repository.pending() if job.status == JobStatus.QUEUED and job.workflow_type != "motion_set"]
        if not jobs:
            return False
        self._run(jobs[0])
        return True

    def _run(self, job) -> None:
        try:
            self.repository.update(job.job_id, JobStatus.UPLOADING, started_at=utc_now())
            image = self.client.upload_image(job.metadata["source_path"], f"{job.character_id}/{job.job_id}")
            if job.workflow_type == "motion_clip":
                workflow = self.orchestrator.video.patch_video(image=image, selector=1, motion_slot=int(job.metadata["motion_slot"]), seed=int(job.metadata["seed"]), prefix=f"vp/{job.character_id}/{job.job_id}/{job.motion_key}")
                node_id, suffix, asset_type = "742", ".webm", "motion_webm"
            else:
                workflow = self.orchestrator.image.patch_image(image=image, variant=job.variant, seed=int(job.metadata["seed"]), prefix=f"vp/{job.character_id}/{job.job_id}", generation_context=str(job.metadata.get("generation_context", "")))
                node_id, suffix, asset_type = {"og": ("475", ".png", "character_variant_png"), "development": ("467", ".png", "character_variant_png"), "event": ("492", ".png", "character_variant_png")}[job.variant]
            prompt_id = self.client.submit_prompt(workflow)
            self.repository.update(job.job_id, JobStatus.RUNNING, comfy_prompt_id=prompt_id)
            output = ComfyUIClient.output(self.client.watch_prompt(prompt_id, job.timeout_sec), node_id)
            content = self.client.download_output(output["filename"], output.get("subfolder", ""), output.get("type", "output"))
            self._save(job, content, suffix, asset_type)
            self.repository.update(job.job_id, JobStatus.COMPLETED, completed_at=utc_now())
            if job.parent_job_id:
                self.orchestrator.aggregate_parent(job.parent_job_id)
        except TimeoutError as error:
            self.repository.update(job.job_id, JobStatus.TIMED_OUT, error_code="timeout", error_message=str(error))
        except Exception as error:
            self.repository.update(job.job_id, JobStatus.FAILED, error_code="execution_error", error_message=str(error))

    def _save(self, job, content: bytes, suffix: str, asset_type: str) -> None:
        root = Path("assets") / "characters" / job.character_id
        if job.workflow_type == "motion_clip":
            path = root / "motions" / job.variant / f"{job.motion_key}{suffix}"
        else:
            name = job.metadata.get("event_id") or job.metadata.get("reward_id") or job.job_id
            path = root / "images" / job.variant / f"{name}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(content)
        temp.replace(path)
        self.repository.save_asset(GeneratedAsset(job.character_id, asset_type, job.variant, path.as_posix(), path.name, "video/webm" if suffix == ".webm" else "image/png", hashlib.sha256(content).hexdigest(), job.job_id, motion_key=job.motion_key, reward_id=job.metadata.get("reward_id"), event_id=job.metadata.get("event_id")))
        if job.motion_key:
            self.library.register_generated_assets(job.character_id, {job.motion_key: str(path)})

    def recover(self) -> None:
        for job in self.repository.pending():
            if job.status == JobStatus.QUEUED:
                continue
            if not job.comfy_prompt_id:
                self.repository.update(job.job_id, JobStatus.FAILED, error_code="recovery_unresolved", error_message="missing prompt id")
                continue
            try:
                history = self.client.get_history(job.comfy_prompt_id)
                if not history.get("outputs"):
                    self.repository.update(job.job_id, JobStatus.FAILED, error_code="recovery_unresolved", error_message="prompt missing from history")
            except Exception as error:
                self.repository.update(job.job_id, JobStatus.FAILED, error_code="comfyui_unavailable", error_message=str(error))
