"""主動打招呼的語音快取:同一(角色、聲線、模型、台詞)只合成一次,之後直接重播 PCM。

只快取首選 provider 的 PCM;發生 fallback、MP3、被抑制/打斷的結果一律不寫入,
否則一次暫時性失敗會把備援聲線永久固定成打招呼的聲音。
"""

from __future__ import annotations

import hashlib
import logging
import os

from PyQt5.QtCore import QThread, pyqtSignal

import config

LOGGER = logging.getLogger(__name__)

CACHE_DIR = config.PROJECT_ROOT / "runtime_cache" / "greetings"
# 小片是刻意的:interrupt() 只清佇列、不殺 ffplay,整段一塊送會讓切換角色後還播完數秒尾音。
_SLICE_BYTES = 4096
_FRAME_BYTES = 2  # s16le mono


def is_greeting(request) -> bool:
    return str(request.trace_id or "").startswith("greeting-")


def _key(request) -> str:
    raw = "\0".join((request.character_id, request.voice_id, request.model_id, request.text))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def load(request) -> tuple[bytes, int] | None:
    """命中回傳 (pcm, sample_rate);檔案缺失、損毀(空或非 frame 倍數)回傳 None,損毀檔順手刪掉。"""
    for path in CACHE_DIR.glob(f"{_key(request)}_*.pcm"):
        try:
            rate = int(path.stem.rsplit("_", 1)[1])
            pcm = path.read_bytes()
            if pcm and len(pcm) % _FRAME_BYTES == 0:
                return pcm, rate
            path.unlink(missing_ok=True)
        except (OSError, ValueError):
            LOGGER.warning("[GREETING CACHE] 讀取失敗,改為即時合成: %s", path, exc_info=True)
    return None


def store(request, pcm: bytes, sample_rate: int) -> None:
    if not pcm or len(pcm) % _FRAME_BYTES:
        return
    target = CACHE_DIR / f"{_key(request)}_{int(sample_rate)}.pcm"
    tmp = target.with_suffix(".tmp")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(pcm)
        os.replace(tmp, target)  # 原子替換,讀取端不會看到半個檔
    except OSError:
        LOGGER.warning("[GREETING CACHE] 寫入失敗: %s", target, exc_info=True)


class RecordingSink:
    """把 PCM 原樣轉交給真正的 sink,同時記下來供合成成功後寫入快取。"""

    def __init__(self, sink) -> None:
        self._sink = sink
        self.chunks: list[bytes] = []
        self.sample_rate: int | None = None

    def enqueue_pcm_chunk(self, chunk, reply_id, trace_id="", sample_rate=None):
        self._sink.enqueue_pcm_chunk(chunk, reply_id, trace_id, sample_rate=sample_rate)
        self.chunks.append(chunk)
        if self.sample_rate is None:
            self.sample_rate = sample_rate

    def finish_pcm_segment(self, reply_id, trace_id=""):
        self._sink.finish_pcm_segment(reply_id, trace_id)

    def store_if_clean(self, request, success: bool, payload: object, suppressed: bool) -> None:
        # attempted_providers 只有一個 = 沒發生 fallback(AdaptiveTTSFallbackWorker 的成功 payload 都帶)
        if not (success and not suppressed and self.sample_rate and isinstance(payload, dict)):
            return
        if payload.get("format") == "pcm" and len(payload.get("attempted_providers") or ()) == 1:
            store(request, b"".join(self.chunks), self.sample_rate)


class CachedGreetingWorker(QThread):
    """遵守 StreamingTTSWorker 四訊號契約的重播 worker,讓動作同步/輸入封鎖/播畢恢復都不用改。"""

    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    audio_ready_signal = pyqtSignal(object, str, str)

    def __init__(self, request, cached: tuple[bytes, int], parent=None) -> None:
        super().__init__(parent)
        self._request = request
        self._pcm, self._rate = cached

    def run(self) -> None:
        r = self._request
        try:
            for start in range(0, len(self._pcm), _SLICE_BYTES):
                r.pcm_stream_sink.enqueue_pcm_chunk(
                    self._pcm[start:start + _SLICE_BYTES], r.reply_id, r.trace_id, sample_rate=self._rate
                )
            r.pcm_stream_sink.finish_pcm_segment(r.reply_id, r.trace_id)
        except Exception as exc:  # noqa: BLE001 - 失敗回報成未播放,保留文字
            try:  # 已送出的分塊要收尾,否則 session 與動作會一直等
                r.pcm_stream_sink.finish_pcm_segment(r.reply_id, r.trace_id)
            except Exception:  # noqa: BLE001
                pass
            self.finished_signal.emit(False, f"快取語音播放失敗: {exc}", None)
            return
        self.finished_signal.emit(True, "TTS 音訊取得完成，已送入播放佇列。(cache)", {
            "reply_id": r.reply_id, "trace_id": r.trace_id, "text": r.text,
            "queued_playback": True, "format": "pcm", "cache_hit": True,
        })
