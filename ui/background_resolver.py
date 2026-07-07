from __future__ import annotations

from pathlib import Path


DEFAULT_BACKGROUND_CANDIDATES = (
    "assets/backgrounds/default_room.png",
    "assets/backgrounds/default_room.jpg",
    "assets/backgrounds/default_room.webp",
    "ui/assets/backgrounds/default-room.jpg",
    "ui/assets/backgrounds/default_room.jpg",
)


class BackgroundResolver:
    def __init__(
        self,
        project_root: str | Path | None = None,
        default_candidates: tuple[str, ...] | None = None,
    ) -> None:
        self._project_root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self._default_candidates = tuple(default_candidates or DEFAULT_BACKGROUND_CANDIDATES)
        self._last_result = {
            "background_status": "fallback_placeholder",
            "background_url": None,
            "reason": "no background has been resolved yet",
        }

    def resolve(self, configured_path: str | Path | None = None) -> tuple[str, str | None]:
        configured = self._resolve_existing_path(configured_path)
        if configured is not None:
            return self._remember("loaded", configured, "configured character background found on disk")

        default_path = self._resolve_default_background()
        if default_path is not None:
            reason = "configured background missing; using default room asset"
            return self._remember("fallback_default", default_path, reason)

        return self._remember("fallback_placeholder", None, "no configured or default background asset found")

    def diagnostics(self) -> dict[str, str | None]:
        return dict(self._last_result)

    def _resolve_default_background(self) -> Path | None:
        for candidate in self._default_candidates:
            resolved = self._resolve_existing_path(candidate)
            if resolved is not None:
                return resolved
        return None

    def _resolve_existing_path(self, candidate: str | Path | None) -> Path | None:
        normalized = str(candidate or "").strip()
        if not normalized:
            return None
        path = Path(normalized)
        absolute = path if path.is_absolute() else (self._project_root / path)
        absolute = absolute.resolve()
        if absolute.is_file():
            return absolute
        return None

    def _remember(self, status: str, path: Path | None, reason: str) -> tuple[str, str | None]:
        safe_url = self._to_safe_url(path) if path is not None else None
        self._last_result = {
            "background_status": status,
            "background_url": safe_url,
            "reason": reason,
        }
        return status, safe_url

    def _to_safe_url(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self._project_root)
            return relative.as_posix()
        except ValueError:
            return path.name
