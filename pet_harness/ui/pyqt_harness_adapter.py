from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pet_harness.agent.provider_factory import create_provider
from pet_harness.engine.harness_engine import PetHarnessEngine
from pet_harness.models.events import PetEvent
from pet_harness.models.provider import ProviderConfig, ProviderType
from pet_harness.models.skill import Skill
from pet_harness.skills.skill_loader import SkillLoader
from pet_harness.skills.skill_router import SkillRouter
from pet_harness.storage.sqlite_store import SQLiteStore
from pet_harness.tools.registry import ToolRegistry
from pet_harness.tools.safety_guard import SafetyGuard
from pet_harness.tools.tool_models import ToolDefinition, ToolExecutionClass, ToolRiskLevel


SAFE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

SKILL_STATE_KEY = "ui_skill_states"
LAST_XP_KEY = "ui_last_xp_delta"
TOOL_ENABLED_KEY = "enabled_overrides"
TOOL_CONFIGS_KEY = "metadata_configs"
DEFAULT_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


class PyQtHarnessAdapter:
    def __init__(
        self,
        agentic_root: str | Path = Path(".agentic"),
        db_path: str | Path = Path("data") / "pet_state.db",
        snapshot_path: str | Path = Path("debug") / "events" / "latest_pet_event.json",
    ) -> None:
        self.agentic_root = Path(agentic_root)
        self.skills_root = self.agentic_root / "skills"
        self.user_skills_root = self.skills_root / "user"
        self.engine = PetHarnessEngine(
            agentic_root=self.agentic_root,
            db_path=Path(db_path),
            snapshot_path=Path(snapshot_path),
        )
        self.store: SQLiteStore = self.engine.store
        self._project_root = self.agentic_root.parent
        self._project_env = self._load_project_env()
        self._bootstrap_primary_provider()
        self._refresh_runtime()

    def handle_text_input(self, text: str, provider: str = "mock") -> dict[str, Any]:
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError("text input cannot be empty")
        self._refresh_runtime()
        self._set_provider(provider)
        previous_progress = self.store.get_user_progress()
        event = self.engine.handle_event({"text": cleaned, "source": "pyqt_ui"})
        self.store.set_setting(LAST_XP_KEY, event.xp_delta)
        return self._serialize_pet_event(event, previous_progress=previous_progress)

    def get_current_state(self) -> dict[str, Any]:
        self._refresh_runtime()
        state = self.engine.state_snapshot()
        latest_event = self._load_latest_snapshot()
        skills = self.list_skills()
        tools = self.list_tools()
        provider_config = self.store.get_provider_config().to_dict()
        provider_status = state.get("provider_status") or self.store.get_provider_status()
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

    def list_skills(self) -> list[dict[str, Any]]:
        disabled = self._skill_disabled_map()
        items: list[dict[str, Any]] = []
        for skill in self._load_all_skills():
            path = Path(skill.file_path or "")
            items.append(
                {
                    "skill_id": skill.name,
                    "display_name": skill.display_name or skill.name,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "default_behavior": skill.behavior,
                    "required_tool": skill.required_tool,
                    "current_skill_xp": self.store.get_skill_progress(skill.name).get("xp_total", 0),
                    "enabled": not disabled.get(skill.name, False),
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
        self.engine.skills = self._load_enabled_skills()
        self.engine.store.sync_skills(self._load_all_skills())
        self.engine.router = SkillRouter(self.engine.skills)
        self.engine.tool_registry = self._build_registry()
        self.engine.safety_guard = SafetyGuard(self.engine.tool_registry)

    def _load_all_skills(self) -> list[Skill]:
        return SkillLoader(self.skills_root).load_skills()

    def _load_enabled_skills(self) -> list[Skill]:
        disabled = self._skill_disabled_map()
        return [skill for skill in self._load_all_skills() if not disabled.get(skill.name, False)]

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
                fallback_provider=ProviderType.LOW_SPEC,
                routing_fallback_enabled=True,
            )
        return ProviderConfig(
            provider_type=provider_type,
            base_url=None,
            model_name=None,
            api_key_env_var=None,
            fallback_provider=ProviderType.LOW_SPEC,
            routing_fallback_enabled=False,
        )

    def _set_provider(self, provider: str) -> None:
        config = self.build_provider_config(provider)
        self.store.set_provider_config(config)
        self.engine.provider_config = config
        self.engine.provider = create_provider(config)

    def _bootstrap_primary_provider(self) -> None:
        current = self.store.get_provider_config()
        if current.provider_type is not ProviderType.MOCK:
            return
        if self._select_existing_api_key_env_var() is None:
            return
        config = self.build_provider_config("api")
        self.store.set_provider_config(config)
        self.engine.provider_config = config
        self.engine.provider = create_provider(config)

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
        source = "ui/assets/backgrounds/default-room.jpg"
        project_source = self._project_root / source
        if project_source.exists():
            return {
                "status": "loaded",
                "source": source,
                "message": "background loaded",
            }
        return {
            "status": "fallback",
            "source": "css:room-placeholder",
            "message": "configured background missing; using visible placeholder",
        }

    def _build_voice_status(self) -> dict[str, Any]:
        stt_configured = bool(self._project_env.get("AZURE_STT_API_KEY") and self._project_env.get("AZURE_STT_REGION"))
        tts_configured = bool(
            self._project_env.get("ELEVENLABS_API_KEY")
            and (
                self._project_env.get("ELEVENLABS_MIKU_VOICE_ID")
                or self._project_env.get("ELEVENLABS_CHOPPER_VOICE_ID")
                or self._project_env.get("ELEVENLABS_VOICE_ID")
            )
        )
        return {
            "stt": {
                "provider": "azure",
                "status": "configured_not_implemented" if stt_configured else "missing",
                "configured": stt_configured,
                "implemented": False,
                "required_env": ["AZURE_STT_API_KEY", "AZURE_STT_REGION"],
                "message": (
                    "STT configured but microphone capture not implemented"
                    if stt_configured
                    else "STT missing Azure configuration"
                ),
            },
            "tts": {
                "provider": "elevenlabs",
                "status": "configured_not_implemented" if tts_configured else "missing",
                "configured": tts_configured,
                "implemented": False,
                "required_env": ["ELEVENLABS_API_KEY", "ELEVENLABS_*_VOICE_ID", "ELEVENLABS_MODEL_ID"],
                "message": (
                    "TTS configured but playback/provider not implemented"
                    if tts_configured
                    else "TTS missing ElevenLabs configuration"
                ),
            },
        }

    def _build_provider_diagnostics(
        self,
        provider_config: dict[str, Any],
        provider_status: dict[str, Any],
    ) -> dict[str, Any]:
        selected = str(provider_config.get("provider_type") or "mock")
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
                "fallback_provider": provider_config.get("fallback_provider"),
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
        return self._mask_payload(
            {
                "bridge_status": "ready",
                "last_action": latest_event.get("source_event_id") or "none",
                "last_error": None,
                "brain_mode": "harness",
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
            }
        )

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
