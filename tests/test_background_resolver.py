from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.background_resolver import BackgroundResolver


def test_resolve_returns_loaded_for_existing_configured_background(tmp_path):
    configured = tmp_path / "characters" / "miku" / "BG_Final.png"
    configured.parent.mkdir(parents=True)
    configured.write_bytes(b"png")

    resolver = BackgroundResolver(project_root=tmp_path)

    status, url = resolver.resolve(configured_path=configured)

    assert status == "loaded"
    assert url
    assert str(configured) not in url
    assert "BG_Final.png" in url


def test_resolve_falls_back_to_default_asset_when_configured_missing(tmp_path):
    default_asset = tmp_path / "assets" / "backgrounds" / "default_room.png"
    default_asset.parent.mkdir(parents=True)
    default_asset.write_bytes(b"png")

    resolver = BackgroundResolver(project_root=tmp_path)

    status, url = resolver.resolve(configured_path=tmp_path / "missing.png")

    assert status == "fallback_default"
    assert url
    assert "default_room.png" in url


def test_resolve_falls_back_to_placeholder_when_no_assets_exist(tmp_path):
    resolver = BackgroundResolver(project_root=tmp_path)

    status, url = resolver.resolve(configured_path=tmp_path / "missing.png")

    assert status == "fallback_placeholder"
    assert url is None


def test_diagnostics_mask_absolute_paths_and_include_reason(tmp_path):
    default_asset = tmp_path / "assets" / "backgrounds" / "default_room.webp"
    default_asset.parent.mkdir(parents=True)
    default_asset.write_bytes(b"webp")
    missing = tmp_path / "characters" / "miku" / "missing.png"

    resolver = BackgroundResolver(project_root=tmp_path)
    resolver.resolve(configured_path=missing)
    diagnostics = resolver.diagnostics()

    assert diagnostics["background_status"] == "fallback_default"
    assert "reason" in diagnostics
    assert str(tmp_path) not in str(diagnostics)
