from __future__ import annotations

from datetime import UTC, datetime
import threading

from pet_harness.memory.memory_models import RetrievalRequest


def previous_turn(
    events: list[dict], now: datetime, max_age_seconds: int = 1800
) -> tuple[str | None, str | None, float | None]:
    if not events:
        return None, None, None
    event = events[-1]
    created = datetime.fromisoformat(event["created_at"])
    created = created if created.tzinfo else created.replace(tzinfo=UTC)
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    age = (now - created).total_seconds()
    if age > max_age_seconds:
        return None, None, None
    return event["input_payload"].get("text"), event["output_payload"].get("reply"), age


class FollowUpDetector:
    _PRONOUNS = ("這個", "那個", "它", "他", "她", "這樣", "然後", "為什麼", "怎麼", "哪個")

    def detect(self, request: RetrievalRequest) -> str | None:
        if not request.previous_user_text:
            return None
        text = request.current_turn_text.strip()
        if any(word in text for word in self._PRONOUNS):
            return "pronoun"
        if len(text) < 12:
            return "short"
        if (request.previous_assistant_text or "").rstrip().endswith(("?", "？")):
            return "assistant_question"
        return None


class LlmQueryRewriter:
    def __init__(self, rewrite_call, timeout_seconds: float = 1.25, enabled: bool = False) -> None:
        self.rewrite_call = rewrite_call
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled

    def rewrite(self, request: RetrievalRequest) -> str | None:
        if not self.enabled:
            return None
        result: list[str | None] = [None]

        def run() -> None:
            try:
                value = self.rewrite_call(request, timeout=self.timeout_seconds)
                result[0] = value.strip() if isinstance(value, str) and value.strip() else None
            except Exception:
                return

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(self.timeout_seconds)
        return result[0]
