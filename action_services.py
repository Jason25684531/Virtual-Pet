"""
ECHOES — Host action services
在背景執行緒中執行新聞抓取與本地音樂挑選，避免阻塞 PyQt UI。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from pathlib import Path

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config
from character_library import CharacterLibrary, PROJECT_ROOT, UI_MUSIC_DIR

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
except ModuleNotFoundError:  # pragma: no cover - 依實際環境決定
    HumanMessage = None  # type: ignore[assignment]
    SystemMessage = None  # type: ignore[assignment]
    ChatOpenAI = None  # type: ignore[assignment]


FIXED_NEWS_VERSION = "fixed-news-2026-05-06-v1"
FIXED_NEWS_SCRIPT = "\n".join(
    [
        "被盜！《人機迷網》黛安娜「駭入」 《惡靈古堡》官方帳號",
        "《澀谷交叉物語》公開卡司陣容、 《428》部分原班人馬重聚",
        "Valve 宣布最新 STEAM 控制器 5 月 5 日上市　台灣等地售價公開",
    ]
)
NEWS_AUDIO_CACHE_DIR = PROJECT_ROOT / "runtime_cache" / "news_audio"
WAVE_GREETING_VERSION = "wave-greeting-2026-05-07-v2"
WAVE_GREETING_SCRIPT = "嗨 你好嗎"
WAVE_AUDIO_CACHE_DIR = PROJECT_ROOT / "runtime_cache" / "wave_audio"
FIXED_INTENT_CACHE_DIR = PROJECT_ROOT / "runtime_cache" / "fixed_intents"
FIXED_INTENT_LABELS = {
    "joke": "Joke",
    "share": "share",
}
FIXED_INTENT_KEYWORDS = {
    "joke": ("笑話",),
    "share": ("分享",),
}
FIXED_INTENT_ACTIONS = {
    "joke": "laugh",
    "share": "listen",
}
FIXED_INTENT_DEFINITIONS = {
    "joke": {
        "version": "fixed-intent-joke-2026-05-08-v1",
        "title": "Joke",
        "button_query_text": "我今天心情不好 可以跟我個笑話嘛",
        "prompt": (
            "請用繁體中文生成 1 則桌面陪伴角色會說的短笑話。"
            "必須是 1 到 2 句，活潑、可愛、自然，不要冷場。"
            "禁止輸出 [ACTION:...]、markdown、清單、引號、括號說明或角色自介。"
        ),
    },
    "share": {
        "version": "fixed-intent-share-2026-05-08-v2",
        "title": "share",
        "button_query_text": "我今天心情不好 可以聽我分享嘛",
        "request_text": "我今天心情不好 可以聽我分享嘛",
        "prompt": (
            "請直接回應這位心情不好的使用者，明確表達你願意傾聽與陪伴，"
            "讓對方放心繼續分享。"
            "必須是 1 到 2 句，溫柔、自然、帶一點活潑感，但不要變成冷知識、說教或轉移話題。"
            "禁止輸出 [ACTION:...]、markdown、清單、引號、括號說明或角色自介。"
        ),
    },
}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
ACTION_DIRECTIVE_PATTERN = re.compile(
    r"(?:\[\s*ACTION\s*:\s*(?:[A-Za-z0-9_-]+)\s*\]|(?<!\w)ACTION\s*:\s*[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def _normalize_reply_text(text: str) -> str:
    normalized = ACTION_DIRECTIVE_PATTERN.sub("", str(text or ""))
    normalized = normalized.replace("```", " ")
    normalized = re.sub(r"[*_#>`-]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def get_fixed_intent_button_query_text(intent_name: str) -> str | None:
    definition = FIXED_INTENT_DEFINITIONS.get(str(intent_name or "").strip().lower())
    if definition is None:
        return None
    text = str(definition.get("button_query_text") or "").strip()
    return text or None


def resolve_fixed_intent_source_label(intent_name: str, trigger_source: str) -> str:
    normalized_source = str(trigger_source or "").strip()
    if normalized_source.endswith("按鈕觸發"):
        button_query_text = get_fixed_intent_button_query_text(intent_name)
        if button_query_text:
            return button_query_text
    return normalized_source


def _write_bytes_atomic(path: Path, content: bytes):
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        temp_path.write_bytes(content)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, object]):
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _load_cached_metadata(metadata_path: Path) -> dict[str, object] | None:
    if not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _build_fixed_audio_cache_path(
    *,
    cache_dir: Path,
    prefix: str,
    version: str,
    script: str,
    voice_config: dict[str, object],
    voice_label: str,
) -> Path:
    raw_key = json.dumps(
        {
            "version": version,
            "script": script,
            "voice": voice_config,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
    safe_label = voice_label.replace(os.sep, "_")
    return cache_dir / f"{prefix}_{safe_label}_{digest}.mp3"


def _synthesize_fixed_audio_to_file(
    *,
    script: str,
    voice_config: dict[str, object],
    cache_path: Path,
    synthesizer=None,
):
    if callable(synthesizer):
        result = synthesizer(script, voice_config, cache_path)
        if isinstance(result, (bytes, bytearray)):
            cache_path.write_bytes(bytes(result))
        elif result:
            source_path = Path(result)
            cache_path.write_bytes(source_path.read_bytes())
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return
        raise RuntimeError("測試 synthesizer 未產出有效音檔。")

    api_key = os.getenv("VOAI_API_KEY") or os.getenv("VoAI_API_KEY") or os.getenv("VOAI_TOKEN")
    if not api_key:
        raise RuntimeError("缺少 VOAI_API_KEY，無法第一次生成固定音檔。")

    payload = {
        "model": voice_config.get("model", "NeoVoice-1-T"),
        "text": script,
        "speaker": voice_config.get("speaker"),
        "version": voice_config.get("version"),
        "style": voice_config.get("style"),
        "speed": voice_config.get("speed"),
        "pitch_shift": voice_config.get("pitch_shift"),
        "style_weight": voice_config.get("style_weight"),
        "breath_pause": voice_config.get("breath_pause"),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    response = requests.post(
        "https://connect.voai.ai/TTS/Speech",
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "x-output-format": "mp3",
        },
        json=payload,
        timeout=(6, 60),
    )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("VoAI 回傳空白音訊。")
    _write_bytes_atomic(cache_path, response.content)


def _cache_metadata_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".json")


def _payload_from_cached_files(
    *,
    metadata_path: Path,
    audio_path: Path,
    cached: bool,
) -> dict[str, object] | None:
    metadata = _load_cached_metadata(metadata_path)
    if metadata is None or not audio_path.is_file() or audio_path.stat().st_size <= 0:
        return None
    metadata["path"] = str(audio_path)
    metadata["audio_path"] = str(audio_path)
    metadata["cached"] = bool(cached)
    return metadata


def _build_cached_metadata(
    *,
    intent_name: str,
    title: str,
    text: str,
    version: str,
    character_id: str | None,
    extra_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "intent": intent_name,
        "title": title,
        "text": text,
        "version": version,
        "character_id": str(character_id or "").strip() or "default",
    }
    if extra_payload:
        payload.update(extra_payload)
    return payload


def _ensure_cached_audio_payload(
    *,
    intent_name: str,
    cache_dir: Path,
    audio_prefix: str,
    version: str,
    cache_identity: dict[str, object],
    voice_label: str,
    voice_config: dict[str, object],
    title: str,
    text_provider,
    synthesizer=None,
    extra_payload_builder=None,
    normalize_text: bool = True,
) -> dict[str, object]:
    audio_path = _build_fixed_audio_cache_path(
        cache_dir=cache_dir,
        prefix=audio_prefix,
        version=version,
        script=json.dumps(cache_identity, ensure_ascii=False, sort_keys=True),
        voice_config=voice_config,
        voice_label=voice_label,
    )
    metadata_path = _cache_metadata_path(audio_path)
    cached_payload = _payload_from_cached_files(
        metadata_path=metadata_path,
        audio_path=audio_path,
        cached=True,
    )
    if cached_payload is not None:
        return cached_payload

    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_text = str(text_provider() or "").strip()
    normalized_text = _normalize_reply_text(raw_text)
    if not normalized_text:
        raise RuntimeError(f"{intent_name} 文字生成失敗，未取得可播放內容。")
    final_text = normalized_text if normalize_text else raw_text

    temp_audio_path = audio_path.with_name(f"{audio_path.stem}.pending{audio_path.suffix}")
    temp_meta_path = metadata_path.with_name(f"{metadata_path.stem}.pending{metadata_path.suffix}")
    try:
        if temp_audio_path.exists():
            temp_audio_path.unlink()
        if temp_meta_path.exists():
            temp_meta_path.unlink()
        _synthesize_fixed_audio_to_file(
            script=final_text,
            voice_config=voice_config,
            cache_path=temp_audio_path,
            synthesizer=synthesizer,
        )
        if not temp_audio_path.is_file() or temp_audio_path.stat().st_size <= 0:
            raise RuntimeError("固定音檔生成失敗，未產出有效音檔。")
        extra_payload = (
            extra_payload_builder(final_text)
            if callable(extra_payload_builder)
            else {}
        )
        metadata = _build_cached_metadata(
            intent_name=intent_name,
            title=title,
            text=final_text,
            version=version,
            character_id=voice_label,
            extra_payload=extra_payload,
        )
        _write_json_atomic(temp_meta_path, metadata)
        temp_audio_path.replace(audio_path)
        temp_meta_path.replace(metadata_path)
    except Exception:
        temp_audio_path.unlink(missing_ok=True)
        temp_meta_path.unlink(missing_ok=True)
        raise
    return _payload_from_cached_files(
        metadata_path=metadata_path,
        audio_path=audio_path,
        cached=False,
    ) or {}


def _resolve_persona_prompt(character_id: str | None) -> tuple[str, str]:
    library = CharacterLibrary()
    manifest = library.get_character(character_id) if character_id else None
    persona_key = config.resolve_persona_key(
        (manifest or {}).get("persona_key"),
        character_id,
        (manifest or {}).get("name"),
    )
    return config.get_persona_prompt(persona_key), persona_key


def _resolve_model_name(character_id: str | None) -> str:
    library = CharacterLibrary()
    manifest = library.get_character(character_id) if character_id else None
    model_name = (
        str((manifest or {}).get("openai_model") or "").strip()
        or str((manifest or {}).get("model_name") or "").strip()
        or config.OPENAI_MODEL
    )
    return model_name or config.OPENAI_MODEL


def _build_system_message(content: str):
    if SystemMessage is None:
        return {"role": "system", "content": content}
    return SystemMessage(content=content)


def _build_human_message(content: str):
    if HumanMessage is None:
        return {"role": "user", "content": content}
    return HumanMessage(content=content)


def generate_fixed_intent_text(
    intent_name: str,
    character_id: str | None,
    llm_factory=None,
) -> str:
    definition = FIXED_INTENT_DEFINITIONS.get(intent_name)
    if definition is None:
        raise RuntimeError(f"未支援的固定意圖: {intent_name}")
    if not config.OPENAI_API_KEY:
        raise RuntimeError("缺少 OPENAI_API_KEY，無法第一次生成固定意圖文字。")
    if ChatOpenAI is None and not callable(llm_factory):
        raise RuntimeError("缺少 langchain_openai，無法第一次生成固定意圖文字。")

    persona_prompt, _persona_key = _resolve_persona_prompt(character_id)
    model_name = _resolve_model_name(character_id)
    llm = (
        llm_factory(model_name=model_name)
        if callable(llm_factory)
        else ChatOpenAI(
            api_key=config.OPENAI_API_KEY,
            model=model_name,
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.4")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
            timeout=(5, 45),
        )
    )
    messages = [
        _build_system_message(persona_prompt),
        _build_system_message(
            "這是一個本地固定快捷回覆生成任務，不是即時對話回覆。"
            "你不得輸出 [ACTION:...]、markdown、清單、emoji、自我介紹或額外說明。"
            "只輸出最終要顯示與朗讀的繁體中文短句。"
        ),
        _build_human_message(
            (
                f"使用者現在對你說：{definition['request_text']}\n"
                if str(definition.get("request_text") or "").strip()
                else ""
            )
            + str(definition["prompt"])
        ),
    ]
    response = llm.invoke(messages)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(
            str(getattr(part, "text", "") or part.get("text", "") if isinstance(part, dict) else part)
            for part in content
        )
    normalized = _normalize_reply_text(str(content or ""))
    if not normalized:
        raise RuntimeError("固定意圖文字生成失敗，未取得有效內容。")
    return normalized


def build_fixed_intent_payload(
    intent_name: str,
    character_id: str | None,
    *,
    cache_dir: str | Path | None = None,
    synthesizer=None,
    text_generator=None,
) -> dict[str, object]:
    definition = FIXED_INTENT_DEFINITIONS.get(intent_name)
    if definition is None:
        raise RuntimeError(f"未支援的固定意圖: {intent_name}")
    normalized_character_id = str(character_id or "").strip() or "default"
    voice_config = config.get_voai_config_for_character(character_id)
    generator = (
        text_generator
        if callable(text_generator)
        else lambda: generate_fixed_intent_text(intent_name, character_id)
    )
    payload = _ensure_cached_audio_payload(
        intent_name=intent_name,
        cache_dir=Path(cache_dir) if cache_dir else FIXED_INTENT_CACHE_DIR,
        audio_prefix=f"intent_{intent_name}",
        version=str(definition["version"]),
        cache_identity={
            "intent": intent_name,
            "character_id": normalized_character_id,
        },
        voice_label=normalized_character_id,
        voice_config=voice_config,
        title=str(definition["title"]),
        text_provider=generator,
        synthesizer=synthesizer,
        extra_payload_builder=lambda normalized_text: {
            "action_name": FIXED_INTENT_ACTIONS[intent_name],
            "label": FIXED_INTENT_LABELS[intent_name],
            "source_kind": intent_name,
        },
    )
    payload["intent"] = intent_name
    payload["action_name"] = FIXED_INTENT_ACTIONS[intent_name]
    return payload


def resolve_fixed_intent_from_text(text: str) -> str | None:
    normalized = str(text or "")
    best_match: tuple[int, int, str] | None = None
    for intent_name, keywords in FIXED_INTENT_KEYWORDS.items():
        for index, keyword in enumerate(keywords):
            position = normalized.find(keyword)
            if position < 0:
                continue
            candidate = (position, index, intent_name)
            if best_match is None or candidate < best_match:
                best_match = candidate
    return best_match[2] if best_match is not None else None


class NewsFetchWorker(QThread):
    """生成或讀取固定新聞播報音檔，不再走 RSS / LLM。"""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(
        self,
        feed_url: str | None = None,
        character_id: str | None = None,
        cache_dir: str | Path | None = None,
        synthesizer=None,
        parent=None,
    ):
        super().__init__(parent)
        self._feed_url = feed_url
        self._character_id = str(character_id or "").strip() or None
        self._cache_dir = Path(cache_dir) if cache_dir else NEWS_AUDIO_CACHE_DIR
        self._synthesizer = synthesizer

    def run(self):
        try:
            payload = _ensure_cached_audio_payload(
                intent_name="report_news",
                cache_dir=self._cache_dir,
                audio_prefix="news",
                version=FIXED_NEWS_VERSION,
                cache_identity={"script": FIXED_NEWS_SCRIPT},
                voice_label=self._character_id or "default",
                voice_config=self._voice_config(),
                title="固定新聞播報",
                text_provider=lambda: FIXED_NEWS_SCRIPT,
                synthesizer=self._synthesizer,
                normalize_text=False,
                extra_payload_builder=lambda normalized_text: {
                    "headline": normalized_text.splitlines()[0] if normalized_text.splitlines() else normalized_text,
                    "script": normalized_text,
                },
            )
            message = "固定新聞音檔已就緒。" if payload.get("cached") else "固定新聞音檔已生成。"
            self.finished_signal.emit(True, message, payload)
        except Exception as exc:
            self.finished_signal.emit(False, f"固定新聞音檔準備失敗: {exc}", None)

    def _voice_config(self) -> dict[str, object]:
        return config.get_voai_config_for_character(self._character_id)


class WaveGreetingWorker(QThread):
    """生成或讀取揮手 greeting 本地音檔，避免即時 TTS 造成動作被切斷。"""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(
        self,
        character_id: str | None = None,
        cache_dir: str | Path | None = None,
        synthesizer=None,
        parent=None,
    ):
        super().__init__(parent)
        self._character_id = str(character_id or "").strip() or None
        self._cache_dir = Path(cache_dir) if cache_dir else WAVE_AUDIO_CACHE_DIR
        self._synthesizer = synthesizer

    def run(self):
        try:
            payload = _ensure_cached_audio_payload(
                intent_name="wave_response",
                cache_dir=self._cache_dir,
                audio_prefix="wave",
                version=WAVE_GREETING_VERSION,
                cache_identity={"script": WAVE_GREETING_SCRIPT},
                voice_label=self._character_id or "default",
                voice_config=self._voice_config(),
                title=WAVE_GREETING_SCRIPT,
                text_provider=lambda: WAVE_GREETING_SCRIPT,
                synthesizer=self._synthesizer,
                normalize_text=False,
            )
            message = "揮手問候音檔已就緒。" if payload.get("cached") else "揮手問候音檔已生成。"
            self.finished_signal.emit(True, message, payload)
        except Exception as exc:
            self.finished_signal.emit(False, f"揮手問候音檔準備失敗: {exc}", None)

    def _voice_config(self) -> dict[str, object]:
        return config.get_voai_config_for_character(self._character_id)


class FixedIntentReplyWorker(QThread):
    """準備 `joke` / `share` 的固定文字與本地音檔快取。"""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(
        self,
        intent_name: str,
        character_id: str | None = None,
        cache_dir: str | Path | None = None,
        synthesizer=None,
        text_generator=None,
        parent=None,
    ):
        super().__init__(parent)
        self._intent_name = str(intent_name or "").strip().lower()
        self._character_id = str(character_id or "").strip() or None
        self._cache_dir = Path(cache_dir) if cache_dir else FIXED_INTENT_CACHE_DIR
        self._synthesizer = synthesizer
        self._text_generator = text_generator

    def run(self):
        try:
            payload = build_fixed_intent_payload(
                self._intent_name,
                self._character_id,
                cache_dir=self._cache_dir,
                synthesizer=self._synthesizer,
                text_generator=self._text_generator,
            )
            label = FIXED_INTENT_LABELS.get(self._intent_name, self._intent_name)
            message = f"{label} 固定回覆已就緒。" if payload.get("cached") else f"{label} 固定回覆已生成。"
            self.finished_signal.emit(True, message, payload)
        except Exception as exc:
            label = FIXED_INTENT_LABELS.get(self._intent_name, self._intent_name)
            self.finished_signal.emit(False, f"{label} 固定回覆準備失敗: {exc}", None)


class MusicSelectionWorker(QThread):
    """在背景執行緒掃描本地音樂並挑選可播放檔案。"""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, music_dir: str | Path | None = None, parent=None):
        super().__init__(parent)
        self._music_dir = Path(music_dir) if music_dir else UI_MUSIC_DIR

    def run(self):
        try:
            if not self._music_dir.is_dir():
                self.finished_signal.emit(False, f"找不到音樂資料夾: {self._music_dir}", None)
                return

            tracks = [
                path for path in self._music_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
            ]
            if not tracks:
                self.finished_signal.emit(False, f"音樂資料夾沒有可播放檔案: {self._music_dir}", None)
                return

            track = random.choice(tracks)
            payload = {"path": str(track), "title": track.stem}
            self.finished_signal.emit(True, track.stem, payload)
        except Exception as exc:
            self.finished_signal.emit(False, f"音樂挑選失敗: {exc}", None)
