from types import SimpleNamespace

from PyQt5.QtCore import QPoint

from ui.harness_ui_bridge import HarnessUiBridge
from ui.interaction_region_manager import InteractionRegionManager


def _bridge(manager):
    bridge = SimpleNamespace(
        _window=SimpleNamespace(
            _developer_input=None,
            set_stage_active=lambda _active, _screen_routed=False: None,
        ),
        _interaction_region_manager=manager,
        _web_regions=[],
    )
    bridge._sync_interaction_regions = lambda: HarnessUiBridge._sync_interaction_regions(bridge)
    return bridge


def test_bridge_report_changes_hit_test_result():
    manager = InteractionRegionManager()
    bridge = _bridge(manager)

    HarnessUiBridge.update_hit_regions(bridge, '{"devicePixelRatio": 1.5, "regions": [{"x": 10, "y": 20, "width": 30, "height": 40}]}')

    assert manager.hit_test(QPoint(10, 20))
    assert not manager.hit_test(QPoint(41, 61))


def test_bridge_malformed_payload_fails_open():
    manager = InteractionRegionManager()
    manager.update_regions([])
    manager.set_fallback_interactive(False)
    bridge = _bridge(manager)

    HarnessUiBridge.update_hit_regions(bridge, '{not json')

    assert manager.hit_test(QPoint(999, 999))
