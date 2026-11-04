"""Thirty-day retention for release-owned secrets; never runs for development .env."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from uuid import UUID, uuid4

from dotenv import dotenv_values

from credential_scheduler import WindowsCredentialScheduler, installation_id

LOGGER = logging.getLogger(__name__)
PREFIX = "ECHOES_ENV_V2\n"


class CredentialError(RuntimeError):
    pass


def sensitive_name(name: str) -> bool:
    upper = name.upper()
    return upper in {"APIKEY", "API_KEY"} or upper.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))


class CredentialLifecycle:
    def __init__(self, root: Path, scheduler=None, clock=None):
        self._root = root.absolute()
        self._scheduler = scheduler if scheduler is not None else WindowsCredentialScheduler(root)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._managed: set[str] = set()
        self._injected: dict[str, str] = {}
        self._loaded_generation: str | None = None
        self._error: str | None = None
        self._schedule_error = False

    def _paths(self) -> tuple[Path, Path]:
        config = self._root / "config"
        env, secure = config / ".env", config / ".env.secure"
        for path in (self._root, config, env, secure):
            if path.exists() or path.is_symlink():
                if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise CredentialError("credentials_invalid_path")
                if path in (env, secure) and not path.is_file():
                    raise CredentialError("credentials_invalid_path")
                if path in (env, secure) and path.stat().st_nlink > 1:
                    raise CredentialError("credentials_invalid_path")
        if config.resolve().parent != self._root.resolve():
            raise CredentialError("credentials_invalid_path")
        return env, secure

    @contextmanager
    def _lock(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.CreateMutexW(None, False, f"Global\\ECHOES-Credentials-{installation_id(self._root)}")
        if not handle:
            raise CredentialError("credentials_lock_failed")
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 5000) in (0, 0x80)
            if not acquired:
                raise CredentialError("credentials_lock_failed")
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)

    def _values(self, text: str) -> dict[str, str | None]:
        from dotenv.parser import parse_stream
        if any(binding.error for binding in parse_stream(io.StringIO(text))):
            raise CredentialError("credentials_invalid")
        return dict(dotenv_values(stream=io.StringIO(text), interpolate=False))

    def _names(self, text: str) -> set[str]:
        values = self._values(text)
        names = {name for name, value in values.items() if value and sensitive_name(name)}
        provider = self._root / "data" / "runtime" / "provider_config.json"
        try:
            variable = json.loads(provider.read_text(encoding="utf-8")).get("api_key_env_var")
            if isinstance(variable, str) and values.get(variable):
                names.add(variable)
        except (OSError, ValueError, AttributeError):
            pass
        return names

    def _new_record(self, text: str) -> dict:
        names = self._names(text)
        now = self._clock()
        return {"version": 2, "generation": str(uuid4()), "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(days=30)).isoformat(), "state": "active" if names else "unmanaged",
                "managed_names": sorted(names), "env_text": text}

    def _read(self, secure: Path) -> tuple[dict | None, bool]:
        import secure_env
        if not secure.exists():
            return None, False
        text = secure_env.unprotect(secure.read_bytes()).decode("utf-8")
        if not text.startswith(PREFIX):
            # Legacy is dotenv text only; damaged JSON/envelopes must never gain another term.
            if text.lstrip().startswith(("{", "ECHOES_ENV_")):
                raise CredentialError("credentials_invalid")
            return self._new_record(text), True
        record = json.loads(text[len(PREFIX):])
        if record["version"] != 2 or record["state"] not in {"active", "expired", "unmanaged"}:
            raise CredentialError("credentials_invalid")
        UUID(record["generation"])
        issued = datetime.fromisoformat(record["issued_at"])
        expires = datetime.fromisoformat(record["expires_at"])
        if issued.utcoffset() is None or expires.utcoffset() is None or expires - issued != timedelta(days=30):
            raise CredentialError("credentials_invalid")
        if self._clock() < issued or not isinstance(record["env_text"], str):
            raise CredentialError("credentials_invalid")
        if not isinstance(record["managed_names"], list) or not all(isinstance(n, str) for n in record["managed_names"]):
            raise CredentialError("credentials_invalid")
        return record, False

    def _write(self, secure: Path, record: dict) -> None:
        import secure_env
        data = secure_env.protect((PREFIX + json.dumps(record, ensure_ascii=False)).encode("utf-8"))
        if secure_env.unprotect(data).decode("utf-8") != PREFIX + json.dumps(record, ensure_ascii=False):
            raise CredentialError("credentials_write_failed")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=secure.parent, prefix=".env-secure-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._paths()
            os.replace(temporary, secure)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def _due(self, record: dict) -> bool:
        return record["state"] == "active" and self._clock() >= datetime.fromisoformat(record["expires_at"])

    def _clear_injected(self) -> None:
        for name, value in self._injected.items():
            if os.environ.get(name) == value:
                os.environ.pop(name, None)
        self._injected.clear()

    def _expire(self, secure: Path, record: dict) -> bool:
        if not self._due(record):
            return False
        self._managed.update(record["managed_names"])
        self._error = "credentials_expired"
        self._clear_injected()
        values = self._values(record["env_text"])
        names = set(record["managed_names"]) | self._names(record["env_text"])
        record["managed_names"] = sorted(names)
        record["env_text"] = "\n".join(f"{name}={json.dumps(value, ensure_ascii=False)}" if value is not None else name
                                       for name, value in values.items() if name not in names) + "\n"
        record["state"] = "expired"
        self._write(secure, record)
        LOGGER.info("[CREDENTIALS] expired credentials removed; non-secret settings retained")
        return True

    def import_environment(self) -> str | None:
        try:
            with self._lock():
                env, secure = self._paths()
                if env.exists():
                    text = env.read_text(encoding="utf-8")
                    record = self._new_record(text)
                    if not record["managed_names"]:
                        previous, _ = self._read(secure)
                        if previous and previous["managed_names"]:
                            record.update(managed_names=previous["managed_names"], issued_at=previous["issued_at"],
                                          expires_at=previous["expires_at"], state=previous["state"])
                    self._write(secure, record)
                    # An editor does not take our mutex. Preserve a replacement input.
                    if env.read_text(encoding="utf-8") == text:
                        env.unlink()
                else:
                    record, legacy = self._read(secure)
                    if record is None:
                        return None
                    if legacy:
                        self._write(secure, record)
                self._clear_injected()
                self._managed = set(record["managed_names"])
                self._loaded_generation = record["generation"]
                self._expire(secure, record)
                self._error = "credentials_expired" if record["state"] == "expired" else None
                if record["state"] == "active":
                    try:
                        self._scheduler.register(self._root / "VirtualPet.exe", record["generation"],
                                                 datetime.fromisoformat(record["expires_at"]))
                        self._schedule_error = False
                    except (OSError, subprocess.TimeoutExpired):
                        self._schedule_error = True
                        LOGGER.warning("[CREDENTIALS] task registration failed; startup/request checks remain active")
                return record["env_text"]
        except (OSError, ValueError, KeyError, TypeError, CredentialError):
            self._error = "credentials_invalid"
            self._clear_injected()
            LOGGER.warning("[CREDENTIALS] cannot load protected settings; recreate config/.env")
            return None

    def track_injected(self, name: str, value: str) -> None:
        if name in self._managed:
            self._injected[name] = value

    def expire_if_due(self, expected_generation: str | None = None) -> bool:
        with self._lock():
            _, secure = self._paths()
            record, legacy = self._read(secure)
            if record is None or legacy or (expected_generation and record["generation"] != expected_generation):
                return False
            return self._expire(secure, record)

    def require_usable(self, variable_name: str) -> None:
        try:
            with self._lock():
                _, secure = self._paths()
                record, legacy = self._read(secure)
                if record is None:
                    if self._loaded_generation or self._error:
                        raise CredentialError("credentials_invalid")
                    return
                if legacy:
                    raise CredentialError("credentials_invalid")
                self._managed.update(record["managed_names"])
                self._managed.update(self._names(record["env_text"]))
                if variable_name not in self._managed and self._error in (None, "credentials_expired"):
                    return
                if self._loaded_generation and record["generation"] != self._loaded_generation:
                    raise CredentialError("credentials_changed_restart_required")
                self._expire(secure, record)
                if record["state"] == "expired":
                    raise CredentialError("credentials_expired")
                if self._error:
                    raise CredentialError(self._error)
        except (OSError, ValueError, KeyError, TypeError, CredentialError) as exc:
            self._clear_injected()
            reason = str(exc) if isinstance(exc, CredentialError) else "credentials_invalid"
            self._error = reason
            raise CredentialError(reason) from None

    def status(self) -> str | None:
        try:
            self.require_usable(next(iter(self._managed), "API_KEY"))
        except CredentialError:
            pass
        return self._error or ("credentials_schedule_failed" if self._schedule_error else None)


def require_usable(variable_name: str) -> None:
    import config
    if config.CREDENTIAL_LIFECYCLE is not None:
        config.CREDENTIAL_LIFECYCLE.require_usable(variable_name)


def runtime_status() -> str | None:
    import config
    return config.CREDENTIAL_LIFECYCLE.status() if config.CREDENTIAL_LIFECYCLE is not None else None


def maintenance_cli(argv: list[str], root: Path) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="ECHOES credential expiry maintenance")
    parser.add_argument("--expire-credentials", action="store_true", required=True)
    parser.add_argument("--generation", required=True, type=lambda value: str(UUID(value)))
    args = parser.parse_args(argv)
    try:
        CredentialLifecycle(root).expire_if_due(args.generation)
        return 0
    except (OSError, ValueError, KeyError, TypeError, CredentialError):
        LOGGER.warning("[CREDENTIALS] scheduled cleanup failed; retry required")
        return 1
