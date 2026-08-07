"""
ECHOES — 集中式設定中心。

本機大腦已完成與 OpenClaw 解耦；LangChain / OpenAI / ElevenLabs 的
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
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ELEVENLABS_VOICE_ID = "zENt0ljwLXypGqHDsdzz"
DEFAULT_TTS_MODEL_ID = "eleven_flash_v2_5"
DEFAULT_TTS_TIMEOUT = (5, 45)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip() or DEFAULT_OLLAMA_BASE_URL
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY", "").strip()
    or os.getenv("CHATGPT_API_KEY", "").strip()
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
ELEVENLABS_VOICE_ID = (
    os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID).strip()
    or DEFAULT_ELEVENLABS_VOICE_ID
)

# --- i18n 語系設定 ---
# --- 多語系 TTS 聲線 ---
CHARACTER_ELEVENLABS_VOICE_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "miku": (
        "ELEVENLABS_MIKU_VOICE_ID",
        "MIKU_VOICE_ID",
        "ELEVENLABS_VOICE_ID",
    ),
    "Choppr": (
        "ELEVENLABS_CHOPPR_VOICE_ID",
        "ELEVENLABS_CHOPPER_VOICE_ID",
        "CHOPPER_VOICE_ID",
        "ELEVENLABS_VOICE_ID",
    ),
}


def _read_first_non_empty_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return str(default or "").strip()


def _build_character_elevenlabs_voice_ids() -> dict[str, str]:
    resolved: dict[str, str] = {}
    for character_id, env_keys in CHARACTER_ELEVENLABS_VOICE_ENV_KEYS.items():
        resolved[character_id] = _read_first_non_empty_env(
            *env_keys,
            default=DEFAULT_ELEVENLABS_VOICE_ID,
        ) or DEFAULT_ELEVENLABS_VOICE_ID
    return resolved


# 各角色專屬 ElevenLabs 聲線映射（從 .env 解析，缺少時回退全域預設）
CHARACTER_VOICE_IDS: dict[str, str] = _build_character_elevenlabs_voice_ids()

# VoAI 角色聲音設定
_DEFAULT_VOAI_CONFIG: dict = {
    "speaker": "柔洢",
    "version": "Classic",
    "pitch_shift": 0,
    "style": "預設",
    "style_weight": 0,
    "speed": 1.2,
    "breath_pause": 0,
}
CHARACTER_VOAI_CONFIGS: dict[str, dict] = {
    "miku": {
        "speaker": "柔洢",
        "version": "Classic",
        "pitch_shift": 1,
        "style": "預設",
        "style_weight": 0,
        "speed": 1.2,
        "breath_pause": 0,
    },
    "Choppr": {
        "speaker": "阿皮",
        "version": "Neo",
        "pitch_shift": 1.5,
        "style": "預設",
        "style_weight": 0,
        "speed": 1.3,
        "breath_pause": 0,
    },
}


def _read_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _read_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return int(default)
    try:
        return int(value)
    except ValueError:
        return int(default)


def _read_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip() or default)
    except ValueError:
        return float(default)


SEMANTIC_ROUTING_ENABLED = _read_bool_env("SEMANTIC_ROUTING_ENABLED", True)
MEMORY_LLM_REWRITE_ENABLED = _read_bool_env("MEMORY_LLM_REWRITE_ENABLED", False)
SEMANTIC_ROUTING_SHADOW_MODE = _read_bool_env("SEMANTIC_ROUTING_SHADOW_MODE", True)
SEMANTIC_ROUTING_MODEL = os.getenv("SEMANTIC_ROUTING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").strip()
SEMANTIC_ROUTING_COLLECTION = os.getenv("SEMANTIC_ROUTING_COLLECTION", "skills").strip() or "skills"
SEMANTIC_ROUTING_TOP_K = _read_int_env("SEMANTIC_ROUTING_TOP_K", 3)
SEMANTIC_ROUTING_ACCEPT_THRESHOLD = _read_float_env("SEMANTIC_ROUTING_ACCEPT_THRESHOLD", 0.60)
SEMANTIC_ROUTING_MARGIN_THRESHOLD = _read_float_env("SEMANTIC_ROUTING_MARGIN_THRESHOLD", 0.08)
QDRANT_MODE = os.getenv("QDRANT_MODE", "local").strip().lower() or "local"
QDRANT_PATH = os.getenv("QDRANT_PATH", str(PROJECT_ROOT / "runtime_cache" / "qdrant")).strip()
QDRANT_URL = os.getenv("QDRANT_URL", "").strip()
PROVIDER_ROUTING_FALLBACK_ENABLED = _read_bool_env("PROVIDER_ROUTING_FALLBACK_ENABLED", True)
PROVIDER_ROUTING_CONFIDENCE_THRESHOLD = _read_float_env("PROVIDER_ROUTING_CONFIDENCE_THRESHOLD", 0.7)
BROWSER_SESSION_RECOVERY_ENABLED = _read_bool_env("BROWSER_SESSION_RECOVERY_ENABLED", True)
BROWSER_SESSION_RECOVERY_MAX_RETRIES = _read_int_env("BROWSER_SESSION_RECOVERY_MAX_RETRIES", 1)


ACTION_SYNC_TIMEOUT_MS = _read_int_env("ACTION_SYNC_TIMEOUT_MS", 6000)

# ComfyUI asset generation is opt-in; the mock service remains the safe default.
COMFYUI_BASE_URL = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188").strip().rstrip("/")
COMFYUI_WS_URL = os.getenv("COMFYUI_WS_URL", "ws://127.0.0.1:8188").strip().rstrip("/")
COMFYUI_TIMEOUT_SEC = _read_int_env("COMFYUI_TIMEOUT_SEC", 300)
COMFYUI_VIDEO_TIMEOUT_SEC = _read_int_env("COMFYUI_VIDEO_TIMEOUT_SEC", 900)
COMFYUI_MAX_RETRIES = _read_int_env("COMFYUI_MAX_RETRIES", 2)
COMFYUI_ENABLED = _read_bool_env("COMFYUI_ENABLED", False)
XP_PER_LEVEL = _read_int_env("XP_PER_LEVEL", 6)
EVENT_INTERVAL_MINUTES = _read_float_env("EVENT_INTERVAL_MINUTES", 3.0)
# --- Faster Whisper STT（toggle-recording，Week 4） ---
STT_ENABLED = _read_bool_env("STT_ENABLED", True)
STT_MODEL = os.getenv("STT_MODEL", "large-v3-turbo").strip() or "large-v3-turbo" #Whisper Model
STT_DEVICE = os.getenv("STT_DEVICE", "cuda").strip() or "cuda"
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16").strip() or "float16"
STT_MODEL_PATH = os.getenv("STT_MODEL_PATH", str(PROJECT_ROOT / "runtime_cache" / "whisper")).strip()
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "").strip()  # 空字串 = auto detection
STT_BEAM_SIZE = _read_int_env("STT_BEAM_SIZE", 1)
STT_SAMPLE_RATE = _read_int_env("STT_SAMPLE_RATE", 16000)
STT_MIN_RECORDING_MS = _read_int_env("STT_MIN_RECORDING_MS", 300)
STT_MAX_RECORDING_SECONDS = _read_int_env("STT_MAX_RECORDING_SECONDS", 30)
STT_VAD_ENABLED = _read_bool_env("STT_VAD_ENABLED", False)
STT_VAD_SILENCE_MS = _read_int_env("STT_VAD_SILENCE_MS", 800)
STT_VAD_THRESHOLD = _read_float_env("STT_VAD_THRESHOLD", 0.5)

LOW_LATENCY_REPLY_POLICY = (
    "即時互動請優先用 1 句短句完成回覆，只有必要時才允許第 2 句。"
    "第一句要直接承載主要內容，不要先鋪陳或寒暄。"
    "整體保持短而活潑，目標約 18 個中文字內，最多約 24 個中文字。"
)

PERSONA_PROMPTS = {
    "default": (
        "你是       ECHOES，本機桌面陪伴 AI。"
        "請以自然、簡潔、溫暖的繁體中文回覆。"
        "若需要觸發 Host action，你必須把單一 [ACTION:...] 標籤放在回覆的第一句第一個字，"
        "不能先輸出任何空白、說明、標點或寒暄。"
        "若不需要動作，就直接輸出自然語言。"
        f"{LOW_LATENCY_REPLY_POLICY}"
    ),
    "miku": (
        "你是 ECHOES 的初音未來（miku）桌面角色。"
        "語氣清亮、活潑、元氣十足，用繁體中文回覆，保持簡潔不冗長。"
        "若需要觸發 Host action，你必須把單一 [ACTION:...] 標籤放在回覆的第一句第一個字，"
        "不能先輸出任何空白、說明、標點或寒暄。"
        "若不需要動作，就直接輸出自然語言。"
        f"{LOW_LATENCY_REPLY_POLICY}"
    ),
    "Choppr": (
        "你是 ECHOES 的喬巴（Choppr）桌面角色，是個活潑可愛的小鹿角色。"
        "語氣熱情、開朗，用繁體中文回覆，保持簡潔不冗長。"
        "若需要觸發 Host action，你必須把單一 [ACTION:...] 標籤放在回覆的第一句第一個字，"
        "不能先輸出任何空白、說明、標點或寒暄。"
        "若不需要動作，就直接輸出自然語言。"
        f"{LOW_LATENCY_REPLY_POLICY}"
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
    "read_news": "report_news",
    "readnews": "report_news",
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
    "並且必須放在整段回覆的第一句第一個字，前面不允許有空白、換行、引號或任何其他字元："
    "[ACTION:report_news]、[ACTION:play_music]、[ACTION:wave_response]、[ACTION:laugh]、"
    "[ACTION:angry]、[ACTION:awkward]、[ACTION:speechless]、[ACTION:listen]、[ACTION:idle]。"
    "新聞、頭條、天氣請使用 report_news；音樂、放鬆、播歌請使用 play_music；"
    "一般聆聽或不確定時使用 listen。禁止自創新的 action 名稱。"
    "除了最前面的 action 前綴外，後續內容只能是自然語言回覆。"
    "若有 action，請在 action 後立刻接自然語言第一句，讓系統可以依標點即時切句播放。"
    "即時互動請優先只回 1 句短句，只有必要時才允許第 2 句。"
    "不要先寒暄、不要鋪陳，第一句就直接回答，整體保持短而活潑。"
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


def get_elevenlabs_voice_id_for_character(character_id: str | None) -> str:
    """回傳角色對應的 ElevenLabs Voice ID。

    優先順序：CHARACTER_VOICE_IDS[character_id] → ELEVENLABS_VOICE_ID（全域預設）。
    """
    cid = str(character_id or "").strip()
    return CHARACTER_VOICE_IDS.get(cid) or ELEVENLABS_VOICE_ID


def get_voai_config_for_character(character_id: str | None) -> dict:
    """回傳角色對應的 VoAI 聲音設定 dict（speaker、version、pitch_shift）。
    找不到時回傳預設設定。
    """
    cid = str(character_id or "").strip()
    return dict(CHARACTER_VOAI_CONFIGS.get(cid) or _DEFAULT_VOAI_CONFIG)


def get_voai_api_key() -> str:
    """取得 VoAI API key（優先 VOAI_API_KEY，次 VoAI_API_KEY）。"""
    return (
        os.getenv("VOAI_API_KEY", "").strip()
        or os.getenv("VoAI_API_KEY", "").strip()
    )


def resolve_tts_runtime_mode() -> tuple[str, str | None]:
    """決定 TTS 運行時模式與原因。

    Returns:
        (mode, reason) tuple：
        - mode: "voai_first" | "fallback_enabled" | "mock_only"
        - reason: 解析原因（debug 用），e.g. "voai_api_key_available" 或 "voai_api_key_missing"
    """
    voai_key = get_voai_api_key()

    if not voai_key:
        return ("fallback_enabled", "voai_api_key_missing")

    return ("voai_first", "voai_api_key_available")
