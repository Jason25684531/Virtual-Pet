import json

import release_bootstrap as rb


class Recorder:
    def __init__(self):
        self.popen, self.run = [], []

    def install(self, monkeypatch, running=()):
        monkeypatch.setattr(rb.subprocess, "Popen", lambda cmd, **kw: self.popen.append(cmd))
        monkeypatch.setattr(rb.subprocess, "run", lambda cmd, **kw: self.run.append(cmd))
        monkeypatch.setattr(rb, "_is_running", lambda name: name in running)
        return self


def _provider(tmp_path, provider_type="ollama"):
    path = tmp_path / "data" / "runtime" / "provider_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"provider_type": provider_type, "base_url": "http://ollama:1"}), encoding="utf-8")


def test_running_ollama_is_not_started_again(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    _provider(tmp_path)
    monkeypatch.setattr(rb, "_reachable", lambda url: True)
    rb._ensure_ollama(tmp_path)
    assert rec.popen == []


def test_stopped_ollama_is_started_once(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    _provider(tmp_path)
    exe = tmp_path / "ollama.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(rb, "_reachable", lambda url: False)
    monkeypatch.setattr(rb.shutil, "which", lambda name: str(exe))
    rb._ensure_ollama(tmp_path)
    assert rec.popen == [[str(exe), "serve"]]


def test_api_provider_skips_ollama(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    _provider(tmp_path, "api")
    monkeypatch.setattr(rb, "_reachable", lambda url: False)
    rb._ensure_ollama(tmp_path)
    assert rec.popen == []


def test_missing_ollama_only_logs(tmp_path, monkeypatch, caplog):
    rec = Recorder().install(monkeypatch)
    _provider(tmp_path)
    monkeypatch.setattr(rb, "_reachable", lambda url: False)
    monkeypatch.setattr(rb.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nowhere"))
    rb._ensure_ollama(tmp_path)
    assert rec.popen == [] and "ollama.exe" in caplog.text


def test_lively_install_is_attempted_only_once(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    monkeypatch.setattr(rb.config, "LIVELY_BACKGROUND_ENABLED", True)
    monkeypatch.setattr(rb, "find_lively_executable", lambda: "")
    (tmp_path / "lively").mkdir()
    (tmp_path / "lively" / "lively_setup_x86_full_v2210.exe").write_bytes(b"")
    rb._ensure_lively(tmp_path)
    rb._ensure_lively(tmp_path)
    assert len(rec.run) == 1 and "/VERYSILENT" in rec.run[0]


def test_lively_disabled_skips_everything(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    monkeypatch.setattr(rb.config, "LIVELY_BACKGROUND_ENABLED", False)
    monkeypatch.setattr(rb, "find_lively_executable", lambda: "C:/Lively.exe")
    rb._ensure_lively(tmp_path)
    assert rec.popen == [] and rec.run == []


def test_lively_started_and_wallpaper_imported_when_missing(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    monkeypatch.setattr(rb.config, "LIVELY_BACKGROUND_ENABLED", True)
    monkeypatch.setattr(rb, "find_lively_executable", lambda: "C:/Lively.exe")
    monkeypatch.setattr(rb, "_has_echoes_wallpaper", lambda: False)
    (tmp_path / "lively" / "echoes_background").mkdir(parents=True)
    rb._ensure_lively(tmp_path)
    assert rec.popen == [["C:/Lively.exe"]]
    assert rec.run == [["C:/Lively.exe", "setwp", "--file", str(tmp_path / "lively" / "echoes_background")]]


def test_running_lively_with_wallpaper_does_nothing(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch, running=("Lively.exe",))
    monkeypatch.setattr(rb.config, "LIVELY_BACKGROUND_ENABLED", True)
    monkeypatch.setattr(rb, "find_lively_executable", lambda: "C:/Lively.exe")
    monkeypatch.setattr(rb, "_has_echoes_wallpaper", lambda: True)
    (tmp_path / "lively" / "echoes_background").mkdir(parents=True)
    rb._ensure_lively(tmp_path)
    assert rec.popen == [] and rec.run == []


def test_comfyui_launch_cmd_only_when_configured_and_down(tmp_path, monkeypatch):
    rec = Recorder().install(monkeypatch)
    monkeypatch.setattr(rb.config, "COMFYUI_ENABLED", True)
    monkeypatch.setattr(rb, "_reachable", lambda url: False)
    monkeypatch.delenv("COMFYUI_LAUNCH_CMD", raising=False)
    rb._ensure_comfyui(tmp_path)
    assert rec.popen == []
    monkeypatch.setenv("COMFYUI_LAUNCH_CMD", "run_nvidia_gpu.bat")
    rb._ensure_comfyui(tmp_path)
    assert rec.popen == ["run_nvidia_gpu.bat"]


def test_step_failure_does_not_stop_other_steps(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "_ensure_lively", lambda root: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(rb, "_ensure_ollama", lambda root: calls.append("ollama"))
    monkeypatch.setattr(rb, "_ensure_comfyui", lambda root: calls.append("comfyui"))
    rb._run(tmp_path)
    assert calls == ["ollama", "comfyui"]
