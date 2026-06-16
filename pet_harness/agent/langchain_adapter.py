from __future__ import annotations

from pet_harness.agent.provider_adapter import LLMProviderAdapter, ProviderReply
from pet_harness.models.events import UserEvent
from pet_harness.models.skill import Skill


class LangChainAdapter:
    """Provider-agnostic placeholder; no real Tool Use in Week 1."""

    def __init__(self, provider: LLMProviderAdapter) -> None:
        self.provider = provider

    def generate(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        return self.provider.generate_reply(event, matched_skill=matched_skill, prompt_text=prompt_text)
