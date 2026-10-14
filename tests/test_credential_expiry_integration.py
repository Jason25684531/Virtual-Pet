from datetime import timedelta
import json
import os

import pytest

import config
from api_client.tts_contract import TtsRequest
from api_client.elevenlabs_client import ElevenLabsStreamingTTSWorker
from api_client.voai_client import VoAIStreamingTTSWorker
from pet_harness.agent.api_provider import APIProvider
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.app.runtime_lifecycle import RuntimeLifecycle
from tests.test_credential_lifecycle import release

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows retention integration")




def test_running_llm_and_tts_stop_at_expiry_then_restart_recovers(release, monkeypatch):
    root, life, clock, scheduler = release
    life.import_environment()
    monkeypatch.setattr(config, "CREDENTIAL_LIFECYCLE", life)
    for name in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "VOAI_TOKEN"):
        monkeypatch.setenv(name, "fake-owned")
        life.track_injected(name, "fake-owned")
    calls = []
    def request(**kwargs):
        calls.append(kwargs)
        return type("Response", (), {"status_code": 200, "json": lambda self: {"output_text": "ok"}})()
    provider_config = ProviderConfig(provider_type=ProviderType.API, base_url="https://fake.invalid", api_key_env_var="OPENAI_API_KEY")
    provider = APIProvider(provider_config, request)
    event = UserEvent.from_dict({"text": "hello", "source": "test"})
    assert provider.generate_reply(event).reply == "ok"
    calls.clear()
    clock[0] += timedelta(days=30)
    assert provider.generate_reply(event).metadata["error_category"] == "credentials_expired"
    assert provider.generate_reply_stream(event) is None
    for worker_type in (ElevenLabsStreamingTTSWorker, VoAIStreamingTTSWorker):
        finished = []
        worker = worker_type(TtsRequest(text="hello", reply_id="test", trace_id="test", character_id="test",
                                       voice_id="test", model_id="test"), requests_post=request)
        worker.finished_signal.connect(lambda *args: finished.append(args))
        worker.run()
        assert finished and finished[0][0] is False
        assert finished[0][2]["failure_code"] == "credentials_expired"
    runtime = ProviderRuntime(config_path=root / "runtime" / "provider_config.json", request_fn=request)
    runtime.configure(provider_config)
    payload = runtime.status_payload()
    assert payload["credential_status"] == "credentials_expired" and not payload["healthy"]
    assert "fake-owned" not in json.dumps(payload)
    assert not calls
    assert runtime.get_status().metadata["error_category"] == "credentials_expired"
    (root / "config" / ".env").write_text("OPENAI_API_KEY=fake-renewed\n", encoding="utf-8")
    life.import_environment()
    monkeypatch.setenv("OPENAI_API_KEY", "fake-renewed")
    assert provider.generate_reply(event).reply == "ok"


@pytest.mark.parametrize(
    ("node_type", "credential_name"),
    [
        ("Azure_ChatGPT_Node", "AZURE_OPENAI_API_KEY"),
        ("ParallelGen_Azure_EditSubmit", "AZURE_OPENAI_API_KEY"),
        ("ParallelGen_Azure_EditSubmit", "AZURE_OPENAI_IMAGES_API_KEY"),
    ],
)
def test_expired_azure_key_is_rejected_before_workflow_injection(release, monkeypatch, node_type, credential_name):
    from credential_lifecycle import CredentialError
    from pet_harness.asset.workflow_patcher import WorkflowPatcher

    root, life, clock, _ = release
    (root / "config" / ".env").write_text(f"{credential_name}=fake-azure\n", encoding="utf-8")
    for name in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_IMAGES_API_KEY"):
        if name != credential_name:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(credential_name, "fake-azure")
    life.import_environment()
    monkeypatch.setattr(config, "CREDENTIAL_LIFECYCLE", life)
    clock[0] += timedelta(days=30)
    workflow = {"1": {"class_type": node_type, "inputs": {}}}

    with pytest.raises(CredentialError, match="credentials_expired"):
        WorkflowPatcher._inject_secrets(workflow)
    assert "api_key" not in workflow["1"]["inputs"]


def test_timer_is_owned_stops_on_shutdown_and_shows_status(release, monkeypatch):
    class Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self):
            self.callback()

    class Timer:
        def __init__(self):
            self.timeout = Signal()
            self.active = False

        def start(self, interval):
            self.interval = interval
            self.active = True

        def stop(self):
            self.active = False
    root, life, clock, scheduler = release
    life.import_environment()
    monkeypatch.setattr(config, "CREDENTIAL_LIFECYCLE", life)
    notices = []
    runtime = RuntimeLifecycle()
    timer = Timer()
    runtime.watch_credentials(timer, notices.append)
    assert timer.active and timer.interval == 60000
    clock[0] += timedelta(days=30)
    timer.timeout.emit()
    assert len(notices) == 1 and "config/.env" in notices[0]
    runtime.shutdown_all()
    assert not timer.active


def test_expiry_during_pcm_failure_blocks_mp3_retry(release, monkeypatch):
    root, life, clock, scheduler = release
    life.import_environment()
    monkeypatch.setattr(config, "CREDENTIAL_LIFECYCLE", life)
    monkeypatch.setenv("VOAI_TOKEN", "fake-voice")
    calls, finished = [], []
    worker = VoAIStreamingTTSWorker(TtsRequest(text="hello", reply_id="test", trace_id="test", character_id="test",
                                              voice_id="test", model_id="test"), requests_post=lambda **kw: calls.append(kw))
    monkeypatch.setattr(worker, "_pcm_streaming_enabled", lambda: True)
    def failed_pcm(*args):
        clock[0] += timedelta(days=30)
        return False, "fake failure", {}
    monkeypatch.setattr(worker, "_try_pcm_stream", failed_pcm)
    worker.finished_signal.connect(lambda *args: finished.append(args))
    worker.run()
    assert not calls and finished[0][2]["failure_code"] == "credentials_expired"


def test_cached_diagnostics_do_not_report_expired_keys_as_configured(release, monkeypatch):
    from types import SimpleNamespace
    from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
    root, life, clock, scheduler = release
    life.import_environment()
    monkeypatch.setattr(config, "CREDENTIAL_LIFECYCLE", life)
    facade = SimpleNamespace(_project_env={"OPENAI_API_KEY": "fake-cached"}, _mask_payload=lambda value: value)
    clock[0] += timedelta(days=30)
    result = PyQtHarnessAdapter._build_provider_diagnostics(facade, {"provider_type": "api", "api_key_env_var": "OPENAI_API_KEY"}, {"healthy": True})
    assert result["api_key_status"] == "expired" and result["api_config_status"] == "credentials_expired"
