from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import requests


class ComfyUIClient:
    def __init__(self, base_url: str, ws_url: str | None = None, timeout_sec: int = 300, session: requests.Session | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = (ws_url or base_url.replace("http", "ws", 1)).rstrip("/")
        self.timeout_sec = timeout_sec
        self.session = session or requests.Session()
        self.client_id = str(uuid.uuid4())

    def upload_image(self, path: str | Path, subfolder: str) -> str:
        file_path = Path(path)
        with file_path.open("rb") as handle:
            response = self.session.post(f"{self.base_url}/upload/image", data={"subfolder": subfolder, "overwrite": "true"}, files={"image": (file_path.name, handle)}, timeout=self.timeout_sec)
        response.raise_for_status()
        data = response.json()
        return "/".join(part for part in (data.get("subfolder", subfolder), data.get("name", file_path.name)) if part)

    def submit_prompt(self, workflow: dict[str, Any]) -> str:
        response = self.session.post(f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": self.client_id}, timeout=self.timeout_sec)
        response.raise_for_status()
        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return prompt_id")
        return str(prompt_id)

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/history/{prompt_id}", timeout=self.timeout_sec)
        response.raise_for_status()
        return response.json().get(prompt_id) or response.json()

    def has_prompt(self, prompt_id: str) -> bool:
        response = self.session.get(f"{self.base_url}/queue", timeout=self.timeout_sec)
        response.raise_for_status()
        queue = response.json()
        return any(
            str(item.get("prompt_id") if isinstance(item, dict) else item[1] if len(item) > 1 else "") == prompt_id
            for name in ("queue_running", "queue_pending")
            for item in queue.get(name, [])
        )

    def watch_prompt(self, prompt_id: str, timeout_sec: int, on_progress=None) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        try:
            import websocket
            ws = websocket.create_connection(f"{self.ws_url}/ws?clientId={self.client_id}", timeout=min(10, timeout_sec))
            try:
                while time.monotonic() < deadline:
                    payload = json.loads(ws.recv())
                    if payload.get("type") == "progress":
                        data = payload.get("data") or {}
                        if data.get("prompt_id") in (None, prompt_id) and on_progress:
                            on_progress(data.get("value"), data.get("max"))
                        continue
                    if payload.get("type") == "execution_error" and payload.get("data", {}).get("prompt_id") == prompt_id:
                        raise RuntimeError(str(payload.get("data")))
                    if payload.get("type") == "executing" and payload.get("data", {}).get("prompt_id") == prompt_id and payload.get("data", {}).get("node") is None:
                        return self.get_history(prompt_id)
            finally:
                ws.close()
        except RuntimeError:
            raise
        except Exception:
            if on_progress:
                on_progress(None, None)
            # WebSocket is an optimization; history polling is authoritative.
        while time.monotonic() < deadline:
            history = self.get_history(prompt_id)
            if history.get("outputs"):
                return history
            time.sleep(0.25)
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")

    def download_output(self, filename: str, subfolder: str = "", output_type: str = "output") -> bytes:
        response = self.session.get(f"{self.base_url}/view", params={"filename": filename, "subfolder": subfolder, "type": output_type}, timeout=self.timeout_sec)
        response.raise_for_status()
        return response.content

    def cancel_prompt(self, prompt_id: str) -> None:
        response = self.session.post(f"{self.base_url}/queue", json={"delete": [prompt_id]}, timeout=self.timeout_sec)
        if response.status_code >= 400:
            self.session.post(f"{self.base_url}/interrupt", timeout=self.timeout_sec).raise_for_status()

    def health_check(self) -> bool:
        try:
            return self.session.get(f"{self.base_url}/system_stats", timeout=5).ok
        except requests.RequestException:
            return False

    @staticmethod
    def output(history: dict[str, Any], node_id: str) -> dict[str, Any]:
        outputs = history.get("outputs", {}).get(node_id, {})
        for media in ("images", "gifs"):
            if outputs.get(media):
                return outputs[media][0]
        raise RuntimeError(f"ComfyUI history has no output for node {node_id}")
