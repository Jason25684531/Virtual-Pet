import json
from pathlib import Path

from pet_harness.asset.workflow_patcher import WorkflowPatcher

VIDEO = Path(__file__).resolve().parents[1] / "ComfyUI_Json" / "AIA_2026_video_gen_260728.json"
ORIGINAL_ROOT = "C:\ComfyUI_windows_portable_260701"


def test_comfyui_path_replaces_only_the_hardcoded_root(monkeypatch):
    monkeypatch.setenv("COMFYUI_PATH", "D:\ComfyUI\\")
    original = json.loads(VIDEO.read_text(encoding="utf-8"))
    patched = WorkflowPatcher(VIDEO).fresh()

    assert patched["728"]["inputs"]["folder_path"] == original["728"]["inputs"]["folder_path"].replace(ORIGINAL_ROOT, "D:\ComfyUI")
    for node_id, key in (("730", "folder_path"), ("733", "folder_path"), ("756", "directory")):
        assert patched[node_id]["inputs"][key].startswith("D:\ComfyUI\\")
    for node_id, node in original.items():
        for key, value in node.get("inputs", {}).items():
            if not (isinstance(value, str) and value.startswith(ORIGINAL_ROOT)):
                assert patched[node_id]["inputs"][key] == value


def test_without_comfyui_path_workflow_is_identical(monkeypatch):
    monkeypatch.delenv("COMFYUI_PATH", raising=False)
    assert WorkflowPatcher(VIDEO).fresh() == json.loads(VIDEO.read_text(encoding="utf-8"))
