"""Environment loading and recursive secret redaction shared by UI facades."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def load_project_env(path: str | Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return loaded
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = re.sub(r"\$\{([^}]+)\}", lambda match: loaded.get(match.group(1), os.environ.get(match.group(1), "")), value)
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


class SecretMasker:
    def __init__(self, environment: dict[str, str]) -> None:
        self._environment = environment

    def payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self.payload(item)
                if str(key).lower().endswith("_env_var") or str(key).lower() == "required_env"
                else ("***" if item and any(token in str(key).lower() for token in ("secret", "token", "api_key", "authorization")) else self.payload(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.payload(item) for item in value]
        return self.text(value) if isinstance(value, str) else value

    def text(self, text: str) -> str:
        for key, value in self._environment.items():
            if value and len(value) >= 8 and any(token in key.lower() for token in ("key", "token", "secret")):
                text = text.replace(value, "***")
        return text
