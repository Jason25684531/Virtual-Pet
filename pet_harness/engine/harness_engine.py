from __future__ import annotations

import json
import logging
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from character_library import CharacterLibrary
from pet_harness.agent.prompt_builder import PromptBuilder
from pet_harness.agent.provider_adapter import LLMProviderAdapter, ProviderReply
from pet_harness.agent.result_parser import ResultParser
from pet_harness.asset.factory import build_asset_service
from pet_harness.asset.mock_asset_service import MockAssetService
from pet_harness.behavior.behavior_manager import BehaviorManager
from pet_harness.character.profile import CharacterProfile
from pet_harness.memory.base_memory_store import BaseMemoryStore, NullMemoryStore
from pet_harness.models.events import PetEvent, ToolRequestEvent, UserEvent
from pet_harness.models.agent_result import AgentResult
from pet_harness.models.provider import ProviderConfig
from pet_harness.models.provider import ProviderStatus
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
from pet_harness.latency import TurnTimeline, create_turn, get_turn

LOGGER = logging.getLogger(__name__)


class _SentenceSplitter:
    """Buffer streamed text until a sentence boundary, stripping only first-turn actions."""

    _ACTION = re.compile(r"^\s*\[ACTION:([A-Za-z0-9_-]+)\]\s*", re.IGNORECASE)
    _END = set(".!?。！？\n")

    def __init__(self) -> None:
        self._buffer = ""
        self._first = True
        self.actions: list[str] = []

    def feed(self, fragment: str) -> list[str]:
        self._buffer += str(fragment or "")
        return self._drain(False)

    def flush(self) -> list[str]:
        return self._drain(True)

    def _drain(self, final: bool) -> list[str]:
        sentences: list[str] = []
        start = 0
        for index, char in enumerate(self._buffer):
            if char not in self._END:
                continue
            if char == "." and index + 1 < len(self._buffer) and self._buffer[index + 1].isdigit():
                continue
            sentences.append(self._buffer[start:index + 1].strip())
            start = index + 1
        if start:
            self._buffer = self._buffer[start:]
        if final and self._buffer.strip():
            sentences.append(self._buffer.strip())
            self._buffer = ""
        result: list[str] = []
        for sentence in sentences:
            if not sentence:
                continue
            if self._first:
                match = self._ACTION.match(sentence)
                if match:
                    self.actions.append(match.group(1).lower())
                    sentence = sentence[match.end():].strip()
                self._first = False
            if sentence:
                result.append(sentence)
        return result


class _StreamingReplyExtractor:
    """Yield only the reply string from JSON or preserve plain-text streams."""

    _REPLY_FIELD = re.compile(r'"reply"\s*:\s*"', re.DOTALL)
    _ACTION_PREFIX = re.compile(r"^\s*\[\s*ACTION\s*:", re.IGNORECASE)
    _ESCAPES = {"\"": '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

    def __init__(self) -> None:
        self._mode = "unknown"
        self._pending = ""
        self._in_reply = False
        self._escaped = False
        self._unicode_digits: str | None = None
        self._done = False

    def feed(self, fragment: str) -> str:
        if not fragment or self._done:
            return ""
        if self._mode == "plain":
            return str(fragment)
        self._pending += str(fragment)
        if self._mode == "unknown":
            stripped = self._pending.lstrip()
            if not stripped:
                return ""
            if self._ACTION_PREFIX.match(stripped) or not stripped.startswith(("{", "```")):
                self._mode = "plain"
                text, self._pending = self._pending, ""
                return text
            self._mode = "json"
        if not self._in_reply:
            match = self._REPLY_FIELD.search(self._pending)
            if not match:
                return ""
            self._pending = self._pending[match.end():]
            self._in_reply = True
        return self._consume_reply()

    def flush(self) -> str:
        if self._mode == "unknown":
            text, self._pending = self._pending, ""
            return text
        if self._mode == "plain" or self._done:
            return ""
        if not self._in_reply:
            return ""
        return self._consume_reply()

    def _consume_reply(self) -> str:
        output: list[str] = []
        text, self._pending = self._pending, ""
        for index, char in enumerate(text):
            if self._unicode_digits is not None:
                if char in "0123456789abcdefABCDEF":
                    self._unicode_digits += char
                    if len(self._unicode_digits) == 4:
                        output.append(chr(int(self._unicode_digits, 16)))
                        self._unicode_digits = None
                    continue
                output.append("\\u" + self._unicode_digits + char)
                self._unicode_digits = None
                continue
            if self._escaped:
                self._escaped = False
                if char == "u":
                    self._unicode_digits = ""
                else:
                    output.append(self._ESCAPES.get(char, char))
                continue
            if char == "\\":
                self._escaped = True
            elif char == '"':
                self._done = True
                self._in_reply = False
                break
            else:
                output.append(char)
        return "".join(output)


def _measure_call(callable_, *args):
    started_at = perf_counter()
    return callable_(*args), round((perf_counter() - started_at) * 1000)
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
        character_profile: CharacterProfile | None = None,
        profile_loader: Callable[[], CharacterProfile] | None = None,
        memory_store: BaseMemoryStore | None = None,
        memory_retriever=None,
        semantic_index_enabled: bool = False,
    ) -> None:
        self.agentic_root = Path(agentic_root)
        self.snapshot_path = Path(snapshot_path)
        self.memory_store = memory_store or NullMemoryStore()
        self.memory_retriever = memory_retriever
        self._semantic_index_enabled = semantic_index_enabled

        self._character_id = character_id
        self._profile: CharacterProfile | None = character_profile
        self._profile_loader = profile_loader
        effective_db_path = db_path
        if self._profile is not None:
            self._character_id = self._profile.character_id
            effective_db_path = self._profile.sqlite_path
        elif character_id is not None:
            self._profile = CharacterProfile.load(character_id)
            effective_db_path = self._profile.sqlite_path

        self.store = SQLiteStore(effective_db_path)
        self.store.initialize()
        if self.memory_retriever is None and hasattr(self.memory_store, "index") and hasattr(self.memory_store, "search"):
            from pet_harness.memory.contextual_memory_retriever import ContextualMemoryRetriever
            from pet_harness.memory.fastembed_reranker import FastembedReranker
            from pet_harness.memory.query_rewriter import LlmQueryRewriter
            import config
            rewriter = LlmQueryRewriter(self._rewrite_query, enabled=config.MEMORY_LLM_REWRITE_ENABLED)
            self.memory_retriever = ContextualMemoryRetriever(
                self.memory_store, self.memory_store.embed_dense, self.memory_store.sparse_encoder, rewriter,
                reranker=FastembedReranker() if config.MEMORY_RERANK_ENABLED else None,
            )
        self._memory_extractor = None
        if self.memory_retriever is not None and self._character_id is not None:
            from pet_harness.memory.memory_item_repository import MemoryItemRepository
            self._memory_repository = MemoryItemRepository(self.store, self._character_id)
        else:
            self._memory_repository = None

        # provider 由 ProviderRuntime 注入;provider_config 僅保留路由偏好,
        # 角色 store 不再持久化任何 provider 設定或狀態。
        self.provider_config = provider_config
        self.provider = provider
        if self._memory_repository is not None:
            from pet_harness.memory.memory_extractor import LlmMemoryExtractor
            self._memory_extractor = LlmMemoryExtractor(self._extract_memory_json)

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
        self._last_action_tag: str | None = None
        self.prompt_builder = PromptBuilder(self.agentic_root)
        self.result_parser = ResultParser()
        self.tool_registry = ToolRegistry()
        self.safety_guard = SafetyGuard(self.tool_registry)
        self.media_session_context = MediaSessionContext(self.store)
        self._tool_lifecycle = ToolExecutionLifecycle(self.safety_guard, self.tool_registry, self.store, self.skills, self.media_session_context)
        self.asset_service = build_asset_service(self.store, self._character_id, self.character_library)
        self.growth_trigger = None
        if self._profile_loader is not None and self._character_id:
            from pet_harness.asset.growth_trigger import GrowthTriggerService
            self.growth_trigger = GrowthTriggerService(
                self.store,
                self.asset_service,
                self._character_id,
                config.XP_PER_LEVEL,
                config.EVENT_INTERVAL_MINUTES,
            )
        self.last_prompt: str | None = None
        self._spoken_chunks: list[str] = []
        self.last_provider_raw_result: str | None = None
        self.last_agent_result: AgentResult | None = None
        self.last_tool_result: ToolResult | None = None
        self.last_asset_result: dict[str, Any] | None = None
        self._shutdown = False
        self._background_executor = None
        self._slow_tool_failure_callback = None
        self._memory_warmup_complete = False
        self._memory_warmup_completed_at: float | None = None

    def configure_background_executor(self, executor) -> None:
        """Reuse the application executor for side-effect tools; no ad-hoc threads."""
        self._background_executor = executor

    def configure_slow_tool_failure_callback(self, callback) -> None:
        self._slow_tool_failure_callback = callback

    @property
    def memory_warmup_complete(self) -> bool:
        return self._memory_warmup_complete

    @property
    def memory_warmup_completed_at(self) -> float | None:
        """perf_counter() timestamp of warmup completion, or None if not (yet) complete.
        Compared against a turn's own start time (not "now") to detect the race between
        warmup finishing and the first turn beginning; see TurnTimeline.resolve_warmup."""
        return self._memory_warmup_completed_at

    def warmup_memory(self) -> None:
        if self.memory_retriever is None:
            self._memory_warmup_complete = True
            self._memory_warmup_completed_at = perf_counter()
            return
        started = perf_counter()
        LOGGER.info("[MEMORY WARMUP] started character_id=%s", self._character_id)
        success = False
        try:
            warmup = getattr(self.memory_retriever, "warmup", None)
            if callable(warmup):
                warmup(self._character_id or "default")
            else:
                from pet_harness.memory.memory_models import RetrievalRequest
                self.memory_retriever.retrieve(RetrievalRequest(self._character_id or "default", "記憶預熱"))
            success = True
        except Exception:
            LOGGER.exception("[MEMORY WARMUP] failed character_id=%s", self._character_id)
        finally:
            self._memory_warmup_complete = success
            self._memory_warmup_completed_at = perf_counter() if success else None
            LOGGER.info("[MEMORY WARMUP] done warmup_ms=%s success=%s", round((perf_counter() - started) * 1000), success)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        close = getattr(self.memory_store, "shutdown", None)
        if callable(close):
            close()

    @property
    def character_profile(self) -> CharacterProfile | None:
        return self._profile

    def mark_spoken_chunk(self, text: str) -> None:
        normalized = str(text or "").strip()
        if normalized:
            self._spoken_chunks.append(normalized)

    def spoken_reply(self) -> str:
        return " ".join(self._spoken_chunks).strip()

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
            self._profile = (
                self._profile_loader()
                if self._profile_loader is not None
                else CharacterProfile.load(self._character_id)
            )
            self.refresh_skill_catalog()
            resolved = self.filter_skills_for_character(self.available_skills)
            self.skills = resolved.resolved_skills
            self.skip_diagnostics = resolved.skip_diagnostics
            self.store.sync_skills(self.skills)
            self.rebuild_router()
            self._tool_lifecycle.skills = {skill.name: skill for skill in self.skills}
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

        if config.SEMANTIC_ROUTING_ENABLED and self._semantic_index_enabled:
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

    def handle_event(
        self,
        event: UserEvent | dict[str, Any],
        *,
        stream_callback: Callable[[str], None] | None = None,
        action_callback: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> PetEvent:
        user_event = event if isinstance(event, UserEvent) else UserEvent.from_dict(event)
        timeline = get_turn(user_event.metadata.get("turn_id"))
        if timeline is None:
            # Direct/CLI callers have no adapter; keep observability fail-open.
            timeline = create_turn(user_event.event_id, "engine")
        timeline.mark("route_done")
        self._spoken_chunks = []
        state_before = self.store.state_snapshot()
        active_capabilities = self._active_capabilities()
        deterministic_skill = self._route_deterministic(user_event, active_capabilities)

        if self._is_ack_only_skill(deterministic_skill):
            return self._handle_ack_only_turn(user_event, deterministic_skill, timeline, stream_callback)
        if self._is_llm_synthesis_skill(deterministic_skill):
            timeline.ack_emitted = True
            timeline.mark("first_speech_chunk_emitted")
            if callable(stream_callback):
                stream_callback(self._ack_text(deterministic_skill, {"query": user_event.text}))

        # 工具先行:deterministic 命中且帶 required_tool 時,先執行工具再讓 LLM 合成回覆,
        # 讓回覆能引用本輪真實取得的資料,而非上一輪殘留的 tool_result(見
        # fix-core-interaction-experience)。LLM 呼叫次數維持一次。
        conversation_history = list(reversed(self.store.recent_events(limit=6)))
        retrieval_result = None
        retrieval_request = None
        if self.memory_retriever is not None:
            from pet_harness.memory.memory_models import RetrievalRequest
            from datetime import UTC, datetime
            from pet_harness.memory.query_rewriter import previous_turn
            prior_user, prior_assistant, age = previous_turn(conversation_history, datetime.now(UTC))
            retrieval_request = RetrievalRequest(self._character_id or "default", user_event.text, prior_user, prior_assistant, age)

        pre_llm_started_at = perf_counter()
        retrieval_ms = None
        if retrieval_request is None:
            tool_started_at = perf_counter()
            if deterministic_skill and deterministic_skill.required_tool:
                timeline.mark("tool_started")
            tool_first_event, tool_first_result = self._run_tool_first(user_event, deterministic_skill)
            if tool_first_event is not None:
                timeline.mark("tool_done")
            tool_ms = round((perf_counter() - tool_started_at) * 1000)
        else:
            # Both results are inputs to the prompt; state updates remain on this thread.
            with ThreadPoolExecutor(max_workers=2) as executor:
                def run_tool_first():
                    if deterministic_skill and deterministic_skill.required_tool:
                        timeline.mark("tool_started")
                    return _measure_call(self._run_tool_first, user_event, deterministic_skill)

                tool_future = executor.submit(run_tool_first)
                retrieval_future = executor.submit(_measure_call, self.memory_retriever.retrieve, retrieval_request)
                (tool_first_event, tool_first_result), tool_ms = tool_future.result()
                retrieval_result, retrieval_ms = retrieval_future.result()
            if tool_first_event is not None:
                timeline.mark("tool_done")
            timeline.mark("retrieval_done")
        pre_llm_ms = round((perf_counter() - pre_llm_started_at) * 1000)
        timeline.mark("pre_llm_done")
        pre_llm_trace = {
            "execution": "parallel" if retrieval_request is not None else "tool_only",
            "tool_ms": tool_ms,
            "retrieval_ms": retrieval_ms,
            "pre_llm_ms": pre_llm_ms,
            "expected_parallel_ms": max(tool_ms, retrieval_ms or 0),
        }
        LOGGER.info("[PRE-LLM] %s", pre_llm_trace)

        if retrieval_result is not None:
            memory_hits = retrieval_result.evidence
        else:
            memory_hits = self.memory_store.recall(user_event.text, top_k=3)
        memory_status = self.memory_store.status()

        prompt_result = self._build_prompt(user_event, state_before, deterministic_skill, tool_first_result, conversation_history, memory_hits, retrieval_result, ack_emitted=timeline.ack_emitted)
        LOGGER.info("[PROMPT SIZE] turn_id=%s chars=%s", timeline.turn_id, prompt_result.section_sizes)
        provider_reply, agent_result = self._invoke_provider(
            user_event,
            deterministic_skill,
            prompt_result.prompt,
            stream_callback=stream_callback,
            action_callback=action_callback,
            cancel=cancel,
            timeline=timeline,
        )
        stream_cancelled = bool(provider_reply.metadata.get("cancelled")) or bool(cancel is not None and cancel.is_set())
        if stream_cancelled:
            spoken_reply = self.spoken_reply()
            if not spoken_reply:
                return PetEvent(
                    source_event_id=user_event.event_id,
                    reply="",
                    matched_skill=None,
                    behavior_id="idle",
                    webm_key="idle",
                    xp_delta=0,
                    reward_events=[],
                    tool_request=None,
                    provider_status=provider_reply.provider_status.to_dict(),
                    saved_to_db=False,
                    metadata={"stale_turn": True, "spoken_chunks": 0},
                )
            agent_result.reply = spoken_reply
            provider_reply.reply = spoken_reply
        matched_skill, skill_source = self._parse_and_route(user_event, agent_result, active_capabilities)
        resolved_action, behavior_event = self._resolve_behavior(matched_skill, agent_result.action_tag)
        tool_event, tool_result_payload, tool_xp_bonus = self._run_tool_fallback(
            user_event, matched_skill, agent_result, tool_first_event, tool_first_result
        )
        xp_delta, reward_events, asset_result = self._award_and_reward(
            user_event, matched_skill, tool_xp_bonus, behavior_event.behavior_id
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
                    "streaming": bool(provider_reply.metadata.get("streaming")),
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
                    "retrieval_trace": retrieval_result.trace.to_dict() if retrieval_result else None,
                    "pre_llm_trace": pre_llm_trace,
                },
                "tool_result": tool_result_payload,
                "asset_result": asset_result,
            },
        )

        timeline.mark("turn_complete")
        timeline.set_context(
            character_id=self._character_id,
            route_kind="deterministic" if deterministic_skill else "conversation",
            skill_name=matched_skill.name if matched_skill else None,
            streaming=bool(provider_reply.metadata.get("streaming")),
            slow_tool=False,
        )
        pet_event.metadata["latency"] = timeline.report(**timeline.context)

        self._persist_and_snapshot(user_event, pet_event)
        LOGGER.info(
            "[CONVERSATION] character=%s user=%r assistant=%r",
            self._character_id,
            user_event.text,
            pet_event.reply,
        )
        return pet_event

    @staticmethod
    def _is_ack_only_skill(skill: Skill | None) -> bool:
        return bool(skill and skill.slow_tool and skill.post_tool_response_policy == "ack_only")

    @staticmethod
    def _is_llm_synthesis_skill(skill: Skill | None) -> bool:
        return bool(skill and skill.slow_tool and skill.post_tool_response_policy == "llm_synthesis")

    def _handle_ack_only_turn(self, event: UserEvent, skill: Skill, timeline: TurnTimeline, stream_callback) -> PetEvent:
        candidate = self._build_tool_request_candidate(event, skill)
        if candidate is None:
            raise RuntimeError("ack_only skill requires a tool candidate")
        candidate.metadata.update({"turn_id": timeline.turn_id, "character_id": self._character_id})
        tool_event = ToolRequestEvent(
            tool_name=candidate.tool_name, source_skill=skill.name,
            metadata={"source": candidate.source, "arguments": candidate.arguments, "turn_id": timeline.turn_id, "character_id": self._character_id},
        )
        timeline.mark("tool_started")

        def complete(ok: bool, message: str, result) -> None:
            timeline.mark("tool_done")
            # ack-only's one [TURN LATENCY] line fires at turn_complete, before this async
            # tool finishes — tool_ms is always None there. Re-log now that tool_done is
            # set, so tool_completion_ms actually surfaces per tool-result-synthesis's
            # "Slow Tool Metrics Separation" requirement (context is already set by the
            # synchronous turn_complete path above, so this is never a no-op here).
            timeline.log_current()
            if not ok or getattr(result, "status", "failed") not in {"success", "completed"}:
                LOGGER.warning("[SLOW TOOL FAILED] turn_id=%s character_id=%s tool=%s detail=%s", timeline.turn_id, self._character_id, candidate.tool_name, message or getattr(result, "error", None))
                callback = self._slow_tool_failure_callback
                if callable(callback):
                    callback(timeline.turn_id, "抱歉，剛才沒有成功找到這首歌。")
            else:
                LOGGER.info("[SLOW TOOL COMPLETE] turn_id=%s character_id=%s tool=%s", timeline.turn_id, self._character_id, candidate.tool_name)

        ack = self._ack_text(skill, candidate.arguments)
        timeline.ack_emitted = True
        timeline.mark("first_speech_chunk_emitted")
        if callable(stream_callback):
            stream_callback(ack)
        executor = self._background_executor
        if executor is None:
            # Non-UI tests and CLI have no application lifecycle; preserve legacy synchronous behavior.
            try:
                result = self._run_tool_request(candidate)
                complete(True, "", result)
            except Exception as exc:  # pragma: no cover - lifecycle executor covers production
                complete(False, str(exc), None)
        else:
            executor.submit(lambda: self._run_tool_request(candidate), complete)
        resolved_action, behavior_event = self._resolve_behavior(skill)
        status_getter = getattr(self.provider, "get_status", None)
        status = status_getter() if callable(status_getter) else ProviderStatus(healthy=True, message="ack-only")
        pet_event = PetEvent(
            source_event_id=event.event_id, reply=ack, matched_skill=skill.name,
            behavior_id=behavior_event.behavior_id, webm_key=behavior_event.webm_key, xp_delta=0,
            reward_events=[], tool_request=tool_event, provider_status=status.to_dict(), saved_to_db=False,
            action_tag=resolved_action["action_tag"] if resolved_action else None, motion_source=behavior_event.reason,
            metadata={"agentic": {"streaming": True, "llm_calls": 0, "ack_emitted": True}, "tool_result": None},
        )
        timeline.mark("turn_complete")
        timeline.set_context(
            character_id=self._character_id, route_kind="deterministic", skill_name=skill.name, streaming=True, slow_tool=True,
        )
        pet_event.metadata["latency"] = timeline.report(**timeline.context)
        self._persist_and_snapshot(event, pet_event)
        return pet_event

    @staticmethod
    def _ack_text(skill: Skill, arguments: dict[str, Any]) -> str:
        template = skill.ack_template or "我來幫你處理。"
        song = str(arguments.get("query") or "這首歌").strip()
        try:
            return template.format(song=song)
        except (KeyError, ValueError):
            return "我來幫你處理。"

    def _route_deterministic(self, event: UserEvent, capabilities: set[str]) -> Skill | None:
        return self.router.match(event.text, capabilities)

    def _build_prompt(
        self,
        event: UserEvent,
        state: dict[str, Any],
        deterministic_skill: Skill | None,
        tool_result: ToolResult | None,
        history: list[dict[str, Any]],
        memory_hits: list[Any],
        retrieval_result=None,
        ack_emitted: bool = False,
    ):
        return self.prompt_builder.build(
            event=event,
            skills=self.skills,
            state_snapshot=state,
            matched_skill=deterministic_skill,
            persona=self._profile.effective_persona if self._profile else None,
            action_tags=(
                [
                    tag
                    for tag in self.character_library.list_action_tags(self._character_id)
                    if tag != self._last_action_tag
                ]
                or self.character_library.list_action_tags(self._character_id)
            ),
            tool_result=tool_result,
            conversation_history=history,
            memory_hits=memory_hits,
            retrieval_result=retrieval_result,
            ack_emitted=ack_emitted,
        )

    def _run_tool_first(self, event: UserEvent, skill: Skill | None) -> tuple[ToolRequestEvent | None, ToolResult | None]:
        if skill is None or not skill.required_tool:
            return None, None
        candidate = self._build_tool_request_candidate(event, skill)
        if candidate is None:
            return None, None
        tool_event, result, _payload, _bonus = self._execute_tool_candidate(event, candidate, skill.name)
        return tool_event, result

    def _invoke_provider(
        self,
        event: UserEvent,
        skill: Skill | None,
        prompt: str,
        *,
        stream_callback: Callable[[str], None] | None = None,
        action_callback: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
        timeline: TurnTimeline | None = None,
    ):
        self.last_prompt = prompt
        if timeline is not None:
            timeline.mark("llm_request_started")
        stream_method = getattr(self.provider, "generate_reply_stream", None)
        if stream_callback is not None and callable(stream_method):
            cancel = cancel or threading.Event()
            stream = stream_method(
                event,
                matched_skill=skill,
                prompt_text=prompt,
                cancel=cancel,
            )
            # stream_method may exist (e.g. ProviderRuntime always exposes it) yet still
            # return None when the underlying provider doesn't actually support streaming
            # (see LLMProviderAdapter protocol). Fall through to the blocking path below
            # instead of recursing: a bare recursive call here used to drop timeline/
            # stream_callback/cancel, silently breaking latency instrumentation and
            # barge-in cancellation, and would infinite-loop against a wrapper that
            # always exposes a callable generate_reply_stream.
            if stream is not None:
                fragments: list[str] = []
                splitter = _SentenceSplitter()
                reply_extractor = _StreamingReplyExtractor()
                first_fragment_seen = False
                for fragment in stream:
                    if cancel.is_set():
                        break
                    raw_fragment = str(fragment)
                    if timeline is not None:
                        timeline.mark("llm_first_token")
                    if not first_fragment_seen:
                        first_fragment_seen = True
                        if timeline is not None:
                            LOGGER.info(
                                "[LLM STREAM] turn_id=%s streaming=True first_fragment_ms=%s",
                                timeline.turn_id, timeline.ms("llm_request_started", "llm_first_token"),
                            )
                    fragments.append(raw_fragment)
                    display_fragment = reply_extractor.feed(raw_fragment)
                    for sentence in splitter.feed(display_fragment):
                        if action_callback and splitter.actions:
                            action_callback(splitter.actions.pop(0))
                        stream_callback(sentence)
                        if timeline is not None:
                            timeline.mark("first_speech_chunk_emitted")
                if not cancel.is_set():
                    display_fragment = reply_extractor.flush()
                    for sentence in splitter.feed(display_fragment) + splitter.flush():
                        if action_callback and splitter.actions:
                            action_callback(splitter.actions.pop(0))
                        stream_callback(sentence)
                        if timeline is not None:
                            timeline.mark("first_speech_chunk_emitted")
                raw_text = "".join(fragments)
                status_getter = getattr(self.provider, "get_status", None)
                status = status_getter() if callable(status_getter) else ProviderStatus(healthy=True, message="streaming provider ready")
                provider_reply = ProviderReply(
                    reply=raw_text,
                    provider_status=status,
                    raw_text=raw_text,
                    prompt_text=prompt,
                    metadata={"streaming": True, "cancelled": cancel.is_set()},
                )
                self.last_provider_raw_result = raw_text
                agent_result = self.result_parser.parse(
                    raw_text or provider_reply.reply,
                    provider_type=provider_reply.provider_status.provider_type,
                    fallback_reply=provider_reply.reply,
                )
                self.last_agent_result = agent_result
                if timeline is not None:
                    timeline.mark("llm_done")
                return provider_reply, agent_result
        provider_reply = self.provider.generate_reply(event, matched_skill=skill, prompt_text=prompt)
        self.last_provider_raw_result = provider_reply.raw_text or provider_reply.reply
        agent_result = self.result_parser.parse(
            self.last_provider_raw_result or provider_reply.reply,
            provider_type=provider_reply.provider_status.provider_type,
            fallback_reply=provider_reply.reply,
        )
        self.last_agent_result = agent_result
        if timeline is not None:
            timeline.mark("llm_first_token")
            timeline.mark("llm_done")
        return provider_reply, agent_result

    def _parse_and_route(self, event: UserEvent, result: AgentResult, capabilities: set[str]):
        import config

        skill, source = self.router.route(
            event.text,
            suggested_skill_name=result.matched_skill,
            suggested_confidence=result.confidence,
            allow_fallback=self.provider_config.routing_fallback_enabled if self.provider_config else config.PROVIDER_ROUTING_FALLBACK_ENABLED,
            confidence_threshold=self.provider_config.routing_confidence_threshold if self.provider_config else config.PROVIDER_ROUTING_CONFIDENCE_THRESHOLD,
            active_capabilities=capabilities,
            **self._semantic_route_options(),
        )
        LOGGER.info("[SKILL ROUTE] text_length=%s matched=%s source=%s", len(event.text), getattr(skill, "name", None), source)
        return skill, source

    def _resolve_behavior(self, skill: Skill | None, suggested_action_tag: str | None = None):
        resolved_action = None
        action_tags = self.character_library.list_action_tags(self._character_id)
        if action_tags:
            candidates = [tag for tag in action_tags if tag != self._last_action_tag] or action_tags
            action_tag = suggested_action_tag if suggested_action_tag in candidates else random.choice(candidates)
            resolved_action = self.character_library.resolve_action_tag(self._character_id, action_tag)
            if resolved_action is None:
                LOGGER.warning("Ignoring unavailable action tag for character %s: %s", self._character_id, action_tag)
            else:
                self._last_action_tag = action_tag
        behavior = self.behavior_manager.resolve(skill, action_motion_key=resolved_action["motion_key"] if resolved_action else None)
        return resolved_action, behavior

    def _run_tool_fallback(
        self,
        event: UserEvent,
        skill: Skill | None,
        result: AgentResult,
        tool_first_event: ToolRequestEvent | None,
        tool_first_result: ToolResult | None,
    ) -> tuple[ToolRequestEvent | None, dict[str, Any] | None, int]:
        self.last_tool_result = tool_first_result
        if tool_first_result is not None:
            bonus = self.tool_registry.get(tool_first_result.tool_name)
            return tool_first_event, tool_first_result.to_dict(), bonus.xp_reward if bonus and tool_first_result.status in {"completed", "success"} else 0
        candidate = self._build_tool_request_candidate(event, skill, result)
        if candidate is None:
            return None, None, 0
        tool_event, tool_result, payload, bonus = self._execute_tool_candidate(event, candidate, skill.name if skill else None)
        self.last_tool_result = tool_result
        return tool_event, payload, bonus

    def _award_and_reward(self, event: UserEvent, skill: Skill | None, tool_xp_bonus: int, behavior_id: str):
        import config
        from pet_harness.asset.growth_trigger import is_generation_frozen
        if is_generation_frozen(self.store, config.PREVIEW_OFFER_TTL_HOURS):
            return 0, [], None
        xp_delta = self.xp_manager.award_for_event(skill)
        if tool_xp_bonus:
            self.store.add_user_xp(tool_xp_bonus)
            xp_delta += tool_xp_bonus
        rewards = self.reward_manager.check_unlocks(self.store.get_user_progress()["xp_total"])
        assets = self._handle_reward_assets(source_event_id=event.event_id, reward_events=rewards, behavior_id=behavior_id)
        if self.growth_trigger is not None:
            growth = (
                self.growth_trigger.on_interaction(event.event_id)
                if isinstance(self.asset_service, MockAssetService)
                else self.growth_trigger.on_xp_awarded(self.store.get_user_progress()["xp_total"], event.event_id)
            )
            if growth is not None:
                assets = {"pending_offer": growth.to_dict()}
        return xp_delta, rewards, assets

    def _persist_and_snapshot(self, user_event: UserEvent, pet_event: PetEvent) -> None:
        self.store.log_event(user_event.to_dict(), pet_event.to_dict())
        pet_event.saved_to_db = True
        self._write_snapshot(pet_event)
        self.memory_store.save_turn(user_event.event_id, user_event.text, pet_event.reply)
        if self._memory_extractor is not None:
            threading.Thread(
                target=self._index_memory_turn,
                args=(user_event.event_id, user_event.text, pet_event.reply),
                daemon=True,
                name="memory-item-index",
            ).start()

    def _index_memory_turn(self, event_id: str, user_text: str, reply: str) -> None:
        try:
            items = self._memory_repository.upsert_candidates(self._memory_extractor.extract(event_id, user_text, reply))
            indexed = self.memory_store.index(items)
            self._memory_repository.mark_indexed(indexed)
        except Exception:
            LOGGER.exception("memory item indexing failed")

    def forget_memory(self, memory_key: str) -> list[str]:
        if self._memory_repository is None:
            return []
        memory_ids = self._memory_repository.forget(memory_key)
        delete = getattr(self.memory_store, "delete", None)
        if callable(delete):
            delete(memory_ids)
        return memory_ids

    def _extract_memory_json(self, user_text: str, reply: str) -> str:
        prompt = (
            "Extract only user-stated facts, or explicit assistant promises, as a JSON array. "
            "Each item must contain memory_key, memory_type (semantic or episodic), and text. "
            "memory_key must use 使用者/角色 plus an allowed attribute; 喜好、狀態、計劃 should add one stable topic/entity subject as a third segment (for example 使用者.喜好.拉麵), while legacy two-segment keys remain valid. Never use a value, polarity, or action as the subject. "
            "A promise must answer『你之前答應要做什麼？』. "
            "Examples to extract: 使用者事實『我喜歡蘋果』; true promise『下次我幫你查攻略』. "
            "Do not extract conditional offers『如果需要幫忙請告訴我』, wishes『希望你的牙齒能健康』, "
            "使用者的提問, or 角色自述設定. Never include character profile claims or assistant guesses.\n"
            f"User: {user_text}\nAssistant: {reply}"
        )
        response = self.provider.generate_reply(UserEvent(text=user_text, source="memory_extractor"), prompt_text=prompt)
        return response.raw_text or response.reply

    def _rewrite_query(self, request, *, timeout: float) -> str:
        prompt = (
            "Rewrite the current user message into a standalone retrieval query. Return only the query.\n"
            f"Previous user: {request.previous_user_text or ''}\n"
            f"Previous assistant: {request.previous_assistant_text or ''}\n"
            f"Current user: {request.current_turn_text}"
        )
        response = self.provider.generate_reply(UserEvent(text=request.current_turn_text, source="query_rewriter"), prompt_text=prompt)
        text = (response.raw_text or response.reply).strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            return "\n".join(lines).strip()
        return text

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
