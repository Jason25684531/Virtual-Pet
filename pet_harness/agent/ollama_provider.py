from __future__ import annotations

import json
import os
from threading import Event
from typing import Any, Callable, Iterator

import requests

from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.models.skill import Skill


# 模型駐留 VRAM 時長;Ollama 預設 5m,閒置後下一輪要重付冷載入(gemma3:12b 實測 60s+)。
_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
# 上下文上限:prompt 實測 ~4.3k 字元,預設載到 131k 的 KV cache 只會拖慢載入與推理。
# 注意:所有請求(含 warmup)必須用同一個 num_ctx,不同值會觸發 Ollama 重載模型。
_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))


class OllamaProvider:
    def __init__(self, config: ProviderConfig, request_fn: Callable[..., Any] | None = None) -> None:
        self.config = config
        self.request_fn = request_fn or self._default_request

    def warmup(self) -> bool:
        """啟動時預載模型並用長 prompt 預熱 prefill kernel。

        冷載入實測 30s+;首次大 prompt 的 GPU kernel 編譯另需 20s+,兩者都在
        背景啟動時付掉,首輪對話才守得住延遲預算(app prompt 約 4.3k 字元)。"""
        base_url = self.config.base_url or "http://localhost:11434"
        try:
            response = self.request_fn(
                "POST",
                f"{base_url}/api/generate",
                timeout=300,
                json={
                    "model": self.config.model_name,
                    "prompt": "暖機用的長輸入。" * 500,
                    "keep_alive": _KEEP_ALIVE,
                    "options": {"num_ctx": _NUM_CTX, "num_predict": 1},
                },
            )
            return getattr(response, "status_code", 500) < 400
        except Exception:  # noqa: BLE001 - 暖機失敗不擋啟動
            return False

    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        base_url = self.config.base_url or "http://localhost:11434" # IP Calling
        prompt = prompt_text or event.text
        try:
            payload = {
                "model": self.config.model_name,
                "prompt": prompt,
                "stream": False,
                "keep_alive": _KEEP_ALIVE,
                "options": {"num_ctx": _NUM_CTX},
            }
            payload.update({key: self.config.metadata[key] for key in ("format", "options") if key in self.config.metadata})
            response = self.request_fn(
                "POST",
                f"{base_url}/api/generate",
                timeout=self.config.timeout_seconds,
                json=payload,
            )
            if getattr(response, "status_code", 500) >= 400:
                return self._unavailable_reply(
                    prompt, "ollama_http_error",
                    f"Ollama returned status {response.status_code}.",
                )
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - fail-closed,不偽造回覆
            return self._unavailable_reply(prompt, "ollama_unavailable", f"Ollama request failed: {exc}")

        content = str(payload.get("response") or "").strip()
        if not content:
            return self._unavailable_reply(prompt, "invalid_response_shape", "Ollama response had no content.")

        return ProviderReply(
            reply=content,
            provider_status=ProviderStatus(
                provider_type=ProviderType.OLLAMA,
                healthy=True,
                message="ollama provider ready",
                metadata={"model_name": self.config.model_name, "base_url": base_url},
            ),
            raw_text=content,
            raw_json=payload,
            prompt_text=prompt,
        )

    def generate_reply_stream(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
        cancel: Event | None = None,
    ) -> Iterator[str]:
        base_url = self.config.base_url or "http://localhost:11434"
        prompt = prompt_text or event.text
        response = self.request_fn(
            "POST",
            f"{base_url}/api/generate",
            timeout=self.config.timeout_seconds,
            json={"model": self.config.model_name, "prompt": prompt, "stream": True, "keep_alive": _KEEP_ALIVE, "options": {"num_ctx": _NUM_CTX}},
            stream=True,
        )
        if getattr(response, "status_code", 500) >= 400:
            raise RuntimeError(f"Ollama returned status {response.status_code}.")
        try:
            for line in response.iter_lines():
                if cancel is not None and cancel.is_set():
                    break
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                payload = json.loads(line)
                fragment = str(payload.get("response") or "")
                if fragment:
                    yield fragment
                if payload.get("done"):
                    break
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _unavailable_reply(self, prompt_text: str, error_category: str, message: str) -> ProviderReply:
        metadata = {"error_category": error_category, "requested_provider": ProviderType.OLLAMA.value}
        return ProviderReply(
            reply=f"AI provider unavailable: {message}",
            provider_status=ProviderStatus(
                provider_type=ProviderType.OLLAMA,
                healthy=False,
                message=message,
                metadata=metadata,
            ),
            prompt_text=prompt_text,
            metadata=metadata,
        )

    def health_check(self) -> dict[str, Any]:
        base_url = self.config.base_url or "http://localhost:11434"
        try:
            response = self.request_fn("GET", f"{base_url}/api/tags", timeout=self.config.timeout_seconds)
            if getattr(response, "status_code", 500) >= 400:
                return self._status_payload(False, "ollama_http_error", base_url)
            response.json()
            return self._status_payload(True, None, base_url)
        except Exception:
            return self._status_payload(False, "ollama_unavailable", base_url)

    def check_model(self, model_name: str) -> dict[str, Any]:
        base_url = self.config.base_url or "http://localhost:11434"
        try:
            response = self.request_fn("GET", f"{base_url}/api/tags", timeout=self.config.timeout_seconds)
            if getattr(response, "status_code", 500) >= 400:
                return {"available": False, "model_name": model_name, "error_category": "ollama_http_error"}
            payload = response.json()
            models = payload.get("models", [])
            names = {item.get("name") for item in models if isinstance(item, dict)}
            return {"available": model_name in names, "model_name": model_name}
        except Exception:
            return {"available": False, "model_name": model_name, "error_category": "ollama_unavailable"}

    def provider_status_from_health(self) -> ProviderStatus:
        health = self.health_check()
        return ProviderStatus(
            provider_type=ProviderType.OLLAMA,
            healthy=health["healthy"],
            message=health["message"],
            metadata=health["metadata"],
        )

    def _status_payload(self, healthy: bool, error_category: str | None, base_url: str) -> dict[str, Any]:
        metadata = {"base_url": base_url}
        if error_category:
            metadata["error_category"] = error_category
        return {
            "healthy": healthy,
            "message": "ollama ready" if healthy else "Ollama unavailable",
            "metadata": metadata,
        }

    def _default_request(self, method: str, url: str, timeout: float, json: Any | None = None, stream: bool = False):
        return requests.request(method, url, timeout=timeout, json=json, stream=stream)
