"""UC02 Customize 上傳建角的 service 層契約(character-validation-flow 的 UI 接點)。"""
from __future__ import annotations

import pytest

from pet_harness.ui.character_ui_service import CharacterUiService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("config.COMFYUI_ENABLED", False)
    return CharacterUiService(router=None, registry=None, customization_service=object())


def test_create_from_upload_rejects_missing_image(service):
    with pytest.raises(ValueError, match="請先選擇要上傳的圖片"):
        service.create_from_upload("no/such/image.png", "丘比")


def test_create_from_upload_rejects_blank_name(service, tmp_path):
    image = tmp_path / "upload.png"
    image.write_bytes(b"png")
    with pytest.raises(ValueError, match="請輸入角色名稱"):
        service.create_from_upload(str(image), "   ")


def test_create_from_upload_queues_validation_job(service, tmp_path):
    image = tmp_path / "upload.png"
    image.write_bytes(b"png")
    result = service.create_from_upload(str(image), "丘比")
    assert result["status"] in {"queued", "completed"}
    assert result["job_id"]


def test_get_validation_status_unknown_job_raises(service):
    with pytest.raises(ValueError, match="validation job not found"):
        service.get_validation_status("ghost-job")


class _FakeLibrary:
    def __init__(self, idle: str | None, background: str | None) -> None:
        self._idle, self._background = idle, background

    def get_motion_path(self, _character_id: str, _motion_key: str) -> str | None:
        return self._idle

    def get_background_path(self, _character_id: str) -> str | None:
        return self._background


def _completed_validation_job(service):
    from pet_harness.asset.asset_models import AssetJob, JobStatus
    from pet_harness.asset.asset_repository import AssetRepository

    repo = AssetRepository(service._asset_store())
    job = repo.create_job(AssetJob("newchar", "character_validation", "og", "key-ready"))
    repo.update(job.job_id, JobStatus.COMPLETED)
    return job


def test_validation_status_not_ready_until_idle_and_background(service, monkeypatch):
    """過審 ≠ 可用:idle WebM 與背景都齊之前,assets_ready 必須是 False。"""
    job = _completed_validation_job(service)
    monkeypatch.setattr(
        "pet_harness.ui.character_ui_service.CharacterLibrary",
        lambda: _FakeLibrary(idle=None, background=None),
    )
    assert service.get_validation_status(job.job_id)["assets_ready"] is False


def test_validation_status_ready_when_idle_and_background_exist(service, monkeypatch):
    job = _completed_validation_job(service)
    monkeypatch.setattr(
        "pet_harness.ui.character_ui_service.CharacterLibrary",
        lambda: _FakeLibrary(idle="motions/idle.webm", background="images/bg/bg.png"),
    )
    assert service.get_validation_status(job.job_id)["assets_ready"] is True
