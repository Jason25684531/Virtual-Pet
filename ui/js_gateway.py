"""Queued, WebView-safe Python-to-JavaScript bridge."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


class JsGateway:
    def __init__(self, page: Callable[[], Any], raw_marker: str) -> None:
        self._page = page
        self._raw_marker = raw_marker
        self._ready = False
        self._pending: list[tuple[str, tuple[object, ...]]] = []

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_ready(self) -> None:
        self._ready = True
        pending, self._pending = self._pending, []
        for name, args in pending:
            self.call(name, *args)

    def raw(self, script: str) -> None:
        self.call(self._raw_marker, script)

    def call(self, function_name: str, *args: object) -> None:
        if not self._ready:
            self._pending.append((function_name, args))
            return
        script = str(args[0]) if function_name == self._raw_marker and args else self.build_call(function_name, *args)
        self._page().runJavaScript(script)

    @staticmethod
    def build_call(function_name: str, *args: object) -> str:
        js_function_name = json.dumps(function_name)
        js_args = ", ".join(json.dumps(arg) for arg in args)
        return (
            "(function(){"
            f"var fn = window[{js_function_name}] || (window.echoes && window.echoes[{js_function_name}]);"
            f"if (typeof fn !== 'function') {{ console.warn('[ECHOES] JS bridge missing function: ' + {js_function_name}); return false; }}"
            f"fn({js_args});"
            "return true;"
            "})();"
        )
