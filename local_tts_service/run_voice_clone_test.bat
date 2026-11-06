@echo off
rem OmniVoice zero-shot test with your own reference voices.
rem Input : ..\voice\<CHARACTER>\ref.wav (or .aac/.m4a/.mp3/.flac) + ref.txt (UTF-8 transcript)
rem Output: poc\out\omnivoice-user\<CHARACTER>_00_ref.wav (original) and <CHARACTER>_01..05.wav (generated)
rem OmniVoice weights are CC-BY-NC: non-commercial research only.
cd /d "%~dp0"
set LOCAL_TTS_ALLOW_NONCOMMERCIAL=1
".venv-omnivoice\Scripts\python.exe" scripts\poc_zero_shot.py --backend omnivoice --voice-dir "%~dp0..\voice" --sentences poc\smoke_zh.txt --stress 0
if errorlevel 1 (echo FAILED, see messages above) else (echo DONE: %~dp0poc\out\omnivoice-user)
pause
