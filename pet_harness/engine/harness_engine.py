from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from character_library import CharacterLibrary
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.agent.provider_adapter import LLMProviderAdapter
from pet_harness.agent.result_parser import ResultParser
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.character.profile import CharacterProfile
from pet_harness.models.events import PetEvent, ToolRequestEvent, UserEvent
from pet_harness.models.agent_result import AgentResult
from pet_harness.models.provider import ProviderConfig
from pet_harness.models.skill import Skill
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.safety_guard import SafetyGuard
from pet_harness.tools.tool_models import ToolRequest, ToolResult
from pet_harness.xp.reward_manager import RewardManager
from pet_harness.xp.xp_manager import XPManager

LOGGER = logging.getLogger(__name__)
CHARACTER_SKILL_ENABLED_KEY = "character_skill_enabled"


class PetHarnessEngine:
    def __init__(
        self,
        provider: LLMProviderAdapter,
        agentic_root: str | Path = Path(".agentic"),
        db_path: str | Path = Path("data") / "pet_state.db",
        snapshot_path: str | Path = Path("debug") / "events" / "latest_pet_event.json",
        provider_config: ProviderConfig | None = None,
        character_id: str | None = None,
    ) -> None:
        self.agentic_root = Path(agentic_root)
        self.snapshot_path = Path(snapshot_path)

        self._character_id = character_id
        self._profile: CharacterProfile | None = None
        effective_db_path = db_path
        if character_id is not None:
            self._profile = CharacterProfile.load(character_id)
            effective_db_path = self._profile.sqlite_path

        self.store = SQLiteStore(effective_db_path)
        self.store.initialize()

        # provider 由 ProviderRuntime 注入;provider_config 僅保留路由偏好,
        # 角色 store 不再持久化任何 provider 設定或狀態。
        self.provider_config = provider_config
        self.provider = provider

        self.skills = SkillLoader(self.agentic_root / "skills").load_skills()
        if self._profile is not None:
            self._initialize_character_skill_overlay(self._profile.allowed_skill_refs)
            self.skills = self._filter_skills_by_config(self.skills, self._profile.allowed_skill_refs)
            self.skills = self._filter_enabled_character_skills(self.skills)
            self.skills.extend(self._profile.load_local_skills())
        self.store.sync_skills(self.skills)
        self.router = SkillRouter(self.skills)
        self.xp_manager = XPManager(self.store)
        self.reward_manager = RewardManager(self.store, self.agentic_root / "rewards" / "reward_rules.json")
        self.behavior_manager = BehaviorManager(self.store, self.agentic_root / "behavior" / "behavior_map.json")
        self.character_library = CharacterLibrary()
        self.prompt_builder = PromptBuilder(self.agentic_root)
        self.result_parser = ResultParser()
        self.tool_registry = ToolRegistry()
        self.safety_guard = SafetyGuard(self.tool_registry)
        self.asset_service = MockAssetService(self.store)
        self.last_prompt: str | None = None
        self.last_provider_raw_result: str | None = None
        self.last_agent_result: AgentResult | None = None
        self.last_tool_result: ToolResult | None = None
        self.last_asset_result: dict[str, Any] | None = None

    def filter_skills_for_character(self, skills: list[Skill]) -> list[Skill]:
        """依 active character 的 skill_config 過濾傳入的技能清單；無 character_id 時原樣回傳。

        供外部（例如 PyQtHarnessAdapter 熱重載技能時）在不需要碰觸
        _profile 的前提下，重新套用 per-character 技能隔離。
        """
        if self._profile is None:
            return skills
        filtered = self._filter_skills_by_config(skills, self._profile.allowed_skill_refs)
        filtered = self._filter_enabled_character_skills(filtered)
        filtered.extend(self._profile.load_local_skills())
        return filtered

    def _initialize_character_skill_overlay(self, authorized_skill_ids: list[str]) -> None:
        """首次使用角色時，以 profile 授權技能建立私有 SQLite enablement overlay。"""
        current = self.store.get_setting(CHARACTER_SKILL_ENABLED_KEY, None)
        overlay = dict(current) if isinstance(current, dict) else {}
        changed = not isinstance(current, dict)
        for skill_id in authorized_skill_ids:
            if skill_id not in overlay:
                overlay[skill_id] = True
                changed = True
        if changed:
            self.store.set_setting(CHARACTER_SKILL_ENABLED_KEY, overlay)

    def _filter_enabled_character_skills(self, skills: list[Skill]) -> list[Skill]:
        enabled = self.store.get_setting(CHARACTER_SKILL_ENABLED_KEY, {})
        enabled_map = dict(enabled) if isinstance(enabled, dict) else {}
        return [skill for skill in skills if enabled_map.get(skill.name, True)]

    def _filter_skills_by_config(self, skills: list[Skill], skill_config: list[str]) -> list[Skill]:
        by_name = {skill.name: skill for skill in skills}
        filtered: list[Skill] = []
        for name in skill_config:
            skill = by_name.get(name)
            if skill is None:
                LOGGER.warning("skill_config 引用不存在的 skill: %s", name)
                continue
            filtered.append(skill)
        return filtered

    def handle_event(self, event: UserEvent | dict[str, Any]) -> PetEvent:
        user_event = event if isinstance(event, UserEvent) else UserEvent.from_dict(event)
        state_before = self.store.state_snapshot()
        deterministic_skill = self.router.match(user_event.text)
        prompt_result = self.prompt_builder.build(
            event=user_event,
            skills=self.skills,
            state_snapshot=state_before,
            matched_skill=deterministic_skill,
            persona=self._profile.effective_persona if self._profile else None,
            action_tags=self.character_library.list_action_tags(self._character_id),
        )
        self.last_prompt = prompt_result.prompt
        provider_reply = self.provider.generate_reply(
            user_event,
            matched_skill=deterministic_skill,
            prompt_text=prompt_result.prompt,
        )
        self.last_provider_raw_result = provider_reply.raw_text or provider_reply.reply
        agent_result = self.result_parser.parse(
            self.last_provider_raw_result or provider_reply.reply,
            provider_type=provider_reply.provider_status.provider_type,
            fallback_reply=provider_reply.reply,
        )
        self.last_agent_result = agent_result

        matched_skill, skill_source = self.router.route(
            user_event.text,
            suggested_skill_name=agent_result.matched_skill,
            suggested_confidence=agent_result.confidence,
            allow_fallback=self.provider_config.routing_fallback_enabled if self.provider_config else False,
            confidence_threshold=self.provider_config.routing_confidence_threshold if self.provider_config else 0.7,
        )

        resolved_action = None
        if matched_skill is None and agent_result.action_tag:
            resolved_action = self.character_library.resolve_action_tag(self._character_id, agent_result.action_tag)
            if resolved_action is None:
                LOGGER.warning(
                    "Ignoring invalid action tag for character %s: %s",
                    self._character_id,
                    agent_result.action_tag,
                )
        behavior_event = self.behavior_manager.resolve(
            matched_skill,
            action_motion_key=resolved_action["motion_key"] if resolved_action else None,
        )
        tool_candidate = self._build_tool_request_candidate(user_event, matched_skill, agent_result)
        tool_event = None
        tool_result_payload = None
        tool_xp_bonus = 0
        self.last_tool_result = None
        if tool_candidate is not None:
            tool_event = ToolRequestEvent(
                tool_name=tool_candidate.tool_name,
                source_skill=matched_skill.name if matched_skill else tool_candidate.source,
                metadata={"source": tool_candidate.source, "arguments": tool_candidate.arguments},
            )
            tool_result = self._run_tool_request(tool_candidate)
            self.last_tool_result = tool_result
            tool_result_payload = tool_result.to_dict()
            if tool_result.status == "completed":
                definition = self.tool_registry.get(tool_result.tool_name)
                tool_xp_bonus = definition.xp_reward if definition is not None else 0

        xp_delta = self.xp_manager.award_for_event(matched_skill)
        if tool_xp_bonus:
            self.store.add_user_xp(tool_xp_bonus)
            xp_delta += tool_xp_bonus
        user_progress = self.store.get_user_progress()
        reward_events = self.reward_manager.check_unlocks(user_progress["xp_total"])
        asset_result = self._handle_reward_assets(
            source_event_id=user_event.event_id,
            reward_events=reward_events,
            behavior_id=behavior_event.behavior_id,
        )

        pet_event = PetEvent(
            source_event_id=user_event.event_id,
            reply=agent_result.reply or provider_reply.reply,
            matched_skill=matched_skill.name if matched_skill else None,
            behavior_id=behavior_event.behavior_id,
            webm_key=behavior_event.webm_key,
            xp_delta=xp_delta,
            reward_events=reward_events,
            tool_request=tool_event,
            provider_status=provider_reply.provider_status.to_dict(),
            saved_to_db=False,
            action_tag=resolved_action["action_tag"] if resolved_action else None,
            motion_source=behavior_event.reason,
            metadata={
                "behavior": behavior_event.to_dict(),
                "agentic": {
                    "provider_type": agent_result.provider_type,
                    "parser_status": agent_result.parser_status,
                    "fallback_used": agent_result.fallback_used,
                    "skill_source": skill_source,
                    "error_category": provider_reply.provider_status.metadata.get("error_category"),
                    "prompt_warnings": prompt_result.warnings,
                },
                "tool_result": tool_result_payload,
                "asset_result": asset_result,
            },
        )

        self.store.log_event(user_event.to_dict(), pet_event.to_dict())
        pet_event.saved_to_db = True
        self._write_snapshot(pet_event)
        return pet_event

    def debug_status(self) -> dict[str, Any]:
        return {
            "db_path": str(self.store.db_path),
            "agentic_root": str(self.agentic_root),
            "skill_count": len(self.skills),
            "tool_count": len(self.tool_registry.list_definitions()),
            "recent_tool_count": len(self.store.recent_tool_results(limit=10)),
            "asset_manifest_count": len(self.store.list_asset_manifest(limit=10)),
            "snapshot_path": str(self.snapshot_path),
        }

    def state_snapshot(self) -> dict[str, Any]:
        return self.store.state_snapshot()

    def get_xp(self) -> int:
        return int(self.store.get_user_progress().get("xp_total", 0))

    def get_level(self) -> int:
        return max(1, (max(0, self.get_xp()) // 100) + 1)

    def recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.recent_events(limit=limit)

    def list_tools(self) -> list[dict[str, Any]]:
        return [definition.to_dict() for definition in self.tool_registry.list_definitions()]

    def tool_status(self) -> dict[str, Any]:
        return {
            "tools": self.list_tools(),
            "recent_results": self.store.recent_tool_results(limit=10),
        }

    def run_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        request = ToolRequest(tool_name=tool_name, source="debug_cli", arguments=arguments or {})
        result = self._run_tool_request(request)
        self.last_tool_result = result
        return {
            "tool_request": request.to_dict(),
            "tool_result": result.to_dict(),
        }

    def run_asset_mock(self) -> dict[str, Any]:
        response = self.asset_service.create_reward_asset_request(
            source_event_id="debug-asset",
            reward_id="debug_reward",
            behavior_id="idle",
        )
        self.last_asset_result = response.to_dict()
        return {
            "asset_result": response.to_dict(),
            "asset_manifest": self.store.list_asset_manifest(limit=1),
        }

    def _write_snapshot(self, pet_event: PetEvent) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(
            json.dumps(pet_event.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_tool_request_candidate(
        self,
        user_event: UserEvent,
        matched_skill,
        agent_result: AgentResult,
    ) -> ToolRequest | None:
        if matched_skill and matched_skill.required_tool:
            return ToolRequest(
                tool_name=matched_skill.required_tool,
                source=matched_skill.name,
                arguments={"query": user_event.text, "mode": "skill_required"},
                metadata={"source_event_id": user_event.event_id},
            )
        if agent_result.tool_request and agent_result.tool_request.get("tool_name"):
            return ToolRequest(
                tool_name=agent_result.tool_request["tool_name"],
                source="agent_result",
                arguments=dict(agent_result.tool_request.get("arguments") or {}),
                metadata={"source_event_id": user_event.event_id},
            )
        return None

    def _run_tool_request(self, request: ToolRequest) -> ToolResult:
        safety = self.safety_guard.evaluate(request)
        if not safety.allowed:
            result = ToolResult(
                tool_name=request.tool_name,
                status="blocked",
                error={"reason": safety.reason, "metadata": safety.metadata},
                request_id=request.request_id,
            )
            self.store.log_tool_result(result, request.to_dict())
            return result
        result = self.tool_registry.execute(request)
        self.store.log_tool_result(result, request.to_dict())
        return result

    def _handle_reward_assets(
        self,
        source_event_id: str,
        reward_events: list,
        behavior_id: str,
    ) -> dict[str, Any] | None:
        if not reward_events:
            self.last_asset_result = None
            return None
        reward = reward_events[0]
        reward_id = reward.reward_id if hasattr(reward, "reward_id") else reward.get("reward_id")
        response = self.asset_service.create_reward_asset_request(
            source_event_id=source_event_id,
            reward_id=reward_id,
            behavior_id=behavior_id,
        )
        self.last_asset_result = response.to_dict()
        return response.to_dict()
