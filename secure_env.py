"""Release 的 config/.env 以 Windows DPAPI 加密成 config/.env.secure，硬碟上不留明文 key。

流程：config/.env（明文）存在 → 加密寫成 .env.secure 並刪除明文；之後啟動只讀 .env.secure。
要換 key：重新放一份明文 config/.env，下次啟動會覆蓋加密檔。
加密綁定「這台電腦＋這個 Windows 帳號」，再加一組編進程式的 entropy，所以：複製到別台電腦、別的帳號、
或用記事本打開都看不到內容；但以同一帳號執行的程式仍解得開（App 本身就是），這只是提高門檻。
開發機的根目錄 .env 不處理（只處理 config/ 底下的）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)
SECURE_NAME = ".env.secure"
_ENTROPY = b"echoes-virtualpet/env/v1:7f3a91c2"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(protect: bool, data: bytes) -> bytes:
    source, _keep_data = _blob(data)
    entropy, _keep_entropy = _blob(_ENTROPY)
    out = _Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out)):
        raise OSError(ctypes.GetLastError(), "DPAPI 失敗（加密檔不是在這台電腦／這個帳號建立的？）")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    return _crypt(True, data)


def unprotect(data: bytes) -> bytes:
    return _crypt(False, data)


def resolve(root: Path) -> Path:
    """config/.env（含其加密檔）優先，否則根目錄 .env。回傳的路徑不保證存在。"""
    env = Path(root) / "config" / ".env"
    return env if env.is_file() or env.with_name(SECURE_NAME).is_file() else Path(root) / ".env"


def read_text(env_path: str | Path) -> str | None:
    """回傳 .env 內容；沒有檔案或解不開回傳 None。必要時順便把 config/.env 加密並刪掉明文。"""
    env = Path(env_path)
    secure = env.with_name(SECURE_NAME)
    if env.is_file():
        text = env.read_text(encoding="utf-8")
        if os.name == "nt" and env.parent.name == "config":
            try:
                secure.write_bytes(protect(text.encode("utf-8")))
                env.unlink()
                LOGGER.info("[ENV] 已將 %s 加密為 %s 並刪除明文", env.name, secure.name)
            except OSError as exc:
                LOGGER.warning("[ENV] 無法加密或刪除明文 %s，請手動刪除：%s", env, exc)
        return text
    if secure.is_file():
        try:
            return unprotect(secure.read_bytes()).decode("utf-8")
        except OSError as exc:
            LOGGER.warning("[ENV] 無法解開 %s：%s。請在 %s 重新放一份明文 .env", secure, exc, env.parent)
    return None
