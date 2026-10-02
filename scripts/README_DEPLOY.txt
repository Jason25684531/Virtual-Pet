VirtualPet Release 部署說明
===========================

一、搬到新電腦
  1. 把整個 VirtualPet_Release 資料夾複製到新電腦（任意可寫入的路徑，中文或空白都可以；
     不要放在 C:\Program Files 這類唯讀位置）。
  2. 把 config\.env.example 複製為 config\.env，填入 API Key 與 Ollama / ComfyUI 位址。
  3. （選用）從舊機複製 runtime data，沒複製就是全新開始：
       data\pet_state.db、data\characters\、data\saves\、data\runtime\
     （data\characters\ 會連同 personal.json 一起覆蓋；舊機改過的角色個性會一起帶過來）
       runtime_cache\qdrant\（個人記憶向量庫）
  4. 雙擊 VirtualPet.exe。需要重新啟動時執行 run.bat。

二、雙擊後自動處理（背景執行，不影響主視窗）
  - 沒安裝 Lively → 靜默安裝 lively\ 內附安裝程式（只嘗試一次），匯入 ECHOES 桌布並啟動
  - Ollama 沒在執行 → 自動 ollama serve（需已安裝 Ollama）
  - config\.env 有 COMFYUI_LAUNCH_CMD 且 ComfyUI 沒在執行 → 自動啟動

三、可以直接修改的內容（改完重新啟動 App 即生效，不需要重新 build）
  .agentic\         System prompt（soul.md、agentic.md、response_rules.md）、behavior、rewards、skills
  assets\           角色圖片 / motion / manifest / 背景（維持原本的目錄結構）
  data\characters\<角色>\personal.json   角色 persona prompt、技能設定（也可在 App 的角色客製化畫面修改）
  ComfyUI_Json\     ComfyUI workflow（現階段未使用，可刪除；刪除後素材生成自動改用 Mock）
  config\.env       所有設定與 API Key
  修改前建議先備份；格式錯誤的 skill 會被略過，不會讓 App 崩潰。

四、已內附（新機不必另外安裝）
  models\           faster-whisper STT、MiniLM embedding、BM25、jina reranker、Silero VAD（離線使用）
  ms-playwright\    Chromium（音樂 / 新聞 skill）
  ffmpeg\           ffplay（TTS 播放）
  nvidia\           CUDA runtime DLL（STT 使用）
  msvcp140*.dll 等  VC++ runtime
  lively\           Lively Wallpaper 安裝程式與 ECHOES 桌布

五、需要在新電腦另外安裝（系統層，無法打包）
  - NVIDIA 顯示卡驅動：STT 使用 CUDA。https://www.nvidia.com/Download/index.aspx
  - Ollama 與 LLM 模型：https://ollama.com/download，安裝後執行 ollama pull gemma3:12b
  - ComfyUI 與其 checkpoint / LoRA / VAE（現階段未使用）：在 config\.env 設定 COMFYUI_PATH、
    COMFYUI_LAUNCH_CMD、COMFYUI_BASE_URL

六、Log：logs\echoes.log、logs\stdout.log、logs\stderr.log

七、第三方授權
  ffmpeg\LICENSE.txt（ffplay，GPLv3，gyan.dev build）
  lively\LICENSE.txt（Lively Wallpaper，GPLv3）
  ms-playwright\LICENSE、NOTICE（Playwright / Chromium）
