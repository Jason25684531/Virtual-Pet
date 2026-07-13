from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pet_harness.models.events import UserEvent
from pet_harness.models.skill import Skill


@dataclass
class PromptBuildResult:
    prompt: str
    warnings: list[str] = field(default_factory=list)


class PromptBuilder:
    def __init__(self, agentic_root: str | Path) -> None:
        self.agentic_root = Path(agentic_root)

    def build(
        self,
        event: UserEvent,
        skills: list[Skill],
        state_snapshot: dict,
        matched_skill: Skill | None = None,
        persona: str | None = None,
        action_tags: list[str] | None = None,
    ) -> PromptBuildResult:
        warnings: list[str] = []
        soul_text = self._read_optional(self.agentic_root / "soul.md", "Soul context unavailable.", warnings)
        agentic_text = self._read_optional(
            self.agentic_root / "agentic.md",
            "Agentic rules unavailable.",
            warnings,
        )
        skill_lines = [
            f"- {skill.name}: {skill.description} | triggers={', '.join(skill.triggers)} | behavior={skill.behavior}"
            for skill in skills
        ]
        matched_text = matched_skill.name if matched_skill else "none"
        valid_action_tags = [str(tag).strip() for tag in (action_tags or []) if str(tag).strip()]
        prompt = "\n".join(
            [
                "You are ECHOES, a local-first desktop companion.",
                "",
                "## Soul",
                soul_text,
                "",
                "## Agentic Notes",
                agentic_text,
                "",
                "## Character Persona",
                persona.strip() if persona else "No persona configured.",
                "",
                "## Available Skills",
                "\n".join(skill_lines) if skill_lines else "- none",
                "",
                f"## Deterministic Matched Skill\n{matched_text}",
                "",
                "## Available Character Action Tags",
                ", ".join(valid_action_tags) if valid_action_tags else "none",
                "",
                "## Current Pet State",
                str(state_snapshot),
                "",
                "## User Text",
                event.text,
                "",
                "## Output Contract",
                'Return JSON only with keys: "reply", "matched_skill", "action_tag", "confidence", "tool_request", and either "notes" or "reasoning_summary".',
                "Do not include private chain-of-thought.",
                "Only use a skill name from the provided skill list or null.",
                "action_tag must be one of the available character action tags or null; never put control tags in reply.",
            ]
        )
        return PromptBuildResult(prompt=prompt, warnings=warnings)

    def _read_optional(self, path: Path, fallback: str, warnings: list[str]) -> str:
        if not path.exists():
            warnings.append(f"Missing context file: {path.name}")
            return fallback
        return path.read_text(encoding="utf-8").strip()
