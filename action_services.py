"""
ECHOES — Host action services
在背景執行緒中執行新聞抓取與本地音樂挑選，避免阻塞 PyQt UI。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config
from character_library import PROJECT_ROOT, UI_MUSIC_DIR


FIXED_NEWS_VERSION = "fixed-news-2026-05-06-v1"
FIXED_NEWS_SCRIPT = "\n".join(
    [
        "被盜！《人機迷網》黛安娜「駭入」 《惡靈古堡》官方帳號",
        "《澀谷交叉物語》公開卡司陣容、 《428》部分原班人馬重聚",
        "Valve 宣布最新 STEAM 控制器 5 月 5 日上市　台灣等地售價公開",
    ]
)
NEWS_AUDIO_CACHE_DIR = PROJECT_ROOT / "runtime_cache" / "news_audio"
WAVE_GREETING_VERSION = "wave-greeting-2026-05-06-v1"
WAVE_GREETING_SCRIPT = "hi~"
WAVE_AUDIO_CACHE_DIR = PROJECT_ROOT / "runtime_cache" / "wave_audio"
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}


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
    cache_path.write_bytes(response.content)


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
            cache_path = self._cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            if cache_path.is_file() and cache_path.stat().st_size > 0:
                self.finished_signal.emit(True, "固定新聞音檔已就緒。", self._payload(cache_path, cached=True))
                return

            self._synthesize_to_file(cache_path)
            self.finished_signal.emit(True, "固定新聞音檔已生成。", self._payload(cache_path, cached=False))
        except Exception as exc:
            self.finished_signal.emit(False, f"固定新聞音檔準備失敗: {exc}", None)

    def _payload(self, cache_path: Path, cached: bool) -> dict[str, object]:
        return {
            "headline": FIXED_NEWS_SCRIPT.splitlines()[0],
            "script": FIXED_NEWS_SCRIPT,
            "title": "固定新聞播報",
            "path": str(cache_path),
            "cached": cached,
            "version": FIXED_NEWS_VERSION,
        }

    def _cache_path(self) -> Path:
        voice_config = self._voice_config()
        return _build_fixed_audio_cache_path(
            cache_dir=self._cache_dir,
            prefix="news",
            version=FIXED_NEWS_VERSION,
            script=FIXED_NEWS_SCRIPT,
            voice_config=voice_config,
            voice_label=self._character_id or "default",
        )

    def _voice_config(self) -> dict[str, object]:
        return config.get_voai_config_for_character(self._character_id)

    def _synthesize_to_file(self, cache_path: Path):
        _synthesize_fixed_audio_to_file(
            script=FIXED_NEWS_SCRIPT,
            voice_config=self._voice_config(),
            cache_path=cache_path,
            synthesizer=self._synthesizer,
        )


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
            cache_path = self._cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            if cache_path.is_file() and cache_path.stat().st_size > 0:
                self.finished_signal.emit(True, "揮手問候音檔已就緒。", self._payload(cache_path, cached=True))
                return

            self._synthesize_to_file(cache_path)
            self.finished_signal.emit(True, "揮手問候音檔已生成。", self._payload(cache_path, cached=False))
        except Exception as exc:
            self.finished_signal.emit(False, f"揮手問候音檔準備失敗: {exc}", None)

    def _payload(self, cache_path: Path, cached: bool) -> dict[str, object]:
        return {
            "text": WAVE_GREETING_SCRIPT,
            "title": "hi~",
            "path": str(cache_path),
            "cached": cached,
            "version": WAVE_GREETING_VERSION,
        }

    def _cache_path(self) -> Path:
        voice_config = self._voice_config()
        return _build_fixed_audio_cache_path(
            cache_dir=self._cache_dir,
            prefix="wave",
            version=WAVE_GREETING_VERSION,
            script=WAVE_GREETING_SCRIPT,
            voice_config=voice_config,
            voice_label=self._character_id or "default",
        )

    def _voice_config(self) -> dict[str, object]:
        return config.get_voai_config_for_character(self._character_id)

    def _synthesize_to_file(self, cache_path: Path):
        _synthesize_fixed_audio_to_file(
            script=WAVE_GREETING_SCRIPT,
            voice_config=self._voice_config(),
            cache_path=cache_path,
            synthesizer=self._synthesizer,
        )


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
