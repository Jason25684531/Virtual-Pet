from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pet_harness.voice_runtime_status_adapter import VoiceRuntimeStatusAdapter


class _FakeSttController:
    def state(self) -> str:
        return "idle"


class _FakeAudioWorker:
    def is_busy(self) -> bool:
        return False


def test_voice_status_defaults_to_missing_runtime():
    adapter = VoiceRuntimeStatusAdapter()

    status = adapter.get_status()

    assert status.stt_status == "configured_missing_runtime"
    assert status.tts_primary_status == "configured_missing_runtime"
    assert status.tts_fallback_status == "configured_missing_runtime"
    assert status.audio_worker_status == "configured_missing_runtime"
    assert status.overall_status == "configured_missing_runtime"


def test_voice_status_reports_ready_runtime_without_configured_not_implemented():
    adapter = VoiceRuntimeStatusAdapter(
        stt_controller=_FakeSttController(),
        tts_clients={"primary": {"configured": True, "wired": True}, "fallback": {"configured": True, "wired": True}},
        audio_worker=_FakeAudioWorker(),
    )

    status = adapter.get_status()

    assert status.stt_status in {"configured_and_ready", "runtime_available"}
    assert status.tts_primary_status in {"configured_and_ready", "runtime_available"}
    assert status.tts_fallback_status in {"configured_and_ready", "runtime_available"}
    assert status.audio_worker_status in {"configured_and_ready", "runtime_available"}
    assert status.overall_status in {"configured_and_ready", "runtime_available"}
    assert "configured_not_implemented" not in status.to_dict().values()


def test_voice_status_reports_trigger_not_wired_when_runtime_partial():
    adapter = VoiceRuntimeStatusAdapter(
        stt_controller=_FakeSttController(),
        tts_clients={"primary": {"configured": True, "wired": False}},
    )

    status = adapter.get_status()

    assert status.tts_primary_status == "runtime_present_trigger_not_wired"
    assert status.overall_status in {
        "runtime_present_trigger_not_wired",
        "runtime_available",
    }


def test_voice_status_masks_secrets_in_last_error():
    adapter = VoiceRuntimeStatusAdapter(
        tts_clients={"primary": {"configured": True, "last_error": "token=sk-secret-12345678 failed"}},
    )

    status = adapter.get_status()

    assert "[REDACTED]" in status.last_voice_error
    assert "sk-secret-12345678" not in status.last_voice_error
