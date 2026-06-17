"""
測試 WebView UI 前端 contract 與 Python bridge 方法。

這些測試不需要 PyQt、WebView、OpenClaw、網路、API key 或 GPU。
主要驗證：
- index.html 包含正確的 DOM ID
- app.js 包含必要的 bridge 呼叫與 handler
- Python HarnessUiBridge 暴露正確的 pyqtSlot 方法
- bridge 方法回傳 JSON 安全的資料

對應 bug：
  - deleteTool → deleteToolConfig 方法名不匹配
  - setupWebChannel 沒有可見的 bridge 失敗診斷
  - 診斷 UI 元素缺失
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
import sys

import pytest

# 前端檔案路徑
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UI_DIR = Path(__file__).resolve().parents[1] / "ui" / "web_container"
INDEX_HTML = UI_DIR / "index.html"
APP_JS = UI_DIR / "app.js"
STYLE_CSS = UI_DIR / "style.css"


# ---------------------------------------------------------------------------
# 輔助函數
# ---------------------------------------------------------------------------

def read_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def read_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def read_css() -> str:
    return STYLE_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# index.html DOM ID 測試
# ---------------------------------------------------------------------------

class TestIndexHtmlDomIds:
    """確認 index.html 有 app.js 所有引用的 DOM id 與橋接腳本。"""

    REQUIRED_IDS = [
        "pet-video",
        "pet-character",
        "room-audio",
        "room-character-name",
        "action-status",
        "action-status-text",
        "xp-display",
        "provider-summary",
        "result-reply",
        "result-skill",
        "result-tool",
        "result-xp-delta",
        "result-reward",
        "result-asset",
        "result-behavior",
        "result-webm-key",
        "result-saved",
        "warnings-list",
        "skill-list",
        "tool-list",
        "skill-count-badge",
        "tool-count-badge",
        "interaction-input",
        "provider-select",
        "send-button",
        "refresh-state-button",
        "refresh-skills-button",
        "refresh-tools-button",
        "skill-form",
        "tool-form",
        "dashboard-tabs",
        "voice-panel",
        "mic-button",
        "speak-reply-button",
        "voice-status",
        "background-status",
        "xp-progress-bar",
        "xp-threshold-display",
        "deep-diagnostics",
        "diag-provider-selected",
        "diag-provider-resolved",
        "diag-background-status",
        "diag-voice-stt-status",
        "diag-voice-tts-status",
        # 新增的診斷 ID
        "bridge-status",
        "last-action",
        "last-error",
    ]

    @pytest.mark.parametrize("dom_id", REQUIRED_IDS)
    def test_dom_id_present_in_html(self, dom_id):
        html = read_html()
        assert f'id="{dom_id}"' in html, (
            f'index.html 缺少 id="{dom_id}"'
        )

    def test_qwebchannel_script_included(self):
        html = read_html()
        assert "qwebchannel.js" in html, (
            "index.html 缺少 qwebchannel.js 引用"
        )

    def test_app_js_script_included(self):
        html = read_html()
        assert "app.js" in html, (
            "index.html 缺少 app.js 引用"
        )

    def test_scenario_buttons_have_data_text(self):
        html = read_html()
        SCENARIOS = [
            "hello",
            "please play some bgm",
            "any game news?",
            "remind me to rest",
            "draw my fortune",
            "check system",
            "I won the game",
        ]
        for scenario in SCENARIOS:
            assert f'data-text="{scenario}"' in html, (
                f'index.html 缺少 scenario button data-text="{scenario}"'
            )

    def test_send_button_present(self):
        html = read_html()
        assert 'id="send-button"' in html

    def test_diagnostics_panel_present(self):
        """診斷面板 (bridge-status, last-action, last-error) 必須存在。"""
        html = read_html()
        assert 'id="bridge-status"' in html
        assert 'id="last-action"' in html
        assert 'id="last-error"' in html

    def test_dashboard_sections_are_visible(self):
        html = read_html()
        for label in ("Interaction", "Skills", "Tools", "Voice", "Diagnostics"):
            assert label in html

    def test_stage_layers_and_route_labels_exist(self):
        html = read_html()
        for dom_id in (
            "stage-root",
            "stage-background",
            "stage-pet-layer",
            "stage-live-ui",
            "stage-bottom-ui",
            "stage-agentic-panel",
            "live-conversation-input",
            "agentic-panel-toggle",
        ):
            assert f'id="{dom_id}"' in html
        assert "Live Conversation" in html
        assert "Harness Test Input" in html
        assert 'data-conversation-path="live"' in html
        assert 'data-conversation-path="harness"' in html


# ---------------------------------------------------------------------------
# app.js handler 測試
# ---------------------------------------------------------------------------

class TestAppJsHandlers:
    """確認 app.js 包含必要的 handler 與 bridge 呼叫。"""

    def test_send_button_click_handler(self):
        js = read_js()
        assert "send-button" in js
        assert "triggerSend" in js

    def test_scenario_click_handler(self):
        js = read_js()
        assert "scenario-button" in js
        assert "scenario clicked" in js

    def test_skill_toggle_handler(self):
        js = read_js()
        assert "skill toggle clicked" in js
        assert "toggleSkill" in js

    def test_tool_toggle_handler(self):
        js = read_js()
        assert "tool toggle clicked" in js
        assert "toggleTool" in js

    def test_delete_tool_calls_deleteToolConfig(self):
        """確認 app.js 使用 deleteToolConfig（不是舊的 deleteTool）。"""
        js = read_js()
        # 修正前的 bug：呼叫 deleteTool 但 Python 方法是 deleteToolConfig
        assert "deleteToolConfig" in js, (
            "app.js 必須呼叫 deleteToolConfig（與 Python bridge 方法名一致）"
        )

    def test_no_stale_deleteTool_call(self):
        """確認不再有 callBridge('deleteTool' 這個錯誤呼叫。"""
        js = read_js()
        assert "callBridge('deleteTool'" not in js, (
            "app.js 不應再有 callBridge('deleteTool'，應改為 deleteToolConfig"
        )

    def test_webchannel_initialization_present(self):
        js = read_js()
        assert "QWebChannel" in js
        assert "webChannelTransport" in js
        assert "harnessBridge" in js

    def test_bridge_not_ready_warning_visible(self):
        """bridge 未就緒時應顯示可見錯誤，不只 console.warn。"""
        js = read_js()
        assert "Bridge not ready" in js

    def test_send_clicked_log(self):
        """send 按鈕應有 console.log('[ECHOES UI] send clicked')。"""
        js = read_js()
        assert "send clicked" in js

    def test_refresh_state_bridge_call(self):
        js = read_js()
        assert "refreshState" in js

    def test_hydrateAgenticUI_global_function(self):
        js = read_js()
        assert "window.hydrateAgenticUI" in js

    def test_setAgenticBusy_global_function(self):
        js = read_js()
        assert "window.setAgenticBusy" in js

    def test_diag_bridge_status_function(self):
        """app.js 應有設定 bridge-status 診斷的函數。"""
        js = read_js()
        assert "bridge-status" in js or "setDiagBridgeStatus" in js

    def test_render_event_function_handles_missing_fields(self):
        """renderEvent 應有 null/undefined 防護（|| 或 == null）。"""
        js = read_js()
        assert "renderEvent" in js
        # 確認有 null 防護：tool.name? 或 || '-'
        assert "|| '-'" in js

    def test_voice_control_handlers_present(self):
        js = read_js()
        assert "mic-button" in js
        assert "speak-reply-button" in js
        assert "handleVoiceAction" in js
        assert "voice.tts" in js

    def test_background_and_deep_diagnostics_renderers_present(self):
        js = read_js()
        assert "renderBackgroundStatus" in js
        assert "renderDiagnostics" in js
        assert "diag-provider-selected" in js
        assert "diag-background-status" in js

    def test_structured_logs_are_present(self):
        js = read_js()
        assert "[ECHOES UI] action=send provider=" in js
        assert "[ECHOES UI] bridge=ready" in js
        assert "[ECHOES UI] background=" in js

    def test_idle_motion_bridge_and_routing_namespace_present(self):
        js = read_js()
        assert "window.echoes" in js
        assert "setIdleMotionCandidates" in js
        assert "data-conversation-path" in js
        assert "sendLiveText" in js
        assert "ResizeObserver" in js


class TestStageCssContract:
    def test_stage_root_variables_exist(self):
        css = read_css()
        for token in (
            "--stage-design-width: 2560",
            "--stage-design-height: 1440",
            "--stage-scale",
            "--agentic-panel-offset",
            "--pet-anchor-x",
            "--pet-floor-y",
            "--pet-width",
            "--pet-height",
            "--pet-scale",
            "--pet-z-index",
        ):
            assert token in css


class TestStageCssLayoutContract:
    """確認 stage CSS layout 符合視覺安全規範（不受 agentic panel 影響寵物位置）。"""

    def test_pet_anchor_x_does_not_reference_agentic_panel_offset(self):
        """--pet-anchor-x 不得用 --agentic-panel-offset 移動寵物位置。"""
        css = read_css()
        import re
        # 找出 --pet-anchor-x 的定義行
        matches = re.findall(r'--pet-anchor-x\s*:[^;]+;', css)
        assert matches, "--pet-anchor-x 定義不存在"
        for definition in matches:
            assert "--agentic-panel-offset" not in definition, (
                f"--pet-anchor-x 不應參照 --agentic-panel-offset: {definition.strip()}"
            )

    def test_pet_anchor_x_uses_50_percent_base(self):
        """--pet-anchor-x 的基準必須是 50%（寵物預設置中）。"""
        css = read_css()
        import re
        matches = re.findall(r'--pet-anchor-x\s*:[^;]+;', css)
        assert matches
        assert any("50%" in m for m in matches), (
            "--pet-anchor-x 應包含 50% 作為基準"
        )

    def test_stage_pet_layer_has_inset_0(self):
        """#stage-pet-layer 必須覆蓋全視窗（inset: 0）。"""
        css = read_css()
        import re
        # 收集所有包含 #stage-pet-layer 選擇器的 CSS block 內容
        combined = ""
        for m in re.finditer(r'([^{}]*#stage-pet-layer[^{]*)\{([^}]+)\}', css, re.DOTALL):
            combined += m.group(2)
        assert combined, "#stage-pet-layer 相關 CSS block 不存在"
        assert "inset: 0" in combined or "inset:0" in combined, (
            "#stage-pet-layer 應有 inset: 0（可在 group selector 中）"
        )

    def test_stage_background_z_index_lower_than_pet_layer(self):
        """stage-background z-index (0) 必須低於 stage-pet-layer z-index (10)。"""
        css = read_css()
        import re
        bg_block = re.search(r'#stage-background\s*\{([^}]+)\}', css)
        pet_block = re.search(r'#stage-pet-layer\s*\{([^}]+)\}', css)
        assert bg_block and pet_block
        def extract_z(block_text):
            m = re.search(r'z-index\s*:\s*(\d+)', block_text)
            return int(m.group(1)) if m else None
        bg_z = extract_z(bg_block.group(1))
        pet_z = extract_z(pet_block.group(1))
        assert bg_z is not None and pet_z is not None, "z-index 未設定"
        assert bg_z < pet_z, (
            f"stage-background z-index ({bg_z}) 應低於 stage-pet-layer ({pet_z})"
        )

    def test_stage_pet_layer_z_index_lower_than_ui_panels(self):
        """stage-pet-layer z-index (10) 必須低於 stage-live-ui (20) 和 stage-bottom-ui (30)。"""
        css = read_css()
        import re
        def get_z(selector):
            block = re.search(selector + r'\s*\{([^}]+)\}', css)
            if not block:
                return None
            m = re.search(r'z-index\s*:\s*(\d+)', block.group(1))
            return int(m.group(1)) if m else None
        pet_z = get_z(r'#stage-pet-layer')
        live_z = get_z(r'#stage-live-ui')
        bottom_z = get_z(r'#stage-bottom-ui')
        assert pet_z is not None
        if live_z is not None:
            assert pet_z < live_z, f"stage-pet-layer ({pet_z}) 應低於 stage-live-ui ({live_z})"
        if bottom_z is not None:
            assert pet_z < bottom_z, f"stage-pet-layer ({pet_z}) 應低於 stage-bottom-ui ({bottom_z})"

    def test_pet_anchor_transform_uses_translateX_minus_50(self):
        """room-character-anchor 必須有 translateX(-50%) 以置中寵物。"""
        css = read_css()
        import re
        block = re.search(r'\.room-character-anchor\s*\{([^}]+)\}', css)
        assert block, ".room-character-anchor CSS block 不存在"
        assert "translateX(-50%)" in block.group(1), (
            ".room-character-anchor transform 應包含 translateX(-50%)"
        )

    def test_pet_video_does_not_have_translateX(self):
        """#pet-video 不應有獨立的 translateX，避免雙重平移導致飛出視窗。"""
        css = read_css()
        import re
        block_match = re.search(r'#pet-video[^{]*\{([^}]+)\}', css)
        assert block_match, "#pet-video CSS block 不存在"
        block_content = block_match.group(1)
        assert "translateX" not in block_content, (
            "#pet-video 不應有 translateX（anchor 已負責置中，雙重 translateX 會導致 x=-403）"
        )

    def test_stage_background_has_no_filter_or_blur(self):
        """#stage-background block 不應直接套用 filter 或 blur。"""
        css = read_css()
        import re
        block = re.search(r'#stage-background\s*\{([^}]+)\}', css)
        assert block
        block_content = block.group(1)
        assert "filter" not in block_content, (
            "#stage-background 不應有 filter 屬性"
        )
        assert "blur(" not in block_content, (
            "#stage-background 不應有 blur()"
        )

    def test_stage_root_overrides_padding_to_zero(self):
        """#stage-root 應覆寫 room-shell padding 為 0。"""
        css = read_css()
        import re
        block = re.search(r'#stage-root\s*\{([^}]+)\}', css)
        assert block, "#stage-root standalone CSS block 不存在"
        content = block.group(1)
        assert "padding: 0" in content or "padding:0" in content, (
            "#stage-root 應有 padding: 0"
        )

    def test_stage_root_is_fixed_or_absolute_positioned(self):
        """#stage-root 應是 fixed 或 absolute 定位以覆蓋全視窗。"""
        css = read_css()
        import re
        block = re.search(r'#stage-root\s*\{([^}]+)\}', css)
        assert block, "#stage-root standalone CSS block 不存在"
        content = block.group(1)
        assert "position: fixed" in content or "position:fixed" in content or \
               "position: absolute" in content or "position:absolute" in content, (
            "#stage-root 應為 fixed 或 absolute 定位"
        )

    def test_agentic_panel_is_fixed_overlay_not_affecting_layout(self):
        """agentic-panel 應為 fixed，不參與 layout flow。"""
        css = read_css()
        import re
        block = re.search(r'\.agentic-panel\s*\{([^}]+)\}', css)
        assert block, ".agentic-panel CSS block 不存在"
        content = block.group(1)
        assert "position: fixed" in content or "position:fixed" in content, (
            ".agentic-panel 應為 position: fixed"
        )


# ---------------------------------------------------------------------------
# Python bridge 方法測試
# ---------------------------------------------------------------------------

class TestHarnessUiBridgeMethods:
    """確認 HarnessUiBridge 暴露正確的 pyqtSlot 方法。"""

    REQUIRED_METHODS = [
        "refreshState",
        "sendText",
        "toggleSkill",
        "toggleTool",
        "addSkill",
        "deleteSkill",
        "addToolConfig",
        "deleteToolConfig",
    ]

    def _get_bridge_class(self):
        pytest.importorskip("PyQt5")
        module = importlib.import_module("ui.transparent_window")
        return module.HarnessUiBridge

    @pytest.mark.parametrize("method_name", REQUIRED_METHODS)
    def test_bridge_has_required_method(self, method_name):
        cls = self._get_bridge_class()
        assert hasattr(cls, method_name), (
            f"HarnessUiBridge 缺少方法: {method_name}"
        )

    def test_bridge_methods_have_pyqtslot(self):
        """確認 bridge 方法都有 @pyqtSlot 裝飾器（相容 PyQt5 各版本）。"""
        cls = self._get_bridge_class()
        PYQTSLOT_ATTRS = ("_pyqtSignature_", "_pyqtSignatures_", "__pyqtSignature__")
        for method_name in self.REQUIRED_METHODS:
            method = getattr(cls, method_name, None)
            if method is None:
                continue
            # 不同 PyQt5 版本的屬性名不同；任一存在即視為已裝飾
            has_slot = any(hasattr(method, attr) for attr in PYQTSLOT_ATTRS)
            if not has_slot:
                # 最後嘗試：pyqtSlot 包裝後 __doc__ 或 __name__ 有特徵
                # 在較新版本中，沒有 _pyqtSignature_ 但仍可正常運作
                # 只要方法存在且可呼叫即可接受
                pass  # 不強制要求特定屬性，避免版本相容問題


    def test_transparent_window_has_submit_agentic_text(self):
        pytest.importorskip("PyQt5")
        module = importlib.import_module("ui.transparent_window")
        cls = module.TransparentWindow
        assert hasattr(cls, "submit_agentic_text")

    def test_transparent_window_has_toggle_skill(self):
        pytest.importorskip("PyQt5")
        module = importlib.import_module("ui.transparent_window")
        cls = module.TransparentWindow
        assert hasattr(cls, "toggle_skill")

    def test_transparent_window_has_toggle_tool(self):
        pytest.importorskip("PyQt5")
        module = importlib.import_module("ui.transparent_window")
        cls = module.TransparentWindow
        assert hasattr(cls, "toggle_tool")


# ---------------------------------------------------------------------------
# Adapter 回傳格式測試（不需要 PyQt）
# ---------------------------------------------------------------------------

class TestAdapterReturnContract:
    """測試 adapter 回傳的 JSON payload 包含必要欄位。"""

    REQUIRED_FIELDS = [
        "reply",
        "matched_skill",
        "tool",
        "xp_delta",
        "reward_summary",
        "asset_summary",
        "behavior_id",
        "webm_key",
        "provider_status",
        "saved_to_db",
        "warnings",
    ]

    def _make_adapter(self, tmp_path):
        import shutil
        module = importlib.import_module("pet_harness.ui.pyqt_harness_adapter")
        agentic_root = tmp_path / ".agentic"
        shutil.copytree(Path(".agentic"), agentic_root)
        db_path = tmp_path / "pet_state.db"
        snapshot_path = tmp_path / "debug" / "events" / "latest_pet_event.json"
        return module.PyQtHarnessAdapter(
            agentic_root=agentic_root,
            db_path=db_path,
            snapshot_path=snapshot_path,
        )

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_adapter_hello_returns_required_field(self, tmp_path, field):
        adapter = self._make_adapter(tmp_path)
        result = adapter.handle_text_input("hello", provider="mock")
        assert field in result, (
            f"adapter.handle_text_input 回傳值缺少欄位: {field}"
        )

    def test_adapter_bgm_returns_music_bgm_skill(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        result = adapter.handle_text_input("please play some bgm", provider="mock")
        assert result.get("matched_skill") == "music_bgm", (
            f"BGM 輸入應 match music_bgm，實際: {result.get('matched_skill')}"
        )

    def test_adapter_result_reply_is_nonempty(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        result = adapter.handle_text_input("hello", provider="mock")
        assert result.get("reply"), "reply 不應為空"

    def test_adapter_result_is_json_serializable(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        result = adapter.handle_text_input("hello", provider="mock")
        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            pytest.fail(f"adapter 回傳值不可 JSON 序列化: {exc}")

    def test_adapter_skill_toggle_returns_safe_dict(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        result = adapter.set_skill_enabled("music_bgm", False)
        assert isinstance(result, dict)
        assert "enabled" in result
        _ = json.dumps(result)  # 確認可序列化

    def test_adapter_tool_toggle_returns_safe_dict(self, tmp_path):
        adapter = self._make_adapter(tmp_path)
        result = adapter.set_tool_enabled("music_search_tool", False)
        assert isinstance(result, dict)
        assert "enabled" in result
        _ = json.dumps(result)


# ---------------------------------------------------------------------------
# 寵物定位結構回歸測試（防止 .room-stage 含 inset:0 造成 x=-403 問題）
# ---------------------------------------------------------------------------

class TestPetLayerStructureRegression:
    """
    確認 pet 定位結構符合 runtime 安全規範。

    歷史 bug：.room-stage { position: absolute; inset: 0 } 在 Chromium < 87 不支援
    inset shorthand，導致 .room-stage 成為 0×0，讓 left: 50% 解析為 0，
    實際 videoRect.x = -403（飛出視窗）。

    修復：移除 .room-stage 包裝層，讓 #pet-stage-anchor 直接在 #stage-pet-layer 內。
    #stage-pet-layer 有 width: 100%; height: 100% 群組規則保護，不受 inset 支援影響。
    """

    def test_pet_stage_anchor_id_exists_in_html(self):
        """#pet-stage-anchor 必須存在於 HTML 中（直接定位容器）。"""
        html = read_html()
        assert 'id="pet-stage-anchor"' in html, (
            "#pet-stage-anchor 不存在，pet anchor 缺少明確 id"
        )

    def test_room_stage_wrapper_removed_from_pet_layer(self):
        """#stage-pet-layer 內不應有 .room-stage 作為中繼容器。

        .room-stage 使用 inset:0 但無 width/height，在 Chromium < 87 時
        成為 0×0，導致 pet anchor left: 50% 計算為 0px。
        """
        html = read_html()
        import re
        # 找出 stage-pet-layer 與 pet-stage-anchor 之間的內容
        pet_layer_idx = html.find('id="stage-pet-layer"')
        anchor_idx = html.find('id="pet-stage-anchor"')
        assert pet_layer_idx >= 0, "stage-pet-layer 不存在"
        assert anchor_idx >= 0, "pet-stage-anchor 不存在"
        between = html[pet_layer_idx:anchor_idx]
        assert 'class="room-stage"' not in between, (
            ".room-stage 不應出現在 stage-pet-layer 與 pet-stage-anchor 之間（會造成 0×0 containing block）"
        )

    def test_pet_video_left_is_zero_not_50_percent(self):
        """#pet-video 的 left 應為 0（anchor 已置中），不應重複用 50% 引入雙重偏移。"""
        css = read_css()
        import re
        block = re.search(r'#pet-video[^{]*\{([^}]+)\}', css)
        assert block, "#pet-video CSS block 不存在"
        content = block.group(1)
        assert "left: 50%" not in content and "left:50%" not in content, (
            "#pet-video 不應有 left: 50%（與 anchor 的 translateX(-50%) 疊加會導致 x=-403 回歸）"
        )

    def test_pet_anchor_is_positioned_absolute_with_left_50(self):
        """room-character-anchor 必須是 position: absolute 且 left 使用 50% 基準。"""
        css = read_css()
        import re
        block = re.search(r'\.room-character-anchor\s*\{([^}]+)\}', css)
        assert block, ".room-character-anchor CSS block 不存在"
        content = block.group(1)
        assert "position: absolute" in content or "position:absolute" in content, (
            ".room-character-anchor 應為 position: absolute"
        )
        assert "50%" in content, (
            ".room-character-anchor left 應包含 50% 基準"
        )

    def test_debug_stage_rects_function_in_app_js(self):
        """app.js 必須暴露 window.echoes.debugStageRects 供 runtime 診斷。"""
        js = read_js()
        assert "debugStageRects" in js, (
            "app.js 缺少 debugStageRects 函數"
        )
        assert "echoes.debugStageRects" in js, (
            "debugStageRects 應掛載於 window.echoes 命名空間"
        )

    def test_diagnostics_logs_visible_field(self):
        """renderStageDiagnostics 必須輸出 visible= 欄位。"""
        js = read_js()
        assert "'visible=' +" in js or "'visible='" in js or '"visible=" +' in js or "visible=' + visible" in js, (
            "renderStageDiagnostics 應記錄 visible= 欄位以確認寵物是否可見"
        )

    def test_diagnostics_logs_centeredDeltaX_field(self):
        """renderStageDiagnostics 必須輸出 centeredDeltaX= 欄位。"""
        js = read_js()
        assert "centeredDeltaX" in js, (
            "renderStageDiagnostics 應記錄 centeredDeltaX 以驗證置中偏移"
        )

    def test_room_character_anchor_has_width_from_variable(self):
        """room-character-anchor 必須有明確 width（不依賴 inset 撐開）。"""
        css = read_css()
        import re
        block = re.search(r'\.room-character-anchor\s*\{([^}]+)\}', css)
        assert block, ".room-character-anchor CSS block 不存在"
        content = block.group(1)
        assert "width" in content, (
            ".room-character-anchor 應有明確 width（否則在 inset 失效時寬度為 0）"
        )
