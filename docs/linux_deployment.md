# ECHOES Linux 部署指南

適用平台：原生 Linux，建議以 Ubuntu 22.04+ 為基準。

本文件聚焦三件事：

1. 安裝 PyQt5 / Qt WebEngine 需要的系統層 runtime。
2. 排查透明視窗、WebGL 與 compositor 的桌面環境問題。
3. 提供 NVIDIA / CUDA 在原生環境下的優化建議。

---

## 1. 系統需求

- Python 3.10+
- Ubuntu 22.04+ 或相容發行版
- 桌面環境建議：
  - GNOME on Wayland / Xorg
  - KDE Plasma 5/6
- 若要使用本機 GPU 加速算圖，請安裝對應顯示驅動

## 2. 安裝系統層依賴

Qt WebEngine 在 Linux 上除了 `pip install -r requirements.txt` 之外，還需要作業系統層的共享庫。建議先執行：

```bash
sudo apt update
sudo apt install -y \
  libasound2 \
  libatk-bridge2.0-0 \
  libdbus-1-3 \
  libdrm2 \
  libegl1 \
  libgbm1 \
  libgl1 \
  libgtk-3-0 \
  libnss3 \
  libopengl0 \
  libx11-xcb1 \
  libxcomposite1 \
  libxdamage1 \
  libxkbcommon0 \
  libxkbcommon-x11-0 \
  libxrandr2 \
  libxcb-cursor0 \
  libxcb-icccm4 \
  libxcb-image0 \
  libxcb-keysyms1 \
  libxcb-randr0 \
  libxcb-render-util0 \
  libxcb-shape0 \
  libxcb-shm0 \
  libxcb-sync1 \
  libxcb-xfixes0 \
  libxcb-xinerama0 \
  libxcb-xkb1 \
  mesa-utils
```

如果你需要本機開發工具，可再補：

```bash
sudo apt install -y python3-venv python3-pip
```

## 3. Python 環境與啟動

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 scripts/verify_linux_env.py
python3 main.py
```

## 4. OpenClaw 設定檔位置

`VMConnector` 在 Linux 上會依序使用下列來源載入 token：

1. `OPENCLAW_ACCESS_TOKEN`
2. `OPENCLAW_CONFIG_PATH`
3. `~/.openclaw/openclaw.json`
4. `XDG_CONFIG_HOME/openclaw/openclaw.json` 或 `~/.config/openclaw/openclaw.json`

建議的預設位置如下：

```bash
mkdir -p ~/.openclaw
```

設定檔範例：

```json
{
  "gateway": {
    "auth": {
      "token": "your-openclaw-token"
    }
  }
}
```

## 5. Compositor 與透明視窗排錯

ECHOES 的透明背景、無邊框視窗與 QWebEngine WebGL 會依賴 compositor 正常運作。如果遇到背景變黑、透明失效、拖曳正常但畫面不透明，請依序檢查：

### GNOME

- 若使用 Wayland，請先確認系統 compositor 沒有被關閉或被外掛干擾。
- 若透明背景異常、WebGL renderer 退回軟體渲染，可先在登入畫面切到 `GNOME on Xorg` 交叉比對。
- 若使用 NVIDIA 專有驅動，請確認驅動版本與目前 kernel / Mesa 相容。

### KDE Plasma

- 確認 compositor 仍啟用。若曾被快捷鍵關閉，可按 `Alt + Shift + F12` 重新開啟。
- 建議在「系統設定 → 顯示與監視器 → Compositor」維持 OpenGL backend。
- 若透明邊框失效但 WebGL 正常，通常是 compositor 被停用或顯示驅動 fallback。

### 通用排錯

- 使用 `python3 scripts/verify_linux_env.py` 檢查缺少哪些共享庫、WebGL renderer 是否退回 `llvmpipe` / `SwiftShader`。
- 使用 `glxinfo -B` 檢查 OpenGL renderer，若出現 `llvmpipe`，代表目前不是硬體加速路徑。
- 不要為了視窗拖曳而停用 GPU；ECHOES 的 Linux 拖曳已經改用 Qt event filter 與 drag surface，不需要靠關閉 WebGL 換取拖曳。

## 6. NVIDIA 驅動與 CUDA 建議

如果你在 Linux 上同時使用 ComfyUI 或其他本機模型推論，建議：

- 優先使用 Ubuntu「額外驅動程式」或發行版官方建議版本安裝 NVIDIA 驅動。
- 使用 `nvidia-smi` 確認驅動是否正確載入。
- CUDA toolkit 只在你需要本機 CUDA 工作流時再安裝；若只是執行 Host UI，不必為了 ECHOES 視窗本身強制安裝 CUDA。
- 若 `scripts/verify_linux_env.py` 顯示 WebGL renderer 為 `llvmpipe`，先檢查顯示驅動，再檢查 compositor 與目前登入 session（Wayland/Xorg）。
- 若要讓 ComfyUI 與 UI 共用 GPU，請避免同時開啟過多瀏覽器 / Electron 應用造成 VRAM 碎片化。

## 7. 常見錯誤

### `Could not load the Qt platform plugin "xcb"`

通常代表 `libxcb-*`、`libx11-xcb1` 或 `libxkbcommon-x11-0` 缺漏。先重新執行本文件的 `apt install` 指令，再跑一次：

```bash
python3 scripts/verify_linux_env.py
```

### `WebGL unavailable` 或 renderer 顯示 `llvmpipe`

通常代表目前是軟體渲染：

- 檢查顯示驅動是否正常載入
- 檢查 compositor 是否啟用
- 使用 `glxinfo -B` / `nvidia-smi` 驗證圖形堆疊

### `OpenClaw access token` 載入失敗

- 檢查 `~/.openclaw/openclaw.json` 是否存在
- 檢查 JSON 結構是否含有 `gateway.auth.token`
- 或使用 `OPENCLAW_CONFIG_PATH` 明確指定檔案位置

## 8. 建議驗證流程

每次在新 Linux 環境安裝完成後，建議依序執行：

```bash
python3 scripts/verify_linux_env.py
python3 main.py
```

若驗證腳本已通過但桌面透明仍異常，問題通常落在 compositor、顯示驅動或目前登入 session。
