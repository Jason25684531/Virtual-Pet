from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pet_harness.agent.ollama_provider import OllamaProvider
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.agent.provider_factory import create_provider
from pet_harness.agent.provider_adapter import LLMProviderAdapter
from pet_harness.agent.result_parser import ResultParser
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.models.events import PetEvent, ToolRequestEvent, UserEvent
from pet_harness.models.agent_result import AgentResult
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.safety_guard import SafetyGuard
from pet_harness.tools.tool_models import ToolRequest, ToolResult
from pet_harness.xp.reward_manager import RewardManager
from pet_harness.xp.xp_manager import XPManager


class PetHarnessEngine:
    def __init__(
        self,
        agentic_root: str | Path = Path(".agentic"),
        db_path: str | Path = Path("data") / "pet_state.db",
        snapshot_path: str | Path = Path("debug") / "events" / "latest_pet_event.json",
        provider: LLMProviderAdapter | None = None,
        provider_config: ProviderConfig | None = None,
        request_fn=None,
    ) -> None:
        self.agentic_root = Path(agentic_root)
        self.snapshot_path = Path(snapshot_path)
        self.store = SQLiteStore(db_path)
        self.store.initialize()

        if provider_config is not None:
            self.store.set_provider_config(provider_config)
        self.provider_config = provider_config or self.store.get_provider_config()
        self.provider = provider or create_provider(self.provider_config, request_fn=request_fn)

        self.skills = SkillLoader(self.agentic_root / "skills").load_skills()
        self.store.sync_skills(self.skills)
        self.router = SkillRouter(self.skills)
        self.xp_manager = XPManager(self.store)
        self.reward_manager = RewardManager(self.store, self.agentic_root / "rewards" / "reward_rules.json")
        self.behavior_manager = BehaviorManager(self.store, self.agentic_root / "behavior" / "behavior_map.json")
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

    def handle_event(self, event: UserEvent | dict[str, Any]) -> PetEvent:
        user_event = event if isinstance(event, UserEvent) else UserEvent.from_dict(event)
        state_before = self.store.state_snapshot()
        deterministic_skill = self.router.match(user_event.text)
        prompt_result = self.prompt_builder.build(
            event=user_event,
            skills=self.skills,
            state_snapshot=state_before,
            matched_skill=deterministic_skill,
        )
        self.last_prompt = prompt_result.prompt
        provider_reply = self.provider.generate_reply(
            user_event,
            matched_skill=deterministic_skill,
            prompt_text=prompt_result.prompt,
        )
        self.last_provider_raw_result = provider_reply.raw_text or provider_reply.reply
        self.store.set_provider_status(provider_reply.provider_status)
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
            allow_fallback=self.provider_config.routing_fallback_enabled,
            confidence_threshold=self.provider_config.routing_confidence_threshold,
        )

        behavior_event = self.behavior_manager.resolve(matched_skill)
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
            "provider_config": self.store.get_provider_config().to_dict(),
            "provider_status": self.store.get_provider_status(),
            "recent_tool_count": len(self.store.recent_tool_results(limit=10)),
            "asset_manifest_count": len(self.store.list_asset_manifest(limit=10)),
            "snapshot_path": str(self.snapshot_path),
        }

    def state_snapshot(self) -> dict[str, Any]:
        return self.store.state_snapshot()

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

    def ollama_health(self) -> dict[str, Any]:
        config = ProviderConfig(
            provider_type=ProviderType.OLLAMA,
            base_url=self.provider_config.base_url or "http://localhost:11434",
            model_name=self.provider_config.model_name,
            timeout_seconds=self.provider_config.timeout_seconds,
            fallback_provider=self.provider_config.fallback_provider,
        )
        provider = OllamaProvider(config)
        status = provider.provider_status_from_health()
        self.store.set_provider_status(status)
        return {"provider_status": status.to_dict()}

    def ollama_model(self, model_name: str) -> dict[str, Any]:
        config = ProviderConfig(
            provider_type=ProviderType.OLLAMA,
            base_url=self.provider_config.base_url or "http://localhost:11434",
            model_name=self.provider_config.model_name,
            timeout_seconds=self.provider_config.timeout_seconds,
            fallback_provider=self.provider_config.fallback_provider,
        )
        provider = OllamaProvider(config)
        return {"model_check": provider.check_model(model_name)}

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
