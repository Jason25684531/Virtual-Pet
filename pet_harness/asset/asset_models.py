from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from pet_harness.models.events import new_id, utc_now


class JobStatus(StrEnum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class AssetJob:
    character_id: str
    workflow_type: str
    variant: str
    idempotency_key: str
    job_id: str = field(default_factory=lambda: new_id("asset-job"))
    parent_job_id: str | None = None
    motion_key: str | None = None
    status: JobStatus = JobStatus.QUEUED
    comfy_prompt_id: str | None = None
    retry_count: int = 0
    max_retries: int = 2
    timeout_sec: int = 300
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class GeneratedAsset:
    character_id: str
    asset_type: str
    variant: str
    file_path: str
    filename: str
    mime_type: str
    sha256: str
    source_job_id: str
    asset_id: str = field(default_factory=lambda: new_id("character-asset"))
    motion_key: str | None = None
    reward_id: str | None = None
    level: int | None = None
    event_id: str | None = None
    created_at: str = field(default_factory=utc_now)
