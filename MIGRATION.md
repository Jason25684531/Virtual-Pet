# 機台轉移清單與注意事項

> 目標:把 Virtual-Pet 從開發機完整轉移到機台(實機為 QtWebEngine 5.15 / Chromium 83)。
> **核心事實:git clone 帶不走這個專案。** 大量關鍵資產被 gitignore,轉移必須以「整包目錄複製」為主、git 為輔。

---

## 一、轉移方式決策

```text
┌─────────────────────────────────────────────────────────────┐
│                git clone  vs  整包複製                       │
├─────────────────────────────────────────────────────────────┤
│  git clone 只會拿到:程式碼 + README + requirements.txt      │
│                                                             │
│  拿不到(全部被 .gitignore):                                │
│    .env                → 所有 API key                        │
│    .agentic/*          → 部分檔案已入庫,其餘需複製              │
│    skills-lock.json    → 技能鎖定檔                          │
│    assets/             → 角色 WebM / 圖片 / manifest(最大宗)│
│    ComfyUI_Json/       → 已入庫的資產生成 workflow 模板      │
│    data/characters/    → *.db 狀態 / personal.json / qdrant/ │
│    runtime_cache/      → whisper 模型 / 語意路由索引 / 音檔快取│
│    docs/、openspec/    → 架構文件與變更管理                  │
│                                                             │
│  結論:整包複製(排除 .venv / __pycache__),機台上重建 venv │
└─────────────────────────────────────────────────────────────┘
```

建議複製指令(Windows → Windows):

```bat
robocopy D:\01_Project\Virtual-Pet <目的地> /E /XD .venv __pycache__ .pytest_cache .ruff_cache .tokensave /XF *.log
```

---

## 二、必帶清單(不帶就壞)

| 路徑 | 內容 | 不帶的後果 |
|------|------|-----------|
| `.env` | VOAI / ElevenLabs / OpenAI 金鑰、COMFYUI/STT 設定 | 快捷動作全靜音;API provider 不可用 |
| `.agentic/`(目前 git 追蹤 behavior_map.json、reward_rules.json、bahamut_daily_news.md、youtube_music_playback.md；其餘檔案仍需複製) | 人格、技能定義、behavior 映射、獎勵規則 | 未複製的檔案會使對話大腦、技能或動畫映射不完整 |
| `skills-lock.json` | 技能鎖定檔 | SkillLoader 行為不一致 |
| `assets/webm/characters/` | 角色 manifest + WebM 動作 + 圖片 + 背景 | **無角色可顯示,應用等於空殼** |
| `ComfyUI_Json/` | 已入庫的 ComfyUI workflow 模板 | git clone 即可取得模板；仍需在 ComfyUI 端準備引用的 model/checkpoint |
| `data/characters/{id}/` | state.db(XP/事件/記憶)、profile.json、personal.json、qdrant/ | 角色從零開始:XP、對話記憶、人設覆寫全失 |

## 三、建議帶(可重建但成本高)

| 路徑 | 內容 | 不帶的後果 |
|------|------|-----------|
| `runtime_cache/whisper/` | faster-whisper large-v3-turbo 模型(數 GB) | 機台首次啟動需重新下載(機台若離線則 STT 永久不可用) |
| `runtime_cache/qdrant/` | 語意路由索引 | 首次啟動重建,較慢 |
| `runtime_cache/fixed_intents/`、`news_audio/`、`wave_audio/` | 快取音檔與固定意圖文字 | 首次觸發時重新生成(需 TTS 金鑰與 LLM) |
| `docs/` | 架構文件、Linux 部署指南、CONTEXT-*.md | 機台上無文件可查 |
| `openspec/` | 變更管理紀錄 | 無法在機台上延續 OpenSpec 流程 |
| Playwright 持久化 Chromium context(瀏覽器 profile 目錄) | cookie / 視覺指紋 | YouTube 反自動化 403 風險回升,需重新累積 |

## 四、不要帶

| 路徑 | 原因 |
|------|------|
| `.venv/` | 綁定原機 Python 與絕對路徑,機台上重建 |
| `__pycache__/`、`.pytest_cache/`、`.ruff_cache/` | 快取,自動重生 |
| `*.log`、`debug/events/*.json` | 執行紀錄 |

---

## 五、機台上重建步驟(依序)

```text
[1] 系統前置          [2] Python 環境        [3] 外部服務          [4] 驗證
────────────────     ────────────────      ────────────────     ────────────────
□ Python 版本一致     □ python -m venv .venv □ Ollama 安裝         □ pytest 快測
□ NVIDIA 驅動+CUDA    □ pip install -r       □ ollama pull         □ --ollama-health
  (STT 需要)            requirements.txt      gemma4:e2b          □ run.bat 冒煙
□ 麥克風/音訊裝置     □ playwright install   □ ComfyUI(可選)     □ 六層舞台顯示
□ 網路/防火牆           chromium             □ .env 金鑰確認       □ STT 按鈕狀態
```

逐項:

1. **Python**:安裝與開發機相同的 Python 版本(檢查開發機 `.venv/pyvenv.cfg`),避免 wheel 相容性問題。
2. **虛擬環境**:`python -m venv .venv` → `pip install -r requirements.txt`。`run.bat` 寫死 `.venv\Scripts\python.exe`,venv 目錄名不可改。
3. **Playwright**:`playwright install chromium`(新聞/YouTube 工具需要;缺少只是工具回錯誤,不擋啟動)。
4. **CUDA(STT)**:預設 `STT_DEVICE=cuda`。Windows 上 cuBLAS/cuDNN 由 pip wheel 提供(requirements 已含),只需 NVIDIA 驅動夠新。**機台若是 AMD 顯卡**:ctranslate2 不支援 ROCm,GPU 推論不可行;但 `device`/`compute_type` 是直通 `WhisperModel` 的,可改 `.env` 設 `STT_DEVICE=cpu` + `STT_COMPUTE_TYPE=int8` 走 CPU 推論(未經專案驗證,上機前先在開發機實測延遲;嫌慢可降 `STT_MODEL=medium`)。完全不設定則 STT 按鈕顯示不可用,文字輸入不受影響。
5. **Ollama**:安裝後 `ollama pull gemma4:e2b`(config.py 預設模型)。確認 `http://localhost:11434` 可達。
6. **ComfyUI(可選)**:機台若要資產生成,需另裝 ComfyUI 於 `127.0.0.1:8188` 並確認 `ComfyUI_Json/` 模板引用的 model/checkpoint 也已轉移到 ComfyUI 端;不裝則設 `COMFYUI_ENABLED=false`。
7. **`.env` 檢查**:金鑰齊全、路徑類設定(`STT_MODEL_PATH`、`QDRANT_PATH`)若曾改為絕對路徑,改回相對或機台路徑。

## 六、機台驗收清單

```bash
# 1. 快速測試(不需 GPU/網路/瀏覽器)
.venv/Scripts/python -m pytest -q --ignore=tests/test_cac_ui_browser.py

# 2. LLM 健康檢查
.venv/Scripts/python scripts/debug_harness.py --ollama-health

# 3. 不開 UI 驅動一輪對話
.venv/Scripts/python scripts/debug_harness.py --text "你好"

# 4. 正式啟動
run.bat
```

啟動後人工確認:

- [ ] 六層 2K 舞台正常顯示,角色 idle WebM 播放(缺角色資產會退回空 idle)
- [ ] 打字對話有回覆(走 Ollama)
- [ ] 快捷動作(新聞/揮手)有動畫;有 TTS 金鑰則有聲音
- [ ] STT 按鈕狀態符合預期(有 CUDA = 可用;無 = 顯示不可用)
- [ ] Style 面板列出變體;無 ComfyUI 時升級 offer 走 Mock 不報錯
- [ ] 關閉程式可正常退出(shutdown 反序,不卡死)

---

## 七、注意事項(機台特有)

1. **Chromium 83 CSS 相容性**:實機 QtWebEngine 5.15 = Chromium 83,**不支援 CSS `inset` 與 flex `gap`**。任何前端改動必須在實機驗證;開發機 Playwright(新版 Chromium)驗不出這類問題。
2. **絕對路徑禁令**:`manifest.json` 的 `background_image`、`motions.*` 一律相對於專案根目錄。轉移前 grep 檢查:`grep -rn "D:\\\\" assets/*/characters/*/manifest.json`,發現 `D:\...` 絕對路徑必須先改掉。
3. **嵌入式 Qdrant 檔案鎖**:對話記憶(`data/characters/{id}/qdrant/`)與語意路由(`runtime_cache/qdrant/`)是實體隔離的兩套。轉移時**確保應用程式已關閉再複製**,否則鎖檔/半寫入會損壞索引。
4. **`.db` 複製時機同上**:SQLite state.db 在應用程式執行中複製可能得到不一致快照,先關程式再拷。
5. **首次啟動較慢**:若沒帶 `runtime_cache/`,fastembed 與 whisper 模型會下載、語意索引會重建——機台若離線,這些將直接失敗(whisper 模型務必事先帶)。
6. **機台若為 Linux**:參考 `docs/linux_deployment.md`(libxcb / libegl 等系統層 runtime 需用套件管理器裝);`run.bat` 不適用,改 `python main.py`;requirements 中 `nvidia-*-cu12` wheel 只裝在 win32,Linux 需系統 CUDA。
7. **金鑰安全**:`.env` 用隨身碟/安全通道帶過去,不要為了方便把金鑰塞進任何會被 git 追蹤的檔案(`tests/test_workflow_secret_scan.py` 會擋 workflow 模板,但擋不了其他地方)。
