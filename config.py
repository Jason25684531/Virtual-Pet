"""
ECHOES — 集中式設定中心。

本機大腦已完成與 OpenClaw 解耦；LangChain / Ollama / ElevenLabs 的
非敏感預設值與 persona prompt 由此集中管理，敏感資訊仍只從 `.env` 讀取。
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - 允許在依賴尚未安裝時安全匯入
    def load_dotenv(*_args, **_kwargs):  # type: ignore[override]
        return False


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)

DEFAULT_PERSONA_KEY = "default"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "minimax-m2.7:cloud"
DEFAULT_ELEVENLABS_VOICE_ID = "zENt0ljwLXypGqHDsdzz"
DEFAULT_TTS_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_TTS_TIMEOUT = (5, 45)
DEFAULT_TEMP_AUDIO_DIR = PROJECT_ROOT / "assets" / "temp_audio"
DEFAULT_AZURE_STT_LANGUAGE = "zh-TW"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip() or DEFAULT_OLLAMA_BASE_URL
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
ELEVENLABS_VOICE_ID = (
    os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID).strip()
    or DEFAULT_ELEVENLABS_VOICE_ID
)
TEMP_AUDIO_DIR = Path(
    os.getenv("ELEVENLABS_TEMP_AUDIO_DIR", "").strip() or str(DEFAULT_TEMP_AUDIO_DIR)
)


def _read_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


AZURE_STT_API_KEY = os.getenv("AZURE_STT_API_KEY", "").strip()
AZURE_STT_REGION = os.getenv("AZURE_STT_REGION", "").strip()
AZURE_STT_LANGUAGE = (
    os.getenv("AZURE_STT_LANGUAGE", DEFAULT_AZURE_STT_LANGUAGE).strip()
    or DEFAULT_AZURE_STT_LANGUAGE
)
AZURE_STT_ENABLED = _read_bool_env("AZURE_STT_ENABLED", default=True)

PERSONA_PROMPTS = {
    "default": (
        "你是       ECHOES，本機桌面陪伴 AI。"
        "請以自然、簡潔、溫暖的繁體中文回覆。"
        "若需要觸發 Host action，只能使用單一 [ACTION:...] 標籤，"
        "且必須放在回覆最前面、作為第一個有效字元。"
    ),
    "初音 (正式版)": (
        "你是 ECHOES 的初音系桌面角色。"
        "語氣清亮、活潑、友善，保持簡潔，不要過度冗長。"
        "若需要觸發 Host action，只能使用單一 [ACTION:...] 標籤，"
        "且必須放在回覆最前面、作為第一個有效字元。"
    ),
    "20260415_168888_初音": (
        "你是 ECHOES 的初音系桌面角色。"
        "語氣清亮、活潑、友善，保持簡潔，不要過度冗長。"
        "若需要觸發 Host action，只能使用單一 [ACTION:...] 標籤，"
        "且必須放在回覆最前面、作為第一個有效字元。"
    ),
}

HOST_ACTION_NAMES = (
    "report_news",
    "play_music",
    "wave_response",
    "laugh",
    "angry",
    "awkward",
    "speechless",
    "listen",
    "idle",
)

HOST_ACTION_ALIASES = {
    "news": "report_news",
    "headline": "report_news",
    "headlines": "report_news",
    "weather": "report_news",
    "forecast": "report_news",
    "music": "play_music",
    "song": "play_music",
    "songs": "play_music",
    "playlist": "play_music",
    "wave": "wave_response",
    "waving": "wave_response",
    "run": "wave_response",
    "happy": "laugh",
    "smile": "laugh",
    "joy": "laugh",
    "mad": "angry",
    "annoyed": "angry",
    "shy": "awkward",
    "embarrassed": "awkward",
    "confused": "awkward",
    "silent": "speechless",
    "sad": "speechless",
    "thinking": "listen",
    "curious": "listen",
    "default": "idle",
    "none": "idle",
}

HOST_ACTION_PROMPT = (
    "若需要觸發 Host action，只能從以下白名單挑一個，且只能輸出一個，"
    "並且必須放在整段回覆最前面、作為第一個有效字元："
    "[ACTION:report_news]、[ACTION:play_music]、[ACTION:wave_response]、[ACTION:laugh]、"
    "[ACTION:angry]、[ACTION:awkward]、[ACTION:speechless]、[ACTION:listen]、[ACTION:idle]。"
    "新聞、頭條、天氣請使用 report_news；音樂、放鬆、播歌請使用 play_music；"
    "一般聆聽或不確定時使用 listen。禁止自創新的 action 名稱。"
    "除了最前面的 action 前綴外，後續內容只能是自然語言回覆。"
)


def resolve_persona_key(*candidates: str | None) -> str:
    """依序尋找存在於 PERSONA_PROMPTS 的 persona key。"""

    for candidate in candidates:
        key = str(candidate or "").strip()
        if key and key in PERSONA_PROMPTS:
            return key
    return DEFAULT_PERSONA_KEY


def get_persona_prompt(persona_key: str | None) -> str:
    key = resolve_persona_key(persona_key)
    return PERSONA_PROMPTS.get(key, PERSONA_PROMPTS[DEFAULT_PERSONA_KEY])


def canonicalize_host_action(action_name: str | None) -> str:
    normalized = str(action_name or "").strip().lower()
    if not normalized:
        return ""
    if normalized in HOST_ACTION_NAMES:
        return normalized
    return HOST_ACTION_ALIASES.get(normalized, "")


def azure_stt_is_configured() -> bool:
    return bool(AZURE_STT_API_KEY and AZURE_STT_REGION)
