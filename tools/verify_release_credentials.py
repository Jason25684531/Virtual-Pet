"""Exercise a built release in an isolated installation with fake credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import secure_env
from credential_lifecycle import CredentialLifecycle, PREFIX
from credential_scheduler import BaseCredentialScheduler, WindowsCredentialScheduler, installation_id


class FakeScheduler(BaseCredentialScheduler):
    def register(self, *args):
        pass

    def remove(self):
        pass


def record(root):
    payload = secure_env.unprotect((root / "config" / ".env.secure").read_bytes()).decode()
    return json.loads(payload[len(PREFIX):])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    assert (args.release / "VirtualPet.exe").is_file(), "release executable missing"
    with tempfile.TemporaryDirectory(prefix="echoes-credential-release-") as directory:
        root = Path(directory)
        # Root binaries suffice for the maintenance branch; no runtime models or GUI assets needed.
        for source in args.release.iterdir():
            if source.is_file():
                try:
                    os.link(source, root / source.name)
                except OSError:
                    import shutil
                    shutil.copy2(source, root / source.name)
        (root / "config").mkdir()
        (root / "logs").mkdir()
        env = root / "config" / ".env"
        env.write_text("OPENAI_API_KEY=fake-old\nOLLAMA_MODEL=retained-model\n", encoding="utf-8")
        life = CredentialLifecycle(root, FakeScheduler(), lambda: datetime.now(timezone.utc) - timedelta(days=31))
        assert life.import_environment() is not None
        old_generation = record(root)["generation"]
        env.write_text("OPENAI_API_KEY=fake-new\nOLLAMA_MODEL=retained-model\n", encoding="utf-8")
        def callback(generation):
            result = subprocess.run([str(root / "VirtualPet.exe"), "--expire-credentials", "--generation", generation],
                                    cwd=root, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0, f"packaged maintenance failed: {result.returncode}"
        callback(old_generation)
        assert record(root)["state"] == "expired" and "fake-old" not in record(root)["env_text"]
        assert "retained-model" in record(root)["env_text"] and env.exists()
        print("PASS packaged expiry retains general settings and pending input", flush=True)
        life = CredentialLifecycle(root, FakeScheduler())
        assert life.import_environment() is not None
        current = record(root)
        callback(old_generation)
        callback(current["generation"])
        assert record(root) == current
        print("PASS packaged stale generation and not-due no-op", flush=True)

        # Schedule a real per-user invocation one minute ahead, with no desktop app running.
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        env.write_text("OPENAI_API_KEY=fake-scheduled\nOLLAMA_MODEL=retained-model\n", encoding="utf-8")
        life = CredentialLifecycle(root, FakeScheduler(), lambda: expires_at - timedelta(days=30))
        assert life.import_environment() is not None
        current = record(root)
        scheduler = WindowsCredentialScheduler(root)
        try:
            scheduler.register(root / "VirtualPet.exe", current["generation"], expires_at)
            print("Registered visible per-user task; waiting for actual trigger", flush=True)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if record(root)["state"] == "expired":
                    break
                time.sleep(2)
            if record(root)["state"] != "expired":
                result = subprocess.run(
                    ["schtasks.exe", "/Query", "/TN", f"ECHOES-CredentialExpiry-{installation_id(root)}", "/V", "/FO", "LIST"],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                print(f"Expected expiry: {expires_at.isoformat()}\n{result.stdout}", flush=True)
            assert record(root)["state"] == "expired", "scheduled packaged callback did not clear credentials"
            assert "fake-scheduled" not in record(root)["env_text"]
            print("PASS scheduled packaged cleanup while desktop app is closed", flush=True)
        finally:
            scheduler.remove()
        print("PASS test task removed; isolated installation cleaned", flush=True)


if __name__ == "__main__":
    main()
