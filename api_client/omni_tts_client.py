"""
ECHOES — char-Omni 本機 TTS(omni_tts_win_bundle 的 HTTP 服務)。

服務合成完才一次回傳整段 WAV(不是串流),這裡驗證完整 WAV 後以 BytesIO 走
audio_ready_signal → AudioStreamWorker 的容器播放路徑;不偽造 driver_started。
任何失敗都是終局失敗(critical_tts_failure),保留文字,不改用雲端。
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
import wave

import requests
from PyQt5.QtCore import QThread, pyqtSignal

import config
from api_client.tts_contract import TtsRequest

_LOGGER = logging.getLogger(__name__)

# connect 1 秒、read 4 秒,外加單次總 budget 5 秒:對齊 6 秒 action sync deadline。
_HTTP_TIMEOUT = (1, 4)
_TOTAL_BUDGET_SEC = 5.0


def _validate_wav(body: bytes) -> str:
    """完整 WAV 回傳空字串,否則回傳失敗原因。"""
    try:
        with wave.open(io.BytesIO(body), "rb") as w:
            frames = w.getnframes()
            if frames <= 0 or w.getnchannels() <= 0 or w.getsampwidth() <= 0 or w.getframerate() <= 0:
                return "empty_wav"
            expected = frames * w.getnchannels() * w.getsampwidth()
            if len(w.readframes(frames)) < expected:
                return "truncated_wav"
    except (wave.Error, EOFError):
        return "invalid_wav"
    return ""


class OmniHealthProbe:
    """背景 GET /health;狀態讀取只拿上次結果,GUI thread 不發 HTTP。"""

    def __init__(self, requests_get=None):
        self._get = requests_get or requests.get
        self._lock = threading.Lock()
        self._inflight = False
        self.ready = False
        self.last_error = "health_not_checked"

    def refresh(self) -> None:
        with self._lock:
            if self._inflight:
                return
            self._inflight = True
        threading.Thread(target=self._check, name="omni-tts-health", daemon=True).start()

    def _check(self) -> None:
        try:
            resp = self._get(f"{config.OMNI_TTS_BASE_URL}/health", timeout=(1, 2))
            ok = resp.status_code == 200 and resp.json().get("ok") is True
            self.ready, self.last_error = ok, "" if ok else f"omni_tts_health_http_{resp.status_code}"
        except (requests.RequestException, ValueError, AttributeError) as exc:
            self.ready, self.last_error = False, f"omni_tts_unreachable: {type(exc).__name__}"
        finally:
            with self._lock:
                self._inflight = False


OMNI_HEALTH = OmniHealthProbe()

OMNI_RUN_BAT = config.PROJECT_ROOT / "omni_tts_win_bundle" / "omni_tts_win_bundle" / "run.bat"


def ensure_omni_service_started(requests_get=None, popen=None) -> threading.Thread | None:
    """啟用時背景檢查 /health;沒開就以新主控台視窗跑 run.bat(暖機約 20 秒)。

    ponytail: 結束 main.py 不關服務 —— 下次啟動直接沿用、省暖機;要釋放 VRAM 就關掉那個視窗。
    """
    if not config.OMNI_TTS_ENABLED:
        return None
    import subprocess
    from urllib.parse import urlsplit

    get, popen = requests_get or requests.get, popen or subprocess.Popen

    def _run() -> None:
        try:
            if get(f"{config.OMNI_TTS_BASE_URL}/health", timeout=(1, 2)).status_code == 200:
                return
        except requests.RequestException:
            pass
        if not OMNI_RUN_BAT.is_file():
            _LOGGER.warning("[OMNI TTS] 服務未啟動且找不到 %s,char-omni 將無語音", OMNI_RUN_BAT)
            return
        env = {**os.environ, "PORT": str(urlsplit(config.OMNI_TTS_BASE_URL).port)}
        try:
            popen(["cmd", "/c", str(OMNI_RUN_BAT)], cwd=str(OMNI_RUN_BAT.parent), env=env,
                  creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            _LOGGER.info("[OMNI TTS] 已自動啟動 %s,等待 [ready]", OMNI_RUN_BAT)
        except OSError as exc:
            _LOGGER.warning("[OMNI TTS] 自動啟動失敗: %s", exc)

    thread = threading.Thread(target=_run, name="omni-tts-autostart", daemon=True)
    thread.start()
    return thread


class OmniLocalTTSWorker(QThread):
    finished_signal = pyqtSignal(bool, str, object)
    progress_signal = pyqtSignal(str, object)
    # (BytesIO wav, reply_id, trace_id)
    audio_ready_signal = pyqtSignal(object, str, str)

    def __init__(self, request: TtsRequest, parent=None, *, requests_post=None):
        super().__init__(parent)
        self._request = request
        self._requests_post = requests_post or requests.post
        self._cancelled = False

    def quit(self) -> None:
        # shortcut: 只能放棄等待/丟棄結果,無法取消 server 已在跑的 GPU 推論;
        # server 端單一推論 + busy 503 防止堆積。需要真正取消時再加取消協定。
        self._cancelled = True
        super().quit()

    def _discarded(self) -> bool:
        r = self._request
        return self._cancelled or (r.playback_guard is not None and not r.playback_guard(r.trace_id, r.reply_id))

    def _fail(self, message: str, code: str) -> None:
        r = self._request
        if self._discarded():  # 已被打斷/切換:不是服務故障,不觸發 critical 收尾
            self.finished_signal.emit(False, "本機語音已取消，丟棄晚到音訊。", {
                "reply_id": r.reply_id, "trace_id": r.trace_id, "suppressed": True,
            })
            return
        self.finished_signal.emit(False, message, {
            "reply_id": r.reply_id, "trace_id": r.trace_id, "failure_code": code, "critical_tts_failure": True,
        })

    def run(self) -> None:
        r = self._request
        text = str(r.text or "").strip()
        if not text:
            self.finished_signal.emit(False, "略過本機 TTS：沒有可朗讀的文字。", None)
            return
        started = time.monotonic()
        try:
            response = self._requests_post(
                f"{config.OMNI_TTS_BASE_URL}/tts", json={"text": text}, timeout=_HTTP_TIMEOUT
            )
        except requests.RequestException as exc:
            self._fail(f"本機語音服務無法連線({type(exc).__name__})，請先執行 omni_tts_win_bundle/omni_tts_win_bundle/run.bat 並等到 [ready]。", "connection_failed")
            return
        if response.status_code != 200:
            code = "busy" if response.status_code == 503 else f"http_{response.status_code}"
            self._fail(f"本機語音服務回應錯誤: {code}", code)
            return
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "wav" not in content_type:
            self._fail(f"本機語音服務回傳非 WAV: {content_type or 'none'}", "invalid_content_type")
            return
        body = response.content or b""
        problem = _validate_wav(body)
        if problem:
            self._fail("本機語音服務回傳的 WAV 不完整。", problem)
            return
        if time.monotonic() - started > _TOTAL_BUDGET_SEC:
            self._fail("本機語音合成超過時間預算。", "budget_exceeded")
            return
        if self._discarded():
            self._fail("", "discarded")  # _fail 會走 suppressed 分支
            return

        self.audio_ready_signal.emit(io.BytesIO(body), r.reply_id, r.trace_id)
        self.finished_signal.emit(True, "TTS 音訊取得完成，已送入播放佇列。", {
            "reply_id": r.reply_id,
            "trace_id": r.trace_id,
            "text": text,
            "bytes_forwarded": len(body),
            "queued_playback": True,
            "format": "wav",
            "transport": "http",
            "provider": "omni_local",
            "synth_seconds": response.headers.get("X-Synth-Seconds"),
        })
