from ui.transparent_window import TransparentWindow

WIDTH = 1000
HEIGHT = 800


def test_top_area_still_caption():
    assert TransparentWindow.should_treat_point_as_caption(500, 50, WIDTH, HEIGHT) is True


def test_xp_badge_area_excluded():
    x = WIDTH - TransparentWindow.XP_BADGE_RIGHT - 10
    y = TransparentWindow.XP_BADGE_TOP + 10
    assert TransparentWindow.should_treat_point_as_caption(x, y, WIDTH, HEIGHT) is False


def test_agentic_panel_area_excluded():
    x = WIDTH - TransparentWindow.AGENTIC_PANEL_RIGHT - 10
    y = TransparentWindow.AGENTIC_PANEL_TOP + 10
    assert TransparentWindow.should_treat_point_as_caption(x, y, WIDTH, HEIGHT) is False


def test_bottom_dock_band_excluded():
    x = WIDTH // 2
    y = HEIGHT - 20
    assert TransparentWindow.should_treat_point_as_caption(x, y, WIDTH, HEIGHT) is False


def test_utility_bar_area_excluded():
    x = WIDTH - TransparentWindow.UTILITY_BAR_RIGHT - 10
    y = HEIGHT - TransparentWindow.UTILITY_BAR_BOTTOM - 10
    assert TransparentWindow.should_treat_point_as_caption(x, y, WIDTH, HEIGHT) is False


def test_middle_of_stage_still_caption():
    assert TransparentWindow.should_treat_point_as_caption(20, HEIGHT // 2, WIDTH, HEIGHT) is True
