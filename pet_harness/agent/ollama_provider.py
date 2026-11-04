from __future__ import annotations

import json
import logging
import time
from threading import Event
from typing import Any, Callable, Iterator

import requests

import config
from pet_harness.latency import get_turn
from pet_harness.agent.provider_adapter import LLMProviderAdapter, ProviderReply
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.models.skill import Skill

LOGGER = logging.getLogger(__name__)
# 共用連線池:每輪都 requests.request() 會重新建立 TCP 連線(與 voai_client / api_provider 相同做法)。
_SESSION = requests.Session()


class OllamaProvider(LLMProviderAdapter):
    def _payload(self, prompt: str, *, stream: bool) -> dict[str, Any]:
        # 串流與非串流必須送同一組 format/options;串流漏帶 format 時回覆不受 JSON 約束,
        # result parser 會落到 fallback(log 的 invalid_json raw_length=0)。
        payload = {"model": self.config.model_name, "prompt": prompt, "stream": stream, "keep_alive": config.ollama_keep_alive_value()}
        payload.update({key: self.config.metadata[key] for key in ("format", "options") if key in self.config.metadata})
        return payload

    def _http_error(self, status_code: int) -> str:
        if status_code == 404:  # Ollama 對不存在的模型回 404
            return f"Ollama returned status 404: model '{self.config.model_name}' not found (run: ollama pull {self.config.model_name})."
        return f"Ollama returned status {status_code}."

    def __init__(self, config: ProviderConfig, request_fn: Callable[..., Any] | None = None) -> None:
        self.config = config
        self.request_fn = request_fn or self._default_request

    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        base_url = self.config.base_url or "http://localhost:11434" # IP Calling
        prompt = prompt_text or event.text
        try:
            response = self.request_fn(
                "POST",
                f"{base_url}/api/generate",
                timeout=self.config.timeout_seconds,
                json=self._payload(prompt, stream=False),
            )
            if getattr(response, "status_code", 500) >= 400:
                return self._unavailable_reply(
                    prompt, "ollama_http_error",
                    self._http_error(response.status_code),
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

    def preload(self, prompt: str | None = None) -> bool:
        """不帶 prompt 只載入模型;帶 prompt(角色的固定前段)時只生 1 token,讓 KV cache 先存好前綴。
        失敗只記 warning,首輪回落成冷啟動。"""
        started = time.perf_counter()
        success = False
        payload = {"model": self.config.model_name, "keep_alive": config.ollama_keep_alive_value()}
        if prompt:
            # 與正式請求同一組 format/options,否則 Ollama 可能因參數不同重載模型。
            payload = self._payload(prompt, stream=False)
            payload["options"] = {**payload.get("options", {}), "num_predict": 1}
        try:
            response = self.request_fn(
                "POST",
                f"{self.config.base_url or 'http://localhost:11434'}/api/generate",
                timeout=self.config.timeout_seconds,
                json=payload,
            )
            success = getattr(response, "status_code", 500) < 400
        except Exception as exc:  # noqa: BLE001 - 預載失敗不得影響啟動
            LOGGER.warning("[LLM PRELOAD] failed: %s", exc)
        LOGGER.info("[LLM PRELOAD] done preload_ms=%s success=%s prefix_chars=%s", round((time.perf_counter() - started) * 1000), success, len(prompt or ""))
        return success

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
            json=self._payload(prompt, stream=True),
            stream=True,
        )
        if getattr(response, "status_code", 500) >= 400:
            raise RuntimeError(self._http_error(response.status_code))
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
                    self._record_stream_stats(event, payload)
                    break
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _record_stream_stats(event: UserEvent, payload: dict[str, Any]) -> None:
        """把 Ollama 的 load/prompt_eval(ns)掛到該回合 timeline,讓 TTFT 離群值分得出是重載還是重算。"""
        timeline = get_turn((getattr(event, "metadata", None) or {}).get("turn_id"))
        if timeline is None:
            return
        to_ms = lambda key: round(payload[key] / 1e6) if isinstance(payload.get(key), (int, float)) else None
        timeline.llm_stats = {
            "llm_load_ms": to_ms("load_duration"),
            "llm_prompt_eval_ms": to_ms("prompt_eval_duration"),
            "llm_prompt_tokens": payload.get("prompt_eval_count"),
        }

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
        return _SESSION.request(method, url, timeout=timeout, json=json, stream=stream)
