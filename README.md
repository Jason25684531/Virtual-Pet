# ECHOES — 次世代虛擬室友 (AR 視覺共感版)

結合生成式 AI 與電腦視覺的桌面陪伴數位生命體。透過本機感知使用者行為，在虛擬機中進行大腦運算，最終在桌面渲染具備 Alpha 透明通道的動態精靈。

---

## 系統架構

```
┌─────────────────── Host (本機 Windows) ───────────────────┐
│                                                            │
│  main.py                                                   │
│    └── TransparentWindow (PyQt5 QMainWindow)               │
│          ├── QWebEngineView ──→ index.html                 │
│          │     背景透明 (page.setBackgroundColor α=0)      │
│          │     ├── style.css   (滿版、透明、無邊距)        │
│          │     └── app.js      (changeVideo 換片函式)      │
│          │           ↕ video.src                           │
│          │     assets/webm/    (情緒動態 WebM 影片)        │
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
├── ui/
│   ├── transparent_window.py  # PyQt5 透明視窗 + WebEngine
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
│   └── webm/                  # 情緒動態 WebM 影片
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
2. 若 `assets/webm/idle.webm` 存在，會自動循環播放該影片
3. 若檔案不存在，視窗透明無內容（正常行為，需自行放入測試用 WebM）
4. 可用滑鼠拖曳移動視窗位置
5. 透明區域的點擊會穿透至底下的視窗
6. 5 秒後 console 會印出 `[ECHOES] 測試: 呼叫 changeVideo('idle.webm')`

### 準備測試用 WebM

將任意含 Alpha 通道的 WebM 影片放入 `assets/webm/` 並命名為 `idle.webm`。

如果暫時沒有素材，可使用 FFmpeg 從帶透明的 PNG 序列生成測試用 WebM：

```bash
ffmpeg -framerate 24 -i frame_%04d.png -c:v libvpx-vp9 -pix_fmt yuva420p idle.webm
```

## JavaScript API

前端提供以下函式，可由 Python 透過 `page().runJavaScript()` 呼叫：

| 函式 | 參數 | 說明 |
|---|---|---|
| `changeVideo(filename)` | `string` 純檔名 | 切換 WebM 影片來源 |
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
