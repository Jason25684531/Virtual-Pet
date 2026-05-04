"""
ECHOES smoke test for OpenAI streaming + VoAI speech playback.

請務必先進入 Ubuntu 24.04 專案虛擬環境後再執行：
    source venv/bin/activate
    python scripts/smoke_test.py
"""

from __future__ import annotations

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
    action_ms: int
    tts_start_ms: int
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
        self.restore_idle_calls = 0

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.motion_calls.append((motion_key, motion_path, loop, perf_counter()))
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

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

    if not openai_key:
        missing.append("OPENAI_API_KEY")
    if not openai_model:
        missing.append("OPENAI_MODEL")
    if not voai_key:
        missing.append("VOAI_API_KEY or VoAI_API_KEY")

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

    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=(5, 30),
            stream=True,
        )
    except requests.RequestException as exc:
        return CheckResult(
            name="VoAI",
            ok=False,
            detail=f"連線失敗: {exc}。請確認網路狀態與 VoAI 服務可用。",
        )

    try:
        if response.status_code == 401:
            return CheckResult(
                name="VoAI",
                ok=False,
                detail="收到 401。請檢查 `VOAI_API_KEY` 是否有效。",
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
    finally:
        response.close()

    return CheckResult(
        name="VoAI",
        ok=True,
        detail=f"已成功取得 PCM 串流測試音訊，大小 {len(received)} bytes。",
    )


class _SmokePcmPlayer:
    def is_available(self):
        return True

    def play_chunks(self, chunks):
        return sum(len(chunk) for chunk in chunks if chunk)


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

        def tracked_worker_factory(*args, **kwargs):
            worker = VoAIStreamingTTSWorker(
                *args,
                pcm_player_factory=_SmokePcmPlayer,
                **kwargs,
            )
            def _record_finished(success, _message, payload):
                if success:
                    timings.setdefault("tts_finished_at", perf_counter())
                    if isinstance(payload, dict):
                        timings.setdefault("tts_format", str(payload.get("format", "")))
            worker.progress_signal.connect(
                lambda event_name, payload: timings.setdefault(
                    "tts_stream_started_at",
                    perf_counter(),
                )
                if event_name == "stream_started"
                else None
            )
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
                if tracker.snapshot(trace_id) is None and "tts_finished_at" in timings:
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
    if "tts_stream_started_at" not in timings or "tts_finished_at" not in timings:
        return None, f"{trial_name}: TTS 沒有完整啟播或完成。"
    if timings.get("tts_format") != "pcm":
        return None, f"{trial_name}: 未使用 PCM 主路徑，實際格式={timings.get('tts_format', '<unknown>')}。"

    start_at = timings["start_at"]
    action_ms = round((window.motion_calls[0][3] - start_at) * 1000)
    tts_start_ms = round((timings.get("tts_stream_started_at", start_at) - start_at) * 1000)
    total_ms = round((timings.get("tts_finished_at", perf_counter()) - start_at) * 1000)
    return LatencySample(action_ms=action_ms, tts_start_ms=tts_start_ms, total_ms=total_ms), None


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
    tts_start_values = [sample.tts_start_ms for sample in measured_samples]
    total_values = [sample.total_ms for sample in measured_samples]
    median_action = round(statistics.median(action_values))
    median_tts_start = round(statistics.median(tts_start_values))
    median_total = round(statistics.median(total_values))
    fast_rounds = sum(1 for total_ms in total_values if total_ms <= LATENCY_SLA_MS)

    if median_total > LATENCY_SLA_MS:
        return CheckResult(
            name="LatencyProbe",
            ok=False,
            detail=(
                "多輪量測未達穩定低延遲門檻。"
                f" totals={total_values}ms, median_total={median_total}ms, "
                f"median_action={median_action}ms, median_tts_start={median_tts_start}ms, "
                f"fast_rounds={fast_rounds}/{measured_rounds}, sla_ms={LATENCY_SLA_MS}"
            ),
        )

    return CheckResult(
        name="LatencyProbe",
        ok=True,
        detail=(
            "多輪量測通過。"
            f" totals={total_values}ms, median_total={median_total}ms, "
            f"median_action={median_action}ms, median_tts_start={median_tts_start}ms, "
            f"fast_rounds={fast_rounds}/{measured_rounds}, sla_ms={LATENCY_SLA_MS}"
        ),
    )


def main() -> int:
    env_map = load_env_values()
    results = [
        check_env(env_map),
        check_openai(),
        check_voai(),
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
