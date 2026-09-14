"""單調遞增的角色世代序號。

切換角色是非同步的:上一個角色的 LLM 串流、TTS 請求和 PCM 分塊可能在切換完成之後
才抵達。只比對 character_id 擋不住「切走再切回同一個角色」的情況,所以每次切換都推進
一個世代序號,讓晚到的結果可以被認出來並丟棄,而不是混進新角色的回合裡。
"""

from __future__ import annotations

from threading import Lock

_LOCK = Lock()
_CURRENT = 1


def current() -> int:
    with _LOCK:
        return _CURRENT


def advance() -> int:
    """切換角色時呼叫;回傳新的世代序號。"""
    global _CURRENT
    with _LOCK:
        _CURRENT += 1
        return _CURRENT
