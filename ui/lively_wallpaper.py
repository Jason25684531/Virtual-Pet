"""Synchronize an active ECHOES Lively wallpaper without blocking Qt."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from functools import lru_cache

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHARACTER_TITLE = "ECHOES Character Host PoC"
BACKGROUND_TITLE = "ECHOES Background Host"


@lru_cache(maxsize=1)
def store_executable() -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-AppxPackage '*LivelyWallpaper*' | Select-Object -First 1).InstallLocation"],
        capture_output=True, text=True, timeout=5, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    location = result.stdout.strip()
    return str(Path(location) / "Build" / "Lively.exe") if location else ""


def _wallpaper_folder(root: Path, value: str) -> Path:
    folder = Path(value)
    if folder.is_dir():
        return folder
    return root / "Library" / "wallpapers" / folder.name


def prepare_command(
    source: str | None,
    *,
    title: str = CHARACTER_TITLE,
    property_name: str = "echoesSource",
    suffixes: tuple[str, ...] = (".webm",),
) -> list[str] | None:
    """Build a setprop command only for the matching active wallpaper."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roots = [local / "Lively Wallpaper"]
    roots.extend((local / "Packages").glob("*LivelyWallpaper*/LocalCache/Local/Lively Wallpaper"))
    for root in roots:
        layout = root / "WallpaperLayout.json"
        if not layout.is_file():
            continue
        for entry in json.loads(layout.read_text(encoding="utf-8-sig")):
            folder = _wallpaper_folder(root, entry["LivelyInfoPath"])
            info = json.loads((folder / "LivelyInfo.json").read_text(encoding="utf-8-sig"))
            properties = json.loads((folder / "LivelyProperties.json").read_text(encoding="utf-8-sig"))
            if info.get("Title") != title or property_name not in properties:
                continue

            executable = os.environ.get("LIVELY_EXE") or shutil.which("Lively.exe")
            if not executable:
                candidates = list(Path(os.environ.get("ProgramFiles", "C:/Program Files")).glob(
                    "WindowsApps/*LivelyWallpaper*/Build/Lively.exe"))
                candidates.extend(local.glob("Programs/Lively Wallpaper/Lively.exe"))
                executable = str(next((p for p in candidates if p.is_file()), "")) or store_executable()
            if not executable or not Path(executable).is_file():
                raise FileNotFoundError("Lively.exe not found; set LIVELY_EXE")

            value = ""
            if source:
                media = Path(source)
                if not media.is_absolute():
                    media = PROJECT_ROOT / media
                media = media.resolve(strict=True)
                if media.suffix.lower() not in suffixes:
                    raise ValueError(f"unsupported Lively media type: {media.suffix}")
                name = hashlib.sha256(str(media).encode()).hexdigest()[:20] + media.suffix.lower()
                target = folder / "assets" / name
                target.parent.mkdir(exist_ok=True)
                if not target.exists() or target.stat().st_size != media.stat().st_size:
                    shutil.copy2(media, target)
                value = f"assets/{name}"
            return [executable, "setprop", "--monitor", str(entry["LivelyScreen"]["Index"]),
                    "--property", f"{property_name}={value}"]
    return None


def prepare_background_command(source: str | None) -> list[str] | None:
    return prepare_command(
        source,
        title=BACKGROUND_TITLE,
        property_name="echoesBackground",
        suffixes=(".png", ".jpg", ".jpeg", ".webp"),
    )


class LivelyWallpaper:
    """Serialize background updates in one worker; never starts Lively itself."""

    def __init__(self, parent):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Lively")
        self._pending = None
        parent.destroyed.connect(lambda: self._executor.shutdown(wait=False, cancel_futures=True))

    def sync(self, source):
        self._queue(source, prepare_command)

    def sync_background(self, source):
        self._queue(source, prepare_background_command)

    def _queue(self, source, builder):
        if self._pending is not None:
            self._pending.cancel()
        self._pending = self._executor.submit(self._send, source, builder)

    @staticmethod
    def _send(source, builder):
        try:
            command = builder(source)
            if command:
                subprocess.run(command, check=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
            LOGGER.exception("Could not synchronize Lively wallpaper")
