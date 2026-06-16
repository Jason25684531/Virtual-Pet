# Current Test UI Agentic Controls

## Launch Command

Use the Windows host entrypoint with the `--brain-mode` flag:

```powershell
# Harness 模式（預設）：UI 啟動，不連 OpenClaw WebSocket
.\.venv\Scripts\python main.py --brain-mode harness

# 或省略 --brain-mode，效果相同
.\.venv\Scripts\python main.py
```

也可透過環境變數設定（CLI 優先）：

```powershell
$env:ECHOES_BRAIN_MODE = "harness"
.\.venv\Scripts\python main.py
```

### Harness 模式預期 log

```
[ECHOES] 已載入 OpenClaw access token。    # token loading 仍允許
[ECHOES] brain mode: harness
[ECHOES] OpenClaw connection skipped in harness mode.
```

**不應出現**：

```
[ECHOES] OpenClaw WebSocket 連線中: ws://127.0.0.1:18789
[ECHOES] 警告: 無法連線至 OpenClaw 大腦，3 秒後自動重試。
```

### OpenClaw 模式

```powershell
.\.venv\Scripts\python main.py --brain-mode openclaw
```

預期 log（OpenClaw 伺服器需在線）：

```
[ECHOES] brain mode: openclaw
[ECHOES] 已載入 OpenClaw access token。
[ECHOES] OpenClaw WebSocket 連線中: ws://127.0.0.1:18789
[ECHOES] 已連線至 OpenClaw 大腦。
```

### 模式說明

| 模式 | 適用情境 | OpenClaw WebSocket |
|------|----------|-------------------|
| `harness` | Week 1–3/3.5 UI smoke test（當前開發預設）| **不啟動** |
| `openclaw` | OpenClaw 橋接完整測試 | 啟動，含 retry loop |
| `auto` | 未來用，目前保留為 future work | 嘗試連線 |

> **重要**：harness 是當前 Week 1-3/3.5 UI 驗證的預設模式。
> OpenClaw 對現有 UI 煙霧測試來說是可選的，不需要 OpenClaw 伺服器也能正常驗證 UI。

## Extended Files

- `main.py`
  加入 `--brain-mode` CLI 解析與條件性 VMConnector 啟動邏輯。
- `brain_mode.py` (**新增**)
  純 Python 模組，負責解析 brain_mode（CLI > env var > 預設 harness），並提供 `is_openclaw_enabled()` gate 函數。
- `ui/transparent_window.py`
  `TransparentWindow.__init__` 新增 `brain_mode` 參數（預設 `"harness"`）。
- `ui/web_container/index.html`
  WebView 現在包含互動區、結果面板、skill 面板與 tool 面板。
- `ui/web_container/style.css`
  新增版面配置與控制元件樣式。
- `ui/web_container/app.js`
  將 WebView 控制元件連接到 Qt bridge 並渲染 adapter 狀態。
- `pet_harness/ui/pyqt_harness_adapter.py`
  純 Python adapter，將 UI 請求安全橋接到 PetHarnessEngine。
- `tests/test_brain_mode.py` (**新增**)
  brain_mode 解析邏輯與 OpenClaw startup gate 的單元測試。

## Layout

- Fixed top-right XP display:
  Shows bond XP, level, and the latest XP delta in a badge that remains visible while the control panels are open.
- Right-side interaction panel:
  Contains text input, provider selector, send button, and scenario smoke buttons.
- Right-side PetEvent result panel:
  Shows reply, matched skill, tool status, XP delta, reward summary, asset summary, behavior ID, WebM key, provider state, save flag, and warnings.
- Right-side Skill Settings panel:
  Lists loaded skills, enabled state, triggers, required tool, and add/delete controls for safe metadata-only test skills.
- Right-side Tool Settings panel:
  Lists registered tools, enabled state, risk level, permission class, and add/delete controls for metadata-only tool configs.

## Provider Selector

The provider selector exposes:

- `mock`
- `api`
- `low_spec`

The UI sends the selected value to `PyQtHarnessAdapter.handle_text_input(text, provider=...)`.

When the project has API credentials configured through `.env` or environment variables, the current UI now prefers `api` as the primary test path and syncs the selector from persisted provider state on refresh.

If `api` is selected but no API key or base URL is configured, the adapter returns a safe fallback result and surfaces warnings in the UI instead of crashing.

## Scenario Buttons

The current UI includes quick smoke buttons for:

- `hello`
- `please play some bgm`
- `any game news?`
- `remind me to rest`
- `draw my fortune`
- `check system`
- `I won the game`

Each button reuses the same adapter-backed interaction path as manual text input.

## Skill Settings Rules

- Built-in skills are loaded from `.agentic/skills`.
- Built-in skills are disabled through persisted UI settings; they are not physically deleted.
- UI-added test skills are written as markdown metadata under `.agentic/skills/user/`.
- New UI-added skills must provide:
  - `skill_id`
  - `display_name`
  - `description`
  - `triggers`
  - `default_behavior`
  - optional `required_tool`
- `skill_id` is validated to reject path traversal, path separators, and unsafe characters.
- Invalid payloads are rejected before any file is created.

## Tool Settings Rules

- Built-in tool implementations remain code-backed and static in `pet_harness/tools/`.
- UI-added tools are metadata-only configs.
- UI-added tool configs do not create Python executors.
- Metadata-only tools are listed as `configured_but_unimplemented`.
- Built-in tools can be disabled from the UI, but are not physically deleted.
- Tool enable/disable state and metadata-only configs are persisted through SQLite `tool_settings`.

## Enable / Disable / Add / Delete Semantics

- Skill enable/disable:
  Controls whether a loaded skill participates in routing.
- Built-in skill delete:
  Treated as disable, not file deletion.
- User skill delete:
  Removes the managed metadata file from `.agentic/skills/user/`.
- Tool enable/disable:
  Controls whether `SafetyGuard` allows the tool to run.
- Built-in tool delete:
  Treated as disable, not code deletion.
- Metadata-only tool delete:
  Removes the stored config entry.

## XP / Level Explanation

The XP badge now shows total bond XP, current level, latest XP delta, and the next threshold in one line. It also includes a progress bar and threshold text such as:

```text
XP: 42 | Lv. 1 | Last +2 | 42 / 100 XP
```

The current validation policy uses transparent 100 XP level bands:

- Lv. 1: 0-99 XP
- Lv. 2: 100-199 XP
- Lv. 3: 200-299 XP
- Lv. N: `(N - 1) * 100` through `(N * 100) - 1`

Adapter state exposes `bond_xp`, `level`, `xp_delta`, `current_level_min_xp`, `next_level_xp`, `progress_to_next_level`, `xp_to_next_level`, and `level_up_event`. If an interaction crosses a threshold, the UI can show the level-up event without guessing in JavaScript.

## Background Status

The room scene keeps a background layer behind the pet, the pet layer above it, and the dashboard above both. If the configured image is missing, the scene uses a visible CSS placeholder background and reports fallback state instead of silently becoming blank.

Diagnostics expose:

- `background_status`: `loaded`, `missing`, or `fallback`
- `background_source`: a masked or relative source only

Normal UI output must not show absolute local paths.

## Voice Controls

The Voice section includes:

- `Mic`: reports STT capability status.
- `Speak Reply`: reports TTS capability status for the latest reply.
- STT status: detects Azure STT env configuration through `AZURE_STT_API_KEY` and `AZURE_STT_REGION`.
- TTS status: detects ElevenLabs env configuration through `ELEVENLABS_API_KEY`, `ELEVENLABS_*_VOICE_ID`, and `ELEVENLABS_MODEL_ID`.

Current support level is diagnostic-only unless concrete capture/playback providers are implemented. If env keys exist but runtime support is absent, the UI shows configured-but-not-implemented status, for example:

```text
STT configured but microphone capture not implemented
TTS configured but playback/provider not implemented
```

Raw keys are never shown in UI, diagnostics, or structured logs.

## Deep Diagnostics

The Diagnostics section shows subsystem-level state for quick validation:

- bridge status
- last action
- last error
- brain mode
- provider selected
- provider resolved
- provider status
- masked API config status
- skill count
- matched skill
- tool count
- tool status
- XP total
- level
- next level threshold
- reward count
- asset manifest count
- behavior ID
- WebM key
- background status
- voice STT status
- voice TTS status

The JavaScript console also emits concise structured lines such as:

```text
[ECHOES UI] action=send provider=api
[ECHOES UI] bridge=ready
[ECHOES UI] background=fallback
[ECHOES UI] voice.tts=configured status=configured_not_implemented
```

Logs must remain short and must not include raw API keys, tokens, absolute asset paths, or full provider payload dumps.

## Provider Env Resolution

The current validation path prefers `api` when project env contains usable API configuration. Supported keys include:

- `OPENAI_API_KEY`
- `CHATGPT_API_KEY`
- `ECHOES_API_KEY`
- `OPENAI_MODEL`
- `ECHOES_API_BASE_URL`
- `OPENAI_BASE_URL`

If `.env` contains indirection such as:

```text
OPENAI_API_KEY=${CHATGPT_API_KEY}
```

the current loader resolves it from already-loaded `.env` values or the process environment. Diagnostics show the env var name and configured/missing state, not the raw value.

Provider diagnostics can show:

- `api configured`
- `api missing key`
- `api fallback low_spec`
- a safe error category such as `timeout`, `request_error`, or `http_error`

To confirm API is selected instead of mock, check the provider selector and the deep diagnostics `Provider Selected` / `Provider Resolved` fields. If API fails, the UI should still return a visible fallback or warning.

## Confirming XP / Tool / Behavior Flow

Recommended smoke flow:

1. Launch `python main.py`.
2. Click `hello`.
3. Confirm the UI updates reply, XP badge, provider status, behavior ID, WebM key, and save flag.
4. Click `please play some bgm`.
5. Confirm `music_bgm`, `music_search_tool`, XP delta, and tool state appear in the result panel.
6. Disable `music_bgm`, run the same scenario again, and confirm the skill no longer routes.
7. Re-enable `music_bgm`.
8. Disable `music_search_tool`, run BGM again, and confirm tool execution becomes blocked or disabled.
9. Re-enable `music_search_tool`.

## WebM Behavior Preview And Fallback

The current UI attempts a safe preview mapping from harness `behavior_id` to the existing motion-switching path.

Current safe preview map includes:

- `idle` -> `idle`
- `music_idle` -> `play_music`
- `news_idle` -> `report_news`
- `break_idle` -> `listen`
- `gacha_idle` -> `laugh`
- `monitor_idle` -> `listen`

If no safe mapping exists, the result panel still shows the `webm_key`, and the UI adds the warning:

`WebM switching not wired; displaying webm_key only`

No renderer rewrite is attempted in that fallback path.

## Known Limitations

- The UI bridge depends on PyQt5 + QWebEngine runtime availability.
- Metadata-only tool configs remain blocked until code-backed executors exist in `ToolRegistry`.
- The current behavior preview uses a safe mapped subset; it does not yet resolve arbitrary generated WebM assets.
- Voice controls currently report configuration and implementation state; microphone capture and TTS playback remain explicit not-implemented statuses unless a provider is wired later.
- Missing background assets use a visible fallback placeholder and diagnostic warning.
- This document only covers the current PyQt validation UI, not Week 4 Electron migration.

## Troubleshooting Quick Reference

- Skill panel missing:
  Confirm `Skills` appears in the right-side dashboard and `refreshState` returns `skills` in the payload.
- Tool panel missing:
  Confirm `Tools` appears in the right-side dashboard and `refreshState` returns `tools` in the payload.
- Background missing:
  Check `Background` in the compact diagnostics row and `background_status` in deep diagnostics. `fallback` means the UI is intentionally showing the placeholder.
- API key configured but provider falls back:
  Check `Provider Selected`, `Provider Resolved`, and `API Config`. Confirm `.env` uses one of the supported key names and that no raw key appears in the UI.
- STT/TTS configured but not implemented:
  This is expected until microphone capture or playback providers are wired. The Voice section should say configured-but-not-implemented rather than pretending the action ran.
- Click works but backend result does not update:
  Check `Bridge: ready`, `Last action`, `last_error`, and the structured console lines. If the bridge is ready but result fields do not change, inspect adapter output with pytest or `scripts/debug_harness.py`.

## Troubleshooting — Buttons Do Nothing

If clicking buttons triggers no visible reaction:

### 1. 確認 Bridge 診斷面板

UI 右側最上方有 **Diagnostics** 小面板，顯示：

| 指示 | 意義 |
|------|------|
| `Bridge: ready` (綠色) | WebChannel 連線正常，可執行操作 |
| `Bridge: initializing…` (紅色) | QWebChannel 尚未初始化完成 |
| `Bridge: not ready — …` (紅色) | 連線失敗，下方會顯示原因 |

### 2. 確認啟動指令

必須使用 `--brain-mode harness`（或省略，預設即為 harness）：

```powershell
.\.venv\Scripts\python main.py --brain-mode harness
```

**不要**直接用瀏覽器開啟 `index.html`，`qt.webChannelTransport` 只存在於 PyQt5 WebEngine 環境中。

### 3. qwebchannel.js 載入確認

`index.html` 已包含：

```html
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
```

這個路徑由 Qt WebEngine 自動提供，不需要本地端檔案。若 Bridge 面板顯示 `QWebChannel not available`，代表程序並非在 PyQt5 WebEngine 中執行。

### 4. 確認 echoesBridge / harnessBridge 物件

Python 端已用以下名稱註冊：

```python
self._channel.registerObject("harnessBridge", self._bridge)
```

JS 端使用：

```javascript
harnessBridge = channel.objects.harnessBridge
```

兩端名稱一致，勿修改。

### 5. 預期點擊結果

**點擊 `hello` 後：**

- Diagnostics Last: 顯示 `scenario: hello`
- Result panel → Reply 更新為自然語言回覆
- Result panel → Matched Skill 顯示對應 skill（例如 `idle`）
- XP badge → Last + 顯示 XP 增量
- Behavior / WebM Key 更新
- Saved 顯示 `true`

**點擊 `please play some bgm` 後：**

- Diagnostics Last: 顯示 `scenario: please play some bgm`
- Result panel → Matched Skill 顯示 `music_bgm`
- Result panel → Tool 顯示 `music_search_tool | completed` 或 `blocked`
- XP badge 更新
- WebM key 可能更新為 `play_music`

**Send 按鈕（手動輸入文字）：**

- 在 Text input 輸入任意文字後點擊 Send
- Diagnostics Last 顯示 `send: <前40字>`
- Result panel 更新

**Skill / Tool toggle：**

- 點擊 Enable / Disable
- Diagnostics Last 顯示 `skill toggle: <skill_id>` 或 `tool toggle: <tool_name>`
- Skill / Tool 列表重新渲染，enabled 狀態改變
