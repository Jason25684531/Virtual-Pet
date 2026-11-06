# 本地語音（Local TTS）接入文件

狀態：**M1 技術驗證階段，尚未接入 Virtual-Pet。** 這個目錄只有驗證用的腳本。M2（API 服務）與 M3（桌寵整合）還沒有開始。
OpenSpec 紀錄：`openspec/changes/archive/2026-11-06-local-tts-omnivoice-integration/`，其中 `poc-results.md` 是實測數據，`design.md` 是設計與授權查核，`tasks.md` 記錄尚未完成的 M1 後半與 M2 到 M5。

## 一、架構與現況

```
STT → HarnessEngine → LLM → build_tts_provider_chain(character_id)
        → LocalTTSWorker → HTTP 127.0.0.1:8760 → 本地 TTS 服務 → WAV → AudioStreamWorker
```

| 部分 | 現況 |
|---|---|
| 桌寵端 | 已有 `api_client/omni_tts_client.py`，只有 `char-omni` 走本機，連到舊的外部服務包 `omni_tts_win_bundle/`。這個服務包不在 git 裡，本機也不存在。「Omni」是角色名，不是 OmniVoice 模型 |
| 新服務 | 尚未實作。設計見 `design.md`：FastAPI、`/health`、`/tts`、聲線設定檔、prompt 快取、模型抽象層 |
| 本目錄 | 兩個獨立 venv、兩組模型、POC 腳本。可獨立使用，不影響桌寵 |

## 二、目錄與環境

| 路徑 | 用途 | 進 git |
|---|---|---|
| `setup_env.ps1` | 建立某個 backend 的獨立 venv，加 `-Download` 才下載模型 | 是 |
| `requirements-*.txt`、`*.lock.txt` | 版本釘死的依賴與實際安裝清單 | 是 |
| `scripts/poc_zero_shot.py` | 合成並量測 | 是 |
| `scripts/poc_eval.py` | CER、逐字稿草稿、盲聽包 | 是 |
| `scripts/design_reference_voice.py` | 用 Qwen VoiceDesign 生成合成參考聲音，只用於模型比較 | 是 |
| `run_voice_clone_test.bat` | 用 `voice\` 的角色參考音做 5 句測試 | 是 |
| `.venv-qwen3tts\`、`.venv-omnivoice\` | 兩個 backend 各一個環境 | 否 |
| `models\` | 模型權重，約 14 GB | 否 |
| `poc\out\` | 產出的 WAV、prompt 快取、量測 | 否 |
| `..\voice\<角色>\` | 你的參考音 | 否，只有 `voice\README.txt` 進 git |

環境版本：Windows 11、Python 3.11、torch 2.8.0+cu128、RTX 5090。
- Qwen3-TTS：qwen-tts 0.1.1、transformers 4.57.3。
- OmniVoice：omnivoice 0.2.1、transformers 5.22.1。
兩者的 transformers 版本互相衝突，所以必須分開環境。

重建環境：

```
powershell -ExecutionPolicy Bypass -File local_tts_service\setup_env.ps1 -Backend qwen3tts -Download
powershell -ExecutionPolicy Bypass -File local_tts_service\setup_env.ps1 -Backend omnivoice -Download
```

## 三、角色參考聲音怎麼放

每個角色一個資料夾，名稱就是角色名：

```
voice\OMNI\ref.wav   voice\OMNI\ref.txt
voice\Zenni\ref.wav  voice\Zenni\ref.txt
```

- 格式可以是 wav、aac、m4a、mp3、flac，每個資料夾只能有一個 `ref.*`。
- `ref.txt` 是 UTF-8 逐字稿，必須與錄音**逐字相同**。
- 沒有逐字稿時，先執行 `.venv\Scripts\python local_tts_service\scripts\poc_eval.py transcribe voice` 產生 `ref.asr.txt` 草稿，人工校對後另存為 `ref.txt`。
- 更換音檔或修改逐字稿，下次執行會自動重建該角色的 prompt 快取並刪除舊檔，其他角色不受影響。
- 執行測試：雙擊 `run_voice_clone_test.bat`。輸出在 `poc\out\omnivoice-user\`，`*_00_ref.wav` 是原始聲音，`*_01..05.wav` 是生成結果。

## 四、實測結論（5 句小樣本，只是初步基準）

| 項目 | Qwen3-TTS 0.6B | Qwen3-TTS 1.7B | OmniVoice |
|---|---|---|---|
| 授權 | Apache-2.0 | Apache-2.0 | CC-BY-NC，只限非商用 |
| RTF | 3.19 | 3.02 | **0.33** |
| 30 字內 p95 | 23.6 s | 15.5 s | **1.56 s** |
| VRAM 峰值 | 2.7 GB | 4.6 GB | 2.1 GB |
| CER | 15.1% | 3.9% | 3.9% |

- 只有 OmniVoice 達到即時，Qwen 瓶頸是每幀 16 次小運算的開銷，GPU 使用率僅 16%，不是算力問題。
- 使用者角色聲音：Zenni 日文 CER 3.3%，品質最好；OMNI 因參考音是唱的，生成帶吟唱感、語速慢約 40%；Zenni 用日文參考合成中文，CER 28% 且會滲入日文。人工評分尚待你回報。

## 五、注意事項

### 授權與使用範圍
1. **OmniVoice 權重是 CC-BY-NC，只限非商業。** 它的 audio tokenizer 另受 Boson Higgs Audio 2 授權約束。商業展示前必須取得授權，或改用 Qwen3-TTS。這是工程判讀，不是法律意見。
2. **不得把 OmniVoice 的輸出拿去訓練或改善 Qwen3-TTS**，包含當參考聲音與微調資料。Boson 授權明文禁止。
3. OmniVoice 官方聲明禁止未經授權的聲音複製。參考音必須是你有權使用的聲音，每個角色要記錄來源與授權。
4. 權重、參考音、輸出 WAV 與 prompt 快取都不得提交 git，這些路徑已在 `.gitignore`。

### 參考音品質
5. **逐字稿錯誤會讓語速失真。** OmniVoice 用「參考音長度 ÷ 逐字稿字數」估算語速。實測逐字稿少了字，結果 15 字的句子變成 9 秒、48 字的句子變成 36 秒。
6. 建議 3 到 10 秒、單一說話人、乾淨、一般說話。超過 10 秒會變慢，也可能降低相似度。
7. **唱誦或特殊語氣會被複製。** OMNI 的唱腔會帶進所有台詞。要日常對話的聲音，就提供一般說話的錄音。
8. **跨語言會帶口音，且可能滲入參考語言。** 要說中文，就提供該角色的中文參考音。
9. 參考音的語言要與台詞相同。

### 文字與發音
10. 英文字母加數字（例如 RTX 5090）三組測試都念錯，要在送進 TTS 前轉成讀法。這屬於 M2 或 M3 的前處理。
11. OmniVoice 沒有 emotion 參數。Qwen3-TTS Base 沒有速度參數，輸出是隨機取樣，比較時要固定 seed。
12. OmniVoice 每句固定跑 32 步，短句延遲約 1.1 到 1.5 秒，不會隨句長明顯縮短。

### 執行環境
13. **GPU 共用。** 實測整張卡已被約 19 GB 佔用：WSL 裡的 Ollama 約 11.6 GB、ComfyUI 約 4.9 GB。工作管理員把 19.7 GB 算在 dwm.exe 頭上，那是歸屬統計。實際可用約 12.9 GB。整合時要在三者同時常駐下重新量測。
14. 兩個 venv 不能合併。依賴衝突時先分析問題，不要改動另一個環境。
15. 這台機器的全域 pip 指向只有 CPU 版 torch 的鏡像。torch 必須從 PyTorch cu128 來源安裝，`setup_env.ps1` 已處理。
16. PowerShell 5.1 的 `.ps1` 必須存成 UTF-8 with BOM，否則中文註解會吃掉下一行程式碼。
17. 已設定離線旗標。模型載入後不需要網路，但第一次下載需要。

### 接入桌寵前（M2、M3）還沒做的事
- 尚未實作：FastAPI 服務、聲線設定檔、模型抽象層、授權閘門。
- 桌寵端要把 `omni_local` 泛化為 `local_tts`。
- 需補 WAV 播放路徑的角色世代檢查，否則切換角色時可能播出上一個角色的晚到語音。
- 正式機台若是 AMD 顯卡，兩個模型的官方文件都沒有提到 AMD 或 ROCm，必須先實測。
- 另外：舊測試 `test_autostart_runs_bat_only_when_service_down` 在沒有舊服務包的機器上會失敗，與本目錄無關。

## 六、回退

- 本目錄完全獨立，刪除 `local_tts_service\` 與 `voice\` 不影響桌寵。
- 桌寵目前沒有任何改動，仍使用既有的雲端語音與舊的 char-omni 本機路徑。
