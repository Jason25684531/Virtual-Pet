from __future__ import annotations

import hashlib
from pathlib import Path

import character_library as library_module
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
        jobs = [
            job for job in self.repository.pending()
            if job.workflow_type != "motion_set"
            and (job.status == JobStatus.QUEUED or job.status == JobStatus.TIMED_OUT and job.retry_count < job.max_retries)
        ]
        if not jobs:
            return False
        # 優先序:審核 → idle 動態 → 背景 → 其餘動態;就緒閘門(idle+背景)最快放行。
        tier = {"character_validation": 0, "background_png": 2}
        jobs.sort(key=lambda job: 1 if job.motion_key == "idle" else tier.get(job.workflow_type, 3))
        job = jobs[0]
        if job.status == JobStatus.TIMED_OUT:
            self.repository.update(job.job_id, JobStatus.TIMED_OUT, retry_count=job.retry_count + 1)
        self._run(job)
        return True

    def _run(self, job) -> None:
        try:
            self.repository.update(job.job_id, JobStatus.UPLOADING, started_at=utc_now())
            if job.workflow_type == "character_validation":
                self._run_validation(job)
                return
            image = self.client.upload_image(job.metadata["source_path"], f"{job.character_id}/{job.job_id}")
            if job.workflow_type == "motion_clip":
                workflow = self.orchestrator.video.patch_video(image=image, selector=1, motion_slot=int(job.metadata["motion_slot"]), seed=int(job.metadata["seed"]), prefix=f"vp/{job.character_id}/{job.job_id}/{job.motion_key}")
                node_id, suffix, asset_type = "770", ".webm", "motion_webm"
            elif job.workflow_type == "background_png":
                room = self.client.upload_image(job.metadata["room_path"], f"{job.character_id}/{job.job_id}")
                workflow = self.orchestrator.background.patch_background(character_image=image, room_image=room, prefix=f"vp/{job.character_id}/{job.job_id}")
                node_id, suffix, asset_type = "16", ".png", "character_background_png"
            else:
                workflow = self.orchestrator.image.patch_image(image=image, variant=job.variant, seed=int(job.metadata["seed"]), prefix=f"vp/{job.character_id}/{job.job_id}", generation_context=str(job.metadata.get("generation_context", "")))
                node_id, suffix, asset_type = {"og": ("475", ".png", "character_variant_png"), "development": ("467", ".png", "character_variant_png"), "event": ("492", ".png", "character_variant_png")}[job.variant]
            prompt_id = self.client.submit_prompt(workflow)
            self.repository.update(job.job_id, JobStatus.RUNNING, comfy_prompt_id=prompt_id)
            output = ComfyUIClient.output(self.client.watch_prompt(prompt_id, job.timeout_sec), node_id)
            self._finalize(job, output, suffix, asset_type)
        except TimeoutError as error:
            self.repository.update(job.job_id, JobStatus.TIMED_OUT, error_code="timeout", error_message=str(error))
        except Exception as error:
            self.repository.update(job.job_id, JobStatus.FAILED, error_code="execution_error", error_message=str(error))

    def _run_validation(self, job) -> None:
        image = self.client.upload_image(job.metadata["source_path"], f"validation/{job.job_id}")
        workflow = self.orchestrator.validation.patch_validation(image=image, prefix=f"vp/{job.character_id}/{job.job_id}")
        prompt_id = self.client.submit_prompt(workflow)
        self.repository.update(job.job_id, JobStatus.RUNNING, comfy_prompt_id=prompt_id)
        history = self.client.watch_prompt(prompt_id, job.timeout_sec)
        output = history.get("outputs", {}).get("16", {})
        if not output.get("images"):
            text = history.get("outputs", {}).get("30", {}).get("text", [""])
            reason = str(text[0] if isinstance(text, list) and text else text or "")
            self.repository.update(job.job_id, JobStatus.FAILED, error_code="rejected", error_message=f"照片不符規定請重新上傳：{reason}")
            return
        image_output = output["images"][0]
        content = self.client.download_output(image_output["filename"], image_output.get("subfolder", ""), image_output.get("type", "output"))
        temp = library_module.PROJECT_ROOT / "assets" / ".validation" / f"{job.job_id}.png"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(content)
        gender_output = history.get("outputs", {}).get("56", {}).get("text", "")
        if isinstance(gender_output, list):
            gender_output = gender_output[0] if gender_output else ""
        voice_gender = str(gender_output).strip()[:1].upper()
        voice_gender = voice_gender if voice_gender in {"F", "M"} else ""
        try:
            manifest = self.library.create_validated_character(job.character_id, str(temp), str(job.metadata.get("character_name", "")), voice_gender)
        finally:
            temp.unlink(missing_ok=True)
        source_path = str(library_module.PROJECT_ROOT / manifest["source_image"])
        self.orchestrator.create_motion_set(job.character_id, source_path, job.job_id)
        self.orchestrator.create_background_job(job.character_id, source_path, "assets/backgrounds/default_room.jpg", job.job_id)
        self.repository.update(job.job_id, JobStatus.COMPLETED, completed_at=utc_now())

    def _save(self, job, content: bytes, suffix: str, asset_type: str) -> None:
        path = self._asset_path(job, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(content)
        temp.replace(path)
        self.repository.save_asset(GeneratedAsset(job.character_id, asset_type, job.variant, path.as_posix(), path.name, "video/webm" if suffix == ".webm" else "image/png", hashlib.sha256(content).hexdigest(), job.job_id, motion_key=job.motion_key, reward_id=job.metadata.get("reward_id"), event_id=job.metadata.get("event_id")))
        if job.motion_key:
            self.library.register_generated_assets(job.character_id, {job.motion_key: str(path)})
        if job.workflow_type == "background_png":
            self.library.set_background(job.character_id, str(path))

    def _finalize(self, job, output, suffix: str, asset_type: str) -> None:
        content = self.client.download_output(output["filename"], output.get("subfolder", ""), output.get("type", "output"))
        self._save(job, content, suffix, asset_type)
        self.repository.update(job.job_id, JobStatus.COMPLETED, completed_at=utc_now())
        if job.workflow_type == "variant_png":
            self.orchestrator.create_motion_set(job.character_id, str(self._asset_path(job, suffix)), job.job_id, job.variant, str(job.metadata.get("trigger_reason", "")))
        if job.parent_job_id:
            self.orchestrator.aggregate_parent(job.parent_job_id)

    @staticmethod
    def _output_info(job) -> tuple[str, str, str]:
        if job.workflow_type == "motion_clip":
            return "770", ".webm", "motion_webm"
        if job.workflow_type == "background_png":
            return "16", ".png", "character_background_png"
        return {"og": ("475", ".png", "character_variant_png"), "development": ("467", ".png", "character_variant_png"), "event": ("492", ".png", "character_variant_png")}[job.variant]

    @staticmethod
    def _asset_path(job, suffix: str) -> Path:
        root = library_module.PROJECT_ROOT / "assets" / "characters" / job.character_id
        if job.workflow_type == "motion_clip":
            return root / "motions" / job.variant / f"{job.motion_key}{suffix}"
        elif job.workflow_type == "background_png":
            return root / "images" / "bg" / f"{job.job_id}{suffix}"
        name = job.metadata.get("event_id") or job.metadata.get("reward_id") or job.job_id
        return root / "images" / job.variant / f"{name}{suffix}"

    def recover(self) -> None:
        for job in self.repository.pending():
            if job.status == JobStatus.QUEUED:
                if job.workflow_type == "motion_set":
                    self.orchestrator.aggregate_parent(job.job_id)
                continue
            if job.workflow_type == "character_validation":
                self.repository.update(job.job_id, JobStatus.FAILED, error_code="recovery_unresolved", error_message="validation job interrupted")
                continue
            if not job.comfy_prompt_id:
                self.repository.update(job.job_id, JobStatus.FAILED, error_code="recovery_unresolved", error_message="missing prompt id")
                continue
            try:
                history = self.client.get_history(job.comfy_prompt_id)
                if not history.get("outputs"):
                    self.repository.update(job.job_id, JobStatus.FAILED, error_code="recovery_unresolved", error_message="prompt missing from history")
                    continue
                node_id, suffix, asset_type = self._output_info(job)
                self._finalize(job, ComfyUIClient.output(history, node_id), suffix, asset_type)
            except Exception as error:
                self.repository.update(job.job_id, JobStatus.FAILED, error_code="comfyui_unavailable", error_message=str(error))
