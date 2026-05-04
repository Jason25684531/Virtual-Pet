"""
ECHOES live STT latency probe.

在真實桌面與麥克風環境下，量測 Azure speech end 到 action / 首次音訊 / 整輪完成。
請先進入專案虛擬環境後執行：
    source venv/bin/activate
    python scripts/live_stt_latency_probe.py
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QCoreApplication

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from action_dispatcher import ActionDispatcher
from api_client.brain_engine import BrainEngine, sanitize_tts_text
from interaction_trace import InteractionLatencyTracker
from interaction_turn_manager import InteractionTurnManager
from main import connect_brain_output_handlers
from sensors.stt_session_controller import STTSessionController


WARMUP_ROUNDS = 1
MEASURED_ROUNDS = 5
ROUND_TIMEOUT_S = 25
RECOMMENDED_PHRASE = "哈囉喬巴，你在嗎？"


@dataclass(frozen=True)
class ProbeSample:
    trace_id: str
    recognized_text: str
    eos_to_first_action: int
    eos_to_first_audio: int
    eos_to_complete: int
    stt_tail: int | None
    total_ms: int


class _ProbeLibrary:
    def get_current_character_id(self):
        return "Choppr"

    def get_character(self, _character_id):
        return None


class _ProbeWindow:
    def __init__(self):
        self.status_calls: list[tuple[str, str, int]] = []
        self.motion_calls: list[tuple[str, str, bool]] = []
        self.restore_idle_calls = 0

    def set_action_status(self, message: str, tone: str = "idle", timeout_ms: int = 0):
        self.status_calls.append((message, tone, timeout_ms))
        print(f"[PROBE][STATUS] {tone}: {message}")

    def play_resolved_motion(self, motion_key: str, motion_path: str, loop: bool = False) -> bool:
        self.motion_calls.append((motion_key, motion_path, loop))
        return True

    def restore_idle_video(self) -> bool:
        self.restore_idle_calls += 1
        return True

    def play_music(self, filename: str, title: str = "", update_status: bool = True) -> bool:
        del filename, title, update_status
        return True

    def stop_music(self):
        return None


def _extract_sample(summary: dict[str, object]) -> ProbeSample | None:
    stages = summary.get("stage_durations", {})
    if not isinstance(stages, dict):
        return None
    eos_to_first_action = stages.get("eos_to_first_action")
    eos_to_first_audio = stages.get("eos_to_first_audio")
    eos_to_complete = stages.get("eos_to_complete")
    total_ms = summary.get("total_ms")
    if not isinstance(eos_to_first_action, int):
        return None
    if not isinstance(eos_to_first_audio, int):
        return None
    if not isinstance(eos_to_complete, int):
        return None
    if not isinstance(total_ms, int):
        total_ms = eos_to_complete
    stt_tail = stages.get("stt_tail")
    return ProbeSample(
        trace_id=str(summary.get("trace_id", "")),
        recognized_text=str(summary.get("input_text", "")),
        eos_to_first_action=eos_to_first_action,
        eos_to_first_audio=eos_to_first_audio,
        eos_to_complete=eos_to_complete,
        stt_tail=stt_tail if isinstance(stt_tail, int) else None,
        total_ms=total_ms,
    )


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication([])
    tracker = InteractionLatencyTracker()
    window = _ProbeWindow()
    brain = BrainEngine(latency_tracker=tracker)
    turn_manager = InteractionTurnManager(brain, tracker)
    stt_controller = STTSessionController(latency_tracker=tracker)
    warnings: list[str] = []
    captured_samples: list[ProbeSample] = []
    pending_trace: dict[str, str] = {"trace_id": ""}

    with tempfile.TemporaryDirectory(prefix="echoes-live-stt-probe-") as temp_dir:
        listen_path = Path(temp_dir) / "listen.webm"
        idle_path = Path(temp_dir) / "Idle.webm"
        listen_path.write_bytes(b"listen")
        idle_path.write_bytes(b"idle")

        dispatcher = ActionDispatcher(
            window,
            library=_ProbeLibrary(),
            motion_path_resolver=lambda motion_key: str(
                {"listen": listen_path, "idle": idle_path}.get(motion_key, "")
            ),
            latency_tracker=tracker,
        )
        window.dispatch_action = dispatcher.dispatch
        window.speak_text = dispatcher.speak_text

        def handle_recognized(text: str, trace_id: str | None):
            preview = text if len(text) <= 40 else f"{text[:40]}..."
            print(f"[PROBE] STT finalized: {preview}")
            result = turn_manager.submit("stt", text, trace_id=trace_id)
            pending_trace["trace_id"] = str(result.get("trace_id") or trace_id or "")

        def handle_turn_completed(trace_id: str, _source: str, _text: str):
            summary = tracker.get_completed_trace(trace_id)
            sample = _extract_sample(summary or {})
            if sample is None:
                print(f"[PROBE] Trace {trace_id} 缺少完整 STT / audio milestones，略過。")
                return
            captured_samples.append(sample)
            print(
                "[PROBE] 完成 "
                f"trace={sample.trace_id} "
                f"eos_to_first_action={sample.eos_to_first_action}ms "
                f"eos_to_first_audio={sample.eos_to_first_audio}ms "
                f"eos_to_complete={sample.eos_to_complete}ms "
                f"stt_tail={sample.stt_tail if sample.stt_tail is not None else 'n/a'}ms"
            )

        connect_brain_output_handlers(window, brain, sanitize_tts_text)
        brain.warning_emitted.connect(warnings.append)
        stt_controller.warning_emitted.connect(warnings.append)
        stt_controller.recognized_result.connect(handle_recognized)
        turn_manager.turn_completed.connect(handle_turn_completed)
        brain.start()

        try:
            if not stt_controller.start_session():
                print("[PROBE] 無法啟動 STT session。")
                return 1

            total_rounds = WARMUP_ROUNDS + MEASURED_ROUNDS
            for round_index in range(total_rounds):
                round_type = "warmup" if round_index < WARMUP_ROUNDS else "measure"
                round_no = round_index + 1 if round_type == "warmup" else round_index - WARMUP_ROUNDS + 1
                print("")
                print(
                    f"[PROBE] {round_type} round {round_no}/{WARMUP_ROUNDS if round_type == 'warmup' else MEASURED_ROUNDS} "
                    f"準備中。按 Enter 後請說一句短話，例如：{RECOMMENDED_PHRASE}"
                )
                input()
                start_count = len(captured_samples)
                deadline = time.monotonic() + ROUND_TIMEOUT_S
                while time.monotonic() < deadline:
                    app.processEvents()
                    if warnings:
                        print(f"[PROBE] 收到警告：{warnings[-1]}")
                        return 1
                    if len(captured_samples) > start_count:
                        break
                    time.sleep(0.01)
                else:
                    print("[PROBE] 等待本輪完成逾時，請確認麥克風、Azure STT 與網路狀態。")
                    return 1
        finally:
            stt_controller.shutdown()
            turn_manager.shutdown()
            brain.stop()
            brain.quit()
            if brain.isRunning():
                brain.wait(3000)
            dispatcher.shutdown()

    measured_samples = captured_samples[WARMUP_ROUNDS:]
    if len(measured_samples) != MEASURED_ROUNDS:
        print(f"[PROBE] 量測輪數不足，預期 {MEASURED_ROUNDS}，實際 {len(measured_samples)}。")
        return 1

    eos_to_first_action_values = [sample.eos_to_first_action for sample in measured_samples]
    eos_to_first_audio_values = [sample.eos_to_first_audio for sample in measured_samples]
    eos_to_complete_values = [sample.eos_to_complete for sample in measured_samples]
    median_action = round(statistics.median(eos_to_first_action_values))
    median_audio = round(statistics.median(eos_to_first_audio_values))
    median_complete = round(statistics.median(eos_to_complete_values))

    print("")
    print("== ECHOES Live STT Latency Probe ==")
    for index, sample in enumerate(measured_samples, start=1):
        print(
            f"[ROUND {index}] text={sample.recognized_text!r} "
            f"eos_to_first_action={sample.eos_to_first_action}ms "
            f"eos_to_first_audio={sample.eos_to_first_audio}ms "
            f"eos_to_complete={sample.eos_to_complete}ms "
            f"stt_tail={sample.stt_tail if sample.stt_tail is not None else 'n/a'}ms"
        )
    print(
        f"[MEDIAN] eos_to_first_action={median_action}ms "
        f"eos_to_first_audio={median_audio}ms "
        f"eos_to_complete={median_complete}ms"
    )

    if median_action > 1300 or median_audio > 1500 or median_complete > 1800:
        print("[PROBE] 未達成 live STT SLA。")
        return 1

    print("[PROBE] Live STT SLA 通過。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
