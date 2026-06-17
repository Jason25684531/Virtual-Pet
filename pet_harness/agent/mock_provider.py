from __future__ import annotations

import json

from pet_harness.agent.provider_adapter import ProviderReply
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderStatus, ProviderType
from pet_harness.models.skill import Skill


class MockProvider:
    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        if matched_skill:
            reply = f"[mock] skill {matched_skill.name} matched for: {event.text}"
            behavior_hint = matched_skill.behavior
        else:
            reply = f"[mock] I heard you: {event.text}"
            behavior_hint = None
        payload = {
            "reply": reply,
            "matched_skill": matched_skill.name if matched_skill else None,
            "behavior_hint": behavior_hint,
            "confidence": 1.0 if matched_skill else 0.0,
            "tool_request": None,
            "notes": "mock provider",
        }
        return ProviderReply(
            reply=reply,
            provider_status=ProviderStatus(provider_type=ProviderType.MOCK, healthy=True),
            behavior_hint=behavior_hint,
            raw_text=json.dumps(payload, ensure_ascii=False),
            raw_json=payload,
            prompt_text=prompt_text,
        )
