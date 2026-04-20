# ECHOES — 次世代虛擬室友 (AR 視覺共感版)

結合生成式 AI 與電腦視覺的桌面陪伴數位生命體。透過本機感知使用者行為，在虛擬機中進行大腦運算，最終在桌面渲染具備 Alpha 透明通道的動態精靈。

---

## 系統架構

```
┌─────────────── Host (本機 Windows / Linux) ───────────────┐
│                                                            │
│  main.py                                                   │
│    └── TransparentWindow (PyQt5 QMainWindow)               │
│          ├── SettingsDialog (非阻塞角色控制面板)            │
│          │     ├── 角色庫選擇 / 動作預覽                    │
│          │     └── 背景算圖 Worker (QThread)               │
│          ├── QWebEngineView ──→ index.html                 │
│          │     背景透明 (page.setBackgroundColor α=0)      │
│          │     ├── style.css   (滿版、透明、無邊距)        │
│          │     └── app.js      (idle / 單次動作切換)       │
│          │           ↕ file:// absolute video url          │
│          │     assets/webm/characters/<角色>/motions/      │
│          │                                                 │
│          ├── 無邊框 + 永遠置頂 + 不佔工作列               │
│          ├── 滑鼠拖曳移動                                  │
│          └── 平台相容透明合成 / 無邊框視窗                │
│                                                            │
│  sensors/          (Week 2+: psutil / OpenCV / MediaPipe)  │
│  api_client/       (Week 2+: VM FastAPI / ComfyUI 通訊)   │
│                                                            │
└────────── HTTP REST API (JSON) ──────────┐                 │
                                           ▼                 │
┌──────── VM (Linux 虛擬機) ─────────┐                       │
│  FastAPI  →  OpenClaw  →  SQLite   │                       │
│  (API)      (大腦)       (記憶庫)  │                       │
└────────────────────────────────────┘                       │
                                                             │
```

## 目錄結構

```
. (Virtual-Pet/)
├── main.py                    # 程式進入點
├── requirements.txt           # Python 依賴
├── character_library.py       # 角色資產索引與 manifest 管理
├── action_dispatcher.py       # [ACTION:*] 綁定表與執行協調器
├── action_services.py         # 新聞抓取 / 音樂挑選背景 worker
├── ui/
│   ├── assets/
│   │   ├── backgrounds/       # 房間背景圖
│   │   └── music/             # 本地音樂資料夾
│   ├── transparent_window.py  # PyQt5 透明視窗 + WebEngine
│   ├── settings_dialog.py     # 非阻塞角色設定 / 算圖面板
│   └── web_container/
│       ├── index.html         # 房間場景骨架
│       ├── style.css          # 房間背景 / 角色貼地樣式
│       └── app.js             # WebM、狀態列與音訊橋接控制器
├── sensors/                   # 感知模組 (Week 2+)
│   ├── window_monitor.py      # psutil 活躍視窗監聽
│   └── camera_vision.py       # OpenCV + MediaPipe
├── api_client/                # 對外通訊 (Week 2+)
│   ├── vm_connector.py        # FastAPI Client
│   └── comfyui_client.py      # ComfyUI Client
├── assets/
│   └── webm/
│       ├── idle.webm          # 舊版 fallback 影片
│       └── characters/        # 每個角色獨立資料夾與動作資產
├── docs/                      # 專案文件
├── openspec/                  # 架構規格文件
└── venv/                      # Python 虛擬環境
```

## 技術棧

| 層級 | 技術 | 用途 |
|---|---|---|
| UI 容器 | PyQt5 + QWebEngineView | 透明無邊框桌面視窗 |
| 前端播放器 | 原生 HTML5 / CSS3 / JavaScript | WebM 影片循環播放與熱切換 |
| 感知 | OpenCV + MediaPipe + psutil | 表情偵測、視窗監聽 (Week 2+) |
| 通訊 | Python requests ↔ FastAPI | Host ↔ VM JSON API (Week 2+) |
| 渲染 | ComfyUI (LayerDiffuse) | 去背算圖 (Week 2+) |

> **技術戒律：** 禁止使用 QMediaPlayer、禁止使用前端框架 (React/Vue/Tailwind)

## 透明渲染原理

要讓桌面精靈「浮在桌面上」且背景完全透明，需要**四層透明設定同時生效**：

1. **QApplication：** `--disable-gpu` (避免部分 Windows GPU 合成器干擾)
2. **QMainWindow：** `Qt.WA_TranslucentBackground` + `FramelessWindowHint`
3. **QWebEngineView：** `page().setBackgroundColor(QColor(0, 0, 0, 0))` ← 最關鍵
4. **HTML/CSS：** `background-color: transparent`

> Linux 路徑預設保留 GPU / WebGL 加速，不建議為了拖曳功能而停用硬體加速。

## 快速開始

### 環境需求

- Python 3.10+
- Windows 10/11 或原生 Linux (Ubuntu 22.04+)
- Linux 需預先安裝 Qt WebEngine 的系統層 runtime，例如 `libegl1`、`libx11-xcb1`、`libxcb-cursor0`、`libxkbcommon-x11-0`
- 若要在 Linux 上使用本機 NVIDIA / CUDA 算圖，請安裝對應驅動與 toolkit，完整步驟請見 `docs/linux_deployment.md`

### 安裝

> 所有安裝與測試都必須先進入專案虛擬環境後再執行，OpenCV 與其他 Python 依賴一律以 `venv/` 內的環境為準。

```bash
# 1. 建立虛擬環境（若尚未建立）
python -m venv venv

# 2. 啟用虛擬環境
# Windows PowerShell:
.\venv\Scripts\Activate.ps1
# Windows CMD:
.\venv\Scripts\activate.bat

# 3. 安裝依賴
pip install -r requirements.txt
```

### Linux 快速開始

1. 先依 `docs/linux_deployment.md` 安裝 `apt` 系統依賴，並確認 compositor / GPU 驅動設定。
2. 建立虛擬環境：`python3 -m venv venv`
3. 啟用虛擬環境：`source venv/bin/activate`
4. 安裝 Python 依賴：`pip install -r requirements.txt`
5. 執行環境驗證：`python3 tests/verify_linux_env.py`
6. 啟動主程式：`python3 main.py`

> Ubuntu 24.04 上若要啟用揮手偵測，請在已啟用的虛擬環境內安裝 `requirements.txt` 中的 `opencv-python`，不要在系統 Python 直接執行 `pip install` 或測試指令。

> Linux 部署、OpenClaw 設定檔路徑、Qt WebEngine 共享庫與 WebGL 排錯，請直接參考 `docs/linux_deployment.md`。

### 啟動

```bash
# 確保虛擬環境已啟用；所有測試也必須沿用同一個 venv
python main.py   # Windows
python3 main.py  # Linux
```

### 預期結果

1. 桌面右下角出現房間場景視窗，顯示固定背景、角色舞台與狀態列
2. 若已有角色 manifest，會自動載入上次套用角色的 `idle.webm`
3. 若沒有角色 manifest，但 `assets/webm/idle.webm` 存在，會使用舊版 fallback idle
4. 右鍵桌面角色可開啟「ECHOES — 角色設定」，該視窗不會鎖住桌面角色拖曳
5. 右鍵選單新增「功能動作 → 播報新聞 / 播放音樂 / 停止音樂」
6. 在角色設定中可選擇既有角色、播放既有動作，或上傳新圖片重新生成 6 個動作
7. 非 idle 動作播放完會自動回到 idle；若 action 專用動畫缺失，仍會執行功能並保持 idle
8. 狀態列會顯示目前 action，例如新聞標題、目前播放曲目或錯誤訊息
9. 若 `ui/assets/music/` 沒有可播放檔案，播放音樂 action 會顯示 warning，但不會讓 UI 卡住
10. 若新聞來源暫時無法存取，新聞 action 會顯示 warning 並回到安全狀態
11. 可用滑鼠拖曳移動視窗位置
12. 視窗外圈透明區域的點擊會穿透至底下的視窗
13. 5 秒後 console 會印出 `[ECHOES] 測試: 更新房間狀態文字`

## 角色資產流程

1. 在角色設定中選擇一張角色圖片。
2. 系統會建立 `assets/webm/characters/<timestamp>_<角色名>/`。
3. 原始圖片會複製到 `source/`，生成出的 6 支 WebM 會存到 `motions/`。
4. 同層會建立 `manifest.json`，記錄角色名稱、來源圖、動作檔與 prompt。
5. 之後可從角色下拉選單直接套用歷史角色，並選擇任一已生成動作預覽。

### Action 動作檔命名

若要讓角色在功能動作時切換專屬動畫，可在角色 `manifest.json` 的 `motions` 裡加入以下可選鍵值：

- `report_news`: 對應新聞播報動畫，例如 `assets/webm/characters/<角色>/motions/report_news.webm`
- `play_music`: 對應播放音樂動畫，例如 `assets/webm/characters/<角色>/motions/play_music.webm`
- `wave_response`: 對應揮手回應動畫，標準檔名為 `assets/webm/characters/<角色>/motions/running_forward.webm`

若這些影片不存在，系統會保留或回退到 `idle.webm`，但仍會繼續執行 action handler 或感測流程。

## 房間模式資產

- 房間背景預設讀取 `ui/assets/backgrounds/初音房3 2.jpg`
- 本地音樂 action 會掃描 `ui/assets/music/`，支援 `.mp3`、`.wav`、`.ogg`、`.m4a`、`.aac`、`.flac`
- 新聞 action 預設抓取 BBC World RSS；若網路或來源失敗，只會在狀態列與 console 顯示警告

### 準備測試用 WebM

若要沿用舊版 fallback 流程，可將任意含 Alpha 通道的 WebM 影片放入 `assets/webm/` 並命名為 `idle.webm`。

如果暫時沒有素材，可使用 FFmpeg 從帶透明的 PNG 序列生成測試用 WebM：

```bash
ffmpeg -framerate 24 -i frame_%04d.png -c:v libvpx-vp9 -pix_fmt yuva420p idle.webm
```

## JavaScript API

前端提供以下函式，可由 Python 透過 `page().runJavaScript()` 呼叫：

| 函式 | 參數 | 說明 |
|---|---|---|
| `setIdleVideo(source)` | `string` 影片 URL | 設定目前角色 idle 動畫 |
| `playTemporaryVideo(source)` | `string` 影片 URL | 播放單次動作，結束後回到 idle |
| `setActionStatus(message, tone, timeoutMs)` | `string`, `string`, `number` | 更新房間狀態列文字與 tone |
| `clearActionStatus()` | 無 | 將狀態列回復待命狀態 |
| `setRoomCharacter(name)` | `string` | 更新房間場景左上角角色名稱 |
| `playRoomAudio(source, title)` | `string`, `string` | 播放本地音訊並更新狀態列 |
| `stopRoomAudio()` | 無 | 停止目前播放中的音訊 |
| `changeVideo(source)` | `string` 影片 URL | 舊版相容別名，等同 `setIdleVideo` |
| `getVideoStatus()` | 無 | 回傳目前播放狀態 (src, paused, currentTime 等) |

Python 端呼叫範例：

```python
window.change_video("happy.webm")  # 切換到開心動畫
```

## 開發階段

- [x] **Week 1：** 基礎渲染與前端容器 (目前)
- [ ] **Week 2：** 感知模組 (psutil + OpenCV)
- [ ] **Week 3：** VM API 橋接 (FastAPI + OpenClaw)
- [ ] **Week 4：** ComfyUI 算圖整合
