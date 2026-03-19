# ECHOES — 次世代虛擬室友 (AR 視覺共感版)

結合生成式 AI 與電腦視覺的桌面陪伴數位生命體。透過本機感知使用者行為，在虛擬機中進行大腦運算，最終在桌面渲染具備 Alpha 透明通道的動態精靈。

---

## 系統架構

```
┌─────────────────── Host (本機 Windows) ───────────────────┐
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
│          └── 透明區域點擊穿透 (WM_NCHITTEST)              │
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
├── ui/
│   ├── transparent_window.py  # PyQt5 透明視窗 + WebEngine
│   ├── settings_dialog.py     # 非阻塞角色設定 / 算圖面板
│   └── web_container/
│       ├── index.html         # HTML5 播放器骨架
│       ├── style.css          # 透明背景樣式
│       └── app.js             # WebM 熱切換 JS 控制器
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

## 快速開始

### 環境需求

- Python 3.10+
- Windows 10/11 (目前僅支援 Windows)

### 安裝

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

### 啟動

```bash
# 確保虛擬環境已啟用
python main.py
```

### 預期結果

1. 桌面右下角出現 400×400 的透明視窗
2. 若已有角色 manifest，會自動載入上次套用角色的 `idle.webm`
3. 若沒有角色 manifest，但 `assets/webm/idle.webm` 存在，會使用舊版 fallback idle
4. 右鍵桌面角色可開啟「ECHOES — 角色設定」，該視窗不會鎖住桌面角色拖曳
5. 在角色設定中可選擇既有角色、播放既有動作，或上傳新圖片重新生成 6 個動作
6. 非 idle 動作播放完會自動回到 idle
7. 若檔案不存在，視窗透明無內容（正常行為）
8. 可用滑鼠拖曳移動視窗位置
9. 透明區域的點擊會穿透至底下的視窗
10. 5 秒後 console 會印出 `[ECHOES] 測試: 呼叫 changeVideo('idle.webm')`

## 角色資產流程

1. 在角色設定中選擇一張角色圖片。
2. 系統會建立 `assets/webm/characters/<timestamp>_<角色名>/`。
3. 原始圖片會複製到 `source/`，生成出的 6 支 WebM 會存到 `motions/`。
4. 同層會建立 `manifest.json`，記錄角色名稱、來源圖、動作檔與 prompt。
5. 之後可從角色下拉選單直接套用歷史角色，並選擇任一已生成動作預覽。

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
