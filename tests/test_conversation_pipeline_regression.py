"""P0 baseline: the legacy adapter's conversation payload is a stable UI contract."""

from pet_harness.runtime.provider_runtime import ProviderRuntime
from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from tests.conftest import FakeProvider
from tests.test_harness_per_character import harness_env  # noqa: F401


def test_text_turn_keeps_the_ui_payload_contract(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    payload = adapter.handle_text_input("tell me a joke")

    assert set(payload) == {
        "reply", "matched_skill", "tool", "xp_delta", "reward_summary",
        "asset_summary", "behavior_id", "webm_key", "provider_status",
        "saved_to_db", "warnings", "raw_event", "xp_display", "user_text",
    }
    assert payload["user_text"] == "tell me a joke"
    assert payload["matched_skill"] == "joke_skill"
    assert payload["reply"].startswith("[fake]")
    assert {"xp_total", "level", "display"} <= set(payload["xp_display"])
