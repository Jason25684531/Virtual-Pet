from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from character_library import CharacterLibrary
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.agent.provider_adapter import LLMProviderAdapter
from pet_harness.agent.result_parser import ResultParser
from pet_harness.asset.factory import build_asset_service
from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.character.profile import CharacterProfile
from pet_harness.memory.base_memory_store import BaseMemoryStore, NullMemoryStore
from pet_harness.models.events import PetEvent, ToolRequestEvent, UserEvent
from pet_harness.models.agent_result import AgentResult
from pet_harness.models.provider import ProviderConfig
from pet_harness.models.skill import Skill
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.skills.semantic_skill_retriever import QdrantFastEmbedRetriever, semantic_manifest
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.safety_guard import SafetyGuard
from pet_harness.tools.tool_models import ToolRequest, ToolResult
from pet_harness.engine.media_session_context import MediaSessionContext
from pet_harness.engine.tool_execution_lifecycle import ToolExecutionLifecycle
from pet_harness.xp.reward_manager import RewardManager
from pet_harness.xp.xp_manager import XPManager

LOGGER = logging.getLogger(__name__)
CHARACTER_SKILL_ENABLED_KEY = "character_skill_enabled"
MEDIA_SKILL_MIGRATION_KEY = "media_skill_migration_v1"
LEGACY_MEDIA_SKILLS = {"music_bgm": "youtube_music_playback", "game_news": "bahamut_daily_news"}


@dataclass
class ResolvedSkillView:
    resolved_skills: list[Skill]
    resolved_skill_map: dict[str, Skill]
    skip_diagnostics: list[dict[str, str]]


class PetHarnessEngine:
    def __init__(
        self,
        provider: LLMProviderAdapter,
        agentic_root: str | Path = Path(".agentic"),
        db_path: str | Path = Path("data") / "pet_state.db",
        snapshot_path: str | Path = Path("debug") / "events" / "latest_pet_event.json",
        provider_config: ProviderConfig | None = None,
        character_id: str | None = None,
        memory_store: BaseMemoryStore | None = None,
    ) -> None:
        self.agentic_root = Path(agentic_root)
        self.snapshot_path = Path(snapshot_path)
        self.memory_store = memory_store or NullMemoryStore()

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

        self.available_skills: list[Skill] = []
        self.skill_load_errors: dict[str, str] = {}
        self.skip_diagnostics: list[dict[str, str]] = []
        self.refresh_skill_catalog()
        self.skills: list[Skill] = list(self.available_skills)
        if self._profile is not None:
            self._initialize_character_skill_overlay(self._profile.allowed_skill_refs)
        resolved = self.filter_skills_for_character(self.available_skills)
        self.skills = resolved.resolved_skills
        self.skip_diagnostics = resolved.skip_diagnostics
        self.store.sync_skills(self.skills)
        import config
        self.semantic_retriever = QdrantFastEmbedRetriever(
            mode=config.QDRANT_MODE, path=config.QDRANT_PATH, url=config.QDRANT_URL,
            model=config.SEMANTIC_ROUTING_MODEL, collection=config.SEMANTIC_ROUTING_COLLECTION,
        )
        self.rebuild_router()
        self.xp_manager = XPManager(self.store)
        self.reward_manager = RewardManager(self.store, self.agentic_root / "rewards" / "reward_rules.json")
        self.behavior_manager = BehaviorManager(self.store, self.agentic_root / "behavior" / "behavior_map.json")
        self.character_library = CharacterLibrary()
        self.prompt_builder = PromptBuilder(self.agentic_root)
        self.result_parser = ResultParser()
        self.tool_registry = ToolRegistry()
        self.safety_guard = SafetyGuard(self.tool_registry)
        self.media_session_context = MediaSessionContext(self.store)
        self._tool_lifecycle = ToolExecutionLifecycle(self.safety_guard, self.tool_registry, self.store, self.skills, self.media_session_context)
        self.asset_service = build_asset_service(self.store, self._character_id, self.character_library)
        self.last_prompt: str | None = None
        self.last_provider_raw_result: str | None = None
        self.last_agent_result: AgentResult | None = None
        self.last_tool_result: ToolResult | None = None
        self.last_asset_result: dict[str, Any] | None = None

    @property
    def character_profile(self) -> CharacterProfile | None:
        return self._profile

    def refresh_skill_catalog(self) -> list[Skill]:
        loader = SkillLoader(self.agentic_root / "skills")
        self.available_skills = loader.load_skills()
        self.skill_load_errors = dict(loader.load_errors)
        if self._profile is not None:
            self._migrate_media_skills()
        return list(self.available_skills)

    def discoverable_skills(self) -> list[Skill]:
        if self._profile is None:
            return list(self.available_skills)
        allowed = set(self._profile.allowed_skill_refs)
        return [skill for skill in self.available_skills if skill.name in allowed]

    def refresh_tool_registry(self, registry: ToolRegistry) -> None:
        self.tool_registry = registry
        self.safety_guard = SafetyGuard(registry)
        self._tool_lifecycle = ToolExecutionLifecycle(
            self.safety_guard, registry, self.store, self.skills, self.media_session_context
        )

    def reload_profile(self) -> None:
        """從磁碟重載 active character 的 profile+personal,讓 persona/alias/local skill 熱更新。

        personal.json 無論經由 customization 面板、手動編輯或外部工具修改,
        下一次互動都會套用;載入失敗時保留前一份 profile,不中斷互動。"""
        if self._character_id is None:
            return
        try:
            self._profile = CharacterProfile.load(self._character_id)
            self.refresh_skill_catalog()
        except Exception:  # noqa: BLE001 - 角色資料暫時不可讀時維持舊 profile
            LOGGER.warning("profile reload failed for %s; keeping previous profile", self._character_id)

    def rebuild_router(self, skills: list[Skill] | None = None) -> None:
        """套用 active character 的 skill_overrides(別名/priority)並重建 router。

        供 __init__ 與 PyQtHarnessAdapter._refresh_runtime 共用,確保兩者用同一套
        resolved skill view 邏輯,不會出現 router 未套用最新 override 的分裂狀態。
        """
        base_skills = skills if skills is not None else self.skills
        if self._profile is not None:
            self.skills, priorities = self._profile.apply_skill_overrides(base_skills)
            self.skills = [replace(skill, priority=priorities.get(skill.name, skill.priority)) for skill in self.skills]
        else:
            self.skills = base_skills
        self.router = SkillRouter(self.skills, semantic_retriever=getattr(self, "semantic_retriever", None))

    def preview_skill_match(self, text: str) -> dict[str, Any]:
        """非執行預覽:回傳命中診斷,絕不觸發工具、XP、事件或動畫。"""
        return self.router.match_diagnostics(text, self._active_capabilities())

    def refresh_semantic_index(self) -> None:
        """Queue a non-blocking index refresh after the resolved skill view changes."""
        import config
        from PyQt5.QtWidgets import QApplication

        if config.SEMANTIC_ROUTING_ENABLED and QApplication.instance() is not None:
            # ponytail: full rebuild, incremental upsert if skill count grows.
            self.semantic_retriever.index(semantic_manifest(self.skills, character_id=self._character_id, model=config.SEMANTIC_ROUTING_MODEL))

    def filter_skills_for_character(self, skills: list[Skill]) -> ResolvedSkillView:
        """依 active character 的 skill_config 過濾傳入的技能清單；無 character_id 時原樣回傳。

        供外部（例如 PyQtHarnessAdapter 熱重載技能時）在不需要碰觸
        _profile 的前提下，重新套用 per-character 技能隔離。
        """
        available = {skill.name: skill for skill in skills}
        diagnostics: list[dict[str, str]] = []
        if self._profile is None:
            resolved = list(skills)
        else:
            enabled = self.store.get_setting(CHARACTER_SKILL_ENABLED_KEY, {})
            enabled_map = dict(enabled) if isinstance(enabled, dict) else {}
            resolved = []
            allowed = set(self._profile.allowed_skill_refs)
            for name in available:
                if name not in allowed:
                    diagnostics.append({"skill_id": name, "reason": "not_allowed"})
            for name in self._profile.allowed_skill_refs:
                skill = available.get(name)
                if skill is None:
                    diagnostics.append({"skill_id": name, "reason": "not_loaded"})
                    continue
                if not enabled_map.get(name, True):
                    diagnostics.append({"skill_id": name, "reason": "disabled"})
                    continue
                resolved.append(skill)
            try:
                resolved.extend(self._profile.load_local_skills())
            except Exception as exc:  # local skills must not make routing unsafe
                diagnostics.append({"skill_id": "local", "reason": "load_error", "detail": str(exc)})
        for path, error in self.skill_load_errors.items():
            diagnostics.append({"skill_id": path, "reason": "load_error", "detail": error})
        return ResolvedSkillView(resolved, {skill.name: skill for skill in resolved}, diagnostics)

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

    def _migrate_media_skills(self) -> None:
        if self.store.get_setting(MEDIA_SKILL_MIGRATION_KEY, False):
            return
        assert self._profile is not None
        available = {skill.name for skill in self.available_skills}
        refs = list(self._profile.skill_config)
        stale = [name for name in refs if name not in available]
        kept = [name for name in refs if name in available]
        added = [target for legacy, target in LEGACY_MEDIA_SKILLS.items() if legacy in kept and target in available and target not in kept]
        if stale or added:
            self._profile.skill_config = kept + added
            self._profile.save()
            log = LOGGER.warning if stale else LOGGER.info
            log("[SKILL MIGRATION] character=%s added=%s removed_stale=%s", self._character_id, added, stale)
        self.store.set_setting(MEDIA_SKILL_MIGRATION_KEY, True)

    def handle_event(self, event: UserEvent | dict[str, Any]) -> PetEvent:
        user_event = event if isinstance(event, UserEvent) else UserEvent.from_dict(event)
        state_before = self.store.state_snapshot()
        active_capabilities = self._active_capabilities()
        deterministic_skill = self.router.match(user_event.text, active_capabilities)

        # 工具先行:deterministic 命中且帶 required_tool 時,先執行工具再讓 LLM 合成回覆,
        # 讓回覆能引用本輪真實取得的資料,而非上一輪殘留的 tool_result(見
        # fix-core-interaction-experience)。LLM 呼叫次數維持一次。
        tool_first_event = None
        tool_first_result = None
        if deterministic_skill is not None and deterministic_skill.required_tool:
            tool_first_candidate = self._build_tool_request_candidate(user_event, deterministic_skill)
            if tool_first_candidate is not None:
                tool_first_event, tool_first_result, _payload, _bonus = self._execute_tool_candidate(
                    user_event, tool_first_candidate, deterministic_skill.name
                )

        conversation_history = list(reversed(self.store.recent_events(limit=6)))
        memory_hits = self.memory_store.recall(user_event.text, top_k=3)
        memory_status = self.memory_store.status()

        prompt_result = self.prompt_builder.build(
            event=user_event,
            skills=self.skills,
            state_snapshot=state_before,
            matched_skill=deterministic_skill,
            persona=self._profile.effective_persona if self._profile else None,
            action_tags=self.character_library.list_action_tags(self._character_id),
            tool_result=tool_first_result,
            conversation_history=conversation_history,
            memory_hits=memory_hits,
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

        import config
        matched_skill, skill_source = self.router.route(
            user_event.text,
            suggested_skill_name=agent_result.matched_skill,
            suggested_confidence=agent_result.confidence,
            allow_fallback=self.provider_config.routing_fallback_enabled if self.provider_config else config.PROVIDER_ROUTING_FALLBACK_ENABLED,
            confidence_threshold=self.provider_config.routing_confidence_threshold if self.provider_config else config.PROVIDER_ROUTING_CONFIDENCE_THRESHOLD,
            active_capabilities=active_capabilities,
            **self._semantic_route_options(),
        )
        LOGGER.info("[SKILL ROUTE] text_length=%s matched=%s source=%s", len(user_event.text), getattr(matched_skill, "name", None), skill_source)

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
        tool_event = None
        tool_result_payload = None
        tool_xp_bonus = 0
        self.last_tool_result = None
        if tool_first_result is not None:
            # 工具已在生成回覆前執行過(見上方工具先行區塊),沿用同一份結果,不重複呼叫工具。
            tool_event = tool_first_event
            self.last_tool_result = tool_first_result
            tool_result_payload = tool_first_result.to_dict()
            if tool_first_result.status in {"completed", "success"}:
                definition = self.tool_registry.get(tool_first_result.tool_name)
                tool_xp_bonus = definition.xp_reward if definition is not None else 0
        else:
            tool_candidate = self._build_tool_request_candidate(user_event, matched_skill, agent_result)
            if tool_candidate is not None:
                tool_event, tool_result, tool_result_payload, tool_xp_bonus = self._execute_tool_candidate(
                    user_event, tool_candidate, matched_skill.name if matched_skill else None
                )
                self.last_tool_result = tool_result

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
                    **self.router.last_route_diagnostics,
                    "skip_diagnostics": self.skip_diagnostics,
                    "memory_status": memory_status.state,
                    "memory_status_reason": memory_status.reason,
                    "memory_hit_count": len(memory_hits),
                },
                "tool_result": tool_result_payload,
                "asset_result": asset_result,
            },
        )

        self.store.log_event(user_event.to_dict(), pet_event.to_dict())
        pet_event.saved_to_db = True
        self._write_snapshot(pet_event)
        self.memory_store.save_turn(user_event.event_id, user_event.text, pet_event.reply)
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
        agent_result: AgentResult | None = None,
    ) -> ToolRequest | None:
        article_index = self.media_session_context.follow_up_index(user_event.text)
        media_context = self.media_session_context.load()
        articles = media_context.get("articles") or []
        if article_index and article_index <= len(articles):
            action = "open_article" if "打開" in user_event.text else "get_article_detail"
            return ToolRequest(
                tool_name="web_article_tool",
                source="bahamut_daily_news",
                arguments={"action": action, "article_index": article_index, "article": articles[article_index - 1]},
                metadata={"source_event_id": user_event.event_id, "raw_text": user_event.text},
            )
        if matched_skill and matched_skill.required_tool:
            arguments = {"query": user_event.text, "mode": "skill_required"}
            arguments.update(dict(matched_skill.tool_policy.get("defaults") or {}))
            arguments.update(self._media_arguments(matched_skill, user_event.text))
            LOGGER.info("[TOOL ROUTE] skill=%s tool=%s action=%s", matched_skill.name, matched_skill.required_tool, arguments.get("action"))
            return ToolRequest(
                tool_name=matched_skill.required_tool,
                source=matched_skill.name,
                arguments=arguments,
                metadata={"source_event_id": user_event.event_id, "raw_text": user_event.text},
            )
        if agent_result is not None and agent_result.tool_request and agent_result.tool_request.get("tool_name"):
            return ToolRequest(
                tool_name=agent_result.tool_request["tool_name"],
                source="agent_result",
                arguments=dict(agent_result.tool_request.get("arguments") or {}),
                metadata={"source_event_id": user_event.event_id},
            )
        return None

    def _execute_tool_candidate(
        self,
        user_event: UserEvent,
        tool_candidate: ToolRequest,
        source_skill_name: str | None,
    ) -> tuple[ToolRequestEvent, ToolResult, dict[str, Any], int]:
        tool_event = ToolRequestEvent(
            tool_name=tool_candidate.tool_name,
            source_skill=source_skill_name or tool_candidate.source,
            metadata={"source": tool_candidate.source, "arguments": tool_candidate.arguments},
        )
        tool_result = self._run_tool_request(tool_candidate)
        tool_xp_bonus = 0
        if tool_result.status in {"completed", "success"}:
            definition = self.tool_registry.get(tool_result.tool_name)
            tool_xp_bonus = definition.xp_reward if definition is not None else 0
        return tool_event, tool_result, tool_result.to_dict(), tool_xp_bonus

    def _active_capabilities(self) -> set[str]:
        context = self.media_session_context.load()
        return {"music"} if context.get("playback") else set()

    @staticmethod
    def _media_arguments(skill: Skill, text: str) -> dict[str, Any]:
        if skill.capability != "music":
            return {}
        from pet_harness.skills.intent_normalizer import normalize

        normalized = normalize(text).stripped_text
        actions = {
            "暫停": "pause",
            "繼續播放": "resume",
            "停止播放": "stop",
            "現在在播放什麼": "get_status",
        }
        if normalized in actions:
            return {"action": actions[normalized], "query": ""}
        query = re.sub(r"^(?:播放|播歌|播|放一首|放|我想聽|想聽)\s*", "", normalized).strip()
        return {"action": "search_and_play", "query": query or normalized}

    @staticmethod
    def _semantic_route_options() -> dict[str, Any]:
        import config

        return {
            "semantic_enabled": config.SEMANTIC_ROUTING_ENABLED,
            "semantic_shadow_mode": config.SEMANTIC_ROUTING_SHADOW_MODE,
            "semantic_top_k": config.SEMANTIC_ROUTING_TOP_K,
            "semantic_accept_threshold": config.SEMANTIC_ROUTING_ACCEPT_THRESHOLD,
            "semantic_margin_threshold": config.SEMANTIC_ROUTING_MARGIN_THRESHOLD,
        }

    def _run_tool_request(self, request: ToolRequest) -> ToolResult:
        self._tool_lifecycle.skills = {skill.name: skill for skill in self.skills}
        return self._tool_lifecycle.run(request)

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
            variant_type=str(getattr(reward, "metadata", {}).get("variant_type", "development")),
        )
        self.last_asset_result = response.to_dict()
        return response.to_dict()
