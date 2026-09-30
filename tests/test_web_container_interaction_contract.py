"""前端互動契約：拖曳範圍、對話捲動、XP 飄字、動作預載。

沿用既有慣例（見 test_cac_ui_contract.py）：對出貨到瀏覽器的 app.js / style.css /
index.html 做靜態斷言，專案沒有 JS test runner。
"""

import re
from pathlib import Path

WEB_ROOT = Path(__file__).parents[1] / "ui" / "web_container"


def _app_js() -> str:
    return (WEB_ROOT / "app.js").read_text(encoding="utf-8")


def _style() -> str:
    return (WEB_ROOT / "style.css").read_text(encoding="utf-8")


def _document() -> str:
    return (WEB_ROOT / "index.html").read_text(encoding="utf-8")


def _drag_setup_body() -> str:
    app_js = _app_js()
    start = app_js.index("function setupWindowDragHandles()")
    return app_js[start:app_js.index("\n    }", start)]


# ── 視窗拖曳範圍（#8 #9）───────────────────────────────────────


def test_drag_requires_an_explicit_handle():
    """預設不可拖：只有 .window-drag-handle 命中才搬移視窗。"""
    body = _drag_setup_body()

    assert ".window-drag-handle" in body
    assert "if (!handle) return;" in body


def test_drag_no_longer_fires_on_bare_mousedown_anywhere():
    """回歸檢查：舊版只要不是控制項就直接拖，整份 document 都是拖曳面。"""
    body = _drag_setup_body()

    assert "if (!control)" not in body


def test_press_and_move_drag_is_limited_to_the_pet_character():
    """舊版對所有元素都「按住移動 5px 就拖視窗」，捲軸與按鈕因此都變成半個把手。
    桌寵本體要保留這個手勢（點擊開聊天、拖曳搬視窗），但只限它自己。"""
    body = _drag_setup_body()

    assert "#pet-character" in body
    assert "Math.hypot" in body
    # 門檻判斷只認 petDragStart，不再對任意控制項生效
    assert "if (!petDragStart" in body
    assert "var control" not in body


def test_drag_excludes_scrollable_elements_and_scrollbars():
    app_js = _app_js()

    assert "function isScrollableInteraction" in app_js
    assert "function isOnScrollbar" in app_js
    assert "scrollHeight > " in app_js and "clientHeight" in app_js
    assert "isScrollableInteraction(handle, event.target, event)" in _drag_setup_body()


def test_drag_still_excludes_controls():
    body = _drag_setup_body()

    assert "isDragBlockedBy(event.target)" in body


def test_drag_block_list_covers_every_editable_and_control_element():
    """5.1:按鈕、輸入框、contenteditable 與內容區都必須把事件還給頁面。"""
    app_js = _app_js()
    start = app_js.index("function isDragBlockedBy(element)")
    body = app_js[start:app_js.index("\n    }", start)]

    for selector in ("button", "input", "a", "textarea", "select", "label", '[contenteditable="true"]'):
        assert selector in body, selector


def test_native_move_result_is_observable_instead_of_silently_assumed():
    """5.2:startSystemMove() 被平台拒絕時要看得見,不得靜靜當成成功。"""
    source = (Path(__file__).parents[1] / "ui" / "transparent_window.py").read_text(encoding="utf-8")
    start = source.index("    def begin_window_drag(self)")
    body = source[start:source.index("\n    def ", start + 10)]

    assert "def begin_window_drag(self) -> bool" in body
    assert "started = bool(start_system_move())" in body
    assert "return started" in body
    assert body.count("[DRAG]") == 2      # 不支援與被拒絕各有一條可查的紀錄


def test_full_screen_sections_are_no_longer_whole_drag_surfaces():
    html = _document()

    assert 'id="screen-main-menu" class="app-screen menu-screen"' in html
    assert 'id="screen-load-save" class="app-screen load-screen"' in html


def test_screens_still_expose_a_drag_handle():
    html = _document()
    style = _style()

    assert html.count("window-drag-bar window-drag-handle") == 2
    assert html.count("screen-header window-drag-handle") == 2
    assert ".window-drag-bar" in style
    # cursor: move 是使用者找得到把手的唯一提示
    assert re.search(r"\.window-drag-handle\s*\{[^}]*cursor:\s*move", style)


# ── 對話紀錄捲動（#5）─────────────────────────────────────────


def test_conversation_keeps_enough_history_to_scroll():
    """3 輪會讓歷史真的被 removeChild 刪掉，不是捲不到而是不存在。"""
    match = re.search(r"var maxConversationTurns = (\d+);", _app_js())

    assert match is not None
    assert int(match.group(1)) >= 20


def test_message_list_avoids_the_chromium_grid_scroll_bug():
    """實機 Chromium 83 在 grid align-content: end 時，溢出到上方的內容捲不到。"""
    style = _style()
    block = style[style.index(".message-list {"):]
    block = block[:block.index("}")]

    assert "align-content: end" not in block
    assert "display: flex" in block
    assert "flex-direction: column" in block
    assert "overflow: auto" in block
    assert "margin-top: auto" in style


def test_message_list_spacing_survives_the_shipped_chromium():
    """flex 的 gap 要 Chromium 84，實機 83 會整條丟棄讓間距歸零。"""
    style = _style()
    block = style[style.index(".message-list {"):]
    block = block[:block.index("}")]

    assert "gap:" not in block
    assert ".message-list>*+*" in style.replace(" ", "")


def test_conversation_autoscroll_measures_before_mutating():
    app_js = _app_js()

    assert "function pinConversationToBottom" in app_js
    # 附加內容前先量測，rAF 讓同 tick 的同步變更做完才捲
    assert "pinConversationToBottom();\n        var existing = conversationTurns.get(turnId);" in app_js
    assert "requestAnimationFrame" in app_js


def test_conversation_autoscroll_respects_a_user_reading_history():
    app_js = _app_js()
    start = app_js.index("function pinConversationToBottom")
    body = app_js[start:app_js.index("\n    }", start)]

    assert "distanceFromBottom >= 40" in body
    assert "return" in body


# ── XP 飄字（#4）──────────────────────────────────────────────


def test_hud_delta_is_not_derived_from_persisted_state():
    """xp.last_delta 是持久化狀態；當事件用會讓每 5s 的輪詢重播一次飄字。"""
    app_js = _app_js()
    start = app_js.index("function renderCharacterHud")
    body = app_js[start:app_js.index("\n    }", start)]

    assert "showHudDelta(xpDeltaOverride)" in body
    assert "xpState.last_delta" not in body


def test_polling_and_state_paths_pass_no_delta():
    app_js = _app_js()

    assert "renderCharacterHud(mergedState, 0)" in app_js
    assert "renderCharacterHud({ active: true, xp: state.xp || {} }, 0)" in app_js


# ── AI 回覆不重複、不摻罐頭訊息 ────────────────────────────────


def _render_agent_event_body() -> str:
    app_js = _app_js()
    start = app_js.index("function renderLatestAgentEvent")
    return app_js[start:app_js.index("\n    }", start)]


def test_agent_reply_never_falls_back_to_status_text():
    """舊版 fallback 到 eventData.message，於是 "Character switched." 這種狀態
    訊息會被當成角色說的話寫進回覆欄，那就是罐頭訊息的來源。"""
    body = _render_agent_event_body()

    assert "eventData.message" not in body
    assert "eventData.summary" not in body
    assert "輸入問題或點選快捷指令" not in body


def test_agent_reply_is_deduplicated():
    body = _render_agent_event_body()

    assert "lastRenderedAgentReply" in body
    assert "if (!reply) return;" in body


def test_state_snapshot_is_not_rendered_as_a_new_reply():
    """state.latest_event 是上一次完成回合的磁碟快照，每次重整都會重播成新回覆。"""
    app_js = _app_js()
    start = app_js.index("function renderState")
    body = app_js[start:app_js.index("\n    }", start)]

    assert "renderLatestAgentEvent" not in body


def test_hydrate_only_renders_the_current_turn_event():
    app_js = _app_js()
    start = app_js.index("window.hydrateAgenticUI")
    body = app_js[start:app_js.index("\n    };", start)]

    assert "renderLatestAgentEvent(payload.event," in body
    code = "\n".join(line for line in body.splitlines() if "//" not in line)
    assert "latest_event" not in code


def test_event_path_still_flashes_the_real_delta():
    app_js = _app_js()
    start = app_js.index("window.hydrateAgenticUI")
    body = app_js[start:app_js.index("\n    };", start)]

    assert "payload.xp_delta" in body


# ── 動作預載（#3）────────────────────────────────────────────


def test_preload_motion_loads_without_playing():
    app_js = _app_js()
    start = app_js.index("window.preloadMotion")
    body = app_js[start:app_js.index("\n    };", start)]

    assert "video.load()" in body
    assert ".play()" not in body


def test_start_motion_loop_skips_a_second_load_for_a_preloaded_source():
    """同一個 <video> 連續兩次 load() 會 abort 前一次 play() 並讓 ended 偵測失靈。"""
    app_js = _app_js()
    start = app_js.index("window.startMotionLoop")
    body = app_js[start:app_js.index("\n    };", start)]

    assert "preloadedMotionSource === source && video.readyState >= 2" in body
    assert "else {\n            setSource(source, false);" in body


def test_changing_the_source_invalidates_the_preload():
    """否則 startMotionLoop 會誤判「已載好」而直接播到別的片子。"""
    app_js = _app_js()
    start = app_js.index("function setSource(source, shouldLoop)")
    body = app_js[start:app_js.index("\n    }", start)]

    assert "preloadedMotionSource = null" in body


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
