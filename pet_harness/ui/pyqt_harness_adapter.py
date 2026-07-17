from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import config
from pet_harness.character.registry import CharacterRegistry
from pet_harness.character.router import CharacterRouter
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.events import PetEvent
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.models.skill import Skill
from pet_harness.runtime.provider_runtime import ProviderRuntime, migrate_legacy_provider_config
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.safety_guard import SafetyGuard
from pet_harness.tools.tool_models import ToolDefinition, ToolExecutionClass, ToolRiskLevel
from pet_harness.ui.character_ui_service import CharacterUiService
from pet_harness.voice_runtime_status_adapter import VoiceRuntimeStatusAdapter
from ui.background_resolver import BackgroundResolver


SAFE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

SKILL_STATE_KEY = "ui_skill_states"
CHARACTER_SKILL_ENABLED_KEY = "character_skill_enabled"
LAST_XP_KEY = "ui_last_xp_delta"
TOOL_ENABLED_KEY = "enabled_overrides"
TOOL_CONFIGS_KEY = "metadata_configs"
DEFAULT_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
# 換模型的位置:先用本地已 pull 的 gemma4:e2b 驗證 Ollama 串接,之後穩定再換 gemma3 等模型。
# 可用 OLLAMA_MODEL 環境變數覆寫,不需改這裡。
DEFAULT_OLLAMA_MODEL = "gemma4:e2b"
LOGGER = logging.getLogger(__name__)


class PyQtHarnessAdapter:
    """PyQt 應用與 PetHarnessEngine 的適配器。

    主要功能：
    - 管理角色的引擎、provider 配置、skills 狀態
    - 暴露 provider 運行時狀態（get_provider_status）
    - 支持 per-character skills CRUD（set_skill_enabled）
    - 與 SQLiteStore 同步狀態
    - 通過 QWebChannel bridge 與前端通訊
    """
    def __init__(
        self,
        agentic_root: str | Path = Path(".agentic"),
        default_character_id: str = "Choppr",
        background_resolver: BackgroundResolver | None = None,
        voice_status_adapter: VoiceRuntimeStatusAdapter | None = None,
        brain_mode: str = "harness",
        runtime_contract: dict[str, Any] | None = None,
        provider_runtime: ProviderRuntime | None = None,
    ) -> None:
        self.agentic_root = Path(agentic_root)
        self.skills_root = self.agentic_root / "skills"
        self.user_skills_root = self.skills_root / "user"
        self._project_root = self.agentic_root.parent
        self._project_env = self._load_project_env()
        # composition root:一個 application session 只有一個 ProviderRuntime,
        # 角色切換共用它;先遷移舊角色 DB 內的 provider 設定再啟動 router。
        self.provider_runtime = provider_runtime or ProviderRuntime()
        migration = migrate_legacy_provider_config(self.provider_runtime)
        if migration.get("migrated_from"):
            print(f"[HARNESS] provider config migrated from {migration['migrated_from']}")
        self._character_registry = CharacterRegistry()
        self.router = CharacterRouter(
            registry=self._character_registry,
            agentic_root=str(self.agentic_root),
            provider_runtime=self.provider_runtime,
        )
        self._bootstrap_primary_provider()
        self.router.switch_character(default_character_id)
        self.character_service = CharacterUiService(router=self.router, registry=self._character_registry)
        self._brain_mode = str(brain_mode or "harness")
        self._background_resolver = background_resolver or BackgroundResolver(project_root=self._project_root)
        self._voice_status_adapter = voice_status_adapter or VoiceRuntimeStatusAdapter()
        _default_contract = {"brain_mode": "harness", "harness_runtime_available": True, "live_runtime_available": False, "openclaw_enabled": False}
        self._runtime_contract = dict(runtime_contract or _default_contract)
        self._last_skill_discovery_log: tuple[int, int, int] | None = None
        self._refresh_runtime()
        self._log_active_character_diagnostics()

    def _log_active_character_diagnostics(self) -> None:
        profile = self.router.get_active_character()
        engine = self.router.get_active_engine()
        print(f"[HARNESS] active_character: {profile.character_id if profile else None}")
        print(f"[HARNESS] engine._character_id: {engine._character_id if engine else None}")
        print(f"[HARNESS] sqlite_path: {profile.sqlite_path if profile else None}")
        skill_names = ", ".join(skill.name for skill in engine.skills) if engine else ""
        print(f"[HARNESS] skills loaded: {skill_names}")

    @property
    def engine(self) -> PetHarnessEngine:
        return self.router.get_active_engine()

    @property
    def store(self) -> SQLiteStore:
        return self.engine.store

    def shutdown(self) -> None:
        from pet_harness.tools.youtube_music_tool import shutdown_default_runtime

        shutdown_default_runtime()

    def configure_runtime_context(
        self,
        *,
        brain_mode: str | None = None,
        background_resolver: BackgroundResolver | None = None,
        voice_status_adapter: VoiceRuntimeStatusAdapter | None = None,
        runtime_contract: dict[str, Any] | None = None,
    ) -> None:
        if brain_mode is not None:
            self._brain_mode = str(brain_mode)
        if background_resolver is not None:
            self._background_resolver = background_resolver
        if voice_status_adapter is not None:
            self._voice_status_adapter = voice_status_adapter
        if runtime_contract is not None:
            self._runtime_contract = dict(runtime_contract)
        else:
            self._runtime_contract = {"brain_mode": "harness", "harness_runtime_available": True, "live_runtime_available": False, "openclaw_enabled": False}

    def handle_text_input(self, text: str) -> dict[str, Any]:
        """文字提交只接受 text;Provider 選擇是 application 層設定,
        不可由訊息參數覆寫(要換 Provider 走 configure_provider)。"""
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError("text input cannot be empty")
        self._refresh_runtime()
        if self.engine.router.match(cleaned, self.engine._active_capabilities()) is None:
            self.engine.refresh_semantic_index()
        previous_progress = self.store.get_user_progress()
        event = self.router.dispatch_event({"text": cleaned, "source": "pyqt_ui"})
        self.store.set_setting(LAST_XP_KEY, event.xp_delta)
        payload = self._serialize_pet_event(event, previous_progress=previous_progress)
        payload["user_text"] = cleaned
        return payload

    def get_current_state(self) -> dict[str, Any]:
        # ponytail: 讀取路徑不重建 runtime;handle_text_input 已在同一輪互動的
        # worker thread 內重建過,UI 執行緒上再重建一次是每輪對話卡頓的主因。
        state = self.engine.state_snapshot()
        latest_event = self._load_latest_snapshot()
        skills = self.list_skills()
        tools = self.list_tools()
        runtime_config = self.provider_runtime.get_config()
        provider_config = runtime_config.to_dict() if runtime_config else {}
        provider_status = self.provider_runtime.get_status().to_dict()
        xp_state = self._build_xp_state(
            state.get("user_progress") or self.store.get_user_progress(),
            self._last_xp_delta(),
        )
        background = self._build_background_status()
        voice = self._build_voice_status()
        diagnostics = self._build_diagnostics(
            state=state,
            latest_event=latest_event,
            skills=skills,
            tools=tools,
            xp_state=xp_state,
            background=background,
            voice=voice,
            provider_config=provider_config,
            provider_status=provider_status,
        )
        return {
            "xp": xp_state,
            "provider_config": self._mask_payload(provider_config),
            "provider_status": self._mask_payload(provider_status),
            "provider_diagnostics": self._build_provider_diagnostics(provider_config, provider_status),
            "background": background,
            "voice": voice,
            "diagnostics": diagnostics,
            "skill_count": len(skills),
            "tool_count": len(tools),
            "latest_event": latest_event,
            "warnings": list((latest_event or {}).get("warnings") or []),
        }

    def get_provider_status(self) -> dict[str, Any]:
        """全域 ProviderRuntime 狀態(角色切換前後一致),附 active character id 供 UI 對照。"""
        snapshot = self.router.get_active_snapshot()
        return {
            "ai": self._build_ai_provider_status(),
            "tts": self._build_tts_provider_status(),
            "stt": self._build_stt_provider_status(),
            "active_character_id": snapshot.character_id if snapshot else None,
        }

    def _build_ai_provider_status(self) -> dict[str, Any]:
        """AI provider 狀態一律來自全域 runtime,不讀角色 store,不含 secret。"""
        payload = self.provider_runtime.status_payload()
        return {
            "provider": payload.get("selected_provider") or "none",
            "model_name": payload.get("model_name"),
            "status": "ready" if payload.get("healthy") else (payload.get("error_category") or "unavailable"),
            "healthy": bool(payload.get("healthy")),
            "message": payload.get("message"),
            "api_key_available": payload.get("api_key_status") == "configured",
        }

    def _build_tts_provider_status(self) -> dict[str, Any]:
        """构建 TTS provider 状态."""
        voice_status = self._voice_status_adapter.get_status()
        resolved_mode, resolution_reason = config.resolve_tts_runtime_mode()

        return {
            "resolved_mode": resolved_mode,
            "resolution_reason": resolution_reason,
            "requested_mode": "voai_first",
            "attempted_providers": [],
            "selected_provider": None,
            "fallback_reason": None,
            "outcome": None,
            "status": voice_status.tts_primary_status,
        }

    def _build_stt_provider_status(self) -> dict[str, Any]:
        """构建 STT provider 状态."""
        voice_status = self._voice_status_adapter.get_status()
        provider = "none" if voice_status.stt_status == "configured_missing_runtime" else "faster_whisper"

        return {
            "provider": provider,
            "status": voice_status.stt_status,
        }

    def list_skills(self) -> list[dict[str, Any]]:
        active_char = self.engine.character_profile
        char_skill_names = set(active_char.allowed_skill_refs) if active_char else set()
        enabled_map = self._character_skill_enabled_map(char_skill_names)
        items: list[dict[str, Any]] = []
        for skill in self.engine.discoverable_skills():
            path = Path(skill.file_path or "")
            items.append(
                {
                    "name": skill.name,
                    "skill_id": skill.name,
                    "display_name": skill.display_name or skill.name,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "default_behavior": skill.behavior,
                    "required_tool": skill.required_tool,
                    "current_skill_xp": self.store.get_skill_progress(skill.name).get("xp_total", 0),
                    "enabled": bool(enabled_map.get(skill.name, True)),
                    "enabled_in_character": skill.name in char_skill_names,
                    "allowed_for_character": skill.name in char_skill_names if active_char else True,
                    "permitted": skill.name in char_skill_names if active_char else True,
                    "priority": skill.priority,
                    "capability": skill.capability,
                    "has_tool_policy": bool(skill.tool_policy),
                    "validation_status": "valid",
                    "is_builtin": not self._is_user_skill_path(path),
                    "file_path": str(path) if skill.file_path else None,
                }
            )
        return items

    def list_tools(self) -> list[dict[str, Any]]:
        registry = self._build_registry()
        items: list[dict[str, Any]] = []
        builtin_names = {tool.name for tool in ToolRegistry().list_definitions()}
        for definition in registry.list_definitions():
            items.append(
                {
                    "tool_name": definition.name,
                    "description": definition.description,
                    "enabled": definition.enabled,
                    "risk_level": definition.risk_level.value,
                    "permission_requirement": definition.execution_class.value,
                    "status": "registered" if registry.has_executor(definition.name) else "configured_but_unimplemented",
                    "has_executor": registry.has_executor(definition.name),
                    "is_builtin": definition.name in builtin_names,
                    "metadata": dict(definition.metadata or {}),
                }
            )
        return items

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        self._validate_safe_id(skill_id, field_name="skill_id")
        active_char = self.router.get_active_character()
        if active_char:
            authorized = set(active_char.allowed_skill_refs)
            if skill_id not in authorized:
                raise ValueError(f"skill is not authorized for active character: {skill_id}")
            overlay = self._character_skill_enabled_map(authorized)
            overlay[skill_id] = bool(enabled)
            self.store.set_setting(CHARACTER_SKILL_ENABLED_KEY, overlay)
            self._refresh_runtime()
            return {"skill_id": skill_id, "enabled": bool(enabled), "permitted": True}

        all_skill_ids = {skill.name for skill in self._load_all_skills()}
        if skill_id not in all_skill_ids:
            raise ValueError(f"unknown skill_id: {skill_id}")

        disabled = self._skill_disabled_map()
        if enabled:
            disabled.pop(skill_id, None)
        else:
            disabled[skill_id] = True
        self.store.set_setting(SKILL_STATE_KEY, disabled)
        self._refresh_runtime()
        return {"skill_id": skill_id, "enabled": enabled}

    def set_tool_enabled(self, tool_name: str, enabled: bool) -> dict[str, Any]:
        self._validate_safe_id(tool_name, field_name="tool_name")
        names = {item["tool_name"] for item in self.list_tools()}
        if tool_name not in names:
            raise ValueError(f"unknown tool_name: {tool_name}")
        overrides = self._tool_enabled_overrides()
        overrides[tool_name] = bool(enabled)
        self.store.set_tool_setting(TOOL_ENABLED_KEY, overrides)
        self._refresh_runtime()
        return {"tool_name": tool_name, "enabled": bool(enabled)}

    def add_skill(self, skill_payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_skill_payload(skill_payload)
        self.user_skills_root.mkdir(parents=True, exist_ok=True)
        target = self._user_skill_path(normalized["skill_id"])
        target.write_text(
            "\n".join(
                [
                    f"name: {normalized['skill_id']}",
                    f"display_name: {normalized['display_name']}",
                    f"description: {normalized['description']}",
                    f"trigger: {', '.join(normalized['triggers'])}",
                    f"behavior: {normalized['default_behavior']}",
                    "xp_reward: 0",
                    f"required_tool: {normalized['required_tool']}" if normalized["required_tool"] else "",
                ]
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        self._refresh_runtime()
        return {
            "skill_id": normalized["skill_id"],
            "display_name": normalized["display_name"],
            "file_path": str(target),
            "enabled": True,
        }

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        self._validate_safe_id(skill_id, field_name="skill_id")
        for item in self.list_skills():
            if item["skill_id"] != skill_id:
                continue
            path = Path(item["file_path"]) if item["file_path"] else None
            if item["is_builtin"]:
                self.set_skill_enabled(skill_id, False)
                return {"skill_id": skill_id, "deleted": False, "disabled": True}
            if path is None or not path.exists() or not self._is_user_skill_path(path):
                raise ValueError(f"unsafe skill path for deletion: {skill_id}")
            path.unlink()
            disabled = self._skill_disabled_map()
            disabled.pop(skill_id, None)
            self.store.set_setting(SKILL_STATE_KEY, disabled)
            self._refresh_runtime()
            return {"skill_id": skill_id, "deleted": True}
        raise ValueError(f"unknown skill_id: {skill_id}")

    def add_tool_config(self, tool_payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_tool_payload(tool_payload)
        configs = self._tool_configs()
        configs[normalized["tool_name"]] = normalized
        self.store.set_tool_setting(TOOL_CONFIGS_KEY, configs)
        overrides = self._tool_enabled_overrides()
        overrides[normalized["tool_name"]] = normalized["enabled"]
        self.store.set_tool_setting(TOOL_ENABLED_KEY, overrides)
        self._refresh_runtime()
        return {"tool_name": normalized["tool_name"], "enabled": normalized["enabled"]}

    def delete_tool_config(self, tool_name: str) -> dict[str, Any]:
        self._validate_safe_id(tool_name, field_name="tool_name")
        configs = self._tool_configs()
        if tool_name in configs:
            configs.pop(tool_name, None)
            self.store.set_tool_setting(TOOL_CONFIGS_KEY, configs)
            overrides = self._tool_enabled_overrides()
            overrides.pop(tool_name, None)
            self.store.set_tool_setting(TOOL_ENABLED_KEY, overrides)
            self._refresh_runtime()
            return {"tool_name": tool_name, "deleted": True}
        if tool_name in {item["tool_name"] for item in self.list_tools()}:
            self.set_tool_enabled(tool_name, False)
            return {"tool_name": tool_name, "deleted": False, "disabled": True}
        raise ValueError(f"unknown tool_name: {tool_name}")

    def _refresh_runtime(self) -> None:
        # 先熱重載 profile+personal,再重建 skills/router:persona、alias、local skill
        # 的任何修改(面板或手動編輯)都在下一次互動生效,不依賴 switch_character。
        self.engine.reload_profile()
        self.engine.refresh_skill_catalog()
        resolved = self.engine.filter_skills_for_character(self.engine.available_skills)
        self.engine.skills = resolved.resolved_skills
        self.engine.skip_diagnostics = resolved.skip_diagnostics
        self.engine.store.sync_skills(self.engine.skills)
        self.engine.rebuild_router()
        self.engine.refresh_tool_registry(self._build_registry())
        signature = (len(self.engine.available_skills), len(self.engine.discoverable_skills()), len(self.engine.skills))
        if signature != self._last_skill_discovery_log:
            LOGGER.info("[SKILL DISCOVERY] loaded=%s allowed=%s enabled=%s", *signature)
            self._last_skill_discovery_log = signature

    def _load_all_skills(self) -> list[Skill]:
        return list(self.engine.available_skills)

    def _character_skill_enabled_map(self, authorized_skill_ids: set[str] | list[str]) -> dict[str, bool]:
        """讀取 active character 的私有 enablement overlay，並補齊首次預設值。"""
        authorized = {str(skill_id) for skill_id in authorized_skill_ids}
        current = self.store.get_setting(CHARACTER_SKILL_ENABLED_KEY, None)
        overlay = dict(current) if isinstance(current, dict) else {}
        changed = not isinstance(current, dict)
        for skill_id in authorized:
            if skill_id not in overlay:
                overlay[skill_id] = True
                changed = True
        if changed:
            self.store.set_setting(CHARACTER_SKILL_ENABLED_KEY, overlay)
        return {skill_id: bool(value) for skill_id, value in overlay.items()}

    def build_provider_config(self, provider: str) -> ProviderConfig:
        provider_type = ProviderType(str(provider))
        if provider_type is ProviderType.API:
            api_key_env_var = self._select_primary_api_key_env_var()
            return ProviderConfig(
                provider_type=provider_type,
                base_url=self._project_env.get("ECHOES_API_BASE_URL")
                or self._project_env.get("OPENAI_BASE_URL")
                or DEFAULT_OPENAI_CHAT_COMPLETIONS_URL,
                model_name=self._project_env.get("ECHOES_API_MODEL")
                or self._project_env.get("OPENAI_MODEL")
                or "gpt-4o-mini",
                api_key_env_var=api_key_env_var,
                routing_fallback_enabled=config.PROVIDER_ROUTING_FALLBACK_ENABLED,
                routing_confidence_threshold=config.PROVIDER_ROUTING_CONFIDENCE_THRESHOLD,
            )
        return ProviderConfig(
            provider_type=ProviderType.OLLAMA,
            base_url=self._project_env.get("OLLAMA_BASE_URL") or "http://localhost:11434",
            model_name=self._project_env.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
            api_key_env_var=None,
            # ponytail: 本機首次推論常需冷啟動載入模型,實測 15s 預設會逾時;
            # 60s 對本地生成足夠,雲端 API 逾時不受影響。
            timeout_seconds=60.0,
            routing_fallback_enabled=config.PROVIDER_ROUTING_FALLBACK_ENABLED,
            routing_confidence_threshold=config.PROVIDER_ROUTING_CONFIDENCE_THRESHOLD,
        )

    def configure_provider(self, provider: str) -> dict[str, Any]:
        """受控的 settings 入口:設定全域 Provider(api/ollama),回傳 secret-safe 狀態。"""
        status = self.provider_runtime.configure(self.build_provider_config(provider))
        return self._mask_payload(status.to_dict())

    def _bootstrap_primary_provider(self) -> None:
        """未設定全域 Provider 時預設啟用本地 Ollama(local-first),不再依環境是否
        有 API key 決定;要改用雲端 API 一律經 configure_provider("api") 明確切換。
        已有持久化設定時只 refresh 健康狀態,不覆寫選擇。
        測試注入的 provider_runtime 不得被自動配置覆寫。"""
        if self.provider_runtime.is_test_injected:
            return
        if self.provider_runtime.get_config() is not None:
            self.provider_runtime.refresh_status()
            return
        self.provider_runtime.configure(self.build_provider_config("ollama"))

    def _build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        enabled_overrides = self._tool_enabled_overrides()
        for definition in registry.list_definitions():
            if definition.name in enabled_overrides:
                definition.enabled = bool(enabled_overrides[definition.name])

        for tool_name, payload in self._tool_configs().items():
            definition = ToolDefinition(
                name=tool_name,
                description=payload["description"],
                risk_level=ToolRiskLevel(payload["risk_level"]),
                execution_class=ToolExecutionClass.INTERNAL,
                enabled=bool(enabled_overrides.get(tool_name, payload["enabled"])),
                xp_reward=0,
                metadata={"source": "ui_metadata_only"},
            )
            registry.register_definition(definition, executor=None)
        return registry

    def _serialize_pet_event(
        self,
        event: PetEvent,
        previous_progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = event.to_dict()
        tool_result = dict(payload.get("metadata", {}).get("tool_result") or {})
        asset_result = dict(payload.get("metadata", {}).get("asset_result") or {})
        warnings = list(payload.get("metadata", {}).get("agentic", {}).get("prompt_warnings") or [])
        if payload.get("provider_status", {}).get("healthy") is False:
            warnings.append(payload["provider_status"].get("message") or "provider fallback active")
        reward_summary = [item.get("reward_id") for item in payload.get("reward_events") or [] if isinstance(item, dict)]
        tool_request = payload.get("tool_request") or {}
        tool_payload = {
            "name": tool_result.get("tool_name") or tool_request.get("tool_name"),
            "status": tool_result.get("status") or tool_request.get("status"),
            "reason": (tool_result.get("error") or {}).get("reason"),
        }
        return {
            "reply": payload["reply"],
            "matched_skill": payload.get("matched_skill"),
            "tool": tool_payload,
            "xp_delta": payload["xp_delta"],
            "reward_summary": reward_summary,
            "asset_summary": {
                "status": asset_result.get("status"),
                "asset_id": asset_result.get("asset_id"),
                "webm_key": asset_result.get("webm_key"),
            },
            "behavior_id": payload.get("behavior_id"),
            "webm_key": payload.get("webm_key"),
            "provider_status": self._mask_payload(payload.get("provider_status")),
            "saved_to_db": bool(payload.get("saved_to_db")),
            "warnings": warnings,
            "raw_event": self._mask_payload(payload),
            "xp_display": self._build_xp_state(
                self.store.get_user_progress(),
                payload["xp_delta"],
                previous_progress=previous_progress,
            ),
        }

    def _build_xp_state(
        self,
        progress: dict[str, Any],
        last_delta: int,
        previous_progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        xp_total = int(progress.get("xp_total", 0))
        level = self._level_for_xp(xp_total)
        current_level_min_xp = self._level_min_xp(level)
        next_level_xp = self._next_level_xp(level)
        span = max(1, next_level_xp - current_level_min_xp)
        progress_to_next_level = min(1.0, max(0.0, (xp_total - current_level_min_xp) / span))
        xp_to_next_level = max(0, next_level_xp - xp_total)
        level_up_event = None
        if previous_progress is not None:
            previous_level = self._level_for_xp(int(previous_progress.get("xp_total", 0)))
            if level > previous_level:
                level_up_event = {
                    "from_level": previous_level,
                    "to_level": level,
                    "threshold": current_level_min_xp,
                }
        last_text = f"Last +{last_delta}" if last_delta >= 0 else f"Last {last_delta}"
        display = f"XP: {xp_total} | Lv. {level} | {last_text} | {xp_total} / {next_level_xp} XP"
        return {
            "bond_xp": xp_total,
            "xp_total": xp_total,
            "level": level,
            "last_delta": last_delta,
            "current_level_min_xp": current_level_min_xp,
            "next_level_xp": next_level_xp,
            "progress_to_next_level": progress_to_next_level,
            "progress_percent": round(progress_to_next_level * 100),
            "xp_to_next_level": xp_to_next_level,
            "level_up_event": level_up_event,
            "display": display,
        }

    def _level_for_xp(self, xp_total: int) -> int:
        return max(1, (max(0, xp_total) // 100) + 1)

    def _level_min_xp(self, level: int) -> int:
        return max(0, (level - 1) * 100)

    def _next_level_xp(self, level: int) -> int:
        return max(1, level) * 100

    def _build_background_status(self) -> dict[str, Any]:
        diagnostics = self._background_resolver.diagnostics()
        if not diagnostics.get("background_url"):
            self._background_resolver.resolve()
            diagnostics = self._background_resolver.diagnostics()
        return {
            "status": diagnostics.get("background_status", "fallback_placeholder"),
            "source": diagnostics.get("background_url") or "css:room-placeholder",
            "message": diagnostics.get("reason") or "background diagnostics unavailable",
        }

    def _build_voice_status(self) -> dict[str, Any]:
        dto = self._voice_status_adapter.get_status()
        stt_provider = "none" if dto.stt_status == "configured_missing_runtime" else "faster_whisper"
        return {
            "stt": {
                "provider": stt_provider,
                "status": dto.stt_status,
                "configured": dto.stt_status != "configured_missing_runtime",
                "implemented": dto.stt_status != "configured_missing_runtime",
                "required_env": ["STT_ENABLED", "STT_MODEL", "STT_DEVICE"],
                "message": dto.stt_status,
            },
            "tts": {
                "provider": "adaptive",
                "status": dto.tts_primary_status,
                "configured": dto.tts_primary_status != "configured_missing_runtime",
                "implemented": dto.tts_primary_status != "configured_missing_runtime",
                "required_env": ["ELEVENLABS_API_KEY", "ELEVENLABS_*_VOICE_ID", "ELEVENLABS_MODEL_ID"],
                "message": dto.tts_primary_status,
            },
            "tts_fallback": {
                "provider": "elevenlabs",
                "status": dto.tts_fallback_status,
                "message": dto.tts_fallback_status,
            },
            "audio_worker": {
                "status": dto.audio_worker_status,
                "message": dto.audio_worker_status,
            },
            "overall_status": dto.overall_status,
            "last_voice_error": dto.last_voice_error,
        }

    def _build_provider_diagnostics(
        self,
        provider_config: dict[str, Any],
        provider_status: dict[str, Any],
    ) -> dict[str, Any]:
        selected = str(provider_config.get("provider_type") or "none")
        resolved = str(provider_status.get("provider_type") or selected)
        api_key_env_var = provider_config.get("api_key_env_var") or "OPENAI_API_KEY"
        key_available = bool(self._project_env.get(str(api_key_env_var)) or os.environ.get(str(api_key_env_var)))
        if selected == "api" and key_available and provider_status.get("healthy") is not False:
            api_config_status = "api configured"
        elif selected == "api" and not key_available:
            api_config_status = "api missing key"
        elif selected == "api":
            api_config_status = f"api fallback {resolved}"
        else:
            api_config_status = f"{selected} selected"
        return self._mask_payload(
            {
                "provider_selected": selected,
                "provider_resolved": resolved,
                "provider_status": provider_status,
                "api_config_status": api_config_status,
                "api_key_env_var": api_key_env_var,
                "api_key_status": "configured" if key_available else "missing",
                "model_name": provider_config.get("model_name"),
            }
        )

    def _build_diagnostics(
        self,
        state: dict[str, Any],
        latest_event: dict[str, Any] | None,
        skills: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        xp_state: dict[str, Any],
        background: dict[str, Any],
        voice: dict[str, Any],
        provider_config: dict[str, Any],
        provider_status: dict[str, Any],
    ) -> dict[str, Any]:
        latest_event = latest_event or {}
        latest_tool = latest_event.get("tool") or latest_event.get("tool_request") or {}
        provider_diagnostics = self._build_provider_diagnostics(provider_config, provider_status)
        runtime_section = {
            "brain_mode": self._runtime_contract["brain_mode"],
            "live_runtime_available": self._runtime_contract["live_runtime_available"],
            "harness_runtime_available": self._runtime_contract["harness_runtime_available"],
            "openclaw_enabled": self._runtime_contract["openclaw_enabled"],
        }
        ui_section = {
            "bridge_ready": True,
            "stage_size": "pending",
            "stage_scale": "pending",
            "pet_anchor_x": "50%",
            "pet_anchor_y": "2%",
            "pet_scale": "1",
            "background_status": background["status"],
            "idle_motion_candidates_count": 0,
        }
        voice_section = {
            "stt_status": voice["stt"]["status"],
            "tts_primary_status": voice["tts"]["status"],
            "tts_fallback_status": (voice.get("tts_fallback") or {}).get("status"),
            "audio_worker_status": (voice.get("audio_worker") or {}).get("status"),
            "last_voice_error": voice.get("last_voice_error", ""),
        }
        harness_section = {
            "provider_selected": provider_diagnostics["provider_selected"],
            "skill_count": len(skills),
            "tool_count": len(tools),
            "matched_skill": latest_event.get("matched_skill"),
            "tool_result": latest_tool.get("status"),
            "xp_level": xp_state["level"],
            "reward_asset": latest_event.get("webm_key"),
        }
        security_section = self._build_security_summary()
        return self._mask_payload(
            {
                "bridge_status": "ready",
                "last_action": latest_event.get("source_event_id") or "none",
                "last_error": None,
                "brain_mode": runtime_section["brain_mode"],
                "provider_selected": provider_diagnostics["provider_selected"],
                "provider_resolved": provider_diagnostics["provider_resolved"],
                "provider_status": provider_diagnostics["provider_status"],
                "api_config_status": provider_diagnostics["api_config_status"],
                "skill_count": len(skills),
                "selected_skill": None,
                "matched_skill": latest_event.get("matched_skill"),
                "tool_count": len(tools),
                "selected_tool": latest_tool.get("tool_name") or latest_tool.get("name"),
                "tool_status": latest_tool.get("status"),
                "xp_total": xp_state["xp_total"],
                "level": xp_state["level"],
                "next_level_xp": xp_state["next_level_xp"],
                "reward_count": len(state.get("reward_unlocks") or []),
                "asset_manifest_count": len(state.get("asset_manifest") or []),
                "behavior_id": latest_event.get("behavior_id") or state.get("behavior_state"),
                "webm_key": latest_event.get("webm_key"),
                "background_status": background["status"],
                "voice_stt_status": voice["stt"]["status"],
                "voice_tts_status": voice["tts"]["status"],
                "runtime": runtime_section,
                "ui": ui_section,
                "voice": voice_section,
                "harness": harness_section,
                "security": security_section,
            }
        )

    def _build_security_summary(self) -> dict[str, str]:
        keys = (
            "OPENAI_API_KEY",
            "CHATGPT_API_KEY",
            "AZURE_STT_API_KEY",
            "AZURE_STT_REGION",
            "ELEVENLABS_API_KEY",
            "ELEVENLABS_MODEL_ID",
        )
        summary: dict[str, str] = {}
        for key in keys:
            configured = bool(self._project_env.get(key) or os.environ.get(key))
            summary[key] = "[configured]" if configured else "[missing]"
        return summary

    def _load_latest_snapshot(self) -> dict[str, Any] | None:
        if not self.engine.snapshot_path.exists():
            return None
        return self._json_safe(json.loads(self.engine.snapshot_path.read_text(encoding="utf-8")))

    def _last_xp_delta(self) -> int:
        return int(self.store.get_setting(LAST_XP_KEY, 0) or 0)

    def _skill_disabled_map(self) -> dict[str, bool]:
        return dict(self.store.get_setting(SKILL_STATE_KEY, {}) or {})

    def _tool_enabled_overrides(self) -> dict[str, bool]:
        return dict(self.store.get_tool_setting(TOOL_ENABLED_KEY, {}) or {})

    def _tool_configs(self) -> dict[str, dict[str, Any]]:
        return dict(self.store.get_tool_setting(TOOL_CONFIGS_KEY, {}) or {})

    def _normalize_skill_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        skill_id = str(payload.get("skill_id") or "").strip()
        display_name = str(payload.get("display_name") or "").strip()
        description = str(payload.get("description") or "").strip()
        default_behavior = str(payload.get("default_behavior") or "").strip()
        required_tool = str(payload.get("required_tool") or "").strip() or None
        triggers = payload.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [item.strip() for item in triggers.split(",") if item.strip()]
        triggers = [str(item).strip().lower() for item in triggers if str(item).strip()]
        self._validate_safe_id(skill_id, field_name="skill_id")
        if not display_name or not description or not default_behavior or not triggers:
            raise ValueError("skill payload is missing required fields")
        if required_tool is not None:
            self._validate_safe_id(required_tool, field_name="required_tool")
        return {
            "skill_id": skill_id,
            "display_name": display_name,
            "description": description,
            "triggers": triggers,
            "default_behavior": default_behavior,
            "required_tool": required_tool,
        }

    def _normalize_tool_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(payload.get("tool_name") or "").strip()
        description = str(payload.get("description") or "").strip()
        risk_level = str(payload.get("risk_level") or "low").strip().lower()
        enabled = bool(payload.get("enabled", True))
        self._validate_safe_id(tool_name, field_name="tool_name")
        if not description:
            raise ValueError("tool config requires a description")
        if risk_level not in {item.value for item in ToolRiskLevel}:
            raise ValueError(f"invalid risk_level: {risk_level}")
        return {
            "tool_name": tool_name,
            "description": description,
            "risk_level": risk_level,
            "enabled": enabled,
        }

    def _user_skill_path(self, skill_id: str) -> Path:
        path = self.user_skills_root / f"{skill_id}.md"
        resolved = path.resolve()
        base = self.user_skills_root.resolve()
        if base not in resolved.parents and resolved != base:
            raise ValueError("resolved user skill path escapes managed directory")
        return path

    def _is_user_skill_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            base = self.user_skills_root.resolve()
            return resolved == base or base in resolved.parents
        except FileNotFoundError:
            return False

    def _validate_safe_id(self, value: str, field_name: str) -> None:
        if not SAFE_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid {field_name}: {value}")

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        return value

    def _mask_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            masked: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                key_lower = key_text.lower()
                if key_lower.endswith("_env_var") or key_lower == "required_env":
                    masked[key_text] = self._mask_payload(item)
                elif any(token in key_lower for token in ("secret", "token", "api_key", "authorization")):
                    masked[key_text] = "***" if item else item
                else:
                    masked[key_text] = self._mask_payload(item)
            return masked
        if isinstance(value, list):
            return [self._mask_payload(item) for item in value]
        if isinstance(value, str):
            return self._mask_text(value)
        return value

    def _mask_text(self, text: str) -> str:
        masked = text
        for key, value in self._project_env.items():
            if not value or len(value) < 8:
                continue
            if any(token in key.lower() for token in ("key", "token", "secret")):
                masked = masked.replace(value, "***")
        return masked

    def _load_project_env(self) -> dict[str, str]:
        env_path = self._project_root / ".env"
        loaded: dict[str, str] = {}
        if not env_path.exists():
            return loaded

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            normalized_value = value.strip()
            if (
                len(normalized_value) >= 2
                and normalized_value[0] == normalized_value[-1]
                and normalized_value[0] in {"'", '"'}
            ):
                normalized_value = normalized_value[1:-1]
            normalized_value = re.sub(
                r"\$\{([^}]+)\}",
                lambda match: loaded.get(match.group(1), os.environ.get(match.group(1), "")),
                normalized_value,
            )
            loaded[normalized_key] = normalized_value
            os.environ.setdefault(normalized_key, normalized_value)
        return loaded

    def _select_existing_api_key_env_var(self) -> str | None:
        for key in ("ECHOES_API_KEY", "OPENAI_API_KEY", "CHATGPT_API_KEY"):
            if self._project_env.get(key) or os.environ.get(key):
                return key
        return None

    def _select_primary_api_key_env_var(self) -> str | None:
        return self._select_existing_api_key_env_var() or "OPENAI_API_KEY"
