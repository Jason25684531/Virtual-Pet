from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from pet_harness.asset.asset_models import AssetJob, JobStatus
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.workflow_patcher import WorkflowPatcher


class AssetOrchestrator:
    def __init__(self, repository: AssetRepository, video_template: str | Path, image_template: str | Path, timeout_sec: int = 300, max_retries: int = 2) -> None:
        self.repository, self.video, self.image = repository, WorkflowPatcher(video_template), WorkflowPatcher(image_template)
        self.timeout_sec, self.max_retries = timeout_sec, max_retries

    @staticmethod
    def _key(*parts: str | None) -> str:
        return hashlib.sha256("|".join(str(part or "") for part in parts).encode()).hexdigest()

    def create_motion_set(self, character_id: str, source_path: str, trigger_id: str, variant: str = "og") -> tuple[AssetJob, list[AssetJob]]:
        parent = self.repository.create_job(AssetJob(character_id, "motion_set", variant, self._key(character_id, "motion_set", variant, trigger_id), timeout_sec=self.timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path}))
        children = []
        for motion_key, slot, _node in self.video.parse_active_motions():
            child = AssetJob(character_id, "motion_clip", variant, self._key(character_id, "motion_clip", variant, motion_key, trigger_id), parent_job_id=parent.job_id, motion_key=motion_key, timeout_sec=self.timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path, "motion_slot": slot, "seed": secrets.randbits(63)})
            children.append(self.repository.create_job(child))
        return parent, children

    def create_variant_png_job(self, character_id: str, source_path: str, variant: str, trigger_id: str, **metadata: str) -> AssetJob:
        return self.repository.create_job(AssetJob(character_id, "variant_png", variant, self._key(character_id, "variant_png", variant, trigger_id), timeout_sec=self.timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path, "seed": secrets.randbits(63), **metadata}))

    def aggregate_parent(self, parent_id: str) -> None:
        children = self.repository.children(parent_id)
        parent = self.repository.get(parent_id)
        if not parent or not children:
            return
        statuses = {job.status for job in children}
        if statuses <= {JobStatus.COMPLETED}:
            self.repository.update(parent_id, JobStatus.COMPLETED)
        elif JobStatus.COMPLETED in statuses and statuses <= {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMED_OUT}:
            self.repository.update(parent_id, JobStatus.PARTIALLY_COMPLETED)
        elif statuses <= {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMED_OUT}:
            self.repository.update(parent_id, JobStatus.FAILED)
