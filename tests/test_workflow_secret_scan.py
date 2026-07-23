import re
from pathlib import Path


def test_comfyui_templates_have_no_embedded_azure_credentials():
    root = Path(__file__).parents[1] / "ComfyUI_Json"
    pattern = re.compile(r'(?i)"(?:api_key|azure_endpoint)"\s*:\s*"(?!")')
    for path in root.glob("*_api.json"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path
