from __future__ import annotations

from abc import ABC, abstractmethod
import json
import re

from pet_harness.agent.result_parser import parse_fenced_json
from pet_harness.memory.memory_models import MemoryCandidate

_MEMORY_KEY_RE = re.compile(r"^(使用者|角色)\.([^\.\s]{1,40})$")
_ALLOWED_ATTRIBUTES = {
    "使用者": {"事件", "喜好", "最愛水果", "狀態", "計劃"},
    "角色": {"承諾"},
}
_GREETINGS = {"你好", "嗨", "哈囉", "早安", "午安", "晚安"}


def is_valid_memory_key(value: str) -> bool:
    match = _MEMORY_KEY_RE.fullmatch(value)
    return bool(match and match.group(2) in _ALLOWED_ATTRIBUTES[match.group(1)])


def _is_explicit_promise(text: str) -> bool:
    return (
        not any(marker in text for marker in ("如果", "希望", "可以", "能夠"))
        and any(marker in text for marker in ("我會", "我將", "我答應", "下次我幫", "我來"))
    )


def is_eligible_memory_item(memory_key: str, text: str) -> bool:
    if not is_valid_memory_key(memory_key) or text.lstrip().startswith("你") or "?" in text or "？" in text:
        return False
    return memory_key != "角色.承諾" or _is_explicit_promise(text)


def _is_usable_candidate(candidate: MemoryCandidate, user_text: str) -> bool:
    return is_eligible_memory_item(candidate.memory_key, candidate.text) and "?" not in user_text and "？" not in user_text


class BaseMemoryExtractor(ABC):
    @abstractmethod
    def extract(self, event_id: str, user_text: str, reply: str) -> list[MemoryCandidate]: ...


class WholeTurnMemoryExtractor(BaseMemoryExtractor):
    """Small fail-open fallback for facts that do not need an LLM."""

    def extract(self, event_id: str, user_text: str, reply: str) -> list[MemoryCandidate]:
        text = user_text.strip()
        if not text or text.strip("！!。.") in _GREETINGS or "工具結果" in reply or text.startswith(("幫我查", "查詢")):
            return []
        if "你是誰" in text and reply.strip().startswith("我是"):
            return []
        if _is_explicit_promise(reply):
            return [MemoryCandidate("角色.承諾", "episodic", reply.strip(), event_id)]
        if "?" in text or "？" in text:
            return []
        if "我" not in text:
            return []
        base_key = "使用者.最愛水果" if "最喜歡" in text else "使用者.喜好" if "喜歡" in text else "使用者.事件"
        memory_type = "semantic" if base_key != "使用者.事件" else "episodic"
        return [MemoryCandidate(base_key, memory_type, text, event_id)]


class LlmMemoryExtractor(BaseMemoryExtractor):
    """Extract user-grounded candidates and fall back when provider output is unusable."""

    def __init__(self, extract_call, fallback: BaseMemoryExtractor | None = None) -> None:
        self.extract_call = extract_call
        self.fallback = fallback or WholeTurnMemoryExtractor()

    def extract(self, event_id: str, user_text: str, reply: str) -> list[MemoryCandidate]:
        try:
            raw = self.extract_call(user_text, reply)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = parse_fenced_json(raw)
            if not isinstance(payload, list):
                raise ValueError("memory candidates must be an array")
            candidates = [
                MemoryCandidate(str(item["memory_key"]), str(item["memory_type"]), str(item["text"]), event_id)
                for item in payload
                if isinstance(item, dict)
                and item.get("memory_type") in {"semantic", "episodic"}
                and item.get("memory_key")
                and item.get("text")
            ]
            candidates = [item for item in candidates if is_valid_memory_key(item.memory_key) and _is_usable_candidate(item, user_text)]
            if not candidates:
                raise ValueError("invalid memory candidate")
            return candidates
        except Exception:
            return self.fallback.extract(event_id, user_text, reply)
