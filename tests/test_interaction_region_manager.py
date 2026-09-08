import json
from types import SimpleNamespace

from PyQt5.QtCore import QPoint, QRect

from ui.harness_ui_bridge import HarnessUiBridge
from ui.interaction_region_manager import InteractionRegionManager


def test_hit_test_and_empty_regions_fail_open():
    manager = InteractionRegionManager()

    assert manager.region_count == 0
    assert manager.hit_test(QPoint(999, 999)) is True

    manager.update_regions([QRect(10, 20, 30, 40)])
    assert manager.region_count == 1
    assert manager.hit_test(QPoint(10, 20)) is True
    assert manager.hit_test(QPoint(50, 60)) is False

    manager.clear()
    assert manager.hit_test(QPoint(999, 999)) is True


def test_invalid_payload_is_ignored_with_fail_open():
    manager = InteractionRegionManager()
    fake = SimpleNamespace(_interaction_regions=manager, _developer_input=None)
    bridge = SimpleNamespace(
        _window=fake,
        _interaction_region_manager=manager,
        _web_regions=[],
    )
    bridge._sync_interaction_regions = lambda: HarnessUiBridge._sync_interaction_regions(bridge)

    HarnessUiBridge.update_hit_regions(bridge, "not json")

    assert manager.region_count == 0
    assert manager.hit_test(QPoint(0, 0)) is True


def test_dpi_conversion_keeps_dom_css_coordinates_aligned_with_native_pixels():
    assert InteractionRegionManager.native_to_css(150, 1.5) == 100
    assert InteractionRegionManager.native_to_css(100, 1.0) == 100

    manager = InteractionRegionManager()
    fake = SimpleNamespace(_interaction_regions=manager, _developer_input=None)
    bridge = SimpleNamespace(
        _window=fake,
        _interaction_region_manager=manager,
        _web_regions=[],
    )
    bridge._sync_interaction_regions = lambda: HarnessUiBridge._sync_interaction_regions(bridge)
    HarnessUiBridge.update_hit_regions(
        bridge,
        json.dumps({"devicePixelRatio": 1.5, "regions": [{"x": 90, "y": 40, "width": 20, "height": 20}]}),
    )

    assert manager.hit_test(QPoint(100, 50)) is True
    assert manager.hit_test(QPoint(120, 50)) is False
