"""
ECHOES smoke test for token-first OpenAI streaming + synced VoAI playback.

請務必先進入 Ubuntu 24.04 專案虛擬環境後再執行：
    source venv/bin/activate
    python scripts/smoke_test.py
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import requests
from dotenv import load_dotenv
from PyQt5.QtCore import QCoreApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from action_dispatcher import ActionDispatcher
from api_client.adaptive_tts_fallback import AdaptiveTTSFallbackWorker
from api_client.brain_engine import BrainEngine, StreamedReplyParser, sanitize_tts_text
from api_client.voai_client import VoAIStreamingTTSWorker
from interaction_trace import InteractionLatencyTracker
from langchain_openai import ChatOpenAI
from main import connect_brain_output_handlers


ENV_PATH = PROJECT_ROOT / ".env"
LATENCY_SLA_MS = 1800


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class LatencySample:
    token_visible_ms: int
    action_ms: int
    driver_start_ms: int
    total_ms: int


class _SmokeLibrary:
    def get_current_character_id(self):
        return None

    def get_character(self, _character_id):
        return None


class _SmokeWindow:
    def __init__(self):
        self.status_calls: list[tuple[str, str, int]] = []
        self.motion_calls: list[tuple[str, str, bool, float]] = []
        self.assistant_chunks: dict[str, list[str]] = {}
        self.restore_idle_calls = 0

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.motion_calls.append((motion_key, motion_path, loop, perf_counter()))
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def append_conversation_assistant(self, trace_id: str, text: str):
        self.assistant_chunks.setdefault(trace_id, []).append(text)

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        del filename, title, update_status
        return True

    def stop_music(self):
        return None


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
    missing = []
    openai_key = os.getenv("OPENAI_API_KEY", "").strip() or env_map.get("OPENAI_API_KEY", "").strip()
    openai_model = os.getenv("OPENAI_MODEL", "").strip() or env_map.get("OPENAI_MODEL", "").strip()
    voai_key = (
        os.getenv("VOAI_API_KEY", "").strip()
        or os.getenv("VoAI_API_KEY", "").strip()
        or env_map.get("VOAI_API_KEY", "").strip()
        or env_map.get("VoAI_API_KEY", "").strip()
    )
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "").strip() or env_map.get("ELEVENLABS_API_KEY", "").strip()

    if not openai_key:
        missing.append("OPENAI_API_KEY")
    if not openai_model:
        missing.append("OPENAI_MODEL")
    if not voai_key:
        missing.append("VOAI_API_KEY or VoAI_API_KEY")
    if not elevenlabs_key:
        missing.append("ELEVENLABS_API_KEY")

    if missing:
        return CheckResult(
            name=".env",
            ok=False,
            detail=f"缺少必要欄位或值: {', '.join(missing)}",
        )

    return CheckResult(
        name=".env",
        ok=True,
        detail="已讀取到 OPENAI / VoAI 必要欄位（已隱藏敏感值）。",
    )


def check_openai() -> CheckResult:
    if not config.OPENAI_API_KEY:
        return CheckResult(name="OpenAI", ok=False, detail="缺少 OPENAI_API_KEY")

    llm = ChatOpenAI(
        api_key=config.OPENAI_API_KEY,
        model=config.OPENAI_MODEL,
        temperature=0,
        streaming=True,
        max_retries=1,
        timeout=(5, 30),
    )
    parser = StreamedReplyParser()
    seen_chunks: list[str] = []

    try:
        for chunk in llm.stream(
            "你是測試助手。請嚴格只輸出：[ACTION:listen] 我在喔！只能這樣輸出。"
        ):
            outputs = parser.feed(str(getattr(chunk, "content", "") or ""))
            seen_chunks.extend(outputs)
        seen_chunks.extend(parser.flush())
    except Exception as exc:
        return CheckResult(name="OpenAI", ok=False, detail=f"串流請求失敗: {exc}")

    if not seen_chunks:
        return CheckResult(name="OpenAI", ok=False, detail="OpenAI 有回應，但沒有切出任何片段。")
    if seen_chunks[0] != "[ACTION:listen]":
        return CheckResult(
            name="OpenAI",
            ok=False,
            detail=f"第一個片段不是 action 前綴，實際為: {seen_chunks[0]!r}",
        )

    return CheckResult(
        name="OpenAI",
        ok=True,
        detail=f"已成功串流並切出 action-first 片段: {seen_chunks[:3]!r}",
    )


def check_voai() -> CheckResult:
    api_key = os.getenv("VOAI_API_KEY", "").strip() or os.getenv("VoAI_API_KEY", "").strip()
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return CheckResult(
            name="VoAI",
            ok=False,
            detail="缺少必要欄位或值: VOAI_API_KEY",
        )

    endpoint = "https://connect.voai.ai/TTS/Speech"
    headers = {
        "x-api-key": api_key,
        "x-output-format": "pcm",
        "x-sample-rate": "32000",
        "Content-Type": "application/json",
    }
    payload = {
        "version": "Classic",
        "text": "好。",
        "speaker": "柔洢",
        "style": "預設",
        "speed": 1.2,
        "pitch_shift": 0,
        "style_weight": 0,
        "breath_pause": 0,
    }
    last_failure = "未知錯誤"
    for attempt in range(3):
        response = None
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=(5, 30),
                stream=True,
            )
            if response.status_code == 401:
                return CheckResult(
                    name="VoAI",
                    ok=False,
                    detail="收到 401。請檢查 `VOAI_API_KEY` 是否有效。",
                )
            if response.status_code == 529 and attempt < 2:
                last_failure = f"HTTP 529: {response.text[:200]}"
                time.sleep(1.0 + attempt)
                continue
            if response.status_code == 529 and elevenlabs_key:
                return CheckResult(
                    name="VoAI",
                    ok=True,
                    detail="VoAI 目前回 529，但 adaptive fallback 可改由 ElevenLabs 接手；主路徑已視為降級可用。",
                )
            if not response.ok:
                return CheckResult(
                    name="VoAI",
                    ok=False,
                    detail=f"HTTP {response.status_code}: {response.text[:200]}",
                )

            content_type = response.headers.get("content-type", "").lower()
            received = b"".join(chunk for chunk in response.iter_content(chunk_size=4096) if chunk)
            if "audio" not in content_type or not received:
                return CheckResult(
                    name="VoAI",
                    ok=False,
                    detail="API 有回應，但不是有效音訊資料。",
                )
            return CheckResult(
                name="VoAI",
                ok=True,
                detail=f"已成功取得 PCM 串流測試音訊，大小 {len(received)} bytes。",
            )
        except requests.RequestException as exc:
            last_failure = f"連線失敗: {exc}。請確認網路狀態與 VoAI 服務可用。"
            if attempt < 2:
                time.sleep(1.0 + attempt)
                continue
        finally:
            if response is not None:
                response.close()

    return CheckResult(name="VoAI", ok=False, detail=last_failure)


class _SmokePcmPlayer:
    def is_available(self):
        return True

    def play_chunks(self, chunks, before_start=None):
        iterator = iter(chunks)
        bytes_written = 0
        started = False
        for chunk in iterator:
            if not chunk:
                continue
            if not started:
                started = True
                if callable(before_start) and before_start() is False:
                    return 0
            bytes_written += len(chunk)
        return bytes_written


class _LateSuppressedTTSWorker:
    instances: list["_LateSuppressedTTSWorker"] = []

    def __init__(
        self,
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        playback_guard=None,
        parent=None,
    ):
        del parent, voice_id
        self._text = text
        self._reply_id = reply_id or "late-suppressed-reply"
        self._trace_id = trace_id or ""
        self._playback_guard = playback_guard
        self.finished_signal = _SignalRecorder()
        self.progress_signal = _SignalRecorder()
        self.audio_ready_signal = _SignalRecorder()
        self.finished = _SignalRecorder()
        self.started = False
        _LateSuppressedTTSWorker.instances.append(self)

    def start(self):
        self.started = True
        self.progress_signal.emit(
            "stream_started",
            {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
                "bytes_forwarded": len(self._text.encode("utf-8")),
            },
        )

    def complete(self):
        payload = {
            "reply_id": self._reply_id,
            "trace_id": self._trace_id,
            "text": self._text,
        }
        if callable(self._playback_guard) and self._playback_guard(self._trace_id, self._reply_id) is False:
            payload["suppressed"] = True
            self.finished_signal.emit(False, "因 timeout_promoted 抑制晚到音訊。", payload)
            self.finished.emit()
            return
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self._reply_id,
                "trace_id": self._trace_id,
            },
        )
        self.finished_signal.emit(True, "語音生成完成。", payload)
        self.finished.emit()

    def deleteLater(self):
        return None


class _SignalRecorder:
    def __init__(self):
        self._callbacks: list[object] = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _MockVoAI529Worker:
    instances: list["_MockVoAI529Worker"] = []

    def __init__(self, *args, adaptive_fallback_enabled: bool = False, **kwargs):
        self.reply_id = kwargs.get("reply_id") or "mock-voai-reply"
        self.trace_id = kwargs.get("trace_id") or ""
        self.adaptive_fallback_enabled = adaptive_fallback_enabled
        self.finished_signal = _SignalRecorder()
        self.progress_signal = _SignalRecorder()
        self.audio_ready_signal = _SignalRecorder()
        self.finished = _SignalRecorder()
        _MockVoAI529Worker.instances.append(self)

    def start(self):
        self.finished_signal.emit(
            False,
            "VoAI 529",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "voai",
                "fast_fail": True,
                "failure_code": "http_529",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


class _MockElevenLabsSuccessWorker:
    instances: list["_MockElevenLabsSuccessWorker"] = []

    def __init__(self, *args, **kwargs):
        self.reply_id = kwargs.get("reply_id") or "mock-eleven-reply"
        self.trace_id = kwargs.get("trace_id") or ""
        self.finished_signal = _SignalRecorder()
        self.progress_signal = _SignalRecorder()
        self.audio_ready_signal = _SignalRecorder()
        self.finished = _SignalRecorder()
        _MockElevenLabsSuccessWorker.instances.append(self)

    def start(self):
        self.progress_signal.emit(
            "driver_started",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
            },
        )
        self.finished_signal.emit(
            True,
            "ElevenLabs fallback ok",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
                "selected_provider": "elevenlabs",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


class _MockElevenLabsFailureWorker:
    instances: list["_MockElevenLabsFailureWorker"] = []

    def __init__(self, *args, **kwargs):
        self.reply_id = kwargs.get("reply_id") or "mock-eleven-fail-reply"
        self.trace_id = kwargs.get("trace_id") or ""
        self.finished_signal = _SignalRecorder()
        self.progress_signal = _SignalRecorder()
        self.audio_ready_signal = _SignalRecorder()
        self.finished = _SignalRecorder()
        _MockElevenLabsFailureWorker.instances.append(self)

    def start(self):
        self.finished_signal.emit(
            False,
            "ElevenLabs fallback failed",
            {
                "reply_id": self.reply_id,
                "trace_id": self.trace_id,
                "provider": "elevenlabs",
            },
        )
        self.finished.emit()

    def deleteLater(self):
        return None


def _extract_milestone_ms(summary: dict[str, object], stage_name: str) -> int | None:
    milestones = summary.get("milestones", [])
    if not isinstance(milestones, list):
        return None
    prefix = f"{stage_name}="
    for item in milestones:
        if not isinstance(item, str) or not item.startswith(prefix) or not item.endswith("ms"):
            continue
        raw_value = item[len(prefix):-2]
        try:
            return int(raw_value)
        except ValueError:
            continue
    return None


def _build_mock_tts_worker_factory(mode: str):
    elevenlabs_factory = (
        _MockElevenLabsFailureWorker if mode == "double-fail" else _MockElevenLabsSuccessWorker
    )

    def factory(
        text: str,
        reply_id: str | None = None,
        trace_id: str | None = None,
        voice_id: str | None = None,
        fallback_voice_id: str | None = None,
        preferred_provider: str | None = None,
        playback_guard=None,
        parent=None,
    ):
        return AdaptiveTTSFallbackWorker(
            text=text,
            reply_id=reply_id,
            trace_id=trace_id,
            voice_id=voice_id,
            fallback_voice_id=fallback_voice_id,
            preferred_provider=preferred_provider,
            playback_guard=playback_guard,
            voai_worker_factory=_MockVoAI529Worker,
            elevenlabs_worker_factory=elevenlabs_factory,
            parent=parent,
        )

    return factory


def check_mock_tts_fail(mode: str) -> CheckResult:
    app = QCoreApplication.instance() or QCoreApplication([])
    tracker = InteractionLatencyTracker()
    _MockVoAI529Worker.instances.clear()
    _MockElevenLabsSuccessWorker.instances.clear()
    _MockElevenLabsFailureWorker.instances.clear()
    with tempfile.TemporaryDirectory(prefix="echoes-mock-fallback-") as temp_dir:
        listen_path = Path(temp_dir) / "listen.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        listen_path.write_bytes(b"listen")
        idle_path.write_bytes(b"idle")

        window = _SmokeWindow()
        dispatcher = ActionDispatcher(
            window,
            library=_SmokeLibrary(),
            motion_path_resolver=lambda motion_key: str(
                {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
            ),
            tts_worker_factory=_build_mock_tts_worker_factory(mode),
            latency_tracker=tracker,
        )
        trace_id = tracker.begin_interaction("mock-fallback", "請測試 fallback")
        tracker.mark_brain_started(trace_id)
        tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")
        dispatcher.dispatch("[ACTION:listen] 第一段測試。", trace_id=trace_id)
        dispatcher.speak_text("第二段測試。", trace_id=trace_id)
        tracker.mark_brain_completed(trace_id)
        app.processEvents()
        summary = tracker.get_completed_trace(trace_id)
        dispatcher.shutdown()

    if summary is None:
        return CheckResult(name="MockFallback", ok=False, detail="mock fallback 未產生完成 trace。")
    if mode == "double-fail":
        if not summary.get("text_only_completed"):
            return CheckResult(name="MockFallback", ok=False, detail="雙重失敗時未標記文字-only 完成。")
        if _extract_milestone_ms(summary, "first_driver_started") is not None:
            return CheckResult(name="MockFallback", ok=False, detail="雙重失敗時不應留下 driver_started。")
        return CheckResult(
            name="MockFallback",
            ok=True,
            detail="已驗證 VoAI 529 + ElevenLabs 失敗時會改為文字-only，且不會誤觸發 driver_started。",
        )

    if summary.get("selected_tts_provider") != "elevenlabs":
        return CheckResult(name="MockFallback", ok=False, detail="fallback 後沒有鎖定 elevenlabs。")
    if not summary.get("fallback_triggered"):
        return CheckResult(name="MockFallback", ok=False, detail="trace 未記錄 voai_failed_triggering_fallback。")
    if _extract_milestone_ms(summary, "first_driver_started") is None:
        return CheckResult(name="MockFallback", ok=False, detail="fallback 成功時缺少 driver_started。")
    if len(_MockVoAI529Worker.instances) != 1 or len(_MockElevenLabsSuccessWorker.instances) != 2:
        return CheckResult(
            name="MockFallback",
            ok=False,
            detail=(
                "同 trace 後續句段未沿用 fallback provider。"
                f" voai={len(_MockVoAI529Worker.instances)} eleven={len(_MockElevenLabsSuccessWorker.instances)}"
            ),
        )
    return CheckResult(
        name="MockFallback",
        ok=True,
        detail="已驗證 --mock-tts-fail 會觸發 VoAI 529 fallback，driver_started 可對齊，且後續句段沿用 ElevenLabs。",
    )


def _run_latency_trial(app: QCoreApplication, trial_name: str) -> tuple[LatencySample | None, str | None]:
    tracker = InteractionLatencyTracker()
    timings: dict[str, float] = {}
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="echoes-latency-probe-") as temp_dir:
        listen_path = Path(temp_dir) / "listen.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        listen_path.write_bytes(b"listen")
        idle_path.write_bytes(b"idle")

        window = _SmokeWindow()

        def _tracked_voai_worker_factory(
            text: str,
            reply_id: str | None = None,
            trace_id: str | None = None,
            voice_id: str | None = None,
            requests_post=None,
            pcm_player_factory=None,
            playback_guard=None,
            adaptive_fallback_enabled: bool = False,
            parent=None,
        ):
            return VoAIStreamingTTSWorker(
                text=text,
                reply_id=reply_id,
                trace_id=trace_id,
                voice_id=voice_id,
                requests_post=requests_post,
                pcm_player_factory=pcm_player_factory or _SmokePcmPlayer,
                playback_guard=playback_guard,
                adaptive_fallback_enabled=adaptive_fallback_enabled,
                parent=parent,
            )

        def tracked_worker_factory(
            text: str,
            reply_id: str | None = None,
            trace_id: str | None = None,
            voice_id: str | None = None,
            fallback_voice_id: str | None = None,
            preferred_provider: str | None = None,
            playback_guard=None,
            parent=None,
        ):
            worker = AdaptiveTTSFallbackWorker(
                text=text,
                reply_id=reply_id,
                trace_id=trace_id,
                voice_id=voice_id,
                fallback_voice_id=fallback_voice_id,
                preferred_provider=preferred_provider,
                playback_guard=playback_guard,
                voai_worker_factory=_tracked_voai_worker_factory,
                parent=parent,
            )

            def _record_finished(success, _message, payload):
                timings.setdefault("tts_finished_at", perf_counter())
                if success and isinstance(payload, dict):
                    timings.setdefault("tts_format", str(payload.get("format", "")))

            def _record_progress(event_name, _payload):
                if event_name == "stream_started":
                    timings.setdefault("tts_stream_started_at", perf_counter())
                elif event_name == "driver_started":
                    timings.setdefault("driver_started_at", perf_counter())

            worker.progress_signal.connect(_record_progress)
            worker.finished_signal.connect(_record_finished)
            return worker

        dispatcher = ActionDispatcher(
            window,
            library=_SmokeLibrary(),
            motion_path_resolver=lambda motion_key: str(
                {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
            ),
            tts_worker_factory=tracked_worker_factory,
            latency_tracker=tracker,
        )
        window.dispatch_action = dispatcher.dispatch
        window.speak_text = dispatcher.speak_text
        brain = BrainEngine(library=_SmokeLibrary(), latency_tracker=tracker)
        brain.warning_emitted.connect(warnings.append)
        connect_brain_output_handlers(window, brain, sanitize_tts_text)
        brain.start()

        input_text = "請嚴格只回：[ACTION:listen] 我在喔！只能這樣回。"
        trace_id = tracker.begin_interaction("stt-smoke", input_text)
        timings["start_at"] = perf_counter()
        brain.send_to_brain(input_text, trace_id=trace_id)

        try:
            deadline = perf_counter() + 15
            while perf_counter() < deadline:
                app.processEvents()
                if tracker.snapshot(trace_id) is None and (
                    "tts_finished_at" in timings or tracker.get_completed_trace(trace_id) is not None
                ):
                    break
                time.sleep(0.01)
            else:
                return None, f"{trial_name}: 等待互動完成逾時（15 秒）。"
        finally:
            brain.stop()
            brain.quit()
            if brain.isRunning():
                brain.wait(3000)

    if warnings:
        return None, f"{trial_name}: 執行期間收到警告: {warnings[0]}"
    if not window.motion_calls:
        return None, f"{trial_name}: 沒有觸發任何動作影片。"
    if "tts_stream_started_at" not in timings or "driver_started_at" not in timings or "tts_finished_at" not in timings:
        return None, f"{trial_name}: TTS 沒有完整完成 stream_started -> driver_started -> finished。"
    visible_chunks = window.assistant_chunks.get(trace_id, [])
    if not visible_chunks:
        return None, f"{trial_name}: token-first UI 沒有收到任何可見文字。"
    if any("[ACTION:" in chunk for chunk in visible_chunks):
        return None, f"{trial_name}: token-first UI 不應顯示 action tag，實際 chunks={visible_chunks!r}"

    summary = tracker.get_completed_trace(trace_id)
    if summary is None:
        return None, f"{trial_name}: 找不到完成後的 trace 摘要。"

    token_visible_ms = _extract_milestone_ms(summary, "first_token_visible")
    action_ms = _extract_milestone_ms(summary, "first_action_dispatched")
    driver_start_ms = _extract_milestone_ms(summary, "first_driver_started")
    total_ms = summary.get("total_ms")
    if not isinstance(token_visible_ms, int):
        return None, f"{trial_name}: trace 缺少 first_token_visible 里程碑。"
    if not isinstance(action_ms, int):
        return None, f"{trial_name}: trace 缺少 first_action_dispatched 里程碑。"
    if not isinstance(driver_start_ms, int):
        return None, f"{trial_name}: trace 缺少 first_driver_started 里程碑。"
    if not isinstance(total_ms, int):
        return None, f"{trial_name}: trace 缺少 total_ms。"
    if summary.get("timeout_promoted"):
        return None, f"{trial_name}: 正常 smoke flow 不應觸發 timeout_promoted。"
    if "tts_to_driver_start" not in dict(summary.get("stage_durations", {})):
        return None, f"{trial_name}: trace 缺少 tts_to_driver_start 階段摘要。"

    return LatencySample(
        token_visible_ms=token_visible_ms,
        action_ms=action_ms,
        driver_start_ms=driver_start_ms,
        total_ms=total_ms,
    ), None


def check_timeout_promotion_guard() -> CheckResult:
    app = QCoreApplication.instance() or QCoreApplication([])
    tracker = InteractionLatencyTracker()
    _LateSuppressedTTSWorker.instances.clear()
    with tempfile.TemporaryDirectory(prefix="echoes-timeout-guard-") as temp_dir:
        listen_path = Path(temp_dir) / "listen.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        listen_path.write_bytes(b"listen")
        idle_path.write_bytes(b"idle")

        window = _SmokeWindow()
        dispatcher = ActionDispatcher(
            window,
            library=_SmokeLibrary(),
            motion_path_resolver=lambda motion_key: str(
                {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
            ),
            tts_worker_factory=_LateSuppressedTTSWorker,
            latency_tracker=tracker,
        )
        trace_id = tracker.begin_interaction("timeout-guard", "請晚點說話")
        tracker.mark_brain_started(trace_id)
        tracker.mark_fragment_emitted(trace_id, "[ACTION:listen]")
        dispatcher.dispatch("[ACTION:listen] 這句要被超時抑制。", trace_id=trace_id)
        tracker.mark_brain_completed(trace_id)
        dispatcher._promote_pending_action(trace_id)

        if len(window.motion_calls) != 1:
            dispatcher.shutdown()
            return CheckResult(
                name="TimeoutGuard",
                ok=False,
                detail=f"超時升級後應只切一次正式動作，實際 motion_calls={window.motion_calls!r}",
            )
        if not _LateSuppressedTTSWorker.instances:
            dispatcher.shutdown()
            return CheckResult(name="TimeoutGuard", ok=False, detail="沒有建立待驗證的 TTS worker。")

        _LateSuppressedTTSWorker.instances[0].complete()
        app.processEvents()
        summary = tracker.get_completed_trace(trace_id)
        dispatcher.shutdown()

    if summary is None:
        return CheckResult(name="TimeoutGuard", ok=False, detail="超時抑制後沒有完成 trace 摘要。")
    if summary.get("timeout_promoted") is not True:
        return CheckResult(name="TimeoutGuard", ok=False, detail="trace 未標記 timeout_promoted。")
    if _extract_milestone_ms(summary, "first_driver_started") is not None:
        return CheckResult(name="TimeoutGuard", ok=False, detail="晚到音訊被抑制時不應留下 first_driver_started。")
    if summary.get("tts_failures") != 1:
        return CheckResult(
            name="TimeoutGuard",
            ok=False,
            detail=f"晚到音訊抑制應被記錄成單次 TTS failure，實際={summary.get('tts_failures')!r}",
        )
    return CheckResult(
        name="TimeoutGuard",
        ok=True,
        detail="已驗證 timeout_promoted 會升級正式動作，並抑制晚到音訊與重複 motion。",
    )


def run_latency_probe() -> CheckResult:
    app = QCoreApplication.instance() or QCoreApplication([])
    warmup_rounds = 1
    measured_rounds = 3

    for warmup_index in range(warmup_rounds):
        _sample, error = _run_latency_trial(app, f"warmup-{warmup_index + 1}")
        if error:
            return CheckResult(name="LatencyProbe", ok=False, detail=error)

    measured_samples: list[LatencySample] = []
    for trial_index in range(measured_rounds):
        sample, error = _run_latency_trial(app, f"measure-{trial_index + 1}")
        if error:
            return CheckResult(name="LatencyProbe", ok=False, detail=error)
        if sample is not None:
            measured_samples.append(sample)

    if len(measured_samples) != measured_rounds:
        return CheckResult(
            name="LatencyProbe",
            ok=False,
            detail=f"量測輪數不足，預期 {measured_rounds} 輪，實際 {len(measured_samples)} 輪。",
        )

    action_values = [sample.action_ms for sample in measured_samples]
    token_visible_values = [sample.token_visible_ms for sample in measured_samples]
    driver_start_values = [sample.driver_start_ms for sample in measured_samples]
    total_values = [sample.total_ms for sample in measured_samples]
    median_token = round(statistics.median(token_visible_values))
    median_action = round(statistics.median(action_values))
    median_driver_start = round(statistics.median(driver_start_values))
    median_total = round(statistics.median(total_values))
    fast_rounds = sum(1 for total_ms in total_values if total_ms <= LATENCY_SLA_MS)

    if median_total > LATENCY_SLA_MS:
        return CheckResult(
            name="LatencyProbe",
            ok=False,
            detail=(
                "多輪量測未達穩定低延遲門檻。"
                f" totals={total_values}ms, median_total={median_total}ms, "
                f"median_token={median_token}ms, median_action={median_action}ms, "
                f"median_driver_start={median_driver_start}ms, "
                f"fast_rounds={fast_rounds}/{measured_rounds}, sla_ms={LATENCY_SLA_MS}"
            ),
        )

    return CheckResult(
        name="LatencyProbe",
        ok=True,
        detail=(
            "多輪量測通過。"
            f" totals={total_values}ms, median_total={median_total}ms, "
            f"median_token={median_token}ms, median_action={median_action}ms, "
            f"median_driver_start={median_driver_start}ms, "
            f"fast_rounds={fast_rounds}/{measured_rounds}, sla_ms={LATENCY_SLA_MS}"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ECHOES smoke test")
    parser.add_argument(
        "--mock-tts-fail",
        choices=("voai529", "double-fail"),
        help="以故障注入模式驗證 Adaptive TTS fallback，不跑真實雲端 smoke。",
    )
    args = parser.parse_args()

    if args.mock_tts_fail:
        results = [check_mock_tts_fail(args.mock_tts_fail)]
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

    env_map = load_env_values()
    results = [
        check_env(env_map),
        check_openai(),
        check_voai(),
        check_timeout_promotion_guard(),
        run_latency_probe(),
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
