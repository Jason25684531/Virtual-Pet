角色參考聲音（OmniVoice zero-shot 聲音複製用）
====================================================

一、放置方式：每個角色一個資料夾，資料夾名稱就是角色名

  D:\01_Project\Virtual-Pet\voice\
    OMNI\
      ref.wav      參考聲音，也可以是 .aac、.m4a、.mp3、.flac，每個資料夾只能放一個 ref.*
      ref.txt      參考聲音的逐字稿（UTF-8）。要跟錄音逐字相同，包含語助詞
    Zenni\
      ref.wav
      ref.txt

  參考聲音建議：
  - 3 到 10 秒；超過時腳本會警告，但仍然可以執行（官方說明：過長會變慢，也可能降低相似度）
  - 單一說話人、乾淨、沒有背景音樂，語言與要合成的台詞相同（中文）
  - 必須是你有權使用的聲音

二、沒有逐字稿時：先產生草稿再人工校對

  .venv\Scripts\python local_tts_service\scripts\poc_eval.py transcribe voice

  會在沒有 ref.txt 的角色資料夾寫出 ref.asr.txt。ASR 會出錯，請逐字對照錄音修正後，另存成 ref.txt。

三、執行測試（5 句中文，每個角色都合成）

  雙擊 local_tts_service\run_voice_clone_test.bat

  輸出：local_tts_service\poc\out\omnivoice-user\
    <角色>_00_ref.wav      原始參考聲音（已轉成 24 kHz 單聲道），方便對聽
    <角色>_01..05.wav      生成的 5 句台詞
    metrics.csv / summary.json   合成時間、RTF、VRAM

四、Prompt 快取

  每個角色的 VoiceClonePrompt 快取放在 local_tts_service\poc\out\user_voices\<角色>\。
  指紋涵蓋參考音內容、逐字稿與 backend 版本。更換 ref.* 或修改 ref.txt 後重新執行，
  就會自動重建該角色的快取並刪除舊檔；其他角色不受影響。

注意：本資料夾的音檔不進 git（.gitignore），只有這份說明檔會進。
OmniVoice 權重為 CC-BY-NC，只供非商業研究使用。
