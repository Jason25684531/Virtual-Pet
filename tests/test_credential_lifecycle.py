from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

import secure_env
from credential_lifecycle import CredentialError, CredentialLifecycle, PREFIX
from credential_scheduler import BaseCredentialScheduler, WindowsCredentialScheduler

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows credential retention")
TEXT = 'OPENAI_API_KEY=fake-old\nELEVENLABS_API_KEY=fake-tts\nVOAI_TOKEN=fake-voice\nOLLAMA_MODEL=gemma3:12b\nELEVENLABS_VOICE_ID=voice-123\n'


class FakeScheduler(BaseCredentialScheduler):
    def __init__(self):
        self.calls = []
        self.fail = False

    def register(self, *args):
        if self.fail:
            raise OSError("fake failure")
        self.calls.append(args)

    def remove(self):
        pass


@pytest.fixture
def release(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".env").write_text(TEXT, encoding="utf-8")
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    scheduler = FakeScheduler()
    lifecycle = CredentialLifecycle(tmp_path, scheduler, lambda: clock[0])
    return tmp_path, lifecycle, clock, scheduler


def record(root):
    text = secure_env.unprotect((root / "config" / ".env.secure").read_bytes()).decode()
    return json.loads(text[len(PREFIX):])


def test_import_restart_boundary_and_selective_cleanup(release, monkeypatch):
    root, life, clock, scheduler = release
    assert life.import_environment() == TEXT
    original = record(root)
    assert scheduler.calls[0][2] == clock[0] + timedelta(days=30)
    assert not (root / "config" / ".env").exists()
    assert b"fake-old" not in (root / "config" / ".env.secure").read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "fake-old")
    life.track_injected("OPENAI_API_KEY", "fake-old")
    clock[0] += timedelta(days=29, hours=23, minutes=59)
    assert life.import_environment() == TEXT
    assert record(root)["expires_at"] == original["expires_at"]
    life.require_usable("OPENAI_API_KEY")
    clock[0] += timedelta(minutes=1)
    with pytest.raises(CredentialError, match="credentials_expired"):
        life.require_usable("OPENAI_API_KEY")
    cleared = record(root)
    assert cleared["state"] == "expired"
    assert "fake-" not in cleared["env_text"]
    assert "OLLAMA_MODEL" in cleared["env_text"] and "voice-123" in cleared["env_text"]
    assert "OPENAI_API_KEY" not in os.environ
    assert not life.expire_if_due()


def test_renewal_old_callback_pending_plaintext_and_role_data(release):
    root, life, clock, scheduler = release
    for role in ("one", "two"):
        path = root / "data" / "characters" / role
        path.mkdir(parents=True)
        (path / "state.db").write_bytes(b"role-state")
    life.import_environment()
    old = record(root)["generation"]
    clock[0] += timedelta(days=31)
    env = root / "config" / ".env"
    env.write_text(TEXT.replace("fake-old", "fake-new"), encoding="utf-8")
    assert life.expire_if_due(old)
    assert env.exists()
    assert "fake-new" in life.import_environment()
    new = record(root)
    assert new["generation"] != old
    assert not life.expire_if_due(old)
    assert record(root) == new
    life.require_usable("OPENAI_API_KEY")
    assert all(p.read_bytes() == b"role-state" for p in (root / "data").rglob("state.db"))
    env.write_text(TEXT, encoding="utf-8")
    clock[0] += timedelta(days=1)
    life.import_environment()
    assert record(root)["expires_at"] == (clock[0] + timedelta(days=30)).isoformat()


def test_legacy_migrates_once_and_general_settings_have_no_task(release):
    root, life, clock, scheduler = release
    (root / "config" / ".env").unlink()
    (root / "config" / ".env.secure").write_bytes(secure_env.protect(TEXT.encode()))
    assert life.import_environment() == TEXT
    expires = record(root)["expires_at"]
    clock[0] += timedelta(days=1)
    assert life.import_environment() == TEXT
    assert record(root)["expires_at"] == expires
    scheduler.calls.clear()
    (root / "config" / ".env").write_text("OLLAMA_MODEL=new\nELEVENLABS_VOICE_ID=voice\n", encoding="utf-8")
    life.import_environment()
    assert record(root)["expires_at"] == expires
    assert "fake-" not in record(root)["env_text"]


def test_fresh_general_settings_and_empty_template_do_not_create_task(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".env").write_text("OPENAI_API_KEY=\nOLLAMA_MODEL=new\n")
    scheduler = FakeScheduler()
    life = CredentialLifecycle(tmp_path, scheduler)
    assert life.import_environment() is not None
    assert record(tmp_path)["state"] == "unmanaged" and not scheduler.calls


def test_general_reimport_cannot_restore_expired_external_key(release, monkeypatch):
    root, life, clock, scheduler = release
    life.import_environment()
    clock[0] += timedelta(days=30)
    life.expire_if_due()
    expires = record(root)["expires_at"]
    (root / "config" / ".env").write_text("OLLAMA_MODEL=new\n")
    life.import_environment()
    assert record(root)["state"] == "expired" and record(root)["expires_at"] == expires
    monkeypatch.setenv("OPENAI_API_KEY", "fake-external")
    with pytest.raises(CredentialError, match="credentials_expired"):
        life.require_usable("OPENAI_API_KEY")


@pytest.mark.parametrize("failure", ["corrupt", "missing_time", "future", "missing_file"])
def test_corruption_and_clock_rollback_fail_closed(release, failure):
    root, life, clock, scheduler = release
    life.import_environment()
    data = record(root)
    secure = root / "config" / ".env.secure"
    if failure == "corrupt":
        secure.write_bytes(b"corrupt")
    elif failure == "missing_time":
        data.pop("expires_at")
        secure.write_bytes(secure_env.protect((PREFIX + json.dumps(data)).encode()))
    elif failure == "future":
        clock[0] -= timedelta(days=1)
    else:
        secure.unlink()
    with pytest.raises(CredentialError, match="credentials_invalid"):
        life.require_usable("OPENAI_API_KEY")


def test_write_failure_does_not_load_plaintext_or_claim_cleanup(release, monkeypatch):
    root, life, clock, scheduler = release
    def denied(*args):
        raise PermissionError("private fake text")
    monkeypatch.setattr(life, "_write", denied)
    assert life.import_environment() is None
    assert (root / "config" / ".env").exists()
    with pytest.raises(CredentialError):
        life.require_usable("OPENAI_API_KEY")
    monkeypatch.undo()
    life.import_environment()
    clock[0] += timedelta(days=30)
    monkeypatch.setattr(life, "_write", denied)
    with pytest.raises(CredentialError):
        life.require_usable("OPENAI_API_KEY")
    assert record(root)["state"] == "active"


def test_scheduler_failure_external_variables_and_no_root_fallback(release, monkeypatch):
    root, life, clock, scheduler = release
    scheduler.fail = True
    assert life.import_environment() == TEXT
    assert life.status() == "credentials_schedule_failed"
    monkeypatch.setenv("OPENAI_API_KEY", "external-value")
    (root / ".env").write_text("OPENAI_API_KEY=development-only", encoding="utf-8")
    clock[0] += timedelta(days=30)
    life.expire_if_due()
    with pytest.raises(CredentialError):
        life.require_usable("OPENAI_API_KEY")
    assert os.environ["OPENAI_API_KEY"] == "external-value"
    assert (root / ".env").read_text() == "OPENAI_API_KEY=development-only"
    life.require_usable("UNRELATED_API_KEY")
    assert secure_env.resolve(root) == root / "config" / ".env"


def test_custom_sensitive_name_and_editor_replacement_preserved(release, monkeypatch):
    root, life, clock, scheduler = release
    provider = root / "data" / "runtime"
    provider.mkdir(parents=True)
    (provider / "provider_config.json").write_text('{"api_key_env_var":"CUSTOM_CREDENTIAL"}')
    env = root / "config" / ".env"
    env.write_text('CUSTOM_CREDENTIAL=fake-custom\nAPIKEY=fake-generic\nVoAI_API_KEY=fake-voai\n', encoding="utf-8")
    original_write = life._write
    def editor_write(*args):
        original_write(*args)
        env.write_text("OPENAI_API_KEY=fake-replacement\n", encoding="utf-8")
    monkeypatch.setattr(life, "_write", editor_write)
    life.import_environment()
    assert "fake-replacement" in env.read_text()
    monkeypatch.setattr(life, "_write", original_write)
    clock[0] += timedelta(days=30)
    life.expire_if_due()
    assert "fake-" not in record(root)["env_text"]


def test_junction_is_rejected_without_touching_external_data(tmp_path):
    root, target = tmp_path / "app", tmp_path / "external"
    root.mkdir()
    target.mkdir()
    env = target / ".env"
    env.write_text(TEXT)
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(root / "config"), str(target)], capture_output=True)
    assert result.returncode == 0
    assert CredentialLifecycle(root, FakeScheduler()).import_environment() is None
    assert env.read_text() == TEXT and not (target / ".env.secure").exists()


def test_cross_process_renewal_cannot_be_deleted_by_old_cleanup(release):
    root, life, clock, scheduler = release
    clock[0] = datetime.now(timezone.utc) - timedelta(days=31)
    life.import_environment()
    generation = record(root)["generation"]
    (root / "config" / ".env").write_text("OPENAI_API_KEY=fake-renewed\n", encoding="utf-8")
    project = Path(__file__).resolve().parents[1]
    common = "import sys; from pathlib import Path; from credential_lifecycle import CredentialLifecycle; life=CredentialLifecycle(Path(sys.argv[1])); "
    cleanup = subprocess.Popen([sys.executable, "-c", common + "life.expire_if_due(sys.argv[2])", str(root), generation], cwd=project)
    renewal = subprocess.Popen([sys.executable, "-c", common + "assert life.import_environment() is not None", str(root)], cwd=project)
    assert cleanup.wait(timeout=20) == 0 and renewal.wait(timeout=20) == 0
    current = record(root)
    assert current["generation"] != generation and current["state"] == "active"
    assert "fake-renewed" in current["env_text"]


def test_corrupt_settings_then_disappearing_file_stays_fail_closed(release):
    root, life, clock, scheduler = release
    (root / "config" / ".env").unlink()
    secure = root / "config" / ".env.secure"
    secure.write_bytes(b"corrupt")
    assert life.import_environment() is None
    secure.unlink()
    with pytest.raises(CredentialError):
        life.require_usable("OPENAI_API_KEY")


def test_visible_task_xml_and_cli_has_no_gui_import(tmp_path):
    generation = str(uuid4())
    xml = WindowsCredentialScheduler.task_xml(tmp_path / 'path & quote' / 'VirtualPet.exe', generation,
                                              datetime.now(timezone.utc) + timedelta(days=30))
    parsed = ET.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert parsed.find("t:Settings/t:Hidden", ns).text == "false"
    assert parsed.find("t:Settings/t:StartWhenAvailable", ns).text == "true"
    assert parsed.find("t:Principals/t:Principal/t:LogonType", ns).text == "InteractiveToken"
    assert parsed.find("t:Principals/t:Principal/t:RunLevel", ns).text == "LeastPrivilege"
    assert "fake-" not in xml
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-X", "importtime", str(root / "main.py"), "--expire-credentials", "--generation", generation]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert "PyQt5" not in result.stderr and "release_bootstrap" not in result.stderr
    assert " config\n" not in result.stderr
    result = subprocess.run(command + ["--target", str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
