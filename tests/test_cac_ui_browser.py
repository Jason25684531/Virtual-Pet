"""Browser-level checks for the confirmed CAC UX public interaction seams."""

from pathlib import Path

import pytest


playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright

# 主螢幕可用區：1920×1080 扣掉 48px 工作列。視窗以此為尺寸，故視口外 == 實機上看不到。
VIEWPORT = {"width": 1920, "height": 1032}
CENTER_X = VIEWPORT["width"] / 2
CENTER_Y = VIEWPORT["height"] / 2


@pytest.fixture
def page():
    with sync_playwright() as runner:
        browser = runner.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.add_init_script(
            """
            window.qt = { webChannelTransport: {} };
            window.QWebChannel = function (_, ready) {
              var ok = function (data) { return JSON.stringify({ok: true, data: data}); };
              ready({objects: {
                harnessBridge: {setDragEnabled: function () {}, beginWindowDrag: function () { window.__drag_calls = (window.__drag_calls || 0) + 1; }, refreshState: function () {}, toggleSkill: function () { window.__skill_toggles = (window.__skill_toggles || 0) + 1; }, sendText: function () {}, toggleStt: function () {}, triggerQuickIntent: function () {}, triggerOverlayAction: function () {}},
                characterBridge: {
                  listCharacters: function (done) { done(ok([])); },
                  listPresets: function (done) { done(ok([{character_id: 'miku', name: 'Miku', persona_description: 'Virtual singer'}])); },
                  createFromPreset: function (_, __, done) { done(ok({})); },
                  getActiveState: function (done) { done(ok(Object.assign({active: true, character_id: 'miku', xp: {xp_total: 72, level: 2}}, window.__activeStateExtra || {}))); },
                  listStyleVariants: function (_, done) { done(ok([{variant: 'og', state: 'ready', thumb: '', is_active: true}, {variant: 'event', state: 'generating', thumb: 'preview.png', is_active: false}, {variant: 'development', state: 'ready', thumb: '', is_active: false}])); },
                  applyStyle: function (_, variant, done) { window.__applied_style = variant; done(ok({})); },
                  confirmGrowthOffer: function (_, __, done) { done(ok({accepted: window.__growthAccepted !== false})); },
                  confirmMotionGeneration: function (_, accept, done) { window.__motion_confirm = accept; done(ok({accepted: true})); },
                  listSceneBackgrounds: function (_, done) { done(ok(window.__sceneBackgrounds || [])); },
                  applyScene: function (_, sceneId, done) { window.__applied_scene = sceneId; done(ok({})); },
                  getCustomization: function (_, done) { window.__persona_loads = (window.__persona_loads || 0) + 1; done(ok({character_id: 'miku', persona: 'A considerate companion', builtin_skills: [], local_skills: []})); }
                }
              }});
            };
            """
        )
        page.goto((Path(__file__).parents[1] / "ui" / "web_container" / "index.html").resolve().as_uri())
        yield page
        browser.close()


def test_user_can_create_a_companion_then_switch_between_one_hud_at_a_time(page):
    page.locator("#menu-create-button").click()
    page.locator("#preset-select-button").click()
    assert page.locator("#modal-name-character").is_visible()

    page.locator("#name-character-confirm").click()
    page.wait_for_timeout(1000)
    assert page.locator("#companion-nav").is_visible()
    level_badge = page.locator("#hud-level-badge").inner_text()
    assert "Score 72" in level_badge
    assert "Lv.2" in level_badge

    page.locator('[data-hud="hud-chat"]').click()
    assert page.locator("#hud-chat").is_visible()
    page.locator('[data-hud="hud-agent"]').click()
    assert page.locator("#hud-agent").is_visible()
    assert not page.locator("#hud-chat").is_visible()

    page.locator("#hud-agent [data-close-hud]").click()
    assert not page.locator("#hud-layer").is_visible()

    score_before = page.locator("#hud-level-badge").inner_text()
    page.locator('[data-hud="hud-style"]').click()
    assert page.locator("#style-slot-count").inner_text() == "格子 3 / 3"
    assert page.locator("#style-slot-grid .slot--generating img").get_attribute("src").endswith("preview.png")
    assert page.locator("#style-slot-grid .slot--generating").is_disabled()
    page.locator("#style-slot-grid .slot--ready").nth(1).click()
    assert page.locator("#style-apply-button").is_enabled()
    page.locator("#style-apply-button").click()
    assert not page.locator("#hud-layer").is_visible()
    assert page.locator("#hud-level-badge").inner_text() == score_before
    assert page.evaluate("window.__applied_style") == "development"

    page.evaluate("window.requestClose()")
    assert page.locator("#modal-close-confirm").is_visible()
    page.locator("#skip-close-confirm").check()
    page.locator("#close-confirm-button").click()
    page.evaluate("window.requestClose()")
    assert not page.locator("#modal-close-confirm").is_visible()


def test_render_activity_badge_is_available_without_progress_overlay(page):
    page.evaluate("""
        document.querySelector('#stage-root').hidden = false;
        document.querySelector('#hud-level-badge').hidden = false;
        document.querySelector('#render-activity-badge').hidden = false;
        document.querySelector('#render-activity-badge').className = 'render-activity-badge is-active';
    """)
    activity = page.locator("#render-activity-badge")

    assert activity.is_visible()
    assert page.locator("#render-progress-overlay").count() == 0


def test_growth_offer_stays_open_when_generation_was_not_queued(page):
    page.evaluate("window.__growthAccepted = false")
    page.locator("#modal-layer").evaluate("element => element.hidden = false")
    page.locator("#modal-growth-offer").evaluate("element => element.hidden = false")

    page.locator("#growth-offer-accept").click()

    assert page.locator("#modal-growth-offer").is_visible()


def test_scene_panel_shows_real_backgrounds_with_no_objects_tab(page):
    _enter_companion_stage(page)
    page.evaluate(
        "window.__sceneBackgrounds = ["
        "{scene_id: 'og', thumb: 'bg/og.png', is_current: true},"
        "{scene_id: 'development', thumb: 'bg/development.png', is_current: false}]"
    )

    page.locator('[data-hud="hud-scene"]').click()

    assert page.locator("[data-scene-tab]").count() == 0
    assert page.locator("#scene-slot-grid .slot--ready").count() == 2
    assert page.locator("#scene-slot-grid .slot--empty").count() == 1

    page.locator("#scene-slot-grid .slot--ready").nth(1).click()
    page.locator("#scene-apply-button").click()
    assert page.evaluate("window.__applied_scene") == "development"


def test_scene_follow_button_restores_style_linked_background(page):
    _enter_companion_stage(page)
    page.locator('[data-hud="hud-scene"]').click()

    page.locator("#scene-follow-button").click()

    assert page.evaluate("window.__applied_scene") == "follow"


def test_motion_offer_accept_closes_modal_and_confirms_generation(page):
    page.locator("#modal-layer").evaluate("element => element.hidden = false")
    page.locator("#modal-motion-offer").evaluate("element => element.hidden = false")

    page.locator("#motion-offer-accept").click()

    assert not page.locator("#modal-motion-offer").is_visible()
    assert page.evaluate("window.__motion_confirm") is True


def test_motion_offer_decline_closes_modal_and_keeps_the_png(page):
    page.locator("#modal-layer").evaluate("element => element.hidden = false")
    page.locator("#modal-motion-offer").evaluate("element => element.hidden = false")

    page.locator("#motion-offer-decline").click()

    assert not page.locator("#modal-motion-offer").is_visible()
    assert page.evaluate("window.__motion_confirm") is False


def test_screens_are_centered_isolate_the_stage_and_offer_a_drag_surface(page):
    menu = page.locator("#screen-main-menu")
    assert menu.bounding_box() == {"x": 0, "y": 0, **VIEWPORT}
    layout = page.locator("#menu-screen-layout").bounding_box()
    assert abs(layout["x"] + layout["width"] / 2 - CENTER_X) <= 1
    actions = page.locator("#screen-main-menu .menu-screen__actions").bounding_box()
    preview = page.locator("#screen-main-menu .menu-screen__preview").bounding_box()
    assert actions["x"] < preview["x"]
    assert abs(preview["y"] + preview["height"] / 2 - CENTER_Y) <= 1
    corner = f"document.elementFromPoint({VIEWPORT['width'] - 10}, {VIEWPORT['height'] - 10}).id"
    assert page.evaluate(corner) == "screen-main-menu"

    page.evaluate("window.__drag_calls = 0")
    menu.click(position={"x": 24, "y": 24})
    assert page.evaluate("window.__drag_calls") == 1

    page.locator("#menu-load-button").evaluate("button => button.disabled = false")
    page.locator("#menu-load-button").click()
    load_card = page.locator("#screen-load-save .screen-card").bounding_box()
    assert abs(load_card["x"] + load_card["width"] / 2 - CENTER_X) <= 1
    assert abs(load_card["y"] + load_card["height"] / 2 - CENTER_Y) <= 1


def test_companion_stage_can_drag_without_stealing_character_clicks(page):
    _enter_companion_stage(page)
    character = page.locator("#pet-character")

    page.evaluate("window.__drag_calls = 0")
    character.click(position={"x": 500, "y": 400})
    assert page.evaluate("window.__drag_calls") == 0
    assert page.locator("#hud-chat").is_visible()

    page.locator("#hud-chat [data-close-hud]").click()
    character.hover(position={"x": 500, "y": 400})
    page.mouse.down()
    page.mouse.move(510, 410)
    page.mouse.up()
    assert page.evaluate("window.__drag_calls") == 1


def test_stage_furniture_stays_inside_the_visible_viewport(page):
    """視窗尺寸即主螢幕可用區,所以任何超出視口的元件在實機上就是看不到。
    寫死過大的視口曾讓角色、底部導覽與等級徽章整個落在畫面外。"""
    page.locator("#menu-create-button").click()
    page.locator("#preset-select-button").click()
    page.locator("#name-character-confirm").click()
    page.wait_for_timeout(1000)

    for selector in ("#companion-nav", "#hud-level-badge", "#pet-character"):
        box = page.locator(selector).bounding_box()
        assert box["x"] >= 0 and box["y"] >= 0, selector
        assert box["x"] + box["width"] <= VIEWPORT["width"], selector
        assert box["y"] + box["height"] <= VIEWPORT["height"], selector


def test_layout_keeps_the_mockup_grid_and_navigation_spacing(page):
    menu_columns = page.locator("#menu-screen-layout").evaluate(
        "element => getComputedStyle(element).gridTemplateColumns"
    )
    assert menu_columns == "616px 616px"

    page.locator("#menu-load-button").evaluate("button => button.disabled = false")
    page.locator("#menu-load-button").click()
    load_card = page.locator("#screen-load-save .screen-card").bounding_box()
    assert load_card["width"] == 1320
    columns = page.locator("#save-card-grid").evaluate(
        "element => getComputedStyle(element).gridTemplateColumns"
    )
    assert len(columns.split()) == 4
    page.locator("#save-card-grid").evaluate(
        """grid => {
            for (let i = 0; i < 12; i += 1) {
                const card = document.createElement('button');
                card.className = 'save-card';
                grid.append(card);
            }
        }"""
    )
    assert page.locator("#save-card-grid").evaluate(
        "grid => grid.scrollHeight > grid.clientHeight"
    )
    footer = page.locator("#screen-load-save .screen-footer").bounding_box()
    assert footer["y"] + footer["height"] <= VIEWPORT["height"]

    page.locator("#screen-load-save [data-screen]").click()
    page.locator("#menu-create-button").click()
    page.locator("#preset-select-button").click()
    page.locator("#name-character-confirm").click()
    page.wait_for_timeout(1000)
    nav_items = page.locator("#companion-nav > button").all()
    first = nav_items[0].bounding_box()
    second = nav_items[1].bounding_box()
    assert second["x"] - first["x"] == 120


def test_chat_history_is_in_its_hud_and_developer_controls_use_the_debug_panel(page):
    page.locator("#menu-create-button").click()
    page.locator("#preset-select-button").click()
    page.locator("#name-character-confirm").click()
    page.wait_for_timeout(1000)

    page.evaluate("window.beginConversationTurn('turn-1', 'User', 'Hello'); window.setConversationAssistant('turn-1', 'Welcome back')")
    page.locator('[data-hud="hud-chat"]').click()
    chat_history = page.locator("#hud-chat #conversation-list").inner_text()
    assert "Hello" in chat_history
    assert "Welcome back" in chat_history

    page.locator('[data-hud="hud-agent"]').click()
    assert page.locator("#hud-agent").is_visible()
    assert page.locator("#debug-panel").is_hidden()
    page.keyboard.press("Control+Shift+D")
    assert page.locator("#debug-panel").is_visible()

    page.evaluate("window.hydrateAgenticUI({skills: [{skill_id: 'news', display_name: 'Game News', enabled: true, triggers: []}]})")
    page.locator("#debug-panel #skill-list [data-skill-toggle]").click()
    assert page.evaluate("window.__skill_toggles") == 1


def _enter_companion_stage(page):
    page.locator("#menu-create-button").click()
    page.locator("#preset-select-button").click()
    page.locator("#name-character-confirm").click()
    page.wait_for_timeout(1000)


def test_stage_keeps_the_background_clean_and_the_character_at_its_native_size(page):
    """webm 是 1920×1080 全幅、角色已合成在畫面裡，所以角色層要滿版承接原尺寸，
    不能再套一層固定小框把它縮成一小塊；背景也不該被壓暗或疊遮罩。"""
    _enter_companion_stage(page)

    assert page.locator(".room-background").evaluate("e => getComputedStyle(e).opacity") == "1"
    assert page.locator("#stage-background").evaluate(
        "e => getComputedStyle(e, '::after').backgroundImage"
    ) == "none"

    char = page.locator("#pet-character").bounding_box()
    assert (char["width"], char["height"]) == (VIEWPORT["width"], VIEWPORT["height"])


def test_companion_stage_offers_a_way_back_to_the_main_menu(page):
    _enter_companion_stage(page)

    back = page.locator("#stage-menu-button")
    assert back.is_visible()
    box = back.bounding_box()
    assert box["x"] > CENTER_X and box["y"] > CENTER_Y
    assert box["x"] + box["width"] <= VIEWPORT["width"]
    assert box["y"] + box["height"] <= VIEWPORT["height"]

    back.click()
    assert page.locator("#screen-main-menu").is_visible()
    assert not back.is_visible()


def test_skill_toggle_button_shows_on_off_by_opacity_not_the_whole_card(page):
    """整張卡片變半透明會連說明文字都讀不清楚，且不像個可互動的開關。
    改成只有「啟動／關閉」按鈕本身透明度隨狀態變化，卡片內容全程可讀。"""
    _enter_companion_stage(page)
    page.keyboard.press("Control+Shift+D")
    page.evaluate(
        "window.hydrateAgenticUI({skills: ["
        "{skill_id: 'news', display_name: 'News', enabled: true, triggers: []},"
        "{skill_id: 'music', display_name: 'Music', enabled: false, triggers: []}]})"
    )

    cards = page.locator("#skill-list .entity-card")
    card_on = float(cards.nth(0).evaluate("e => getComputedStyle(e).opacity"))
    card_off = float(cards.nth(1).evaluate("e => getComputedStyle(e).opacity"))
    assert card_on == 1
    assert card_off == 1

    toggles = page.locator("#skill-list [data-skill-toggle]")
    button_on = float(toggles.nth(0).evaluate("e => getComputedStyle(e).opacity"))
    button_off = float(toggles.nth(1).evaluate("e => getComputedStyle(e).opacity"))
    assert button_off < button_on


def test_voice_button_stays_an_icon_and_carries_its_label_as_a_tooltip(page):
    """狀態文案（如「STT 不可用」）曾被塞進 42×42 的 icon 按鈕，撐出 Chat 面板外。"""
    _enter_companion_stage(page)
    page.locator('[data-hud="hud-chat"]').click()
    page.evaluate(
        "window.updateRuntimeControls({stt: {label: 'STT 不可用', statusLabel: '未連線',"
        " state: 'unavailable', enabled: false}, reset: {enabled: true}})"
    )

    button = page.locator("#runtime-stt-button")
    assert len(button.inner_text().strip()) <= 2
    assert button.get_attribute("title") == "STT 不可用"
    assert button.get_attribute("data-state") == "unavailable"

    box = button.bounding_box()
    assert box["width"] <= 48 and box["height"] <= 48


def test_hud_panel_sits_above_the_nav_instead_of_dead_center(page):
    """面板曾用 place-items:center，中心點精準落在螢幕正中央（與角色臉部/身體中線重疊）。
    面板中心點離視口正中央要有實質距離（不論偏移方向），且底部不與底部導覽互壓。
    用距離而非單一軸向斷言，允許版面之後在水平或垂直任一軸調整位置。"""
    _enter_companion_stage(page)
    page.locator('[data-hud="hud-agent"]').click()

    panel = page.locator("#hud-agent")
    box = panel.bounding_box()
    nav = page.locator("#companion-nav").bounding_box()
    panel_center_x = box["x"] + box["width"] / 2
    panel_center_y = box["y"] + box["height"] / 2
    distance_from_dead_center = ((panel_center_x - CENTER_X) ** 2 + (panel_center_y - CENTER_Y) ** 2) ** 0.5

    assert distance_from_dead_center > 150
    assert box["y"] + box["height"] <= nav["y"]


def test_persona_field_lives_in_the_agent_hud_not_the_debug_panel(page):
    """人設欄位原本只藏在 Ctrl+Shift+D 的除錯面板裡，一般使用流程完全看不到。
    移入 Agent HUD 本體，開啟 Agent 面板時沿用既有 bridge 自動載入目前人設。"""
    _enter_companion_stage(page)
    page.locator('[data-hud="hud-agent"]').click()

    persona_field = page.locator("#hud-agent #persona-textarea")
    assert persona_field.is_visible()
    assert persona_field.input_value() == "A considerate companion"
    assert page.locator("#hud-agent #persona-save-button").is_visible()


def test_agent_chips_go_solid_only_when_a_matching_skill_is_enabled(page):
    """音樂／新聞快捷鍵原本不論底下技能是否啟用都長得一樣，點下去也沒有任何回饋。
    改為重用既有的 button:disabled 樣式：有已啟用的對應技能才是實體可按，否則變淡。"""
    _enter_companion_stage(page)
    page.locator('[data-hud="hud-agent"]').click()

    page.evaluate(
        "window.hydrateAgenticUI({skills: ["
        "{skill_id: 'bahamut_daily_news', default_behavior: 'news_idle', enabled: true},"
        "{skill_id: 'music_bgm', default_behavior: 'music_idle', enabled: false},"
        "{skill_id: 'youtube_music_playback', default_behavior: 'music_idle', enabled: false}]})"
    )

    assert page.locator("#agent-chip-news").is_enabled()
    assert page.locator("#agent-chip-music").is_disabled()
    music_opacity = float(page.locator("#agent-chip-music").evaluate("e => getComputedStyle(e).opacity"))
    news_opacity = float(page.locator("#agent-chip-news").evaluate("e => getComputedStyle(e).opacity"))
    assert music_opacity < news_opacity

    page.evaluate(
        "window.hydrateAgenticUI({skills: ["
        "{skill_id: 'music_bgm', default_behavior: 'music_idle', enabled: true}]})"
    )
    assert page.locator("#agent-chip-music").is_enabled()


def test_agent_chips_have_a_toggle_for_the_highest_priority_matching_skill(page):
    """音樂/新聞各對應兩顆候選技能，開關鈕固定切最高優先度那顆
    （youtube_music_playback / bahamut_daily_news），沿用既有 toggleSkill bridge。"""
    _enter_companion_stage(page)
    page.locator('[data-hud="hud-agent"]').click()

    page.evaluate(
        "window.hydrateAgenticUI({skills: ["
        "{skill_id: 'music_bgm', default_behavior: 'music_idle', enabled: false, priority: 0},"
        "{skill_id: 'youtube_music_playback', default_behavior: 'music_idle', enabled: true, priority: 100},"
        "{skill_id: 'game_news', default_behavior: 'news_idle', enabled: false, priority: 0},"
        "{skill_id: 'bahamut_daily_news', default_behavior: 'news_idle', enabled: true, priority: 100}]})"
    )

    music_toggle = page.locator("#agent-toggle-music")
    news_toggle = page.locator("#agent-toggle-news")
    assert music_toggle.get_attribute("data-skill-id") == "youtube_music_playback"
    assert music_toggle.get_attribute("data-skill-enabled") == "true"
    assert news_toggle.get_attribute("data-skill-id") == "bahamut_daily_news"

    music_toggle.click()
    assert page.evaluate("window.__skill_toggles") == 1
