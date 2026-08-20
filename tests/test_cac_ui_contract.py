"""Public web-container contract for the CAC UX screens and interaction layers."""

from pathlib import Path


WEB_ROOT = Path(__file__).parents[1] / "ui" / "web_container"


def _document() -> str:
    return (WEB_ROOT / "index.html").read_text(encoding="utf-8")


def test_cac_ui_exposes_only_the_new_public_layers():
    """Users can reach screens, one HUD layer, and one modal layer from the document."""
    html = _document()

    for element_id in (
        "stage-background", "stage-pet-layer", "app-screens", "hud-layer", "modal-layer",
        "debug-panel", "screen-main-menu", "screen-create-character", "screen-load-save",
        "screen-loading", "hud-chat", "hud-agent", "hud-style", "hud-scene",
        "modal-name-character", "modal-discard-confirm", "modal-delete-confirm",
        "modal-close-confirm", "modal-reward-popup", "companion-nav", "hud-level-badge", "render-activity-badge",
    ):
        assert f'id="{element_id}"' in html

    for removed_id in (
        "conversation-history-panel", "companion-dock-root", "dock-panel-",
        "action-status", "stage-bottom-ui", "talk-send-button", "menu-settings-button",
    ):
        assert removed_id not in html

    for legacy_state in ("overlay-active", "cdn.tailwindcss.com", "googleusercontent", "fonts.googleapis.com"):
        assert legacy_state not in html


def test_cac_ui_router_and_slot_contract_are_shipped_to_the_browser():
    app_js = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    style = (WEB_ROOT / "style.css").read_text(encoding="utf-8")

    for public_behavior in ("screen", "hud", "modal", "slot--ready", "slot--generating", "slot--empty"):
        assert public_behavior in app_js or public_behavior in style

    assert "body.overlay-active" not in style


def test_style_avoids_css_the_shipped_chromium_cannot_parse():
    """實機是 QtWebEngine 5.15 = Chromium 83。`inset` 簡寫要 87、flex 的 `gap` 要 84，
    兩者在實機上會被丟棄:整層塌成內容大小並跑到左上角、間距全部歸零。
    Playwright 用的是新版 Chromium，這兩件事它一律驗不出來。"""
    style = (WEB_ROOT / "style.css").read_text(encoding="utf-8")

    assert "inset:" not in style
    for rule in style.split("}"):
        if "display: flex" in rule:
            assert "gap:" not in rule, rule.strip()[:120]


def test_qt_does_not_second_guess_which_pixels_the_web_ui_can_receive():
    """視窗命中判定曾用舊 UI 的矩形白名單（XP badge／agentic panel／dock band／utility bar）。
    新版面把元件挪到別處後，HUD 關閉鈕、Menu 鈕等落在名單外，Windows 會回 HTCAPTION、
    改送 WM_NCLBUTTONDOWN 去拖視窗，QWebEngineView 永遠收不到那些點擊。
    可點區域一律由前端決定，Qt 端不再猜。"""
    source = (Path(__file__).parents[1] / "ui" / "transparent_window.py").read_text(encoding="utf-8")

    assert "should_treat_point_as_caption" not in source
    for stale_region in ("XP_BADGE_", "AGENTIC_PANEL_", "DOCK_BAND_", "UTILITY_BAR_"):
        assert stale_region not in source, stale_region


def test_transparent_window_sizes_itself_to_the_primary_screen():
    """視窗尺寸就是 CSS 視口尺寸。寫死解析度時視窗會超出螢幕並被夾在 (0, 0)，
    使用者只看得到畫布左上角的裁切——角色與底部導覽會整個落在畫面外。"""
    source = (Path(__file__).parents[1] / "ui" / "transparent_window.py").read_text(encoding="utf-8")

    assert "availableGeometry()" in source
    assert "WINDOW_WIDTH" not in source
    assert "WINDOW_HEIGHT" not in source
