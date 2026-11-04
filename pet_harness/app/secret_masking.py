"""Environment loading and recursive secret redaction shared by UI facades."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

import secure_env


def load_project_env(path: str | Path) -> dict[str, str]:
    text = secure_env.read_text(path)
    if text is None:
        return {}
    loaded = {key: value for key, value in dotenv_values(stream=io.StringIO(text)).items() if value is not None}
    for key, value in loaded.items():
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
