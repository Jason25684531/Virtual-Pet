from __future__ import annotations

import json

from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderStatus, ProviderType
from pet_harness.models.skill import Skill


class LowSpecProvider:
    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        reply_text = f"[low_spec] I heard you: {event.text}"
        payload = {
            "reply": reply_text,
            "matched_skill": matched_skill.name if matched_skill else None,
            "behavior_hint": matched_skill.behavior if matched_skill else None,
            "confidence": 1.0 if matched_skill else 0.0,
            "tool_request": None,
            "notes": "low spec fallback",
        }
        raw_text = json.dumps(payload, ensure_ascii=False)
        return ProviderReply(
            reply=reply_text,
            provider_status=ProviderStatus(
                provider_type=ProviderType.LOW_SPEC,
                healthy=True,
                message="low spec provider ready",
            ),
            behavior_hint=payload["behavior_hint"],
            raw_text=raw_text,
            raw_json=payload,
            prompt_text=prompt_text,
        )
