"""一鍵啟動：在背景把 Lively 桌布、Ollama、ComfyUI 準備好，讓使用者只需要雙擊 VirtualPet.exe。

每一步都 best-effort：失敗只記 log，不擋主視窗，也不丟例外。由 bootstrap 啟動的服務在 App
關閉時不會被終止——它們是共用服務，與既有 run.bat 的行為一致。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

import requests

import config
from ui.lively_wallpaper import BACKGROUND_TITLE, find_lively_executable, lively_data_roots

LOGGER = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = _NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def start(app_root: Path) -> threading.Thread:
    thread = threading.Thread(target=_run, args=(Path(app_root),), daemon=True, name="ReleaseBootstrap")
    thread.start()
    return thread


def _run(app_root: Path) -> None:
    for step in (_ensure_lively, _ensure_ollama, _ensure_comfyui):
        try:
            step(app_root)
        except Exception:  # noqa: BLE001 - 周邊服務失敗不得影響主程式
            LOGGER.exception("[BOOTSTRAP] %s failed", step.__name__)


def _is_running(image_name: str) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
    )
    return image_name.lower() in result.stdout.lower()


def _has_echoes_wallpaper() -> bool:
    for root in lively_data_roots():
        for info in (root / "Library" / "wallpapers").glob("*/LivelyInfo.json"):
            try:
                if json.loads(info.read_text(encoding="utf-8-sig")).get("Title") == BACKGROUND_TITLE:
                    return True
            except (OSError, ValueError):
                continue
    return False


def _ensure_lively(app_root: Path) -> None:
    if not config.LIVELY_BACKGROUND_ENABLED:
        return
    executable = find_lively_executable()
    installer = next((app_root / "lively").glob("lively_setup_*.exe"), None)
    marker = app_root / "data" / "runtime" / "lively_install_attempted"
    if not executable and installer and not marker.exists():
        # 只嘗試一次：被權限政策或防毒擋下時，之後每次啟動不再反覆跑安裝程式。
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
        LOGGER.info("[BOOTSTRAP] Lively 未安裝，執行內附安裝程式：%s", installer.name)
        subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
                       timeout=600, creationflags=_NO_WINDOW)
        executable = find_lively_executable()
    if not executable:
        LOGGER.warning("[BOOTSTRAP] 找不到 Lively，略過桌布背景（可手動安裝 Lively 或設定 LIVELY_EXE）")
        return
    if not _is_running("Lively.exe"):
        subprocess.Popen([executable], creationflags=_DETACHED, close_fds=True)
        LOGGER.info("[BOOTSTRAP] 已啟動 Lively")
    wallpaper = app_root / "lively" / "echoes_background"
    if wallpaper.is_dir() and not _has_echoes_wallpaper():
        subprocess.run([executable, "setwp", "--file", str(wallpaper)], timeout=60, creationflags=_NO_WINDOW)
        LOGGER.info("[BOOTSTRAP] 已匯入 ECHOES Background Host 桌布")


def _ollama_base_url(app_root: Path) -> str | None:
    """provider_config.json 指定 ollama 才回傳 URL；選 API provider 時不必啟動本機 LLM。"""
    path = app_root / "data" / "runtime" / "provider_config.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    if payload.get("provider_type", "ollama") != "ollama":
        return None
    return str(payload.get("base_url") or os.getenv("OLLAMA_BASE_URL") or config.DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def _reachable(url: str) -> bool:
    try:
        return requests.get(url, timeout=2).ok
    except requests.RequestException:
        return False


def _ensure_ollama(app_root: Path) -> None:
    base_url = _ollama_base_url(app_root)
    if not base_url or _reachable(f"{base_url}/api/tags"):
        return
    candidates = [shutil.which("ollama"), str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")]
    executable = next((c for c in candidates if c and Path(c).is_file()), None)
    if not executable:
        LOGGER.warning("[BOOTSTRAP] Ollama 未執行且找不到 ollama.exe（%s），請安裝 Ollama", base_url)
        return
    subprocess.Popen([executable, "serve"], creationflags=_DETACHED, close_fds=True)
    LOGGER.info("[BOOTSTRAP] 已啟動 ollama serve")


def _ensure_comfyui(app_root: Path) -> None:
    command = os.getenv("COMFYUI_LAUNCH_CMD", "").strip()
    if not config.COMFYUI_ENABLED or not command or _reachable(f"{config.COMFYUI_BASE_URL}/system_stats"):
        return
    # 指令來自使用者本機 config/.env，信任等級與使用者相同；不接受任何外部輸入。
    subprocess.Popen(command, shell=True, cwd=config.COMFYUI_PATH or None, creationflags=_DETACHED, close_fds=True)
    LOGGER.info("[BOOTSTRAP] 已執行 COMFYUI_LAUNCH_CMD；ComfyUI 就緒前素材生成暫用 Mock")
