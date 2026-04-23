# ECHOES Virtual Pet

以 PyQt5 + QWebEngine 為外殼、LangChain + Ollama 為本地大腦、ElevenLabs 為 TTS、WebM 為角色動作載體的桌面虛擬寵物專案。

目前主線已完成：

- 本地 AI 回覆與 `[ACTION:...]` 動作標籤解析
- 角色 `manifest.json` 驅動的 WebM 動作播放
- `wave_response` 揮手感測回應
- 新聞 / 音樂 action service
- 測試與手動驗證腳本分流

## 架構圖

```text
使用者輸入 / 感測事件
        │
        ▼
+--------------------+
| BrainEngine        |
| api_client/        |
| - Ollama 對話      |
| - TTS 文字清理     |
| - Action 正規化    |
+--------------------+
        │ message_received
        ▼
+--------------------+
| ActionDispatcher   |
| - 解析 [ACTION:*]  |
| - 白名單 / alias   |
| - 資產路徑驗證     |
| - fallback Idle    |
+--------------------+
        │
        ▼
+------------------------------+
| TransparentWindow            |
| ui/transparent_window.py     |
| - PyQt5 無邊框透明視窗       |
| - QWebEngineView             |
| - Python -> JS 橋接          |
+------------------------------+
        │ runJavaScript
        ▼
+------------------------------+
| ui/web_container/app.js      |
| - setIdleVideo()             |
| - playTemporaryVideo()       |
| - playRoomAudio()            |
+------------------------------+
        │
        ▼
角色 WebM / 音訊資產

額外事件來源：
- sensors/camera_vision.py -> [ACTION:wave_response]
- action_services.py -> 新聞 / 音樂背景 worker
```

## 目前目錄

```text
Virtual-Pet/
├── main.py                     # 應用程式進入點
├── config.py                   # 全域設定、action 白名單、prompt 規則
├── character_library.py        # 角色 manifest / 動作資產索引
├── action_dispatcher.py        # Action 解析、路徑驗證、fallback、TTS 協調
├── action_services.py          # 新聞與音樂背景 worker
├── api_client/
│   ├── brain_engine.py         # Ollama / LangChain / ElevenLabs 整合
│   └── comfyui_client.py       # ComfyUI 生成 client
├── sensors/
│   └── camera_vision.py        # OpenCV 揮手偵測
├── ui/
│   ├── transparent_window.py   # 主視窗與 JS bridge
│   ├── settings_dialog.py      # 角色設定與生成功能
│   └── web_container/
│       ├── index.html          # 房間場景 DOM
│       ├── style.css           # 房間樣式
│       └── app.js              # WebM / 音訊播放控制
├── tests/
│   ├── test_action_playback.py # Action / URL bridge / fallback 測試
│   └── test_wave_sensor.py     # Wave sensor 與整合測試
├── scripts/
│   ├── smoke_test.py           # Ollama / ElevenLabs / env 冒煙測試
│   └── verify_linux_env.py     # Linux Qt / WebGL / shared lib 驗證
├── docs/
│   ├── linux_deployment.md     # Linux 安裝與排錯
│   ├── STTTTS.md               # STT / TTS 筆記
│   └── archive/                # 歷史參考文件
├── legacy/
│   └── openclaw/               # 舊版 OpenClaw 連線封存
└── openspec/                   # OpenSpec 規格與變更紀錄
```

## 核心模組說明

- `main.py`
  啟動 `QApplication`、`TransparentWindow`、`BrainEngine`、`WaveSensor`，並管理關閉流程。

- `api_client/brain_engine.py`
  負責本地大腦推論、對話記憶、TTS 文字清理，以及把 AI 可能輸出的 action alias 正規化成 Host 可接受的白名單 action。

- `action_dispatcher.py`
  專案的 action 中樞。收到 `[ACTION:tag]` 後會：
  1. 正規化 action 名稱
  2. 驗證對應 WebM 是否存在
  3. 缺檔時退回 `Idle.webm`
  4. 依 action 啟動新聞 / 音樂 / 單次動作 / TTS

- `character_library.py`
  管理角色資料夾、`manifest.json`、動作檔路徑、目前角色狀態。

- `ui/transparent_window.py`
  管理透明視窗、系統匣、角色切換、動作播放，以及 Python 到 JavaScript 的橋接。

- `ui/web_container/app.js`
  真正控制瀏覽器中的 `<video>` 與 `<audio>` 元素，處理 idle、temporary motion、動作播放完回 idle。

- `sensors/camera_vision.py`
  OpenCV 揮手偵測，偵測成功後送出 `[ACTION:wave_response]`。

## Action 白名單

目前 Host 支援的 action：

- `report_news`
- `play_music`
- `wave_response`
- `laugh`
- `angry`
- `awkward`
- `speechless`
- `listen`
- `idle`

AI 若輸出常見 alias，系統也會自動正規化，例如：

- `news` -> `report_news`
- `music` -> `play_music`
- `happy` -> `laugh`
- `mad` -> `angry`
- `thinking` -> `listen`

## 資產規則

角色資產放在：

```text
assets/webm/characters/<character_id>/
├── manifest.json
├── source/
└── motions/
```

`manifest.json` 內的 `motions` 目前至少建議包含：

- `idle`
- `report_news`
- `play_music`
- `wave_response`
- `laugh`
- `angry`
- `awkward`
- `speechless`
- `listen`

其中：

- `wave_response` 預設標準檔名為 `running_forward.webm`
- 缺少 action 專用 WebM 時，系統會安全退回 idle

## 安裝

### 1. 建立並啟用虛擬環境

```bash
python -m venv venv
```

Windows PowerShell：

```bash
.\venv\Scripts\Activate.ps1
```

Linux / macOS：

```bash
source venv/bin/activate
```

### 2. 安裝依賴

```bash
pip install -r requirements.txt
```

### 3. 設定 `.env`

最少建議提供：

```bash
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=minimax-m2.7:cloud
ELEVENLABS_API_KEY=your_api_key
ELEVENLABS_VOICE_ID=zENt0ljwLXypGqHDsdzz
```

## 啟動

```bash
python main.py
```

Linux 使用者若遇到 Qt / WebEngine / WebGL 問題，請先看 [docs/linux_deployment.md](/home/norlan/projecgt/Virtual-Pet/docs/linux_deployment.md)。

## 測試與驗證

### 單元測試

```bash
python -m unittest discover -s tests -v
```

### 冒煙測試

```bash
python scripts/smoke_test.py
```

用途：

- 檢查 `.env`
- 檢查 Ollama API
- 檢查 ElevenLabs API
- 檢查暫存音訊目錄

### Linux 環境驗證

```bash
python scripts/verify_linux_env.py
```

用途：

- 檢查 Qt WebEngine shared libraries
- 檢查 WebGL / renderer 狀態
- 檢查 Linux 上 legacy OpenClaw 設定檔探測順序

## 開發流程建議

1. 啟用 `venv`
2. 修改程式
3. 先跑：

```bash
python -m unittest discover -s tests -v
```

4. 若涉及本地大腦或 Linux 部署，再補跑：

```bash
python scripts/smoke_test.py
python scripts/verify_linux_env.py
```

## 備註

- `legacy/openclaw/` 是封存區，不是目前主流程依賴。
- `docs/archive/` 放歷史參考文件，不影響執行。
- `__pycache__` 與暫存音檔可以隨時清理，不應視為專案原始碼的一部分。
