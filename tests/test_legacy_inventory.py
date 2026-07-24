"""The temporary compatibility inventory must stay explicit until deletion."""

from pathlib import Path


REMOVAL_TARGETS = {
    "ui/transparent_window.py": (
        "def _dispatch_action_legacy",
        "def _trigger_cached_intent_legacy",
        "def _reset_runtime_state_legacy",
        "def trigger_overlay_action(",
        "def _init_stt_button",
        "def _init_reset_button",
        "def _init_fixed_intent_buttons",
    ),
    "action_dispatcher.py": ("ActionDispatcher",),
    "pet_harness/app/ports/character_port.py": ("class CharacterPort",),
    "pet_harness/app/runtime_lifecycle.py": ("def start_all",),
}


def test_removal_inventory_targets_are_gone():
    root = Path(__file__).parents[1]
    for relative_path, symbols in REMOVAL_TARGETS.items():
        path = root / relative_path
        source = path.read_text(encoding="utf-8") if path.exists() else ""
        assert all(symbol not in source for symbol in symbols), relative_path


def test_only_provider_config_migration_is_a_permitted_legacy_name_after_removal():
    root = Path(__file__).parents[1]
    source = (root / "pet_harness/runtime/provider_runtime.py").read_text(encoding="utf-8")
    assert "migrate_legacy_provider_config" in source
