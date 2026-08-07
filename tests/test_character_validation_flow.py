from pathlib import Path

from character_library import CharacterLibrary
from pet_harness.asset.asset_job_worker import AssetJobWorker
from pet_harness.asset.asset_orchestrator import AssetOrchestrator
from pet_harness.asset.asset_repository import AssetRepository
from pet_harness.asset.asset_models import JobStatus
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.storage.sqlite_store import SQLiteStore


class FakeClient:
    def __init__(self, history):
        self.history = history

    def upload_image(self, path, subfolder):
        return f"upload/{Path(path).name}"

    def submit_prompt(self, workflow):
        return "prompt-1"

    def watch_prompt(self, prompt_id, timeout_sec):
        return self.history

    def download_output(self, filename, subfolder="", output_type="output"):
        return b"png"


def test_validation_approval_creates_character_and_fans_out(tmp_path, monkeypatch):
    import character_library as library_module

    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "upload.png"
    source.write_bytes(b"input")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(
        AssetRepository(store),
        Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json",
        Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json",
        validation_template=Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_character validation_260728.json",
        background_template=Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_background_gen_260728.json",
    )
    job = orchestrator.create_character_validation_job(str(source), "丘比", "event-1")
    worker = AssetJobWorker(orchestrator.repository, orchestrator, FakeClient({"outputs": {"16": {"images": [{"filename": "approved.png"}]}}}), CharacterLibrary())

    assert worker.run_once() is True
    completed = orchestrator.repository.get(job.job_id)
    assert completed.status == JobStatus.COMPLETED
    manifest = CharacterLibrary().get_character(completed.character_id)
    assert manifest["name"] == "丘比"
    assert Path(tmp_path / manifest["source_image"]).read_bytes() == b"png"
    assert len(orchestrator.repository.children(next(item.job_id for item in orchestrator.repository.pending() if item.workflow_type == "motion_set"))) == 7
    assert any(item.workflow_type == "background_png" for item in orchestrator.repository.pending())


def test_validation_rejection_leaves_no_character(tmp_path, monkeypatch):
    import character_library as library_module

    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "upload.png"
    source.write_bytes(b"input")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(AssetRepository(store), Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json", Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json", validation_template=Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_character validation_260728.json")
    job = orchestrator.create_character_validation_job(str(source), "Nope", "event-1")

    AssetJobWorker(orchestrator.repository, orchestrator, FakeClient({"outputs": {"30": {"text": ["not a character"]}}}), CharacterLibrary()).run_once()

    failed = orchestrator.repository.get(job.job_id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_message == "照片不符規定請重新上傳：not a character"
    assert CharacterLibrary().get_character(failed.character_id) is None


def test_character_id_uses_name_slug_with_version_suffix(tmp_path, monkeypatch):
    import character_library as library_module

    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "upload.png"
    source.write_bytes(b"input")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(AssetRepository(store), Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json", Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json", validation_template=Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_character validation_260728.json")

    job = orchestrator.create_character_validation_job(str(source), "Chopper", "event-1")
    assert job.character_id == "char-chopper"

    # 同名角色已存在 → 版本尾碼遞增
    CharacterLibrary().create_validated_character("char-chopper", str(source), "Chopper")
    job2 = orchestrator.create_character_validation_job(str(source), "Chopper", "event-2")
    assert job2.character_id == "char-chopper-2"

    # 中文名稱音譯成拼音 slug(2026-08-07 實測回饋:使用者輸入「亞洲統神」得到隨機碼)
    import pytest as _pytest
    _pytest.importorskip("unidecode")
    job3 = orchestrator.create_character_validation_job(str(source), "亞洲統神", "event-3")
    assert job3.character_id == "char-ya-zhou-tong-shen"


def test_patch_video_touches_only_declared_nodes():
    """去背子圖(752:*)與其他節點保證原封不動——回應「是否不小心注入 prompt」的疑慮。"""
    from pet_harness.asset.workflow_patcher import WorkflowPatcher

    patcher = WorkflowPatcher(Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json")
    patched = patcher.patch_video(image="x.png", selector=1, motion_slot=2, seed=1, prefix="pre")
    template = patcher.fresh()
    declared = {"733", "728", "730", "735", "756", "708", "770"}
    secret_nodes = {node_id for node_id, node in template.items() if node.get("class_type") in ("Azure_ChatGPT_Node", "ParallelGen_Azure_EditSubmit")}
    for node_id, node in template.items():
        if node_id in declared | secret_nodes:
            continue
        assert patched[node_id] == node, f"node {node_id} was unexpectedly modified"
    assert patched["752:100"]["inputs"]["text"] == template["752:100"]["inputs"]["text"]


def test_worker_runs_idle_then_background_before_other_motions(tmp_path, monkeypatch):
    """優先序:idle 動態 → 背景 → 其餘動態,使用者最快通過就緒閘門進場。"""
    import character_library as library_module

    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "og.png"
    source.write_bytes(b"og")
    room = tmp_path / "room.jpg"
    room.write_bytes(b"room")
    library = CharacterLibrary()
    library.create_validated_character("char-1", str(source), "Char")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(AssetRepository(store), Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json", Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json", background_template=Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_background_gen_260728.json")
    orchestrator.create_motion_set("char-1", str(source), "event-1")
    orchestrator.create_background_job("char-1", str(source), str(room), "event-1")
    history = {"outputs": {"770": {"gifs": [{"filename": "clip.webm"}]}, "16": {"images": [{"filename": "bg.png"}]}}}
    worker = AssetJobWorker(orchestrator.repository, orchestrator, FakeClient(history), library)

    worker.run_once()
    assert library.get_motion_path("char-1", "idle")

    worker.run_once()
    assert library.get_background_path("char-1")
    remaining = [job for job in orchestrator.repository.pending() if job.status == JobStatus.QUEUED and job.workflow_type == "motion_clip"]
    assert len(remaining) == 6


def test_mock_asset_service_exposes_validation_and_background_requests(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    service = MockAssetService(store, complete_immediately=False)

    assert service.create_character_validation_request("upload.png", "丘比", "event-1").status == "queued"
    assert service.create_background_request("char-1", "event-1").status == "queued"


def test_background_completion_updates_character_library(tmp_path, monkeypatch):
    import character_library as library_module

    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "og.png"
    source.write_bytes(b"og")
    room = tmp_path / "room.jpg"
    room.write_bytes(b"room")
    library = CharacterLibrary()
    library.create_validated_character("char-1", str(source), "Char")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(AssetRepository(store), Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json", Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json", background_template=Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_background_gen_260728.json")
    orchestrator.create_background_job("char-1", str(source), str(room), "event-1")

    AssetJobWorker(orchestrator.repository, orchestrator, FakeClient({"outputs": {"16": {"images": [{"filename": "background.png"}]}}}), library).run_once()

    assert library.get_background_path("char-1").endswith(".png")


def test_variant_completion_queues_a_replacement_motion_set(tmp_path, monkeypatch):
    import character_library as library_module

    monkeypatch.setattr(library_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_module, "CHARACTER_LIBRARY_DIR", tmp_path / "assets" / "characters")
    source = tmp_path / "og.png"
    source.write_bytes(b"og")
    library = CharacterLibrary()
    library.create_validated_character("char-1", str(source), "Char")
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    orchestrator = AssetOrchestrator(AssetRepository(store), Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json", Path(__file__).parents[1] / "ComfyUI_Json" / "AIA_2026_image_gen_260720.json")
    orchestrator.create_variant_png_job("char-1", str(source), "development", "event-1", trigger_reason="level_up")

    AssetJobWorker(orchestrator.repository, orchestrator, FakeClient({"outputs": {"467": {"images": [{"filename": "variant.png"}]}}}), library).run_once()

    parent = next(job for job in orchestrator.repository.pending() if job.workflow_type == "motion_set")
    assert len(orchestrator.repository.children(parent.job_id)) == 7
    assert parent.metadata["trigger_reason"] == "level_up"
