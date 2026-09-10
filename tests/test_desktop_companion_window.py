from types import SimpleNamespace
import ctypes

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QRegion

from ui.transparent_window import TransparentWindow
from ui.interaction_region_manager import InteractionRegionManager


def test_companion_mode_does_not_set_always_on_top(monkeypatch):
    captured = []
    fake = SimpleNamespace(
        _desktop_companion_mode=True,
        setWindowFlags=captured.append,
        setAttribute=lambda *_args: None,
        setAutoFillBackground=lambda *_args: None,
        _apply_left_clickthrough_mask=lambda: None,
        setStyleSheet=lambda *_args: None,
        setGeometry=lambda *_args: None,
    )
    monkeypatch.setattr('ui.transparent_window.QApplication', SimpleNamespace(primaryScreen=lambda: None))

    TransparentWindow._init_window(fake)

    assert not captured[0] & Qt.WindowStaysOnTopHint


def test_rollback_mode_restores_always_on_top(monkeypatch):
    captured = []
    fake = SimpleNamespace(
        _desktop_companion_mode=False,
        setWindowFlags=captured.append,
        setAttribute=lambda *_args: None,
        setAutoFillBackground=lambda *_args: None,
        _apply_left_clickthrough_mask=lambda: None,
        setStyleSheet=lambda *_args: None,
        setGeometry=lambda *_args: None,
    )
    monkeypatch.setattr('ui.transparent_window.QApplication', SimpleNamespace(primaryScreen=lambda: None))

    TransparentWindow._init_window(fake)

    assert captured[0] & Qt.WindowStaysOnTopHint


def test_native_hit_test_passes_through_outside_regions():
    manager = InteractionRegionManager()
    manager.update_regions([QRect(10, 10, 20, 20)])
    manager.set_fallback_interactive(False)
    fake = SimpleNamespace(
        _desktop_companion_mode=True,
        _interaction_regions=manager,
        _developer_input=SimpleNamespace(isVisible=lambda: False),
        _native_screen_to_local=lambda x, y: QPoint(x, y),
    )
    message = ctypes.wintypes.MSG()
    message.message = 0x0084
    message.pt.x, message.pt.y = 100, 100

    handled, result = TransparentWindow.nativeEvent(fake, b'windows_generic_MSG', ctypes.addressof(message))

    assert handled is True
    assert result == -1  # HTTRANSPARENT


def test_native_hit_test_keeps_reported_regions_interactive():
    manager = InteractionRegionManager()
    manager.update_regions([QRect(10, 10, 20, 20)])
    manager.set_fallback_interactive(False)
    fake = SimpleNamespace(
        _desktop_companion_mode=True,
        _interaction_regions=manager,
        _developer_input=SimpleNamespace(isVisible=lambda: False),
        _native_screen_to_local=lambda x, y: QPoint(x, y),
    )
    message = ctypes.wintypes.MSG()
    message.message = 0x0084
    message.pt.x, message.pt.y = 15, 15

    handled, result = TransparentWindow.nativeEvent(fake, b'windows_generic_MSG', ctypes.addressof(message))

    assert handled is True
    assert result == 1  # HTCLIENT


def test_left_strip_is_cut_out_of_the_window():
    """WM_NCHITTEST 回 HTTRANSPARENT 只往同執行緒的視窗傳，跨行程沒用（實測 nchittest 回
    -1，WindowFromPoint 仍是本視窗）。要讓桌面 icon 點得到，左側那條得真的不屬於視窗。"""
    masks = []
    fake = SimpleNamespace(
        _left_clickthrough_px=200,
        _stage_active=True,
        width=lambda: 1920,
        height=lambda: 1032,
        setMask=masks.append,
        clearMask=lambda: masks.append(None),
    )

    TransparentWindow._apply_left_clickthrough_mask(fake)

    assert masks == [QRegion(QRect(200, 0, 1720, 1032))]

    # 主選單／讀檔頁不是舞台，整片都要留給程式自己點。
    fake._stage_active = False
    TransparentWindow._apply_left_clickthrough_mask(fake)

    assert masks[-1] is None
