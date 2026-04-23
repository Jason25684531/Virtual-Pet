"""
ECHOES smoke test for LangChain + ElevenLabs environment.

請務必先進入 Ubuntu 24.04 專案虛擬環境後再執行：
    source venv/bin/activate
    python smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

import config
from api_client.brain_engine import BrainEngine


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
TEMP_AUDIO_DIR = config.TEMP_AUDIO_DIR
DEFAULT_OLLAMA_MODEL = config.DEFAULT_OLLAMA_MODEL


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def load_env_values() -> dict[str, str]:
    load_dotenv(ENV_PATH, override=False)
    parsed: dict[str, str] = {}
    if not ENV_PATH.exists():
        return parsed

    for raw_line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        parsed[normalized_key] = normalized_value
        if normalized_key and normalized_value and not os.getenv(normalized_key):
            os.environ[normalized_key] = normalized_value
    return parsed


def check_env(env_map: dict[str, str]) -> CheckResult:
    required_keys = [
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
    ]
    missing = []
    for key in required_keys:
        value = os.getenv(key, "").strip() or env_map.get(key, "").strip()
        if not value:
            missing.append(key)

    if missing:
        return CheckResult(
            name=".env",
            ok=False,
            detail=f"缺少必要欄位或值: {', '.join(missing)}",
        )

    return CheckResult(
        name=".env",
        ok=True,
        detail="已讀取到必要欄位（已隱藏敏感值）。",
    )


def check_temp_audio_dir() -> CheckResult:
    try:
        TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        probe_path = TEMP_AUDIO_DIR / ".smoke_write_test.tmp"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        return CheckResult(
            name="assets/temp_audio",
            ok=False,
            detail=f"無法建立或寫入暫存音訊目錄: {exc}",
        )

    return CheckResult(
        name="assets/temp_audio",
        ok=True,
        detail=f"可寫入: {TEMP_AUDIO_DIR}",
    )


def check_ollama() -> CheckResult:
    base_url = (os.getenv("OLLAMA_BASE_URL", config.OLLAMA_BASE_URL).strip() or config.OLLAMA_BASE_URL).rstrip("/")
    model_name = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
    endpoint = f"{base_url}/api/chat"
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "你是測試助手。請只輸出：測試成功 [ACTION:listen]",
            },
            {
                "role": "user",
                "content": "請照做",
            },
        ],
        "stream": False,
    }

    try:
        response = requests.post(endpoint, json=payload, timeout=(5, 40))
    except requests.RequestException as exc:
        return CheckResult(
            name="Ollama",
            ok=False,
            detail=f"連線失敗: {exc}。請確認 Ollama 服務已啟動，且位址為 {base_url}",
        )

    if response.status_code == 404:
        return CheckResult(
            name="Ollama",
            ok=False,
            detail=(
                "收到 404。請檢查 Ollama 服務狀態，並確認模型 "
                f"`{model_name}` 已存在（例如先執行 `ollama pull {model_name}`）。"
            ),
        )

    if not response.ok:
        return CheckResult(
            name="Ollama",
            ok=False,
            detail=f"HTTP {response.status_code}: {response.text[:200]}",
        )

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="Ollama",
            ok=False,
            detail=f"回應不是有效 JSON: {exc}",
        )

    message = data.get("message") if isinstance(data, dict) else None
    reply = str((message or {}).get("content", "")).strip()
    if not reply:
        return CheckResult(
            name="Ollama",
            ok=False,
            detail="回應成功，但沒有拿到模型輸出。",
        )

    normalized_reply = BrainEngine._normalize_reply(reply)
    action_detected = "[ACTION:" in normalized_reply
    if not action_detected:
        return CheckResult(
            name="Ollama",
            ok=False,
            detail=(
                f"模型 `{model_name}` 有回應，但現有正規化後未偵測到 action 標籤。"
                f" 原始輸出片段: {reply[:80]!r}"
            ),
        )

    return CheckResult(
        name="Ollama",
        ok=True,
        detail=(
            f"模型 `{model_name}` 可回應，"
            f"原始輸出片段: {reply[:80]!r}；"
            f"正規化結果: {normalized_reply[:80]!r}"
        ),
    )


def check_elevenlabs() -> CheckResult:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", config.ELEVENLABS_VOICE_ID).strip() or config.ELEVENLABS_VOICE_ID
    if not api_key or not voice_id:
        missing = []
        if not api_key:
            missing.append("ELEVENLABS_API_KEY")
        if not voice_id:
            missing.append("ELEVENLABS_VOICE_ID")
        return CheckResult(
            name="ElevenLabs",
            ok=False,
            detail=f"缺少必要欄位或值: {', '.join(missing)}",
        )

    endpoint = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": "測試。",
        "model_id": "eleven_multilingual_v2",
    }

    try:
        response = requests.post(
            endpoint,
            params={"output_format": "mp3_22050_32"},
            headers=headers,
            json=payload,
            timeout=(5, 40),
        )
    except requests.RequestException as exc:
        return CheckResult(
            name="ElevenLabs",
            ok=False,
            detail=f"連線失敗: {exc}。請確認網路狀態與 ElevenLabs 服務可用。",
        )

    if response.status_code == 401:
        return CheckResult(
            name="ElevenLabs",
            ok=False,
            detail="收到 401。請檢查 `ELEVENLABS_API_KEY` 是否有效、是否過期，或是否有多餘空白。",
        )

    if response.status_code == 404:
        return CheckResult(
            name="ElevenLabs",
            ok=False,
            detail="收到 404。請檢查 `ELEVENLABS_VOICE_ID` 是否正確，並確認 ElevenLabs TTS 端點可用。",
        )

    if not response.ok:
        return CheckResult(
            name="ElevenLabs",
            ok=False,
            detail=f"HTTP {response.status_code}: {response.text[:200]}",
        )

    content_type = response.headers.get("content-type", "").lower()
    if "audio" not in content_type or not response.content:
        return CheckResult(
            name="ElevenLabs",
            ok=False,
            detail="API 有回應，但不是有效音訊資料。",
        )

    return CheckResult(
        name="ElevenLabs",
        ok=True,
        detail=f"已成功取得測試音訊，大小 {len(response.content)} bytes。",
    )


def main() -> int:
    env_map = load_env_values()
    results = [
        check_env(env_map),
        check_temp_audio_dir(),
        check_ollama(),
        check_elevenlabs(),
    ]

    print("== ECHOES Smoke Test ==")
    failed = 0
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
        if not result.ok:
            failed += 1

    if failed:
        print(f"\nSmoke test failed: {failed} check(s) did not pass.")
        return 1

    print("\nSmoke test passed: all checks succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
