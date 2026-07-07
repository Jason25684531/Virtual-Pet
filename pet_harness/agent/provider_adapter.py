from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderStatus
from pet_harness.models.skill import Skill


@dataclass
class ProviderReply:
    reply: str
    provider_status: ProviderStatus
    behavior_hint: str | None = None
    raw_text: str | None = None
    raw_json: dict[str, Any] | None = None
    prompt_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProviderAdapter(Protocol):
    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        """Generate a reply for the harness without exposing provider details."""
