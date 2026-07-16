"""互動流暢性回歸測試：讀取路徑不得在同一輪互動內重複觸發全量 runtime 重建。"""

from unittest.mock import patch

from pet_harness.ui.pyqt_harness_adapter import PyQtHarnessAdapter
from pet_harness.runtime.provider_runtime import ProviderRuntime

from tests.test_harness_per_character import harness_env  # noqa: F401  (reused fixture)
from tests.conftest import FakeProvider


def test_get_current_state_does_not_rebuild_runtime(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    with patch.object(PyQtHarnessAdapter, "_refresh_runtime") as mocked_refresh:
        adapter.get_current_state()
        mocked_refresh.assert_not_called()


def test_get_provider_status_does_not_rebuild_runtime(harness_env):
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    with patch.object(PyQtHarnessAdapter, "_refresh_runtime") as mocked_refresh:
        adapter.get_provider_status()
        mocked_refresh.assert_not_called()


def test_single_interaction_rebuilds_runtime_exactly_once(harness_env):
    """單輪互動(handle_text_input + 緊接的 get_current_state)只能重建一次 runtime。"""
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    with patch.object(PyQtHarnessAdapter, "_refresh_runtime", wraps=adapter._refresh_runtime) as spy:
        adapter.handle_text_input("hello")
        adapter.get_current_state()
        assert spy.call_count == 1


def test_skill_toggle_takes_effect_on_next_interaction(harness_env):
    """toggle 後不強制立即重建;下一輪互動仍會反映最新的 enabled 狀態。"""
    _tmp_path, agentic_root = harness_env
    adapter = PyQtHarnessAdapter(
        default_character_id="Choppr",
        agentic_root=str(agentic_root),
        provider_runtime=ProviderRuntime(provider=FakeProvider()),
    )

    result = adapter.set_skill_enabled("joke_skill", False)
    assert result["enabled"] is False

    skills_by_id = {item["skill_id"]: item for item in adapter.list_skills()}
    assert skills_by_id["joke_skill"]["enabled"] is False

    payload = adapter.handle_text_input("joke")
    assert payload["matched_skill"] != "joke_skill"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
