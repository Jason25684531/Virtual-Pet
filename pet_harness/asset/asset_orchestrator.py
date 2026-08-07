from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

from pet_harness.asset.asset_models import AssetJob, JobStatus
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.workflow_patcher import WorkflowPatcher


class AssetOrchestrator:
    def __init__(self, repository: AssetRepository, video_template: str | Path, image_template: str | Path, timeout_sec: int = 300, max_retries: int = 2, validation_template: str | Path | None = None, background_template: str | Path | None = None, video_timeout_sec: int | None = None) -> None:
        self.repository, self.video, self.image = repository, WorkflowPatcher(video_template), WorkflowPatcher(image_template)
        self.validation = WorkflowPatcher(validation_template) if validation_template else None
        self.background = WorkflowPatcher(background_template) if background_template else None
        self.timeout_sec, self.max_retries = timeout_sec, max_retries
        self.video_timeout_sec = video_timeout_sec or timeout_sec

    @staticmethod
    def _key(*parts: str | None) -> str:
        return hashlib.sha256("|".join(str(part or "") for part in parts).encode()).hexdigest()

    def create_motion_set(self, character_id: str, source_path: str, trigger_id: str, variant: str = "og", trigger_reason: str = "") -> tuple[AssetJob, list[AssetJob]]:
        extra = {"trigger_reason": trigger_reason} if trigger_reason else {}
        parent = self.repository.create_job(AssetJob(character_id, "motion_set", variant, self._key(character_id, "motion_set", variant, trigger_id), timeout_sec=self.video_timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path, **extra}))
        children = []
        # idle 排最前:桌寵最先需要的是待機動態,其餘六隻在背景補(單隻約 9 分鐘)。
        for motion_key, slot, _node in sorted(self.video.parse_active_motions(), key=lambda item: item[0] != "idle"):
            child = AssetJob(character_id, "motion_clip", variant, self._key(character_id, "motion_clip", variant, motion_key, trigger_id), parent_job_id=parent.job_id, motion_key=motion_key, timeout_sec=self.video_timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path, "motion_slot": slot, "seed": secrets.randbits(63), **extra})
            children.append(self.repository.create_job(child))
        return parent, children

    def create_variant_png_job(self, character_id: str, source_path: str, variant: str, trigger_id: str, **metadata: str) -> AssetJob:
        return self.repository.create_job(AssetJob(character_id, "variant_png", variant, self._key(character_id, "variant_png", variant, trigger_id), timeout_sec=self.timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path, "seed": secrets.randbits(63), **metadata}))

    def create_character_validation_job(self, upload_path: str, character_name: str, trigger_id: str) -> AssetJob:
        if self.validation is None:
            raise RuntimeError("character validation workflow is not configured")
        from character_library import CharacterLibrary

        # id = char-{名稱slug};同名已存在則加版本尾碼 char-{slug}-2、-3…。
        # 中文名先音譯(亞洲統神 → ya-zhou-tong-shen);音譯不可用或結果為空才退回隨機碼。
        try:
            from unidecode import unidecode
            ascii_name = unidecode(character_name)
        except ImportError:
            ascii_name = character_name
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
        base = f"char-{slug}" if slug else f"char-{secrets.token_hex(4)}"
        library, character_id, version = CharacterLibrary(), base, 2
        while library.get_character(character_id):
            character_id = f"{base}-{version}"
            version += 1
        return self.repository.create_job(AssetJob(character_id, "character_validation", "og", self._key("character_validation", upload_path, character_name, trigger_id), timeout_sec=self.timeout_sec, max_retries=self.max_retries, metadata={"source_path": upload_path, "character_name": character_name, "seed": secrets.randbits(63)}))

    def create_background_job(self, character_id: str, source_path: str, room_path: str, trigger_id: str) -> AssetJob:
        if self.background is None:
            raise RuntimeError("background workflow is not configured")
        return self.repository.create_job(AssetJob(character_id, "background_png", "bg", self._key(character_id, "background_png", trigger_id), timeout_sec=self.timeout_sec, max_retries=self.max_retries, metadata={"source_path": source_path, "room_path": room_path}))

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
