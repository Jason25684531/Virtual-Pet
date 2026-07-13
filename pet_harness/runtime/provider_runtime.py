"""Application 層唯一的 AI Provider runtime。

一個 application session 建立一個 ProviderRuntime,由 composition root 注入
router/engine;角色切換不得重建或改寫 Provider 選擇。設定持久化於
`data/runtime/provider_config.json`(僅非機密欄位),secret 一律由環境變數取得。
Provider 不可用時 fail-closed:回傳結構化 unavailable,不偽造回覆。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from pet_harness.agent.provider_adapter import LLMProviderAdapter, ProviderReply
from pet_harness.agent.provider_factory import create_provider
from pet_harness.models.events import UserEvent
from pet_harness.models.provider import ProviderConfig, ProviderStatus, ProviderType
from pet_harness.models.skill import Skill

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("data") / "runtime" / "provider_config.json"


class UnavailableProvider:
    """fail-closed adapter:未設定或不可用時回傳安全 unavailable 結果。"""

    def __init__(self, message: str, error_category: str, provider_type: ProviderType | None = None) -> None:
        self.message = message
        self.error_category = error_category
        self.provider_type = provider_type

    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        return ProviderReply(
            reply=f"AI provider unavailable: {self.message}",
            provider_status=ProviderStatus(
                provider_type=self.provider_type,
                healthy=False,
                message=self.message,
                metadata={"error_category": self.error_category},
            ),
            prompt_text=prompt_text,
        )


class ProviderRuntime:
    """單一 session 的 Provider 設定、client 與健康狀態持有者。

    adapter replacement 是原子的:configure() 只在鎖內替換 _provider 引用,
    進行中的請求繼續使用它們已取得的 adapter,下一個請求才拿到新 adapter。
    """

    def __init__(
        self,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        request_fn=None,
        provider: LLMProviderAdapter | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._request_fn = request_fn
        self._lock = threading.Lock()
        if provider is not None:
            # 僅供測試注入 fake adapter;不觸碰磁碟設定,composition root 不得覆寫。
            self._config: ProviderConfig | None = None
            self._provider: LLMProviderAdapter = provider
            self._status = ProviderStatus(healthy=True, message="injected test provider")
            self._test_injected = True
            return
        self._test_injected = False
        self._config = self._load_config()
        self._provider, self._status = self._build(self._config)

    @property
    def is_test_injected(self) -> bool:
        """True 代表建構時注入了 fake adapter;composition root 的自動配置/遷移須略過。"""
        return self._test_injected

    def get_provider(self) -> LLMProviderAdapter:
        with self._lock:
            return self._provider

    def generate_reply(
        self,
        event: UserEvent,
        matched_skill: Skill | None = None,
        prompt_text: str | None = None,
    ) -> ProviderReply:
        """ProviderRuntime 本身滿足 LLMProviderAdapter 協定:每個請求在進入時
        取得當下 adapter;configure() 之後的下一個請求才使用新 adapter。"""
        return self.get_provider().generate_reply(
            event,
            matched_skill=matched_skill,
            prompt_text=prompt_text,
        )

    def get_status(self) -> ProviderStatus:
        with self._lock:
            return self._status

    def get_config(self) -> ProviderConfig | None:
        with self._lock:
            return self._config

    def configure(self, config: ProviderConfig) -> ProviderStatus:
        provider, status = self._build(config)
        with self._lock:
            self._config = config
            self._provider = provider
            self._status = status
        self._save_config(config)
        return status

    def refresh_status(self) -> ProviderStatus:
        """重建 provider 與健康狀態(例如 API key 補上後),不改變設定。"""
        with self._lock:
            config = self._config
        provider, status = self._build(config)
        with self._lock:
            if self._config is config:
                self._provider = provider
                self._status = status
        return status

    def status_payload(self) -> dict[str, Any]:
        """給 UI 的 secret-safe 全域狀態。"""
        with self._lock:
            config = self._config
            status = self._status
        payload = {
            "selected_provider": config.provider_type.value if config else None,
            "resolved_provider": status.provider_type.value if status.provider_type else None,
            "model_name": config.model_name if config else None,
            "healthy": status.healthy,
            "message": status.message,
            "checked_at": status.checked_at,
            "error_category": status.metadata.get("error_category"),
        }
        if config and config.provider_type is ProviderType.API:
            env_var = config.api_key_env_var or ""
            payload["api_key_env_var"] = env_var
            payload["api_key_status"] = "configured" if os.environ.get(env_var) else "missing"
        return payload

    # ── 內部 ────────────────────────────────────────────────

    def _build(self, config: ProviderConfig | None) -> tuple[LLMProviderAdapter, ProviderStatus]:
        if config is None:
            provider = UnavailableProvider(
                "no AI provider configured (choose api or ollama in settings)",
                "unconfigured",
            )
            return provider, ProviderStatus(
                provider_type=None,
                healthy=False,
                message=provider.message,
                metadata={"error_category": "unconfigured"},
            )

        if config.provider_type is ProviderType.API:
            env_var = config.api_key_env_var or ""
            if not env_var or not os.environ.get(env_var):
                message = f"missing API key environment variable: {env_var or '(unset)'}"
                provider = UnavailableProvider(message, "missing_api_key", ProviderType.API)
                return provider, ProviderStatus(
                    provider_type=ProviderType.API,
                    healthy=False,
                    message=message,
                    metadata={"error_category": "missing_api_key", "api_key_env_var": env_var},
                )
            provider = create_provider(config, request_fn=self._request_fn)
            return provider, ProviderStatus(
                provider_type=ProviderType.API,
                healthy=True,
                message="api provider ready",
                metadata={"model_name": config.model_name, "base_url": config.base_url},
            )

        # OLLAMA:啟動時做一次健康檢查,不健康仍保留 provider(端點恢復後即可用),
        # 但狀態如實回報 unhealthy,generate_reply 失敗時自身也會回傳 unavailable。
        provider = create_provider(config, request_fn=self._request_fn)
        status = provider.provider_status_from_health()
        return provider, status

    def _load_config(self) -> ProviderConfig | None:
        if not self._config_path.exists():
            return None
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
            return ProviderConfig.from_dict(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("invalid global provider config %s: %s", self._config_path, exc)
            return None

    def _save_config(self, config: ProviderConfig) -> None:
        payload = config.to_dict()
        # 只落地非機密欄位;api_key_env_var 是變數名,不是 secret 值。
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("failed to persist provider config: %s", exc)


def migrate_legacy_provider_config(
    runtime: ProviderRuntime,
    characters_data_dir: str | Path = Path("data") / "characters",
) -> dict[str, Any]:
    """一次性遷移:把角色 state.db 內的 provider_config 提升為全域設定。

    非破壞性:不刪除舊值(rollback 只需回復程式版本),之後角色 store 的
    provider 設定一律被忽略。回傳診斷資訊供啟動 log 檢視。
    """
    from pet_harness.storage.sqlite_store import SQLiteStore

    diagnostics: dict[str, Any] = {"migrated_from": None, "skipped": [], "already_configured": False}
    if runtime.get_config() is not None or runtime.is_test_injected:
        diagnostics["already_configured"] = True
        return diagnostics

    data_dir = Path(characters_data_dir)
    if not data_dir.exists():
        return diagnostics

    for db_path in sorted(data_dir.glob("*/state.db")):
        try:
            payload = SQLiteStore(db_path).get_setting("provider_config")
        except Exception as exc:  # noqa: BLE001 - 壞 DB 不能擋啟動
            diagnostics["skipped"].append({"db": str(db_path), "reason": str(exc)})
            continue
        if not payload:
            continue
        try:
            config = ProviderConfig.from_dict(payload)
        except ValueError as exc:
            # 舊 mock/low_spec 設定不再是合法產品模式,忽略之。
            diagnostics["skipped"].append({"db": str(db_path), "reason": str(exc)})
            continue
        runtime.configure(config)
        diagnostics["migrated_from"] = str(db_path)
        LOGGER.info("migrated provider config from %s to global runtime", db_path)
        break
    return diagnostics
