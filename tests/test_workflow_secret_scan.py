import json
from pathlib import Path


def test_comfyui_templates_have_no_embedded_azure_credentials():
    # 從 ComfyUI 重新匯出會把節點值(含 key)帶回 JSON;這裡掃所有模板的 LLM 節點。
    root = Path(__file__).parents[1] / "ComfyUI_Json"
    for path in root.glob("*.json"):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        for node_id, node in workflow.items():
            if node.get("class_type") != "Azure_ChatGPT_Node":
                continue
            assert not node.get("inputs", {}).get("api_key"), f"{path}:{node_id}.api_key"


def test_parallelgen_injection_derives_images_endpoint(monkeypatch):
    from pet_harness.asset.workflow_patcher import WorkflowPatcher

    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.services.ai.azure.com/")
    monkeypatch.delenv("AZURE_OPENAI_IMAGES_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_IMAGES_API_KEY", raising=False)
    workflow = {"9": {"class_type": "ParallelGen_Azure_EditSubmit", "inputs": {"api_key": "", "endpoint_url": ""}}}
    patched = WorkflowPatcher._inject_secrets(workflow)
    assert patched["9"]["inputs"]["api_key"] == "test-key"
    assert patched["9"]["inputs"]["endpoint_url"] == "https://example.services.ai.azure.com/openai/v1/images/generations"

    # 影像專用變數優先於 chat 資源的 key/endpoint
    monkeypatch.setenv("AZURE_OPENAI_IMAGES_API_KEY", "images-key")
    monkeypatch.setenv("AZURE_OPENAI_IMAGES_ENDPOINT", "https://images.example.com/openai/v1/images/generations")
    patched = WorkflowPatcher._inject_secrets(workflow)
    assert patched["9"]["inputs"]["api_key"] == "images-key"
    assert patched["9"]["inputs"]["endpoint_url"] == "https://images.example.com/openai/v1/images/generations"


def test_parallelgen_nodes_have_no_embedded_credentials():
    root = Path(__file__).parents[1] / "ComfyUI_Json"
    for path in root.glob("*.json"):
        workflow = json.loads(path.read_text(encoding="utf-8"))
        for node_id, node in workflow.items():
            if node.get("class_type") != "ParallelGen_Azure_EditSubmit":
                continue
            inputs = node.get("inputs", {})
            assert not inputs.get("api_key"), f"{path}:{node_id}.api_key"
            assert not inputs.get("endpoint_url"), f"{path}:{node_id}.endpoint_url"
