"""Release 版把 .agentic/ 與 ComfyUI_Json/ 加密內嵌進 binary，不再以明文檔案交付。

開發模式沒有 `_embedded_resources` 模組，一律讀磁碟；Release 優先讀內嵌資料，
找不到才讀磁碟（例如使用者自建的 .agentic/skills/user/*.md）。
ponytail: SHA256-CTR XOR + 金鑰同在 binary 內，只提高逆向成本，不是真正的機密保護。
"""

from __future__ import annotations

import hashlib
import marshal
import zlib
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
EMBEDDED_ROOTS = (".agentic", "ComfyUI_Json")


def _keystream_xor(data: bytes, key: bytes) -> bytes:
    stream = b"".join(
        hashlib.sha256(key + index.to_bytes(8, "little")).digest()
        for index in range((len(data) + 31) // 32)
    )[: len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(data), "little")


def pack(files: dict[str, bytes], key: bytes) -> bytes:
    return _keystream_xor(zlib.compress(marshal.dumps(files), 9), key)


def unpack(blob: bytes, key: bytes) -> dict[str, bytes]:
    return marshal.loads(zlib.decompress(_keystream_xor(blob, key)))


try:
    from pet_harness._embedded_resources import BLOB, KEY

    _FILES: dict[str, bytes] = unpack(BLOB, KEY)
except ImportError:
    _FILES = {}


def _key(path: str | Path) -> str:
    path = Path(path)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(APP_ROOT)
        except ValueError:
            return ""
    return path.as_posix()


def exists(path: str | Path) -> bool:
    return _key(path) in _FILES or Path(path).exists()


def read_text(path: str | Path) -> str:
    data = _FILES.get(_key(path))
    if data is None:
        return Path(path).read_text(encoding="utf-8")
    return data.decode("utf-8")


def rglob(directory: str | Path, suffix: str) -> list[Path]:
    """內嵌檔與磁碟檔的聯集；回傳路徑與原本 Path.rglob 同一種相對/絕對形式。"""
    directory = Path(directory)
    prefix = _key(directory).rstrip("/") + "/"
    found = {directory / name[len(prefix):] for name in _FILES if name.startswith(prefix) and name.endswith(suffix)}
    if directory.exists():
        found.update(directory.rglob(f"*{suffix}"))
    return sorted(found)


if __name__ == "__main__":
    sample = {".agentic/skills/a.md": "name: a\n中文".encode("utf-8")}
    assert unpack(pack(sample, b"k" * 32), b"k" * 32) == sample
    assert _key(APP_ROOT / ".agentic" / "soul.md") == ".agentic/soul.md"
    assert _key(".agentic/soul.md") == ".agentic/soul.md"
    print("ok")
