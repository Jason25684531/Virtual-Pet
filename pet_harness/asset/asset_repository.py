from __future__ import annotations

from typing import Any

from pet_harness.asset.asset_models import AssetJob, GeneratedAsset
from pet_harness.storage.sqlite_store import SQLiteStore


class AssetRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def create_job(self, job: AssetJob) -> AssetJob:
        existing = self.store.find_job_by_idempotency_key(job.idempotency_key)
        if existing:
            return self._job(existing)
        self.store.insert_asset_job(job.to_dict())
        return job

    def get(self, job_id: str) -> AssetJob | None:
        item = self.store.get_asset_job(job_id)
        return self._job(item) if item else None

    def find(self, key: str) -> AssetJob | None:
        item = self.store.find_job_by_idempotency_key(key)
        return self._job(item) if item else None

    def update(self, job_id: str, status: str, **changes: Any) -> None:
        self.store.update_asset_job_status(job_id, status, **changes)

    def pending(self) -> list[AssetJob]:
        return [self._job(item) for item in self.store.list_pending_asset_jobs()]

    def claim(self, job: AssetJob) -> bool:
        from pet_harness.asset.asset_models import JobStatus
        from pet_harness.models.events import utc_now
        retry_count = job.retry_count + (job.status == JobStatus.TIMED_OUT)
        return self.store.claim_asset_job(job.job_id, job.status.value, retry_count, utc_now())

    def children(self, parent_job_id: str) -> list[AssetJob]:
        return [self._job(item) for item in self.store.list_asset_jobs_by_parent(parent_job_id)]

    def save_asset(self, asset: GeneratedAsset) -> dict[str, Any]:
        return self.store.insert_character_asset(asset.__dict__)

    @staticmethod
    def _job(data: dict[str, Any]) -> AssetJob:
        from pet_harness.asset.asset_models import JobStatus
        data = dict(data)
        data["status"] = JobStatus(data["status"])
        return AssetJob(**data)
