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


def lively_data_roots() -> list[Path]:
    """安裝版與 Store 版 Lively 的資料根目錄（WallpaperLayout.json、Library/wallpapers 所在處）。"""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    return [local / "Lively Wallpaper", *(local / "Packages").glob("*LivelyWallpaper*/LocalCache/Local/Lively Wallpaper")]


def find_lively_executable() -> str:
    """LIVELY_EXE → PATH → Store 版 / 安裝版常見位置；找不到回傳空字串。"""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    executable = os.environ.get("LIVELY_EXE") or shutil.which("Lively.exe")
    if not executable:
        candidates = list(Path(os.environ.get("ProgramFiles", "C:/Program Files")).glob(
            "WindowsApps/*LivelyWallpaper*/Build/Lively.exe"))
        candidates.extend(local.glob("Programs/Lively Wallpaper/Lively.exe"))
        try:
            executable = str(next((p for p in candidates if p.is_file()), "")) or store_executable()
        except (OSError, subprocess.SubprocessError):
            executable = ""
    return executable if executable and Path(executable).is_file() else ""


def _wallpaper_folder(root: Path, value: str) -> Path:
    folder = Path(value)
    if folder.is_dir():
        return folder
    return root / "Library" / "wallpapers" / folder.name


def lively_command(executable: str, *arguments: str) -> list[str]:
    """Run Store Lively in its package context; ordinary installs run directly."""
    installation = Path(executable).parent.parent
    if installation.parent.name.lower() != "windowsapps":
        return [executable, *arguments]

    # WindowsApps 資料夾名 = Name_Version_Arch_Resource_PublisherId；直接推出 family name，
    # 省掉每次切換都要跑一次約 0.4 秒的 Get-AppxPackage。
    parts = installation.name.split("_")
    family = f"{parts[0]}_{parts[-1]}".replace("'", "''")
    program = executable.replace("'", "''")
    args = subprocess.list2cmdline(arguments).replace("'", "''")
    script = (
        f"Invoke-CommandInDesktopPackage -PackageFamilyName '{family}' "
        f"-AppId App -Command '{program}' -Args '{args}' -PreventBreakaway -ErrorAction Stop"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def prepare_command(
    source: str | None,
    *,
    title: str = CHARACTER_TITLE,
    property_name: str = "echoesSource",
    suffixes: tuple[str, ...] = (".webm",),
) -> list[str] | None:
    """Build a setprop command only for the matching active wallpaper."""
    for root in lively_data_roots():
        layout = root / "WallpaperLayout.json"
        if not layout.is_file():
            continue
        for entry in json.loads(layout.read_text(encoding="utf-8-sig")):
            folder = _wallpaper_folder(root, entry["LivelyInfoPath"])
            info = json.loads((folder / "LivelyInfo.json").read_text(encoding="utf-8-sig"))
            properties = json.loads((folder / "LivelyProperties.json").read_text(encoding="utf-8-sig"))
            if info.get("Title") != title or property_name not in properties:
                continue

            executable = find_lively_executable()
            if not executable:
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
            return lively_command(executable, "setprop", "--monitor", str(entry["LivelyScreen"]["Index"]),
                                  "--property", f"{property_name}={value}")
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
        self._shell = None
        parent.destroyed.connect(self._close)

    def _close(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._shell is not None:
            self._shell.kill()

    def sync(self, source):
        self._queue(source, prepare_command)

    def sync_background(self, source):
        self._queue(source, prepare_background_command)

    def _queue(self, source, builder):
        if self._pending is not None:
            self._pending.cancel()
        self._pending = self._executor.submit(self._send, source, builder)

    def _send(self, source, builder):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            command = builder(source)
            if not command:
                return
            if command[0] != "powershell.exe":
                subprocess.run(command, check=True, timeout=5, creationflags=flags)
                return
            # Store 版每次冷啟 PowerShell 約 0.8 秒；常駐一個 shell 後每次約 0.03 秒。
            # ponytail: fire-and-forget，PowerShell 錯誤只會出現在 stderr，需要時再改成讀回結果。
            if self._shell is None or self._shell.poll() is not None:
                self._shell = subprocess.Popen([*command[:-1], "-"], stdin=subprocess.PIPE, text=True, creationflags=flags)
            self._shell.stdin.write(command[-1] + "\n")
            self._shell.stdin.flush()
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
            LOGGER.exception("Could not synchronize Lively wallpaper")
